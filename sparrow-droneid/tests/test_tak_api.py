"""TAK HTTP API: handlers called directly with a fake request, plus one real-socket check."""
import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import (FakeGps, FakeTransport, FakeEnrollment, build_zip, make_ca,  # noqa: E402
                         make_cert, make_p12, pem)

import backend.api_handler as api  # noqa: E402
from backend.database import Database  # noqa: E402
from backend.tak import config as tak_config  # noqa: E402
from backend.tak.engine import TakEngine  # noqa: E402


class FakeRequest:
    def __init__(self, json_data=None):
        self.json_data = json_data
        self.status = None
        self.body = None

    def _send_ok(self, payload, status=200):
        self.status = status
        self.body = payload

    def _send_error(self, status, code, msg, detail=None):
        self.status = status
        self.body = {'error': {'code': code, 'message': msg}}
        if detail:
            self.body['error']['detail'] = detail


def b64(data):
    return base64.b64encode(data).decode()


CA = make_ca('API CA')
CKEY, CCERT = make_cert(CA, 'apiuser', client=True)


class ApiCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, 'd.db'))
        FakeTransport.instances = []
        self.engine = TakEngine(
            cert_dir=os.path.join(self.tmp.name, 'tak_certs'), drone_source=lambda: [],
            gps_engine=FakeGps(), transport_factory=FakeTransport,
            enrollment_factory=FakeEnrollment, start_threads=False)
        self.addCleanup(self.engine.stop)
        for p in (patch.object(api, '_db', self.db), patch.object(api, '_cot_engine', self.engine),
                  patch.object(api, '_data_dir', self.tmp.name)):
            p.start()
            self.addCleanup(p.stop)

    def call(self, fn, body=None):
        req = FakeRequest(body)
        fn(req)
        return req

    def stored(self, key):
        return self.db.get_setting_str(key, '')


class TestSettingsApi(ApiCase):

    def test_get_masks_tak_secrets(self):
        for key in tak_config.SECRET_KEYS:
            self.db.set_setting(key, 'topsecret')
        body = self.call(api.api_settings_get).body['settings']
        for key in tak_config.SECRET_KEYS:
            self.assertEqual(body[key], '(set)', key)
        self.db.set_setting('tak_auth_password', '')
        self.assertEqual(self.call(api.api_settings_get).body['settings']['tak_auth_password'], '')
        self.assertIs(body['tak_send_deletes'], True)
        self.assertEqual(body['tak_send_interval_s'], 2.0)
        self.assertEqual(body['tak_multicast_ttl'], 1)

    def test_put_mask_keeps_and_empty_clears(self):
        self.db.set_setting('tak_auth_password', 'hunter2')
        r = self.call(api.api_settings_put, {'tak_auth_password': '(set)', 'cot_port': 7001})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.stored('tak_auth_password'), 'hunter2')
        self.assertEqual(self.stored('cot_port'), '7001')
        self.call(api.api_settings_put, {'tak_auth_password': ''})
        self.assertEqual(self.stored('tak_auth_password'), '')
        self.call(api.api_settings_put, {'tak_auth_password': 'new'})
        self.assertEqual(self.stored('tak_auth_password'), 'new')

    def test_existing_secret_keys_also_ignore_the_mask(self):
        self.db.set_setting('es_password', 'realpw')
        self.call(api.api_settings_put, {'es_password': '(set)'})
        self.assertEqual(self.stored('es_password'), 'realpw')

    def test_invalid_tak_value_is_400_and_writes_nothing(self):
        r = self.call(api.api_settings_put, {'operator_name': 'Alice', 'cot_port': 99999,
                                             'tak_multicast_ttl': 5})
        self.assertEqual(r.status, 400)
        self.assertEqual(r.body['error']['code'], 'VALIDATION_ERROR')
        errors = r.body['error']['detail']['errors']
        self.assertEqual([e['field'] for e in errors], ['cot_port'])
        self.assertEqual(r.body['error']['message'], errors[0]['message'])
        self.assertEqual(self.stored('operator_name'), '')       # a valid non-TAK key: not written
        self.assertEqual(self.stored('tak_multicast_ttl'), '1')
        self.assertEqual(self.stored('cot_port'), '6969')

    def test_valid_put_configures_engine_once(self):
        with patch.object(self.engine, 'configure', wraps=self.engine.configure) as spy:
            r = self.call(api.api_settings_put, {'cot_enabled': True, 'cot_port': 7002,
                                                 'tak_station_name': 'Alpha'})
            self.assertEqual(r.status, 200)
            self.assertEqual(spy.call_count, 1)
            cfg = spy.call_args[0][0]
            self.assertTrue(cfg.enabled)
            self.assertEqual((cfg.port, cfg.station_name), (7002, 'Alpha'))
            self.call(api.api_settings_put, {'operator_name': 'x'})
            self.assertEqual(spy.call_count, 1)                  # non-TAK keys do not reconfigure
        self.assertTrue(self.engine.enabled)
        self.assertEqual(self.stored('cot_enabled'), 'true')

    def test_same_values_resent_is_a_noop_for_the_engine(self):
        self.call(api.api_settings_put, {'cot_enabled': True})
        transports = len(FakeTransport.instances)
        raw = tak_config.read_raw(self.db)
        r = self.call(api.api_settings_put, tak_config.masked(raw))
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(len(FakeTransport.instances), transports)

    def test_unknown_key_still_rejected(self):
        r = self.call(api.api_settings_put, {'tak_nonsense': 1})
        self.assertEqual(r.status, 400)

    def test_writable_includes_every_tak_key(self):
        self.assertTrue(set(tak_config.TAK_KEYS) <= api._SETTINGS_WRITABLE)


