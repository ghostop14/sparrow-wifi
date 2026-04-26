"""Tests for sparrow_elastic.channel_utils."""

import pytest
from sparrow_elastic.channel_utils import (
    band_for_frequency,
    channel_for_frequency,
    compute_occupied_set,
)


# ---------------------------------------------------------------------------
# compute_occupied_set
# ---------------------------------------------------------------------------

class TestComputeOccupiedSet:
    # 5 GHz -- 20 MHz
    def test_5ghz_20mhz(self):
        assert compute_occupied_set(36, 20, "5ghz") == [36]

    def test_5ghz_20mhz_ch_44(self):
        assert compute_occupied_set(44, 20, "5ghz") == [44]

    # 5 GHz -- 40 MHz
    def test_5ghz_40mhz(self):
        result = compute_occupied_set(36, 40, "5ghz")
        assert result == [36, 40]

    # 5 GHz -- 80 MHz
    def test_5ghz_80mhz_primary_36(self):
        assert compute_occupied_set(36, 80, "5ghz") == [36, 40, 44, 48]

    def test_5ghz_80mhz_primary_40(self):
        # Primary is inside the 36-48 group; should return the full group
        assert compute_occupied_set(40, 80, "5ghz") == [36, 40, 44, 48]

    def test_5ghz_80mhz_primary_44(self):
        assert compute_occupied_set(44, 80, "5ghz") == [36, 40, 44, 48]

    def test_5ghz_80mhz_primary_48(self):
        assert compute_occupied_set(48, 80, "5ghz") == [36, 40, 44, 48]

    def test_5ghz_80mhz_group2(self):
        assert compute_occupied_set(52, 80, "5ghz") == [52, 56, 60, 64]

    def test_5ghz_80mhz_group_149(self):
        assert compute_occupied_set(149, 80, "5ghz") == [149, 153, 157, 161]

    def test_5ghz_80mhz_unknown_primary_fallback(self):
        # Channel 11 is not in the 5 GHz lookup table
        result = compute_occupied_set(11, 80, "5ghz")
        assert result == [11]

    # 5 GHz -- 160 MHz
    def test_5ghz_160mhz_primary_36(self):
        assert compute_occupied_set(36, 160, "5ghz") == [36, 40, 44, 48, 52, 56, 60, 64]

    def test_5ghz_160mhz_primary_52(self):
        # 52 is inside the 36-64 group
        assert compute_occupied_set(52, 160, "5ghz") == [36, 40, 44, 48, 52, 56, 60, 64]

    def test_5ghz_160mhz_group2(self):
        assert compute_occupied_set(100, 160, "5ghz") == [100, 104, 108, 112, 116, 120, 124, 128]

    def test_5ghz_160mhz_unknown_fallback(self):
        result = compute_occupied_set(200, 160, "5ghz")
        assert result == [200]

    # 2.4 GHz -- never expand regardless of width
    def test_2_4ghz_20mhz(self):
        assert compute_occupied_set(6, 20, "2_4ghz") == [6]

    def test_2_4ghz_40mhz_no_expand(self):
        # Spec says: don't expand in 2.4 GHz to avoid nonsense
        assert compute_occupied_set(6, 40, "2_4ghz") == [6]

    def test_2_4ghz_ch1(self):
        assert compute_occupied_set(1, 20, "2_4ghz") == [1]

    # 6 GHz
    def test_6ghz_20mhz(self):
        assert compute_occupied_set(1, 20, "6ghz") == [1]

    def test_6ghz_40mhz(self):
        result = compute_occupied_set(1, 40, "6ghz")
        assert 1 in result
        assert len(result) == 2

    def test_6ghz_80mhz(self):
        result = compute_occupied_set(1, 80, "6ghz")
        assert len(result) == 4

    # ISB band uses same logic as 5ghz
    def test_5_8ghz_isb_80mhz(self):
        assert compute_occupied_set(149, 80, "5_8ghz_isb") == [149, 153, 157, 161]

    # Unknown band
    def test_unknown_band_fallback(self):
        assert compute_occupied_set(7, 80, "unknown") == [7]


# ---------------------------------------------------------------------------
# band_for_frequency
# ---------------------------------------------------------------------------

class TestBandForFrequency:
    def test_2_4ghz(self):
        assert band_for_frequency(2437) == "2_4ghz"

    def test_2_4ghz_low_edge(self):
        assert band_for_frequency(2400) == "2_4ghz"

    def test_2_4ghz_high_edge(self):
        assert band_for_frequency(2500) == "2_4ghz"

    def test_5ghz(self):
        assert band_for_frequency(5180) == "5ghz"

    def test_5ghz_low_edge(self):
        assert band_for_frequency(5150) == "5ghz"

    def test_5_8ghz_isb(self):
        # 5800 MHz is in the ISB range -- ISB label should win over plain 5ghz
        assert band_for_frequency(5800) == "5_8ghz_isb"

    def test_5_8ghz_isb_low_edge(self):
        assert band_for_frequency(5725) == "5_8ghz_isb"

    def test_5_8ghz_isb_high_edge(self):
        assert band_for_frequency(5875) == "5_8ghz_isb"

    def test_6ghz(self):
        assert band_for_frequency(5955) == "6ghz"

    def test_6ghz_low_edge(self):
        assert band_for_frequency(5925) == "6ghz"

    def test_6ghz_high_edge(self):
        assert band_for_frequency(7125) == "6ghz"

    def test_sub_ghz(self):
        assert band_for_frequency(433) == "sub_ghz"

    def test_sub_ghz_boundary(self):
        assert band_for_frequency(999) == "sub_ghz"

    def test_unknown(self):
        assert band_for_frequency(3000) == "unknown"

    def test_unknown_high(self):
        assert band_for_frequency(7200) == "unknown"


# ---------------------------------------------------------------------------
# channel_for_frequency
# ---------------------------------------------------------------------------

class TestChannelForFrequency:
    def test_2_4ghz_ch1(self):
        # 2412 MHz -> channel 1
        assert channel_for_frequency(2412) == 1

    def test_2_4ghz_ch6(self):
        # 2437 MHz -> channel 6
        assert channel_for_frequency(2437) == 6

    def test_2_4ghz_ch11(self):
        # 2462 MHz -> channel 11
        assert channel_for_frequency(2462) == 11

    def test_5ghz_ch36(self):
        # 5180 MHz -> channel 36 ((5180-5000)//5 = 36)
        assert channel_for_frequency(5180) == 36

    def test_5ghz_ch44(self):
        # 5220 MHz -> channel 44
        assert channel_for_frequency(5220) == 44

    def test_5ghz_ch149(self):
        # 5745 MHz -> channel 149
        assert channel_for_frequency(5745) == 149

    def test_6ghz_ch1(self):
        # 5955 MHz -> channel 1  ((5955-5955)//5+1 = 1)
        assert channel_for_frequency(5955) == 1

    def test_6ghz_ch5(self):
        # 5975 MHz -> channel 5
        assert channel_for_frequency(5975) == 5

    def test_unknown_returns_none(self):
        assert channel_for_frequency(3000) is None

    def test_sub_ghz_returns_none(self):
        assert channel_for_frequency(433) is None
