"""
Tests that DocumentBuilder includes droneid.disposition in both
detection and alert documents.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.elasticsearch_engine import DocumentBuilder
from backend.models import DroneIDDevice, AlertEvent


def _make_device(disposition='unknown', **kwargs):
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
        disposition=disposition,
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


class TestDocumentBuilderDisposition(unittest.TestCase):

    def test_build_detection_includes_disposition_unknown(self):
        device = _make_device(disposition='unknown')
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'unknown')

    def test_build_detection_includes_disposition_friendly(self):
        device = _make_device(disposition='friendly')
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'friendly')

    def test_build_detection_includes_disposition_threat(self):
        device = _make_device(disposition='threat')
        doc = DocumentBuilder.build_detection(
            device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'threat')

    def test_build_detection_disposition_fallback_on_missing_attr(self):
        # Simulate an old device object without disposition attribute
        device = _make_device()
        del device.__dict__['disposition']  # remove the instance attribute
        doc = DocumentBuilder.build_detection(
            device, 0.0, 0.0, 0.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'unknown')

    def test_build_alert_includes_disposition_from_device(self):
        device = _make_device(disposition='threat')
        alert = _make_alert()
        doc = DocumentBuilder.build_alert(
            alert, device, 35.1, -78.1, 10.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'threat')

    def test_build_alert_disposition_none_device_defaults_unknown(self):
        alert = _make_alert()
        doc = DocumentBuilder.build_alert(
            alert, None, 0.0, 0.0, 0.0, 'sensor-1', 'host-1')
        self.assertEqual(doc['droneid']['disposition'], 'unknown')


if __name__ == '__main__':
    unittest.main()
