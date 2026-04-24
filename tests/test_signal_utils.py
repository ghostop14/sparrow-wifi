"""Tests for sparrow_elastic.signal_utils."""

import math
import pytest
from sparrow_elastic.signal_utils import dbm_to_mw, quality_0_to_5


# ---------------------------------------------------------------------------
# dbm_to_mw
# ---------------------------------------------------------------------------

class TestDbmToMw:
    def test_none_returns_none(self):
        assert dbm_to_mw(None) is None

    def test_minus70_dbm(self):
        result = dbm_to_mw(-70.0)
        assert result is not None
        # -70 dBm = 10^(-70/10) = 10^(-7) = 1e-7 mW
        assert math.isclose(result, 1e-7, rel_tol=1e-6)

    def test_zero_dbm(self):
        result = dbm_to_mw(0.0)
        assert result is not None
        # 0 dBm = 10^(0/10) = 10^0 = 1.0 mW
        assert math.isclose(result, 1.0, rel_tol=1e-9)

    def test_positive_dbm_clipped_to_one_mw(self):
        # +10 dBm is anomalous; should be clipped to 0 dBm → 1.0 mW
        result = dbm_to_mw(10.0)
        assert result is not None
        assert math.isclose(result, 1.0, rel_tol=1e-9)

    def test_positive_dbm_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="sparrow_elastic.signal_utils"):
            dbm_to_mw(5.0)
        assert any("anomalous" in record.message for record in caplog.records)

    def test_minus50_dbm(self):
        result = dbm_to_mw(-50.0)
        assert result is not None
        assert math.isclose(result, 10 ** (-50.0 / 10.0), rel_tol=1e-9)


# ---------------------------------------------------------------------------
# quality_0_to_5
# ---------------------------------------------------------------------------

class TestQuality0To5:
    def test_none_returns_none(self):
        assert quality_0_to_5(None) is None

    def test_strong_signal_5_bars(self):
        assert quality_0_to_5(-45.0) == 5

    def test_boundary_minus50_is_5(self):
        assert quality_0_to_5(-50.0) == 5

    def test_4_bars(self):
        assert quality_0_to_5(-55.0) == 4

    def test_boundary_minus60_is_4(self):
        assert quality_0_to_5(-60.0) == 4

    def test_3_bars(self):
        assert quality_0_to_5(-65.0) == 3

    def test_boundary_minus70_is_3(self):
        assert quality_0_to_5(-70.0) == 3

    def test_2_bars(self):
        assert quality_0_to_5(-75.0) == 2

    def test_boundary_minus80_is_2(self):
        assert quality_0_to_5(-80.0) == 2

    def test_1_bar(self):
        assert quality_0_to_5(-85.0) == 1

    def test_boundary_minus90_is_1(self):
        assert quality_0_to_5(-90.0) == 1

    def test_0_bars(self):
        assert quality_0_to_5(-95.0) == 0
