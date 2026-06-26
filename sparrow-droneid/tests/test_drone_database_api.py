"""
Tests for GET /api/v1/drone-database (api_handler.py).

Uses the FakeRequest + patch pattern from test_flags_api.py.
Covers:
  - 503 when droneid_engine or db absent.
  - Disposition overlay defaults to 'unknown' for absent serial.
  - Vendor and flags present in response.
  - drone_maps_url / controller_maps_url null for 0,0 positions.
  - drone_maps_url present for valid coords.
  - controller_maps_url present for non-zero operator coords.
  - Orphan exclusion: a disposition for a serial absent from detections
    does NOT appear as a row in the response.
  - count field equals len(drones).
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

import backend.api_handler as api_handler


class FakeRequest:
    """Minimal stand-in for RequestHandler, matching test_flags_api.py pattern."""

    def __init__(self, json_data=None, query_params=None):
        self.json_data = json_data or {}
        self._query_params = query_params or {}
        self._response = None
        self._status = None

    def _send_ok(self, payload):
        self._status = 200
        self._response = payload

    def _send_error(self, status, code, msg):
        self._status = status
        self._response = {'error': {'code': code, 'message': msg}}

    def _qparam(self, name, default=None):
        return self._query_params.get(name, default)

    def _qparam_int(self, name, default=0):
        v = self._query_params.get(name)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default


def _make_db(rows=None):
    """Create a mock DB returning the given drone-database rows."""
    db = MagicMock()
    db.get_drone_database.return_value = rows if rows is not None else []
    db.get_current_dispositions.return_value = {}
    db.get_current_flags.return_value = {}
    return db


def _make_engine():
    engine = MagicMock()
    return engine


def _make_alert_engine(vendor=''):
    ae = MagicMock()
    ae.resolve_vendor.return_value = vendor
    return ae


def _sample_row(**overrides):
    """Build a minimal drone-database row dict."""
    base = {
        'serial_number': 'TEST-001',
        'registration_id': '',
        'id_type': 0,
        'ua_type': 2,
        'ua_type_name': 'Helicopter / Multirotor',
        'protocol': 'astm_ble',
        'mac_address': 'AA:BB:CC:DD:EE:FF',
        'operator_id': '',
        'self_id_text': '',
        'drone_lat': 33.138546,
        'drone_lon': -80.105286,
        'drone_alt_geo': 100.0,
        'drone_alt_baro': 0.0,
        'drone_height_agl': 30.0,
        'speed': 5.0,
        'direction': 90.0,
        'vertical_speed': 0.0,
        'rssi': -65,
        'takeoff_lat': 0.0,
        'takeoff_lon': 0.0,
        'operator_lat': None,
        'operator_lon': None,
        'operator_alt': None,
        'controller_last_seen': None,
        'first_seen': '2026-01-01T00:00:00Z',
        'last_seen': '2026-01-01T00:01:00Z',
        'detection_count': 10,
        'time_in_area_seconds': 60,
    }
    base.update(overrides)
    return base


class TestApiDroneDatabase(unittest.TestCase):

    # ------------------------------------------------------------------
    # Guard / 503

    def test_503_when_droneid_engine_absent(self):
        """503 when _droneid_engine is None."""
        db = _make_db()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', None), \
             patch.object(api_handler, '_db', db):
            api_handler.api_drone_database(req)
        self.assertEqual(req._status, 503)

    def test_503_when_db_absent(self):
        """503 when _db is None."""
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._status, 503)

    # ------------------------------------------------------------------
    # Empty result

    def test_empty_database_returns_200(self):
        """Empty database returns 200 with empty drones list."""
        db = _make_db(rows=[])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._status, 200)
        self.assertEqual(req._response['drones'], [])
        self.assertEqual(req._response['count'], 0)

    # ------------------------------------------------------------------
    # Disposition overlay

    def test_disposition_defaults_to_unknown(self):
        """Rows without a stored disposition get 'unknown'."""
        db = _make_db(rows=[_sample_row()])
        db.get_current_dispositions.return_value = {}
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._status, 200)
        self.assertEqual(req._response['drones'][0]['disposition'], 'unknown')

    def test_disposition_overlay_applied(self):
        """Stored disposition is returned for the matching serial."""
        db = _make_db(rows=[_sample_row()])
        db.get_current_dispositions.return_value = {'TEST-001': 'friendly'}
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._response['drones'][0]['disposition'], 'friendly')

    # ------------------------------------------------------------------
    # Vendor

    def test_vendor_present_when_alert_engine_available(self):
        """vendor field is set from alert_engine.resolve_vendor()."""
        db = _make_db(rows=[_sample_row()])
        engine = _make_engine()
        ae = _make_alert_engine(vendor='DJI')
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', ae):
            api_handler.api_drone_database(req)
        self.assertEqual(req._response['drones'][0]['vendor'], 'DJI')

    def test_vendor_empty_without_alert_engine(self):
        """vendor is '' when _alert_engine is None."""
        db = _make_db(rows=[_sample_row()])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._response['drones'][0]['vendor'], '')

    # ------------------------------------------------------------------
    # Flags

    def test_flags_default_false(self):
        """military and law_enforcement default to False when absent from flags_map."""
        db = _make_db(rows=[_sample_row()])
        db.get_current_flags.return_value = {}
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        d = req._response['drones'][0]
        self.assertFalse(d['military'])
        self.assertFalse(d['law_enforcement'])

    def test_military_flag_applied(self):
        """military flag is True when set in flags_map."""
        db = _make_db(rows=[_sample_row()])
        db.get_current_flags.return_value = {'TEST-001': {'military': True}}
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertTrue(req._response['drones'][0]['military'])

    # ------------------------------------------------------------------
    # Maps URLs

    def test_drone_maps_url_null_for_zero_position(self):
        """drone_maps_url is None when drone position is 0,0."""
        row = _sample_row(drone_lat=0.0, drone_lon=0.0)
        db = _make_db(rows=[row])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertIsNone(req._response['drones'][0]['drone_maps_url'])

    def test_drone_maps_url_present_for_valid_coords(self):
        """drone_maps_url is a valid Google Maps URL for non-zero position."""
        row = _sample_row(drone_lat=33.138546, drone_lon=-80.105286)
        db = _make_db(rows=[row])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        url = req._response['drones'][0]['drone_maps_url']
        self.assertIsNotNone(url)
        self.assertIn('33.138546', url)
        self.assertIn('-80.105286', url)
        self.assertTrue(url.startswith('https://www.google.com/maps/'))

    def test_controller_maps_url_null_when_no_operator(self):
        """controller_maps_url is None when operator_lat/lon are None."""
        row = _sample_row(operator_lat=None, operator_lon=None)
        db = _make_db(rows=[row])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertIsNone(req._response['drones'][0]['controller_maps_url'])

    def test_controller_maps_url_null_for_zero_operator(self):
        """controller_maps_url is None when operator position is 0,0."""
        row = _sample_row(operator_lat=0.0, operator_lon=0.0)
        db = _make_db(rows=[row])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertIsNone(req._response['drones'][0]['controller_maps_url'])

    def test_controller_maps_url_present_for_valid_coords(self):
        """controller_maps_url is a valid Google Maps URL when operator coords are set."""
        row = _sample_row(operator_lat=33.140000, operator_lon=-80.110000)
        db = _make_db(rows=[row])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        url = req._response['drones'][0]['controller_maps_url']
        self.assertIsNotNone(url)
        self.assertIn('33.140000', url)
        self.assertIn('-80.110000', url)

    # ------------------------------------------------------------------
    # Orphan exclusion

    def test_orphan_disposition_not_in_response(self):
        """A disposition for a serial absent from detections does not produce a row."""
        # DB returns only TEST-001; disposition_map also has GHOST-999 (orphan)
        db = _make_db(rows=[_sample_row(serial_number='TEST-001')])
        db.get_current_dispositions.return_value = {
            'TEST-001': 'friendly',
            'GHOST-999': 'threat',
        }
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        serials = [d['serial_number'] for d in req._response['drones']]
        self.assertNotIn('GHOST-999', serials)
        self.assertIn('TEST-001', serials)

    # ------------------------------------------------------------------
    # count field

    def test_count_matches_drones_length(self):
        """count field equals len(drones)."""
        rows = [
            _sample_row(serial_number='SN-001'),
            _sample_row(serial_number='SN-002'),
            _sample_row(serial_number='SN-003'),
        ]
        db = _make_db(rows=rows)
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._response['count'], 3)
        self.assertEqual(len(req._response['drones']), 3)

    # ------------------------------------------------------------------
    # drone_key

    def test_drone_key_set_to_serial_number(self):
        """drone_key is the serial_number."""
        db = _make_db(rows=[_sample_row(serial_number='MY-DRONE')])
        engine = _make_engine()
        req = FakeRequest()
        with patch.object(api_handler, '_droneid_engine', engine), \
             patch.object(api_handler, '_db', db), \
             patch.object(api_handler, '_alert_engine', None):
            api_handler.api_drone_database(req)
        self.assertEqual(req._response['drones'][0]['drone_key'], 'MY-DRONE')


class TestMapsPushpinUrl(unittest.TestCase):
    """Direct assertions on the module-level maps_pushpin_url helper."""

    def test_format(self):
        from backend.alert_engine import maps_pushpin_url
        url = maps_pushpin_url(33.138546, -80.105286)
        self.assertEqual(url, 'https://www.google.com/maps/search/?api=1&query=33.138546,-80.105286')

    def test_six_decimal_places(self):
        from backend.alert_engine import maps_pushpin_url
        url = maps_pushpin_url(1.0 / 3, 2.0 / 3)
        # Must have 6 decimal places
        import re
        m = re.search(r'query=([\d.-]+),([\d.-]+)', url)
        self.assertIsNotNone(m)
        self.assertEqual(len(m.group(1).split('.')[-1]), 6)


if __name__ == '__main__':
    unittest.main()
