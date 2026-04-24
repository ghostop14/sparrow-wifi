"""Tests for sparrow_elastic.fingerbank_client.

All external I/O (sqlite3, urllib) is mocked — no live network calls are made.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import time
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, call, patch

# Ensure the project root is on sys.path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sparrow_elastic.fingerbank_client as fb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_module():
    """Reset all module-level state between tests."""
    fb._api_key = None
    fb._offline_db_path = None
    fb._NEG_CACHE.clear()


def _make_live_response(data: dict, status: int = 200) -> MagicMock:
    """Build a fake urllib response context manager."""
    body = json.dumps(data).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# Minimal Fingerbank live-API success payload.
_LIVE_PAYLOAD = {
    "device": {
        "name": "Apple iPhone 13",
        "device_type": {"name": "Phone"},
    },
    "score": 80,
}


# ---------------------------------------------------------------------------
# FingerbankResult dataclass
# ---------------------------------------------------------------------------

class TestFingerbankResult(unittest.TestCase):

    def test_frozen_and_comparable(self):
        r1 = fb.FingerbankResult(
            device_model="iPhone", device_type="Phone",
            confidence=0.8, source="live_api", raw={},
        )
        r2 = fb.FingerbankResult(
            device_model="iPhone", device_type="Phone",
            confidence=0.8, source="live_api", raw={},
        )
        self.assertEqual(r1, r2)

    def test_frozen_raises_on_mutation(self):
        r = fb.FingerbankResult(
            device_model="X", device_type="Y",
            confidence=0.5, source="live_api", raw={},
        )
        with self.assertRaises((FrozenInstanceError, TypeError, AttributeError)):
            r.device_model = "Z"  # type: ignore[misc]

    def test_fields_accessible(self):
        r = fb.FingerbankResult(
            device_model="Samsung TV", device_type="IoT Device",
            confidence=0.6, source="offline_db", raw={"k": "v"},
        )
        self.assertEqual(r.device_model, "Samsung TV")
        self.assertEqual(r.device_type, "IoT Device")
        self.assertAlmostEqual(r.confidence, 0.6)
        self.assertEqual(r.source, "offline_db")
        self.assertEqual(r.raw, {"k": "v"})


# ---------------------------------------------------------------------------
# lookup — early exits
# ---------------------------------------------------------------------------

class TestLookupEarlyExits(unittest.TestCase):

    def setUp(self):
        _reset_module()

    def test_none_mac_returns_none(self):
        self.assertIsNone(fb.lookup(None))

    def test_empty_mac_returns_none(self):
        self.assertIsNone(fb.lookup(""))

    def test_no_api_key_no_offline_db_returns_none(self):
        """Disabled when neither source is available."""
        # Ensure DB does not exist and no key is set.
        with patch("os.path.isfile", return_value=False):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")
        self.assertIsNone(result)

    def test_disabled_does_not_call_api(self):
        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen") as mock_url:
            fb.lookup("AA:BB:CC:DD:EE:FF")
        mock_url.assert_not_called()


# ---------------------------------------------------------------------------
# lookup — offline DB path
# ---------------------------------------------------------------------------

class TestLookupOfflineDB(unittest.TestCase):

    def setUp(self):
        _reset_module()

    def _make_sqlite_cursor(self, row):
        """Return a mock sqlite3 connection/cursor that yields *row*."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        return mock_conn

    def test_offline_db_hit_returns_result(self):
        """Mock sqlite3 returns a row; lookup yields FingerbankResult."""
        mock_conn = self._make_sqlite_cursor(("Apple iPhone 13", "Phone", 85))

        with patch("os.path.isfile", return_value=True), \
             patch("sqlite3.connect", return_value=mock_conn):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNotNone(result)
        self.assertEqual(result.device_model, "Apple iPhone 13")
        self.assertEqual(result.device_type, "Phone")
        self.assertAlmostEqual(result.confidence, 0.85)
        self.assertEqual(result.source, "offline_db")

    def test_offline_db_miss_with_no_api_key_returns_none(self):
        """DB returns no row and no API key → None."""
        mock_conn = self._make_sqlite_cursor(None)

        with patch("os.path.isfile", return_value=True), \
             patch("sqlite3.connect", return_value=mock_conn):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_offline_db_miss_with_api_key_tries_live_api(self):
        """DB miss + api_key → falls through to live API."""
        fb._api_key = "test-key"
        mock_conn = self._make_sqlite_cursor(None)
        mock_resp = _make_live_response(_LIVE_PAYLOAD)

        with patch("os.path.isfile", return_value=True), \
             patch("sqlite3.connect", return_value=mock_conn), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNotNone(result)
        self.assertEqual(result.source, "live_api")

    def test_offline_db_missing_with_api_key_goes_directly_to_live(self):
        """When offline DB file doesn't exist, go straight to live API."""
        fb._api_key = "test-key"
        mock_resp = _make_live_response(_LIVE_PAYLOAD)

        def isfile_side_effect(path):
            # DB absent; no other file checks needed for this code path.
            return False

        with patch("os.path.isfile", side_effect=isfile_side_effect), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNotNone(result)
        self.assertEqual(result.source, "live_api")

    def test_offline_db_exception_returns_none_no_raise(self):
        """sqlite3 error → return None, do not re-raise."""
        with patch("os.path.isfile", return_value=True), \
             patch("sqlite3.connect", side_effect=sqlite3.DatabaseError("corrupt")):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# lookup — live API
