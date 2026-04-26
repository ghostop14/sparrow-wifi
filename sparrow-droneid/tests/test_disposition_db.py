"""
Tests for the disposition DAO layer (database.py).

Covers: add_disposition_event, get_current_dispositions,
get_disposition_history, migrate_disposition_key, invalid input.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.database import Database


def _make_db():
    """In-memory SQLite database for tests."""
    return Database(db_path=':memory:')


class TestDispositionEvents(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()

    def test_add_and_retrieve_event(self):
        self.db.add_disposition_event('DRONE-1', 'friendly', changed_by='op1')
        rows = self.db.get_disposition_history('DRONE-1')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['disposition'], 'friendly')
        self.assertEqual(rows[0]['changed_by'], 'op1')
        self.assertEqual(rows[0]['drone_key'], 'DRONE-1')

    def test_invalid_disposition_raises(self):
        with self.assertRaises(ValueError):
            self.db.add_disposition_event('DRONE-1', 'badvalue')

    def test_get_current_dispositions_latest_wins(self):
        self.db.add_disposition_event('DRONE-1', 'friendly')
        self.db.add_disposition_event('DRONE-1', 'threat')
        current = self.db.get_current_dispositions()
        self.assertEqual(current.get('DRONE-1'), 'threat')

    def test_get_current_dispositions_excludes_unknown(self):
        self.db.add_disposition_event('DRONE-A', 'friendly')
        self.db.add_disposition_event('DRONE-B', 'unknown')
        current = self.db.get_current_dispositions()
        self.assertIn('DRONE-A', current)
        self.assertNotIn('DRONE-B', current)

    def test_unknown_disposition_clears_cache(self):
        # friendly then unknown — latest is unknown so should NOT appear
        self.db.add_disposition_event('DRONE-X', 'friendly')
        self.db.add_disposition_event('DRONE-X', 'unknown')
        current = self.db.get_current_dispositions()
        self.assertNotIn('DRONE-X', current)

    def test_migrate_disposition_key_appends_new_row(self):
        self.db.add_disposition_event('MAC-AA:BB', 'threat')
        migrated = self.db.migrate_disposition_key('MAC-AA:BB', 'SERIAL-123')
        self.assertTrue(migrated)
        # Old rows still exist
        old_history = self.db.get_disposition_history('MAC-AA:BB')
        self.assertEqual(len(old_history), 1)
        # New key has a row
        new_history = self.db.get_disposition_history('SERIAL-123')
        self.assertEqual(len(new_history), 1)
        self.assertEqual(new_history[0]['disposition'], 'threat')
        self.assertIn('MAC-AA:BB', new_history[0]['notes'])

    def test_migrate_disposition_key_unknown_source_returns_false(self):
        # No prior disposition for old key
        migrated = self.db.migrate_disposition_key('NO-DISP', 'NEW-KEY')
        self.assertFalse(migrated)
        # No new event should be written
        history = self.db.get_disposition_history('NEW-KEY')
        self.assertEqual(len(history), 0)

    def test_get_disposition_history_all_keys(self):
        self.db.add_disposition_event('D1', 'friendly')
        self.db.add_disposition_event('D2', 'threat')
        rows = self.db.get_disposition_history()
        keys = {r['drone_key'] for r in rows}
        self.assertIn('D1', keys)
        self.assertIn('D2', keys)

    def test_get_disposition_history_limit(self):
        for i in range(10):
            self.db.add_disposition_event('D3', 'friendly')
        rows = self.db.get_disposition_history('D3', limit=3)
        self.assertEqual(len(rows), 3)

    def test_append_only_old_rows_preserved(self):
        self.db.add_disposition_event('D4', 'friendly', changed_by='a')
        self.db.add_disposition_event('D4', 'threat', changed_by='b')
        rows = self.db.get_disposition_history('D4')
        self.assertEqual(len(rows), 2)
        dispositions = [r['disposition'] for r in rows]
        self.assertIn('friendly', dispositions)
        self.assertIn('threat', dispositions)


if __name__ == '__main__':
    unittest.main()
