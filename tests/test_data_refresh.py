"""Tests for sparrow_elastic.data_refresh — staleness checks and refresh logic.

All tests mock urllib.request.urlopen; no live network calls are made.
"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sparrow_elastic.data_refresh import DataFile, all_data_files, refresh_all


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_file(tmp_path: Path, name: str = "test_file.json",
                    url: str = "https://example.com/data.json",
                    max_age_days: int = 30,
                    fmt: str = "json") -> DataFile:
    """Build a DataFile whose path lives under tmp_path."""
    df = DataFile(name=name, url=url, max_age_days=max_age_days, format=fmt)
    # Monkey-patch the path property to point at tmp_path.
    object.__setattr__(df, "_test_data_dir", tmp_path)
    # We cannot easily override the property without subclassing, so we
    # patch at the module level via the _DATA_DIR instead.  Use a fresh
    # DataFile and mock the module-level constant in each test that needs it.
    return df


# ---------------------------------------------------------------------------
# is_stale — file absent
# ---------------------------------------------------------------------------

class TestIsStaleFileAbsent:
    def test_missing_file_is_stale(self, tmp_path: Path):
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="missing.json", url="https://example.com/x.json",
                          max_age_days=30)
            assert df.is_stale() is True


# ---------------------------------------------------------------------------
# is_stale — file present, within max_age
# ---------------------------------------------------------------------------

class TestIsStaleFileRecent:
    def test_file_modified_1_day_ago_within_30d_is_not_stale(self, tmp_path: Path):
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="recent.json", url="https://example.com/x.json",
                          max_age_days=30)
            # Create the file
            df.path.write_text('{"ok": true}', encoding="utf-8")
            # Set mtime to 1 day ago
            one_day_ago = time.time() - 86400
            os.utime(df.path, (one_day_ago, one_day_ago))
            assert df.is_stale() is False


# ---------------------------------------------------------------------------
# is_stale — file present, older than max_age
# ---------------------------------------------------------------------------

class TestIsStaleFileOld:
    def test_file_91_days_old_with_90_day_max_is_stale(self, tmp_path: Path):
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="old.json", url="https://example.com/x.json",
                          max_age_days=90)
            df.path.write_text('{"ok": true}', encoding="utf-8")
            # Set mtime to 91 days ago
            old_time = time.time() - 91 * 86400
            os.utime(df.path, (old_time, old_time))
            assert df.is_stale() is True


# ---------------------------------------------------------------------------
# refresh — success path (atomic write, no .tmp leftover)
# ---------------------------------------------------------------------------

class TestRefreshSuccess:
    def test_refresh_writes_file_atomically(self, tmp_path: Path):
        payload = json.dumps({"result": "data"}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="out.json", url="https://example.com/data.json",
                          max_age_days=30, format="json")
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = df.refresh(force=True)
            # Assertions inside the patch context so df.path resolves to tmp_path.
            assert result is True
            assert df.path.is_file()
            written = json.loads(df.path.read_text(encoding="utf-8"))
            assert written == {"result": "data"}

    def test_no_tmp_file_left_after_successful_refresh(self, tmp_path: Path):
        payload = json.dumps({"clean": True}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="clean.json", url="https://example.com/d.json",
                          max_age_days=30)
            with patch("urllib.request.urlopen", return_value=mock_resp):
                df.refresh(force=True)

        # Verify no .tmp files remain.
        tmp_files = list(tmp_path.glob("*.tmp-*"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"

    def test_refresh_raw_format_writes_verbatim(self, tmp_path: Path):
        payload = b"00:11:22\tVendor\tLong Vendor Name\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="manuf_test", url="https://example.com/manuf",
                          max_age_days=90, format="raw")
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = df.refresh(force=True)
            # Assertions inside the patch context so df.path resolves to tmp_path.
            assert result is True
            assert df.path.read_bytes() == payload


# ---------------------------------------------------------------------------
# refresh — network error: returns False, existing file untouched
# ---------------------------------------------------------------------------

class TestRefreshNetworkError:
    def test_network_error_returns_false(self, tmp_path: Path):
        import urllib.error
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="net_err.json", url="https://example.com/d.json",
                          max_age_days=30)
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("connection refused")):
                result = df.refresh(force=True)

        assert result is False

    def test_existing_file_not_modified_on_network_error(self, tmp_path: Path):
        import urllib.error
        original_content = b'{"existing": true}'
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            df = DataFile(name="preserve.json", url="https://example.com/d.json",
                          max_age_days=30)
            df.path.write_bytes(original_content)
            mtime_before = df.path.stat().st_mtime

            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("timeout")):
                df.refresh(force=True)

            assert df.path.read_bytes() == original_content
            assert df.path.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# refresh_all — returns correct status dict
# ---------------------------------------------------------------------------

class TestRefreshAll:
    def test_returns_dict_keyed_by_name(self, tmp_path: Path):
        """refresh_all() returns a dict; every key is one of the managed names."""
        # Make all files "stale" by ensuring they don't exist in tmp_path,
        # and mock urlopen to simulate a network failure (so we get 'failed').
        import urllib.error
        expected_names = {df.name for df in all_data_files()}

        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("mocked")):
                results = refresh_all(force=True)

        assert set(results.keys()) == expected_names

    def test_status_values_are_valid(self, tmp_path: Path):
        """All status values must be one of the defined literals."""
        import urllib.error
        valid_statuses = {"ok", "not_stale", "failed"}

        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("mocked")):
                results = refresh_all(force=True)

        for name, status in results.items():
            assert status in valid_statuses, f"{name!r} got unexpected status {status!r}"

    def test_not_stale_returned_when_file_is_fresh(self, tmp_path: Path):
        """Files that are not stale should return 'not_stale' without a fetch."""
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            # Create every managed file and set its mtime to now.
            for df in all_data_files():
                df.path.write_bytes(b"seed content")
                now = time.time()
                os.utime(df.path, (now, now))

            with patch("urllib.request.urlopen") as mock_open:
                results = refresh_all(force=False)

            # No network call should have been made.
            mock_open.assert_not_called()
            for status in results.values():
                assert status == "not_stale"

    def test_failed_status_on_network_error(self, tmp_path: Path):
        """When every fetch fails, every result should be 'failed'."""
        import urllib.error
        with patch("sparrow_elastic.data_refresh._DATA_DIR", tmp_path):
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("network down")):
                results = refresh_all(force=True)

        for name, status in results.items():
            assert status == "failed", f"{name!r} expected 'failed', got {status!r}"


# ---------------------------------------------------------------------------
# all_data_files — registry completeness
# ---------------------------------------------------------------------------

class TestAllDataFiles:
    def test_returns_six_items(self):
        # Five original files + fingerbank.db added in Step 5b.
        assert len(all_data_files()) == 6

    def test_manuf_is_raw_format(self):
        manuf = next(df for df in all_data_files() if df.name == "manuf")
        assert manuf.format == "raw"
        assert manuf.max_age_days == 90

    def test_bt_sig_company_ids_is_yaml(self):
        df = next(d for d in all_data_files() if d.name == "bt_sig_company_ids.json")
        assert df.format == "yaml"
        assert df.max_age_days == 30

    def test_apple_continuity_is_json(self):
        df = next(d for d in all_data_files() if "apple" in d.name)
        assert df.format == "json"