# ---------------------------------------------------------------------------

class TestLookupLiveAPI(unittest.TestCase):

    def setUp(self):
        _reset_module()
        fb._api_key = "test-api-key"

    def test_live_api_200_valid_json_returns_result(self):
        mock_resp = _make_live_response(_LIVE_PAYLOAD)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNotNone(result)
        self.assertEqual(result.device_model, "Apple iPhone 13")
        self.assertEqual(result.device_type, "Phone")
        self.assertAlmostEqual(result.confidence, 0.80)
        self.assertEqual(result.source, "live_api")

    def test_live_api_non_200_returns_none(self):
        mock_resp = _make_live_response({}, status=500)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_live_api_timeout_returns_none(self):
        import urllib.error
        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_live_api_non_json_response_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html>error page</html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_live_api_500_returns_none(self):
        mock_resp = _make_live_response({"error": "server error"}, status=500)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_live_api_empty_device_returns_none(self):
        payload = {"device": {}, "score": 0}
        mock_resp = _make_live_response(payload)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = fb.lookup("AA:BB:CC:DD:EE:FF")

        self.assertIsNone(result)

    def test_live_api_logs_at_debug_on_error(self):
        import urllib.error
        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("network down")), \
             self.assertLogs("sparrow_elastic.fingerbank_client", level="DEBUG") as log_ctx:
            fb.lookup("AA:BB:CC:DD:EE:FF")

        # At least one DEBUG message should have been emitted.
        self.assertTrue(
            any("DEBUG" in line for line in log_ctx.output),
            f"No DEBUG log found: {log_ctx.output}",
        )


# ---------------------------------------------------------------------------
# Negative-cache TTL
# ---------------------------------------------------------------------------

class TestNegativeCacheTTL(unittest.TestCase):

    def setUp(self):
        _reset_module()
        fb._api_key = "test-key"

    def test_same_mac_miss_does_not_re_query_within_ttl(self):
        """A miss caches the result; second call within TTL skips urlopen."""
        mock_resp = _make_live_response({}, status=200)
        # First call: empty result → negative cache entry
        empty_payload = {"device": {}, "score": 0}
        mock_resp_empty = _make_live_response(empty_payload)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   return_value=mock_resp_empty) as mock_url:
            fb.lookup("AA:BB:CC:DD:EE:FF")
            fb.lookup("AA:BB:CC:DD:EE:FF")

        # urlopen should have been called exactly once.
        self.assertEqual(mock_url.call_count, 1)

    def test_neg_cache_expires_after_ttl(self):
        """After TTL expires the entry is evicted and a fresh call is made."""
        empty_payload = {"device": {}, "score": 0}
        mock_resp_empty = _make_live_response(empty_payload)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   return_value=mock_resp_empty) as mock_url:
            fb.lookup("BB:CC:DD:EE:FF:00")

        # Expire the cache entry by back-dating it.
        fb._NEG_CACHE["BB:CC:DD:EE:FF:00"] = time.monotonic() - 1.0

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   return_value=mock_resp_empty) as mock_url2:
            fb.lookup("BB:CC:DD:EE:FF:00")

        self.assertEqual(mock_url2.call_count, 1)


