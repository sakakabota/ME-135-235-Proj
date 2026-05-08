"""Serial protocol for shipping 64x64 binary masks to the ESP32.

Frame layout: [0xAA 0x55][LEN_H LEN_L=0x0200][512B payload][CRC_H CRC_L][0x55 0xAA]
Payload is row-major, MSB-first bit-packed (64*64/8 = 512 bytes).
CRC16/CCITT-FALSE over the payload only. ESP32 replies 0x06 (ACK) or 0x15 (NAK).
50 ms ACK timeout, up to 3 retries per frame.
"""

from __future__ import annotations

import logging
import struct

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
    # Normalize 0/255 (or anything nonzero) to 0/1.
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


class SerialSender:
    """Framed sender for 64x64 binary masks. ACK/NAK with retries."""

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

    def _build_packet(self, payload: bytes) -> bytes:
        length = len(payload)
        header = FRAME_START + struct.pack(">H", length)
        crc = crc16_ccitt(payload)
        footer = struct.pack(">H", crc) + FRAME_END
        return header + payload + footer

    def _wait_ack(self) -> bool:
        resp = self._ser.read(1)
        if len(resp) == 0:
            logger.debug("ACK timeout")
            return False
        if resp[0] == ACK_BYTE:
            return True
        if resp[0] == NAK_BYTE:
            logger.debug("NAK received")
            return False
        logger.warning("Unexpected response byte: 0x%02X", resp[0])
        return False

    def send_frame(self, mask: np.ndarray) -> bool:
        """Bit-pack, frame, transmit. Returns True on ACK within retries.

        Sends one initial attempt + up to ``max_retries`` re-sends on NAK/timeout
        (so ``max_retries=3`` means up to 4 transmissions total).
        """
        payload = pack_mask(mask)
        packet = self._build_packet(payload)

        max_attempts = 1 + self._max_retries
        for attempt in range(1, max_attempts + 1):
            self._ser.reset_input_buffer()
            self._ser.write(packet)
            self._ser.flush()
            self.frames_sent += 1

            if self._wait_ack():
                self.frames_acked += 1
                return True

            self.frames_naked += 1
            if attempt < max_attempts:
                logger.warning(
                    "Frame NAK/timeout — retry %d/%d",
                    attempt,
                    self._max_retries,
                )

        logger.error("Frame delivery failed after %d attempts", max_attempts)
        return False

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info(
                "Serial closed. Stats: sent=%d ack=%d nak=%d",
                self.frames_sent,
                self.frames_acked,
                self.frames_naked,
            )
