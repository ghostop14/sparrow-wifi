"""Tests for sparrow_elastic.templates — template loading and OpenSearch inlining."""

import pytest
from sparrow_elastic.templates import load_template, load_component, resolve_template


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------

class TestLoadTemplate:
    def test_wifi_template_returns_dict(self):
        t = load_template("sparrow-wifi-template")
        assert isinstance(t, dict)

    def test_wifi_template_has_index_patterns(self):
        t = load_template("sparrow-wifi-template")
        assert "index_patterns" in t
        assert t["index_patterns"] == ["sparrow-wifi-*"]

    def test_wifi_template_has_composed_of(self):
        t = load_template("sparrow-wifi-template")
        assert "composed_of" in t
        assert isinstance(t["composed_of"], list)
        assert len(t["composed_of"]) > 0

    def test_wifi_template_has_priority(self):
        t = load_template("sparrow-wifi-template")
        assert t.get("priority") == 500

    def test_bt_template_returns_dict(self):
        t = load_template("sparrow-bt-template")
        assert isinstance(t, dict)

    def test_bt_template_has_index_patterns(self):
        t = load_template("sparrow-bt-template")
        assert t["index_patterns"] == ["sparrow-bt-*"]

    def test_bt_template_no_data_stream_key(self):
        """data_stream key must be absent (not false) — rollover alias mode."""
        t = load_template("sparrow-wifi-template")
        assert "data_stream" not in t

    def test_missing_template_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_template("does-not-exist-template")


# ---------------------------------------------------------------------------
# load_component
# ---------------------------------------------------------------------------

class TestLoadComponent:
    def test_wifi_settings_component(self):
        c = load_component("sparrow-wifi", "settings")
        assert "template" in c
        assert "settings" in c["template"]

    def test_wifi_mappings_component(self):
        c = load_component("sparrow-wifi", "mappings")
        assert "template" in c
        assert "mappings" in c["template"]

    def test_bt_settings_component(self):
        c = load_component("sparrow-bt", "settings")
        settings = c["template"]["settings"]
        assert settings["index"]["lifecycle"]["name"] == "sparrow-bt-ilm"

    def test_missing_component_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_component("sparrow-wifi", "nonexistent")


# ---------------------------------------------------------------------------
# resolve_template — ES path (composed_of intact)
# ---------------------------------------------------------------------------

class TestResolveTemplateES:
    def test_returns_dict(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=False)
        assert isinstance(result, dict)

    def test_composed_of_present_when_not_os(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=False)
        assert "composed_of" in result

    def test_no_inline_template_block_injected(self):
        """ES path must NOT inject a 'template' block (server handles composition)."""
        t = load_template("sparrow-wifi-template")
        result = resolve_template("sparrow-wifi-template", for_opensearch=False)
        # The top-level keys should be the same as the raw file.
        assert set(result.keys()) == set(t.keys())

    def test_does_not_mutate_original(self):
        """resolve_template must return an independent copy."""
        r1 = resolve_template("sparrow-wifi-template", for_opensearch=False)
        r2 = resolve_template("sparrow-wifi-template", for_opensearch=False)
        r1["_injected"] = True
        assert "_injected" not in r2


# ---------------------------------------------------------------------------
# resolve_template — OpenSearch path (components inlined)
# ---------------------------------------------------------------------------

