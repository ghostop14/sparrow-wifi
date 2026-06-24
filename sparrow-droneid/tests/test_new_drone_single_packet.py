"""
Regression tests for single-packet new_drone announcement.

Root cause (fixed): new_drone alerts are deferred a few seconds so device fields
can merge across frames, and that deferred buffer was flushed ONLY from
evaluate() — i.e. only when another frame arrived. A drone that emitted a single
Remote ID frame and then went silent therefore never had its deferral window
re-examined: the new_drone alert stayed pending until the drone was evicted as
stale, so only signal_lost ever fired (operator sees a "Signal Lost" for a drone
they were never told about).

Fix: a public flush_pending_new() driven by the periodic maintenance loop emits
pending new_drone alerts once their deferral elapses, independent of frame
arrival. The deferral threshold itself is unchanged, so multi-frame drones keep
merging fields and are unaffected.
"""

import sys
import os
import time
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


def _types(engine):
    return [a.get('alert_type') for a in engine.get_pending_alerts()]


class TestSinglePacketNewDrone(unittest.TestCase):

    def test_single_packet_announced_by_timer_flush(self):
        """One frame, no second frame: new_drone is NOT emitted during evaluate
        (deferral active) but IS emitted by flush_pending_new() once the window
        elapses."""
        engine = _make_alert_engine(new_drone_delay=0.1)
        engine.evaluate(_make_device('SOLO-1', 'AA:BB:CC:DD:EE:10',
                                     drone_height_agl=50.0))
        # Still inside the deferral window: nothing announced yet.
        self.assertNotIn(AlertType.NEW_DRONE.value, _types(engine))

        time.sleep(0.15)  # window elapses with NO further frame
        engine.flush_pending_new()
        order = _types(engine)
        self.assertEqual(order.count(AlertType.NEW_DRONE.value), 1,
                         'single-packet drone must announce exactly once')

    def test_flush_is_noop_before_window(self):
        """flush_pending_new() must not emit a drone still inside its deferral
        window (preserves the merge interval for multi-frame drones)."""
        engine = _make_alert_engine(new_drone_delay=10.0)
        engine.evaluate(_make_device('SOLO-2', 'AA:BB:CC:DD:EE:11'))
        engine.flush_pending_new()  # immediate — window not elapsed
        self.assertNotIn(AlertType.NEW_DRONE.value, _types(engine))

    def test_flush_does_not_refire(self):
        """Once announced, a subsequent flush does not re-emit new_drone."""
        engine = _make_alert_engine(new_drone_delay=0.1)
        engine.evaluate(_make_device('SOLO-3', 'AA:BB:CC:DD:EE:12'))
        time.sleep(0.15)
        engine.flush_pending_new()
        engine.get_pending_alerts()  # drain the announcement
        engine.flush_pending_new()   # nothing left pending
        self.assertNotIn(AlertType.NEW_DRONE.value, _types(engine))

    def test_flush_noop_when_nothing_pending(self):
        """flush_pending_new() on an idle engine is a harmless no-op."""
        engine = _make_alert_engine(new_drone_delay=0.1)
        engine.flush_pending_new()
        self.assertEqual(_types(engine), [])

    def test_announced_single_packet_then_signal_lost_order(self):
        """End-to-end single-packet story: new_drone (via timer flush) precedes
        signal_lost for the same drone."""
        engine = _make_alert_engine(new_drone_delay=0.1)
        device = _make_device('SOLO-4', 'AA:BB:CC:DD:EE:13', drone_height_agl=50.0)
        device.last_seen = '2000-01-01T00:00:00Z'  # far in the past -> lost
        engine.evaluate(device)

        time.sleep(0.15)
        engine.flush_pending_new()                    # new_drone now
        engine.check_signal_lost({'SOLO-4': device})  # signal_lost now
        order = _types(engine)
        self.assertIn(AlertType.NEW_DRONE.value, order)
        self.assertIn(AlertType.SIGNAL_LOST.value, order)
        self.assertLess(
            order.index(AlertType.NEW_DRONE.value),
            order.index(AlertType.SIGNAL_LOST.value),
            'new_drone must precede signal_lost for a single-packet drone',
        )


if __name__ == '__main__':
    unittest.main()
