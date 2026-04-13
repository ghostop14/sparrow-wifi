"""
Tests for disposition handling in DroneIDEngine.

Covers: _track_device stamps disposition from cache, set_disposition
updates both DB and in-memory state, get_disposition returns cached value.
"""

import sys
import os
import threading
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.models import DroneIDDevice, DroneState, drone_state
from backend.droneid_engine import DroneIDEngine


def _make_engine(dispositions=None):
    """Create an engine with a mocked DB and GPS."""
    db = MagicMock()
    db.get_current_dispositions.return_value = dispositions or {}
    db.add_disposition_event.return_value = 1
    db.migrate_disposition_key.return_value = True

    gps = MagicMock()
    gps.get_receiver_position.return_value = (0.0, 0.0, 0.0)

    engine = DroneIDEngine(db, gps)
    # Don't run real capture threads in tests
    engine._monitoring = False
    return engine, db


def _make_device(serial='TEST-SERIAL', mac='AA:BB:CC:DD:EE:FF', **kwargs):
    d = DroneIDDevice(
        serial_number=serial,
        mac_address=mac,
        drone_lat=35.0, drone_lon=-78.0,
        **kwargs,
    )
    return d


class TestEngineDispositionStamping(unittest.TestCase):

    def test_track_device_stamps_unknown_by_default(self):
        engine, db = _make_engine()
        device = _make_device()
        engine._track_device(device)
        self.assertEqual(device.disposition, 'unknown')

    def test_track_device_stamps_from_cache(self):
        engine, db = _make_engine(dispositions={'TEST-SERIAL': 'friendly'})
        device = _make_device()
        engine._track_device(device)
        self.assertEqual(device.disposition, 'friendly')

    def test_set_disposition_updates_cache(self):
        engine, db = _make_engine()
        engine.set_disposition('DRONE-KEY', 'threat', changed_by='op')
        db.add_disposition_event.assert_called_once_with(
            'DRONE-KEY', 'threat', changed_by='op')
        self.assertEqual(engine.get_disposition('DRONE-KEY'), 'threat')

    def test_set_disposition_unknown_removes_from_cache(self):
        engine, db = _make_engine(dispositions={'DRONE-KEY': 'friendly'})
        engine.set_disposition('DRONE-KEY', 'unknown')
        self.assertEqual(engine.get_disposition('DRONE-KEY'), 'unknown')
        self.assertNotIn('DRONE-KEY', engine._dispositions)

    def test_set_disposition_stamps_active_drone(self):
        engine, db = _make_engine()
        device = _make_device()
        engine._track_device(device)
        engine.set_disposition('TEST-SERIAL', 'threat')
        # The in-memory drone should be updated
        self.assertEqual(engine._active_drones['TEST-SERIAL'].disposition, 'threat')

    def test_get_disposition_defaults_unknown(self):
        engine, db = _make_engine()
        self.assertEqual(engine.get_disposition('NONEXISTENT'), 'unknown')

    def test_track_device_second_call_preserves_disposition(self):
        engine, db = _make_engine(dispositions={'TEST-SERIAL': 'friendly'})
        device1 = _make_device()
        engine._track_device(device1)
        device2 = _make_device()
        engine._track_device(device2)
        with engine._lock:
            stored = engine._active_drones.get('TEST-SERIAL')
        self.assertIsNotNone(stored)
        self.assertEqual(stored.disposition, 'friendly')


