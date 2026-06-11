"""
Regression tests for new_drone alert re-arming.

Root cause (fixed): AlertEngine._known_serials was only ever added to, never
pruned, so the new_drone alert fired exactly once per drone per process
lifetime. A drone relaunched hours later was silently treated as already
known and produced no alert, even though the tracker re-acquired it.

The fix ties "forget for alerting" to the authoritative drone-gone event:
DroneIDEngine.cleanup_stale() returns the evicted keys, and the maintenance
loop hands them to AlertEngine.forget_drones(), which discards them from
_known_serials (re-arming new_drone) and the other per-drone alert sets.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.models import DroneIDDevice, AlertType  # noqa: E402
from backend.alert_engine import AlertEngine  # noqa: E402
from backend.droneid_engine import DroneIDEngine  # noqa: E402


def _make_alert_engine():
    db = MagicMock()

    def _get_setting(key, default=''):
        if key.startswith('alert_') and key.endswith('_enabled'):
            return 'true'
        if key in ('alert_rules', 'vendor_serial_prefixes', 'vendor_mac_oui'):
            return ''
        return default

    db.get_setting.side_effect = _get_setting
    db.insert_alert.return_value = 1
    db.get_alerts.return_value = ([], 0)

    engine = AlertEngine(db, gps_engine=None)
    engine._new_drone_delay = 0.0  # fire new_drone immediately, no deferral
    return engine


def _make_device(serial='SERIAL-TEST', mac='AA:BB:CC:DD:EE:FF'):
    return DroneIDDevice(serial_number=serial, mac_address=mac,
                         drone_lat=35.0, drone_lon=-78.0)


def _fired_types(engine):
    engine._flush_pending_new()
    return {a.get('alert_type') for a in engine.get_pending_alerts()}


class TestNewDroneRearm(unittest.TestCase):

    def test_known_drone_does_not_refire_without_forget(self):
        """Baseline: a second sighting of the same drone fires no new_drone."""
        engine = _make_alert_engine()
        device = _make_device()

        engine.evaluate(device)
        self.assertIn(AlertType.NEW_DRONE.value, _fired_types(engine))
        # Same drone again, still known -> no new_drone.
        engine.evaluate(device)
        self.assertNotIn(AlertType.NEW_DRONE.value, _fired_types(engine))

    def test_forget_drones_rearms_new_drone(self):
        """After forget_drones(), the same drone fires new_drone again."""
        engine = _make_alert_engine()
        device = _make_device()
        key = device.get_key()

        engine.evaluate(device)
        self.assertIn(AlertType.NEW_DRONE.value, _fired_types(engine))

        # Drone left tracking; maintenance loop forgets it.
        engine.forget_drones([key])

        engine.evaluate(device)
        self.assertIn(AlertType.NEW_DRONE.value, _fired_types(engine),
                      'Relaunched drone should re-alert after being forgotten')

    def test_forget_clears_lost_and_violation_state(self):
        """forget_drones() also clears signal-lost and violation dedup state."""
        engine = _make_alert_engine()
        key = 'SERIAL-TEST'
        engine._known_serials.add(key)
        engine._alerted_lost.add(key)
        engine._alerted_violations[key] = {AlertType.ALTITUDE_MAX.value}
        engine._pending_new[key] = (0.0, _make_device())

        engine.forget_drones([key])

        self.assertNotIn(key, engine._known_serials)
        self.assertNotIn(key, engine._alerted_lost)
        self.assertNotIn(key, engine._alerted_violations)
        self.assertNotIn(key, engine._pending_new)

    def test_forget_empty_is_noop(self):
        engine = _make_alert_engine()
        engine._known_serials.add('X')
        engine.forget_drones([])
        engine.forget_drones(None)
        self.assertIn('X', engine._known_serials)


class TestCleanupStaleReturnsKeys(unittest.TestCase):

    def test_cleanup_stale_returns_evicted_keys(self):
        """cleanup_stale must report which keys it evicted."""
        engine = DroneIDEngine(db=MagicMock(), gps_engine=MagicMock())
        old = _make_device(serial='OLD-DRONE')
        old.last_seen = '2000-01-01T00:00:00Z'  # ancient -> evicted
        fresh = _make_device(serial='FRESH-DRONE')
        fresh.last_seen = '2999-01-01T00:00:00Z'  # future -> retained
        engine._active_drones = {'OLD-DRONE': old, 'FRESH-DRONE': fresh}

        evicted = engine.cleanup_stale(max_age=300)

        self.assertEqual(evicted, ['OLD-DRONE'])
        self.assertNotIn('OLD-DRONE', engine._active_drones)
        self.assertIn('FRESH-DRONE', engine._active_drones)


if __name__ == '__main__':
    unittest.main()
