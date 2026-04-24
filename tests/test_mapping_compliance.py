"""
Recursive mapping-compliance tests for the sparrow-wifi document builders.

Ensures every leaf field emitted by build_wifi_document() and
build_bt_document() is declared in the corresponding index mapping,
so ES/OS with `dynamic: strict` will not reject documents at index time.

Helpers
-------
_flatten_paths(doc)    Walk a nested dict, yield dotted paths to leaves.
                       Lists are emitted at the parent path.
_mapping_paths(json)   Walk the template.mappings.properties tree and yield
                       valid field paths. geo_point fields expand to
                       <path>.lat and <path>.lon so that {lat, lon} objects
                       are recognised as valid.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from sparrow_elastic.document_builder import (
    build_wifi_document,
    build_bt_document,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _flatten_paths(d: dict, prefix: str = "") -> set:
    paths: set = set()
    for key, value in d.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.update(_flatten_paths(value, full))
        else:
            paths.add(full)
    return paths


def _walk_properties(props: dict, prefix: str = "") -> set:
    paths: set = set()
    for name, defn in props.items():
        full = f"{prefix}.{name}" if prefix else name
        if "properties" in defn:
            paths.update(_walk_properties(defn["properties"], full))
        else:
            paths.add(full)
            if defn.get("type") == "geo_point":
                paths.add(f"{full}.lat")
                paths.add(f"{full}.lon")
    return paths


def _mapping_paths(mapping_file: Path) -> set:
    with open(mapping_file, encoding="utf-8") as fh:
        data = json.load(fh)
    props = data["template"]["mappings"]["properties"]
    return _walk_properties(props)


_REPO = Path(__file__).resolve().parent.parent
_WIFI_MAPPING = _REPO / "sparrow_elastic" / "templates" / "sparrow-wifi-components" / "mappings.json"
_BT_MAPPING   = _REPO / "sparrow_elastic" / "templates" / "sparrow-bt-components" / "mappings.json"


# ---------------------------------------------------------------------------
# Fixtures — mirror tests/test_document_builder.py for consistency
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

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


def _full_wifi_net():
    return {
        "macAddr": "00:11:22:33:44:55",
        "ssid": "TestNetwork",
        "mode": "master",
        "security": "WPA2",
        "privacy": "CCMP",
        "cipher": "CCMP",
        "frequency": 5180,
        "channel": 36,
        "secondaryChannel": 40,
        "bandwidth": 80,
        "signal": -65,
        "stationcount": 3,
        "utilization": 0.25,
        "firstseen": "2024-06-15 14:00:00",
        "lastseen":  "2024-06-15 14:29:00",
        "strongestsignal": -60,
        "lat": "37.7750",
        "lon": "-122.4180",
        "alt": "12.0",
        "strongestlat": "37.7750",
        "strongestlon": "-122.4180",
        "strongestalt": "12.0",
        "strongestgpsvalid": "True",
        "mac_vendor": "Intel Corp",
        "ht": True,
        "vht": True,
        "he": True,
        "eht": False,
        # Extended agent fields (exercise the full mapping surface)
        "vendor_ie_ouis": ["00:03:7F", "00:15:6D"],
        "wps_enabled": True,
        "wps_uuid": "abcd-1234",
        "probe_ssid_list": ["Home", "CafeWifi"],
    }


def _minimal_wifi_net():
    return {
        "macAddr": "00:11:22:33:44:55",
        "ssid": "",
    }


def _full_bt_dev():
    return {
        "macAddr": "AA:BB:CC:DD:EE:FF",
        "name": "My Headphones",
        "company": "Sony Corp",
        "manufacturer": "Sony Corp",
        "bluetoothdescription": "Audio Headset",
        "bttype": 2,
        "rssi": -70,
        "txpower": -60,
        "txpowervalid": "True",
        "ibeaconrange": 2.5,
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "firstseen": "2024-06-15 14:00:00",
        "lastseen":  "2024-06-15 14:29:00",
        "lat": "37.7749",
        "lon": "-122.4194",
    }


def _minimal_bt_dev():
    return {
        "macAddr": "AA:BB:CC:DD:EE:FF",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWifiMappingCompliance:
    def setup_method(self):
        self.valid = _mapping_paths(_WIFI_MAPPING)

    def _check(self, doc, label):
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - self.valid
        assert unmapped == set(), (
            f"{label}: unmapped fields:\n" + "\n".join(f"  {p}" for p in sorted(unmapped))
        )

    def test_full_wifi_no_unmapped(self):
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        self._check(doc, "full wifi")

    def test_minimal_wifi_no_unmapped(self):
        doc = build_wifi_document(_minimal_wifi_net(), _OBS_NO_GPS, _NOW)
        self._check(doc, "minimal wifi")

    def test_wifi_zero_gps_no_unmapped(self):
        net = _full_wifi_net()
        doc = build_wifi_document(net, {"id": "s", "hostname": "s",
                                        "geo": {"lat": 0.0, "lon": 0.0}}, _NOW)
        self._check(doc, "zero-gps wifi")


class TestBtMappingCompliance:
    def setup_method(self):
        self.valid = _mapping_paths(_BT_MAPPING)

    def _check(self, doc, label):
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - self.valid
        assert unmapped == set(), (
            f"{label}: unmapped fields:\n" + "\n".join(f"  {p}" for p in sorted(unmapped))
        )

    def test_full_bt_no_unmapped(self):
        doc = build_bt_document(_full_bt_dev(), _OBS_WITH_GPS, _NOW)
        self._check(doc, "full bt")

    def test_minimal_bt_no_unmapped(self):
        doc = build_bt_document(_minimal_bt_dev(), _OBS_NO_GPS, _NOW)
        self._check(doc, "minimal bt")


class TestHelperSanity:
    def test_flatten_nested(self):
        paths = _flatten_paths({"a": {"b": {"c": 1}}, "x": 2})
        assert paths == {"a.b.c", "x"}

    def test_flatten_list_emits_parent(self):
        paths = _flatten_paths({"a": {"b": [1, 2, 3]}})
        assert "a.b" in paths

    def test_mapping_paths_include_geo_sub(self):
        valid = _mapping_paths(_WIFI_MAPPING)
        assert "observer.geo.location" in valid
        assert "observer.geo.location.lat" in valid
        assert "observer.geo.location.lon" in valid

    def test_rogue_field_detected(self):
        valid = _mapping_paths(_WIFI_MAPPING)
        doc = build_wifi_document(_full_wifi_net(), _OBS_WITH_GPS, _NOW)
        doc["rogue_top"] = "boom"
        doc.setdefault("wifi", {})["rogue_nested"] = 1
        unmapped = _flatten_paths(doc) - valid
        assert "rogue_top" in unmapped
        assert "wifi.rogue_nested" in unmapped