class TestTakConfigApi(ApiCase):

    def test_get_shape(self):
        r = self.call(api.api_tak_config_get)
        self.assertEqual(r.status, 200)
        self.assertEqual(set(r.body), {'settings', 'effective', 'default_ports'})
        self.assertEqual(set(r.body['settings']), set(tak_config.TAK_KEYS))
        self.assertEqual(r.body['default_ports'], {'udp_multicast': 6969, 'udp_broadcast': 6969,
                                                   'udp_unicast': 4242, 'tcp': 8087, 'tls': 8089})
        eff = r.body['effective']
        self.assertTrue(eff['station_name'])
        self.assertTrue(eff['client_uid'].startswith('sparrow-droneid-'))
        self.assertEqual(eff['sensor_stale_s'], 30.0)
        self.assertTrue(eff['sensor_stale_auto'])

    def test_put_applies_and_returns_get_shape(self):
        r = self.call(api.api_tak_config_put, {
            'cot_enabled': True, 'tak_transport': 'tcp', 'cot_address': 'takserver.local',
            'cot_port': 8087, 'tak_auth_password': 'pw', 'tak_sensor_stale_s': '90'})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.body['settings']['tak_auth_password'], '(set)')
        self.assertFalse(r.body['effective']['sensor_stale_auto'])
        self.assertEqual(r.body['effective']['sensor_stale_s'], 90.0)
        self.assertEqual(self.engine.config.transport, 'tcp')
        self.assertEqual(self.stored('tak_auth_password'), 'pw')

    def test_put_validation_error_shape_and_nothing_written(self):
        r = self.call(api.api_tak_config_put, {'tak_transport': 'tcp', 'cot_address': 'bad host',
                                               'tak_stale_after_s': 1})
        self.assertEqual(r.status, 400)
        fields = {e['field'] for e in r.body['error']['detail']['errors']}
        self.assertEqual(fields, {'cot_address', 'tak_stale_after_s'})
        self.assertEqual(self.stored('tak_transport'), 'udp_multicast')
        self.assertEqual(self.stored('tak_stale_after_s'), '30')

    def test_put_unknown_and_non_object(self):
        r = self.call(api.api_tak_config_put, {'cot_port': 1, 'bogus': 1})
        self.assertEqual(r.status, 400)
        self.assertEqual(r.body['error']['detail'], {'unknown_keys': ['bogus']})
        self.assertEqual(self.call(api.api_tak_config_put, None).status, 400)

    def test_secret_semantics_via_tak_config(self):
        self.call(api.api_tak_config_put, {'tak_enroll_password': 'abc'})
        self.call(api.api_tak_config_put, {'tak_enroll_password': '(set)', 'cot_port': 7003})
        self.assertEqual(self.stored('tak_enroll_password'), 'abc')
        self.call(api.api_tak_config_put, {'tak_enroll_password': ''})
        self.assertEqual(self.stored('tak_enroll_password'), '')

    def test_status_and_interfaces_and_503(self):
        self.assertEqual(self.call(api.api_tak_status).body['state'], 'disabled')
        r = self.call(api.api_tak_interfaces)
        self.assertIsInstance(r.body['interfaces'], list)
        with patch.object(api, '_cot_engine', None):
            self.assertEqual(self.call(api.api_tak_status).status, 503)


