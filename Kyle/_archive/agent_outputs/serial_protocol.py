"""
ME135 Human Detection — Serial Protocol (Jetson → ESP32)
========================================================

⚠ STALE — payload sizing is wrong (2026-05-07)
The display hardware changed to a Waveshare RGB-Matrix-P2 64×64 (HUB75).
Reference: https://www.waveshare.com/wiki/RGB-Matrix-P2-64x64
Required rewrite before next deploy:
  - Resize CV mask to 64×64 (panel native — no separate downsample tier needed)
  - Bit-packed payload becomes 64×64 / 8 = 512 bytes/frame (was 1,458)
  - Adjust LEN_H/LEN_L, frame buffer, and CRC scope accordingly
  - Larry's pipeline already outputs 64×64; can be wired in directly without resize

(Original docstring follows.)
Downsamples the 400x300 CV matrix to the physical 108x108 LED panel,
bit-packs it into 1,458 bytes, and transmits over UART with framing,
CRC-16, and ACK/NAK flow control.

Wire format (see PROTOCOL_SPEC.md for full details):
    [0xAA][0x55][LEN_H][LEN_L][PAYLOAD …][CRC_H][CRC_L][0x55][0xAA]

Public API (used by main.py):
    - SerialSender(config)
    - sender.send_frame(binary_matrix)  →  bool (True = ACK received)
    - sender.close()
"""

import struct
import logging
import time
import numpy as np
import serial

logger = logging.getLogger("me135.serial_protocol")

# ---- Constants matching PROTOCOL_SPEC.md ----
FRAME_START = b"\xAA\x55"
FRAME_END = b"\x55\xAA"
ACK_BYTE = 0x06
NAK_BYTE = 0x15

# CV processing resolution (kept high for better detection accuracy)
CV_ROWS = 300
CV_COLS = 400

# Physical LED panel resolution — what gets transmitted
PANEL_ROWS = 108
PANEL_COLS = 108
PAYLOAD_BYTES = (PANEL_ROWS * PANEL_COLS) // 8  # 1,458


# ---- CRC-16/CCITT-FALSE ----
def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """Compute CRC-16/CCITT-FALSE over *data*."""
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


# ---- Downsample + bit-packing ----
def downsample_to_panel(matrix: np.ndarray) -> np.ndarray:
    """
    Downsample a (300, 400) CV matrix to (108, 108) for the LED panel.
    Uses nearest-neighbour to preserve binary values.

    Parameters
    ----------
    matrix : np.ndarray, shape (CV_ROWS, CV_COLS), dtype uint8, values {0,1}

    Returns
    -------
    np.ndarray, shape (108, 108), dtype uint8, values {0,1}
    """
    import cv2
    resized = cv2.resize(
        matrix, (PANEL_COLS, PANEL_ROWS), interpolation=cv2.INTER_NEAREST
    )
    return resized


def pack_matrix(matrix: np.ndarray) -> bytes:
    """
    Downsample a (300, 400) CV matrix to (108, 108) and bit-pack into
    1,458 bytes, MSB-first, row-major order.

    Parameters
    ----------
    matrix : np.ndarray, shape (300, 400) or (108, 108), dtype uint8

    Returns
    -------
    bytes of length 1,458
    """
    if matrix.shape == (CV_ROWS, CV_COLS):
        matrix = downsample_to_panel(matrix)
    if matrix.shape != (PANEL_ROWS, PANEL_COLS):
        raise ValueError(
            f"Expected matrix shape ({CV_ROWS}, {CV_COLS}) or "
            f"({PANEL_ROWS}, {PANEL_COLS}), got {matrix.shape}"
        )
    packed = np.packbits(matrix.flatten(), bitorder="big")
    return packed.tobytes()


def unpack_matrix(data: bytes) -> np.ndarray:
    """Inverse of pack_matrix — returns (108, 108) panel matrix."""
    if len(data) != PAYLOAD_BYTES:
        raise ValueError(f"Expected {PAYLOAD_BYTES} bytes, got {len(data)}")
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")
    return bits[: PANEL_ROWS * PANEL_COLS].reshape(PANEL_ROWS, PANEL_COLS)


# ---- Serial sender ----
class SerialSender:
    """Manages framed serial transmission of binary matrices to ESP32."""

    def __init__(self, config: dict):
        ser_cfg = config["serial"]
        self._max_retries = ser_cfg["max_retries"]
        self._ack_timeout = ser_cfg["ack_timeout_s"]

        self._ser = serial.Serial(
            port=ser_cfg["port"],
            baudrate=ser_cfg["baud_rate"],
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=ser_cfg["timeout_s"],
        )
        logger.info(
            "Serial opened: %s @ %d baud", ser_cfg["port"], ser_cfg["baud_rate"]
        )

        # Statistics
        self.frames_sent = 0
        self.frames_acked = 0
        self.frames_naked = 0

    # ------------------------------------------------------------------
    def _build_packet(self, payload: bytes) -> bytes:
        """Wrap payload with start marker, length, CRC, end marker."""
        length = len(payload)
        header = FRAME_START + struct.pack(">H", length)
        crc = crc16_ccitt(payload)
        footer = struct.pack(">H", crc) + FRAME_END
        return header + payload + footer

    def _wait_ack(self) -> bool:
        """Read one byte from ESP32: ACK (0x06) or NAK (0x15)."""
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

    # ------------------------------------------------------------------
    def send_frame(self, matrix: np.ndarray) -> bool:
        """
        Bit-pack and transmit one binary matrix frame.

        Parameters
        ----------
        matrix : np.ndarray, shape (300, 400), dtype uint8, values {0, 1}
                 Will be automatically downsampled to 108x108 before transmission.

        Returns
        -------
        True if ESP32 acknowledged the frame.
        """
        payload = pack_matrix(matrix)
        packet = self._build_packet(payload)

        for attempt in range(1, self._max_retries + 1):
            self._ser.write(packet)
            self.frames_sent += 1

            if self._wait_ack():
                self.frames_acked += 1
                return True

            self.frames_naked += 1
            logger.warning("Frame NAK/timeout — retry %d/%d",
                           attempt, self._max_retries)

        logger.error("Frame delivery failed after %d attempts", self._max_retries)
        return False

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info(
                "Serial closed. Stats: sent=%d ack=%d nak=%d",
                self.frames_sent, self.frames_acked, self.frames_naked,
            )
