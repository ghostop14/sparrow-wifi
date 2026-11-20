"""Regression tests for Wi-Fi Remote ID frames that carry no identity.

Root cause (fixed): FrameExtractor accepted an ASTM NAN / beacon frame only
when the parsed device already had a key (serial or registration), and the MAC
that get_key() falls back to was only filled in later by _process_frame. A
transmitter that sends one ODID message per NAN frame and no Basic ID (the
Open Drone ID sample odid_wifi_sample.pcap does exactly this) was therefore
never tracked at all.

Fix: NAN frames are recognised by the Remote ID service ID instead of by the
presence of a serial, _process_frame sets the MAC before the key check, and
_track_device merges identity-less frames into the active entry for their MAC,
carrying Location fields forward only when a frame had no Location message.

The NAN frames below are taken verbatim from odid_wifi_sample.pcap
(https://github.com/opendroneid/wireshark-dissector), transmitter
84:cc:a8:60:43:24.
"""

import os
import struct
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.alert_engine import AlertEngine  # noqa: E402
from backend.droneid_engine import DroneIDEngine, FrameExtractor  # noqa: E402
from backend.models import AlertType, DroneIDDevice, Protocol  # noqa: E402

MAC = '84:cc:a8:60:43:24'

# One frame per ODID message type, each a message pack holding one message.
NAN_LOCATION = bytes.fromhex(
    'd0000000516f9a01000084cca8604324506f9a01017980060409506f9a13032700'
    '8869199d92090100101d23f0190110026c527e979f251be6eab3b60000aa099808'
    '394100000a000e040001000223')
NAN_SELF_ID = bytes.fromhex(
    'd0000000516f9a01000084cca8604324506f9a01017970070409506f9a13032700'
    '8869199d92090100101d28f01901300052656372656174696f6e616c0000000000'
    '0000000000000e040001000228')
NAN_SYSTEM = bytes.fromhex(
    'd0000000516f9a01000084cca8604324506f9a01017900080409506f9a13032700'
    '8869199d92090100101d2bf019014004a485251b6edbb3b6010032000000001500'
    '0000000000000e04000100022b')
NAN_OPERATOR_ID = bytes.fromhex(
    'd0000000516f9a01000084cca8604324506f9a01017950060409506f9a13032700'
    '8869199d92090100101d22f0190150004742522d4f502d31323341424344000000'
    '0000000000000e040001000222')

# Offset of the SDA Service ID inside the frames above.
_SERVICE_ID_OFFSET = 33


def _radiotap(frame: bytes) -> bytes:
    """Wrap an 802.11 frame in a minimal 8-byte radiotap header (no fields)."""
    return struct.pack('<BBHI', 0, 0, 8, 0) + frame


def _basic_id_msg(ident: str, id_type: int = 1) -> bytes:
    """25-byte ODID Basic ID message, UA type 2."""
    return bytes([0x00, (id_type << 4) | 0x02]) + ident.encode().ljust(20, b'\x00') + b'\x00' * 3


def _basic_id_frame(serial: str) -> bytes:
    """NAN_LOCATION's frame with its message replaced by a Basic ID (serial)."""
    msg = _basic_id_msg(serial)
    frame = bytearray(NAN_LOCATION)
    start = NAN_LOCATION.index(bytes.fromhex('f01901')) + 3
    frame[start:start + 25] = msg
    return bytes(frame)


# The Location message carried by NAN_LOCATION.
_LOC_MSG = NAN_LOCATION[NAN_LOCATION.index(bytes.fromhex('f01901')) + 3:][:25]

BEACON_MAC = '02:11:22:33:44:55'


def _beacon_frame(*messages: bytes) -> bytes:
    """ASTM F3411 beacon (FA:0B:BC type 0x0D vendor IE) with a message pack."""
    pack = bytes([0xF2, 25, len(messages)]) + b''.join(messages)
    ie_body = b'\xfa\x0b\xbc\x0d' + b'\x01' + pack  # OUI, type, msg counter
    hdr = (b'\x80\x00' + b'\x00\x00' + b'\xff' * 6
           + bytes.fromhex(BEACON_MAC.replace(':', '')) * 2 + b'\x00\x00')
    fixed = b'\x00' * 8 + b'\x64\x00' + b'\x01\x00'
    return hdr + fixed + bytes([221, len(ie_body)]) + ie_body


def _make_engine():
    db = MagicMock()
    db.get_current_dispositions.return_value = {}
    gps = MagicMock()
    gps.get_receiver_position.return_value = (0.0, 0.0, 0.0)
    engine = DroneIDEngine(db, gps)
    engine._monitoring = False
    return engine, db