# ---------------------------------------------------------------------------
# enrich_classification
# ---------------------------------------------------------------------------

class TestEnrichClassification(unittest.TestCase):

    def test_none_fb_result_returns_per_class_unchanged(self):
        per_class = {"phone": [(0.9, "cod:phone")]}
        result = fb.enrich_classification(per_class, None)
        self.assertEqual(result, {"phone": [(0.9, "cod:phone")]})
        self.assertIs(result, per_class)  # same object

    def test_known_type_adds_entry(self):
        per_class: dict = {}
        fb_result = fb.FingerbankResult(
            device_model="Apple iPhone 13", device_type="Phone",
            confidence=0.80, source="live_api", raw={},
        )
        result = fb.enrich_classification(per_class, fb_result)
        self.assertIn("phone", result)
        self.assertEqual(len(result["phone"]), 1)
        conf, tag = result["phone"][0]
        self.assertLessEqual(conf, 0.75)
        self.assertIn("fingerbank:Apple iPhone 13", tag)

    def test_confidence_capped_at_075_even_if_fb_conf_is_1(self):
        per_class: dict = {}
        fb_result = fb.FingerbankResult(
            device_model="Some Laptop", device_type="Laptop",
            confidence=1.0, source="offline_db", raw={},
        )
        fb.enrich_classification(per_class, fb_result)
        conf, _ = per_class["laptop"][0]
        self.assertLessEqual(conf, 0.75)

    def test_unknown_type_does_not_add_entry(self):
        per_class: dict = {}
        fb_result = fb.FingerbankResult(
            device_model="Mystery Box", device_type="Unknown Device",
            confidence=0.50, source="live_api", raw={},
        )
        result = fb.enrich_classification(per_class, fb_result)
        self.assertEqual(result, {})

    def test_empty_type_does_not_add_entry(self):
        per_class: dict = {}
        fb_result = fb.FingerbankResult(
            device_model="", device_type="",
            confidence=0.5, source="live_api", raw={},
        )
        result = fb.enrich_classification(per_class, fb_result)
        self.assertEqual(result, {})

    def test_fingerbank_agrees_with_tier1_bumps_combined_confidence(self):
        """When Fingerbank matches an existing Tier 1 class, combiner yields higher conf."""
        from sparrow_elastic.device_classifier import combine_matches

        # Tier 1: phone at 0.9 (simulating cod_major rule)
        per_class_base = {"phone": [(0.9, "cod:phone")]}
        base_cls, base_conf, _ = combine_matches(dict(per_class_base))

        # Add Fingerbank phone result.
        fb_result = fb.FingerbankResult(
            device_model="Samsung Galaxy", device_type="Phone",
            confidence=0.75, source="live_api", raw={},
        )
        per_class_enriched = {"phone": [(0.9, "cod:phone")]}
        fb.enrich_classification(per_class_enriched, fb_result)
        enriched_cls, enriched_conf, enriched_tags = combine_matches(per_class_enriched)

        self.assertEqual(enriched_cls, "phone")
        self.assertGreater(enriched_conf, base_conf)
        self.assertIn("fingerbank:Samsung Galaxy", enriched_tags)

    def test_tier1_wins_when_fingerbank_disagrees(self):
        """Tier 1 at 0.95 wins against Fingerbank at 0.75 for a different class."""
        from sparrow_elastic.device_classifier import combine_matches

        # Tier 1: headset at 0.95
        per_class = {"headset": [(0.95, "apple:airpods")]}

        fb_result = fb.FingerbankResult(
            device_model="Acme Phone", device_type="Phone",
            confidence=0.75, source="live_api", raw={},
        )
        fb.enrich_classification(per_class, fb_result)
        cls, conf, tags = combine_matches(per_class)

        self.assertEqual(cls, "headset")
        self.assertGreaterEqual(conf, 0.95)

    def test_evidence_tag_format_is_fingerbank_colon_model(self):
        per_class: dict = {}
        fb_result = fb.FingerbankResult(
            device_model="Nest Thermostat", device_type="Thermostat",
            confidence=0.70, source="offline_db", raw={},
        )
        fb.enrich_classification(per_class, fb_result)
        _, tag = per_class["iot"][0]
        self.assertEqual(tag, "fingerbank:Nest Thermostat")


