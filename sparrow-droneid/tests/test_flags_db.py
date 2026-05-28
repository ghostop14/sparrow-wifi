"""
Tests for the flags DAO layer (database.py).

Covers: add_flag_event, get_current_flags, get_flag_history,
migrate_flags_key, and invalid input.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.database import Database


def _make_db():
    """In-memory SQLite database for tests."""
    return Database(db_path=':memory:')


class TestFlagEvents(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()

    # -- Validation -----------------------------------------------------------

    def test_invalid_flag_name_raises(self):
        with self.assertRaises(ValueError):
            self.db.add_flag_event('DRONE-1', 'badvalue', True)

    def test_invalid_flag_name_hostile_raises(self):
        with self.assertRaises(ValueError):
            self.db.add_flag_event('DRONE-1', 'disposition', True)

    def test_valid_flag_names_accepted(self):
        # Neither should raise
        self.db.add_flag_event('DRONE-1', 'military', True)
        self.db.add_flag_event('DRONE-1', 'law_enforcement', True)

    # -- get_current_flags ----------------------------------------------------

    def test_get_current_flags_returns_nested_dict(self):
        self.db.add_flag_event('DRONE-1', 'military', True)
        result = self.db.get_current_flags()
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get('DRONE-1'), dict)
        self.assertTrue(result['DRONE-1']['military'])

    def test_get_current_flags_excludes_false_latest_rows(self):
        # Set True then set False — latest row per (key, flag) is False → excluded
        self.db.add_flag_event('DRONE-A', 'military', True)
        self.db.add_flag_event('DRONE-A', 'military', False)
        result = self.db.get_current_flags()
        # DRONE-A should not appear (latest value=0)
        self.assertNotIn('DRONE-A', result)

    def test_get_current_flags_latest_row_wins_per_key_and_flag(self):
        # Flip military three times: True, False, True → should appear
        self.db.add_flag_event('DRONE-B', 'military', True)
        self.db.add_flag_event('DRONE-B', 'military', False)
        self.db.add_flag_event('DRONE-B', 'military', True)
        result = self.db.get_current_flags()
        self.assertIn('DRONE-B', result)
        self.assertTrue(result['DRONE-B']['military'])

    def test_get_current_flags_two_flags_independent(self):
        self.db.add_flag_event('DRONE-C', 'military', True)
        self.db.add_flag_event('DRONE-C', 'law_enforcement', True)
        result = self.db.get_current_flags()
        self.assertTrue(result['DRONE-C']['military'])
        self.assertTrue(result['DRONE-C']['law_enforcement'])

    def test_get_current_flags_one_true_one_false(self):
        self.db.add_flag_event('DRONE-D', 'military', True)
        self.db.add_flag_event('DRONE-D', 'law_enforcement', False)
        result = self.db.get_current_flags()
        self.assertIn('DRONE-D', result)
        self.assertTrue(result['DRONE-D']['military'])
        self.assertNotIn('law_enforcement', result['DRONE-D'])

    def test_get_current_flags_absent_means_false(self):
        result = self.db.get_current_flags()
        # No entries at all
        self.assertEqual(result, {})

    # -- get_flag_history -----------------------------------------------------

    def test_get_flag_history_ordering_newest_first(self):
        self.db.add_flag_event('DRONE-E', 'military', True, changed_by='op1')
        self.db.add_flag_event('DRONE-E', 'military', False, changed_by='op2')
        rows = self.db.get_flag_history('DRONE-E')
        self.assertEqual(len(rows), 2)
        # Newest first
        self.assertEqual(rows[0]['changed_by'], 'op2')
        self.assertEqual(rows[1]['changed_by'], 'op1')

    def test_get_flag_history_filter_by_drone_key(self):
        self.db.add_flag_event('DRONE-F', 'military', True)
        self.db.add_flag_event('DRONE-G', 'law_enforcement', True)
        rows = self.db.get_flag_history('DRONE-F')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['drone_key'], 'DRONE-F')

    def test_get_flag_history_unfiltered(self):
        self.db.add_flag_event('D1', 'military', True)
        self.db.add_flag_event('D2', 'law_enforcement', True)
        rows = self.db.get_flag_history()
        keys = {r['drone_key'] for r in rows}
        self.assertIn('D1', keys)
        self.assertIn('D2', keys)

    def test_get_flag_history_limit(self):
        for _ in range(10):
            self.db.add_flag_event('DRONE-H', 'military', True)
        rows = self.db.get_flag_history('DRONE-H', limit=3)
        self.assertEqual(len(rows), 3)

    def test_get_flag_history_value_is_int(self):
        self.db.add_flag_event('DRONE-I', 'military', True)
        rows = self.db.get_flag_history('DRONE-I')
        self.assertIsInstance(rows[0]['value'], int)
        self.assertEqual(rows[0]['value'], 1)

    # -- migrate_flags_key ----------------------------------------------------

    def test_migrate_flags_key_moves_all_true_flags(self):
        self.db.add_flag_event('MAC-AA:BB', 'military', True)
        self.db.add_flag_event('MAC-AA:BB', 'law_enforcement', True)
        migrated = self.db.migrate_flags_key('MAC-AA:BB', 'SERIAL-123')
        self.assertTrue(migrated)
        result = self.db.get_current_flags()
        # New key has both flags
        self.assertIn('SERIAL-123', result)
        self.assertTrue(result['SERIAL-123']['military'])
        self.assertTrue(result['SERIAL-123']['law_enforcement'])

    def test_migrate_flags_key_old_rows_preserved(self):
        self.db.add_flag_event('MAC-OLD', 'military', True)
        self.db.migrate_flags_key('MAC-OLD', 'SERIAL-NEW')
        # Old rows still present
        old_history = self.db.get_flag_history('MAC-OLD')
        self.assertEqual(len(old_history), 1)

    def test_migrate_flags_key_no_op_when_none(self):
        migrated = self.db.migrate_flags_key('NO-FLAGS', 'NEW-KEY')
        self.assertFalse(migrated)
        history = self.db.get_flag_history('NEW-KEY')
        self.assertEqual(len(history), 0)

    def test_migrate_flags_key_skips_false_flags(self):
        # Flag is currently False — should not migrate
        self.db.add_flag_event('OLD-KEY', 'military', True)
        self.db.add_flag_event('OLD-KEY', 'military', False)
        migrated = self.db.migrate_flags_key('OLD-KEY', 'NEW-KEY')
        self.assertFalse(migrated)

    def test_migrate_flags_key_notes_contain_old_key(self):
        self.db.add_flag_event('SRC-KEY', 'military', True)
        self.db.migrate_flags_key('SRC-KEY', 'DST-KEY')
        rows = self.db.get_flag_history('DST-KEY')
        self.assertEqual(len(rows), 1)
        self.assertIn('SRC-KEY', rows[0]['notes'])


if __name__ == '__main__':
    unittest.main()