class TestLegacyCot(ApiCase):

    def test_status_has_legacy_keys(self):
        body = self.call(api.api_cot_status).body
        for key in ('enabled', 'address', 'port', 'events_sent'):
            self.assertIn(key, body)
        self.assertEqual((body['address'], body['port'], body['enabled']), ('239.2.3.1', 6969, False))
        self.assertEqual(body['transport'], None)

    def test_address_or_port_switches_to_multicast(self):
        self.db.set_setting('tak_transport', 'tcp')
        self.db.set_setting('cot_address', 'host.example')
        r = self.call(api.api_cot_config, {'enabled': True, 'address': '239.2.3.2', 'port': 7000})
        self.assertEqual((r.status, r.body), (200, {}))
        self.assertEqual(self.stored('tak_transport'), 'udp_multicast')
        self.assertEqual((self.stored('cot_address'), self.stored('cot_port'),
                          self.stored('cot_enabled')), ('239.2.3.2', '7000', 'true'))
        self.assertTrue(self.engine.enabled)
        self.assertEqual(self.call(api.api_cot_status).body['port'], 7000)

    def test_enabled_alone_does_not_touch_transport(self):
        self.db.set_setting('tak_transport', 'tcp')
        self.db.set_setting('cot_address', 'host.example')
        self.db.set_setting('cot_port', '8087')
        self.call(api.api_cot_config, {'enabled': True})
        self.assertEqual(self.stored('tak_transport'), 'tcp')
        self.assertEqual(self.stored('cot_enabled'), 'true')

    def test_bad_address_and_port_are_400(self):
        self.assertEqual(self.call(api.api_cot_config, {'address': 'not-a-group'}).status, 400)
        self.assertEqual(self.call(api.api_cot_config, {'port': 'abc'}).status, 400)
        self.assertEqual(self.call(api.api_cot_config, {'port': 70000}).status, 400)
        self.assertEqual(self.stored('cot_address'), '239.2.3.1')
        self.assertEqual(self.call(api.api_cot_config, None).status, 400)


