"""
Tests for the Elasticsearch/OpenSearch integration engine.

Covers: document building, _id determinism, buffer back-pressure,
geo sentinel handling, config validation, and engine state transitions.
"""

import threading
import unittest
from unittest.mock import MagicMock

# Add project root to path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.elasticsearch_engine import (
    BulkBuffer,
    DocumentBuilder,
    ElasticsearchEngine,
    build_index_template,
    build_default_ilm_policy,
    build_default_ism_policy,
    _compute_severity,
)
from backend.models import DroneIDDevice, AlertEvent


class TestDocumentBuilder(unittest.TestCase):
    """Test ECS document construction."""

    def _make_device(self, **overrides):
        defaults = dict(
            serial_number="TEST123",
            registration_id="",
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
            protocol="dji_proprietary",
            first_seen="2026-03-31T14:30:00Z",
            last_seen="2026-03-31T14:33:00Z",
        )
        defaults.update(overrides)
        return DroneIDDevice(**defaults)

    def test_detection_has_required_ecs_fields(self):
        device = self._make_device()
        doc = DocumentBuilder.build_detection(
            device, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertIn("@timestamp", doc)
        self.assertEqual(doc["ecs"]["version"], "8.17.0")
        self.assertEqual(doc["event"]["kind"], "event")
        self.assertEqual(doc["event"]["dataset"], "sparrow_droneid.detection")

    def test_detection_observer_fields(self):
        doc = DocumentBuilder.build_detection(
            self._make_device(), 35.0, -78.0, 100.0,
            "north-fence", "rpi-04",
        )
        self.assertEqual(doc["observer"]["name"], "north-fence")
        self.assertEqual(doc["observer"]["hostname"], "rpi-04")
        self.assertEqual(doc["observer"]["type"], "sensor")
        self.assertIn("geo", doc["observer"])
        self.assertEqual(doc["observer"]["geo"]["location"]["lat"], 35.0)

    def test_detection_droneid_namespace(self):
        doc = DocumentBuilder.build_detection(
            self._make_device(), 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        d = doc["droneid"]
        self.assertEqual(d["serial_number"], "TEST123")
        self.assertEqual(d["protocol"], "dji_proprietary")
        self.assertEqual(d["drone"]["height_agl"], 45.0)
        self.assertEqual(d["drone"]["speed"], 12.5)
        self.assertEqual(d["operator"]["id"], "OP-001")

    def test_geo_sentinel_null_coercion_drone(self):
        """0,0 drone position → source.geo.location should be absent."""
        device = self._make_device(drone_lat=0.0, drone_lon=0.0)
        doc = DocumentBuilder.build_detection(
            device, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertNotIn("geo", doc.get("source", {}))

    def test_geo_sentinel_null_coercion_operator(self):
        """0,0 operator position → droneid.operator.location absent."""
        device = self._make_device(operator_lat=0.0, operator_lon=0.0)
        doc = DocumentBuilder.build_detection(
            device, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertNotIn("location", doc["droneid"]["operator"])

    def test_geo_sentinel_null_coercion_receiver(self):
        """0,0 receiver position → observer.geo absent."""
        doc = DocumentBuilder.build_detection(
            self._make_device(), 0.0, 0.0, 0.0,
            "sensor-1", "rpi-01",
        )
        self.assertNotIn("geo", doc["observer"])

    def test_valid_positions_produce_geo_points(self):
        doc = DocumentBuilder.build_detection(
            self._make_device(), 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertIn("geo", doc["source"])
        self.assertEqual(doc["source"]["geo"]["location"]["lat"], 35.1234)
        self.assertIn("geo", doc["observer"])
        self.assertIn("location", doc["droneid"]["operator"])

    def test_bvlos_true_when_no_operator_pos(self):
        device = self._make_device(operator_lat=0.0, operator_lon=0.0)
        doc = DocumentBuilder.build_detection(
            device, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertTrue(doc["droneid"]["operator"]["bvlos"])

    def test_bvlos_false_when_operator_close(self):
        """Operator at nearly same position as drone → within VLOS."""
        device = self._make_device(
            operator_lat=35.1234, operator_lon=-78.5678)
        doc = DocumentBuilder.build_detection(
            device, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertFalse(doc["droneid"]["operator"]["bvlos"])

    def test_event_duration_nanoseconds(self):
        doc = DocumentBuilder.build_detection(
            self._make_device(), 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01", time_in_area_s=180,
        )
        self.assertEqual(doc["event"]["duration"], 180_000_000_000)

    def test_alert_document(self):
        alert = AlertEvent(
            id=1, timestamp="2026-03-31T14:35:00Z",
            alert_type="altitude_max",
            serial_number="TEST123",
            detail="Altitude 450m exceeds limit",
            drone_lat=35.12, drone_lon=-78.56,
            drone_height_agl=450.0,
        )
        doc = DocumentBuilder.build_alert(
            alert, None, 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertEqual(doc["event"]["kind"], "alert")
        self.assertEqual(doc["event"]["dataset"], "sparrow_droneid.alert")
        self.assertEqual(doc["droneid"]["alert"]["type"], "altitude_max")
        self.assertIn("geo", doc["source"])

    def test_event_category_intrusion_detection(self):
        doc = DocumentBuilder.build_detection(
            self._make_device(), 35.0, -78.0, 100.0,
            "sensor-1", "rpi-01",
        )
        self.assertEqual(doc["event"]["category"], ["intrusion_detection"])


class TestDocIdDeterminism(unittest.TestCase):
    """Deterministic _id ensures retry idempotency."""

    def test_same_inputs_same_id(self):
        id1 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        id2 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        self.assertEqual(id1, id2)

    def test_different_serial_different_id(self):
        id1 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        id2 = DocumentBuilder.compute_doc_id("SN456", "sensor-1", "2026-03-31T14:30:00Z")
        self.assertNotEqual(id1, id2)

    def test_different_observer_different_id(self):
        id1 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        id2 = DocumentBuilder.compute_doc_id("SN123", "sensor-2", "2026-03-31T14:30:00Z")
        self.assertNotEqual(id1, id2)

    def test_different_timestamp_different_id(self):
        id1 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        id2 = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:01Z")
        self.assertNotEqual(id1, id2)

    def test_id_is_sha256_hex(self):
        doc_id = DocumentBuilder.compute_doc_id("SN123", "sensor-1", "2026-03-31T14:30:00Z")
        self.assertEqual(len(doc_id), 64)  # SHA-256 hex digest
        int(doc_id, 16)  # Should not raise

    def test_uses_epoch_ms_not_raw_string(self):
        """Equivalent timestamps in different formats must produce same _id."""
        id1 = DocumentBuilder.compute_doc_id("SN123", "s1", "2026-03-31T14:30:00Z")
        id2 = DocumentBuilder.compute_doc_id("SN123", "s1", "2026-03-31T14:30:00+00:00")
        self.assertEqual(id1, id2)


class TestBulkBuffer(unittest.TestCase):
    """Thread-safe bounded buffer with drop-oldest."""

    def test_append_and_swap(self):
        buf = BulkBuffer(max_size=100)
        buf.append({"doc": 1})
        buf.append({"doc": 2})
        items = buf.swap()
        self.assertEqual(len(items), 2)
        self.assertEqual(buf.depth, 0)

    def test_drop_oldest_when_full(self):
        buf = BulkBuffer(max_size=3)
        buf.append({"doc": 1})
        buf.append({"doc": 2})
        buf.append({"doc": 3})
        buf.append({"doc": 4})  # Should drop doc 1
        items = buf.swap()
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["doc"], 2)
        self.assertEqual(items[-1]["doc"], 4)
        self.assertEqual(buf.docs_dropped, 1)

    def test_concurrent_append(self):
        """Multiple threads appending should not lose events (up to capacity)."""
        buf = BulkBuffer(max_size=10_000)
        errors = []

        def writer(start):
            try:
                for i in range(1000):
                    buf.append({"doc": start + i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i * 1000,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        items = buf.swap()
        self.assertEqual(len(items), 4000)

    def test_swap_returns_empty_when_no_data(self):
        buf = BulkBuffer()
        items = buf.swap()
        self.assertEqual(items, [])


class TestConfigValidation(unittest.TestCase):
    """Engine config validation gates."""

    def test_empty_url_prevents_start(self):
        engine = ElasticsearchEngine()
        engine.configure(enabled=True, url="")
        self.assertIsNone(engine._flush_thread)

    def test_no_scheme_prevents_start(self):
        engine = ElasticsearchEngine()
        engine.configure(enabled=True, url="localhost:9200")
        self.assertIsNone(engine._flush_thread)

    def test_invalid_backend_prevents_start(self):
        engine = ElasticsearchEngine()
        engine.configure(enabled=True, url="https://localhost:9200",
                         backend_type="invalid")
        self.assertIsNone(engine._flush_thread)

    def test_disabled_does_not_start(self):
        engine = ElasticsearchEngine()
        engine.configure(enabled=False, url="https://localhost:9200")
        self.assertIsNone(engine._flush_thread)

    def test_get_status_when_disabled(self):
        engine = ElasticsearchEngine()
        status = engine.get_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["healthy"])


class TestEngineStatusTracking(unittest.TestCase):
    """Status counters and state transitions."""

    def test_add_detection_when_disabled_is_noop(self):
        engine = ElasticsearchEngine()
        device = DroneIDDevice(serial_number="TEST")
        # Should not raise
        engine.add_detection(device, 0.0, 0.0, 0.0)
        self.assertEqual(engine._buffer.depth, 0)

    def test_status_reflects_buffer_depth(self):
        engine = ElasticsearchEngine()
        engine._enabled = True
        engine._client = MagicMock()
        device = DroneIDDevice(
            serial_number="TEST", last_seen="2026-03-31T14:30:00Z")
        engine.add_detection(device, 35.0, -78.0, 100.0)
        status = engine.get_status()
        self.assertEqual(status["docs_in_buffer"], 1)


class TestSeverityComputation(unittest.TestCase):
    """Event severity heuristic."""

    def test_low_threat(self):
        score = _compute_severity(
            range_m=2000, alt_class="LOW", bvlos=False,
            speed=5.0, dwell_s=30)
        self.assertLessEqual(score, 20)

    def test_high_threat(self):
        score = _compute_severity(
            range_m=100, alt_class="ILLEGAL", bvlos=True,
            speed=35.0, dwell_s=700)
        self.assertGreaterEqual(score, 80)

    def test_none_range(self):
        score = _compute_severity(
            range_m=None, alt_class="MEDIUM", bvlos=False,
            speed=10.0, dwell_s=0)
        self.assertGreaterEqual(score, 0)

    def test_capped_at_100(self):
        score = _compute_severity(
            range_m=50, alt_class="ILLEGAL", bvlos=True,
            speed=50.0, dwell_s=1000)
        self.assertLessEqual(score, 100)


class TestIndexTemplate(unittest.TestCase):
    """Index template construction."""

    def test_template_has_correct_pattern(self):
        tmpl = build_index_template("sparrow-droneid", 2, 0, "")
        self.assertEqual(tmpl["index_patterns"], ["sparrow-droneid-*"])

    def test_template_has_ecs_fields(self):
        tmpl = build_index_template("sparrow-droneid", 2, 0, "")
        props = tmpl["template"]["mappings"]["properties"]
        self.assertEqual(props["@timestamp"]["type"], "date")
        self.assertEqual(props["event"]["properties"]["kind"]["type"], "keyword")
        self.assertIn("droneid", props)

    def test_template_includes_ilm_when_set(self):
        tmpl = build_index_template("sparrow-droneid", 2, 0, "my-policy")
        settings = tmpl["template"]["settings"]
        self.assertEqual(settings["index.lifecycle.name"], "my-policy")

    def test_template_omits_ilm_when_empty(self):
        tmpl = build_index_template("sparrow-droneid", 2, 0, "")
        settings = tmpl["template"]["settings"]
        self.assertNotIn("index.lifecycle.name", settings)

    def test_droneid_geo_point_fields(self):
        tmpl = build_index_template("sparrow-droneid", 2, 0, "")
        props = tmpl["template"]["mappings"]["properties"]
        self.assertEqual(
            props["source"]["properties"]["geo"]["properties"]["location"]["type"],
            "geo_point")
        self.assertEqual(
            props["observer"]["properties"]["geo"]["properties"]["location"]["type"],
            "geo_point")
        self.assertEqual(
            props["droneid"]["properties"]["operator"]["properties"]["location"]["type"],
            "geo_point")


class TestLifecyclePolicyBuilders(unittest.TestCase):

    def test_ilm_policy_has_phases(self):
        policy = build_default_ilm_policy()
        self.assertIn("hot", policy["phases"])
        self.assertIn("warm", policy["phases"])
        self.assertIn("delete", policy["phases"])

    def test_ilm_policy_warm_phase_actions(self):
        policy = build_default_ilm_policy()
        warm = policy["phases"]["warm"]
        self.assertIn("readonly", warm["actions"])
        self.assertIn("forcemerge", warm["actions"])
        self.assertEqual(warm["actions"]["forcemerge"]["max_num_segments"], 1)

    def test_ilm_policy_custom_durations(self):
        policy = build_default_ilm_policy(hot_days=3, warm_days=14, delete_days=60)
        self.assertEqual(policy["phases"]["hot"]["actions"]["rollover"]["max_age"], "3d")
        self.assertEqual(policy["phases"]["warm"]["min_age"], "14d")
        self.assertEqual(policy["phases"]["delete"]["min_age"], "60d")

    def test_ism_policy_has_states(self):
        policy = build_default_ism_policy("sparrow-droneid")
        states = policy["policy"]["states"]
        state_names = [s["name"] for s in states]
        self.assertIn("hot", state_names)
        self.assertIn("warm", state_names)
        self.assertIn("delete", state_names)


if __name__ == "__main__":
    unittest.main()
