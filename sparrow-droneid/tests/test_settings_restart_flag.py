"""PUT /api/v1/settings reports restart_required only for a real change to a restart-only key."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

import backend.api_handler as api  # noqa: E402
from backend.database import Database  # noqa: E402


class FakeRequest:
    def __init__(self, json_data=None):
        self.json_data = json_data
        self.status = None
        self.body = None

    def _send_ok(self, payload, status=200):
        self.status = status
        self.body = payload

    def _send_error(self, status, code, msg, detail=None):
        self.status = status
        self.body = {'error': {'code': code, 'message': msg}}


class TestRestartFlag(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, 'd.db'))
        p = patch.object(api, '_db', self.db)
        p.start()
        self.addCleanup(p.stop)

    def put(self, body):
        req = FakeRequest(body)
        api.api_settings_put(req)
        self.assertEqual(req.status, 200, req.body)
        return req.body['restart_required']

    def current(self):
        s = self.db.get_all_settings()
        return {'port': int(s['port']), 'bind_address': s['bind_address'],
                'https_enabled': s['https_enabled'] == 'true',
                'https_cert_name': s['https_cert_name']}

    def test_resending_stored_values_needs_no_restart(self):
        body = dict(self.current(), retention_days=30, display_units='imperial')
        self.assertFalse(self.put(body))

    def test_live_only_change_needs_no_restart(self):
        self.assertFalse(self.put({'retention_days': 7, 'tile_cache_enabled': False}))

    def test_each_restart_key_change_needs_restart(self):
        changes = {'port': 8123, 'bind_address': '127.0.0.1', 'https_enabled': True,
                   'https_cert_name': 'lab'}
        for key, value in changes.items():
            with self.subTest(key=key):
                self.assertTrue(self.put(dict(self.current(), **{key: value})))
                # Saving again with the new value stored is not a further change.
                self.assertFalse(self.put(self.current()))


if __name__ == '__main__':
    unittest.main()
