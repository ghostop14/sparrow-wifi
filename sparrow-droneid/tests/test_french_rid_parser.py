"""Tests for the French RemoteID TLV parser (FrenchRIDParser).

Reference: opendroneid-core-c wifi.c frdid_build() lines 717-772.
"""
import struct
import unittest

from sparrow_droneid.backend.droneid_engine import (
    FrenchRIDParser,
    FrameExtractor,
    OUI_FRENCH,
    FRENCH_OUI_TYPE,
)
from sparrow_droneid.backend.models import Protocol


def _tlv(t: int, value: bytes) -> bytes:
    return bytes([t, len(value)]) + value


def _build_frdid_payload(
    version: int = 1,
    identifier: str = "FRA-0001-DRONE-ID-TEST-VALUE00",
    ansi_cta: str = "1581F5XYZ789",
    lat_deg: float = 48.8584,
    lon_deg: float = 2.2945,
    altitude_m: int = 120,
    height_m: int = 95,
    takeoff_lat: float = 48.8580,
    takeoff_lon: float = 2.2940,
    h_speed: int = 15,
    true_course: int = 212,
) -> bytes:
    """Build a valid FR-DID TLV payload matching frdid_build()'s output."""
    out = b""
    out += _tlv(0x01, bytes([version]))
    # Identifier is a fixed 30-byte zero-padded ASCII field in the C impl.
    id30 = identifier.encode("ascii")[:30].ljust(30, b"\x00")
    out += _tlv(0x02, id30)
    out += _tlv(0x03, ansi_cta.encode("ascii"))
    out += _tlv(0x04, struct.pack(">i", int(lat_deg * 1e5 + 0.5)))
    out += _tlv(0x05, struct.pack(">i", int(lon_deg * 1e5 + 0.5)))
    out += _tlv(0x06, struct.pack(">h", altitude_m))
    out += _tlv(0x07, struct.pack(">h", height_m))
    out += _tlv(0x08, struct.pack(">i", int(takeoff_lat * 1e5 + 0.5)))
    out += _tlv(0x09, struct.pack(">i", int(takeoff_lon * 1e5 + 0.5)))
    out += _tlv(0x0A, struct.pack(">b", h_speed))
    out += _tlv(0x0B, struct.pack(">h", true_course))
    return out


class TestFrenchRIDParser(unittest.TestCase):
    def test_full_payload_decodes(self):
        payload = _build_frdid_payload()
        d = FrenchRIDParser.parse(payload)

        self.assertEqual(d.protocol, Protocol.FRENCH.value)
        self.assertEqual(d.serial_number, "1581F5XYZ789")
        self.assertEqual(d.id_type, 1)
        self.assertEqual(d.registration_id, "FRA-0001-DRONE-ID-TEST-VALUE00")
        self.assertAlmostEqual(d.drone_lat, 48.8584, places=4)
        self.assertAlmostEqual(d.drone_lon, 2.2945, places=4)
        self.assertEqual(d.drone_alt_geo, 120.0)
        self.assertEqual(d.drone_height_agl, 95.0)
        # Takeoff TLVs (0x08/0x09) map to takeoff_* — NOT operator_*. French
        # RID does not transmit live operator position.
        self.assertAlmostEqual(d.takeoff_lat, 48.8580, places=4)
        self.assertAlmostEqual(d.takeoff_lon, 2.2940, places=4)
        self.assertEqual(d.operator_lat, 0.0)
        self.assertEqual(d.operator_lon, 0.0)
        self.assertEqual(d.speed, 15.0)
        self.assertEqual(d.direction, 212.0)

    def test_identifier_without_ansi_cta(self):
        payload = _tlv(0x01, bytes([1]))
        payload += _tlv(0x02, b"FRONLY-ID".ljust(30, b"\x00"))
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.registration_id, "FRONLY-ID")
        self.assertEqual(d.id_type, 2)
        self.assertEqual(d.serial_number, "")

    def test_identifier_wrong_length_rejected(self):
        # Spec: 30 bytes exactly. A 15-byte Identifier TLV must not populate
        # registration_id — we'd rather reject than silently accept malformed.
        payload = _tlv(0x02, b"TOOSHORT-XYZ123")  # 15 bytes, not 30
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.registration_id, "")
        self.assertEqual(d.id_type, 0)

    def test_altitude_sentinel_treated_as_unknown(self):
        # -1000 matches opendroneid-core-c INV_ALT sentinel — must not be
        # decoded as "-1000m real altitude" or altitude-class logic mislabels.
        payload = _tlv(0x06, struct.pack(">h", -1000))
        payload += _tlv(0x07, struct.pack(">h", -1000))
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.drone_alt_geo, 0.0)
        self.assertEqual(d.drone_height_agl, 0.0)

    def test_ansi_cta_preferred_as_serial(self):
        payload = _build_frdid_payload()
        d = FrenchRIDParser.parse(payload)
        # get_key() should prefer the CTA-2063 serial
        self.assertEqual(d.get_key(), "1581F5XYZ789")

    def test_negative_altitude_and_height(self):
        payload = _tlv(0x01, bytes([1]))
        payload += _tlv(0x06, struct.pack(">h", -50))
        payload += _tlv(0x07, struct.pack(">h", -5))
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.drone_alt_geo, -50.0)
        self.assertEqual(d.drone_height_agl, -5.0)

    def test_negative_horizontal_speed(self):
        # int8 — descent/backward encoding path
        payload = _tlv(0x0A, struct.pack(">b", -12))
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.speed, -12.0)

    def test_course_wrap(self):
        payload = _tlv(0x0B, struct.pack(">h", 540))
        d = FrenchRIDParser.parse(payload)
        self.assertEqual(d.direction, 180.0)

    def test_truncated_payload_does_not_raise(self):
        # Declared length overruns available bytes — parser must stop gracefully.
        bad = bytes([0x04, 0x04, 0x00, 0x01])
        d = FrenchRIDParser.parse(bad)
        self.assertEqual(d.drone_lat, 0.0)

    def test_empty_payload(self):
        d = FrenchRIDParser.parse(b"")
        self.assertEqual(d.protocol, Protocol.FRENCH.value)
        self.assertEqual(d.get_key(), "")

    def test_frame_extractor_dispatches_to_french_parser(self):
        """End-to-end: assemble a minimal 802.11 beacon with FR-DID vendor IE."""
        payload = _build_frdid_payload()

        # Vendor IE: tag(221) + len + OUI(3) + OUI-type(1) + payload
        ie = bytes([221, 4 + len(payload)]) + OUI_FRENCH + bytes([FRENCH_OUI_TYPE]) + payload
        # Prepend 24-byte MAC header + 12-byte fixed beacon params.
        frame = b"\x00" * 24 + b"\x00" * 12 + ie

        d = FrameExtractor.extract_from_beacon_or_probe(frame)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.protocol, Protocol.FRENCH.value)
        self.assertEqual(d.serial_number, "1581F5XYZ789")


if __name__ == "__main__":
    unittest.main()
