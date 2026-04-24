"""
tests/test_dashboards.py — Test suite for Step 8 dashboard NDJSON files
and the install_dashboards.py installer script.

Run with: pytest tests/test_dashboards.py -v
"""

import importlib.util
import io
import json
import os
import unittest
import unittest.mock

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARDS_DIR = os.path.join(REPO_ROOT, "sparrow_elastic", "dashboards")
INSTALLER = os.path.join(REPO_ROOT, "install_dashboards.py")

EXPECTED_FILES = {
    "index_patterns.ndjson",
    "sparrow_wifi_situational_awareness.ndjson",
    "sparrow_wifi_pattern_of_life.ndjson",
    "sparrow_wifi_new_device_detection.ndjson",
    "sparrow_wifi_spectrum_planning.ndjson",
    "legacy_preserved.ndjson",
}

VALID_INDEX_PATTERN_IDS = {"sparrow-wifi", "sparrow-bt"}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_ndjson(filename):
    """Return list of parsed objects from an NDJSON file."""
    path = os.path.join(DASHBOARDS_DIR, filename)
    objects = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)  # raises on bad JSON
            obj["_lineno"] = lineno
            objects.append(obj)
    return objects


def _load_installer():
    """Import install_dashboards module dynamically."""
    spec = importlib.util.spec_from_file_location("install_dashboards", INSTALLER)
    assert spec is not None and spec.loader is not None, \
        f"Failed to load spec for {INSTALLER}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 8e-1: File existence
# ---------------------------------------------------------------------------

class TestNDJSONFilesExist(unittest.TestCase):
    def test_all_expected_files_present(self):
        present = set(os.listdir(DASHBOARDS_DIR))
        for filename in EXPECTED_FILES:
            with self.subTest(file=filename):
                self.assertIn(
                    filename,
                    present,
                    f"Expected NDJSON file missing: {filename}",
                )

    def test_no_unexpected_ndjson_files(self):
        """Each NDJSON file in dashboards dir is accounted for in the spec."""
        present = {
            f for f in os.listdir(DASHBOARDS_DIR) if f.endswith(".ndjson")
        }
        extra = present - EXPECTED_FILES
        self.assertEqual(
            extra,
            set(),
            f"Unexpected NDJSON files found: {extra}",
        )


# ---------------------------------------------------------------------------
# 8e-2: All lines are valid JSON
# ---------------------------------------------------------------------------

class TestNDJSONValidity(unittest.TestCase):
    def test_all_lines_valid_json(self):
        for filename in sorted(EXPECTED_FILES):
            with self.subTest(file=filename):
                path = os.path.join(DASHBOARDS_DIR, filename)
                with open(path, encoding="utf-8") as fh:
                    for lineno, raw_line in enumerate(fh, 1):
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            json.loads(raw_line)
                        except json.JSONDecodeError as exc:
                            self.fail(
                                f"{filename}:{lineno} — JSON parse error: {exc}"
                            )


# ---------------------------------------------------------------------------
# 8e-3: Required fields on every object
# ---------------------------------------------------------------------------

