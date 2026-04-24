"""Tests for sparrow-elastic.py CLI bridge.

Covers:
- Argparse: required --elasticserver enforced, defaults correct,
  SPARROW_ES_URL env fallback works.
- apply_fingerbank(): disabled, lookup returning None, lookup enriching
  matching class, lookup returning different class with strong Tier 1.
- Bootstrap sequence: mock SearchClient verifies all three calls per kind.

All tests are fully offline — no live HTTP calls, no live ES cluster.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import socket
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Path setup: repo root so both sparrow_elastic and the bridge script are found.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Load the bridge as a module (it has a hyphen in its name so importlib is
# needed rather than a plain import statement).
# ---------------------------------------------------------------------------

def _load_bridge():
    """Load sparrow-elastic.py as a module named ``_sparrow_elastic_bridge``."""
    bridge_path = os.path.join(_REPO_ROOT, "sparrow-elastic.py")
    spec = importlib.util.spec_from_file_location("_sparrow_elastic_bridge", bridge_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load_bridge()


# ---------------------------------------------------------------------------
# Helper: minimal WiFi document (output of build_wifi_document shape)
# ---------------------------------------------------------------------------

def _make_wifi_doc(
    class_guess: str = "phone",
    class_conf: float = 0.9,
    class_evidence: list | None = None,
    mac: str = "AA:BB:CC:DD:EE:FF",
) -> dict:
    return {
        "@timestamp": "2026-04-24T12:00:00Z",
        "ecs": {"version": "8.17.0"},
        "event": {"dataset": "sparrow.wifi"},
        "observer": {"id": socket.gethostname()},
        "source": {"mac": mac, "address": mac},
        "device": {
            "id": mac,
            "class_guess": class_guess,
            "class_confidence": class_conf,
            "class_evidence": class_evidence or [f"{class_guess}_base"],
        },
    }


def _make_bt_doc(
    class_guess: str = "headset",
    class_conf: float = 0.7,
    class_evidence: list | None = None,
    mac: str = "11:22:33:44:55:66",
) -> dict:
    doc = _make_wifi_doc(class_guess, class_conf, class_evidence, mac)
    doc["event"]["dataset"] = "sparrow.bluetooth"
    return doc


# ---------------------------------------------------------------------------
# Tests: Argparse
# ---------------------------------------------------------------------------

class TestArgparse(unittest.TestCase):

    def _parse(self, argv):
        """Call _build_parser().parse_args() and return the result."""
        return bridge._build_parser().parse_args(argv)

    def test_elasticserver_empty_when_not_in_env(self):
        """When neither --elasticserver nor SPARROW_ES_URL is set, elasticserver
        defaults to empty string.  Validation (and SystemExit) happens in main(),
        not at parse time, so that --refresh-data can work without the flag."""
        env_backup = os.environ.pop("SPARROW_ES_URL", None)
        try:
            args = self._parse([])
            # Parse succeeds; value is empty string.
            self.assertEqual(args.elasticserver, "")
        finally:
            if env_backup is not None:
                os.environ["SPARROW_ES_URL"] = env_backup

    def test_main_rejects_missing_elasticserver(self):
        """main() calls parser.error() (SystemExit) when elasticserver is empty
        and --refresh-data is not set."""
        env_backup = os.environ.pop("SPARROW_ES_URL", None)
        try:
            with self.assertRaises(SystemExit) as cm:
                bridge.main([])
            self.assertNotEqual(cm.exception.code, 0)
        finally:
            if env_backup is not None:
                os.environ["SPARROW_ES_URL"] = env_backup

    def test_elasticserver_accepted_from_cli(self):
        args = self._parse(["--elasticserver", "https://es:9200"])
        self.assertEqual(args.elasticserver, "https://es:9200")

    def test_env_fallback_sparrow_es_url(self):
        """SPARROW_ES_URL env var provides the --elasticserver value."""
        env_backup = os.environ.get("SPARROW_ES_URL")
        try:
            os.environ["SPARROW_ES_URL"] = "https://from-env:9200"
            args = self._parse([])
            self.assertEqual(args.elasticserver, "https://from-env:9200")
        finally:
            if env_backup is None:
                os.environ.pop("SPARROW_ES_URL", None)
            else:
                os.environ["SPARROW_ES_URL"] = env_backup

    def test_cli_flag_takes_precedence_over_env(self):
        env_backup = os.environ.get("SPARROW_ES_URL")
        try:
            os.environ["SPARROW_ES_URL"] = "https://from-env:9200"
            args = self._parse(["--elasticserver", "https://from-cli:9200"])
            self.assertEqual(args.elasticserver, "https://from-cli:9200")
        finally:
            if env_backup is None:
                os.environ.pop("SPARROW_ES_URL", None)
            else:
                os.environ["SPARROW_ES_URL"] = env_backup

    def test_defaults(self):
        # Clean environment for this test.
        env_vars = ["SPARROW_ES_URL", "SPARROW_ES_ENGINE", "SPARROW_ES_USERNAME",
                    "SPARROW_ES_PASSWORD", "SPARROW_ES_API_KEY",
                    "SPARROW_FINGERBANK_API_KEY"]
        backup = {k: os.environ.pop(k, None) for k in env_vars}
        try:
            args = self._parse(["--elasticserver", "https://es:9200"])
            self.assertEqual(args.engine, "elasticsearch")
            self.assertEqual(args.sparrowagent, "127.0.0.1")
            self.assertEqual(args.sparrowport, 8020)
            self.assertEqual(args.wifi_alias, "sparrow-wifi")
            self.assertEqual(args.bt_alias, "sparrow-bt")
            self.assertEqual(args.scandelay, 15.0)
            self.assertEqual(args.flush_interval, 5.0)
            self.assertEqual(args.batch_size, 500)
            self.assertFalse(args.dont_create_indices)
            self.assertFalse(args.refresh_data)
            self.assertFalse(args.debug)
            self.assertTrue(args.verify_tls)
        finally:
            for k, v in backup.items():
                if v is not None:
                    os.environ[k] = v

    def test_engine_choices(self):
        args = self._parse(["--elasticserver", "https://es:9200",
                            "--engine", "opensearch"])
        self.assertEqual(args.engine, "opensearch")

    def test_invalid_engine_raises(self):
        with self.assertRaises(SystemExit):
            self._parse(["--elasticserver", "https://es:9200",
                         "--engine", "solr"])

    def test_no_verify_tls_flag(self):
        args = self._parse(["--elasticserver", "https://es:9200",
                            "--no-verify-tls"])
        self.assertFalse(args.verify_tls)

    def test_env_fallback_username_password_api_key(self):
        backup = {
            "SPARROW_ES_USERNAME": os.environ.pop("SPARROW_ES_USERNAME", None),
            "SPARROW_ES_PASSWORD": os.environ.pop("SPARROW_ES_PASSWORD", None),
            "SPARROW_ES_API_KEY":  os.environ.pop("SPARROW_ES_API_KEY", None),
        }
        try:
            os.environ["SPARROW_ES_USERNAME"] = "alice"
            os.environ["SPARROW_ES_PASSWORD"] = "secret"
            os.environ["SPARROW_ES_API_KEY"]  = "mykey"
            args = self._parse(["--elasticserver", "https://es:9200"])
            self.assertEqual(args.username, "alice")
            self.assertEqual(args.password, "secret")
            self.assertEqual(args.api_key, "mykey")
        finally:
            for k, v in backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_env_fallback_fingerbank_api_key(self):
        bk = os.environ.pop("SPARROW_FINGERBANK_API_KEY", None)
        try:
            os.environ["SPARROW_FINGERBANK_API_KEY"] = "fb_key_xyz"
            args = self._parse(["--elasticserver", "https://es:9200"])
            self.assertEqual(args.fingerbank_api_key, "fb_key_xyz")
        finally:
            if bk is None:
                os.environ.pop("SPARROW_FINGERBANK_API_KEY", None)
            else:
                os.environ["SPARROW_FINGERBANK_API_KEY"] = bk

    def test_refresh_data_flag(self):
        # --refresh-data does not require --elasticserver at parse time.
        args = self._parse(["--refresh-data"])
        self.assertTrue(args.refresh_data)


# ---------------------------------------------------------------------------
# Tests: apply_fingerbank()
# ---------------------------------------------------------------------------

class TestApplyFingerbank(unittest.TestCase):
    """Unit tests for the apply_fingerbank post-processor in the bridge."""

    def _settings_disabled(self) -> dict:
        return {
            "fingerbank_api_key": "",
            "fingerbank_offline_db": "/nonexistent/fingerbank.db",
        }

    def _settings_enabled(self, api_key: str = "test-key") -> dict:
        return {
            "fingerbank_api_key": api_key,
            "fingerbank_offline_db": "",
        }

    def test_disabled_state_doc_unchanged(self):
        """When Fingerbank is disabled, the document must not be modified."""
        doc = _make_wifi_doc(class_guess="phone", class_conf=0.9)
        original = json.loads(json.dumps(doc))

        # fingerbank_enabled() returns False because no key and no DB file.
        bridge.apply_fingerbank(doc, self._settings_disabled())

        self.assertEqual(doc["device"]["class_guess"], original["device"]["class_guess"])
        self.assertEqual(doc["device"]["class_confidence"], original["device"]["class_confidence"])
        self.assertEqual(doc["device"]["class_evidence"], original["device"]["class_evidence"])

    def test_lookup_returning_none_doc_unchanged(self):
        """When lookup() returns None (no match), the document must not change."""
        doc = _make_wifi_doc(class_guess="phone", class_conf=0.9)
        original_class = doc["device"]["class_guess"]
        original_conf = doc["device"]["class_confidence"]

        with patch.object(bridge, "fb_lookup", return_value=None):
            bridge.apply_fingerbank(doc, self._settings_enabled())

        self.assertEqual(doc["device"]["class_guess"], original_class)
        self.assertEqual(doc["device"]["class_confidence"], original_conf)

    def test_lookup_matching_class_confidence_bumps(self):
        """Fingerbank agreeing with existing class should raise combined confidence."""
        from sparrow_elastic.fingerbank_client import FingerbankResult

        doc = _make_wifi_doc(
            class_guess="phone",
            class_conf=0.7,
            class_evidence=["oui_apple"],
        )
        fb_result = FingerbankResult(
            device_model="Apple iPhone 13",
            device_type="Phone",
            confidence=0.72,   # maps to "phone"
            source="live_api",
            raw={},
        )

        with patch.object(bridge, "fb_lookup", return_value=fb_result):
            bridge.apply_fingerbank(doc, self._settings_enabled())

        # Combined confidence must be higher than either input alone.
        self.assertGreater(doc["device"]["class_confidence"], 0.7)
        self.assertEqual(doc["device"]["class_guess"], "phone")
        # Fingerbank tag should appear in evidence.
        self.assertTrue(
            any("fingerbank" in tag for tag in doc["device"]["class_evidence"]),
            "Expected a fingerbank: tag in evidence"
        )

    def test_strong_tier1_class_not_overridden_by_different_fb_class(self):
        """When Tier 1 fires at 0.95 for 'phone' and Fingerbank says 'laptop',
        the winner should remain 'phone' because prob-OR keeps the Tier 1 lead.

        Fingerbank's tag should NOT appear in the final evidence since the
        winning class is 'phone' and combine_matches only collects tags from
        the winner.
        """
        from sparrow_elastic.fingerbank_client import FingerbankResult

        doc = _make_wifi_doc(
            class_guess="phone",
            class_conf=0.95,
            class_evidence=["cod_major_phone"],
        )
        # Fingerbank returns a different class at mid-range confidence.
        fb_result = FingerbankResult(
            device_model="Dell Latitude",
            device_type="Laptop",
            confidence=0.65,   # maps to "laptop"
            source="live_api",
            raw={},
        )

        with patch.object(bridge, "fb_lookup", return_value=fb_result):
            bridge.apply_fingerbank(doc, self._settings_enabled())

        # phone must still win.
        self.assertEqual(doc["device"]["class_guess"], "phone",
                         "Strong Tier 1 'phone' should not be overridden by Fingerbank")
        # confidence must stay at or above existing (prob-OR cannot decrease it).
        self.assertGreaterEqual(doc["device"]["class_confidence"], 0.95)
        # The fingerbank tag for 'laptop' must NOT be present because laptop lost.
        for tag in doc["device"]["class_evidence"]:
            self.assertNotIn("fingerbank", tag,
                             f"Fingerbank (losing class) tag leaked into evidence: {tag}")

    def test_no_mac_address_skips_lookup(self):
        """When source.mac is absent, lookup must not be called."""
        doc = _make_wifi_doc()
        doc["source"]["mac"] = ""  # no MAC

        with patch.object(bridge, "fb_lookup") as mock_lookup:
            bridge.apply_fingerbank(doc, self._settings_enabled())
            mock_lookup.assert_not_called()

    def test_unknown_class_with_fingerbank_sets_class(self):
        """When the document has class_guess='unknown' and Fingerbank fires,
        the Fingerbank class should become the new winner."""
        from sparrow_elastic.fingerbank_client import FingerbankResult

        doc = _make_wifi_doc(class_guess="unknown", class_conf=0.0, class_evidence=[])
        fb_result = FingerbankResult(
            device_model="Nest Thermostat",
            device_type="Smart Home",
            confidence=0.6,   # maps to "iot"
            source="offline_db",
            raw={},
        )

        with patch.object(bridge, "fb_lookup", return_value=fb_result):
            bridge.apply_fingerbank(doc, self._settings_enabled())

        self.assertEqual(doc["device"]["class_guess"], "iot")
        self.assertGreater(doc["device"]["class_confidence"], 0.0)
        self.assertTrue(
            any("fingerbank" in tag for tag in doc["device"]["class_evidence"])
        )


# ---------------------------------------------------------------------------
# Tests: Bootstrap sequence
# ---------------------------------------------------------------------------

class TestBootstrap(unittest.TestCase):
    """Verify that bootstrap() calls the correct SearchClient methods."""

    def _make_mock_client(self) -> MagicMock:
        mock = MagicMock()
        mock.ensure_policy.return_value = None
        mock.ensure_template.return_value = None
        mock.ensure_initial_index.return_value = None
        return mock

    def _run_bootstrap(
        self,
        alias: str,
        kind: str,
        engine: str = "elasticsearch",
        ilm_override: str = "",
    ) -> MagicMock:
        """Run bridge.bootstrap() with mocked resolve_template + policy load."""
        mock_client = self._make_mock_client()
        fake_template = {"index_patterns": [f"{alias}-*"], "template": {}}

        with (
            patch.object(bridge, "resolve_template", return_value=fake_template),
            patch.object(bridge, "_load_policy", return_value={"policy": {}}),
        ):
            result = bridge.bootstrap(
                mock_client, alias, kind, engine, ilm_override
            )

        return mock_client, result

    def test_wifi_bootstrap_calls_all_three_methods(self):
        mock_client, success = self._run_bootstrap("sparrow-wifi", "wifi")

        self.assertTrue(success, "bootstrap() should return True on success")
        mock_client.ensure_policy.assert_called_once()
        mock_client.ensure_template.assert_called_once()
        mock_client.ensure_initial_index.assert_called_once()

    def test_bt_bootstrap_calls_all_three_methods(self):
        mock_client, success = self._run_bootstrap("sparrow-bt", "bt")

        self.assertTrue(success)
        mock_client.ensure_policy.assert_called_once()
        mock_client.ensure_template.assert_called_once()
        mock_client.ensure_initial_index.assert_called_once()

    def test_ensure_initial_index_called_with_alias_and_000001(self):
        mock_client, _ = self._run_bootstrap("sparrow-wifi", "wifi")

        args_pos, _ = mock_client.ensure_initial_index.call_args
        alias_arg, initial_index_arg = args_pos
        self.assertEqual(alias_arg, "sparrow-wifi")
        self.assertEqual(initial_index_arg, "sparrow-wifi-000001")

    def test_bt_initial_index_name_derived_from_alias(self):
        mock_client, _ = self._run_bootstrap("sparrow-bt", "bt")

        args_pos, _ = mock_client.ensure_initial_index.call_args
        alias_arg, initial_index_arg = args_pos
        self.assertEqual(alias_arg, "sparrow-bt")
        self.assertEqual(initial_index_arg, "sparrow-bt-000001")

    def test_ilm_override_passed_to_ensure_policy(self):
        mock_client, _ = self._run_bootstrap(
            "sparrow-wifi", "wifi", ilm_override="my-custom-policy"
        )

        args_pos, _ = mock_client.ensure_policy.call_args
        policy_name_arg = args_pos[0]
        self.assertEqual(policy_name_arg, "my-custom-policy")

    def test_default_ilm_policy_name_for_wifi(self):
        mock_client, _ = self._run_bootstrap("sparrow-wifi", "wifi", ilm_override="")

        args_pos, _ = mock_client.ensure_policy.call_args
        policy_name_arg = args_pos[0]
        self.assertEqual(policy_name_arg, "sparrow-wifi-ilm")

    def test_default_ilm_policy_name_for_bt(self):
        mock_client, _ = self._run_bootstrap("sparrow-bt", "bt", ilm_override="")

        args_pos, _ = mock_client.ensure_policy.call_args
        policy_name_arg = args_pos[0]
        self.assertEqual(policy_name_arg, "sparrow-bt-ilm")

    def test_template_resolution_failure_returns_false(self):
        """If resolve_template raises, bootstrap() returns False and makes no
        calls to ensure_template or ensure_initial_index."""
        mock_client = self._make_mock_client()

        with (
            patch.object(bridge, "resolve_template",
                         side_effect=FileNotFoundError("no such template")),
            patch.object(bridge, "_load_policy", return_value={"policy": {}}),
        ):
            result = bridge.bootstrap(mock_client, "sparrow-wifi", "wifi",
                                      "elasticsearch", "")

        self.assertFalse(result)
        mock_client.ensure_template.assert_not_called()
        mock_client.ensure_initial_index.assert_not_called()

    def test_policy_failure_is_non_fatal(self):
        """ensure_policy() failure is logged but does NOT abort bootstrap."""
        mock_client = self._make_mock_client()
        mock_client.ensure_policy.side_effect = Exception("policy server error")
        fake_template = {"index_patterns": ["sparrow-wifi-*"]}

        with (
            patch.object(bridge, "resolve_template", return_value=fake_template),
            patch.object(bridge, "_load_policy", return_value={"policy": {}}),
        ):
            result = bridge.bootstrap(mock_client, "sparrow-wifi", "wifi",
                                      "elasticsearch", "")

        # bootstrap still returns True because template + index succeeded.
        self.assertTrue(result)
        mock_client.ensure_template.assert_called_once()
        mock_client.ensure_initial_index.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: FlushState
# ---------------------------------------------------------------------------

class TestFlushState(unittest.TestCase):

    def test_not_due_below_batch_and_interval(self):
        fs = bridge.FlushState(flush_interval=60.0, batch_size=100)
        self.assertFalse(fs.is_due(50))

    def test_due_when_depth_reaches_batch_size(self):
        fs = bridge.FlushState(flush_interval=60.0, batch_size=10)
        self.assertTrue(fs.is_due(10))
        self.assertTrue(fs.is_due(20))

    def test_due_when_interval_elapsed(self):
        fs = bridge.FlushState(flush_interval=0.0, batch_size=1000)
        # flush_interval=0 means always due.
        self.assertTrue(fs.is_due(0))

    def test_mark_flushed_resets_timer(self):
        fs = bridge.FlushState(flush_interval=0.001, batch_size=1000)
        # Advance past the interval (it's 1 ms so by the time we check it's elapsed).
        import time
        time.sleep(0.005)
        self.assertTrue(fs.is_due(0))
        fs.mark_flushed()
        # Immediately after marking, timer should not be due again for 1 ms.
        self.assertFalse(fs.is_due(0))


if __name__ == "__main__":
    unittest.main()
