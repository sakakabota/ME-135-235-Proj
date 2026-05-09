"""Serial protocol for shipping 64x64 frames to the ESP32.

Frame layout: [0xAA 0x55][LEN_H LEN_L][MODE][payload...][CRC_H CRC_L][0x55 0xAA]
CRC16/CCITT-FALSE over MODE byte + payload. ESP32 replies 0x06 (ACK) or 0x15 (NAK),
or sends mode-change notifications 0x10 (mode 0) / 0x11 (mode 1).

Modes:
  0x00 — binary mask: 512 bytes bit-packed row-major MSB-first.
         ESP32 colors the mask using its pot (white → red lerp).
  0x01 — fingertip packet: [count(1)][x(1) y(1) r(1) g(1) b(1)]...
         Up to 10 fingertips. ESP32 renders colored dots on black.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import NamedTuple

import numpy as np
import serial
import serial.tools.list_ports

logger = logging.getLogger("me135.serial_protocol")

PANEL_SIZE = 64
PAYLOAD_BYTES = 512  # 64 * 64 / 8

FRAME_START = b"\xAA\x55"
FRAME_END = b"\x55\xAA"
ACK_BYTE = 0x06
NAK_BYTE = 0x15

MODE_MASK = 0x00
MODE_FINGERTIPS = 0x01

MODE_NOTIFY_0 = 0x10
MODE_NOTIFY_1 = 0x11

MAX_FINGERTIPS = 10


class Fingertip(NamedTuple):
    x: int
    y: int
    r: int
    g: int
    b: int


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE: poly=0x1021, init=0xFFFF, no reflect, no xorout."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def pack_mask(mask: np.ndarray) -> bytes:
    """Pack a (64, 64) {0,1} or {0,255} mask into 512 bytes, MSB-first row-major."""
    if mask.shape != (PANEL_SIZE, PANEL_SIZE):
        raise ValueError(
            f"Expected mask shape ({PANEL_SIZE}, {PANEL_SIZE}), got {mask.shape}"
        )
    arr = np.ascontiguousarray(mask, dtype=np.uint8)
    if arr.max() > 1:
        arr = (arr > 0).astype(np.uint8)
    packed = np.packbits(arr.flatten(), bitorder="big").tobytes()
    if len(packed) != PAYLOAD_BYTES:
        raise ValueError(f"Pack produced {len(packed)} bytes, expected {PAYLOAD_BYTES}")
    return packed


def unpack_mask(data: bytes) -> np.ndarray:
    """Inverse of pack_mask — for round-trip tests."""
    if len(data) != PAYLOAD_BYTES:
        raise ValueError(f"Expected {PAYLOAD_BYTES} bytes, got {len(data)}")
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")
    return bits[: PANEL_SIZE * PANEL_SIZE].reshape(PANEL_SIZE, PANEL_SIZE)


def pack_fingertips(fingertips: list[Fingertip]) -> bytes:
    """Pack fingertip positions + colors into a mode-0x01 payload."""
    if len(fingertips) > MAX_FINGERTIPS:
        raise ValueError(f"Max {MAX_FINGERTIPS} fingertips, got {len(fingertips)}")
    buf = bytearray(1 + len(fingertips) * 5)
    buf[0] = len(fingertips)
    for i, ft in enumerate(fingertips):
        off = 1 + i * 5
        buf[off] = ft.x & 0xFF
        buf[off + 1] = ft.y & 0xFF
        buf[off + 2] = ft.r & 0xFF
        buf[off + 3] = ft.g & 0xFF
        buf[off + 4] = ft.b & 0xFF
    return bytes(buf)


def unpack_fingertips(data: bytes) -> list[Fingertip]:
    """Inverse of pack_fingertips."""
    if len(data) < 1:
        raise ValueError("Fingertip payload too short")
    count = data[0]
    if count > MAX_FINGERTIPS:
        raise ValueError(f"Invalid fingertip count: {count}")
    expected = 1 + count * 5
    if len(data) < expected:
        raise ValueError(f"Fingertip payload: expected {expected} bytes, got {len(data)}")
    tips: list[Fingertip] = []
    for i in range(count):
        off = 1 + i * 5
        tips.append(Fingertip(
            x=data[off],
            y=data[off + 1],
            r=data[off + 2],
            g=data[off + 3],
            b=data[off + 4],
        ))
    return tips


def build_frame(mode: int, payload: bytes) -> bytes:
    """Wrap payload in the full framed packet."""
    body = bytes([mode]) + payload
    crc = crc16_ccitt(body)
    length = len(payload)
    return FRAME_START + struct.pack(">H", length) + body + struct.pack(">H", crc) + FRAME_END


class SerialSender:
    """Framed sender for 64x64 frames. ACK/NAK with retries. Mode-aware."""

    def __init__(
        self,
        port: str,
        baudrate: int = 1_000_000,
        ack_timeout_s: float = 0.05,
        max_retries: int = 3,
    ):
        self._port = port
        self._max_retries = max_retries
        self._ack_timeout = ack_timeout_s

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                timeout=ack_timeout_s,
                write_timeout=1.0,
            )
        except serial.SerialException as exc:
            available = [p.device for p in serial.tools.list_ports.comports()]
            raise serial.SerialException(
                f"Could not open serial port {port!r}: {exc}. "
                f"Available ports: {available or '(none detected)'}"
            ) from exc

        logger.info("Serial opened: %s @ %d baud", port, baudrate)

        self.frames_sent = 0
        self.frames_acked = 0
        self.frames_naked = 0
        self._esp32_mode = 0

    @property
    def esp32_mode(self) -> int:
        return self._esp32_mode

    def _send_packet(self, packet: bytes) -> bool:
        """Transmit with ACK/NAK + retries. Returns True on ACK."""
        max_attempts = 1 + self._max_retries
        for attempt in range(1, max_attempts + 1):
            self._drain_pending_input()
            self._ser.write(packet)
            self._ser.flush()
            self.frames_sent += 1

            if self._wait_ack():
                self.frames_acked += 1
                return True

            self.frames_naked += 1
            if attempt < max_attempts:
                logger.warning("Frame NAK/timeout — retry %d/%d", attempt, self._max_retries)

        logger.error("Frame delivery failed after %d attempts", max_attempts)
        return False

    def _handle_mode_byte(self, b: int) -> int | None:
        if b == MODE_NOTIFY_0:
            self._esp32_mode = 0
            logger.info("ESP32 switched to mode 0 (Kyle/pot)")
            return 0
        if b == MODE_NOTIFY_1:
            self._esp32_mode = 1
            logger.info("ESP32 switched to mode 1 (Wen/fingers)")
            return 1
        return None

    def _drain_pending_input(self) -> None:
        while self._ser.in_waiting > 0:
            self._handle_mode_byte(self._ser.read(1)[0])

    def _wait_ack(self) -> bool:
        deadline = time.monotonic() + self._ack_timeout
        while time.monotonic() < deadline:
            resp = self._ser.read(1)
            if len(resp) == 0:
                continue
            if resp[0] == ACK_BYTE:
                return True
            if resp[0] == NAK_BYTE:
                logger.debug("NAK received")
                return False
            if self._handle_mode_byte(resp[0]) is not None:
                continue
            logger.debug("Unexpected response byte: 0x%02X", resp[0])
        logger.debug("ACK timeout")
        return False

    def read_mode_change(self) -> int | None:
        """Check for ESP32 mode-change notifications. Returns new mode or None."""
        while self._ser.in_waiting > 0:
            mode = self._handle_mode_byte(self._ser.read(1)[0])
            if mode is not None:
                return mode
        return None

    def send_mask(self, mask: np.ndarray) -> bool:
        """Send a binary mask frame (mode 0x00)."""
        payload = pack_mask(mask)
        packet = build_frame(MODE_MASK, payload)
        return self._send_packet(packet)

    def send_fingertips(self, fingertips: list[Fingertip]) -> bool:
        """Send a fingertip frame (mode 0x01)."""
        payload = pack_fingertips(fingertips)
        packet = build_frame(MODE_FINGERTIPS, payload)
        return self._send_packet(packet)

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info(
                "Serial closed. Stats: sent=%d ack=%d nak=%d",
                self.frames_sent,
                self.frames_acked,
                self.frames_naked,
            )
