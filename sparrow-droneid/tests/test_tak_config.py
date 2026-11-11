"""TAK settings: defaults, merge rules, validation edges, normalisation, DB seeding."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tak_support  # noqa: E402,F401  (sets sys.path)

from backend.database import Database  # noqa: E402
from backend.tak import config as c  # noqa: E402


def raw(**kw):
    r = dict(c.DEFAULTS)
    for k, v in kw.items():
        r[k] = ('true' if v else 'false') if isinstance(v, bool) else str(v)
    return r


def fields(errors):
    return {e['field'] for e in errors}


class TestDefaults(unittest.TestCase):

    def test_defaults_match_plan(self):
        d = c.DEFAULTS
        self.assertEqual(d['cot_enabled'], 'false')
        self.assertEqual(d['cot_address'], '239.2.3.1')
        self.assertEqual(d['cot_port'], '6969')
        self.assertEqual(d['tak_transport'], 'udp_multicast')
        self.assertEqual(d['tak_multicast_ttl'], '1')
        self.assertEqual(d['tak_uid_prefix'], 'sparrow-droneid')
        self.assertEqual(d['tak_tls_client_mode'], 'upload')
        self.assertEqual(d['tak_tls_ca_mode'], 'none')
        self.assertEqual(d['tak_enroll_port'], '8446')
        self.assertEqual([d[k] for k in ('tak_send_interval_s', 'tak_refresh_interval_s',
                                         'tak_stale_after_s', 'tak_delete_after_s',
                                         'tak_sensor_heartbeat_s')],
                         ['2', '10', '30', '180', '10'])
        self.assertEqual(d['tak_unknown_affiliation'], 'u')
        self.assertEqual(d['tak_drone_type_family'], 'uav')
        self.assertEqual(d['tak_sensor_stale_s'], '')
        for k in ('tak_send_deletes', 'tak_operator_enabled', 'tak_line_enabled',
                  'tak_sensor_enabled', 'tak_tls_verify', 'tak_tls_check_hostname'):
            self.assertEqual(d[k], 'true', k)
        self.assertEqual(d['tak_include_ssid_detections'], 'false')

    def test_key_sets(self):
        self.assertEqual(len(c.TAK_KEYS), 36)
        self.assertEqual(len(set(c.TAK_KEYS)), 36)
        self.assertEqual(c.TAK_KEYS[:3], ('cot_enabled', 'cot_address', 'cot_port'))
        self.assertEqual(c.SECRET_KEYS, {'tak_auth_password', 'tak_tls_client_password',
                                         'tak_tls_ca_password', 'tak_enroll_password'})
        self.assertEqual(c.DEFAULT_PORT_BY_TRANSPORT['tls'], 8089)
        self.assertEqual(c.validate(raw()), [])

    def test_default_config_is_not_enabled(self):
        cfg, warnings = c.normalize(raw(), hostname='rx1')
        self.assertFalse(cfg.enabled)
        self.assertEqual(warnings, [])
        self.assertEqual(cfg.station_name, 'rx1')
        self.assertEqual(cfg.client_uid, 'sparrow-droneid-rx1')
        self.assertEqual(cfg.sensor_stale_s, 30.0)


class TestMergePatch(unittest.TestCase):

    def test_secret_rules(self):
        stored = raw(tak_auth_password='old', tak_enroll_password='keepme')
        m = c.merge_patch(stored, {'tak_auth_password': '(set)'})
        self.assertEqual(m['tak_auth_password'], 'old')
        m = c.merge_patch(stored, {'tak_enroll_password': ''})
        self.assertEqual(m['tak_enroll_password'], '')
        m = c.merge_patch(stored, {'tak_auth_password': ' new pw '})
        self.assertEqual(m['tak_auth_password'], ' new pw ')        # not stripped
        m = c.merge_patch(stored, {'cot_port': 8089})
        self.assertEqual(m['tak_auth_password'], 'old')             # absent = keep
        self.assertEqual(m['cot_port'], '8089')

    def test_types_and_ignored_keys(self):
        m = c.merge_patch(raw(), {'cot_enabled': True, 'tak_send_interval_s': 2.5,
                                  'cot_address': '  10.0.0.1 ', 'tak_station_name': None,
                                  'not_a_tak_key': 'x'})
        self.assertEqual(m['cot_enabled'], 'true')
        self.assertEqual(m['tak_send_interval_s'], '2.5')
        self.assertEqual(m['cot_address'], '10.0.0.1')
        self.assertEqual(m['tak_station_name'], '')
        self.assertNotIn('not_a_tak_key', m)

    def test_masked(self):
        out = c.masked(raw(tak_auth_password='x', tak_enroll_password='', cot_port=7000,
                           tak_send_interval_s=3, cot_enabled=True))
        self.assertEqual(out['tak_auth_password'], '(set)')
        self.assertEqual(out['tak_enroll_password'], '')
        self.assertEqual(out['cot_port'], 7000)
        self.assertEqual(out['tak_send_interval_s'], 3.0)
        self.assertIs(out['cot_enabled'], True)
        self.assertEqual(out['tak_sensor_stale_s'], '')
        self.assertEqual(set(out), set(c.TAK_KEYS))


class TestValidateRanges(unittest.TestCase):

    def check(self, key, ok, bad, **extra):
        for v in ok:
            self.assertNotIn(key, fields(c.validate(raw(**{key: v}, **extra))), (key, v))
        for v in bad:
            self.assertIn(key, fields(c.validate(raw(**{key: v}, **extra))), (key, v))

    def test_port_edges(self):
        self.check('cot_port', [1, 65535], [0, 65536, 'abc', ''])
        self.check('tak_enroll_port', [1, 65535], [0, 65536])

    def test_ttl_edges(self):
        self.check('tak_multicast_ttl', [1, 255], [0, 256])

    def test_send_interval_edges(self):
        self.check('tak_send_interval_s', [0.5, 2, 60], [0.49, 60.1, 'nan', 'inf'],
                   tak_refresh_interval_s=300, tak_stale_after_s=300, tak_delete_after_s=300)

    def test_refresh_edges(self):
        self.check('tak_refresh_interval_s', [2, 10, 300], [0.99, 300.1, 1],
                   tak_send_interval_s=2, tak_stale_after_s=300, tak_delete_after_s=300)

    def test_delete_cap(self):
        self.check('tak_delete_after_s', [180, 300], [301, 'x'])

    def test_heartbeat_edges(self):
        self.check('tak_sensor_heartbeat_s', [1, 300], [0.9, 300.1])

    def test_enum_and_format(self):
        self.check('tak_transport', ['tcp', 'tls'], ['bogus', ''])
        self.check('tak_tls_client_mode', ['upload', 'path', 'enroll'], ['x'])
        self.check('tak_tls_ca_mode', ['none', 'upload', 'path'], ['x'])
        self.check('tak_unknown_affiliation', ['u', 'n'], ['f', 'h'])
        self.check('tak_drone_type_family', ['uav', 'civil'], ['x'])
        self.check('tak_multicast_interface', ['', 'eth0', 'wlan0.100'], ['eth0 ', 'a' * 16, 'a/b'])
        self.check('tak_client_uid', ['', 'a.b_c:d-1'], ['has space', 'x' * 129])
        self.check('tak_uid_prefix', ['sparrow-droneid', 'a'], ['', 'a b', 'x' * 49])
        self.check('tak_station_name', ['', 'Rx 1', 'x' * 64], ['x' * 65, 'a\x01b'])
        self.check('tak_auth_username', ['', 'x' * 128], ['x' * 129, 'a\x00b'])
        self.check('tak_auth_password', ['x' * 256], ['x' * 257])
        self.check('tak_tls_client_cert_path', ['/a' * 100], ['x' * 4097])
        self.check('tak_send_deletes', ['true', 'false'], ['maybe'])

    def test_sensor_stale(self):
        self.check('tak_sensor_stale_s', ['', 30, 86400, 45.5], [29.9, 86401, 'abc'])
        # lower bound follows the effective heartbeat: ceil(20) * 3 = 60
        self.check('tak_sensor_stale_s', [60], [59], tak_sensor_heartbeat_s=20)
        self.check('tak_sensor_stale_s', [33], [32], tak_sensor_heartbeat_s=10.5)


class TestCrossField(unittest.TestCase):

    def test_stale_vs_three_times_max(self):
        self.assertEqual(c.validate(raw(tak_stale_after_s=30)), [])
        errs = c.validate(raw(tak_stale_after_s=29))
        self.assertEqual(fields(errs), {'tak_stale_after_s'})
        self.assertIn('3 x max(send interval, refresh interval) = 30 s', errs[0]['message'])
        # refresh 20 raises the floor to 60
        self.assertIn('tak_stale_after_s', fields(c.validate(raw(tak_refresh_interval_s=20))))
        self.assertEqual(c.validate(raw(tak_refresh_interval_s=20, tak_stale_after_s=60)), [])
        # send larger than refresh is impossible
        self.assertIn('tak_refresh_interval_s',
                      fields(c.validate(raw(tak_send_interval_s=12, tak_refresh_interval_s=10,
                                            tak_stale_after_s=120))))

    def test_stale_not_above_delete(self):
        errs = c.validate(raw(tak_stale_after_s=200, tak_delete_after_s=180))
        self.assertIn('tak_stale_after_s', fields(errs))
        self.assertIn('tak_delete_after_s', fields(errs))
        self.assertEqual(c.validate(raw(tak_stale_after_s=180, tak_delete_after_s=180)), [])

    def test_address_rules_per_transport(self):
        self.assertIn('cot_address', fields(c.validate(raw(cot_address='10.0.0.1'))))   # not multicast
        self.assertIn('cot_address', fields(c.validate(raw(cot_address='takserver.local'))))
        self.assertEqual(c.validate(raw(cot_address='224.0.0.1')), [])
        self.assertEqual(c.validate(raw(cot_address='239.255.255.255')), [])
        self.assertIn('cot_address', fields(c.validate(raw(cot_address='240.0.0.1'))))
        # broadcast: any IPv4 literal, no hostnames
        self.assertEqual(c.validate(raw(tak_transport='udp_broadcast', cot_address='192.168.1.255')), [])
        self.assertIn('cot_address', fields(c.validate(raw(tak_transport='udp_broadcast', cot_address='host'))))
        # unicast / tcp / tls: hostnames, IPv4, IPv6
        for t in ('udp_unicast', 'tcp', 'tls'):
            for a in ('takserver.local', '10.1.2.3', '::1', 'a-b.example.org'):
                self.assertEqual(c.validate(raw(tak_transport=t, cot_address=a)), [], (t, a))
            for a in ('bad host', '-a.com', 'a..b'):
                self.assertIn('cot_address', fields(c.validate(raw(tak_transport=t, cot_address=a))), (t, a))

    def test_blank_address_only_matters_when_enabled(self):
        self.assertEqual(c.validate(raw(cot_address='')), [])
        self.assertIn('cot_address', fields(c.validate(raw(cot_address='', cot_enabled=True))))

    def test_enroll_credentials_only_when_tls_enroll_enabled(self):
        base = dict(tak_transport='tls', tak_tls_client_mode='enroll', cot_address='h.example')
        self.assertEqual(c.validate(raw(**base)), [])                       # disabled
        self.assertEqual(c.validate(raw(cot_enabled=True, tak_transport='tcp',
                                        tak_tls_client_mode='enroll', cot_address='h.example')), [])
        errs = c.validate(raw(cot_enabled=True, **base))
        self.assertEqual(fields(errs), {'tak_enroll_username', 'tak_enroll_password'})
        self.assertEqual(c.validate(raw(cot_enabled=True, tak_enroll_username='u',
                                        tak_enroll_password='p', **base)), [])

    def test_path_required_only_for_tls(self):
        self.assertEqual(c.validate(raw(tak_tls_client_mode='path', tak_tls_ca_mode='path')), [])
        tls = dict(tak_transport='tls', cot_address='h.example')
        errs = c.validate(raw(tak_tls_client_mode='path', tak_tls_ca_mode='path', **tls))
        self.assertEqual(fields(errs), {'tak_tls_client_cert_path', 'tak_tls_ca_path'})
        self.assertEqual(c.validate(raw(tak_tls_client_mode='path', tak_tls_ca_mode='path',
                                        tak_tls_client_cert_path='/c', tak_tls_ca_path='/ca', **tls)), [])


class TestNormalize(unittest.TestCase):

    def test_hand_edited_db_is_clamped_never_raises(self):
        cfg, warnings = c.normalize(raw(
            tak_stale_after_s='1e12', tak_delete_after_s=900, tak_multicast_ttl=0,
            tak_transport='bogus', cot_port='abc', tak_send_interval_s='-5',
            tak_sensor_heartbeat_s='nope', tak_unknown_affiliation='z', cot_enabled='maybe'),
            hostname='h')
        self.assertEqual(cfg.transport, 'udp_multicast')
        self.assertEqual(cfg.multicast_ttl, 1)
        self.assertEqual(cfg.port, 6969)
        self.assertEqual(cfg.send_interval_s, 0.5)
        self.assertEqual(cfg.delete_after_s, 300.0)
        self.assertLessEqual(cfg.stale_after_s, cfg.delete_after_s)
        self.assertGreaterEqual(cfg.stale_after_s, 3 * max(cfg.send_interval_s, cfg.refresh_interval_s))
        self.assertEqual(cfg.sensor_heartbeat_s, 10.0)
        self.assertEqual(cfg.unknown_affiliation, 'u')
        self.assertFalse(cfg.enabled)
        keys = [w.split()[1] for w in warnings]
        self.assertEqual(len(keys), len(set(keys)))                 # one warning per key
        for k in ('tak_transport', 'tak_multicast_ttl', 'cot_port', 'tak_send_interval_s',
                  'tak_delete_after_s', 'tak_stale_after_s', 'tak_sensor_heartbeat_s',
                  'tak_unknown_affiliation', 'cot_enabled'):
            self.assertIn(k, keys)

    def test_delete_raised_when_stale_floor_exceeds_it(self):
        cfg, w = c.normalize(raw(tak_refresh_interval_s=40, tak_stale_after_s=60,
                                 tak_delete_after_s=100))
        self.assertEqual(cfg.refresh_interval_s, 40.0)
        self.assertEqual(cfg.delete_after_s, 120.0)
        self.assertEqual(cfg.stale_after_s, 120.0)

    def test_refresh_follows_send(self):
        cfg, w = c.normalize(raw(tak_send_interval_s=12, tak_refresh_interval_s=10))
        self.assertEqual(cfg.refresh_interval_s, 12.0)
        self.assertTrue(any('tak_refresh_interval_s' in x for x in w))

    def test_auto_sensor_stale(self):
        for hb, want in ((10, 30.0), (10.5, 33.0), (20, 60.0), (1, 30.0)):
            cfg, _ = c.normalize(raw(tak_sensor_heartbeat_s=hb))
            self.assertEqual(cfg.sensor_stale_s, want, hb)
            self.assertEqual(c.auto_sensor_stale(hb), want)
        self.assertEqual(c.effective_sensor_heartbeat(10.2), 11.0)

    def test_sensor_stale_clamped(self):
        cfg, w = c.normalize(raw(tak_sensor_stale_s=1))
        self.assertEqual(cfg.sensor_stale_s, 30.0)
        self.assertTrue(w)
        cfg, w = c.normalize(raw(tak_sensor_stale_s=1e9))
        self.assertEqual(cfg.sensor_stale_s, 86400.0)

    def test_tls_check_hostname_forced_off_without_verify(self):
        cfg, _ = c.normalize(raw(tak_tls_verify=False, tak_tls_check_hostname=True))
        self.assertFalse(cfg.tls_check_hostname)
        cfg, _ = c.normalize(raw())
        self.assertTrue(cfg.tls_check_hostname)

    def test_hostname_defaults_and_equality(self):
        a, _ = c.normalize(raw(), hostname='raven24')
        b, _ = c.normalize(raw(), hostname='raven24')
        self.assertEqual(a, b)
        self.assertEqual(a.client_uid, 'sparrow-droneid-raven24')
        d, _ = c.normalize(raw(tak_station_name='Alpha', tak_client_uid='my.uid'), hostname='x')
        self.assertEqual((d.station_name, d.client_uid), ('Alpha', 'my.uid'))
        e, _ = c.normalize(raw(cot_port=7000), hostname='raven24')
        self.assertNotEqual(a, e)

    def test_repr_hides_secrets(self):
        cfg, _ = c.normalize(raw(tak_auth_password='hunter2', tak_enroll_password='hunter3'))
        self.assertNotIn('hunter', repr(cfg))


class TestDatabaseSeeding(unittest.TestCase):

    def test_fresh_db_seeds_every_key_ttl_1(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, 'a.db'))
            allv = db.get_all_settings()
            for key, default in c.DEFAULTS.items():
                self.assertIn(key, allv, key)
                self.assertEqual(allv[key], default, key)
            self.assertEqual(allv['tak_multicast_ttl'], '1')
            self.assertEqual(c.read_raw(db), {k: allv[k] for k in c.TAK_KEYS})

    def test_upgrade_seeds_ttl_32(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'old.db')
            db = Database(path)
            conn = sqlite3.connect(path)
            conn.execute("DELETE FROM settings WHERE key LIKE 'tak\\_%' ESCAPE '\\'")
            conn.execute("UPDATE settings SET value='7000' WHERE key='cot_port'")
            conn.commit()
            conn.close()
            db2 = Database(path)                       # re-runs _init_defaults
            allv = db2.get_all_settings()
            self.assertEqual(allv['tak_multicast_ttl'], '32')
            self.assertEqual(allv['cot_port'], '7000')                 # untouched
            self.assertEqual(allv['tak_transport'], 'udp_multicast')
            self.assertEqual(Database(path).get_all_settings()['tak_multicast_ttl'], '32')

    def test_operator_changed_ttl_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.db')
            db = Database(path)
            db.set_setting('tak_multicast_ttl', '5')
            self.assertEqual(Database(path).get_setting('tak_multicast_ttl'), '5')


if __name__ == '__main__':
    unittest.main()
