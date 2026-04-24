"""
Tests for sparrow_elastic.device_classifier — rule-table-driven device
classification.

Covers: empty evidence, single-rule fires, multi-rule reinforcement,
conflict resolution, case insensitivity, missing/None evidence keys,
rules-file resilience, and public API contracts.
"""

import sys
import os
import unittest
from unittest.mock import patch

# Add project root to path so that sparrow_elastic is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sparrow_elastic.device_classifier as dc


def _fresh_classify(evidence):
    """Call classify() after forcing a fresh rule load.

    Ensures each test group starts with a clean state, unaffected by
    any monkeypatching done in earlier tests.
    """
    dc.reload_rules()
    return dc.classify(evidence)


class TestEmptyEvidence(unittest.TestCase):

    def test_empty_dict_returns_unknown(self):
        cls, conf, tags = _fresh_classify({})
        self.assertEqual(cls, "unknown")
        self.assertEqual(conf, 0.0)
        self.assertEqual(tags, [])

    def test_all_none_values_returns_unknown(self):
        evidence = {
            "oui_vendor": None,
            "bt_cod": None,
            "bt_appearance": None,
            "bt_name": None,
            "bt_company": None,
            "wifi_ssid": None,
            "wifi_vendor_ies": None,
            "service_uuids": None,
            "apple_continuity_type": None,
        }
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "unknown")

    def test_all_empty_string_values_returns_unknown(self):
        evidence = {k: "" for k in (
            "oui_vendor", "bt_name", "wifi_ssid", "apple_continuity_type"
        )}
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "unknown")


class TestSingleRuleFires(unittest.TestCase):

    def test_dji_oui_fires_drone_controller(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "DJI Technology Co., Ltd."})
        self.assertEqual(cls, "drone_controller")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("oui:DJI", tags)

    def test_airpods_name_fires_headset(self):
        cls, conf, tags = _fresh_classify({"bt_name": "Mike's AirPods Pro"})
        self.assertEqual(cls, "headset")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("name:airpods", tags)

    def test_bt_cod_phone_major_fires_phone(self):
        # CoD 0x5a020c: (0x5a020c >> 8) & 0x1F == 2 → phone major class
        cls, conf, tags = _fresh_classify({"bt_cod": 0x5a020c})
        self.assertEqual(cls, "phone")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("cod:phone", tags)

    def test_gap_appearance_watch_fires_wearable(self):
        # Appearance 0x00C0: 0x00C0 >> 6 == 3 → watch category
        cls, conf, tags = _fresh_classify({"bt_appearance": 0x00C0})
        self.assertEqual(cls, "wearable")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("appearance:watch", tags)

    def test_apple_continuity_airpods_fires_headset(self):
        cls, conf, tags = _fresh_classify({"apple_continuity_type": "airpods"})
        self.assertEqual(cls, "headset")
        self.assertGreaterEqual(conf, 0.95)
        self.assertIn("apple:airpods", tags)

    def test_autel_oui_fires_drone_controller(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Autel Robotics"})
        self.assertEqual(cls, "drone_controller")
        self.assertGreaterEqual(conf, 0.9)

    def test_fitbit_oui_fires_wearable(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Fitbit Inc."})
        self.assertEqual(cls, "wearable")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("oui:Fitbit", tags)

    def test_bose_oui_fires_headset(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Bose Corporation"})
        self.assertEqual(cls, "headset")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("oui:Bose", tags)

    def test_hp_print_ssid_fires_printer(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "HP-Print-4A-LaserJet"})
        self.assertEqual(cls, "printer")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("ssid:HP-Print", tags)

    def test_epson_ssid_fires_printer(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "EPSONXXX123"})
        self.assertEqual(cls, "printer")
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("ssid:EPSON", tags)


class TestMultiRuleReinforcement(unittest.TestCase):

    def test_apple_oui_plus_airpods_name_plus_continuity(self):
        """Multiple headset signals should combine to high confidence headset."""
        evidence = {
            "oui_vendor": "Apple, Inc.",
            "bt_name": "John's AirPods Pro",
            "apple_continuity_type": "airpods",
        }
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "headset")
        self.assertGreaterEqual(conf, 0.95)
        self.assertIn("name:airpods", tags)
        self.assertIn("apple:airpods", tags)

    def test_combined_confidence_higher_than_single(self):
        """Probabilistic OR: two rules firing → higher confidence than either alone."""
        single_conf = _fresh_classify({"bt_name": "My AirPods"})[1]
        combined_conf = _fresh_classify({
            "bt_name": "My AirPods",
            "apple_continuity_type": "airpods",
        })[1]
        self.assertGreater(combined_conf, single_conf)

    def test_apple_watch_name_and_appearance_reinforce(self):
        """Apple Watch name + watch appearance category → strong wearable."""
        evidence = {
            "bt_name": "Apple Watch Series 9",
            "bt_appearance": 0x00C0,  # category 3 = watch
        }
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "wearable")
        self.assertIn("name:apple_watch", tags)
        self.assertIn("appearance:watch", tags)


