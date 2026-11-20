"""Tests for ODIDParser.parse_location (ODID Location/Vector message, type 1).

Guards the track direction decode. ASTM F3411 sends the direction as whole
degrees: byte 2 holds 0-179 and the E/W bit (byte 1, bit 1) adds 180
(opendroneid-core-c encodeDirection/decodeDirection). It is not a 2-degree
step value.

The first message below is the first Location message of the Open Drone ID
Wi-Fi beacon sample capture (odid_wifi_bcn_sample.pcap from
https://github.com/opendroneid/wireshark-dissector), whose track direction is
92 degrees.
"""
import unittest

from sparrow_droneid.backend.droneid_engine import ODIDParser
from sparrow_droneid.backend.models import DroneIDDevice

# Location message from odid_wifi_bcn_sample.pcap: direction byte 92, E/W bit 0.
SAMPLE_LOCATION = bytes.fromhex("10005c527ebcba251ba88cb4b60000aa099808394100000a00")


class TestODIDLocationParser(unittest.TestCase):
    def _parse(self, msg: bytes) -> DroneIDDevice:
        device = DroneIDDevice()
        ODIDParser.parse_location(msg, device)
        return device

    def test_sample_capture_direction(self):
        device = self._parse(SAMPLE_LOCATION)
        self.assertEqual(device.direction, 92.0)
        self.assertAlmostEqual(device.drone_lat, 45.5457468, places=7)
        self.assertAlmostEqual(device.drone_lon, -122.9681496, places=7)
        self.assertEqual(device.speed, 20.5)

    def test_west_half_adds_180(self):
        msg = bytearray(SAMPLE_LOCATION)
        msg[1] |= 0x02  # E/W direction bit
        msg[2] = 35
        self.assertEqual(self._parse(bytes(msg)).direction, 215.0)

    def test_east_half_is_whole_degrees(self):
        msg = bytearray(SAMPLE_LOCATION)
        msg[2] = 179
        self.assertEqual(self._parse(bytes(msg)).direction, 179.0)


if __name__ == "__main__":
    unittest.main()
