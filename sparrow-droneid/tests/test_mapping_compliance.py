"""
Recursive mapping-compliance tests for Elasticsearch document builders.

Ensures that every leaf field emitted by DocumentBuilder.build_detection()
and DocumentBuilder.build_alert() exists in the index mapping produced by
build_index_template().

Design
------
_flatten_paths(d)      Walk a nested dict, yield dotted paths to leaves.
_mapping_paths(tmpl)   Walk the mapping properties tree, yield valid field paths.
                       Geo-point fields expand to <field>.lat / <field>.lon
                       so that doc-level {"lat": ..., "lon": ...} objects are
                       recognised as valid representations.
                       Multi-field sub-objects (the "fields" key) are also
                       expanded so that keyword sub-fields are accounted for.

Test matrix
-----------
1. Full detection doc (all positions populated) — no missing paths
2. Minimal detection doc (zero positions → geo conditional fields absent)
3. Full alert doc — no missing paths
4. Minimal alert doc — no missing paths
5. Sanity: injecting a rogue field into the doc FAILS the check
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sparrow_droneid.backend.elasticsearch_engine import (
    DocumentBuilder,
    build_index_template,
)
from sparrow_droneid.backend.models import DroneIDDevice, AlertEvent


# ── Path helpers ─────────────────────────────────────────────────────────


def _flatten_paths(d: dict, prefix: str = "") -> set:
    """Walk a nested dict, yielding dotted paths to non-dict leaves.

    List values are skipped — ECS fields like ``event.category`` are
    keyword arrays; the mapping declares them at the parent path.
    """
    paths: set = set()
    for key, value in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.update(_flatten_paths(value, full_key))
        else:
            # Emit the leaf path (list values included as their parent path)
            paths.add(full_key)
    return paths


def _mapping_paths(template: dict) -> set:
    """Walk the mapping's properties tree, yielding all valid field paths.

    Handles:
    - Nested ``properties`` objects (recurse into sub-fields)
    - ``geo_point`` fields: emits both ``<path>`` and ``<path>.lat`` /
      ``<path>.lon`` so that doc-level {lat, lon} objects are recognised.
    - Multi-field ``fields`` sub-objects (e.g. ``keyword`` sub-fields of
      ``text`` fields): emits ``<path>.<subfield_name>`` for completeness.
    """
    mappings = template["template"]["mappings"]
    properties = mappings.get("properties", {})
    return _walk_properties(properties, prefix="")


def _walk_properties(props: dict, prefix: str) -> set:
    paths: set = set()
    for field_name, field_def in props.items():
        full_path = f"{prefix}.{field_name}" if prefix else field_name

        field_type = field_def.get("type")

        if "properties" in field_def:
            # Object or nested field — recurse, don't emit the parent path
            # as a leaf (it's a container, not a stored field)
            paths.update(_walk_properties(field_def["properties"], full_path))
        else:
            # Leaf field
            paths.add(full_path)

            if field_type == "geo_point":
                # ES accepts {lat, lon} objects; emit virtual sub-paths
                paths.add(f"{full_path}.lat")
                paths.add(f"{full_path}.lon")

        # Multi-fields (e.g. text + keyword) — emit sub-field paths
        if "fields" in field_def:
            for sub_name, sub_def in field_def["fields"].items():
                sub_path = f"{full_path}.{sub_name}"
                paths.add(sub_path)
                if sub_def.get("type") == "geo_point":
                    paths.add(f"{sub_path}.lat")
                    paths.add(f"{sub_path}.lon")

    return paths


# ── Fixtures ─────────────────────────────────────────────────────────────


def _full_device() -> DroneIDDevice:
    """A DroneIDDevice with ALL positions populated (maximum field emission)."""
    return DroneIDDevice(
        serial_number="FULL-TEST-001",
        registration_id="REG-001",
        id_type=1,
        ua_type=2,
        drone_lat=35.1234,
        drone_lon=-78.5678,
        drone_alt_geo=150.0,
        drone_alt_baro=148.5,
        drone_height_agl=45.0,
        speed=12.5,
        direction=270.0,
        vertical_speed=-0.5,
        operator_lat=35.1200,
        operator_lon=-78.5700,
        operator_alt=95.0,
        operator_id="OP-001",
        self_id_text="Survey flight",
        mac_address="AA:BB:CC:DD:EE:FF",
        rssi=-68,
        channel=6,
        frequency=2437,
        protocol="astm_nan",
        first_seen="2026-03-31T14:30:00Z",
        last_seen="2026-03-31T14:33:00Z",
        takeoff_lat=48.8580,
        takeoff_lon=2.2940,
        disposition="unknown",
    )


def _minimal_device() -> DroneIDDevice:
    """A DroneIDDevice with no positional data (minimum field emission)."""
    return DroneIDDevice(
        serial_number="MIN-TEST-001",
        last_seen="2026-03-31T14:33:00Z",
    )


def _full_alert(with_device: bool = True) -> tuple:
    """Return (AlertEvent, DroneIDDevice|None) with positions populated."""
    alert = AlertEvent(
        id=1,
        timestamp="2026-03-31T14:35:00Z",
        alert_type="altitude_max",
        serial_number="FULL-TEST-001",
        detail="Altitude 450m exceeds limit",
        drone_lat=35.12,
        drone_lon=-78.56,
        drone_height_agl=450.0,
    )
    device = _full_device() if with_device else None
    return alert, device


def _build_mapping_paths() -> set:
    """Build the canonical set of valid mapping paths once."""
    template = build_index_template(
        prefix="sparrow-droneid",
        shards=2,
        replicas=0,
        ilm_policy="",
    )
    return _mapping_paths(template)


# ── Tests ─────────────────────────────────────────────────────────────────


class TestDetectionDocCompliance(unittest.TestCase):
    """build_detection() output must be a strict subset of the mapping."""

    @classmethod
    def setUpClass(cls):
        cls.valid_paths = _build_mapping_paths()

    def _check_no_unmapped_fields(self, doc: dict, label: str):
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - self.valid_paths
        self.assertEqual(
            unmapped, set(),
            f"{label}: doc has fields not in mapping:\n"
            + "\n".join(f"  {p}" for p in sorted(unmapped)),
        )

    def test_full_detection_no_unmapped_fields(self):
        """Fully-populated detection doc must have no fields outside the mapping."""
        doc = DocumentBuilder.build_detection(
            device=_full_device(),
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="north-fence",
            observer_hostname="rpi-01",
            rssi_trend_value="strengthening",
            vendor="DJI",
            time_in_area_s=300,
        )
        self._check_no_unmapped_fields(doc, "full detection")

    def test_minimal_detection_no_unmapped_fields(self):
        """Minimal detection doc (no positions) must also be within mapping."""
        doc = DocumentBuilder.build_detection(
            device=_minimal_device(),
            receiver_lat=0.0,
            receiver_lon=0.0,
            receiver_alt=0.0,
            observer_name="sensor-1",
            observer_hostname="rpi-02",
        )
        self._check_no_unmapped_fields(doc, "minimal detection")

    def test_french_rid_detection_no_unmapped_fields(self):
        """French RID detection with takeoff point must be within mapping."""
        device = DroneIDDevice(
            serial_number="FR-TEST-001",
            protocol="french",
            drone_lat=48.8566,
            drone_lon=2.3522,
            takeoff_lat=48.8580,
            takeoff_lon=2.2940,
            last_seen="2026-03-31T14:33:00Z",
        )
        doc = DocumentBuilder.build_detection(
            device=device,
            receiver_lat=48.85,
            receiver_lon=2.35,
            receiver_alt=50.0,
            observer_name="paris-sensor",
            observer_hostname="rpi-03",
        )
        self._check_no_unmapped_fields(doc, "French RID detection")

    def test_full_detection_emits_expected_field_count(self):
        """Sanity: fully-populated doc should emit a reasonable number of paths."""
        doc = DocumentBuilder.build_detection(
            device=_full_device(),
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        doc_paths = _flatten_paths(doc)
        # Should have at least 30 distinct leaf paths when fully populated
        self.assertGreaterEqual(len(doc_paths), 30,
                                f"Expected >= 30 paths, got {len(doc_paths)}: "
                                + str(sorted(doc_paths)))


class TestAlertDocCompliance(unittest.TestCase):
    """build_alert() output must be a strict subset of the mapping."""

    @classmethod
    def setUpClass(cls):
        cls.valid_paths = _build_mapping_paths()

    def _check_no_unmapped_fields(self, doc: dict, label: str):
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - self.valid_paths
        self.assertEqual(
            unmapped, set(),
            f"{label}: doc has fields not in mapping:\n"
            + "\n".join(f"  {p}" for p in sorted(unmapped)),
        )

    def test_full_alert_with_device_no_unmapped_fields(self):
        """Alert with associated device and drone position must be within mapping."""
        alert, device = _full_alert(with_device=True)
        doc = DocumentBuilder.build_alert(
            alert=alert,
            device=device,
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor-1",
            observer_hostname="rpi-01",
        )
        self._check_no_unmapped_fields(doc, "alert with device")

    def test_minimal_alert_no_device_no_unmapped_fields(self):
        """Alert without device and no geo positions must be within mapping."""
        alert = AlertEvent(
            id=2,
            timestamp="2026-03-31T14:35:00Z",
            alert_type="proximity",
            serial_number="SN-002",
            detail="Within 200m",
            drone_lat=0.0,
            drone_lon=0.0,
        )
        doc = DocumentBuilder.build_alert(
            alert=alert,
            device=None,
            receiver_lat=0.0,
            receiver_lon=0.0,
            receiver_alt=0.0,
            observer_name="sensor-1",
            observer_hostname="rpi-01",
        )
        self._check_no_unmapped_fields(doc, "minimal alert")


class TestHeartbeatDocCompliance(unittest.TestCase):
    """build_heartbeat() output must be a strict subset of the mapping."""

    @classmethod
    def setUpClass(cls):
        cls.valid_paths = _build_mapping_paths()

    def _check_no_unmapped_fields(self, doc: dict, label: str):
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - self.valid_paths
        self.assertEqual(
            unmapped, set(),
            f"{label}: doc has fields not in mapping:\n"
            + "\n".join(f"  {p}" for p in sorted(unmapped)),
        )

    def test_heartbeat_no_unmapped_fields(self):
        """Heartbeat doc must be within mapping."""
        doc = DocumentBuilder.build_heartbeat(
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor-1",
            observer_hostname="rpi-01",
            heartbeat_data={
                "active_drones": 3,
                "monitoring": True,
                "interface": "wlan0",
                "uptime_s": 3600,
                "frame_count": 42000,
                "gps_fix": True,
            },
        )
        self._check_no_unmapped_fields(doc, "heartbeat")

    def test_heartbeat_no_geo_no_unmapped_fields(self):
        """Heartbeat without receiver position must also be within mapping."""
        doc = DocumentBuilder.build_heartbeat(
            receiver_lat=0.0,
            receiver_lon=0.0,
            receiver_alt=0.0,
            observer_name="sensor-1",
            observer_hostname="rpi-01",
            heartbeat_data={"active_drones": 0},
        )
        self._check_no_unmapped_fields(doc, "heartbeat no geo")


class TestSanityCheckHelpers(unittest.TestCase):
    """Self-tests that the helper functions and negative-case work correctly."""

    def test_rogue_field_at_top_level_fails(self):
        """A rogue top-level field must be detected as unmapped."""
        valid_paths = _build_mapping_paths()
        doc = DocumentBuilder.build_detection(
            device=_full_device(),
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        doc["rogue_field"] = "boom"
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - valid_paths
        self.assertIn("rogue_field", unmapped,
                      "Expected 'rogue_field' to be detected as unmapped")

    def test_rogue_nested_field_fails(self):
        """A rogue field nested inside a known sub-object must also be detected."""
        valid_paths = _build_mapping_paths()
        doc = DocumentBuilder.build_detection(
            device=_full_device(),
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        doc["droneid"]["rogue_nested"] = "boom"
        doc_paths = _flatten_paths(doc)
        unmapped = doc_paths - valid_paths
        self.assertIn("droneid.rogue_nested", unmapped,
                      "Expected 'droneid.rogue_nested' to be detected as unmapped")

    def test_flatten_paths_handles_lists(self):
        """_flatten_paths should emit the parent key for list values."""
        d = {"a": {"b": [1, 2, 3], "c": "hello"}}
        paths = _flatten_paths(d)
        self.assertIn("a.b", paths)
        self.assertIn("a.c", paths)

    def test_flatten_paths_simple_nesting(self):
        """_flatten_paths correctly walks nested dicts."""
        d = {"x": {"y": {"z": 1}}, "a": 2}
        paths = _flatten_paths(d)
        self.assertEqual(paths, {"x.y.z", "a"})

    def test_mapping_paths_includes_geo_sub_paths(self):
        """Mapping paths for geo_point fields include .lat / .lon virtual paths."""
        valid_paths = _build_mapping_paths()
        self.assertIn("source.geo.location", valid_paths)
        self.assertIn("source.geo.location.lat", valid_paths)
        self.assertIn("source.geo.location.lon", valid_paths)

    def test_mapping_paths_includes_observer_geo(self):
        valid_paths = _build_mapping_paths()
        self.assertIn("observer.geo.location.lat", valid_paths)
        self.assertIn("observer.geo.location.lon", valid_paths)

    def test_mapping_paths_includes_operator_location(self):
        valid_paths = _build_mapping_paths()
        self.assertIn("droneid.operator.location.lat", valid_paths)
        self.assertIn("droneid.operator.location.lon", valid_paths)

    def test_mapping_paths_includes_takeoff_location(self):
        valid_paths = _build_mapping_paths()
        self.assertIn("droneid.takeoff.location.lat", valid_paths)
        self.assertIn("droneid.takeoff.location.lon", valid_paths)

    def test_mapping_paths_includes_known_droneid_fields(self):
        valid_paths = _build_mapping_paths()
        expected = {
            "droneid.serial_number",
            "droneid.protocol",
            "droneid.drone.speed",
            "droneid.drone.altitude_class",
            "droneid.rf.rssi",
            "droneid.rf.rssi_trend",
            "droneid.operator.bvlos",
            "droneid.range.drone_m",
            "droneid.state",
            "droneid.disposition",
            "droneid.alert.type",
            "droneid.heartbeat.active_drones",
        }
        missing_from_mapping = expected - valid_paths
        self.assertEqual(
            missing_from_mapping, set(),
            f"These known fields are missing from the mapping: {missing_from_mapping}",
        )

    def test_all_builder_paths_in_mapping(self):
        """Aggregate assertion: union of all doc paths is in the mapping."""
        valid_paths = _build_mapping_paths()

        all_doc_paths: set = set()

        # Detection: full
        doc = DocumentBuilder.build_detection(
            device=_full_device(),
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        all_doc_paths.update(_flatten_paths(doc))

        # Detection: minimal
        doc = DocumentBuilder.build_detection(
            device=_minimal_device(),
            receiver_lat=0.0,
            receiver_lon=0.0,
            receiver_alt=0.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        all_doc_paths.update(_flatten_paths(doc))

        # Alert: full
        alert, device = _full_alert()
        doc = DocumentBuilder.build_alert(
            alert=alert,
            device=device,
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
        )
        all_doc_paths.update(_flatten_paths(doc))

        # Heartbeat
        doc = DocumentBuilder.build_heartbeat(
            receiver_lat=35.0,
            receiver_lon=-78.0,
            receiver_alt=100.0,
            observer_name="sensor",
            observer_hostname="host",
            heartbeat_data={
                "active_drones": 1,
                "monitoring": True,
                "interface": "wlan0",
                "uptime_s": 600,
                "frame_count": 100,
                "gps_fix": True,
            },
        )
        all_doc_paths.update(_flatten_paths(doc))

        unmapped = all_doc_paths - valid_paths
        self.assertEqual(
            unmapped, set(),
            "These paths appear in builder output but are absent from the mapping:\n"
            + "\n".join(f"  {p}" for p in sorted(unmapped)),
        )


if __name__ == "__main__":
    unittest.main()