class TestTestEndpoint(ApiCase):

    def test_bad_candidate_is_400(self):
        r = self.call(api.api_tak_test, {'cot_port': 0})
        self.assertEqual(r.status, 400)
        self.assertEqual(r.body['error']['detail']['errors'][0]['field'], 'cot_port')

    def test_merges_candidate_over_stored_and_resolves_masked_secret(self):
        self.db.set_setting('tak_auth_password', 'stored-pw')
        self.db.set_setting('cot_port', '7100')
        seen = {}

        def fake_probe(cfg, store, enrollment):
            seen.update(cfg=cfg, enrollment=enrollment)
            return {'ok': True, 'message': 'fine', 'error': '', 'duration_ms': 1, 'details': {}}
        with patch.object(api, 'probe_connection', fake_probe):
            r = self.call(api.api_tak_test, {'tak_transport': 'tcp', 'cot_address': 'h.example',
                                             'tak_auth_password': '(set)', 'cot_enabled': False})
        self.assertEqual(r.status, 200)
        self.assertTrue(r.body['ok'])
        cfg = seen['cfg']
        self.assertEqual((cfg.transport, cfg.host, cfg.port, cfg.auth_password),
                         ('tcp', 'h.example', 7100, 'stored-pw'))
        self.assertIsNone(seen['enrollment'])
        self.assertEqual(self.stored('tak_transport'), 'udp_multicast')   # nothing saved

    def test_empty_body_tests_saved_config(self):
        with patch.object(api, 'probe_connection',
                          lambda cfg, s, e: {'ok': True, 'message': '', 'error': '',
                                             'duration_ms': 0, 'details': {'t': cfg.transport}}):
            r = self.call(api.api_tak_test, {})
        self.assertEqual(r.body['details']['t'], 'udp_multicast')

    def test_real_udp_probe_end_to_end(self):
        r = self.call(api.api_tak_test, {})
        self.assertTrue(r.body['ok'], r.body)
        self.assertIn('nothing was sent', r.body['message'])

    def test_enroll_mode_without_credentials_reports_instead_of_enrolling(self):
        r = self.call(api.api_tak_test, {'tak_transport': 'tls', 'cot_address': 'h.example',
                                         'tak_tls_client_mode': 'enroll'})
        self.assertEqual(r.status, 200)
        self.assertFalse(r.body['ok'])
        self.assertIn('enrollment username and password', r.body['error'])

    def test_enroll_mode_builds_an_enrollment_manager(self):
        seen = {}
        with patch.object(api, 'probe_connection',
                          lambda cfg, s, e: seen.setdefault('e', e) and {
                              'ok': True, 'message': '', 'error': '', 'duration_ms': 0, 'details': {}}):
            self.call(api.api_tak_test, {'tak_transport': 'tls', 'cot_address': 'h.example',
                                         'tak_tls_client_mode': 'enroll',
                                         'tak_enroll_username': 'u', 'tak_enroll_password': 'p'})
        self.assertIsNotNone(seen['e'])