class TestKeyMigration(unittest.TestCase):

    def test_maybe_migrate_key_transfers_disposition(self):
        engine, db = _make_engine(dispositions={'AA:BB:CC:DD:EE:FF': 'threat'})
        # Register a MAC-keyed drone first
        mac_device = _make_device(serial='', mac='AA:BB:CC:DD:EE:FF')
        # Manually set up the old entry to simulate a prior BLE-only detection
        with engine._lock:
            mac_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['AA:BB:CC:DD:EE:FF'] = mac_device

        # Now track a serial-keyed device with the same MAC
        serial_device = _make_device(serial='SERIAL-001', mac='AA:BB:CC:DD:EE:FF')
        engine._track_device(serial_device)

        # Old entry removed, new serial-keyed entry present
        with engine._lock:
            self.assertNotIn('AA:BB:CC:DD:EE:FF', engine._active_drones)
            self.assertIn('SERIAL-001', engine._active_drones)
            # Disposition should have been migrated
            self.assertEqual(engine._dispositions.get('SERIAL-001'), 'threat')

    def test_maybe_migrate_key_unknown_no_migration_event(self):
        engine, db = _make_engine(dispositions={})
        mac_device = _make_device(serial='', mac='AA:BB:CC:DD:EE:FF')
        with engine._lock:
            mac_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['AA:BB:CC:DD:EE:FF'] = mac_device

        serial_device = _make_device(serial='SERIAL-002', mac='AA:BB:CC:DD:EE:FF')
        engine._track_device(serial_device)

        db.migrate_disposition_key.assert_not_called()

    def test_ble_track_migrates_disposition_from_serial_key(self):
        """_track_ble_device must call _maybe_migrate_key so a disposition tagged
        under a serial key transfers to the BLE MAC key when the same device
        reappears via BLE."""
        engine, db = _make_engine(dispositions={'SERIAL-BLE-001': 'friendly'})

        # Pre-seed a serial-keyed entry (as if the drone was seen via WiFi first)
        serial_device = _make_device(serial='SERIAL-BLE-001', mac='11:22:33:44:55:66')
        with engine._lock:
            serial_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['SERIAL-BLE-001'] = serial_device

        # Now the same physical drone arrives via BLE (MAC-keyed)
        ble_device = _make_device(serial='', mac='11:22:33:44:55:66')
        engine._track_ble_device(ble_device)

        with engine._lock:
            # The old serial key should be gone
            self.assertNotIn('SERIAL-BLE-001', engine._active_drones)
            # The MAC key entry should exist with the migrated disposition
            self.assertIn('11:22:33:44:55:66', engine._active_drones)
            self.assertEqual(engine._dispositions.get('11:22:33:44:55:66'), 'friendly')

        # DB migration event must have been written
        db.migrate_disposition_key.assert_called_once_with('SERIAL-BLE-001', '11:22:33:44:55:66')


class TestDroneStateTimezone(unittest.TestCase):
    """Regression tests: drone_state() must not raise TypeError mixing
    aware/naive datetimes after the utcnow() → now(timezone.utc) migration."""

    def _iso_z(self, dt: datetime) -> str:
        return dt.isoformat().replace('+00:00', 'Z')

    def test_active_drone_returns_active(self):
        last_seen = datetime.now(timezone.utc) - timedelta(seconds=5)
        result = drone_state(self._iso_z(last_seen))
        self.assertEqual(result, DroneState.ACTIVE)

    def test_aging_drone_returns_aging(self):
        last_seen = datetime.now(timezone.utc) - timedelta(seconds=60)
        result = drone_state(self._iso_z(last_seen))
        self.assertEqual(result, DroneState.AGING)

    def test_stale_drone_returns_stale(self):
        last_seen = datetime.now(timezone.utc) - timedelta(seconds=200)
        result = drone_state(self._iso_z(last_seen))
        self.assertEqual(result, DroneState.STALE)

    def test_explicit_aware_now_param(self):
        now = datetime.now(timezone.utc)
        last_seen = now - timedelta(seconds=10)
        result = drone_state(self._iso_z(last_seen), now=now)
        self.assertEqual(result, DroneState.ACTIVE)

    def test_bad_timestamp_returns_stale(self):
        result = drone_state('not-a-timestamp')
        self.assertEqual(result, DroneState.STALE)


if __name__ == '__main__':
    unittest.main()