class TestConflictResolution(unittest.TestCase):

    def test_apple_oui_with_no_device_clues_returns_phone(self):
        """Apple OUI (phone 0.5) alone should win 'phone' with low confidence."""
        cls, conf, tags = _fresh_classify({"oui_vendor": "Apple, Inc."})
        self.assertEqual(cls, "phone")
        self.assertIn("oui:Apple", tags)

    def test_apple_oui_with_printer_bt_name_does_not_match_printer_rule(self):
        """The bt_name printer rule uses specific product patterns.

        'Printer' as a generic bt_name string does not match
        any printer rule in the seed table — the ssid and oui rules are
        product-specific (Canon, EPSON, HP-Print).  Apple OUI still wins.
        """
        evidence = {
            "oui_vendor": "Apple, Inc.",
            "bt_name": "Printer",
        }
        cls, conf, tags = _fresh_classify(evidence)
        # No printer rule fires for generic "Printer" bt_name.
        # Apple OUI rule fires → class is phone.
        self.assertEqual(cls, "phone")
        self.assertIn("oui:Apple", tags)

    def test_winning_class_has_highest_combined_confidence(self):
        """When two classes fire, the one with higher combined wins."""
        # apple_continuity "airpods" → headset 0.95
        # Apple OUI → phone 0.5
        # Expected: headset wins
        evidence = {
            "oui_vendor": "Apple, Inc.",
            "apple_continuity_type": "airpods",
        }
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "headset")


class TestCaseInsensitivity(unittest.TestCase):

    def test_lowercase_dji_matches(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "dji corp"})
        self.assertEqual(cls, "drone_controller")
        self.assertIn("oui:DJI", tags)

    def test_mixed_case_autel_matches(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "AUTEL ROBOTICS USA"})
        self.assertEqual(cls, "drone_controller")

    def test_lowercase_airpod_name_matches(self):
        cls, conf, tags = _fresh_classify({"bt_name": "my airpods"})
        self.assertEqual(cls, "headset")

    def test_uppercase_epson_ssid_matches(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "EPSONABCDEF"})
        self.assertEqual(cls, "printer")

    def test_lowercase_epson_ssid_matches(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "epsonabcdef"})
        self.assertEqual(cls, "printer")


class TestMissingAndNoneEvidence(unittest.TestCase):

    def test_absent_bt_keys_do_not_raise(self):
        """Only oui_vendor present — bt_* rules should skip without error."""
        cls, conf, tags = _fresh_classify({"oui_vendor": "Apple, Inc."})
        # Should return a valid result, not raise
        self.assertEqual(cls, "phone")

    def test_none_oui_with_airpods_name_fires_headset(self):
        """None oui_vendor: OUI rule skipped; AirPods name rule fires."""
        evidence = {"oui_vendor": None, "bt_name": "AirPods"}
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "headset")
        self.assertIn("name:airpods", tags)

    def test_none_oui_no_false_drone_match(self):
        """oui_vendor=None must not match the DJI OUI regex."""
        evidence = {"oui_vendor": None, "bt_name": "AirPods"}
        cls, conf, tags = _fresh_classify(evidence)
        self.assertNotEqual(cls, "drone_controller")

    def test_empty_list_service_uuids_skips_rule(self):
        """Empty list is treated as absent — in_list rules skip."""
        evidence = {
            "service_uuids": [],
            "oui_vendor": "Bose Corporation",
        }
        cls, conf, tags = _fresh_classify(evidence)
        self.assertEqual(cls, "headset")  # Bose OUI rule fires

    def test_no_key_error_for_arbitrary_absent_keys(self):
        """Evidence dict may omit any key — classifier never raises KeyError."""
        # Minimal evidence with only one key; all other rule keys absent
        try:
            cls, conf, tags = _fresh_classify({"wifi_ssid": "HP-Print-AB"})
        except KeyError:
            self.fail("classify() raised KeyError for absent evidence key")
        self.assertEqual(cls, "printer")


