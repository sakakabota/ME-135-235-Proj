"""
Unit tests for serial_protocol.py
Tests CRC-16, bit-packing, downsampling, and round-trip correctness.
Run: cd /Users/kyle/Documents/Antigravity/ME-135-235-Proj/Kyle && python -m pytest tests/ -v

⚠ These tests pin the legacy 400×300 → 108×108 / 1,458-byte payload contract
  (2026-05-07). The display hardware is now a Waveshare RGB-Matrix-P2 64×64
  (HUB75), so when serial_protocol.py is rewritten to emit 64×64 = 512-byte
  frames, the constants imported here (CV_ROWS, CV_COLS, PANEL_ROWS,
  PANEL_COLS, PAYLOAD_BYTES) and any size-specific assertions below will need
  to be updated together. Don't update tests in isolation — match them to the
  rewritten module.
"""

import sys
from pathlib import Path
import numpy as np
import pytest

# Allow importing from agent_outputs/
sys.path.insert(0, str(Path(__file__).parent.parent / "agent_outputs"))
from serial_protocol import (
    crc16_ccitt,
    pack_matrix,
    unpack_matrix,
    downsample_to_panel,
    CV_ROWS,
    CV_COLS,
    PANEL_ROWS,
    PANEL_COLS,
    PAYLOAD_BYTES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_panel_matrix(seed: int = 42) -> np.ndarray:
    """Return a reproducible random binary (108, 108) uint8 matrix."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, size=(PANEL_ROWS, PANEL_COLS), dtype=np.uint8)


def _zeros_cv() -> np.ndarray:
    return np.zeros((CV_ROWS, CV_COLS), dtype=np.uint8)


def _ones_cv() -> np.ndarray:
    return np.ones((CV_ROWS, CV_COLS), dtype=np.uint8)


def _zeros_panel() -> np.ndarray:
    return np.zeros((PANEL_ROWS, PANEL_COLS), dtype=np.uint8)


def _ones_panel() -> np.ndarray:
    return np.ones((PANEL_ROWS, PANEL_COLS), dtype=np.uint8)


# ---------------------------------------------------------------------------
# 1. CRC-16/CCITT-FALSE known test vectors
# ---------------------------------------------------------------------------

class TestCRC16:
    def test_standard_vector_123456789(self):
        """The canonical CRC-16/CCITT-FALSE test vector must equal 0x29B1."""
        result = crc16_ccitt(b"123456789")
        assert result == 0x29B1, (
            f"Standard test vector failed: expected 0x29B1, got 0x{result:04X}"
        )

    def test_empty_input_equals_init(self):
        """Empty input must return the init value 0xFFFF unchanged."""
        result = crc16_ccitt(b"")
        assert result == 0xFFFF, (
            f"Empty input should return init=0xFFFF, got 0x{result:04X}"
        )

    def test_four_zero_bytes_deterministic(self):
        """Four zero bytes must produce a consistent, deterministic CRC."""
        result1 = crc16_ccitt(b"\x00" * 4)
        result2 = crc16_ccitt(b"\x00" * 4)
        assert result1 == result2, "CRC of identical inputs must be identical"
        # The CRC-16/CCITT-FALSE of 0x0000_0000 is 0x84C0
        assert result1 == 0x84C0, (
            f"CRC of b'\\x00'*4 should be 0x84C0, got 0x{result1:04X}"
        )

    def test_idempotency_same_data_same_crc(self):
        """Calling crc16_ccitt twice on the same data returns the same value."""
        data = b"ME135 serial protocol test"
        assert crc16_ccitt(data) == crc16_ccitt(data)

    def test_different_data_different_crc(self):
        """Different payloads should (almost certainly) produce different CRCs."""
        crc_a = crc16_ccitt(b"frame_A")
        crc_b = crc16_ccitt(b"frame_B")
        assert crc_a != crc_b

    def test_custom_init_value(self):
        """A non-default init value should change the CRC output."""
        default_crc = crc16_ccitt(b"test", init=0xFFFF)
        custom_crc = crc16_ccitt(b"test", init=0x0000)
        assert default_crc != custom_crc

    def test_return_type_is_int(self):
        """CRC must be returned as an int."""
        result = crc16_ccitt(b"abc")
        assert isinstance(result, int)

    def test_return_value_fits_16_bits(self):
        """CRC must fit in 16 bits (0x0000–0xFFFF)."""
        for data in [b"", b"\xFF" * 256, b"123456789"]:
            result = crc16_ccitt(data)
            assert 0 <= result <= 0xFFFF, (
                f"CRC out of 16-bit range: 0x{result:X}"
            )


# ---------------------------------------------------------------------------
# 2. pack_matrix output length
# ---------------------------------------------------------------------------

class TestPackMatrixLength:
    def test_cv_zeros_length(self):
        """All-zeros (300,400) matrix must produce exactly 1458 bytes."""
        packed = pack_matrix(_zeros_cv())
        assert len(packed) == PAYLOAD_BYTES

    def test_cv_ones_length(self):
        """All-ones (300,400) matrix must produce exactly 1458 bytes."""
        packed = pack_matrix(_ones_cv())
        assert len(packed) == PAYLOAD_BYTES

    def test_panel_matrix_accepted(self):
        """A (108,108) input is accepted directly and produces 1458 bytes."""
        packed = pack_matrix(_zeros_panel())
        assert len(packed) == PAYLOAD_BYTES

    def test_panel_ones_length(self):
        """All-ones (108,108) matrix must produce exactly 1458 bytes."""
        packed = pack_matrix(_ones_panel())
        assert len(packed) == PAYLOAD_BYTES

    def test_wrong_shape_raises_value_error(self):
        """Any matrix with an unsupported shape must raise ValueError."""
        bad_shapes = [(100, 100), (300, 300), (400, 400), (1, 1458), (108, 109)]
        for shape in bad_shapes:
            with pytest.raises(ValueError):
                pack_matrix(np.zeros(shape, dtype=np.uint8))

    def test_return_type_is_bytes(self):
        """pack_matrix must return a bytes object."""
        result = pack_matrix(_zeros_panel())
        assert isinstance(result, bytes)

    def test_payload_bytes_constant_matches_formula(self):
        """Sanity check: PAYLOAD_BYTES == PANEL_ROWS * PANEL_COLS // 8."""
        assert PAYLOAD_BYTES == (PANEL_ROWS * PANEL_COLS) // 8
        assert PAYLOAD_BYTES == 1458


# ---------------------------------------------------------------------------
# 3. pack_matrix / unpack_matrix round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_random_panel_matrix_roundtrip(self):
        """Random (108,108) binary matrix survives pack→unpack unchanged."""
        original = _random_panel_matrix(seed=42)
        recovered = unpack_matrix(pack_matrix(original))
        np.testing.assert_array_equal(
            recovered, original,
            err_msg="Round-trip mismatch on random panel matrix"
        )

    def test_all_zeros_cv_roundtrip(self):
        """All-zeros (300,400) → pack → unpack returns all-zeros (108,108)."""
        recovered = unpack_matrix(pack_matrix(_zeros_cv()))
        assert recovered.shape == (PANEL_ROWS, PANEL_COLS)
        assert np.all(recovered == 0)

    def test_all_ones_cv_roundtrip(self):
        """All-ones (300,400) → downsample → pack → unpack returns all-ones."""
        recovered = unpack_matrix(pack_matrix(_ones_cv()))
        assert recovered.shape == (PANEL_ROWS, PANEL_COLS)
        assert np.all(recovered == 1)

    def test_all_zeros_panel_roundtrip(self):
        """All-zeros panel matrix survives pack→unpack as all zeros."""
        recovered = unpack_matrix(pack_matrix(_zeros_panel()))
        assert np.all(recovered == 0)

    def test_all_ones_panel_roundtrip(self):
        """All-ones panel matrix survives pack→unpack as all ones."""
        recovered = unpack_matrix(pack_matrix(_ones_panel()))
        assert np.all(recovered == 1)

    def test_checkerboard_roundtrip(self):
        """Checkerboard pattern (alternating 0,1) survives round-trip exactly."""
        checkerboard = np.indices((PANEL_ROWS, PANEL_COLS)).sum(axis=0) % 2
        checkerboard = checkerboard.astype(np.uint8)
        recovered = unpack_matrix(pack_matrix(checkerboard))
        np.testing.assert_array_equal(recovered, checkerboard)

    def test_multiple_seeds_roundtrip(self):
        """Round-trip correctness holds for several distinct random matrices."""
        for seed in [0, 1, 99, 1234, 9999]:
            original = _random_panel_matrix(seed=seed)
            recovered = unpack_matrix(pack_matrix(original))
            np.testing.assert_array_equal(
                recovered, original,
                err_msg=f"Round-trip mismatch for seed={seed}"
            )

    def test_unpack_wrong_length_raises(self):
        """unpack_matrix must raise ValueError for incorrect byte lengths."""
        for bad_len in [0, 1, 1457, 1459, 2916]:
            with pytest.raises(ValueError):
                unpack_matrix(bytes(bad_len))

    def test_roundtrip_output_dtype(self):
        """unpack_matrix output must have dtype uint8."""
        recovered = unpack_matrix(pack_matrix(_random_panel_matrix()))
        assert recovered.dtype == np.uint8

    def test_roundtrip_output_shape(self):
        """unpack_matrix output must have shape (108, 108)."""
        recovered = unpack_matrix(pack_matrix(_random_panel_matrix()))
        assert recovered.shape == (PANEL_ROWS, PANEL_COLS)

    def test_roundtrip_values_binary(self):
        """unpack_matrix output must contain only 0 and 1."""
        recovered = unpack_matrix(pack_matrix(_random_panel_matrix()))
        unique = np.unique(recovered)
        assert set(unique).issubset({0, 1}), (
            f"Non-binary values after unpack: {unique}"
        )


# ---------------------------------------------------------------------------
# 4. downsample_to_panel shape and values
# ---------------------------------------------------------------------------

class TestDownsampleToPanel:
    def test_output_shape(self):
        """(300,400) input must produce exactly (108,108) output."""
        result = downsample_to_panel(_zeros_cv())
        assert result.shape == (PANEL_ROWS, PANEL_COLS)

    def test_all_zeros_preserved(self):
        """All-zeros input → all-zeros output after downsampling."""
        result = downsample_to_panel(_zeros_cv())
        assert np.all(result == 0)

    def test_all_ones_preserved(self):
        """All-ones input → all-ones output (nearest-neighbour keeps binary)."""
        result = downsample_to_panel(_ones_cv())
        assert np.all(result == 1)

    def test_output_dtype_uint8(self):
        """Output dtype must be uint8."""
        result = downsample_to_panel(_zeros_cv())
        assert result.dtype == np.uint8

    def test_output_values_binary(self):
        """Nearest-neighbour resize of a binary matrix must stay binary."""
        rng = np.random.default_rng(7)
        matrix = rng.integers(0, 2, size=(CV_ROWS, CV_COLS), dtype=np.uint8)
        result = downsample_to_panel(matrix)
        unique = np.unique(result)
        assert set(unique).issubset({0, 1}), (
            f"Non-binary values after downsample: {unique}"
        )

    def test_deterministic(self):
        """Same input always produces the same output."""
        matrix = _ones_cv()
        result1 = downsample_to_panel(matrix)
        result2 = downsample_to_panel(matrix)
        np.testing.assert_array_equal(result1, result2)


# ---------------------------------------------------------------------------
# 5. pack_matrix bit order (MSB-first)
# ---------------------------------------------------------------------------

class TestBitOrder:
    def test_first_pixel_set_sets_msb(self):
        """Only pixel (0,0) set → first packed byte must have bit 7 (MSB) set."""
        matrix = _zeros_panel()
        matrix[0, 0] = 1
        packed = pack_matrix(matrix)
        # MSB of first byte corresponds to pixel (0,0)
        assert packed[0] & 0x80 == 0x80, (
            f"Expected MSB set in first byte, got 0x{packed[0]:02X}"
        )
        # All other bytes must be zero
        assert all(b == 0 for b in packed[1:]), (
            "Expected all bytes after the first to be 0x00"
        )

    def test_second_pixel_set_sets_bit6(self):
        """Only pixel (0,1) set → first packed byte must have bit 6 set."""
        matrix = _zeros_panel()
        matrix[0, 1] = 1
        packed = pack_matrix(matrix)
        assert packed[0] & 0x40 == 0x40, (
            f"Expected bit 6 set in first byte, got 0x{packed[0]:02X}"
        )
        assert packed[0] & 0x80 == 0x00, "Bit 7 must be clear when only pixel (0,1) is set"
        assert all(b == 0 for b in packed[1:])

    def test_eighth_pixel_sets_lsb_of_first_byte(self):
        """Only pixel (0,7) set → first packed byte must equal 0x01 (LSB set)."""
        matrix = _zeros_panel()
        matrix[0, 7] = 1
        packed = pack_matrix(matrix)
        assert packed[0] == 0x01, (
            f"Expected first byte == 0x01, got 0x{packed[0]:02X}"
        )
        assert all(b == 0 for b in packed[1:])

    def test_ninth_pixel_starts_second_byte(self):
        """Only pixel (0,8) set → second packed byte must have MSB set, first byte zero."""
        matrix = _zeros_panel()
        matrix[0, 8] = 1
        packed = pack_matrix(matrix)
        assert packed[0] == 0x00, (
            f"Expected first byte == 0x00, got 0x{packed[0]:02X}"
        )
        assert packed[1] & 0x80 == 0x80, (
            f"Expected MSB of second byte set, got 0x{packed[1]:02X}"
        )
        assert all(b == 0 for b in packed[2:])

    def test_all_zeros_packs_to_all_zero_bytes(self):
        """All-zeros panel matrix must pack to all 0x00 bytes."""
        packed = pack_matrix(_zeros_panel())
        assert all(b == 0 for b in packed)

    def test_all_ones_packs_to_all_0xff_bytes(self):
        """All-ones panel matrix must pack to all 0xFF bytes."""
        packed = pack_matrix(_ones_panel())
        assert all(b == 0xFF for b in packed), (
            f"Expected all 0xFF, first non-0xFF byte: "
            f"0x{next(b for b in packed if b != 0xFF):02X}"
        )

    def test_bit_position_across_row(self):
        """Pixels 0–7 in row 0 map to bits 7–0 of the first byte, MSB-first."""
        for col in range(8):
            matrix = _zeros_panel()
            matrix[0, col] = 1
            packed = pack_matrix(matrix)
            expected_bit = 7 - col
            expected_byte = 1 << expected_bit
            assert packed[0] == expected_byte, (
                f"Pixel (0,{col}): expected first byte 0x{expected_byte:02X}, "
                f"got 0x{packed[0]:02X}"
            )


# ---------------------------------------------------------------------------
# 6. Integration: CRC over packed data
# ---------------------------------------------------------------------------

class TestCRCIntegration:
    def test_crc_nonzero_for_nontrivial_data(self):
        """Packing a matrix with set pixels and computing CRC gives a non-zero result."""
        matrix = _random_panel_matrix(seed=0)
        payload = pack_matrix(matrix)
        crc = crc16_ccitt(payload)
        # CRC of all-zeros is 0x84C0 (non-zero), but for random data it's very
        # unlikely to be 0x0000; assert it is not 0x0000 for this known seed.
        assert crc != 0x0000, (
            "CRC should not be zero for a non-trivial payload"
        )

    def test_flipping_one_bit_changes_crc(self):
        """Mutating a single bit in the packed payload must change the CRC."""
        matrix = _random_panel_matrix(seed=1)
        payload = pack_matrix(matrix)
        original_crc = crc16_ccitt(payload)

        # Flip bit 3 of byte 0
        mutated = bytearray(payload)
        mutated[0] ^= 0x08
        mutated_crc = crc16_ccitt(bytes(mutated))

        assert original_crc != mutated_crc, (
            "CRC must change when payload is modified (bit-flip not detected)"
        )

    def test_flipping_bits_across_payload_all_change_crc(self):
        """Flipping any single byte-level bit across several positions changes CRC."""
        matrix = _random_panel_matrix(seed=2)
        payload = pack_matrix(matrix)
        original_crc = crc16_ccitt(payload)

        flip_positions = [0, 1, PAYLOAD_BYTES // 2, PAYLOAD_BYTES - 2, PAYLOAD_BYTES - 1]
        for pos in flip_positions:
            mutated = bytearray(payload)
            mutated[pos] ^= 0xFF  # flip all bits in that byte
            assert crc16_ccitt(bytes(mutated)) != original_crc, (
                f"CRC unchanged after flipping byte at position {pos}"
            )

    def test_crc_consistent_across_identical_frames(self):
        """The same matrix packed twice must produce identical CRC values."""
        matrix = _random_panel_matrix(seed=3)
        payload_a = pack_matrix(matrix)
        payload_b = pack_matrix(matrix)
        assert crc16_ccitt(payload_a) == crc16_ccitt(payload_b)

    def test_different_matrices_produce_different_crcs(self):
        """Two distinct matrices must produce distinct CRC values (collision unlikely)."""
        matrix_a = _random_panel_matrix(seed=10)
        matrix_b = _random_panel_matrix(seed=11)
        payload_a = pack_matrix(matrix_a)
        payload_b = pack_matrix(matrix_b)
        assert crc16_ccitt(payload_a) != crc16_ccitt(payload_b)

    def test_all_zeros_payload_crc(self):
        """Packed all-zeros payload CRC must equal CRC of 1458 zero bytes."""
        payload = pack_matrix(_zeros_panel())
        expected = crc16_ccitt(bytes(PAYLOAD_BYTES))
        assert crc16_ccitt(payload) == expected

    def test_crc_16bit_range_over_full_payload(self):
        """CRC computed over a full 1458-byte payload must fit in 16 bits."""
        payload = pack_matrix(_random_panel_matrix(seed=5))
        crc = crc16_ccitt(payload)
        assert 0 <= crc <= 0xFFFF
