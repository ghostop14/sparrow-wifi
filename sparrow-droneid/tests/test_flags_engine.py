"""
Tests for flag handling in DroneIDEngine.

Covers: set_flag/get_flags round-trip; cache stays sparse when cleared;
_recover_flags applies onto a DroneIDDevice on appearance; key-migration
carries flags forward even when disposition is absent.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.models import DroneIDDevice
from backend.droneid_engine import DroneIDEngine


def _make_engine(dispositions=None, flags=None):
    """Create an engine with mocked DB and GPS."""
    db = MagicMock()
    db.get_current_dispositions.return_value = dispositions or {}
    db.get_current_flags.return_value = flags or {}
    db.add_disposition_event.return_value = 1
    db.add_flag_event.return_value = 1
    db.migrate_disposition_key.return_value = True
    db.migrate_flags_key.return_value = True

    gps = MagicMock()
    gps.get_receiver_position.return_value = (0.0, 0.0, 0.0)

    engine = DroneIDEngine(db, gps)
    engine._monitoring = False
    return engine, db


def _make_device(serial='TEST-SERIAL', mac='AA:BB:CC:DD:EE:FF', **kwargs):
    return DroneIDDevice(
        serial_number=serial,
        mac_address=mac,
        drone_lat=35.0, drone_lon=-78.0,
        **kwargs,
    )


class TestSetGetFlags(unittest.TestCase):

    def test_set_flag_updates_cache(self):
        engine, db = _make_engine()
        engine.set_flag('DRONE-KEY', 'military', True, changed_by='op')
        db.add_flag_event.assert_called_once_with(
            'DRONE-KEY', 'military', True, changed_by='op')
        flags = engine.get_flags('DRONE-KEY')
        self.assertTrue(flags['military'])
        self.assertFalse(flags['law_enforcement'])

    def test_set_flag_two_independent_flags(self):
        engine, db = _make_engine()
        engine.set_flag('DRONE-KEY', 'military', True)
        engine.set_flag('DRONE-KEY', 'law_enforcement', True)
        flags = engine.get_flags('DRONE-KEY')
        self.assertTrue(flags['military'])
        self.assertTrue(flags['law_enforcement'])

    def test_set_flag_independence_setting_one_does_not_clear_other(self):
        engine, db = _make_engine()
        engine.set_flag('DRONE-KEY', 'military', True)
        engine.set_flag('DRONE-KEY', 'military', False)
        engine.set_flag('DRONE-KEY', 'law_enforcement', True)
        flags = engine.get_flags('DRONE-KEY')
        self.assertFalse(flags['military'])
        self.assertTrue(flags['law_enforcement'])

    def test_set_flag_false_removes_from_cache(self):
        engine, db = _make_engine(flags={'DRONE-KEY': {'military': True}})
        engine.set_flag('DRONE-KEY', 'military', False)
        flags = engine.get_flags('DRONE-KEY')
        self.assertFalse(flags['military'])

    def test_cache_stays_sparse_when_last_flag_cleared(self):
        engine, db = _make_engine(flags={'DRONE-KEY': {'military': True}})
        engine.set_flag('DRONE-KEY', 'military', False)
        with engine._lock:
            self.assertNotIn('DRONE-KEY', engine._flags)

    def test_get_flags_defaults_both_false_for_unknown_key(self):
        engine, db = _make_engine()
        flags = engine.get_flags('NONEXISTENT')
        self.assertFalse(flags['military'])
        self.assertFalse(flags['law_enforcement'])

    def test_set_flag_stamps_active_drone(self):
        engine, db = _make_engine()
        device = _make_device()
        engine._track_device(device)
        engine.set_flag('TEST-SERIAL', 'military', True)
        with engine._lock:
            self.assertTrue(engine._active_drones['TEST-SERIAL'].military)


class TestRecoverFlags(unittest.TestCase):

    def test_track_device_stamps_flags_from_cache(self):
        engine, db = _make_engine(flags={'TEST-SERIAL': {'military': True}})
        device = _make_device()
        engine._track_device(device)
        self.assertTrue(device.military)
        self.assertFalse(device.law_enforcement)

    def test_track_device_both_flags_default_false(self):
        engine, db = _make_engine()
        device = _make_device()
        engine._track_device(device)
        self.assertFalse(device.military)
        self.assertFalse(device.law_enforcement)

    def test_track_device_preserves_flags_on_second_call(self):
        engine, db = _make_engine(flags={'TEST-SERIAL': {'law_enforcement': True}})
        device1 = _make_device()
        engine._track_device(device1)
        device2 = _make_device()
        engine._track_device(device2)
        with engine._lock:
            stored = engine._active_drones.get('TEST-SERIAL')
        self.assertIsNotNone(stored)
        self.assertTrue(stored.law_enforcement)

    def test_recover_flags_from_legacy_serial_for_ble(self):
        """Flag stored under serial key is recovered when drone reappears MAC-keyed."""
        engine, db = _make_engine(flags={'LEGACY-SERIAL': {'military': True}})
        ble_device = _make_device(serial='LEGACY-SERIAL', mac='AB:CD:EF:01:02:03')
        engine._track_ble_device(ble_device)

        with engine._lock:
            self.assertIn('AB:CD:EF:01:02:03', engine._active_drones)
            entry = engine._active_drones['AB:CD:EF:01:02:03']
            self.assertTrue(entry.military)
            # Cache migrated to MAC key
            self.assertIn('AB:CD:EF:01:02:03', engine._flags)
            self.assertNotIn('LEGACY-SERIAL', engine._flags)

        db.migrate_flags_key.assert_called_with('LEGACY-SERIAL', 'AB:CD:EF:01:02:03')


class TestKeyMigrationWithFlags(unittest.TestCase):

    def test_maybe_migrate_key_transfers_flags(self):
        engine, db = _make_engine(flags={'AA:BB:CC:DD:EE:FF': {'military': True}})
        mac_device = _make_device(serial='', mac='AA:BB:CC:DD:EE:FF')
        with engine._lock:
            mac_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['AA:BB:CC:DD:EE:FF'] = mac_device

        serial_device = _make_device(serial='SERIAL-001', mac='AA:BB:CC:DD:EE:FF')
        engine._track_device(serial_device)

        with engine._lock:
            self.assertNotIn('AA:BB:CC:DD:EE:FF', engine._active_drones)
            self.assertIn('SERIAL-001', engine._active_drones)
            self.assertIn('military', engine._flags.get('SERIAL-001', {}))

    def test_migration_fires_when_flags_present_and_disposition_absent(self):
        """Key migration must execute migrate_flags_key even when disposition
        is absent — this is the divergence case specified by the plan."""
        # Flags present, no disposition
        engine, db = _make_engine(dispositions={},
                                  flags={'AA:BB:CC:DD:EE:FF': {'law_enforcement': True}})
        mac_device = _make_device(serial='', mac='AA:BB:CC:DD:EE:FF')
        with engine._lock:
            mac_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['AA:BB:CC:DD:EE:FF'] = mac_device

        serial_device = _make_device(serial='SERIAL-002', mac='AA:BB:CC:DD:EE:FF')
        engine._track_device(serial_device)

        # migrate_flags_key must have been called; migrate_disposition_key must NOT
        db.migrate_flags_key.assert_called_once_with('AA:BB:CC:DD:EE:FF', 'SERIAL-002')
        db.migrate_disposition_key.assert_not_called()

    def test_ble_migration_transfers_flags(self):
        """_track_ble_device must carry flags forward when MAC collides with serial key."""
        engine, db = _make_engine(flags={'SERIAL-BLE-001': {'military': True}})
        serial_device = _make_device(serial='SERIAL-BLE-001', mac='11:22:33:44:55:66')
        with engine._lock:
            serial_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['SERIAL-BLE-001'] = serial_device

        ble_device = _make_device(serial='', mac='11:22:33:44:55:66')
        engine._track_ble_device(ble_device)

        with engine._lock:
            self.assertIn('11:22:33:44:55:66', engine._active_drones)
            entry = engine._active_drones['11:22:33:44:55:66']
            self.assertTrue(entry.military)

        db.migrate_flags_key.assert_called_once_with('SERIAL-BLE-001', '11:22:33:44:55:66')

    def test_set_flag_resolves_ble_serial_to_mac_key(self):
        """Tagging a BLE drone by serial resolves to the MAC-keyed entry."""
        engine, db = _make_engine()
        ble_device = _make_device(serial='BLE-SERIAL-X', mac='AA:BB:CC:11:22:33')
        with engine._lock:
            ble_device.first_seen = '2026-04-01T10:00:00Z'
            engine._active_drones['AA:BB:CC:11:22:33'] = ble_device

        engine.set_flag('BLE-SERIAL-X', 'military', True, changed_by='op')

        with engine._lock:
            self.assertIn('military', engine._flags.get('AA:BB:CC:11:22:33', {}))
            self.assertNotIn('BLE-SERIAL-X', engine._flags)
            self.assertTrue(engine._active_drones['AA:BB:CC:11:22:33'].military)

        db.add_flag_event.assert_called_once_with(
            'AA:BB:CC:11:22:33', 'military', True, changed_by='op')


if __name__ == '__main__':
    unittest.main()