class TestRulesFileResilience(unittest.TestCase):

    def test_missing_rules_file_returns_unknown(self):
        """If the rules file is absent, classify() returns unknown, no exception."""
        import sparrow_elastic.device_classifier as classifier_module

        # Monkeypatch the rules file path to a non-existent file
        original_path = classifier_module._RULES_FILE
        try:
            classifier_module._RULES_FILE = "/nonexistent/path/rules.json"
            classifier_module._RULES_LOADED = False
            classifier_module._COMPILED_RULES = []

            result = classifier_module.classify({})
            self.assertEqual(result, ("unknown", 0.0, []))
        finally:
            # Restore and reload
            classifier_module._RULES_FILE = original_path
            classifier_module._RULES_LOADED = False
            classifier_module._COMPILED_RULES = []
            classifier_module.reload_rules()

    def test_invalid_json_returns_unknown(self):
        """If the rules file contains invalid JSON, classify() returns unknown."""
        import tempfile
        import sparrow_elastic.device_classifier as classifier_module

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("{not valid json!!!}")
            tmp_path = fh.name

        original_path = classifier_module._RULES_FILE
        try:
            classifier_module._RULES_FILE = tmp_path
            classifier_module._RULES_LOADED = False
            classifier_module._COMPILED_RULES = []

            result = classifier_module.classify({})
            self.assertEqual(result, ("unknown", 0.0, []))
        finally:
            classifier_module._RULES_FILE = original_path
            classifier_module._RULES_LOADED = False
            classifier_module._COMPILED_RULES = []
            classifier_module.reload_rules()
            os.unlink(tmp_path)

    def test_classify_does_not_raise_when_rules_empty(self):
        """Empty rule list → unknown, not an exception."""
        import sparrow_elastic.device_classifier as classifier_module

        original_rules = classifier_module._COMPILED_RULES[:]
        original_loaded = classifier_module._RULES_LOADED
        try:
            classifier_module._COMPILED_RULES = []
            classifier_module._RULES_LOADED = True

            result = classifier_module.classify({"oui_vendor": "DJI"})
            self.assertEqual(result, ("unknown", 0.0, []))
        finally:
            classifier_module._COMPILED_RULES = original_rules
            classifier_module._RULES_LOADED = original_loaded


