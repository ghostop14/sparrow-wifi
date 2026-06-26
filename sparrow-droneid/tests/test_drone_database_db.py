"""
Tests for Database.get_drone_database() (database.py).

Verifies:
  - One row per serial (dedup even on identical-timestamp tie).
  - Latest drone position = newest frame.
  - Controller = most-recent NON-ZERO operator frame, even when the newest
    frame's operator is 0,0.
  - detection_count / first_seen / last_seen correct.
  - ORDER BY last_seen DESC (newest serial first).
  - Serial that never had a non-zero operator → operator_* are None.
  - ua_type_name and time_in_area_seconds populated correctly.
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.database import Database
from backend.models import DroneIDDevice


def _make_db():
    """In-memory SQLite database for tests."""
    return Database(db_path=':memory:')


def _ts(offset_seconds: float = 0) -> str:
    """UTC ISO timestamp at an offset from epoch 2026-01-01T00:00:00Z."""
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dt = base + timedelta(seconds=offset_seconds)
    return dt.isoformat().replace('+00:00', 'Z')


def _device(serial: str, drone_lat: float = 1.0, drone_lon: float = 2.0,
            operator_lat: float = 0.0, operator_lon: float = 0.0,
            ua_type: int = 2, ts: str = None) -> DroneIDDevice:
    dev = DroneIDDevice(
        serial_number=serial,
        drone_lat=drone_lat,
        drone_lon=drone_lon,
        drone_alt_geo=100.0,
        operator_lat=operator_lat,
        operator_lon=operator_lon,
        ua_type=ua_type,
    )
    dev.last_seen = ts or _ts(0)
    return dev


class TestGetDroneDatabase(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()

    # ------------------------------------------------------------------
    # Basic one-row-per-serial

    def test_one_row_per_serial(self):
        """Multiple frames for the same serial produce exactly one row."""
        dev = _device('SN-AAA', ts=_ts(0))
        self.db.insert_detection(dev)
        dev2 = _device('SN-AAA', drone_lat=1.5, ts=_ts(10))
        self.db.insert_detection(dev2)

        rows = self.db.get_drone_database()
        serials = [r['serial_number'] for r in rows]
        self.assertEqual(serials.count('SN-AAA'), 1)

    def test_latest_drone_position(self):
        """Row uses the newest frame's drone position."""
        self.db.insert_detection(_device('SN-BBB', drone_lat=10.0, ts=_ts(0)))
        self.db.insert_detection(_device('SN-BBB', drone_lat=20.0, ts=_ts(5)))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-BBB')
        self.assertAlmostEqual(row['drone_lat'], 20.0, places=4)

    def test_detection_count(self):
        """detection_count equals the number of inserted frames."""
        for i in range(5):
            self.db.insert_detection(_device('SN-CCC', ts=_ts(i)))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-CCC')
        self.assertEqual(row['detection_count'], 5)

    def test_first_seen_and_last_seen(self):
        """first_seen is the earliest frame; last_seen is the latest."""
        t0 = _ts(0)
        t1 = _ts(60)
        self.db.insert_detection(_device('SN-DDD', ts=t0))
        self.db.insert_detection(_device('SN-DDD', ts=t1))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-DDD')
        self.assertEqual(row['first_seen'], t0)
        self.assertEqual(row['last_seen'], t1)

    def test_order_by_last_seen_desc(self):
        """Rows are ordered newest-first."""
        self.db.insert_detection(_device('SN-EEE', ts=_ts(0)))
        self.db.insert_detection(_device('SN-FFF', ts=_ts(100)))
        rows = self.db.get_drone_database()
        serials = [r['serial_number'] for r in rows]
        fff_i = serials.index('SN-FFF')
        eee_i = serials.index('SN-EEE')
        self.assertLess(fff_i, eee_i)

    # ------------------------------------------------------------------
    # Controller (operator) position logic

    def test_controller_from_most_recent_nonzero_operator(self):
        """Controller position comes from the most-recent frame with non-zero operator,
        even when the newest frame has operator 0,0."""
        # Frame 1: has operator coords
        self.db.insert_detection(
            _device('SN-GGG', operator_lat=33.0, operator_lon=-80.0, ts=_ts(0)))
        # Frame 2 (newer): no operator coords
        self.db.insert_detection(
            _device('SN-GGG', operator_lat=0.0, operator_lon=0.0, ts=_ts(10)))

        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-GGG')
        self.assertIsNotNone(row['operator_lat'])
        self.assertAlmostEqual(row['operator_lat'], 33.0, places=4)
        self.assertAlmostEqual(row['operator_lon'], -80.0, places=4)

    def test_no_controller_when_operator_always_zero(self):
        """operator_lat/lon are None when no frame ever had non-zero operator coords."""
        self.db.insert_detection(
            _device('SN-HHH', operator_lat=0.0, operator_lon=0.0, ts=_ts(0)))
        self.db.insert_detection(
            _device('SN-HHH', operator_lat=0.0, operator_lon=0.0, ts=_ts(5)))

        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-HHH')
        self.assertIsNone(row['operator_lat'])
        self.assertIsNone(row['operator_lon'])
        self.assertIsNone(row['operator_alt'])
        self.assertIsNone(row['controller_last_seen'])

    def test_controller_present_for_only_nonzero_frame(self):
        """When only one frame has non-zero operator, that frame's coords are returned."""
        self.db.insert_detection(
            _device('SN-III', operator_lat=50.0, operator_lon=10.0, ts=_ts(0)))

        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-III')
        self.assertAlmostEqual(row['operator_lat'], 50.0, places=4)
        self.assertAlmostEqual(row['operator_lon'], 10.0, places=4)

    # ------------------------------------------------------------------
    # Enrichment fields

    def test_ua_type_name_populated(self):
        """ua_type_name is set to the human-readable UA type string."""
        self.db.insert_detection(_device('SN-JJJ', ua_type=2, ts=_ts(0)))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-JJJ')
        self.assertEqual(row['ua_type_name'], 'Helicopter / Multirotor')

    def test_ua_type_name_unknown_for_out_of_range(self):
        """ua_type_name is 'Unknown' for values outside 0-15."""
        dev = _device('SN-KKK', ua_type=0, ts=_ts(0))
        # Patch ua_type to an out-of-range value after construction
        dev.ua_type = 99
        self.db.insert_detection(dev)
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-KKK')
        self.assertEqual(row['ua_type_name'], 'Unknown')

    def test_time_in_area_seconds(self):
        """time_in_area_seconds is last_seen - first_seen in integer seconds."""
        self.db.insert_detection(_device('SN-LLL', ts=_ts(0)))
        self.db.insert_detection(_device('SN-LLL', ts=_ts(120)))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-LLL')
        self.assertEqual(row['time_in_area_seconds'], 120)

    def test_time_in_area_zero_for_single_frame(self):
        """time_in_area_seconds is 0 when first_seen == last_seen."""
        self.db.insert_detection(_device('SN-MMM', ts=_ts(0)))
        rows = self.db.get_drone_database()
        row = next(r for r in rows if r['serial_number'] == 'SN-MMM')
        self.assertEqual(row['time_in_area_seconds'], 0)

    # ------------------------------------------------------------------
    # Identical-timestamp tie (dedup guard)

    def test_identical_timestamp_tie_single_row(self):
        """Identical-timestamp tie on the latest frame produces exactly one row."""
        ts = _ts(50)
        # Two inserts with the same timestamp and serial → both match 'latest' in JOIN
        dev1 = _device('SN-NNN', drone_lat=10.0, ts=ts)
        dev2 = _device('SN-NNN', drone_lat=11.0, ts=ts)
        self.db.insert_detection(dev1)
        self.db.insert_detection(dev2)

        rows = self.db.get_drone_database()
        nnn_rows = [r for r in rows if r['serial_number'] == 'SN-NNN']
        self.assertEqual(len(nnn_rows), 1)

    # ------------------------------------------------------------------
    # Multiple serials independent

    def test_multiple_serials_independent(self):
        """Each serial gets its own row with its own data."""
        self.db.insert_detection(_device('SN-OOO', drone_lat=1.0, ts=_ts(0)))
        self.db.insert_detection(_device('SN-PPP', drone_lat=2.0, ts=_ts(5)))

        rows = self.db.get_drone_database()
        self.assertEqual(len(rows), 2)
        serials = {r['serial_number'] for r in rows}
        self.assertIn('SN-OOO', serials)
        self.assertIn('SN-PPP', serials)


if __name__ == '__main__':
    unittest.main()
