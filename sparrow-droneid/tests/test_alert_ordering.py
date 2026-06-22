"""
Regression tests for new_drone alert ordering.

Root cause (fixed): new_drone alerts are deferred a few seconds to let device
fields fill in over multiple frames, but threshold alerts (altitude/speed) fired
immediately on the first qualifying frame. A brand-new drone already above the
altitude ceiling therefore produced its altitude_max alert BEFORE its new_drone
alert (with new_drone lagging until a later frame flushed the deferral).

Fix: a threshold violation force-emits a still-pending new_drone first, so the
identity alert always precedes the condition alert for the same drone, while the
deferral is preserved for the normal (non-violating) case.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.models import DroneIDDevice, AlertType  # noqa: E402
from backend.alert_engine import AlertEngine  # noqa: E402


def _make_alert_engine(new_drone_delay=4.0):
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
    engine._new_drone_delay = new_drone_delay  # keep the deferral ACTIVE
    return engine


def _make_device(serial, mac, **kwargs):
    return DroneIDDevice(serial_number=serial, mac_address=mac,
                         drone_lat=35.0, drone_lon=-78.0, **kwargs)


def _types_in_order(engine):
    return [a.get('alert_type') for a in engine.get_pending_alerts()]


class TestNewDroneOrdering(unittest.TestCase):

    def test_new_drone_precedes_altitude_on_first_frame(self):
        """A brand-new drone already over the ceiling fires new_drone BEFORE
        altitude_max on the very first frame, even with the deferral active."""
        engine = _make_alert_engine(new_drone_delay=4.0)
        device = _make_device('OVER-ALT', 'AA:BB:CC:DD:EE:01',
                              drone_height_agl=200.0)  # > 122 m default
        engine.evaluate(device)
        order = _types_in_order(engine)
        self.assertIn(AlertType.NEW_DRONE.value, order)
        self.assertIn(AlertType.ALTITUDE_MAX.value, order)
        self.assertLess(
            order.index(AlertType.NEW_DRONE.value),
            order.index(AlertType.ALTITUDE_MAX.value),
            'new_drone must precede altitude_max for the same drone',
        )

    def test_new_drone_precedes_speed_on_first_frame(self):
        engine = _make_alert_engine(new_drone_delay=4.0)
        device = _make_device('OVER-SPD', 'AA:BB:CC:DD:EE:02',
                              speed=60.0)  # > 44.7 m/s default
        engine.evaluate(device)
        order = _types_in_order(engine)
        self.assertIn(AlertType.NEW_DRONE.value, order)
        self.assertIn(AlertType.SPEED_MAX.value, order)
        self.assertLess(
            order.index(AlertType.NEW_DRONE.value),
            order.index(AlertType.SPEED_MAX.value),
        )

    def test_new_drone_fires_once_when_both_violations(self):
        """Altitude + speed both violating: new_drone emitted exactly once and
        first."""
        engine = _make_alert_engine(new_drone_delay=4.0)
        device = _make_device('OVER-BOTH', 'AA:BB:CC:DD:EE:03',
                              drone_height_agl=200.0, speed=60.0)
        engine.evaluate(device)
        order = _types_in_order(engine)
        self.assertEqual(order.count(AlertType.NEW_DRONE.value), 1)
        self.assertEqual(order[0], AlertType.NEW_DRONE.value)

    def test_non_violating_new_drone_still_deferred(self):
        """Without a co-occurring violation, new_drone keeps its deferral and
        does not fire on the first frame before the delay elapses."""
        engine = _make_alert_engine(new_drone_delay=4.0)
        device = _make_device('NORMAL', 'AA:BB:CC:DD:EE:04',
                              drone_height_agl=50.0)  # legal altitude
        engine.evaluate(device)
        self.assertNotIn(AlertType.NEW_DRONE.value, _types_in_order(engine))

    def test_known_drone_violation_does_not_refire_new_drone(self):
        """A drone already announced that later climbs over the ceiling fires
        altitude_max but does NOT re-fire new_drone."""
        engine = _make_alert_engine(new_drone_delay=0.0)  # announce immediately
        engine.evaluate(_make_device('KNOWN', 'AA:BB:CC:DD:EE:05',
                                     drone_height_agl=50.0))
        engine.get_pending_alerts()  # drain the initial new_drone
        engine.evaluate(_make_device('KNOWN', 'AA:BB:CC:DD:EE:05',
                                     drone_height_agl=200.0))
        order = _types_in_order(engine)
        self.assertNotIn(AlertType.NEW_DRONE.value, order)
        self.assertIn(AlertType.ALTITUDE_MAX.value, order)


if __name__ == '__main__':
    unittest.main()
