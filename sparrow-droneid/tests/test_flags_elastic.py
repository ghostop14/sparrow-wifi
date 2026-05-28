"""
Tests for military/law_enforcement flag support in the Elasticsearch layer.

Covers: build_detection includes booleans; build_alert includes booleans
(default False when device is None); template mapping contains both boolean
fields; put_mapping is invoked during bootstrap; ElasticsearchClient and
OpenSearchClient both expose put_mapping calling indices.put_mapping.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.elasticsearch_engine import (
    DocumentBuilder,
    ElasticsearchEngine,
    build_index_template,
)
from backend.models import DroneIDDevice, AlertEvent


def _make_device(military=False, law_enforcement=False, **kwargs):
    defaults = dict(
        serial_number='TEST-001',
        drone_lat=35.0, drone_lon=-78.0,
        drone_height_agl=50.0,
        speed=10.0, direction=90.0,
        mac_address='AA:BB:CC:DD:EE:FF',
        rssi=-70,
        protocol='astm_nan',
        first_seen='2026-04-13T10:00:00Z',
        last_seen='2026-04-13T10:01:00Z',
        disposition='unknown',
        military=military,
        law_enforcement=law_enforcement,
    )
    defaults.update(kwargs)
    return DroneIDDevice(**defaults)


def _make_alert(**kwargs):
    defaults = dict(
        id=1,
        timestamp='2026-04-13T10:00:00Z',
        alert_type='new_drone',
        serial_number='TEST-001',
        detail='New drone detected',
        drone_lat=35.0, drone_lon=-78.0,
        drone_height_agl=50.0,
    )
    defaults.update(kwargs)
    return AlertEvent(**defaults)


class TestDocumentBuilderFlags(unittest.TestCase):

    def test_build_detection_military_false_by_default(self):
        device = _make_device()
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertFalse(doc['droneid']['military'])
        self.assertFalse(doc['droneid']['law_enforcement'])

    def test_build_detection_military_true(self):
        device = _make_device(military=True)
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertTrue(doc['droneid']['military'])
        self.assertFalse(doc['droneid']['law_enforcement'])

    def test_build_detection_law_enforcement_true(self):
        device = _make_device(law_enforcement=True)
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertFalse(doc['droneid']['military'])
        self.assertTrue(doc['droneid']['law_enforcement'])

    def test_build_detection_both_true(self):
        device = _make_device(military=True, law_enforcement=True)
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertTrue(doc['droneid']['military'])
        self.assertTrue(doc['droneid']['law_enforcement'])

    def test_build_detection_fallback_when_attr_absent(self):
        device = _make_device()
        del device.__dict__['military']
        del device.__dict__['law_enforcement']
        doc = DocumentBuilder.build_detection(
            device, 0.0, 0.0, 0.0, 'sensor-1', 'host-1')
        self.assertFalse(doc['droneid']['military'])
        self.assertFalse(doc['droneid']['law_enforcement'])

    def test_build_alert_includes_flags_from_device(self):
        device = _make_device(military=True, law_enforcement=True)
        alert = _make_alert()
        doc = DocumentBuilder.build_alert(
            alert, device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertTrue(doc['droneid']['military'])
        self.assertTrue(doc['droneid']['law_enforcement'])

    def test_build_alert_device_none_defaults_false(self):
        alert = _make_alert()
        doc = DocumentBuilder.build_alert(
            alert, None, 0.0, 0.0, 0.0, 'sensor-1', 'host-1')
        self.assertFalse(doc['droneid']['military'])
        self.assertFalse(doc['droneid']['law_enforcement'])


class TestTemplateMappingContainsFlags(unittest.TestCase):

    def test_build_index_template_contains_military_boolean(self):
        tmpl = build_index_template(prefix='test', shards=1, replicas=0,
                                    ilm_policy=None, backend_type='elasticsearch')
        droneid_props = (
            tmpl['template']['mappings']['properties']['droneid']['properties']
        )
        self.assertIn('military', droneid_props)
        self.assertEqual(droneid_props['military']['type'], 'boolean')

    def test_build_index_template_contains_law_enforcement_boolean(self):
        tmpl = build_index_template(prefix='test', shards=1, replicas=0,
                                    ilm_policy=None, backend_type='elasticsearch')
        droneid_props = (
            tmpl['template']['mappings']['properties']['droneid']['properties']
        )
        self.assertIn('law_enforcement', droneid_props)
        self.assertEqual(droneid_props['law_enforcement']['type'], 'boolean')


class TestBootstrapPutMapping(unittest.TestCase):
    """put_mapping must be called against the write alias during _attempt_bootstrap."""

    def _make_es_engine(self):
        engine = ElasticsearchEngine.__new__(ElasticsearchEngine)
        engine._index_prefix = 'sparrow-droneid'
        engine._shards = 1
        engine._replicas = 0
        engine._ilm_policy = None
        engine._backend_type = 'elasticsearch'
        engine._client = None
        engine._status = MagicMock()
        return engine

    def test_put_mapping_called_with_alias_and_flag_fields(self):
        engine = self._make_es_engine()
        mock_client = MagicMock()
        mock_client.put_index_template.return_value = None
        mock_client.alias_exists.return_value = True  # alias exists → skip create_initial_index
        mock_client.put_mapping.return_value = None

        with patch.object(engine, '_require_client', return_value=mock_client):
            engine._attempt_bootstrap()

        mock_client.put_mapping.assert_called_once()
        call_args = mock_client.put_mapping.call_args
        # First positional arg is the alias name
        alias_arg = call_args[0][0] if call_args[0] else call_args[1].get('index_or_alias')
        self.assertEqual(alias_arg, 'sparrow-droneid')
        # Second positional arg is the properties dict
        props_arg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get('properties')
        self.assertIn('droneid', props_arg)
        droneid_inner = props_arg['droneid']['properties']
        self.assertIn('military', droneid_inner)
        self.assertIn('law_enforcement', droneid_inner)
        self.assertEqual(droneid_inner['military']['type'], 'boolean')
        self.assertEqual(droneid_inner['law_enforcement']['type'], 'boolean')

    def test_put_mapping_failure_is_non_fatal(self):
        """If put_mapping raises, _attempt_bootstrap must still return True."""
        engine = self._make_es_engine()
        mock_client = MagicMock()
        mock_client.put_index_template.return_value = None
        mock_client.alias_exists.return_value = True
        mock_client.put_mapping.side_effect = Exception("mapping error")

        with patch.object(engine, '_require_client', return_value=mock_client):
            result = engine._attempt_bootstrap()

        self.assertTrue(result)


class TestClientPutMapping(unittest.TestCase):
    """ElasticsearchClient and OpenSearchClient must call indices.put_mapping."""

    def test_elasticsearch_client_put_mapping(self):
        from backend.elasticsearch_engine import ElasticsearchClient
        client = ElasticsearchClient.__new__(ElasticsearchClient)
        inner = MagicMock()
        inner.indices.put_mapping.return_value = None
        client._client = inner

        client.put_mapping('my-alias', {'field': {'type': 'boolean'}})

        inner.indices.put_mapping.assert_called_once_with(
            index='my-alias',
            body={'properties': {'field': {'type': 'boolean'}}},
        )

    def test_opensearch_client_put_mapping(self):
        from backend.elasticsearch_engine import OpenSearchClient
        client = OpenSearchClient.__new__(OpenSearchClient)
        inner = MagicMock()
        inner.indices.put_mapping.return_value = None
        client._client = inner

        client.put_mapping('my-alias', {'field': {'type': 'boolean'}})

        inner.indices.put_mapping.assert_called_once_with(
            index='my-alias',
            body={'properties': {'field': {'type': 'boolean'}}},
        )


if __name__ == '__main__':
    unittest.main()