class TestNanSingleMessageFrames(unittest.TestCase):

    def setUp(self):
        self.engine, self.db = _make_engine()

    def _feed(self, *frames):
        for f in frames:
            self.engine._process_pcap_frame(_radiotap(f))

    def _active(self):
        with self.engine._lock:
            return dict(self.engine._active_drones)

    def test_location_only_frame_is_tracked_by_mac(self):
        self._feed(NAN_LOCATION)
        active = self._active()
        self.assertEqual(list(active), [MAC])
        d = active[MAC]
        self.assertEqual(d.protocol, Protocol.ASTM_NAN.value)
        self.assertNotEqual(d.drone_lat, 0.0)
        self.assertNotEqual(d.drone_lon, 0.0)
        self.db.insert_detection.assert_called_once()

    def test_messages_merge_across_frames(self):
        self._feed(NAN_LOCATION, NAN_SELF_ID, NAN_SYSTEM, NAN_OPERATOR_ID)
        active = self._active()
        self.assertEqual(list(active), [MAC])
        d = active[MAC]
        # Position from the Location frame survives the later frames
        self.assertNotEqual(d.drone_lat, 0.0)
        self.assertEqual(d.self_id_text, 'Recreational')
        self.assertNotEqual(d.operator_lat, 0.0)
        self.assertEqual(d.operator_id, 'GBR-OP-123ABCD')

    def test_basic_id_migrates_mac_entry_and_later_frames_follow(self):
        self._feed(NAN_LOCATION)
        lat = self._active()[MAC].drone_lat

        self._feed(_basic_id_frame('SERIAL-NAN-01'))
        active = self._active()
        self.assertEqual(list(active), ['SERIAL-NAN-01'])
        # The Basic ID frame carried no Location; position is kept
        self.assertEqual(active['SERIAL-NAN-01'].drone_lat, lat)

        # Identity-less frames now merge into the serial-keyed entry instead
        # of re-creating a MAC-keyed one.
        self._feed(NAN_SYSTEM, NAN_LOCATION)
        active = self._active()
        self.assertEqual(list(active), ['SERIAL-NAN-01'])
        d = active['SERIAL-NAN-01']
        self.assertEqual(d.serial_number, 'SERIAL-NAN-01')
        self.assertEqual(d.id_type, 1)
        self.assertEqual(d.ua_type, 2)
        self.assertNotEqual(d.operator_lat, 0.0)

    def test_other_nan_service_is_ignored(self):
        frame = bytearray(NAN_LOCATION)
        frame[_SERVICE_ID_OFFSET:_SERVICE_ID_OFFSET + 6] = b'\x01\x02\x03\x04\x05\x06'
        self.assertIsNone(FrameExtractor.extract_from_action(bytes(frame)))
        self._feed(bytes(frame))
        self.assertEqual(self._active(), {})


class TestFrameMergeKeepsReportedLocation(unittest.TestCase):
    """A frame that carries a Location message is authoritative, zeros included."""

    def test_hovering_speed_zero_replaces_previous_speed(self):
        engine, _ = _make_engine()
        moving = DroneIDDevice(serial_number='S1', mac_address='aa:bb:cc:dd:ee:ff',
                               drone_lat=35.0, drone_lon=-78.0, speed=12.0, direction=90.0,
                               odid_messages=frozenset({0, 1}))
        hovering = DroneIDDevice(serial_number='S1', mac_address='aa:bb:cc:dd:ee:ff',
                                 drone_lat=35.0, drone_lon=-78.0, speed=0.0, direction=0.0,
                                 odid_messages=frozenset({0, 1}))
        engine._track_device(moving)
        engine._track_device(hovering)
        d = engine._active_drones['S1']
        self.assertEqual(d.speed, 0.0)
        self.assertEqual(d.direction, 0.0)

    def test_non_odid_frame_does_not_inherit_location(self):
        engine, _ = _make_engine()
        first = DroneIDDevice(serial_number='DJI1', mac_address='aa:bb:cc:dd:ee:01',
                              drone_lat=35.0, drone_lon=-78.0, speed=5.0,
                              protocol=Protocol.DJI_PROPRIETARY.value)
        second = DroneIDDevice(serial_number='DJI1', mac_address='aa:bb:cc:dd:ee:01',
                               protocol=Protocol.DJI_PROPRIETARY.value)
        engine._track_device(first)
        engine._track_device(second)
        d = engine._active_drones['DJI1']
        self.assertEqual(d.drone_lat, 0.0)
        self.assertEqual(d.speed, 0.0)

    def test_odid_messages_not_in_api_dict(self):
        d = DroneIDDevice(odid_messages=frozenset({1}))
        self.assertNotIn('odid_messages', d.to_dict())