class TestRequiredFields(unittest.TestCase):
    REQUIRED = ("id", "type", "attributes")

    def test_required_fields_present(self):
        for filename in sorted(EXPECTED_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                lineno = obj.get("_lineno")
                for field in self.REQUIRED:
                    with self.subTest(file=filename, line=lineno, field=field):
                        self.assertIn(
                            field,
                            obj,
                            f"{filename}:{lineno} — missing required field '{field}'",
                        )

    def test_attributes_is_dict(self):
        for filename in sorted(EXPECTED_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                lineno = obj.get("_lineno")
                with self.subTest(file=filename, line=lineno):
                    self.assertIsInstance(
                        obj["attributes"],
                        dict,
                        f"{filename}:{lineno} — 'attributes' must be a dict",
                    )

    def test_id_is_nonempty_string(self):
        for filename in sorted(EXPECTED_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                lineno = obj.get("_lineno")
                with self.subTest(file=filename, line=lineno):
                    self.assertIsInstance(obj["id"], str)
                    self.assertGreater(len(obj["id"]), 0)

    def test_type_is_known(self):
        known_types = {
            "index-pattern", "visualization", "dashboard", "search", "lens",
        }
        for filename in sorted(EXPECTED_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                lineno = obj.get("_lineno")
                with self.subTest(file=filename, line=lineno):
                    self.assertIn(
                        obj["type"],
                        known_types,
                        f"{filename}:{lineno} — unrecognised type '{obj['type']}'",
                    )


# ---------------------------------------------------------------------------
# 8e-4: Index-pattern file content
# ---------------------------------------------------------------------------

class TestIndexPatterns(unittest.TestCase):
    def setUp(self):
        self.objects = _load_ndjson("index_patterns.ndjson")

    def test_exactly_two_index_patterns(self):
        self.assertEqual(len(self.objects), 2)

    def test_both_are_index_pattern_type(self):
        for obj in self.objects:
            self.assertEqual(obj["type"], "index-pattern")

    def test_correct_ids(self):
        ids = {obj["id"] for obj in self.objects}
        self.assertEqual(ids, VALID_INDEX_PATTERN_IDS)

    def test_correct_titles(self):
        expected = {
            "sparrow-wifi": "sparrow-wifi-*",
            "sparrow-bt": "sparrow-bt-*",
        }
        for obj in self.objects:
            self.assertEqual(
                obj["attributes"]["title"],
                expected[obj["id"]],
            )

    def test_timestamp_field(self):
        for obj in self.objects:
            self.assertEqual(
                obj["attributes"]["timeFieldName"], "@timestamp"
            )

    def test_name_attribute(self):
        for obj in self.objects:
            self.assertEqual(obj["attributes"]["name"], obj["id"])


# ---------------------------------------------------------------------------
# 8e-5: Dashboard files reference valid index-pattern IDs
# ---------------------------------------------------------------------------

class TestDashboardIndexPatternRefs(unittest.TestCase):
    DASHBOARD_FILES = EXPECTED_FILES - {"index_patterns.ndjson"}

    def _get_index_ref_ids(self, objects):
        """Collect all index-pattern reference IDs from an object list."""
        ids = set()
        for obj in objects:
            for ref in obj.get("references", []):
                if ref.get("type") == "index-pattern":
                    ids.add(ref["id"])
        return ids

    def test_all_refs_point_to_known_index_patterns(self):
        for filename in sorted(self.DASHBOARD_FILES):
            with self.subTest(file=filename):
                objects = _load_ndjson(filename)
                refs = self._get_index_ref_ids(objects)
                for ref_id in refs:
                    self.assertIn(
                        ref_id,
                        VALID_INDEX_PATTERN_IDS,
                        f"{filename} references unknown index-pattern id '{ref_id}'",
                    )

    def test_each_dashboard_file_has_at_least_one_dashboard_object(self):
        for filename in sorted(self.DASHBOARD_FILES):
            with self.subTest(file=filename):
                objects = _load_ndjson(filename)
                dashboard_objs = [o for o in objects if o["type"] == "dashboard"]
                self.assertGreaterEqual(
                    len(dashboard_objs),
                    1,
                    f"{filename} contains no dashboard object",
                )

    def test_each_dashboard_file_has_at_least_one_visualization(self):
        for filename in sorted(self.DASHBOARD_FILES):
            with self.subTest(file=filename):
                objects = _load_ndjson(filename)
                viz_objs = [o for o in objects if o["type"] == "visualization"]
                self.assertGreaterEqual(
                    len(viz_objs),
                    1,
                    f"{filename} contains no visualization objects",
                )

    def test_dashboard_title_not_empty(self):
        for filename in sorted(self.DASHBOARD_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                if obj["type"] == "dashboard":
                    with self.subTest(file=filename, id=obj["id"]):
                        title = obj["attributes"].get("title", "")
                        self.assertGreater(
                            len(title), 0, "Dashboard title is empty"
                        )

    def test_visualization_visstate_is_valid_json_string(self):
        """visState is a string-of-JSON — verify it parses."""
        for filename in sorted(self.DASHBOARD_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                if obj["type"] == "visualization":
                    with self.subTest(file=filename, id=obj["id"]):
                        vis_state_str = obj["attributes"].get("visState", "{}")
                        self.assertIsInstance(vis_state_str, str)
                        try:
                            parsed = json.loads(vis_state_str)
                        except json.JSONDecodeError as e:
                            self.fail(
                                f"visState for {obj['id']} is not valid JSON: {e}"
                            )
                        self.assertIn(
                            "type",
                            parsed,
                            f"visState for {obj['id']} missing 'type' key",
                        )

    def test_visualization_search_source_is_valid_json_string(self):
        """kibanaSavedObjectMeta.searchSourceJSON must be valid JSON string."""
        for filename in sorted(self.DASHBOARD_FILES):
            objects = _load_ndjson(filename)
            for obj in objects:
                if obj["type"] == "visualization":
                    with self.subTest(file=filename, id=obj["id"]):
                        meta = obj["attributes"].get(
                            "kibanaSavedObjectMeta", {}
                        )
                        ssj = meta.get("searchSourceJSON", "{}")
                        self.assertIsInstance(ssj, str)
                        try:
                            json.loads(ssj)
                        except json.JSONDecodeError as e:
                            self.fail(
                                f"searchSourceJSON for {obj['id']} is not valid JSON: {e}"
                            )


# ---------------------------------------------------------------------------
# 8e-6: Dashboard panel counts match expected spec
# ---------------------------------------------------------------------------

class TestDashboardPanelCounts(unittest.TestCase):
    # (dashboard file, expected viz count, expected total objects including dashboard)
    EXPECTED = [
        ("sparrow_wifi_situational_awareness.ndjson", 5, 6),
        ("sparrow_wifi_pattern_of_life.ndjson", 5, 6),
        ("sparrow_wifi_new_device_detection.ndjson", 4, 5),
        ("sparrow_wifi_spectrum_planning.ndjson", 5, 6),
    ]

    def test_panel_counts(self):
        for filename, expected_viz_count, expected_total in self.EXPECTED:
            with self.subTest(file=filename):
                objects = _load_ndjson(filename)
                viz_count = sum(1 for o in objects if o["type"] == "visualization")
                total = len(objects)
                self.assertEqual(
                    viz_count,
                    expected_viz_count,
                    f"{filename}: expected {expected_viz_count} viz objects, got {viz_count}",
                )
                self.assertEqual(
                    total,
                    expected_total,
                    f"{filename}: expected {expected_total} total objects, got {total}",
                )


# ---------------------------------------------------------------------------
# 8e-7: install_dashboards.py — argparse and mocked POST
# ---------------------------------------------------------------------------

class TestInstallerArgparse(unittest.TestCase):
    def setUp(self):
        self.mod = _load_installer()

    def _parse(self, args):
        """Parse args via the module's argparser without calling main()."""
        import argparse
        # Re-create parser inline to avoid sys.exit on bad args
        parser = argparse.ArgumentParser()
        parser.add_argument("--kibana-url", required=True)
        parser.add_argument("--username", default=None)
        parser.add_argument("--password", default=None)
        parser.add_argument("--api-key", default=None)
        parser.add_argument("--overwrite", action="store_true", default=False)
        parser.add_argument("--file", default=None)
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--verify-tls", dest="verify_tls",
                           action="store_true", default=True)
        group.add_argument("--no-verify-tls", dest="verify_tls",
                           action="store_false")
        return parser.parse_args(args)

    def test_basic_args_parse(self):
        args = self._parse([
            "--kibana-url", "http://kibana.test",
            "--username", "elastic",
            "--password", "elastic",
        ])
        self.assertEqual(args.kibana_url, "http://kibana.test")
        self.assertEqual(args.username, "elastic")
        self.assertFalse(args.overwrite)
        self.assertTrue(args.verify_tls)

    def test_overwrite_flag(self):
        args = self._parse(["--kibana-url", "http://k", "--overwrite"])
        self.assertTrue(args.overwrite)

    def test_no_verify_tls(self):
        args = self._parse(["--kibana-url", "http://k", "--no-verify-tls"])
        self.assertFalse(args.verify_tls)

    def test_api_key_arg(self):
        args = self._parse(["--kibana-url", "http://k", "--api-key", "abc123=="])
        self.assertEqual(args.api_key, "abc123==")

    def test_single_file_arg(self):
        args = self._parse([
            "--kibana-url", "http://k",
            "--file", "sparrow_elastic/dashboards/index_patterns.ndjson",
        ])
        self.assertEqual(args.file,
                         "sparrow_elastic/dashboards/index_patterns.ndjson")


class TestInstallerAuthHeader(unittest.TestCase):
    def setUp(self):
        self.mod = _load_installer()

    def test_basic_auth(self):
        hdr = self.mod._build_auth_header("elastic", "elastic", None)
        self.assertTrue(hdr.startswith("Basic "))

    def test_api_key(self):
        hdr = self.mod._build_auth_header(None, None, "mykey")
        self.assertEqual(hdr, "ApiKey mykey")

    def test_no_auth(self):
        hdr = self.mod._build_auth_header(None, None, None)
        self.assertIsNone(hdr)

    def test_api_key_takes_precedence_over_basic(self):
        hdr = self.mod._build_auth_header("user", "pass", "mykey")
        self.assertTrue(hdr.startswith("ApiKey "))


class TestInstallerMultipartEncoder(unittest.TestCase):
    def setUp(self):
        self.mod = _load_installer()

    def test_multipart_contains_file_content(self):
        payload = b"test ndjson line\n"
        body, ct = self.mod._encode_multipart(payload, filename="test.ndjson")
        self.assertIn(b"test ndjson line", body)
        self.assertIn(b"test.ndjson", body)
        self.assertIn("multipart/form-data", ct)

    def test_multipart_has_kbn_field_name(self):
        body, _ = self.mod._encode_multipart(b"data", filename="f.ndjson")
        self.assertIn(b'name="file"', body)


class TestInstallerMockedPost(unittest.TestCase):
    """Verify install_file() POSTs to the correct endpoint."""

    def setUp(self):
        self.mod = _load_installer()
        self.test_file = os.path.join(
            DASHBOARDS_DIR, "index_patterns.ndjson"
        )

    def _mock_response(self, payload_dict, status=200):
        response_bytes = json.dumps(payload_dict).encode("utf-8")
        mock_resp = unittest.mock.MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        return mock_resp

    def test_posts_to_correct_endpoint(self):
        mock_resp = self._mock_response(
            {"success": True, "successCount": 2, "errors": []}
        )
        captured_url = []

        def fake_open(req, timeout=None):
            captured_url.append(req.full_url)
            return mock_resp

        opener_mock = unittest.mock.MagicMock()
        opener_mock.open.side_effect = fake_open

        with unittest.mock.patch("urllib.request.build_opener",
                                 return_value=opener_mock):
            result = self.mod.import_file(
                "http://kibana.test",
                self.test_file,
                overwrite=True,
            )

        self.assertEqual(len(captured_url), 1)
        self.assertIn("/api/saved_objects/_import", captured_url[0])
        self.assertIn("overwrite=true", captured_url[0])

    def test_success_result_parsed(self):
        mock_resp = self._mock_response(
            {"success": True, "successCount": 2, "errors": []}
        )
        opener_mock = unittest.mock.MagicMock()
        opener_mock.open.return_value = mock_resp

        with unittest.mock.patch("urllib.request.build_opener",
                                 return_value=opener_mock):
            result = self.mod.import_file(
                "http://kibana.test",
                self.test_file,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["error_count"], 0)
        self.assertIsNone(result["http_error"])

    def test_error_result_parsed(self):
        mock_resp = self._mock_response({
            "success": False,
            "successCount": 0,
            "errors": [
                {"id": "bad-id", "type": "visualization",
                 "error": {"message": "Conflict"}}
            ],
        })
        opener_mock = unittest.mock.MagicMock()
        opener_mock.open.return_value = mock_resp

        with unittest.mock.patch("urllib.request.build_opener",
                                 return_value=opener_mock):
            result = self.mod.import_file(
                "http://kibana.test",
                self.test_file,
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_count"], 1)

    def test_http_error_handled_gracefully(self):
        import urllib.error
        opener_mock = unittest.mock.MagicMock()
        from email.message import Message
        opener_mock.open.side_effect = urllib.error.HTTPError(
            url="http://kibana.test",
            code=401,
            msg="Unauthorized",
            hdrs=Message(),
            fp=io.BytesIO(b"not authorised"),
        )

        with unittest.mock.patch("urllib.request.build_opener",
                                 return_value=opener_mock):
            result = self.mod.import_file(
                "http://kibana.test",
                self.test_file,
            )

        self.assertFalse(result["success"])
        self.assertIsNotNone(result["http_error"])
        self.assertIn("401", result["http_error"])

    def test_kbn_xsrf_header_sent(self):
        mock_resp = self._mock_response(
            {"success": True, "successCount": 1, "errors": []}
        )
        captured_headers = []

        def fake_open(req, timeout=None):
            captured_headers.append(dict(req.headers))
            return mock_resp

        opener_mock = unittest.mock.MagicMock()
        opener_mock.open.side_effect = fake_open

        with unittest.mock.patch("urllib.request.build_opener",
                                 return_value=opener_mock):
            self.mod.import_file("http://kibana.test", self.test_file)

        self.assertTrue(len(captured_headers) > 0)
        # Header names are title-cased by urllib
        headers_lower = {k.lower(): v for k, v in captured_headers[0].items()}
        self.assertIn("kbn-xsrf", headers_lower)
        self.assertEqual(headers_lower["kbn-xsrf"], "true")


class TestInstallerFileDiscovery(unittest.TestCase):
    def setUp(self):
        self.mod = _load_installer()

    def test_discovers_all_ndjson_files(self):
        files = self.mod._ndjson_files_in_dir(DASHBOARDS_DIR)
        filenames = {os.path.basename(f) for f in files}
        self.assertEqual(filenames, EXPECTED_FILES)

    def test_index_patterns_first(self):
        files = self.mod._ndjson_files_in_dir(DASHBOARDS_DIR)
        self.assertEqual(os.path.basename(files[0]), "index_patterns.ndjson")

    def test_all_files_exist(self):
        files = self.mod._ndjson_files_in_dir(DASHBOARDS_DIR)
        for f in files:
            self.assertTrue(os.path.isfile(f), f"File not found: {f}")


# ---------------------------------------------------------------------------
# 9b: TestLegacyPreserved — legacy_preserved.ndjson
# ---------------------------------------------------------------------------

LEGACY_FILE = "legacy_preserved.ndjson"

EXPECTED_LEGACY_VIZ_IDS = {
    "legacy_sparrow_unique_wifi_networks",
    "legacy_sparrow_unique_aps",
    "legacy_sparrow_wifi_ssid_list",
    "legacy_sparrow_unique_bt_devices",
    "legacy_sparrow_bt_time_series",
    "legacy_sparrow_bt_top_named",
}

LEGACY_BANNED_FIELDS = ["wifi.mac_addr", "wifi.signal_strength", "agent_name"]
LEGACY_REQUIRED_NEW_FIELDS = ["source.mac", "wifi.channel.primary"]


class TestLegacyPreserved(unittest.TestCase):
    """Step 9: Tests for legacy_preserved.ndjson field-renamed viz clones."""

    def setUp(self):
        self.objects = _load_ndjson(LEGACY_FILE)
        self.raw_text = open(
            os.path.join(DASHBOARDS_DIR, LEGACY_FILE), encoding="utf-8"
        ).read()

    # ---- 9b-1: File exists and contains enough objects ----

    def test_file_exists(self):
        self.assertTrue(
            os.path.isfile(os.path.join(DASHBOARDS_DIR, LEGACY_FILE)),
            f"{LEGACY_FILE} missing from dashboards dir",
        )

    def test_at_least_six_objects(self):
        self.assertGreaterEqual(
            len(self.objects), 6,
            f"{LEGACY_FILE} must contain at least 6 objects, found {len(self.objects)}",
        )

    # ---- 9b-2: All viz IDs are present ----

    def test_all_required_viz_ids_present(self):
        found_ids = {obj["id"] for obj in self.objects if obj.get("type") == "visualization"}
        for expected_id in EXPECTED_LEGACY_VIZ_IDS:
            with self.subTest(viz_id=expected_id):
                self.assertIn(
                    expected_id,
                    found_ids,
                    f"Expected viz id '{expected_id}' not found in {LEGACY_FILE}",
                )

    # ---- 9b-3: References point to valid index patterns ----

    def test_all_index_pattern_refs_valid(self):
        for obj in self.objects:
            for ref in obj.get("references", []):
                if ref.get("type") == "index-pattern":
                    with self.subTest(obj_id=obj["id"], ref_id=ref["id"]):
                        self.assertIn(
                            ref["id"],
                            VALID_INDEX_PATTERN_IDS,
                            f"Object '{obj['id']}' references unknown index-pattern '{ref['id']}'",
                        )

    def test_wifi_viz_reference_sparrow_wifi(self):
        wifi_viz_ids = {
            "legacy_sparrow_unique_wifi_networks",
            "legacy_sparrow_unique_aps",
            "legacy_sparrow_wifi_ssid_list",
        }
        for obj in self.objects:
            if obj.get("id") in wifi_viz_ids:
                refs = {r["id"] for r in obj.get("references", [])
                        if r.get("type") == "index-pattern"}
                with self.subTest(viz_id=obj["id"]):
                    self.assertIn(
                        "sparrow-wifi",
                        refs,
                        f"WiFi viz '{obj['id']}' must reference sparrow-wifi index pattern",
                    )

    def test_bt_viz_reference_sparrow_bt(self):
        bt_viz_ids = {
            "legacy_sparrow_unique_bt_devices",
            "legacy_sparrow_bt_time_series",
            "legacy_sparrow_bt_top_named",
        }
        for obj in self.objects:
            if obj.get("id") in bt_viz_ids:
                refs = {r["id"] for r in obj.get("references", [])
                        if r.get("type") == "index-pattern"}
                with self.subTest(viz_id=obj["id"]):
                    self.assertIn(
                        "sparrow-bt",
                        refs,
                        f"BT viz '{obj['id']}' must reference sparrow-bt index pattern",
                    )

    # ---- 9b-4: No legacy (banned) field names in the file ----

    def test_no_banned_legacy_fields(self):
        for field in LEGACY_BANNED_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(
                    field,
                    self.raw_text,
                    f"Banned legacy field '{field}' found in {LEGACY_FILE}",
                )

    # ---- 9b-5: Required new field names ARE present ----

    def test_required_new_fields_present(self):
        for field in LEGACY_REQUIRED_NEW_FIELDS:
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    self.raw_text,
                    f"Expected new field '{field}' not found in {LEGACY_FILE}",
                )

    # ---- 9b-6: Optional compat dashboard references all 6 viz IDs ----

    def test_compat_dashboard_references_all_six_viz(self):
        """If a compat dashboard is present, it must ref all 6 legacy viz."""
        dashboard_objs = [o for o in self.objects if o.get("type") == "dashboard"]
        if not dashboard_objs:
            self.skipTest("No dashboard object in legacy_preserved.ndjson")
        db = dashboard_objs[0]
        ref_ids = {r["id"] for r in db.get("references", [])
                   if r.get("type") == "visualization"}
        for vid in EXPECTED_LEGACY_VIZ_IDS:
            with self.subTest(viz_id=vid):
                self.assertIn(
                    vid,
                    ref_ids,
                    f"Compat dashboard does not reference viz '{vid}'",
                )

    # ---- 9b-7: All viz IDs carry the [Legacy] title prefix ----

    def test_legacy_title_prefix_on_all_viz(self):
        for obj in self.objects:
            if obj.get("type") == "visualization" and obj.get("id", "").startswith("legacy_"):
                title = obj["attributes"].get("title", "")
                with self.subTest(viz_id=obj["id"]):
                    self.assertTrue(
                        title.startswith("[Legacy]"),
                        f"Viz '{obj['id']}' title does not start with '[Legacy]': {title!r}",
                    )

    # ---- 9b-8: visState inner JSON is valid and has 'type' key ----

    def test_legacy_viz_vis_state_valid(self):
        for obj in self.objects:
            if obj.get("type") == "visualization":
                with self.subTest(viz_id=obj["id"]):
                    vis_str = obj["attributes"].get("visState", "{}")
                    try:
                        parsed = json.loads(vis_str)
                    except json.JSONDecodeError as e:
                        self.fail(f"visState for '{obj['id']}' is not valid JSON: {e}")
                    self.assertIn("type", parsed,
                                  f"visState for '{obj['id']}' missing 'type' key")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
