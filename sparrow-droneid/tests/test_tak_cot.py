"""CoT XML builders: exact output, type matrix, escaping, stale clamping, UIDs."""
import itertools
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import FakeClock, default_cfg, make_device  # noqa: E402

from backend.tak import cot  # noqa: E402

NOW = datetime(2026, 11, 11, 14, 0, 2, tzinfo=timezone.utc)
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def full_device(**kw):
    fields = dict(
        serial_number='1581F5FHD23450019', registration_id='FA3XYZ', id_type=1, ua_type=2,
        drone_lat=32.7157, drone_lon=-117.1611, drone_alt_geo=152.0, drone_height_agl=45.0,
        speed=5.25, direction=240.0, vertical_speed=0.5, operator_lat=32.71,
        operator_lon=-117.17, operator_id='FIN87astrdge12k8', self_id_text='Survey flight',
        mac_address='60:60:1F:AA:BB:CC', rssi=-68, protocol='astm_ble',
        last_seen='2026-11-11T14:00:01Z', disposition='threat', law_enforcement=True)
    fields.update(kw)
    from backend.models import DroneIDDevice
    return DroneIDDevice(**fields)


CFG = default_cfg()


def parse(b):
    return ET.fromstring(b)


class TestExactXml(unittest.TestCase):

    def setUp(self):
        self.dev = full_device()
        self.view = cot.drone_view(self.dev.get_key(), self.dev, CFG)

    def test_drone_exact(self):
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" '
            'type="a-h-A-M-F-Q-r" time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:31.000Z" how="m-g">'
            '<point lat="32.7157000" lon="-117.1611000" hae="152.0" ce="10.0" le="15.0"/>'
            '<detail><contact callsign="UAS-450019"/><track course="240.0" speed="5.25"/>'
            '<remarks>Remote ID drone (Sparrow DroneID)\n'
            'ID: 1581F5FHD23450019 (Serial Number (ANSI/CTA-2063-A))\n'
            'Registration: FA3XYZ\nMAC: 60:60:1F:AA:BB:CC\nType: Helicopter / Multirotor\n'
            'Class: civil\nFlags: law enforcement\nDisposition: threat\n'
            'Altitude: 152.0 m HAE, 45.0 m AGL\n'
            'Speed: 5.25 m/s, course 240 deg, vertical 0.5 m/s\n'
            'Operator ID: FIN87astrdge12k8\nSelf-ID: Survey flight\n'
            'RSSI: -68 dBm (astm_ble)\nLast seen: 2026-11-11T14:00:01Z\nSensor: rx1</remarks>'
            '<__droneid serial="1581F5FHD23450019" registration_id="FA3XYZ" '
            'mac="60:60:1F:AA:BB:CC" key="1581F5FHD23450019" ua_type="2" height_agl="45.0" '
            'operator_lat="32.7100000" operator_lon="-117.1700000" '
            'operator_id="FIN87astrdge12k8" self_id="Survey flight" rssi="-68" '
            'protocol="astm_ble" disposition="threat" military="false" '
            'law_enforcement="true"/></detail></event>')
        self.assertEqual(cot.build_drone(self.view, self.dev, CFG, NOW).decode(), expected)

    def test_operator_exact(self):
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-op" '
            'type="a-h-G" time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:31.000Z" how="m-g">'
            '<point lat="32.7100000" lon="-117.1700000" hae="9999999.0" ce="10.0" le="9999999.0"/>'
            '<detail><contact callsign="OP-450019"/>'
            '<link uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" type="a-h-A-M-F-Q-r" '
            'relation="p-p"/><remarks>Remote ID operator position for UAS-450019\n'
            'Operator ID: FIN87astrdge12k8\nSensor: rx1</remarks></detail></event>')
        self.assertEqual(cot.build_operator(self.view, self.dev, NOW).decode(), expected)

    def test_operator_altitude_used_when_present(self):
        dev = full_device(operator_alt=33.0)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        self.assertIn('hae="33.0"', cot.build_operator(view, dev, NOW).decode())

    def test_takeoff_exact(self):
        dev = full_device(operator_lat=0.0, operator_lon=0.0, takeoff_lat=48.85,
                          takeoff_lon=2.35, operator_id='')
        view = cot.drone_view(dev.get_key(), dev, CFG)
        self.assertTrue(view.operator_is_takeoff)
        self.assertEqual(view.operator_callsign, 'HOME-450019')
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-op" '
            'type="a-h-G" time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:31.000Z" how="m-g">'
            '<point lat="48.8500000" lon="2.3500000" hae="9999999.0" ce="10.0" le="9999999.0"/>'
            '<detail><contact callsign="HOME-450019"/>'
            '<link uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" type="a-h-A-M-F-Q-r" '
            'relation="p-p"/><remarks>Takeoff point for UAS-450019 (no live operator position)\n'
            'Sensor: rx1</remarks></detail></event>')
        self.assertEqual(cot.build_operator(view, dev, NOW).decode(), expected)
        line = cot.build_line(view, dev, NOW).decode()
        self.assertIn('<remarks>UAS-450019 to HOME-450019</remarks>', line)
        self.assertIn('<link point="48.8500000,2.3500000"/>', line)

    def test_line_exact(self):
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-line" '
            'type="u-d-f" time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:31.000Z" how="h-e">'
            '<point lat="32.7157000" lon="-117.1611000" hae="9999999.0" ce="9999999.0" '
            'le="9999999.0"/><detail><link point="32.7157000,-117.1611000"/>'
            '<link point="32.7100000,-117.1700000"/>'
            '<__shapeExtras cpvis="true" editable="false"/><labels_on value="false"/>'
            '<contact callsign="UAS-450019 link"/><remarks>UAS-450019 to OP-450019</remarks>'
            '<color value="-65536"/><strokeColor value="-65536"/><strokeWeight value="2.0"/>'
            '<strokeStyle value="dashed"/></detail></event>')
        self.assertEqual(cot.build_line(self.view, self.dev, NOW).decode(), expected)

    def test_line_colour_per_affiliation(self):
        for disp, aff, color in (('threat', 'h', '-65536'), ('friendly', 'f', '-16711681'),
                                 ('unknown', 'u', '-256')):
            dev = full_device(disposition=disp)
            view = cot.drone_view(dev.get_key(), dev, CFG)
            self.assertEqual(view.affiliation, aff)
            self.assertIn('<color value="%s"/>' % color, cot.build_line(view, dev, NOW).decode())
        cfg_n = default_cfg(tak_unknown_affiliation='n')
        dev = full_device(disposition='unknown')
        view = cot.drone_view(dev.get_key(), dev, cfg_n)
        self.assertIn('<color value="-16711936"/>', cot.build_line(view, dev, NOW).decode())

    def test_sensor_gpsd_and_static_exact(self):
        gps = {'mode': 'gpsd', 'fix': True, 'latitude': 32.7, 'longitude': -117.15,
               'altitude': 12.0}
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-sensor" type="a-f-G-E-S" '
            'time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:32.000Z" how="m-g">'
            '<point lat="32.7000000" lon="-117.1500000" hae="12.0" ce="10.0" le="15.0"/>'
            '<detail><contact callsign="rx1"/><remarks>Sparrow DroneID Remote ID receiver\n'
            'Drones tracked: 3</remarks></detail></event>')
        self.assertEqual(cot.build_sensor(CFG, gps, NOW, 3).decode(), expected)
        static = dict(gps, mode='static', altitude=0.0)
        out = cot.build_sensor(CFG, static, NOW, 0).decode()
        self.assertIn('how="h-e"', out)
        self.assertIn('hae="9999999.0" ce="9999999.0"', out)

    def test_sensor_stale_uses_configured_value(self):
        cfg = default_cfg(tak_sensor_stale_s=120)
        out = cot.build_sensor(cfg, {'mode': 'gpsd', 'latitude': 1.0, 'longitude': 2.0,
                                     'altitude': 0.0}, NOW).decode()
        self.assertIn('stale="2026-11-11T14:02:02.000Z"', out)

    def test_delete_exact(self):
        expected = (
            HDR + '<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-delete" '
            'type="t-x-d-d" time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z" '
            'stale="2026-11-11T14:00:22.000Z" how="h-e">'
            '<point lat="0.0" lon="0.0" hae="0.0" ce="9999999.0" le="9999999.0"/>'
            '<detail><link uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" relation="none" '
            'type="a-h-A-M-F-Q-r"/><__forcedelete/></detail></event>')
        self.assertEqual(
            cot.build_delete('sparrow-droneid-rx1-drone-1581F5FHD23450019',
                             'a-h-A-M-F-Q-r', NOW).decode(), expected)

    def test_wifi_ssid_variant(self):
        dev = full_device(protocol='wifi_ssid', speed=0.0, direction=0.0)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        out = cot.build_drone(view, dev, CFG, NOW).decode()
        self.assertIn('ce="500.0" le="9999999.0"', out)
        self.assertNotIn('<track', out)
        remarks = parse(out.encode()).find('detail/remarks').text
        self.assertTrue(remarks.startswith(
            "Wi-Fi SSID detection (no Remote ID); position is the receiver's\n"))

    def test_track_omitted_when_stationary(self):
        dev = full_device(speed=0.0, direction=0.0)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        self.assertNotIn('<track', cot.build_drone(view, dev, CFG, NOW).decode())

    def test_optional_remark_lines_omitted(self):
        dev = full_device(registration_id='', mac_address='', operator_id='', self_id_text='',
                          law_enforcement=False, drone_alt_geo=0.0, drone_height_agl=0.0)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        text = cot.build_drone(view, dev, CFG, NOW).decode()
        for absent in ('Registration:', 'MAC:', 'Operator ID:', 'Self-ID:', 'Flags:', 'm AGL'):
            self.assertNotIn(absent, text.split('<__droneid')[0])
        self.assertIn('Altitude: unknown HAE\n', text)
        self.assertIn('hae="9999999.0"', text)


