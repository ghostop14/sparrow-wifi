"""Tests for sparrow_elastic.document_builder.

Covers:
- Golden WiFi document structure
- Golden BT document structure
- Null / 0,0 observer GPS handling
- Missing frequency handling
- Bonded 80 MHz occupied-set
- Controller-candidate flag
- Deterministic _id
- Empty adv payload
- related.hash absent when no fingerprint inputs
- related.hash present + hash_strength when some inputs
- class_evidence always present as list
"""

import hashlib
import pytest
from datetime import datetime, timezone, timedelta

from sparrow_elastic.document_builder import (
    build_wifi_document,
    build_bt_document,
    compute_doc_id,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _utc(y, mo, d, h=12, mi=0, s=0, us=0):
    return datetime(y, mo, d, h, mi, s, us, tzinfo=timezone.utc)


_NOW = _utc(2024, 6, 15, 14, 30, 0)

_OBS_WITH_GPS = {
    "id": "sensor-alpha",
    "hostname": "sensor-alpha",
    "geo": {"lat": 37.7749, "lon": -122.4194, "alt": 10.0},
    "gps_status": "locked",
}

_OBS_NO_GPS = {
    "id": "sensor-alpha",
    "hostname": "sensor-alpha",
    "geo": None,
    "gps_status": "unlocked",
}

_OBS_ZERO_GPS = {
    "id": "sensor-alpha",
    "hostname": "sensor-alpha",
    "geo": {"lat": 0.0, "lon": 0.0, "alt": 0.0},
    "gps_status": "unlocked",
}


def _full_wifi_net():
    """A fully-populated WirelessNetwork.toJsondict() style dict."""
    return {
        "macAddr": "00:11:22:33:44:55",
        "ssid": "TestNetwork",
        "mode": "master",
        "security": "WPA2",
        "privacy": "CCMP",
        "cipher": "CCMP",
        "frequency": 5180,       # channel 36
        "channel": 36,
        "secondaryChannel": 0,
        "bandwidth": 20,
        "signal": -65,
        "stationcount": 3,
        "utilization": 0.25,
        "firstseen": "2024-06-15 14:00:00",
        "lastseen":  "2024-06-15 14:29:00",
        "strongestsignal": -60,
        "lat": "37.7750",
        "lon": "-122.4180",
        "alt": "12.0",
        "speed": "0.0",
        "gpsvalid": "True",
        "strongestlat": "37.7750",
        "strongestlon": "-122.4180",
        "strongestalt": "12.0",
        "strongestspeed": "0.0",
        "strongestgpsvalid": "True",
        "mac_vendor": "Intel Corp",
        "ht": True,
        "vht": True,
        "he": False,
        "eht": False,
    }


def _full_bt_dev():
    """A fully-populated BluetoothDevice.toJsondict() style dict."""
    return {
        "macAddr": "AA:BB:CC:DD:EE:FF",
        "name": "My Headphones",
        "company": "Sony Corp",
        "manufacturer": "Sony Corp",
        "bluetoothdescription": "Audio Headset",
        "bttype": 2,   # BT_LE
        "rssi": -70,
        "txpower": -60,
        "txpowervalid": "True",
        "ibeaconrange": 2.5,
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "firstseen": "2024-06-15 14:00:00",
        "lastseen":  "2024-06-15 14:29:00",
        "strongestrssi": -65,
        "lat": "37.7749",
        "lon": "-122.4194",
        "alt": "10.0",
        "speed": "0.0",
        "gpsvalid": "True",
        "strongestlat": "37.7749",
        "strongestlon": "-122.4194",
        "strongestalt": "10.0",
        "strongestspeed": "0.0",
        "strongestgpsvalid": "True",
    }


# ---------------------------------------------------------------------------
# Golden WiFi document test
# ---------------------------------------------------------------------------

class TestBuildWifiDocumentGolden:
    def test_required_ecs_fields_present(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert "@timestamp" in doc
        assert doc["ecs"]["version"] == "8.17.0"

    def test_event_fields(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        ev = doc["event"]
        assert ev["kind"] == "event"
        assert ev["category"] == ["network"]
        assert ev["type"] == ["info"]
        assert ev["module"] == "sparrow-wifi"
        assert ev["dataset"] == "sparrow.wifi"
        assert ev["action"] == "wifi-network-observed"
        assert "ingested" in ev

    def test_source_mac_canonicalized(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["source"]["mac"] == "00:11:22:33:44:55"

    def test_observer_id(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["observer"]["id"] == "sensor-alpha"

    def test_observer_geo_present_when_gps_locked(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert "geo" in doc["observer"]
        assert "location" in doc["observer"]["geo"]
        loc = doc["observer"]["geo"]["location"]
        assert "lat" in loc and "lon" in loc

    def test_signal_fields(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert "signal" in doc
        assert doc["signal"]["strength_dbm"] == -65.0
        assert "strength_mw" in doc["signal"]
        assert "strength_quality_0_5" in doc["signal"]

    def test_wifi_ssid(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["wifi"]["ssid"] == "TestNetwork"
        assert doc["wifi"]["ssid_hidden"] is False

    def test_wifi_channel(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        chan = doc["wifi"]["channel"]
        assert chan["primary"] == 36
        assert chan["width_mhz"] == 20

    def test_rf_band(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["rf"]["band"] == "5ghz"
        assert doc["rf"]["frequency_mhz"] == 5180

    def test_device_class_fields(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["device"]["class_guess"] == "unknown"
        assert doc["device"]["class_confidence"] == 0.0
        assert isinstance(doc["device"]["class_evidence"], list)

    def test_observed_temporal(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        obs = doc["observed"]
        assert 0 <= obs["hour_utc"] <= 23
        assert obs["day_of_week_utc"] in (
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        )
        assert "first_seen" in obs
        assert "last_seen" in obs
        assert "age_seconds" in obs
        assert obs["age_seconds"] >= 0

    def test_wifi_qbss(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["wifi"]["qbss"]["station_count"] == 3
        assert doc["wifi"]["qbss"]["channel_utilization"] == 0.25

    def test_wifi_capabilities(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        caps = doc["wifi"]["capabilities"]
        assert caps["ht"] is True
        assert caps["vht"] is True
        assert caps["he"] is False
        assert caps["eht"] is False

    def test_wifi_mac_vendor(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert doc["wifi"]["mac_vendor"] == "Intel Corp"

    def test_wifi_strongest_signal_geo(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert "strongest_signal" in doc["wifi"]
        assert "geo" in doc["wifi"]["strongest_signal"]

    def test_timestamp_uses_lastseen(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        # last_seen is 2024-06-15 14:29:00 UTC
        assert doc["@timestamp"].startswith("2024-06-15T14:29:00")


# ---------------------------------------------------------------------------
# Golden BT document test
# ---------------------------------------------------------------------------

class TestBuildBtDocumentGolden:
    def test_required_ecs_fields(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert "@timestamp" in doc
        assert doc["ecs"]["version"] == "8.17.0"

    def test_event_dataset(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["event"]["dataset"] == "sparrow.bluetooth"
        assert doc["event"]["action"] == "bluetooth-device-observed"

    def test_source_mac_canonicalized(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["source"]["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_bluetooth_name(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["bluetooth"]["name"] == "My Headphones"

    def test_bluetooth_type_ble(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["bluetooth"]["type"] == "ble"

    def test_bluetooth_uuid(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["bluetooth"]["uuid"] == "12345678-1234-1234-1234-123456789abc"

    def test_bluetooth_ibeacon_range(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["bluetooth"]["ibeacon_range_m"] == 2.5

    def test_signal_fields(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert "signal" in doc
        assert doc["signal"]["strength_dbm"] == -70.0

    def test_rf_band_is_2_4ghz(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["rf"]["band"] == "2_4ghz"

    def test_device_class_fields(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert doc["device"]["class_guess"] == "unknown"
        assert doc["device"]["class_confidence"] == 0.0
        assert isinstance(doc["device"]["class_evidence"], list)

    def test_bt_geo_when_gps_valid(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        assert "geo" in doc["bluetooth"]
        assert "location" in doc["bluetooth"]["geo"]


# ---------------------------------------------------------------------------
# class_evidence always list
# ---------------------------------------------------------------------------

class TestClassEvidenceAlwaysList:
    def test_wifi_class_evidence_is_list(self):
        doc = build_wifi_document({
            "macAddr": "00:11:22:33:44:55",
            "signal": -70,
        }, _OBS_NO_GPS, _NOW)
        assert isinstance(doc["device"]["class_evidence"], list)

    def test_bt_class_evidence_is_list(self):
        doc = build_bt_document({
            "macAddr": "AA:BB:CC:DD:EE:FF",
        }, _OBS_NO_GPS, _NOW)
        assert isinstance(doc["device"]["class_evidence"], list)


# ---------------------------------------------------------------------------
# Null GPS observer
# ---------------------------------------------------------------------------

class TestNullGpsObserver:
    def test_no_observer_geo_when_gps_none(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_NO_GPS, _NOW)
        assert "geo" not in doc["observer"]

    def test_gps_status_still_present(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_NO_GPS, _NOW)
        assert doc["observer"]["gps"]["status"] == "unlocked"


# ---------------------------------------------------------------------------
# 0,0 sentinel GPS
# ---------------------------------------------------------------------------

class TestZeroSentinelGps:
    def test_observer_geo_absent_for_0_0(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_ZERO_GPS, _NOW)
        assert "geo" not in doc["observer"]


# ---------------------------------------------------------------------------
# Missing frequency
# ---------------------------------------------------------------------------

class TestMissingFrequency:
    def test_rf_frequency_absent_when_no_frequency(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "NoFreq",
            "signal": -70,
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert "frequency_mhz" not in doc.get("rf", {})
        assert "band" not in doc.get("rf", {})
        # occupied_set should also be absent (no channel info)
        assert "channel_occupied_set" not in doc.get("rf", {})
        # wifi.channel may also be absent
        assert "occupied_set" not in doc.get("wifi", {}).get("channel", {})


# ---------------------------------------------------------------------------
# Bonded 80 MHz
# ---------------------------------------------------------------------------

class TestBonded80MHz:
    def test_occupied_set_for_ch44_80mhz(self):
        # frequency 5220 MHz = channel 44, part of [36,40,44,48] group
        net = dict(_full_wifi_net())
        net["frequency"] = 5220   # ch 44
        net["channel"] = 44
        net["bandwidth"] = 80
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["wifi"]["channel"]["occupied_set"] == [36, 40, 44, 48]
        assert doc["rf"]["channel_occupied_set"] == [36, 40, 44, 48]


# ---------------------------------------------------------------------------
# Controller candidate
# ---------------------------------------------------------------------------

class TestControllerCandidate:
    def test_dji_isb_strong_signal(self):
        net = {
            "macAddr": "00:26:7E:AA:BB:CC",  # DJI OUI prefix
            "ssid": "DJI_RC",
            "frequency": 5800,   # 5_8ghz_isb band
            "channel": 160,
            "bandwidth": 20,
            "signal": -50,
            "mac_vendor": "DJI Technology Co., Ltd.",
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["rf"]["signature"]["controller_candidate"] is True

    def test_non_controller_device(self):
        net = dict(_full_wifi_net())
        net["mac_vendor"] = "Linksys"
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["rf"]["signature"]["controller_candidate"] is False


# ---------------------------------------------------------------------------
# Deterministic _id
# ---------------------------------------------------------------------------

class TestComputeDocId:
    def test_same_inputs_same_id(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        id1 = compute_doc_id(doc)
        id2 = compute_doc_id(doc)
        assert id1 == id2

    def test_different_timestamp_different_id(self):
        now1 = _utc(2024, 6, 15, 14, 30, 0)
        now2 = _utc(2024, 6, 15, 14, 31, 0)  # 1 minute later
        net = _full_wifi_net()
        net["lastseen"] = "2024-06-15 14:30:00"
        doc1 = build_wifi_document(net, _OBS_WITH_GPS, now1)
        net2 = _full_wifi_net()
        net2["lastseen"] = "2024-06-15 14:31:00"
        doc2 = build_wifi_document(net2, _OBS_WITH_GPS, now2)
        assert compute_doc_id(doc1) != compute_doc_id(doc2)

    def test_id_is_32_chars(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        assert len(compute_doc_id(doc)) == 32

    def test_id_is_hex_string(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        doc_id = compute_doc_id(doc)
        int(doc_id, 16)  # should not raise


# ---------------------------------------------------------------------------
# BT empty adv payload
# ---------------------------------------------------------------------------

class TestBtEmptyAdvPayload:
    def test_no_advertising_key_in_bluetooth(self):
        dev = dict(_full_bt_dev())
        # adv_hex not set -- stub returns {}
        doc = build_bt_document(dev, _OBS_NO_GPS, _NOW)
        assert "advertising" not in doc.get("bluetooth", {})

    def test_no_beacon_key_in_bluetooth(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_NO_GPS, _NOW)
        assert "beacon" not in doc.get("bluetooth", {})


# ---------------------------------------------------------------------------
# related.hash absent when no fingerprint inputs
# ---------------------------------------------------------------------------

class TestRelatedHashAbsent:
    def test_no_hash_when_no_fingerprint_fields(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "Basic",
            "signal": -70,
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert "hash" not in doc.get("related", {})
        assert "hash_strength" not in doc.get("related", {})


# ---------------------------------------------------------------------------
# related.hash present when some fingerprint inputs
# ---------------------------------------------------------------------------

class TestRelatedHashPresent:
    def test_hash_present_with_vendor_ie_ouis(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "HashTest",
            "signal": -70,
            "vendor_ie_ouis": ["00:03:7F"],
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert "hash" in doc["related"]
        assert len(doc["related"]["hash"]) == 40  # SHA-1 hex = 40 chars

    def test_hash_strength_weak_with_one_input(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "HashTest",
            "vendor_ie_ouis": ["00:03:7F"],
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["related"]["hash_strength"] == "weak"

    def test_hash_strength_strong_with_all_four_inputs(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "HashTest",
            "vendor_ie_ouis": ["00:03:7F"],
            "wps_uuid": "some-uuid",
            "supported_rates": "6,9,12,18,24,36,48,54",
            "ht_capabilities": "HT20/HT40",
        }
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["related"]["hash_strength"] == "strong"

    def test_hash_is_deterministic(self):
        net = {
            "macAddr": "00:11:22:33:44:55",
            "ssid": "HashTest",
            "vendor_ie_ouis": ["00:03:7F"],
        }
        doc1 = build_wifi_document(dict(net), _OBS_NO_GPS, _NOW)
        doc2 = build_wifi_document(dict(net), _OBS_NO_GPS, _NOW)
        assert doc1["related"]["hash"] == doc2["related"]["hash"]


# ---------------------------------------------------------------------------
# Hidden SSID
# ---------------------------------------------------------------------------

class TestHiddenSsid:
    def test_empty_ssid_sets_ssid_hidden_true(self):
        net = {"macAddr": "00:11:22:33:44:55", "ssid": "", "signal": -70}
        doc = build_wifi_document(net, _OBS_NO_GPS, _NOW)
        assert doc["wifi"]["ssid_hidden"] is True
        assert "ssid" not in doc["wifi"]

    def test_nonempty_ssid_sets_ssid_hidden_false(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_NO_GPS, _NOW)
        assert doc["wifi"]["ssid_hidden"] is False
        assert doc["wifi"]["ssid"] == "TestNetwork"


# ---------------------------------------------------------------------------
# BT classic vs LE type
# ---------------------------------------------------------------------------

class TestBtType:
    def test_bttype_1_is_classic(self):
        dev = dict(_full_bt_dev())
        dev["bttype"] = 1
        doc = build_bt_document(dev, _OBS_NO_GPS, _NOW)
        assert doc["bluetooth"]["type"] == "classic"

    def test_bttype_2_is_ble(self):
        dev = dict(_full_bt_dev())
        dev["bttype"] = 2
        doc = build_bt_document(dev, _OBS_NO_GPS, _NOW)
        assert doc["bluetooth"]["type"] == "ble"


# ---------------------------------------------------------------------------
# Mapping compliance spot-check: no extra top-level keys
# ---------------------------------------------------------------------------

_ALLOWED_WIFI_TOPLEVEL = {
    "@timestamp", "ecs", "event", "observer", "source", "device",
    "related", "signal", "rf", "wifi", "observed", "threat", "host",
}

_ALLOWED_BT_TOPLEVEL = {
    "@timestamp", "ecs", "event", "observer", "source", "device",
    "related", "signal", "rf", "bluetooth", "observed", "threat", "host",
}


class TestMappingCompliance:
    def test_wifi_no_extra_toplevel_keys(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        extra = set(doc.keys()) - _ALLOWED_WIFI_TOPLEVEL
        assert not extra, f"Extra keys not in mapping: {extra}"

    def test_bt_no_extra_toplevel_keys(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        extra = set(doc.keys()) - _ALLOWED_BT_TOPLEVEL
        assert not extra, f"Extra keys not in mapping: {extra}"
