"""Enrollment: a local HTTPS server plays TAK Server's /Marti/api/tls endpoints."""
import base64
import http.server
import json
import logging
import os
import ssl
import stat
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import FakeClock, default_cfg, make_ca, make_cert, pem, write  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from backend.tak import certs, enrollment  # noqa: E402
from backend.tak.enrollment import EnrollError, EnrollmentManager  # noqa: E402

SERVER_CA = make_ca('TAK Enrollment CA')
SRV_KEY, SRV_CERT = make_cert(SERVER_CA, 'takserver', sans=('127.0.0.1', 'localhost'))
CONFIG_XML = (
    '<?xml version="1.0"?><ns2:certificateConfig xmlns:ns2="com.bbn.marti.config">'
    '<ns2:nameEntries><ns2:nameEntry name="O" value="TAK"/>'
    '<ns2:nameEntry name="OU" value="Sparrow Group"/></ns2:nameEntries>'
    '</ns2:certificateConfig>')


class FakeTak:
    def __init__(self, tmpdir):
        self.hits = 0
        self.config_hits = 0
        self.sign_hits = 0
        self.mode = 'ok'               # ok | 401 | 500 | xml
        self.cert_days = 365
        self.config_body = CONFIG_XML.encode()
        self.last_csr = None
        self.last_headers = None
        self.last_query = None
        self.user, self.password = 'sparrowuser', 's3cret!pw'
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth_ok(self):
                want = 'Basic ' + base64.b64encode(
                    ('%s:%s' % (outer.user, outer.password)).encode()).decode()
                return self.headers.get('Authorization') == want

            def _reply(self, code, body, ctype='application/json'):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.hits += 1
                outer.config_hits += 1
                if outer.mode == '401' or not self._auth_ok():
                    return self._reply(401, b'no')
                if outer.mode == '500':
                    return self._reply(500, b'boom')
                self._reply(200, outer.config_body, 'application/xml')

            def do_POST(self):
                outer.hits += 1
                outer.sign_hits += 1
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                outer.last_headers = dict(self.headers)
                parsed = urllib.parse.urlparse(self.path)
                outer.last_query = urllib.parse.parse_qs(parsed.query)
                if not self._auth_ok():
                    return self._reply(401, b'no')
                csr = x509.load_pem_x509_csr(body)
                outer.last_csr = csr
                now = datetime.now(timezone.utc)
                cert = (x509.CertificateBuilder().subject_name(csr.subject)
                        .issuer_name(SERVER_CA.cert.subject).public_key(csr.public_key())
                        .serial_number(x509.random_serial_number())
                        .not_valid_before((now - timedelta(days=1)).replace(tzinfo=None))
                        .not_valid_after((now + timedelta(days=outer.cert_days)).replace(tzinfo=None))
                        .sign(SERVER_CA.key, hashes.SHA256()))
                armoured = pem(cert).decode()
                bare = ''.join(armoured.splitlines()[1:-1])
                ca_bare = ''.join(pem(SERVER_CA.cert).decode().splitlines()[1:-1])
                if outer.mode == 'xml':
                    xml = ('<?xml version="1.0"?><root><signedCert>%s</signedCert>'
                           '<ca0>%s</ca0></root>' % (bare, ca_bare))
                    return self._reply(200, xml.encode(), 'application/xml')
                self._reply(200, json.dumps({'signedCert': bare, 'ca0': ca_bare}).encode())

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cpath = write(os.path.join(tmpdir, 'es.crt'), pem(SRV_CERT))
        kpath = write(os.path.join(tmpdir, 'es.key'), pem(SRV_KEY))
        ctx.load_cert_chain(cpath, kpath)
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class EnrollCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = certs.CertStore(os.path.join(self.tmp.name, 'tak_certs'))
        self.store.store_ca(certs.normalize_ca(pem(SERVER_CA.cert), ''), source='upload',
                            filename='', fmt='pem')
        self.server = FakeTak(self.tmp.name)
        self.addCleanup(self.server.close)
        self.clock = FakeClock()
        self.backoff = {}
        old = enrollment.KEY_SIZE
        enrollment.KEY_SIZE = 2048                      # keep the tests quick
        self.addCleanup(setattr, enrollment, 'KEY_SIZE', old)

    def cfg(self, **kw):
        base = dict(tak_transport='tls', cot_address='127.0.0.1', cot_enabled=True,
                    tak_tls_client_mode='enroll', tak_tls_ca_mode='upload',
                    tak_enroll_port=self.server.port, tak_enroll_username=self.server.user,
                    tak_enroll_password=self.server.password, tak_client_uid='uid-sparrow-1')
        base.update(kw)
        return default_cfg(**base)

    def manager(self, cfg=None, **kw):
        return EnrollmentManager(cfg or self.cfg(), self.store, wallclock=self.clock.wallclock,
                                 monotonic=self.clock.monotonic, backoff_store=self.backoff, **kw)