class TestTypes(unittest.TestCase):

    def test_type_matrix(self):
        rotary = {2, 3, 13}
        for disp, unk, family, ua in itertools.product(
                ('friendly', 'threat', 'unknown'), ('u', 'n'), ('uav', 'civil'),
                (0, 1, 2, 3, 4, 13)):
            cfg = default_cfg(tak_unknown_affiliation=unk, tak_drone_type_family=family)
            dev = full_device(disposition=disp, ua_type=ua)
            view = cot.drone_view(dev.get_key(), dev, cfg)
            aff = {'friendly': 'f', 'threat': 'h'}.get(disp, unk)
            if family == 'uav':
                want = 'a-%s-A-M-F-Q%s' % (aff, '-r' if ua in rotary else '')
            else:
                want = 'a-%s-A-C-H' % aff if ua in rotary else 'a-%s-A-C-F-q' % aff
            self.assertEqual(view.drone_type, want, (disp, unk, family, ua))
            self.assertEqual(view.operator_type, 'a-%s-G' % aff)

    def test_flags_change_remarks_and_droneid_never_type(self):
        base = full_device(military=False, law_enforcement=False, disposition='unknown')
        mil = full_device(military=True, law_enforcement=True, disposition='unknown')
        v0 = cot.drone_view(base.get_key(), base, CFG)
        v1 = cot.drone_view(mil.get_key(), mil, CFG)
        self.assertEqual(v0.drone_type, v1.drone_type)
        self.assertNotEqual(v0.type_sig, v1.type_sig)
        out = cot.build_drone(v1, mil, CFG, NOW).decode()
        self.assertIn('Class: military (operator flag)', out)
        self.assertIn('Flags: military, law enforcement', out)
        self.assertIn('military="true" law_enforcement="true"', out)
        self.assertIn('Class: civil', cot.build_drone(v0, base, CFG, NOW).decode())


