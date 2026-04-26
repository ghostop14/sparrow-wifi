"""Tests for sparrow_elastic.mac_utils."""

import pytest
from sparrow_elastic.mac_utils import canonicalize_mac, mac_flags


# ---------------------------------------------------------------------------
# canonicalize_mac — format acceptance
# ---------------------------------------------------------------------------

class TestCanonicalizeMac:
    """All five accepted input formats should produce the same canonical output."""

    CANONICAL = "AA:BB:CC:DD:EE:FF"

    def test_colon_uppercase(self):
        assert canonicalize_mac("AA:BB:CC:DD:EE:FF") == self.CANONICAL

    def test_colon_lowercase(self):
        assert canonicalize_mac("aa:bb:cc:dd:ee:ff") == self.CANONICAL

    def test_hyphen_separated(self):
        assert canonicalize_mac("AA-BB-CC-DD-EE-FF") == self.CANONICAL

    def test_bare_hex_uppercase(self):
        assert canonicalize_mac("AABBCCDDEEFF") == self.CANONICAL

    def test_bare_hex_lowercase(self):
        assert canonicalize_mac("aabbccddeeff") == self.CANONICAL

    def test_cisco_dot_notation(self):
        assert canonicalize_mac("aabb.ccdd.eeff") == self.CANONICAL

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_empty(self):
        assert canonicalize_mac("") == ""

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            canonicalize_mac("AA:BB:CC")

    def test_too_long_raises(self):
        with pytest.raises(ValueError):
            canonicalize_mac("AA:BB:CC:DD:EE:FF:00")

    def test_non_hex_chars_raise(self):
        with pytest.raises(ValueError):
            canonicalize_mac("ZZ:BB:CC:DD:EE:FF")

    def test_garbage_string_raises(self):
        with pytest.raises(ValueError):
            canonicalize_mac("not-a-mac-address")


# ---------------------------------------------------------------------------
# mac_flags
# ---------------------------------------------------------------------------

class TestMacFlags:
    def test_locally_administered_bit_set(self):
        flags = mac_flags("02:00:00:00:00:00")
        assert flags["locally_administered"] is True

    def test_locally_administered_bit_unset(self):
        flags = mac_flags("00:00:00:00:00:01")
        assert flags["locally_administered"] is False

    def test_ble_public_addr_type_universal(self):
        flags = mac_flags("00:1A:2B:3C:4D:5E", is_ble=True, ble_addr_type=0)
        assert flags["addr_type"] == "universal"
        assert flags["randomized"] is False

    def test_ble_random_static(self):
        # First byte 0xC3 — top 2 bits = 0b11 → random_static
        flags = mac_flags("C3:00:00:00:00:00", is_ble=True, ble_addr_type=1)
        assert flags["addr_type"] == "random_static"
        assert flags["randomized"] is True

    def test_ble_random_resolvable(self):
        # First byte 0x42 — top 2 bits = 0b01 → random_resolvable
        flags = mac_flags("42:00:00:00:00:00", is_ble=True, ble_addr_type=1)
        assert flags["addr_type"] == "random_resolvable"
        assert flags["randomized"] is True

    def test_ble_random_nonresolvable(self):
        # First byte 0x10 — top 2 bits = 0b00 → random_nonresolvable
        flags = mac_flags("10:00:00:00:00:00", is_ble=True, ble_addr_type=1)
        assert flags["addr_type"] == "random_nonresolvable"
        assert flags["randomized"] is True

    def test_empty_mac_returns_unknown(self):
        flags = mac_flags("")
        assert flags["addr_type"] == "unknown"
        assert flags["locally_administered"] is False
        assert flags["randomized"] is False

    def test_malformed_mac_returns_unknown(self):
        flags = mac_flags("not-valid")
        assert flags["addr_type"] == "unknown"