class TestEnroll(EnrollCase):

    def test_enrolls_and_validates_request(self):
        mgr = self.manager()
        self.assertEqual(mgr.status()['status'], 'pending')
        self.assertEqual(mgr.check(), 'enrolled')
        csr = self.server.last_csr
        self.assertEqual(csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
                         self.server.user)
        self.assertEqual(csr.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value, 'TAK')
        self.assertEqual(
            csr.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)[0].value,
            'Sparrow Group')
        order = [a.oid for a in csr.subject]
        self.assertEqual(order, [NameOID.ORGANIZATION_NAME, NameOID.ORGANIZATIONAL_UNIT_NAME,
                                 NameOID.COMMON_NAME])
        h = {k.lower(): v for k, v in self.server.last_headers.items()}
        self.assertEqual(h['content-type'], 'application/pkcs10')
        self.assertEqual(self.server.last_query['clientUid'], ['uid-sparrow-1'])
        self.assertTrue(h['authorization'].startswith('Basic '))
        st = mgr.status()
        self.assertEqual(st['status'], 'enrolled')
        self.assertIsNone(st['last_error'])
        self.assertIsNotNone(st['last_success_at'])
        self.assertIn('CN=sparrowuser', st['cert_subject'])
        mats = mgr.current_material()
        self.assertTrue(os.path.isfile(mats.cert_file))
        self.assertIsNotNone(mats.ca_file)                       # enrollment returned ca0
        self.assertTrue(certs.key_matches_cert(open(mats.cert_file, 'rb').read(),
                                               open(mats.key_file, 'rb').read()))

    def test_files_are_0600_and_meta_has_no_password(self):
        mgr = self.manager()
        mgr.check()
        for name in ('key.pem', 'cert.pem', 'ca_chain.pem', 'meta.json'):
            path = os.path.join(self.store.cert_dir, 'enroll', name)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, name)
        meta = open(os.path.join(self.store.cert_dir, 'enroll', 'meta.json')).read()
        self.assertNotIn(self.server.password, meta)
        data = json.loads(meta)
        self.assertEqual({'host', 'port', 'username', 'client_uid', 'enrolled_at', 'not_after'},
                         set(data))
        self.assertEqual(data['port'], self.server.port)

    def test_cached_certificate_is_served_without_network(self):
        mgr = self.manager()
        mgr.check()
        hits = self.server.hits
        self.assertEqual(mgr.check(), 'cached')
        self.assertEqual(self.server.hits, hits)
        self.assertEqual(self.manager().check(), 'cached')       # a new manager, same cache
        self.assertEqual(self.server.hits, hits)
        self.manager().current_material()                        # no network either

    def test_expiring_certificate_is_renewed(self):
        self.server.cert_days = 10
        mgr = self.manager()
        mgr.check()
        hits = self.server.sign_hits
        self.server.cert_days = 365
        self.assertEqual(mgr.check(), 'enrolled')                # within 14 days of expiry
        self.assertEqual(self.server.sign_hits, hits + 1)
        self.assertEqual(mgr.check(), 'cached')

    def test_expired_cached_cert_is_not_served(self):
        mgr = self.manager()
        mgr.check()
        self.clock.advance(400 * 86400)
        with self.assertRaises(certs.CertError) as cm:
            mgr.current_material()
        self.assertIn('Waiting for certificate enrollment', str(cm.exception))

    def test_identity_change_invalidates_cache_and_reenrolls(self):
        mgr = self.manager()
        mgr.check()
        hits = self.server.sign_hits
        for change in (dict(tak_client_uid='another-uid'), dict(tak_enroll_username='other'),
                       dict(tak_enroll_port=self.server.port + 0, cot_address='localhost')):
            cfg = self.cfg(**change)
            m2 = self.manager(cfg)
            with self.assertRaises(certs.CertError, msg=str(change)):
                m2.current_material()
            if 'tak_enroll_username' in change:
                self.server.user = 'other'
            m2.check()
            self.assertGreater(self.server.sign_hits, hits, change)
            hits = self.server.sign_hits
            self.server.user = 'sparrowuser'
            self.manager(self.cfg()).check()                     # restore the base identity
            hits = self.server.sign_hits

    def test_blank_credentials_make_no_request(self):
        mgr = self.manager(self.cfg(tak_enroll_username='', tak_enroll_password=''))
        self.assertEqual(mgr.check(), 'failed')
        self.assertEqual(self.server.hits, 0)
        self.assertIn('not set', mgr.status()['last_error'])

    def test_xml_sign_response_fallback(self):
        self.server.mode = 'xml'
        mgr = self.manager()
        self.assertEqual(mgr.check(), 'enrolled')

    def test_dtd_in_tls_config_is_rejected(self):
        self.server.config_body = b'<!DOCTYPE x [<!ENTITY a "b">]><x/>'
        mgr = self.manager()
        self.assertEqual(mgr.check(), 'failed')
        self.assertIn('DTD', mgr.status()['last_error'])
        self.assertEqual(self.server.sign_hits, 0)

    def test_enroll_now_returns_status(self):
        mgr = self.manager()
        st = mgr.enroll_now(20)
        self.assertEqual(st['status'], 'enrolled')
        self.assertEqual(mgr.ensure_certificate(), 'cached')

    def test_start_runs_an_immediate_check_and_stop_is_clean(self):
        mgr = self.manager()
        mgr.start()
        self.addCleanup(mgr.stop)
        end = datetime.now() + timedelta(seconds=10)
        while mgr.status()['status'] != 'enrolled' and datetime.now() < end:
            threading.Event().wait(0.05)
        self.assertEqual(mgr.status()['status'], 'enrolled')
        mgr.stop()