class TestPublicApiContracts(unittest.TestCase):

    def test_reload_rules_returns_count(self):
        """reload_rules() must return a positive integer count."""
        count = dc.reload_rules()
        self.assertIsInstance(count, int)
        self.assertGreater(count, 0)

    def test_reload_rules_count_matches_file(self):
        """reload_rules() count should match the number of rules in the JSON file."""
        import json
        with open(dc._RULES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        expected = len(data["rules"])
        actual = dc.reload_rules()
        self.assertEqual(actual, expected)

    def test_get_rule_count_matches_reload(self):
        """get_rule_count() should return the same number as reload_rules()."""
        reload_count = dc.reload_rules()
        self.assertEqual(dc.get_rule_count(), reload_count)

    def test_reload_rules_returns_roughly_expected_seed_count(self):
        """Seed file should have 55-70 rules."""
        count = dc.reload_rules()
        self.assertGreaterEqual(count, 55,
                                f"Expected >= 55 seed rules, got {count}")
        self.assertLessEqual(count, 75,
                             f"Expected <= 75 seed rules, got {count}")

    def test_classify_return_type(self):
        """classify() always returns (str, float, list)."""
        result = _fresh_classify({"oui_vendor": "DJI"})
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        cls, conf, tags = result
        self.assertIsInstance(cls, str)
        self.assertIsInstance(conf, float)
        self.assertIsInstance(tags, list)

    def test_confidence_in_range(self):
        """Confidence is always in [0.0, 1.0]."""
        for evidence in [
            {},
            {"oui_vendor": "DJI"},
            {"bt_name": "AirPods", "apple_continuity_type": "airpods"},
        ]:
            _, conf, _ = _fresh_classify(evidence)
            self.assertGreaterEqual(conf, 0.0)
            self.assertLessEqual(conf, 1.0)

    def test_evidence_tags_deduplicated(self):
        """No duplicate evidence tags in the returned list."""
        # Construct evidence that fires the same logical tag via multiple paths
        # (in practice the same rule fires once, but guard against duplicates)
        cls, conf, tags = _fresh_classify({"bt_name": "AirPods Max", "apple_continuity_type": "airpods"})
        self.assertEqual(len(tags), len(set(tags)), "Duplicate tags in result")


class TestCodAndAppearanceBitMath(unittest.TestCase):
    """Verify the bit-field extraction helpers are correct."""

    def test_cod_major_phone_extraction(self):
        """CoD 0x5a020c: major = 2 (phone)."""
        from sparrow_elastic.device_classifier import _match_cod_major
        self.assertTrue(_match_cod_major(0x5a020c, 2))
        self.assertFalse(_match_cod_major(0x5a020c, 1))

    def test_cod_major_computer_extraction(self):
        """CoD 0x100100: major = 1 (computer/laptop)."""
        from sparrow_elastic.device_classifier import _match_cod_major
        # 0x100100 >> 8 = 0x1001 & 0x1F = 0x01 = 1
        self.assertTrue(_match_cod_major(0x100100, 1))

    def test_appearance_category_watch(self):
        """Appearance 0x00C0: category = 3 (watch)."""
        from sparrow_elastic.device_classifier import _match_appearance_category
        self.assertTrue(_match_appearance_category(0x00C0, 3))
        self.assertFalse(_match_appearance_category(0x00C0, 1))

    def test_appearance_category_phone(self):
        """Appearance 0x0040: category = 1 (phone)."""
        from sparrow_elastic.device_classifier import _match_appearance_category
        # 0x0040 >> 6 = 1
        self.assertTrue(_match_appearance_category(0x0040, 1))

    def test_cod_non_int_returns_false(self):
        from sparrow_elastic.device_classifier import _match_cod_major
        self.assertFalse(_match_cod_major("0x5a020c", 2))
        self.assertFalse(_match_cod_major(None, 2))

    def test_appearance_non_int_returns_false(self):
        from sparrow_elastic.device_classifier import _match_appearance_category
        self.assertFalse(_match_appearance_category("0x00C0", 3))
        self.assertFalse(_match_appearance_category(None, 3))


class TestSpecificRuleCategories(unittest.TestCase):
    """Spot-check representative rules from each category in the seed file."""

    def test_skydio_oui_drone_controller(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Skydio Inc."})
        self.assertEqual(cls, "drone_controller")

    def test_parrot_oui_drone_controller(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Parrot SA"})
        self.assertEqual(cls, "drone_controller")

    def test_apple_watch_continuity_wearable(self):
        cls, conf, tags = _fresh_classify({"apple_continuity_type": "apple_watch"})
        self.assertEqual(cls, "wearable")
        self.assertIn("apple:watch", tags)

    def test_homepod_continuity_speaker(self):
        cls, conf, tags = _fresh_classify({"apple_continuity_type": "homepod"})
        self.assertEqual(cls, "speaker")
        self.assertIn("apple:homepod", tags)

    def test_tesla_oui_vehicle(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Tesla, Inc."})
        self.assertEqual(cls, "vehicle")
        self.assertIn("oui:Tesla", tags)

    def test_ring_oui_iot(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Ring LLC"})
        self.assertEqual(cls, "iot")
        self.assertIn("oui:Ring", tags)

    def test_garmin_oui_wearable(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Garmin International"})
        self.assertEqual(cls, "wearable")
        self.assertIn("oui:Garmin", tags)

    def test_galaxy_buds_bt_name_headset(self):
        cls, conf, tags = _fresh_classify({"bt_name": "Galaxy Buds+"})
        self.assertEqual(cls, "headset")
        self.assertIn("name:galaxy_buds", tags)

    def test_fitbit_bt_name_wearable(self):
        cls, conf, tags = _fresh_classify({"bt_name": "Fitbit Charge 5"})
        self.assertEqual(cls, "wearable")
        self.assertIn("name:fitbit", tags)

    def test_tesla_ssid_vehicle(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "Tesla Model 3 WiFi"})
        self.assertEqual(cls, "vehicle")
        self.assertIn("ssid:tesla", tags)

    def test_canon_ssid_printer(self):
        cls, conf, tags = _fresh_classify({"wifi_ssid": "Canon_XYZ123"})
        self.assertEqual(cls, "printer")
        self.assertIn("ssid:Canon", tags)

    def test_ubiquiti_oui_ap(self):
        cls, conf, tags = _fresh_classify({"oui_vendor": "Ubiquiti Inc."})
        self.assertEqual(cls, "ap")
        self.assertIn("oui:Ubiquiti", tags)

    def test_cod_health_wearable(self):
        """BT CoD major=9 (health) → wearable."""
        # 0x090000 = (9 << 16); but major = (cod >> 8) & 0x1F
        # Need cod where (cod >> 8) & 0x1F == 9
        # 0x000900 >> 8 = 0x09; 0x09 & 0x1F = 9
        cls, conf, tags = _fresh_classify({"bt_cod": 0x000900})
        self.assertEqual(cls, "wearable")
        self.assertIn("cod:health", tags)

    def test_appearance_heart_rate(self):
        """GAP Appearance category 13 → heart rate → wearable."""
        # category 13 = 13 << 6 = 0x0340
        cls, conf, tags = _fresh_classify({"bt_appearance": 0x0340})
        self.assertEqual(cls, "wearable")
        self.assertIn("appearance:heart_rate", tags)


if __name__ == "__main__":
    unittest.main()
