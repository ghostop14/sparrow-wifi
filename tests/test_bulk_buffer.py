"""Tests for sparrow_elastic.bulk_buffer.BulkBuffer.

Covers:
- Basic append + depth + swap
- Overflow eviction (oldest dropped, docs_dropped incremented)
- Concurrent appends from multiple threads
- Swap while another thread is appending (no interleaving corruption)
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sparrow_elastic.bulk_buffer import BulkBuffer


def _action(i: int) -> dict:
    """Build a minimal bulk action dict tagged with index *i*."""
    return {"_op_type": "index", "_index": "test", "_id": str(i), "_source": {"n": i}}


class TestBulkBufferBasic(unittest.TestCase):
    """Normal (non-overflow) append / depth / swap behaviour."""

    def test_append_below_max_size_depth_reflects(self):
        buf = BulkBuffer(max_size=10)
        for i in range(5):
            buf.append(_action(i))
        self.assertEqual(buf.depth(), 5)
        self.assertEqual(buf.docs_dropped(), 0)

    def test_swap_returns_correct_list_and_clears_buffer(self):
        buf = BulkBuffer(max_size=10)
        actions = [_action(i) for i in range(3)]
        for a in actions:
            buf.append(a)

        result = buf.swap()

        self.assertEqual(len(result), 3)
        self.assertEqual(result, actions)
        # Buffer should be empty after swap.
        self.assertEqual(buf.depth(), 0)

    def test_swap_empty_buffer_returns_empty_list(self):
        buf = BulkBuffer(max_size=5)
        result = buf.swap()
        self.assertEqual(result, [])
        self.assertEqual(buf.depth(), 0)

    def test_append_after_swap_starts_fresh(self):
        buf = BulkBuffer(max_size=5)
        buf.append(_action(0))
        buf.swap()
        buf.append(_action(1))

        result = buf.swap()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["_source"]["n"], 1)

    def test_docs_dropped_starts_at_zero(self):
        buf = BulkBuffer(max_size=100)
        self.assertEqual(buf.docs_dropped(), 0)


class TestBulkBufferOverflow(unittest.TestCase):
    """Overflow eviction: oldest entry is dropped, counter increments."""

    def test_overflow_drops_oldest_entry(self):
        buf = BulkBuffer(max_size=3)
        # Fill to capacity.
        buf.append(_action(0))
        buf.append(_action(1))
        buf.append(_action(2))
        self.assertEqual(buf.depth(), 3)
        self.assertEqual(buf.docs_dropped(), 0)

        # One more pushes out action(0).
        buf.append(_action(3))
        self.assertEqual(buf.depth(), 3)
        self.assertEqual(buf.docs_dropped(), 1)

        result = buf.swap()
        ids = [a["_source"]["n"] for a in result]
        self.assertEqual(ids, [1, 2, 3])

    def test_docs_dropped_increments_on_each_overflow(self):
        buf = BulkBuffer(max_size=2)
        for i in range(7):
            buf.append(_action(i))
        # Each append past capacity drops one entry: 7 total, max 2 → 5 drops.
        self.assertEqual(buf.docs_dropped(), 5)

    def test_overflow_buffer_depth_never_exceeds_max_size(self):
        buf = BulkBuffer(max_size=10)
        for i in range(100):
            buf.append(_action(i))
        self.assertEqual(buf.depth(), 10)

    def test_overflow_keeps_newest_entries(self):
        """After N overflows the buffer contains the N most-recently appended."""
        max_size = 5
        total = 20
        buf = BulkBuffer(max_size=max_size)
        for i in range(total):
            buf.append(_action(i))

        result = buf.swap()
        ids = [a["_source"]["n"] for a in result]
        self.assertEqual(ids, list(range(total - max_size, total)))

    def test_docs_dropped_warn_logged_every_100(self):
        """No assertion on log output, but verify the counter increments cleanly."""
        buf = BulkBuffer(max_size=1)
        for i in range(200):
            buf.append(_action(i))
        self.assertEqual(buf.docs_dropped(), 199)


class TestBulkBufferConcurrent(unittest.TestCase):
    """Thread safety: concurrent appends and concurrent append-while-swap."""

    def test_concurrent_appends_no_lost_or_double_entries(self):
        """N threads each append M items; final count must equal min(N*M, max_size)."""
        n_threads = 8
        items_per_thread = 50
        max_size = n_threads * items_per_thread  # large enough to hold everything
        buf = BulkBuffer(max_size=max_size)

        def worker(start_id: int) -> None:
            for i in range(items_per_thread):
                buf.append(_action(start_id + i))

        threads = [
            threading.Thread(target=worker, args=(t * items_per_thread,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        result = buf.swap()
        # Each action has a unique _id.  No duplicates, no corruption.
        ids = [a["_id"] for a in result]
        self.assertEqual(len(ids), len(set(ids)),
                         "Duplicate entries detected — thread safety violation")
        self.assertEqual(len(result), n_threads * items_per_thread)

    def test_concurrent_appends_with_overflow_no_corruption(self):
        """Same as above but with a small max_size that forces overflow."""
        n_threads = 4
        items_per_thread = 200
        max_size = 100
        buf = BulkBuffer(max_size=max_size)

        def worker(start_id: int) -> None:
            for i in range(items_per_thread):
                buf.append(_action(start_id * 1000 + i))

        threads = [
            threading.Thread(target=worker, args=(t,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        result = buf.swap()
        # Buffer should be capped at max_size.
        self.assertEqual(len(result), max_size)
        # No duplicate _id values.
        ids = [a["_id"] for a in result]
        self.assertEqual(len(ids), len(set(ids)),
                         "Duplicate entries detected — thread safety violation")
        # All entries must be valid dicts.
        for a in result:
            self.assertIn("_source", a)
            self.assertIn("n", a["_source"])

    def test_swap_while_appending_no_interleaving_corruption(self):
        """A flusher thread calling swap() while appender threads are writing
        must never observe a partial action or corrupt list state."""
        max_size = 500
        buf = BulkBuffer(max_size=max_size)
        n_appenders = 4
        items_per_appender = 500
        swap_results: list[list[dict]] = []
        stop_event = threading.Event()

        def appender(start_id: int) -> None:
            for i in range(items_per_appender):
                buf.append(_action(start_id * 10000 + i))

        def flusher() -> None:
            while not stop_event.is_set():
                batch = buf.swap()
                if batch:
                    swap_results.append(batch)
                time.sleep(0.001)
            # One final swap to drain anything left.
            final = buf.swap()
            if final:
                swap_results.append(final)

        flush_thread = threading.Thread(target=flusher)
        flush_thread.start()

        append_threads = [
            threading.Thread(target=appender, args=(t,))
            for t in range(n_appenders)
        ]
        for t in append_threads:
            t.start()
        for t in append_threads:
            t.join()

        stop_event.set()
        flush_thread.join()

        # Every captured action must be a valid dict with the expected keys.
        all_seen: list[dict] = []
        for batch in swap_results:
            # Each item in a batch must be a well-formed action dict.
            for a in batch:
                self.assertIsInstance(a, dict, "Action is not a dict — corruption")
                self.assertIn("_source", a, "Missing _source — corruption")
                all_seen.append(a)

        # No duplicates across all flushed batches.
        all_ids = [a["_id"] for a in all_seen]
        self.assertEqual(len(all_ids), len(set(all_ids)),
                         "Duplicate actions across swap batches — interleaving bug")


if __name__ == "__main__":
    unittest.main()