class TestCertEndpoints(ApiCase):

    def test_client_upload_success_sets_mode_and_reconfigures(self):
        self.db.set_setting('tak_tls_client_mode', 'path')
        p12 = make_p12(CKEY, CCERT, [CA.cert], 'pw')
        r = self.call(api.api_tak_certs_client_post,
                      {'cert_b64': b64(p12), 'password': 'pw', 'filename': '../x/c.p12'})
        self.assertEqual(r.status, 201, r.body)
        info = r.body['client']
        self.assertEqual((info['subject'], info['source'], info['format'], info['filename']),
                         ('CN=apiuser', 'upload', 'p12', 'c.p12'))
        self.assertEqual(self.stored('tak_tls_client_mode'), 'upload')
        self.assertNotIn('pw', json.dumps(tak_config.masked(tak_config.read_raw(self.db))))
        got = self.call(api.api_tak_certs_get).body
        self.assertEqual(got['client']['subject'], 'CN=apiuser')
        self.assertIsNone(got['ca'])
        self.assertIsNone(got['enrolled'])
        self.assertTrue(self.call(api.api_tak_certs_client_delete).body['deleted'])
        self.assertFalse(self.call(api.api_tak_certs_client_delete).body['deleted'])

    def test_client_upload_pem_pair(self):
        r = self.call(api.api_tak_certs_client_post,
                      {'cert_b64': b64(pem(CCERT)), 'key_b64': b64(pem(CKEY))})
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body['client']['format'], 'pem')

    def test_caps_base64_and_cert_errors(self):
        big = b64(b'x' * (256 * 1024 + 1))
        r = self.call(api.api_tak_certs_client_post, {'cert_b64': big})
        self.assertEqual((r.status, r.body['error']['message']),
                         (400, 'Certificate file too large (max 256 KiB)'))
        ok_size = b64(b'x' * (256 * 1024))
        self.assertNotEqual(self.call(api.api_tak_certs_client_post,
                                      {'cert_b64': ok_size}).body['error']['message'],
                            'Certificate file too large (max 256 KiB)')
        r = self.call(api.api_tak_certs_client_post, {'cert_b64': 'not base64!!'})
        self.assertEqual(r.status, 400)
        self.assertIn('base64', r.body['error']['message'])
        r = self.call(api.api_tak_certs_client_post, {'cert_b64': b64(make_p12(CKEY, CCERT, [], 'right')),
                                                      'password': 'wrong'})
        self.assertEqual(r.status, 400)
        self.assertIn('Wrong password', r.body['error']['message'])
        self.assertEqual(self.call(api.api_tak_certs_client_post, {}).status, 400)
        self.assertEqual(self.stored('tak_tls_client_mode'), 'upload')    # failed uploads change nothing

    def test_ca_upload_and_delete(self):
        r = self.call(api.api_tak_certs_ca_post,
                      {'data_b64': b64(make_p12(None, None, [CA.cert], 'atakatak')),
                       'password': 'atakatak', 'filename': 'truststore.p12'})
        self.assertEqual(r.status, 201, r.body)
        self.assertEqual(r.body['ca']['subjects'], ['CN=API CA'])
        self.assertEqual(self.stored('tak_tls_ca_mode'), 'upload')
        self.assertTrue(self.call(api.api_tak_certs_ca_delete).body['deleted'])
        self.assertEqual(self.call(api.api_tak_certs_ca_post, {'data_b64': b64(b'junk')}).status, 400)

    def test_upload_restarts_a_running_tls_link(self):
        self.call(api.api_tak_config_put, {'cot_enabled': True, 'tak_transport': 'tls',
                                           'cot_address': 'h.example'})
        before = len(FakeTransport.instances)
        self.call(api.api_tak_certs_ca_post, {'data_b64': b64(pem(CA.cert))})
        self.assertGreater(len(FakeTransport.instances), before)

    def test_crypto_missing_is_503(self):
        with patch.object(api.tak_certs, 'HAS_CRYPTOGRAPHY', False):
            for fn in (api.api_tak_certs_get,):
                self.assertEqual(self.call(fn).status, 503)
            r = self.call(api.api_tak_certs_client_post, {'cert_b64': b64(b'x')})
            self.assertEqual((r.status, r.body['error']['message']),
                             (503, 'cryptography is not installed'))
            self.assertEqual(self.call(api.api_tak_package, {'data_b64': b64(b'x')}).status, 503)


class TestEnrollmentEndpoints(ApiCase):

    def test_status_disabled_without_manager(self):
        self.assertEqual(self.call(api.api_tak_enrollment_get).body, {'status': 'disabled'})

    def test_enroll_409_unless_tls_enroll_with_credentials(self):
        r = self.call(api.api_tak_enroll, {})
        self.assertEqual((r.status, r.body['error']['code']), (409, 'CONFLICT'))
        self.db.set_setting('tak_transport', 'tls')
        self.db.set_setting('cot_address', 'h.example')
        self.assertEqual(self.call(api.api_tak_enroll, {}).status, 409)     # mode is upload
        self.db.set_setting('tak_tls_client_mode', 'enroll')
        r = self.call(api.api_tak_enroll, {})
        self.assertEqual(r.status, 409)
        self.assertIn('username and password', r.body['error']['message'])

    def test_enroll_runs_the_manager_and_returns_status(self):
        for k, v in (('tak_transport', 'tls'), ('cot_address', 'h.example'),
                     ('tak_tls_client_mode', 'enroll'), ('tak_enroll_username', 'u'),
                     ('tak_enroll_password', 'p')):
            self.db.set_setting(k, v)
        with patch.object(api.EnrollmentManager, 'enroll_now',
                          lambda self_, t: {'status': 'failed', 'last_error': 'x'}) as m:
            r = self.call(api.api_tak_enroll, {})
        self.assertEqual((r.status, r.body['status']), (200, 'failed'))


