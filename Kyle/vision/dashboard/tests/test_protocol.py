"""Verify the existing serial_protocol.py contract at 64x64 / 512-byte payload.

The archived tests under Kyle/_archive/tests/ are pinned to the legacy 108x108 /
1458-byte payload and will not pass against the current module. These tests
replace them. They verify byte-for-byte agreement with the firmware's pollFrame
state machine and renderMask bit indexing.

Run from repo root:
    python -m pytest Kyle/vision/dashboard/tests/ -v
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

# Kyle/vision/dashboard/tests/test_protocol.py -> Kyle/vision/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from serial_protocol import (
    FRAME_END,
    FRAME_START,
    MAX_FINGERTIPS,
    MODE_FINGERTIPS,
    MODE_MASK,
    PANEL_SIZE,
    PAYLOAD_BYTES,
    Fingertip,
    build_frame,
    crc16_ccitt,
    pack_fingertips,
    pack_mask,
    unpack_fingertips,
    unpack_mask,
)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_panel_size_is_64(self):
        assert PANEL_SIZE == 64

    def test_payload_bytes_is_512(self):
        assert PAYLOAD_BYTES == (PANEL_SIZE * PANEL_SIZE) // 8 == 512

    def test_frame_markers(self):
        assert FRAME_START == b"\xAA\x55"
        assert FRAME_END == b"\x55\xAA"

    def test_mode_constants(self):
        assert MODE_MASK == 0x00
        assert MODE_FINGERTIPS == 0x01

    def test_max_fingertips_is_10(self):
        assert MAX_FINGERTIPS == 10


# ---------------------------------------------------------------------------
# CRC-16/CCITT-FALSE — matches firmware crc16_ccitt() in main.cpp
# ---------------------------------------------------------------------------

class TestCRC16:
    def test_canonical_vector(self):
        # The standard CRC-16/CCITT-FALSE test vector
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_empty_returns_init(self):
        assert crc16_ccitt(b"") == 0xFFFF

    def test_four_zero_bytes(self):
        # Per CRC reference: CRC-16/CCITT-FALSE of \x00\x00\x00\x00 = 0x84C0
        assert crc16_ccitt(b"\x00\x00\x00\x00") == 0x84C0

    def test_deterministic(self):
        data = b"ME135 protocol"
        assert crc16_ccitt(data) == crc16_ccitt(data)

    def test_single_bit_flip_changes_crc(self):
        a = crc16_ccitt(b"\x00" * 16)
        b = bytearray(b"\x00" * 16)
        b[3] ^= 0x01
        assert crc16_ccitt(bytes(b)) != a

    def test_fits_in_16_bits(self):
        for data in (b"", b"\xFF" * 256, b"123456789", b"\x01\x02\x03"):
            crc = crc16_ccitt(data)
            assert 0 <= crc <= 0xFFFF


# ---------------------------------------------------------------------------
# pack_mask — MSB-first bit packing matches firmware renderMask() indexing
# ---------------------------------------------------------------------------

class TestPackMask:
    def _zeros(self):
        return np.zeros((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)

    def _ones(self):
        return np.ones((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)

    def test_output_length_512(self):
        assert len(pack_mask(self._zeros())) == 512

    def test_all_zero_payload(self):
        assert pack_mask(self._zeros()) == b"\x00" * 512

    def test_all_one_payload(self):
        assert pack_mask(self._ones()) == b"\xFF" * 512

    def test_pixel_0_0_sets_msb_of_first_byte(self):
        # firmware: i=0 -> bit = (byte >> 7) & 1, so bit 7 (MSB) of byte 0
        m = self._zeros()
        m[0, 0] = 1
        packed = pack_mask(m)
        assert packed[0] == 0x80
        assert all(b == 0 for b in packed[1:])

    def test_pixel_0_1_sets_bit_6(self):
        m = self._zeros()
        m[0, 1] = 1
        packed = pack_mask(m)
        assert packed[0] == 0x40

    def test_pixel_0_7_sets_lsb_of_first_byte(self):
        m = self._zeros()
        m[0, 7] = 1
        packed = pack_mask(m)
        assert packed[0] == 0x01

    def test_pixel_0_8_sets_msb_of_second_byte(self):
        m = self._zeros()
        m[0, 8] = 1
        packed = pack_mask(m)
        assert packed[0] == 0x00
        assert packed[1] == 0x80

    def test_pixel_1_0_sets_msb_of_byte_8(self):
        # row-major: i = 1*64 + 0 = 64 -> byte index 8, bit 7 of that byte
        m = self._zeros()
        m[1, 0] = 1
        packed = pack_mask(m)
        assert packed[8] == 0x80
        for i, b in enumerate(packed):
            if i != 8:
                assert b == 0

    def test_accepts_0_255_mask(self):
        # OpenCV typically gives binary masks as {0, 255}. pack_mask must threshold.
        m = self._zeros()
        m[0, 0] = 255
        packed = pack_mask(m)
        assert packed[0] == 0x80

    def test_wrong_shape_raises(self):
        for bad in [(63, 64), (64, 63), (32, 32), (128, 128), (1, 4096)]:
            with pytest.raises(ValueError):
                pack_mask(np.zeros(bad, dtype=np.uint8))

    def test_round_trip_random(self):
        rng = np.random.default_rng(42)
        m = rng.integers(0, 2, size=(PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)
        recovered = unpack_mask(pack_mask(m))
        np.testing.assert_array_equal(recovered, m)


# ---------------------------------------------------------------------------
# build_frame — wire format matches firmware pollFrame() state machine
# ---------------------------------------------------------------------------

class TestBuildFrame:
    def test_mask_frame_structure(self):
        payload = pack_mask(np.zeros((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8))
        frame = build_frame(MODE_MASK, payload)
        # [AA 55][LEN_H LEN_L][MODE][512][CRC_H CRC_L][55 AA]
        assert len(frame) == 2 + 2 + 1 + PAYLOAD_BYTES + 2 + 2  # 519
        assert frame[0:2] == b"\xAA\x55"
        assert frame[2:4] == struct.pack(">H", PAYLOAD_BYTES)  # big-endian LEN
        assert frame[4] == MODE_MASK
        assert frame[5:5 + PAYLOAD_BYTES] == payload
        assert frame[-2:] == b"\x55\xAA"

    def test_mask_frame_crc_over_mode_plus_payload(self):
        # Firmware computes CRC over [MODE byte || payload], NOT including LEN
        payload = pack_mask(np.zeros((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8))
        frame = build_frame(MODE_MASK, payload)
        crc_in_frame = struct.unpack(">H", frame[-4:-2])[0]
        expected = crc16_ccitt(bytes([MODE_MASK]) + payload)
        assert crc_in_frame == expected

    def test_fingertip_frame_crc(self):
        tips = [Fingertip(10, 20, 255, 0, 0), Fingertip(40, 50, 0, 255, 128)]
        payload = pack_fingertips(tips)
        frame = build_frame(MODE_FINGERTIPS, payload)
        crc_in_frame = struct.unpack(">H", frame[-4:-2])[0]
        expected = crc16_ccitt(bytes([MODE_FINGERTIPS]) + payload)
        assert crc_in_frame == expected

    def test_fingertip_len_field(self):
        # LEN must equal len(payload), not 1 + payload (firmware checks rxLen ≤ 1+10*5)
        tips = [Fingertip(0, 0, 0, 0, 0)]
        payload = pack_fingertips(tips)
        frame = build_frame(MODE_FINGERTIPS, payload)
        len_in_frame = struct.unpack(">H", frame[2:4])[0]
        assert len_in_frame == len(payload) == 1 + 1 * 5

    def test_blank_mask_frame_first_and_last_bytes(self):
        blank = np.zeros((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)
        frame = build_frame(MODE_MASK, pack_mask(blank))
        assert frame[0:2] == b"\xAA\x55"
        assert frame[-2:] == b"\x55\xAA"
        # All payload bytes are zero
        assert frame[5:5 + PAYLOAD_BYTES] == b"\x00" * PAYLOAD_BYTES


# ---------------------------------------------------------------------------
# Fingertip pack/unpack
# ---------------------------------------------------------------------------

class TestFingertips:
    def test_empty_list(self):
        payload = pack_fingertips([])
        assert payload == b"\x00"
        assert unpack_fingertips(payload) == []

    def test_single_tip_round_trip(self):
        tip = Fingertip(x=12, y=34, r=200, g=100, b=50)
        payload = pack_fingertips([tip])
        recovered = unpack_fingertips(payload)
        assert recovered == [tip]

    def test_layout_count_then_xyrgb(self):
        tip = Fingertip(x=1, y=2, r=3, g=4, b=5)
        payload = pack_fingertips([tip])
        # firmware: rxLen ≤ 1 + 10*5; payload = [count][x y r g b]...
        assert payload == bytes([1, 1, 2, 3, 4, 5])

    def test_max_count(self):
        tips = [Fingertip(i, i, i, i, i) for i in range(MAX_FINGERTIPS)]
        payload = pack_fingertips(tips)
        assert payload[0] == MAX_FINGERTIPS
        assert len(payload) == 1 + MAX_FINGERTIPS * 5

    def test_too_many_raises(self):
        tips = [Fingertip(0, 0, 0, 0, 0)] * (MAX_FINGERTIPS + 1)
        with pytest.raises(ValueError):
            pack_fingertips(tips)


# ---------------------------------------------------------------------------
# Smoke: full mask pipeline mirrors what SerialWorker.send_mask does
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_mask_roundtrip_through_frame(self):
        rng = np.random.default_rng(7)
        m = rng.integers(0, 2, size=(PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)
        payload = pack_mask(m)
        frame = build_frame(MODE_MASK, payload)
        # Strip framing back out
        assert frame[0:2] == b"\xAA\x55"
        len_field = struct.unpack(">H", frame[2:4])[0]
        assert len_field == PAYLOAD_BYTES
        assert frame[4] == MODE_MASK
        recovered_payload = frame[5:5 + PAYLOAD_BYTES]
        recovered_mask = unpack_mask(recovered_payload)
        np.testing.assert_array_equal(recovered_mask, m)
        # CRC is correct
        crc = struct.unpack(">H", frame[-4:-2])[0]
        assert crc == crc16_ccitt(bytes([MODE_MASK]) + recovered_payload)
