"""Tests for sparrow_elastic.controller_signature."""

import pytest
from sparrow_elastic.controller_signature import is_controller_candidate


class TestIsControllerCandidate:
    # --- True cases ---

    def test_dji_vendor_isb_strong_signal(self):
        """DJI vendor + strong signal + ISB band -> True."""
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="DJI Technology Co., Ltd.",
        ) is True

    def test_dji_vendor_2_4ghz_strong_signal(self):
        """DJI vendor + strong signal + 2.4 GHz band -> True."""
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-60.0,
            device_class="unknown",
            mac_vendor="DJI",
        ) is True

    def test_drone_controller_class_2_4ghz(self):
        """device_class=drone_controller overrides vendor check."""
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="drone_controller",
            mac_vendor="Acme Corp",
        ) is True

    def test_drone_controller_class_any_vendor_isb(self):
        """drone_controller class with None vendor + ISB band -> True."""
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-55.0,
            device_class="drone_controller",
            mac_vendor=None,
        ) is True

    def test_autel_vendor(self):
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="Autel Robotics",
        ) is True

    def test_holy_stone_vendor(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="Holy Stone",
        ) is True

    def test_parrot_vendor(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-65.0,
            device_class="unknown",
            mac_vendor="Parrot SA",
        ) is True

    def test_skydio_vendor(self):
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="Skydio",
        ) is True

    def test_yuneec_vendor(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="Yuneec International",
        ) is True

    # --- False cases: weak signal ---

    def test_dji_vendor_weak_signal_exactly_minus70(self):
        """Signal exactly -70 dBm is NOT > -70 -- should return False."""
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-70.0,
            device_class="unknown",
            mac_vendor="DJI",
        ) is False

    def test_dji_vendor_weak_signal(self):
        """Signal -75 dBm is too weak -> False."""
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-75.0,
            device_class="unknown",
            mac_vendor="DJI",
        ) is False

    def test_none_signal(self):
        """None signal -> False regardless of vendor/band."""
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=None,
            device_class="unknown",
            mac_vendor="DJI",
        ) is False

    # --- False cases: wrong band ---

    def test_dji_vendor_plain_5ghz_not_isb(self):
        """5ghz (not ISB) band is not in the controller bands set -> False."""
        assert is_controller_candidate(
            rf_band="5ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="DJI",
        ) is False

    def test_dji_vendor_6ghz_band(self):
        assert is_controller_candidate(
            rf_band="6ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="DJI",
        ) is False

    # --- False cases: unrecognised vendor ---

    def test_random_vendor_acme(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="Acme Corp",
        ) is False

    def test_none_vendor_unknown_class(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor=None,
        ) is False

    def test_empty_vendor_unknown_class(self):
        assert is_controller_candidate(
            rf_band="2_4ghz",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="",
        ) is False

    # --- Case insensitivity ---

    def test_dji_lowercase(self):
        assert is_controller_candidate(
            rf_band="5_8ghz_isb",
            signal_dbm=-50.0,
            device_class="unknown",
            mac_vendor="dji technologies",
        ) is True