class TestBeaconWithoutIdentity(unittest.TestCase):

    def test_beacon_pack_without_basic_id_is_tracked_by_mac(self):
        engine, _ = _make_engine()
        engine._process_pcap_frame(_radiotap(_beacon_frame(_LOC_MSG)))
        d = engine._active_drones[BEACON_MAC]
        self.assertEqual(d.protocol, Protocol.ASTM_BEACON.value)
        self.assertNotEqual(d.drone_lat, 0.0)

    def test_short_identity_is_still_rejected(self):
        dev = FrameExtractor.extract_from_beacon_or_probe(
            _beacon_frame(_basic_id_msg('AB'), _LOC_MSG))
        self.assertIsNone(dev)

    def test_beacon_with_identity_still_keyed_by_serial(self):
        engine, _ = _make_engine()
        engine._process_pcap_frame(_radiotap(_beacon_frame(_basic_id_msg('SER-BCN-1'), _LOC_MSG)))
        self.assertEqual(list(engine._active_drones), ['SER-BCN-1'])


class TestKeyStability(unittest.TestCase):

    def test_serial_and_registration_frames_share_one_stable_key(self):
        engine, db = _make_engine()
        db.get_current_flags.return_value = {}
        frames = [_beacon_frame(_basic_id_msg('REG-BBBB', id_type=2), _LOC_MSG),
                  _beacon_frame(_basic_id_msg('SER-AAAA', id_type=1), _LOC_MSG),
                  _beacon_frame(_basic_id_msg('REG-BBBB', id_type=2), _LOC_MSG),
                  _beacon_frame(_basic_id_msg('SER-AAAA', id_type=1), _LOC_MSG)]
        keys = []
        for f in frames:
            engine._process_pcap_frame(_radiotap(f))
            keys.append(list(engine._active_drones))
        # Registration first, then the serial takes over and stays.
        self.assertEqual(keys, [['REG-BBBB'], ['SER-AAAA'], ['SER-AAAA'], ['SER-AAAA']])
        d = engine._active_drones['SER-AAAA']
        self.assertEqual(d.get_key(), 'SER-AAAA')
        self.assertEqual(d.registration_id, 'REG-BBBB')

    def test_ssid_scanner_entry_is_not_merged(self):
        engine, _ = _make_engine()
        ssid = DroneIDDevice(serial_number='DJI-AP [ssid]', mac_address=BEACON_MAC,
                             self_id_text='DJI-AP [ssid]', protocol=Protocol.WIFI_SSID.value)
        engine._track_device(ssid)
        engine._process_pcap_frame(_radiotap(_beacon_frame(_LOC_MSG)))
        d = engine._active_drones[BEACON_MAC]
        self.assertEqual(d.serial_number, '')
        self.assertEqual(d.self_id_text, '')


class TestSingleNewDroneAlertAcrossRekey(unittest.TestCase):
    """MAC-keyed first sighting, then a Basic ID: one drone, one new_drone alert."""

    def _wire(self, delay):
        engine, _ = _make_engine()
        adb = MagicMock()
        adb.get_setting.side_effect = lambda k, default='': (
            'true' if k.startswith('alert_') and k.endswith('_enabled')
            else '' if k in ('alert_rules', 'vendor_serial_prefixes', 'vendor_mac_oui')
            else default)
        adb.insert_alert.return_value = 1
        alerts = AlertEngine(adb, gps_engine=None)
        alerts._new_drone_delay = delay
        engine.on_detection = alerts.evaluate
        return engine, alerts

    def _new_drone_alerts(self, alerts):
        return [a.get('detail') for a in alerts.get_pending_alerts()
                if a.get('alert_type') == AlertType.NEW_DRONE.value]

    def _feed(self, engine):
        for f in (NAN_LOCATION, NAN_SYSTEM, _basic_id_frame('SERIAL-NAN-01'), NAN_LOCATION):
            engine._process_pcap_frame(_radiotap(f))

    def test_pending_alert_is_rekeyed_and_fires_once_with_serial(self):
        engine, alerts = self._wire(delay=60.0)
        self._feed(engine)
        alerts._new_drone_delay = 0.0
        alerts.flush_pending_new()
        self.assertEqual(self._new_drone_alerts(alerts), ['First detection of SERIAL-NAN-01'])

    def test_registration_then_serial_fires_once(self):
        engine, alerts = self._wire(delay=60.0)
        reg = _basic_id_frame('REG-BBBB')
        start = reg.index(bytes.fromhex('f01901')) + 3
        reg = reg[:start] + _basic_id_msg('REG-BBBB', id_type=2) + reg[start + 25:]
        for f in (NAN_LOCATION, reg, NAN_SYSTEM, _basic_id_frame('SER-AAAA'), NAN_LOCATION):
            engine._process_pcap_frame(_radiotap(f))
        self.assertEqual(list(engine._active_drones), ['SER-AAAA'])
        alerts._new_drone_delay = 0.0
        alerts.flush_pending_new()
        self.assertEqual(self._new_drone_alerts(alerts), ['First detection of SER-AAAA'])

    def test_already_announced_mac_is_not_announced_again(self):
        engine, alerts = self._wire(delay=0.0)
        self._feed(engine)
        alerts.flush_pending_new()
        self.assertEqual(self._new_drone_alerts(alerts), [f'First detection of {MAC}'])


if __name__ == '__main__':
    unittest.main()
