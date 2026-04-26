"""
Tests for the alert engine's friendly-drone suppression.

Verifies that drones tagged 'friendly' do not fire new_drone, altitude,
speed, or signal_lost alerts when alert_friendly_enabled=false. Default
behavior (enabled=true) keeps all existing rules firing.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.models import DroneIDDevice, AlertType  # noqa: E402
from backend.alert_engine import AlertEngine  # noqa: E402


def _make_alert_engine(friendly_enabled=True):
    """Create an alert engine with mocked DB and all rules enabled."""
    db = MagicMock()

    def _get_setting(key, default=''):
        if key == 'alert_friendly_enabled':
            return 'true' if friendly_enabled else 'false'
        if key.startswith('alert_') and key.endswith('_enabled'):
            return 'true'
        if key == 'alert_rules':
            # Empty string -> engine uses DEFAULT_ALERT_RULES
            return ''
        if key == 'vendor_serial_prefixes' or key == 'vendor_mac_oui':
            return ''
        return default

    db.get_setting.side_effect = _get_setting
    db.insert_alert.return_value = 1
    db.get_alerts.return_value = ([], 0)

    engine = AlertEngine(db, gps_engine=None)
    # Disable the pending-new deferral so new-drone alerts fire immediately
    engine._new_drone_delay = 0.0
    return engine, db


def _make_device(serial='SERIAL-TEST', mac='AA:BB:CC:DD:EE:FF',
                 disposition='unknown', **kwargs):
    return DroneIDDevice(
        serial_number=serial,
        mac_address=mac,
        disposition=disposition,
        drone_lat=35.0,
        drone_lon=-78.0,
        **kwargs,
    )


class TestFriendlySuppression(unittest.TestCase):

    def test_friendly_drone_fires_alerts_by_default(self):
        """When alert_friendly_enabled is true (default), friendly drones still
        generate alerts — matches current behavior, avoids regressions."""
        engine, _db = _make_alert_engine(friendly_enabled=True)
        device = _make_device(disposition='friendly')
        engine.evaluate(device)
        # Allow the pending-new flush to run
        engine._flush_pending_new()
        pending = engine.get_pending_alerts()
        types = {a.get('alert_type') for a in pending}
        self.assertIn(AlertType.NEW_DRONE.value, types)

    def test_friendly_drone_suppressed_when_disabled(self):
        """With alert_friendly_enabled=false, no alerts fire for friendly."""
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='friendly', drone_height_agl=500.0,
                              speed=100.0)
        engine.evaluate(device)
        engine._flush_pending_new()
        pending = engine.get_pending_alerts()
        self.assertEqual(pending, [],
                         'Friendly drone should emit no alerts when suppressed')

    def test_non_friendly_drone_still_fires_when_friendly_disabled(self):
        """Turning off friendly alerts must not affect threat/unknown drones."""
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='unknown')
        engine.evaluate(device)
        engine._flush_pending_new()
        pending = engine.get_pending_alerts()
        types = {a.get('alert_type') for a in pending}
        self.assertIn(AlertType.NEW_DRONE.value, types)

    def test_threat_drone_still_fires_when_friendly_disabled(self):
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='threat')
        engine.evaluate(device)
        engine._flush_pending_new()
        pending = engine.get_pending_alerts()
        types = {a.get('alert_type') for a in pending}
        self.assertIn(AlertType.NEW_DRONE.value, types)

    def test_friendly_drone_marked_known_even_when_suppressed(self):
        """A friendly drone seen while suppressed should be recorded as known.
        Otherwise re-enabling alerts later would immediately fire a 'new drone'
        for a drone the operator already accepts."""
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='friendly')
        engine.evaluate(device)

        # Flip the toggle on and re-evaluate — should not fire new_drone
        engine._friendly_alerts_enabled = True
        engine.evaluate(device)
        engine._flush_pending_new()
        pending = engine.get_pending_alerts()
        types = {a.get('alert_type') for a in pending}
        self.assertNotIn(AlertType.NEW_DRONE.value, types)

    def test_friendly_signal_lost_suppressed(self):
        """check_signal_lost must skip friendly drones when disabled."""
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='friendly',
                              last_seen='2000-01-01T00:00:00Z')
        engine.check_signal_lost({'SERIAL-TEST': device})
        pending = engine.get_pending_alerts()
        self.assertEqual(pending, [])

    def test_nonfriendly_signal_lost_still_fires(self):
        engine, _db = _make_alert_engine(friendly_enabled=False)
        device = _make_device(disposition='unknown',
                              last_seen='2000-01-01T00:00:00Z')
        engine.check_signal_lost({'SERIAL-TEST': device})
        pending = engine.get_pending_alerts()
        types = {a.get('alert_type') for a in pending}
        self.assertIn(AlertType.SIGNAL_LOST.value, types)

    def test_config_includes_friendly_flag(self):
        """get_config / set_config round-trip the new flag."""
        engine, db = _make_alert_engine(friendly_enabled=True)
        cfg = engine.get_config()
        self.assertIn('friendly_alerts_enabled', cfg)
        self.assertTrue(cfg['friendly_alerts_enabled'])

        # Flip via set_config path — writes to DB setting
        engine.set_config({'friendly_alerts_enabled': False})
        # set_config persisted the setting string
        db.set_setting.assert_any_call('alert_friendly_enabled', 'false')


if __name__ == '__main__':
    unittest.main()
