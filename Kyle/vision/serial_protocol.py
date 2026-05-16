"""Host side framed serial protocol for the ESP32 panel 

Frame 0xAA 0x55 LEN_H LEN_L MODE payload CRC_H CRC_L 0x55 0xAA 
CRC 16 CCITT FALSE over MODE byte payload 

Mode 0x00 512 byte bit packed 64x64 mask ESP32 lerps white red via pot 
Mode 0x01 fingertip dots count x y r g b count up to 10 tips 

ESP32 sideband bytes not framed 
 0x06 ACK 0x15 NAK 0x10 0x11 mode change 0x20 value pot update 
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
PAYLOAD_BYTES = 512  # 64 64 8

FRAME_START = b"\xAA\x55"
FRAME_END = b"\x55\xAA"
ACK_BYTE = 0x06
NAK_BYTE = 0x15

MODE_MASK = 0x00
MODE_FINGERTIPS = 0x01

MODE_NOTIFY_0 = 0x10
MODE_NOTIFY_1 = 0x11
POT_NOTIFY = 0x20  # followed by 1 byte 0 255 

MAX_FINGERTIPS = 10


class Fingertip(NamedTuple):
    x: int
    y: int
    r: int
    g: int
    b: int


def crc16_ccitt(data, init=0xFFFF):
    # CRC 16 CCITT FALSE poly 0x1021 init 0xFFFF no reflect no xorout
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


def pack_mask(mask):
    """Pack a 64 64 0 1 0 255 mask into 512 bytes MSB first row major """
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


def unpack_mask(data):
    """Inverse of pack_mask For round trip tests """
    if len(data) != PAYLOAD_BYTES:
        raise ValueError(f"Expected {PAYLOAD_BYTES} bytes, got {len(data)}")
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")
    return bits[: PANEL_SIZE * PANEL_SIZE].reshape(PANEL_SIZE, PANEL_SIZE)


def pack_fingertips(fingertips):
    """Pack fingertip positions colors into a mode 0x01 payload """
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


def unpack_fingertips(data):
    """Inverse of pack_fingertips """
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


def build_frame(mode, payload):
    body = bytes([mode]) + payload
    crc = crc16_ccitt(body)
    length = len(payload)
    return FRAME_START + struct.pack(">H", length) + body + struct.pack(">H", crc) + FRAME_END


class SerialSender:
    """Framed sender for 64x64 frames ACK NAK with retries Mode aware """

    def __init__(
        self,
        port,
        baudrate=1_000_000,
        ack_timeout_s=0.05,
        max_retries=3,
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
        self._esp32_pot = 0  # 0 255 mirrors the ESP32 pot for host preview

    @property
    def esp32_mode(self):
        return self._esp32_mode

    @property
    def esp32_pot(self):
        return self._esp32_pot

    def _send_packet(self, packet):
        # transmit wait for ACK with retries
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

    def _handle_mode_byte(self, b):
        # Returns new mode if this byte was a mode change else None 
        # Pot updates 0x20 value read synchronously here so a pending
        # second byte never leaks into the next ACK read 
        if b == MODE_NOTIFY_0:
            self._esp32_mode = 0
            logger.info("ESP32 switched to mode 0 (Kyle/pot)")
            return 0
        if b == MODE_NOTIFY_1:
            self._esp32_mode = 1
            logger.info("ESP32 switched to mode 1 (Wen/fingers)")
            return 1
        if b == POT_NOTIFY:
            # Block briefly for the value byte Firmware writes both bytes together
            # via Serial write pkt 2 a 5 ms wait is generous at 1 Mbaud If the
            # value never arrives drop the packet rather than poison later reads 
            deadline = time.monotonic() + 0.005
            while time.monotonic() < deadline:
                v = self._ser.read(1)
                if len(v) == 1:
                    self._esp32_pot = v[0]
                    return None
            logger.debug("POT_NOTIFY without value byte — dropped")
            return None
        return None

    def _drain_pending_input(self):
        while self._ser.in_waiting > 0:
            self._handle_mode_byte(self._ser.read(1)[0])

    def _wait_ack(self):
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

    def read_mode_change(self):
        """Check for ESP32 mode change notifications Returns new mode or None """
        while self._ser.in_waiting > 0:
            mode = self._handle_mode_byte(self._ser.read(1)[0])
            if mode is not None:
                return mode
        return None

    def send_mask(self, mask):
        payload = pack_mask(mask)
        return self._send_packet(build_frame(MODE_MASK, payload))

    def send_fingertips(self, fingertips):
        payload = pack_fingertips(fingertips)
        return self._send_packet(build_frame(MODE_FINGERTIPS, payload))

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info(
                "Serial closed. Stats: sent=%d ack=%d nak=%d",
                self.frames_sent,
                self.frames_acked,
                self.frames_naked,
            )
