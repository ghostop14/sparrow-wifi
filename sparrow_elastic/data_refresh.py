"""sparrow_elastic.data_refresh — bundled reference-data refresh module.

Manages five data files that are bundled as seeds and optionally refreshed from
upstream sources on a configurable schedule.

Usage
-----
Programmatic::

    from sparrow_elastic.data_refresh import refresh_all, start_background_refresh
    results = refresh_all(force=False)   # {name: 'ok'|'not_stale'|'failed'}
    start_background_refresh(interval_hours=24)

CLI::

    python -m sparrow_elastic.data_refresh [--force]

Data files are stored alongside the package's bundled seed copies.  If a
refresh fails the existing (seed or previously-refreshed) copy is left intact.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package data directory — same directory as this module's bundled data/
# ---------------------------------------------------------------------------
_DATA_DIR: Path = Path(__file__).parent / "data"

# ---------------------------------------------------------------------------
# YAML availability guard (pyyaml is optional at module load; required only
# for YAML source files)
# ---------------------------------------------------------------------------
try:
    import yaml as _yaml  # type: ignore[import]
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# DataFile
# ---------------------------------------------------------------------------

@dataclass
class DataFile:
    """Descriptor for a single managed data file.

    Attributes:
        name:          Logical name / filename (no path), e.g. ``"manuf"``.
        url:           Upstream URL to fetch from.
        max_age_days:  Number of days before the file is considered stale.
        format:        ``"json"``, ``"yaml"`` (converted to JSON on write), or
                       ``"raw"`` (stored verbatim).
    """

    name: str
    url: str
    max_age_days: int
    format: str = "json"

    @property
    def path(self) -> Path:
        """Absolute path to the managed data file."""
        return _DATA_DIR / self.name

    # ------------------------------------------------------------------
    # Staleness check
    # ------------------------------------------------------------------

    def is_stale(self) -> bool:
        """Return True if the file is absent or older than ``max_age_days``."""
        p = self.path
        if not p.is_file():
            return True
        age_seconds = time.time() - p.stat().st_mtime
        return age_seconds > self.max_age_days * 86400

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def refresh(self, timeout: float = 30.0, *, force: bool = False) -> bool:
        """Fetch the upstream URL and atomically replace the local file.

        Args:
            timeout: HTTP request timeout in seconds.
            force:   When True, skip the staleness check and always fetch.

        Returns:
            True if the file was successfully updated, False on any error.
        """
        if not force and not self.is_stale():
            logger.debug("data_refresh: '%s' is not stale — skipping", self.name)
            return True  # Treated as success (nothing to do)

        logger.info("data_refresh: refreshing '%s' from %s", self.name, self.url)
        try:
            with urllib.request.urlopen(self.url, timeout=timeout) as resp:
                raw_bytes: bytes = resp.read()
        except (urllib.error.URLError, OSError, Exception) as exc:
            logger.warning(
                "data_refresh: failed to fetch '%s' from %s: %s",
                self.name, self.url, exc,
            )
            return False

        # Convert content if needed.
        try:
            content_bytes = self._convert(raw_bytes)
        except Exception as exc:
            logger.warning(
                "data_refresh: failed to convert '%s' content: %s",
                self.name, exc,
            )
            return False

        # Atomic write: write to a temp file in the same directory, then rename.
        dest = self.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=dest.parent, prefix=f".{self.name}.tmp-"
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(content_bytes)
                os.replace(tmp_path, dest)
            except Exception:
                # Clean up the temp file if rename failed.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.warning(
                "data_refresh: failed to write '%s': %s",
                self.name, exc,
            )
            return False

        logger.info("data_refresh: '%s' updated successfully", self.name)
        return True

    # ------------------------------------------------------------------
    # Internal: content conversion
    # ------------------------------------------------------------------

    def _convert(self, raw_bytes: bytes) -> bytes:
        """Convert *raw_bytes* according to self.format.

        - ``"raw"``: returned unchanged.
        - ``"json"``: parsed + re-serialised for normalisation.
        - ``"yaml"``: parsed from YAML, serialised as JSON.
        """
        if self.format == "raw":
            return raw_bytes

        if self.format == "json":
            # Validate and normalise
            data = json.loads(raw_bytes.decode("utf-8"))
            return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        if self.format == "yaml":
            if not _YAML_AVAILABLE:
                raise ImportError(
                    "pyyaml is required to refresh YAML sources. "
                    "Install with: pip install pyyaml"
                )
            data = _yaml.safe_load(raw_bytes.decode("utf-8"))
            return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

        raise ValueError(f"Unknown format: {self.format!r}")


# ---------------------------------------------------------------------------
# File registry
# ---------------------------------------------------------------------------

def all_data_files() -> list[DataFile]:
    """Return the list of all managed DataFile descriptors."""
    return [
        DataFile(
            name="manuf",
            url="https://www.wireshark.org/download/automated/data/manuf",
            max_age_days=90,
            format="raw",
        ),
        DataFile(
            name="bt_sig_company_ids.json",
            url=(
                "https://bitbucket.org/bluetooth-SIG/public/raw/main/"
                "assigned_numbers/company_identifiers/company_identifiers.yaml"
            ),
            max_age_days=30,
            format="yaml",
        ),
        DataFile(
            name="bt_sig_service_uuids.json",
            url=(
                "https://bitbucket.org/bluetooth-SIG/public/raw/main/"
                "assigned_numbers/uuids/service_uuids.yaml"
            ),
            max_age_days=30,
            format="yaml",
        ),
        DataFile(
            name="bt_sig_appearance_values.json",
            url=(
                "https://bitbucket.org/bluetooth-SIG/public/raw/main/"
                "assigned_numbers/core/appearance_values.yaml"
            ),
            max_age_days=30,
            format="yaml",
        ),
        # Apple Continuity: the furiousMAC upstream serves a Wireshark C
        # dissector, not clean JSON, so we treat this file as a static seed.
        # The refresh is defined here for API completeness but the URL will
        # likely return non-JSON content; the refresh() call will fail and
        # leave the bundled seed intact, which is the desired behaviour.
        DataFile(
            name="apple_continuity_subtypes.json",
            url=(
                "https://raw.githubusercontent.com/furiousMAC/continuity/"
                "master/dissectors/packet-bthci_cmd-apple.json"
            ),
            max_age_days=90,
            format="json",
        ),
    ]


# ---------------------------------------------------------------------------
# refresh_all
# ---------------------------------------------------------------------------

def refresh_all(force: bool = False) -> dict[str, str]:
    """Refresh all managed data files.

    Args:
        force: When True, bypass staleness checks and re-fetch every file.

    Returns:
        A dict mapping each file's ``name`` to one of:
        ``"ok"``        — file was fetched and written successfully.
        ``"not_stale"`` — file is still fresh; no fetch was performed.
        ``"failed"``    — fetch or write failed; existing copy preserved.
    """
    results: dict[str, str] = {}
    for df in all_data_files():
        if not force and not df.is_stale():
            results[df.name] = "not_stale"
            continue
        success = df.refresh(force=True)  # We've already done the staleness gate above
        results[df.name] = "ok" if success else "failed"
    return results


# ---------------------------------------------------------------------------
# Background refresh thread
# ---------------------------------------------------------------------------

_bg_thread: Optional[threading.Thread] = None
_bg_stop_event: threading.Event = threading.Event()


def start_background_refresh(interval_hours: float = 24.0) -> None:
    """Spawn a daemon thread that calls refresh_all() on the given interval.

    The thread is a daemon (won't block process exit).  Calling this function
    more than once has no effect if the thread is already running.

    Args:
        interval_hours: How often to run refresh_all().
    """
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        logger.debug("data_refresh: background thread already running")
        return

    interval_seconds = interval_hours * 3600.0
    _bg_stop_event.clear()

    def _loop() -> None:
        logger.info(
            "data_refresh: background thread started (interval=%.1fh)", interval_hours
        )
        while not _bg_stop_event.wait(timeout=interval_seconds):
            try:
                results = refresh_all(force=False)
                logger.info("data_refresh: background refresh complete: %s", results)
            except Exception as exc:
                logger.warning("data_refresh: background refresh error: %s", exc)
        logger.info("data_refresh: background thread exiting")

    _bg_thread = threading.Thread(target=_loop, name="sparrow-data-refresh", daemon=True)
    _bg_thread.start()


def stop_background_refresh() -> None:
    """Signal the background thread to stop.  Returns immediately (best-effort)."""
    _bg_stop_event.set()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Refresh Sparrow-Elastic bundled reference data from upstream sources."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force refresh even if files are not stale.",
    )
    args = parser.parse_args()

    results = refresh_all(force=args.force)

    # Print a status table.
    col_w = max(len(n) for n in results) + 2
    print(f"\n{'File':<{col_w}}  Status")
    print("-" * (col_w + 10))
    for name, status in results.items():
        icon = "OK   " if status == "ok" else ("SKIP " if status == "not_stale" else "FAIL ")
        print(f"{name:<{col_w}}  {icon} ({status})")
    print()

    failed = [n for n, s in results.items() if s == "failed"]
    sys.exit(1 if failed else 0)