# ---------------------------------------------------------------------------
# Taxonomy mapping — all mappings fire
# ---------------------------------------------------------------------------

class TestTaxonomyMapping(unittest.TestCase):
    """Verify every documented mapping in the spec fires correctly."""

    def _check(self, device_type: str, expected_class: str):
        result = fb._map_device_type(device_type)
        self.assertEqual(
            result, expected_class,
            f"device_type={device_type!r} → expected {expected_class!r}, got {result!r}",
        )

    # Phone group
    def test_phone_maps_to_phone(self):       self._check("Phone", "phone")
    def test_mobile_device_maps_to_phone(self): self._check("Mobile Device", "phone")
    def test_smartphone_maps_to_phone(self):  self._check("Smartphone", "phone")
    def test_iphone_maps_to_phone(self):      self._check("iPhone", "phone")
    def test_android_maps_to_phone(self):     self._check("Android", "phone")

    # Laptop group
    def test_laptop_maps_to_laptop(self):     self._check("Laptop", "laptop")
    def test_desktop_maps_to_laptop(self):    self._check("Desktop", "laptop")
    def test_windows_maps_to_laptop(self):    self._check("Windows", "laptop")
    def test_macos_maps_to_laptop(self):      self._check("macOS", "laptop")
    def test_linux_maps_to_laptop(self):      self._check("Linux", "laptop")

    # Printer group
    def test_printer_maps_to_printer(self):    self._check("Printer", "printer")
    def test_print_server_maps_to_printer(self): self._check("Print Server", "printer")

    # IoT group
    def test_iot_device_maps_to_iot(self):    self._check("IoT Device", "iot")
    def test_smart_home_maps_to_iot(self):    self._check("Smart Home", "iot")
    def test_thermostat_maps_to_iot(self):    self._check("Thermostat", "iot")
    def test_camera_maps_to_iot(self):        self._check("Camera", "iot")
    def test_doorbell_maps_to_iot(self):      self._check("Doorbell", "iot")

    # Wearable group
    def test_wearable_maps_to_wearable(self): self._check("Wearable", "wearable")
    def test_watch_maps_to_wearable(self):    self._check("Watch", "wearable")
    def test_fitness_tracker_maps_to_wearable(self): self._check("Fitness Tracker", "wearable")
    def test_smart_glasses_maps_to_wearable(self): self._check("Smart Glasses", "wearable")

    # Headset group
    def test_headset_maps_to_headset(self):   self._check("Headset", "headset")
    def test_earbuds_maps_to_headset(self):   self._check("Earbuds", "headset")
    def test_audio_device_maps_to_headset(self): self._check("Audio Device", "headset")

    # Speaker group
    def test_speaker_maps_to_speaker(self):   self._check("Speaker", "speaker")
    def test_home_assistant_maps_to_speaker(self): self._check("Home Assistant", "speaker")
    def test_smart_speaker_maps_to_speaker(self): self._check("Smart Speaker", "speaker")

    # AP group
    def test_access_point_maps_to_ap(self):   self._check("Access Point", "ap")
    def test_router_maps_to_ap(self):         self._check("Router", "ap")
    def test_network_device_maps_to_ap(self): self._check("Network Device", "ap")

    # Vehicle group
    def test_vehicle_maps_to_vehicle(self):   self._check("Vehicle", "vehicle")
    def test_car_maps_to_vehicle(self):       self._check("Car", "vehicle")
    def test_automotive_maps_to_vehicle(self): self._check("Automotive", "vehicle")

    # Unknown / unmapped
    def test_unknown_maps_to_none(self):      self.assertIsNone(fb._map_device_type("Unknown"))
    def test_empty_maps_to_none(self):        self.assertIsNone(fb._map_device_type(""))
    def test_arbitrary_maps_to_none(self):    self.assertIsNone(fb._map_device_type("Submarine"))
    def test_none_maps_to_none(self):
        # _map_device_type is typed str; pass empty string rather than None for that edge.
        self.assertIsNone(fb._map_device_type(""))


# ---------------------------------------------------------------------------
# settings.fingerbank_enabled helper
# ---------------------------------------------------------------------------

