"""Tests for sparrow_elastic.ecs_helpers."""

import pytest
from datetime import datetime, timezone, timedelta
from sparrow_elastic.ecs_helpers import to_es_timestamp, DAY_OF_WEEK


class TestToEsTimestamp:
    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2024, 3, 15, 14, 30, 0, 0)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.000Z"

    def test_utc_aware_datetime(self):
        dt = datetime(2024, 3, 15, 14, 30, 0, 0, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.000Z"

    def test_non_utc_tz_converted_to_utc(self):
        # UTC+5: 2024-03-15 19:30:00+05:00 == 2024-03-15 14:30:00 UTC
        east5 = timezone(timedelta(hours=5))
        dt = datetime(2024, 3, 15, 19, 30, 0, 0, tzinfo=east5)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.000Z"

    def test_millisecond_precision(self):
        # 123456 microseconds -> 123 ms
        dt = datetime(2024, 3, 15, 14, 30, 0, 123456, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.123Z"

    def test_z_suffix_not_plus_00_00(self):
        dt = datetime(2024, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result.endswith("Z")
        assert "+00:00" not in result

    def test_zero_microseconds(self):
        dt = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result == "2024-01-01T00:00:00.000Z"

    def test_999ms_precision(self):
        dt = datetime(2024, 3, 15, 14, 30, 0, 999000, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.999Z"

    def test_fractional_ms_truncated_not_rounded(self):
        # 999999 microseconds -> 999 ms (truncation, not rounding to 1000)
        dt = datetime(2024, 3, 15, 14, 30, 0, 999999, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        assert result == "2024-03-15T14:30:00.999Z"

    def test_format_structure(self):
        dt = datetime(2024, 6, 5, 9, 7, 3, 50000, tzinfo=timezone.utc)
        result = to_es_timestamp(dt)
        # Expect YYYY-MM-DDTHH:MM:SS.mmmZ
        assert len(result) == 24
        assert result[10] == "T"
        assert result[19] == "."
        assert result[23] == "Z"


class TestDayOfWeek:
    def test_length(self):
        assert len(DAY_OF_WEEK) == 7

    def test_monday_is_index_0(self):
        assert DAY_OF_WEEK[0] == "Monday"

    def test_sunday_is_index_6(self):
        assert DAY_OF_WEEK[6] == "Sunday"

    def test_order(self):
        expected = (
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        )
        assert DAY_OF_WEEK == expected