class TestEscaping(unittest.TestCase):

    def test_hostile_strings_parse_and_are_escaped(self):
        evil = 'a<b>&"\'c\x00\x0b\x1f\ud800'.replace('\ud800', '') + '\nINJECT: x'
        dev = full_device(self_id_text=evil, operator_id=evil, registration_id=evil,
                          mac_address=evil)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        for b in (cot.build_drone(view, dev, CFG, NOW), cot.build_operator(view, dev, NOW)):
            root = parse(b)            # must be well-formed XML 1.0
            text = b.decode()
            self.assertNotIn('\x00', text)
            self.assertNotIn('\x0b', text)
        drone = parse(cot.build_drone(view, dev, CFG, NOW))
        sid = drone.find('detail/__droneid').get('self_id')
        self.assertEqual(sid, 'a<b>&"\'c INJECT: x')
        # the newline was flattened, so it cannot forge a remarks line
        self.assertNotIn('\nINJECT', drone.find('detail/remarks').text)

    def test_truncated_to_64_characters(self):
        dev = full_device(self_id_text='x' * 200)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        root = parse(cot.build_drone(view, dev, CFG, NOW))
        self.assertEqual(root.find('detail/__droneid').get('self_id'), 'x' * 64)

    def test_xml_helpers(self):
        self.assertEqual(cot.xml_attr('<&">\''), '&lt;&amp;&quot;&gt;&apos;')
        self.assertEqual(cot.xml_text('a\x00b\x01c\td'), 'abc\td')
        self.assertEqual(cot.clean_field('a\x00\tb'), 'a b')