class TestBackoff(EnrollCase):

    def test_401_backoff_300_600_capped_3600(self):
        self.server.mode = '401'
        mgr = self.manager()
        self.assertEqual(mgr.check(), 'failed')
        st = mgr.status()
        self.assertIn('HTTP 401', st['last_error'])
        self.assertIn('check username/password', st['last_error'])
        self.assertEqual(st['next_attempt_at'],
                         (self.clock.wall + timedelta(seconds=300)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(self.server.hits, 1)
        # inside the window: no extra hits (a forced request still honours the backoff)
        self.clock.advance(299)
        mgr.check()
        mgr.check(force=True)
        self.assertEqual(self.server.hits, 1)
        self.assertIn('backed off until', mgr.status()['last_error'])
        # +300: second attempt, then +600
        self.clock.advance(1)
        mgr.check()
        self.assertEqual(self.server.hits, 2)
        self.assertEqual(mgr.status()['next_attempt_at'],
                         (self.clock.wall + timedelta(seconds=600)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        delays = []
        for _ in range(5):
            nxt = datetime.strptime(mgr.status()['next_attempt_at'],
                                    '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            delays.append((nxt - self.clock.wall).total_seconds())
            self.clock.advance(delays[-1])
            mgr.check()
        self.assertEqual(delays, [600, 1200, 2400, 3600, 3600])

    def test_password_change_resets_backoff_immediately(self):
        self.server.mode = '401'
        self.manager().check()
        hits = self.server.hits
        self.server.mode = 'ok'
        self.server.password = 'fixed-password'
        fixed = self.manager(self.cfg(tak_enroll_password='fixed-password'))
        self.assertEqual(fixed.check(), 'enrolled')
        self.assertGreater(self.server.hits, hits)

    def test_success_clears_backoff(self):
        self.server.mode = '500'
        mgr = self.manager()
        mgr.check()
        self.server.mode = 'ok'
        self.clock.advance(301)
        self.assertEqual(mgr.check(), 'enrolled')
        self.assertEqual(self.backoff, {})

    def test_failed_renewal_keeps_serving_the_valid_cert(self):
        self.server.cert_days = 5
        mgr = self.manager()
        mgr.check()
        self.server.mode = '401'
        self.assertEqual(mgr.check(), 'cached')                  # renewal failed, cert still valid
        self.assertIn('401', mgr.status()['last_error'])
        mgr.current_material()

    def test_password_never_logged(self):
        self.server.mode = '401'
        with self.assertLogs(level=logging.DEBUG) as captured:
            mgr = self.manager()
            mgr.check()
            logging.getLogger('backend.tak.enrollment').warning('sentinel')
        text = '\n'.join(r.getMessage() for r in captured.records)
        self.assertNotIn(self.server.password, text)
        self.assertNotIn(base64.b64encode(
            ('%s:%s' % (self.server.user, self.server.password)).encode()).decode(), text)
        self.assertNotIn(self.server.password, mgr.status()['last_error'] or '')

    def test_unreachable_server_message(self):
        self.server.close()
        mgr = self.manager()
        self.assertEqual(mgr.check(), 'failed')
        self.assertTrue(mgr.status()['last_error'])
        self.server = FakeTak(self.tmp.name)                     # for cleanup


class TestClientHelpers(unittest.TestCase):

    def test_pem_block_normalisation(self):
        bare = ''.join(pem(SERVER_CA.cert).decode().splitlines()[1:-1])
        for value in (bare, pem(SERVER_CA.cert).decode(),
                      pem(SERVER_CA.cert).decode().replace('\n', '\\n')):
            blocks = enrollment._to_pem_blocks(value)
            self.assertEqual(len(blocks), 1)
            x509.load_pem_x509_certificate(blocks[0].encode())
        self.assertEqual(enrollment._to_pem_blocks(''), [])

    def test_sign_response_requires_cert(self):
        with self.assertRaises(EnrollError):
            enrollment.EnrollmentClient._parse_sign_response(b'{"ca0": "x"}')
        with self.assertRaises(EnrollError):
            enrollment.EnrollmentClient._parse_sign_response(b'<!DOCTYPE a><a/>')


if __name__ == '__main__':
    unittest.main()