class TestFingerbankEnabled(unittest.TestCase):

    def test_enabled_with_api_key_set(self):
        from sparrow_elastic.settings import fingerbank_enabled
        settings = {"fingerbank_api_key": "abc123", "fingerbank_offline_db": ""}
        self.assertTrue(fingerbank_enabled(settings))

    def test_disabled_with_no_key_and_no_db(self):
        from sparrow_elastic.settings import fingerbank_enabled
        with patch("os.path.isfile", return_value=False):
            settings = {"fingerbank_api_key": "", "fingerbank_offline_db": ""}
            self.assertFalse(fingerbank_enabled(settings))

    def test_enabled_with_db_file_present(self):
        from sparrow_elastic.settings import fingerbank_enabled
        with patch("os.path.isfile", return_value=True):
            settings = {"fingerbank_api_key": "", "fingerbank_offline_db": ""}
            self.assertTrue(fingerbank_enabled(settings))

    def test_enabled_with_explicit_db_path(self):
        from sparrow_elastic.settings import fingerbank_enabled
        with patch("os.path.isfile", return_value=True):
            settings = {
                "fingerbank_api_key": "",
                "fingerbank_offline_db": "/custom/path/fingerbank.db",
            }
            self.assertTrue(fingerbank_enabled(settings))

    def test_disabled_with_explicit_nonexistent_db(self):
        from sparrow_elastic.settings import fingerbank_enabled
        with patch("os.path.isfile", return_value=False):
            settings = {
                "fingerbank_api_key": "",
                "fingerbank_offline_db": "/nonexistent/fingerbank.db",
            }
            self.assertFalse(fingerbank_enabled(settings))


# ---------------------------------------------------------------------------
# data_refresh registry
# ---------------------------------------------------------------------------

class TestDataRefreshRegistry(unittest.TestCase):

    def test_fingerbank_db_in_registry(self):
        from sparrow_elastic.data_refresh import all_data_files
        names = [df.name for df in all_data_files()]
        self.assertIn("fingerbank.db", names)

    def test_fingerbank_db_is_raw_format(self):
        from sparrow_elastic.data_refresh import all_data_files
        df = next(d for d in all_data_files() if d.name == "fingerbank.db")
        self.assertEqual(df.format, "raw")

    def test_fingerbank_db_max_age_is_7_days(self):
        from sparrow_elastic.data_refresh import all_data_files
        df = next(d for d in all_data_files() if d.name == "fingerbank.db")
        self.assertEqual(df.max_age_days, 7)

    def test_fingerbank_db_url(self):
        from sparrow_elastic.data_refresh import all_data_files
        df = next(d for d in all_data_files() if d.name == "fingerbank.db")
        self.assertIn("fingerbank.org", df.url)


# ---------------------------------------------------------------------------
# Integration: enrich then combine — Tier 1 still dominant
# ---------------------------------------------------------------------------

class TestIntegrationEnrichAndCombine(unittest.TestCase):

    def test_tier1_headset_095_still_wins_after_fingerbank_phone_0_75(self):
        """Fingerbank phone at 0.75 must not overturn Tier 1 headset at 0.95."""
        from sparrow_elastic.device_classifier import combine_matches

        per_class = {"headset": [(0.95, "apple:airpods")]}
        fb_result = fb.FingerbankResult(
            device_model="Generic Phone", device_type="Phone",
            confidence=0.80, source="live_api", raw={},
        )
        fb.enrich_classification(per_class, fb_result)
        cls, conf, tags = combine_matches(per_class)

        self.assertEqual(cls, "headset",
                         f"Expected headset, got {cls} (conf={conf}, tags={tags})")

    def test_fingerbank_evidence_appears_in_winning_class_tags(self):
        """When Fingerbank agrees with winner, its tag appears in the evidence list."""
        from sparrow_elastic.device_classifier import combine_matches

        per_class = {"phone": [(0.9, "cod:phone")]}
        fb_result = fb.FingerbankResult(
            device_model="Samsung Galaxy S22", device_type="Phone",
            confidence=0.80, source="offline_db", raw={},
        )
        fb.enrich_classification(per_class, fb_result)
        cls, conf, tags = combine_matches(per_class)

        self.assertEqual(cls, "phone")
        self.assertIn("fingerbank:Samsung Galaxy S22", tags)


if __name__ == "__main__":
    unittest.main()
