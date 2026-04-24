"""Tests for sparrow_elastic.device_classifier (placeholder).

Step 5 will replace the placeholder with a rule-table-driven implementation
and add the real classification tests here.
"""

import pytest
from sparrow_elastic.device_classifier import classify


class TestClassifyPlaceholder:
    """All invocations must return ("unknown", 0.0, []) until Step 5."""

    def test_empty_evidence(self):
        assert classify({}) == ("unknown", 0.0, [])

    def test_with_oui_vendor(self):
        assert classify({"oui_vendor": "Apple"}) == ("unknown", 0.0, [])

    def test_with_bt_cod(self):
        assert classify({"bt_cod": 0x200404}) == ("unknown", 0.0, [])

    def test_with_wifi_ssid(self):
        assert classify({"wifi_ssid": "DJI-Phantom-123"}) == ("unknown", 0.0, [])

    def test_with_multiple_fields(self):
        result = classify({
            "oui_vendor": "DJI",
            "bt_name": "DJI-RC",
            "bt_company": "DJI Technology",
            "wifi_ssid": "DJI-RC",
        })
        assert result == ("unknown", 0.0, [])

    def test_return_types(self):
        class_guess, confidence, evidence = classify({})
        assert isinstance(class_guess, str)
        assert isinstance(confidence, float)
        assert isinstance(evidence, list)

    def test_evidence_list_is_empty(self):
        _, _, evidence = classify({"oui_vendor": "Parrot"})
        assert evidence == []

    def test_confidence_is_zero(self):
        _, confidence, _ = classify({"oui_vendor": "Autel"})
        assert confidence == 0.0