class TestPackageEndpoint(ApiCase):

    def zip(self, **kw):
        entries = [('count', '1'), ('connectString0', 'tak.example.org:8089:ssl'),
                   ('description0', 'Lab'), ('caLocation0', 'cert/ts.p12'),
                   ('caPassword0', 'atakatak'), ('enrollForCertificateWithTrust0', 'true')]
        pref = ("<?xml version='1.0'?><preferences><preference name='cot_streams'>%s"
                '</preference></preferences>' % ''.join(
                    '<entry key="%s">%s</entry>' % e for e in entries)).encode()
        return build_zip({'config.pref': pref,
                          'ts.p12': make_p12(None, None, [CA.cert], 'atakatak')})

    def test_enrollment_package_applies(self):
        self.db.set_setting('cot_enabled', 'false')
        r = self.call(api.api_tak_package, {'data_b64': b64(self.zip()), 'filename': 'p.zip'})
        self.assertEqual(r.status, 200, r.body)
        body = r.body
        self.assertEqual((body['kind'], body['host'], body['port'], body['transport']),
                         ('enrollment', 'tak.example.org', 8089, 'tls'))
        self.assertTrue(body['enroll_required'])
        self.assertEqual(body['imported']['ca']['subjects'], ['CN=API CA'])
        self.assertIsNone(body['imported']['client'])
        self.assertIn('Enter the enrollment username and password', body['next_step'])
        self.assertEqual(body['settings']['tak_tls_client_mode'], 'enroll')
        self.assertEqual(body['settings']['tak_tls_ca_mode'], 'upload')
        self.assertEqual(body['description'], 'Lab')
        self.assertEqual(self.stored('cot_address'), 'tak.example.org')
        self.assertEqual(self.stored('cot_port'), '8089')
        self.assertEqual(self.stored('tak_transport'), 'tls')
        self.assertEqual(self.stored('cot_enabled'), 'false')            # never changed
        self.assertTrue(self.engine.cert_store.exists('ca.pem'))
        meta = self.engine.cert_store.ca_info()
        self.assertEqual(meta['source'], 'package')

    def test_does_not_flip_enable_and_applies_when_already_enabled(self):
        self.db.set_setting('cot_enabled', 'true')
        r = self.call(api.api_tak_package, {'data_b64': b64(self.zip())})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.stored('cot_enabled'), 'true')

    def test_certificate_package_imports_client(self):
        entries = [('connectString0', 'h.example:8089:ssl'), ('caLocation0', 'ts.p12'),
                   ('caPassword0', 'atakatak'), ('certificateLocation0', 'me.p12'),
                   ('clientPassword0', 'pw')]
        pref = ('<preferences><preference name="cot_streams">%s</preference></preferences>'
                % ''.join('<entry key="%s">%s</entry>' % e for e in entries)).encode()
        z = build_zip({'config.pref': pref, 'ts.p12': make_p12(None, None, [CA.cert], 'atakatak'),
                       'me.p12': make_p12(CKEY, CCERT, [CA.cert], 'pw')})
        r = self.call(api.api_tak_package, {'data_b64': b64(z)})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.body['kind'], 'certificate')
        self.assertEqual(r.body['imported']['client']['subject'], 'CN=apiuser')
        self.assertEqual(self.stored('tak_tls_client_mode'), 'upload')
        self.assertEqual(self.engine.cert_store.client_info()['source'], 'package')
        self.assertIn('Client certificate imported', r.body['next_step'])

    def test_bad_package_leaves_database_and_files_untouched(self):
        before = tak_config.read_raw(self.db)
        bad_pw = build_zip({'config.pref': (
            '<preferences><preference name="cot_streams"><entry key="connectString0">h:8089:ssl</entry>'
            '<entry key="caLocation0">ts.p12</entry><entry key="caPassword0">WRONG</entry>'
            '</preference></preferences>').encode(),
            'ts.p12': make_p12(None, None, [CA.cert], 'atakatak')})
        for payload in (b'not a zip', bad_pw, build_zip({'readme': b'x'})):
            r = self.call(api.api_tak_package, {'data_b64': b64(payload)})
            self.assertEqual(r.status, 400, payload[:10])
        self.assertEqual(tak_config.read_raw(self.db), before)
        self.assertFalse(self.engine.cert_store.exists('ca.pem'))
        r = self.call(api.api_tak_package, {'data_b64': b64(b'x' * (2 * 1024 * 1024 + 1))})
        self.assertEqual((r.status, r.body['error']['message']),
                         (400, 'Package is too large (max 2 MiB)'))