class TestStaleAndTime(unittest.TestCase):

    def test_stale_clamped(self):
        past = NOW - timedelta(hours=1)
        self.assertEqual(cot._stale_time(past, NOW), NOW + timedelta(seconds=1))
        far = NOW + timedelta(days=30)
        self.assertEqual(cot._stale_time(far, NOW), NOW + timedelta(seconds=86400))
        ok = NOW + timedelta(seconds=30)
        self.assertEqual(cot._stale_time(ok, NOW), ok)

    def test_old_drone_stale_is_at_least_one_second_ahead(self):
        dev = full_device(last_seen='2026-11-11T10:00:00Z')
        view = cot.drone_view(dev.get_key(), dev, CFG)
        out = cot.build_drone(view, dev, CFG, NOW).decode()
        self.assertIn('stale="2026-11-11T14:00:03.000Z"', out)

    def test_huge_sensor_stale_clamped(self):
        cfg = default_cfg(tak_sensor_stale_s=86400)
        out = cot.build_sensor(cfg, {'mode': 'gpsd', 'latitude': 1.0, 'longitude': 1.0,
                                     'altitude': 0}, NOW).decode()
        self.assertIn('stale="2026-11-12T14:00:02.000Z"', out)

    def test_stale_is_anchored_to_last_seen(self):
        dev = full_device(last_seen='2026-11-11T14:00:01Z')
        view = cot.drone_view(dev.get_key(), dev, default_cfg(tak_stale_after_s=45))
        self.assertEqual(view.stale_at, datetime(2026, 11, 11, 14, 0, 46, tzinfo=timezone.utc))

    def test_unparsable_last_seen_raises(self):
        with self.assertRaises(ValueError):
            cot.drone_view('k', full_device(last_seen='garbage'), CFG)

    def test_fmt_time_millis(self):
        dt = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
        self.assertEqual(cot.fmt_time(dt), '2026-01-02T03:04:05.678Z')


class TestUidsAndNumbers(unittest.TestCase):

    def test_uid_sanitisation_and_callsign_tail(self):
        dev = full_device(serial_number='', registration_id='', mac_address='60:60:1F:AA:BB:CC')
        view = cot.drone_view(dev.get_key(), dev, CFG)
        self.assertEqual(view.drone_uid, 'sparrow-droneid-rx1-drone-60_60_1F_AA_BB_CC')
        self.assertEqual(view.operator_uid, view.drone_uid + '-op')
        self.assertEqual(view.line_uid, view.drone_uid + '-line')
        self.assertEqual(view.callsign, 'UAS-AABBCC')

    def test_tail_fallback_unkn(self):
        dev = full_device(serial_number='', registration_id='', mac_address='::')
        self.assertEqual(cot.id_tail(dev), 'UNKN')
        self.assertEqual(cot.sanitize_uid_part(''), 'unknown')
        self.assertEqual(len(cot.sanitize_uid_part('x' * 300)), 96)

    def test_station_in_uid(self):
        cfg = default_cfg(tak_station_name='My Rx/1')
        self.assertEqual(cot.sensor_uid(cfg), 'sparrow-droneid-My_Rx_1-sensor')

    def test_tails_distinguish_same_maker(self):
        a = full_device(serial_number='1581F5FHD23450019')
        b = full_device(serial_number='1581F5FHD23450020')
        self.assertNotEqual(cot.id_tail(a), cot.id_tail(b))

    def test_unknown_altitude_and_numbers(self):
        dev = full_device(drone_alt_geo=0.0)
        view = cot.drone_view(dev.get_key(), dev, CFG)
        self.assertIn('hae="9999999.0"', cot.build_drone(view, dev, CFG, NOW).decode())

    def test_droneid_keeps_legacy_attribute_names(self):
        dev = full_device()
        view = cot.drone_view(dev.get_key(), dev, CFG)
        el = parse(cot.build_drone(view, dev, CFG, NOW)).find('detail/__droneid')
        for name in ('serial', 'ua_type', 'height_agl', 'operator_lat', 'operator_lon',
                     'operator_id', 'self_id', 'rssi', 'protocol', 'registration_id', 'mac',
                     'key', 'disposition', 'military', 'law_enforcement'):
            self.assertIn(name, el.attrib)


if __name__ == '__main__':
    unittest.main()
