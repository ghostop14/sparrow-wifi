"""
Tests for Google Maps links in alert messages.

Alert messages (Slack + the plain-text ECS/API summary) include a tap-to-pushpin
Google Maps link under the drone coordinates/altitude, and a SEPARATE link for
the controller/operator position when known (a Google Maps URL can't show two
distinctly-iconed pins in one map).
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.alert_engine import AlertEngine  # noqa: E402


def _make_engine():
    db = MagicMock()

    def _get_setting(key, default=''):
        if key.startswith('alert_') and key.endswith('_enabled'):
            return 'true'
        return default

    db.get_setting.side_effect = _get_setting
    db.get_alerts.return_value = ([], 0)
    return AlertEngine(db, gps_engine=None)


def _alert(**overrides):
    base = {
        'alert_type': 'new_drone',
        'serial_number': 'TEST-123',
        'detail': '',
        'drone_lat': 33.138546,
        'drone_lon': -80.105286,
        'drone_height_agl': 18.5,
    }
    base.update(overrides)
    return base


DRONE_URL = 'https://www.google.com/maps/search/?api=1&query=33.138546,-80.105286'
OP_URL = 'https://www.google.com/maps/search/?api=1&query=33.138737,-80.105254'


class TestMapsLink(unittest.TestCase):

    def test_slack_drone_map_link(self):
        msg = _make_engine()._format_alert_message(_alert(), slack=True)
        self.assertIn(f'Map: <{DRONE_URL}|Open in Google Maps>', msg)

    def test_map_link_under_coords_and_alt(self):
        """The drone map link sits below the Pos and Alt lines."""
        lines = _make_engine()._format_alert_message(_alert(), slack=True).split('\n')
        pos_i = next(i for i, l in enumerate(lines) if l.startswith('Pos:'))
        alt_i = next(i for i, l in enumerate(lines) if l.startswith('Alt:'))
        map_i = next(i for i, l in enumerate(lines) if l.startswith('Map:'))
        self.assertGreater(map_i, pos_i)
        self.assertGreater(map_i, alt_i)

    def test_controller_link_when_operator_coords_present(self):
        msg = _make_engine()._format_alert_message(
            _alert(operator_lat=33.138737, operator_lon=-80.105254), slack=True)
        self.assertIn('Controller Pos: 33.138737, -80.105254', msg)
        self.assertIn(f'Controller Map: <{OP_URL}|Open in Google Maps>', msg)

    def test_no_controller_link_without_operator_coords(self):
        msg = _make_engine()._format_alert_message(_alert(), slack=True)
        self.assertNotIn('Controller Map', msg)
        self.assertNotIn('Controller Pos', msg)

    def test_no_controller_link_when_operator_coords_zero(self):
        msg = _make_engine()._format_alert_message(
            _alert(operator_lat=0.0, operator_lon=0.0), slack=True)
        self.assertNotIn('Controller Map', msg)

    def test_plain_text_uses_bare_url(self):
        """Non-Slack consumers get a bare URL, not Slack mrkdwn <url|text>."""
        msg = _make_engine()._format_alert_message(
            _alert(operator_lat=33.138737, operator_lon=-80.105254), slack=False)
        self.assertIn(f'Map: {DRONE_URL}', msg)
        self.assertIn(f'Controller Map: {OP_URL}', msg)
        self.assertNotIn('|Open in Google Maps>', msg)

    def test_no_map_link_without_drone_coords(self):
        msg = _make_engine()._format_alert_message(
            _alert(drone_lat=0.0, drone_lon=0.0), slack=True)
        self.assertNotIn('\nMap:', '\n' + msg)


if __name__ == '__main__':
    unittest.main()
