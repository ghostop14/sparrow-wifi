"""
Tests for the flags HTTP API layer (api_handler.py).

Covers: PUT /flags partial body; independence (one flag doesn't clear other);
unknown-key 400; non-bool 400; 503 when engine absent; GET /flags/history.

Tests call the handler functions directly, bypassing HTTP using a fake
request object and patching module-level globals.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

import backend.api_handler as api_handler


class FakeRequest:
    """Minimal stand-in for the RequestHandler passed to route functions."""

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


def _make_engine():
    engine = MagicMock()
    engine.set_flag.return_value = None
    engine.get_flags.return_value = {'military': False, 'law_enforcement': False}
    return engine


def _make_db():
    db = MagicMock()
    db.get_flag_history.return_value = []
    return db


class TestPutFlags(unittest.TestCase):

    def setUp(self):
        self.engine = _make_engine()
        self.db = _make_db()

    def _call_put(self, serial, body):
        req = FakeRequest(json_data=body)
        with patch.object(api_handler, '_droneid_engine', self.engine), \
             patch.object(api_handler, '_db', self.db):
            api_handler.api_drone_set_flags(req, serial)
        return req

    def test_put_military_only(self):
        self.engine.get_flags.return_value = {'military': True, 'law_enforcement': False}
        req = self._call_put('DRONE-1', {'military': True})
        self.assertEqual(req._status, 200)
        self.assertIn('military', req._response)
        self.assertTrue(req._response['military'])
        self.engine.set_flag.assert_called_once_with(
            'DRONE-1', 'military', True, changed_by='')

    def test_put_law_enforcement_only(self):
        self.engine.get_flags.return_value = {'military': False, 'law_enforcement': True}
        req = self._call_put('DRONE-1', {'law_enforcement': True})
        self.assertEqual(req._status, 200)
        self.assertTrue(req._response['law_enforcement'])
        self.engine.set_flag.assert_called_once_with(
            'DRONE-1', 'law_enforcement', True, changed_by='')

    def test_put_both_flags(self):
        self.engine.get_flags.return_value = {'military': True, 'law_enforcement': True}
        req = self._call_put('DRONE-1', {'military': True, 'law_enforcement': True})
        self.assertEqual(req._status, 200)
        self.assertTrue(req._response['military'])
        self.assertTrue(req._response['law_enforcement'])
        self.assertEqual(self.engine.set_flag.call_count, 2)

    def test_independence_setting_military_does_not_clear_le(self):
        """Setting only military should call set_flag once (military), not touch LE."""
        self.engine.get_flags.return_value = {'military': True, 'law_enforcement': True}
        req = self._call_put('DRONE-1', {'military': True})
        self.assertEqual(req._status, 200)
        # set_flag was called only once with 'military'
        calls = self.engine.set_flag.call_args_list
        flag_names_set = [c[0][1] for c in calls]
        self.assertIn('military', flag_names_set)
        self.assertNotIn('law_enforcement', flag_names_set)

    def test_unknown_key_returns_400(self):
        req = self._call_put('DRONE-1', {'military': True, 'disposition': 'friendly'})
        self.assertEqual(req._status, 400)

    def test_no_flags_present_returns_400(self):
        req = self._call_put('DRONE-1', {'changed_by': 'op'})
        self.assertEqual(req._status, 400)

    def test_non_bool_value_returns_400(self):
        req = self._call_put('DRONE-1', {'military': 'yes'})
        self.assertEqual(req._status, 400)

    def test_integer_value_returns_400(self):
        req = self._call_put('DRONE-1', {'military': 1})
        self.assertEqual(req._status, 400)

    def test_503_when_engine_absent(self):
        req = FakeRequest(json_data={'military': True})
        with patch.object(api_handler, '_droneid_engine', None), \
             patch.object(api_handler, '_db', self.db):
            api_handler.api_drone_set_flags(req, 'DRONE-1')
        self.assertEqual(req._status, 503)

    def test_changed_by_forwarded(self):
        req = self._call_put('DRONE-1', {'military': True, 'changed_by': 'operator-99'})
        self.assertEqual(req._status, 200)
        self.engine.set_flag.assert_called_once_with(
            'DRONE-1', 'military', True, changed_by='operator-99')

    def test_response_contains_drone_key(self):
        req = self._call_put('MY-DRONE', {'military': False})
        self.assertEqual(req._status, 200)
        self.assertEqual(req._response['drone_key'], 'MY-DRONE')


class TestGetFlagHistory(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()

    def _call_get_history(self, serial, limit=None):
        params = {}
        if limit is not None:
            params['limit'] = str(limit)
        req = FakeRequest(query_params=params)
        with patch.object(api_handler, '_db', self.db):
            api_handler.api_drone_flag_history(req, serial)
        return req

    def test_get_flag_history_returns_200(self):
        self.db.get_flag_history.return_value = [
            {'id': 1, 'drone_key': 'DRONE-1', 'flag_name': 'military',
             'value': 1, 'changed_at': '2026-01-01T00:00:00Z', 'changed_by': 'op', 'notes': ''},
        ]
        req = self._call_get_history('DRONE-1')
        self.assertEqual(req._status, 200)
        self.assertEqual(req._response['drone_key'], 'DRONE-1')
        self.assertEqual(len(req._response['history']), 1)

    def test_get_flag_history_empty(self):
        req = self._call_get_history('UNKNOWN')
        self.assertEqual(req._status, 200)
        self.assertEqual(req._response['history'], [])

    def test_get_all_flags(self):
        self.db.get_flag_history.return_value = []
        req = FakeRequest()
        with patch.object(api_handler, '_db', self.db):
            api_handler.api_flags_all(req)
        self.assertEqual(req._status, 200)
        self.assertIn('flags', req._response)
        self.db.get_flag_history.assert_called_once_with(limit=500)


if __name__ == '__main__':
    unittest.main()