class TestBodyCap(unittest.TestCase):

    def make_handler(self, length):
        h = api.RequestHandler.__new__(api.RequestHandler)

        class NoRead:
            def read(self, n=-1):
                raise AssertionError('the body must not be read')
        h.headers = {'Content-Length': str(length), 'Content-Type': 'application/json'}
        h.rfile = NoRead()
        h.path = '/api/v1/tak/config'
        h.body_data = None
        h.json_data = None
        h.query_params = {}
        h._deprecated_path = False
        h._body_too_large = False
        h.close_connection = False
        h.sent = []
        h._send_error = lambda status, code, msg, detail=None: h.sent.append((status, code, msg))
        return h

    def test_over_cap_is_413_without_reading(self):
        for method in ('do_PUT', 'do_POST'):
            h = self.make_handler(16 * 1024 * 1024 + 1)
            with patch.object(api, '_db', None):
                getattr(h, method)()
            self.assertEqual(h.sent, [(413, 'PAYLOAD_TOO_LARGE', 'Request body exceeds 16 MiB')])
            self.assertTrue(h.close_connection)

    def test_exactly_at_cap_is_read(self):
        h = self.make_handler(16 * 1024 * 1024)

        class Reader:
            called = 0

            def read(self, n):
                Reader.called = n
                return b'{}'
        h.rfile = Reader()
        h.parse_body()
        self.assertEqual(Reader.called, 16 * 1024 * 1024)
        self.assertFalse(h._body_too_large)


class TestRealServer(ApiCase):
    """One real HTTP round trip: routes are registered and the 413 reaches a socket."""

    def test_http_roundtrip(self):
        httpd = api.MultithreadHTTPServer(('127.0.0.1', 0), api.RequestHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('GET', '/api/v1/tak/status')
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read())['state'], 'disabled')
        conn.request('PUT', '/api/v1/tak/config', body=json.dumps({'cot_port': 7500}),
                     headers={'Content-Type': 'application/json'})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.read())['settings']['cot_port'], 7500)
        conn.request('GET', '/api/v1/openapi.json')
        spec = json.loads(conn.getresponse().read())
        for path in ('/api/v1/tak/status', '/api/v1/tak/config', '/api/v1/tak/test',
                     '/api/v1/tak/certs', '/api/v1/tak/certs/client', '/api/v1/tak/certs/ca',
                     '/api/v1/tak/enrollment', '/api/v1/tak/enroll', '/api/v1/tak/package',
                     '/api/v1/tak/interfaces'):
            self.assertIn(path, spec['paths'], path)
        for name in ('TakStatus', 'TakConfigResponse', 'TakTestResult', 'TakCertInfo',
                     'TakCaInfo', 'TakCertsResponse', 'TakEnrollmentStatus',
                     'TakPackageResult', 'TakInterfaces'):
            self.assertIn(name, spec['components']['schemas'])
        self.assertIn('tak_transport', spec['components']['schemas']['Settings']['properties'])
        self.assertIn('TAK', [t['name'] for t in spec['tags']])
        conn.close()
        sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        sock.sendall(b'PUT /api/v1/tak/config HTTP/1.1\r\nHost: x\r\n'
                     b'Content-Type: application/json\r\nContent-Length: 16777217\r\n\r\n{')
        data = b''
        while True:                      # the server closes the connection after the 413
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        self.assertTrue(data.startswith(b'HTTP/1.0 413') or data.startswith(b'HTTP/1.1 413'), data[:40])
        self.assertIn(b'PAYLOAD_TOO_LARGE', data)


if __name__ == '__main__':
    unittest.main()