class TestResolveTemplateOpenSearch:
    def test_composed_of_absent_after_inline(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        assert "composed_of" not in result

    def test_template_settings_present_after_inline(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        assert "template" in result
        assert "settings" in result["template"]

    def test_template_mappings_present_after_inline(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        assert "mappings" in result["template"]

    def test_settings_content_correct(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        settings = result["template"]["settings"]
        assert settings["number_of_shards"] == 1
        assert settings["refresh_interval"] == "10s"
        assert settings["index"]["lifecycle"]["name"] == "sparrow-wifi-ilm"

    def test_mappings_properties_present(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        props = result["template"]["mappings"]["properties"]
        assert "@timestamp" in props
        assert "wifi" in props

    def test_bt_template_inlined_correctly(self):
        result = resolve_template("sparrow-bt-template", for_opensearch=True)
        assert "composed_of" not in result
        settings = result["template"]["settings"]
        assert settings["index"]["lifecycle"]["name"] == "sparrow-bt-ilm"
        props = result["template"]["mappings"]["properties"]
        assert "bluetooth" in props

    def test_deep_merge_preserves_both_subtrees(self):
        """After inlining, both settings and mappings must coexist under 'template'."""
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        tmpl = result["template"]
        assert "settings" in tmpl and "mappings" in tmpl

    def test_index_patterns_preserved_after_inline(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        assert result["index_patterns"] == ["sparrow-wifi-*"]

    def test_priority_preserved_after_inline(self):
        result = resolve_template("sparrow-wifi-template", for_opensearch=True)
        assert result["priority"] == 500


# ---------------------------------------------------------------------------
# Mapping field-type spot checks (WiFi)
# ---------------------------------------------------------------------------

class TestWifiMappingFields:
    """Verify representative ECS + wifi fields have the expected ES types."""

    @pytest.fixture(scope="class")
    def props(self):
        c = load_component("sparrow-wifi", "mappings")
        return c["template"]["mappings"]["properties"]

    def test_timestamp_is_date(self, props):
        assert props["@timestamp"]["type"] == "date"

    def test_observer_geo_location_is_geo_point(self, props):
        assert props["observer"]["properties"]["geo"]["properties"]["location"]["type"] == "geo_point"

    def test_signal_strength_dbm_is_float(self, props):
        assert props["signal"]["properties"]["strength_dbm"]["type"] == "float"

    def test_wifi_channel_occupied_set_is_integer(self, props):
        assert props["wifi"]["properties"]["channel"]["properties"]["occupied_set"]["type"] == "integer"

    def test_rf_signature_controller_candidate_is_boolean(self, props):
        assert props["rf"]["properties"]["signature"]["properties"]["controller_candidate"]["type"] == "boolean"

    def test_device_class_confidence_is_float(self, props):
        assert props["device"]["properties"]["class_confidence"]["type"] == "float"

    def test_observed_age_seconds_is_long(self, props):
        assert props["observed"]["properties"]["age_seconds"]["type"] == "long"

    def test_wifi_ssid_is_keyword(self, props):
        assert props["wifi"]["properties"]["ssid"]["type"] == "keyword"

    def test_wifi_ssid_hidden_is_boolean(self, props):
        assert props["wifi"]["properties"]["ssid_hidden"]["type"] == "boolean"

    def test_wifi_mac_vendor_is_keyword(self, props):
        assert props["wifi"]["properties"]["mac_vendor"]["type"] == "keyword"

    def test_wifi_strongest_signal_geo_location_is_geo_point(self, props):
        assert (
            props["wifi"]["properties"]["strongest_signal"]["properties"]["geo"]
            ["properties"]["location"]["type"] == "geo_point"
        )

    def test_wifi_capabilities_he_is_boolean(self, props):
        assert props["wifi"]["properties"]["capabilities"]["properties"]["he"]["type"] == "boolean"

    def test_event_ingested_is_date(self, props):
        assert props["event"]["properties"]["ingested"]["type"] == "date"

    def test_rf_frequency_mhz_is_long(self, props):
        assert props["rf"]["properties"]["frequency_mhz"]["type"] == "long"

    def test_signal_strength_quality_is_byte(self, props):
        assert props["signal"]["properties"]["strength_quality_0_5"]["type"] == "byte"


# ---------------------------------------------------------------------------
# Mapping field-type spot checks (Bluetooth)
# ---------------------------------------------------------------------------

class TestBtMappingFields:
    """Verify representative ECS + bluetooth fields have the expected ES types."""

    @pytest.fixture(scope="class")
    def props(self):
        c = load_component("sparrow-bt", "mappings")
        return c["template"]["mappings"]["properties"]

    def test_observer_geo_location_is_geo_point(self, props):
        assert props["observer"]["properties"]["geo"]["properties"]["location"]["type"] == "geo_point"

    def test_signal_strength_dbm_is_float(self, props):
        assert props["signal"]["properties"]["strength_dbm"]["type"] == "float"

    def test_rf_signature_controller_candidate_is_boolean(self, props):
        assert props["rf"]["properties"]["signature"]["properties"]["controller_candidate"]["type"] == "boolean"

    def test_device_class_confidence_is_float(self, props):
        assert props["device"]["properties"]["class_confidence"]["type"] == "float"

    def test_bluetooth_name_is_keyword(self, props):
        assert props["bluetooth"]["properties"]["name"]["type"] == "keyword"

    def test_bluetooth_geo_location_is_geo_point(self, props):
        assert props["bluetooth"]["properties"]["geo"]["properties"]["location"]["type"] == "geo_point"

    def test_bluetooth_advertising_tx_power_is_float(self, props):
        assert props["bluetooth"]["properties"]["advertising"]["properties"]["tx_power_dbm"]["type"] == "float"

    def test_bluetooth_beacon_major_is_integer(self, props):
        assert props["bluetooth"]["properties"]["beacon"]["properties"]["major"]["type"] == "integer"

    def test_bluetooth_mac_randomized_is_boolean(self, props):
        assert props["bluetooth"]["properties"]["mac"]["properties"]["randomized"]["type"] == "boolean"
