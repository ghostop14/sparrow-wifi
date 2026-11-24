"""Tests for the optional Remote ID trust verdicts (the `remoteid` package).

When `remoteid` is installed, every captured WiFi frame and BLE service-data
payload is also fed to a per-transmitter Tracker, and the resulting verdict
(authenticated / plausible / suspicious / spoofed) is attached to the tracked
DroneIDDevice. Without the package the engine must behave exactly as before,
leaving trust_verdict empty.

The 802.11 frame below is the first beacon of the Open Drone ID sample
capture odid_wifi_bcn_sample.pcap
(https://github.com/opendroneid/wireshark-dissector), transmitter
84:cc:a8:60:43:24; the BLE blob is the Basic ID message from that same
capture in ASTM F3411 service-data form (application code + counter +
25-byte message).
"""

import os
import struct
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

import backend.droneid_engine as engine_mod  # noqa: E402
from backend.droneid_engine import DroneIDEngine  # noqa: E402
from backend.models import Protocol  # noqa: E402

MAC = '84:cc:a8:60:43:24'

BEACON_FRAME = bytes.fromhex(
    '80000000ffffffffffff84cca860432484cca8604324000d00000000'
    '00000000b80b2104030106000e4742522d4f502d31323341424344dd'
    '85fa0bbc0dd0f0190500004d46473141303132333435363738390000'
    '0000000050f610005c527ebcba251ba88cb4b60000aa099808394100'
    '000a00300052656372656174696f6e616c0000000000000000000000'
    '4004a485251b6edbb3b6010032000000001500000000000000500047'
    '42522d4f502d31323341424344000000000000000000')

# ASTM F3411 BLE service data: application code 0x0D, counter, Basic ID msg.
BLE_SERVICE_DATA = bytes.fromhex(
    '0d0100004d464731413031323334353637383900000000000050f6')

VERDICTS = {'authenticated', 'plausible', 'suspicious', 'spoofed'}


def _radiotap(frame: bytes) -> bytes:
    """Wrap an 802.11 frame in a minimal 8-byte radiotap header (no fields)."""
    return struct.pack('<BBHI', 0, 0, 8, 0) + frame


def _make_engine():
    db = MagicMock()
    db.get_current_dispositions.return_value = {}
    gps = MagicMock()
    gps.get_receiver_position.return_value = (0.0, 0.0, 0.0)
    return DroneIDEngine(db, gps)


@unittest.skipUnless(engine_mod.remoteid, "remoteid package not installed")
class TestTrustVerdicts(unittest.TestCase):

    def setUp(self):
        self.engine = _make_engine()

    def test_wifi_beacon_frame_gets_verdict(self):
        self.engine._process_pcap_frame(_radiotap(BEACON_FRAME))
        with self.engine._lock:
            devices = list(self.engine._active_drones.values())
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.serial_number, 'MFG1A0123456789')
        self.assertIn(device.trust_verdict, VERDICTS)
        self.assertIsInstance(device.trust_reasons, list)

    def test_legitimate_sample_is_not_flagged(self):
        # A coherent capture from a real transmitter must not alert.
        verdict = self.engine._trust_ingest(_radiotap(BEACON_FRAME), 'radiotap')
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.verdict, 'plausible')
        self.assertFalse(verdict.is_alert)

    def test_ble_service_data_gets_verdict(self):
        verdict = self.engine._trust_ingest(
            BLE_SERVICE_DATA, 'ble_service_data', address=MAC, rssi=-70)
        self.assertIsNotNone(verdict)
        self.assertIn(verdict.verdict, VERDICTS)
        self.assertEqual(verdict.uas_id, 'MFG1A0123456789')

    def test_non_remoteid_frame_returns_none(self):
        verdict = self.engine._trust_ingest(b'\x01\x02\x03', 'radiotap')
        self.assertIsNone(verdict)

    def test_trust_fields_in_api_dict(self):
        self.engine._process_pcap_frame(_radiotap(BEACON_FRAME))
        with self.engine._lock:
            device = next(iter(self.engine._active_drones.values()))
        data = device.to_dict()
        self.assertIn(data['trust_verdict'], VERDICTS)
        self.assertIn('trust_reasons', data)


class TestTrustGracefulDegradation(unittest.TestCase):
    """Without `remoteid` the engine must track drones exactly as before."""

    def test_engine_tracks_without_tracker(self):
        engine = _make_engine()
        engine._trust_tracker = None  # simulate remoteid not installed
        self.assertIsNone(
            engine._trust_ingest(_radiotap(BEACON_FRAME), 'radiotap'))
        engine._process_pcap_frame(_radiotap(BEACON_FRAME))
        with engine._lock:
            devices = list(engine._active_drones.values())
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.protocol, Protocol.ASTM_BEACON.value)
        self.assertEqual(device.trust_verdict, '')
        self.assertEqual(device.trust_reasons, [])


if __name__ == '__main__':
    unittest.main()
