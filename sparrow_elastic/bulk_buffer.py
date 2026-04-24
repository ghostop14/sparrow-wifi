"""Thread-safe bulk buffer with swap-and-flush semantics for sparrow_elastic.

Used by the sparrow-elastic bridge to accumulate Elasticsearch bulk actions
from the scan loop and flush them in batches, decoupled from HTTP latency.

Public API
----------
BulkBuffer(max_size)   -- construct a buffer with an overflow cap
  .append(action)      -- add an action; drops oldest on overflow (thread-safe)
  .swap()              -- atomically exchange buffer for empty list (thread-safe)
  .depth()             -- current number of buffered actions
  .docs_dropped()      -- cumulative count of dropped-on-overflow actions
"""

from __future__ import annotations

import logging
import threading
from typing import List

logger = logging.getLogger(__name__)

# Log a WARN once per this many drops to avoid log spam on sustained overflow.
_LOG_EVERY_N_DROPS = 100


class BulkBuffer:
    """Fixed-capacity bulk action buffer with thread-safe append and swap.

    When the buffer is full, the *oldest* entry is evicted (popleft-style) to
    make room for the new entry.  A cumulative ``docs_dropped`` counter tracks
    total evictions so operators can monitor overflow pressure.

    Args:
        max_size: Maximum number of actions to hold before evicting the oldest.
                  Default is 10,000.

    Thread safety:
        All public methods acquire ``_lock`` internally.  ``append`` and
        ``swap`` are designed for single-writer / single-flusher usage but are
        safe under concurrent writers as well — the lock is re-entrant only if
        the same thread calls append from within a lock-held section, which
        does not occur in normal usage.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._lock = threading.Lock()
        self._actions: List[dict] = []
        self._max_size = max_size
        self._docs_dropped: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, action: dict) -> None:
        """Append *action* to the buffer; evict the oldest if at capacity.

        When the buffer already holds ``max_size`` items, the item at index 0
        (oldest) is removed and ``docs_dropped`` is incremented.  A WARN is
        logged once every ``_LOG_EVERY_N_DROPS`` drops to signal sustained
        overflow pressure without flooding the log.

        Args:
            action: An Elasticsearch bulk action dict, e.g.
                ``{"_op_type": "index", "_index": "sparrow-wifi",
                   "_id": "...", "_source": {...}}``.
        """
        with self._lock:
            if len(self._actions) >= self._max_size:
                self._actions.pop(0)  # evict oldest
                self._docs_dropped += 1
                if self._docs_dropped % _LOG_EVERY_N_DROPS == 0:
                    logger.warning(
                        "BulkBuffer overflow: %d documents dropped so far "
                        "(max_size=%d). Consider reducing scan delay or "
                        "increasing flush frequency.",
                        self._docs_dropped,
                        self._max_size,
                    )
            self._actions.append(action)

    def swap(self) -> List[dict]:
        """Atomically replace the internal buffer with an empty list.

        Returns the old list so the caller can flush it to Elasticsearch
        without holding the lock.  The buffer is available for new appends
        immediately after this call returns.

        Returns:
            The list of accumulated actions (may be empty).
        """
        with self._lock:
            old = self._actions
            self._actions = []
            return old

    def depth(self) -> int:
        """Return the current number of buffered actions (thread-safe snapshot)."""
        with self._lock:
            return len(self._actions)

    def docs_dropped(self) -> int:
        """Return the cumulative number of documents evicted due to overflow."""
        with self._lock:
            return self._docs_dropped
