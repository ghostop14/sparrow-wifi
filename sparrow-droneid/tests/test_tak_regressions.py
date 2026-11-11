"""Regression tests for review findings (each fails on the code before the fix)."""
import dataclasses
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import (FakeClock, FakeEnrollment, FakeGps, FakeTransport, build_zip,  # noqa: E402
                         default_cfg, make_ca, make_cert, make_device, make_p12, pem)
from test_tak_api import ApiCase, b64  # noqa: E402,F401
from test_tak_transport import TcpServer, wait_for  # noqa: E402

from cryptography.hazmat.primitives import serialization  # noqa: E402

import backend.api_handler as api  # noqa: E402
from backend.tak import certs, enrollment, engine as engine_mod  # noqa: E402
from backend.tak.certs import CertError, CertStore  # noqa: E402
from backend.tak.enrollment import EnrollmentManager, EnrollResult  # noqa: E402
from backend.tak.engine import TakEngine  # noqa: E402
from backend.tak.transport import CotTransport  # noqa: E402

CA = make_ca('Reg CA')


class FakeClient:
    """Stands in for EnrollmentClient: issues a certificate for the requested user."""
    gate = None            # optional threading.Event the enroll() call waits on
    calls = 0

    def __init__(self, host, port, user, password, uid, ctx):
        self.user = user

    def enroll(self):
        FakeClient.calls += 1
        if FakeClient.gate is not None:
            FakeClient.gate.wait(10)
        key, cert = make_cert(CA, self.user, client=True)
        return EnrollResult(
            key_pem=key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()),
            cert_pem=cert.public_bytes(serialization.Encoding.PEM), ca_pems=[],
            subject='CN=' + self.user, not_after=datetime.now(timezone.utc) + timedelta(days=300))


class EnrollRegression(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CertStore(os.path.join(self.tmp.name, 'tak_certs'))
        FakeClient.gate = None
        FakeClient.calls = 0
        self.cfg_a = default_cfg(cot_enabled=True, tak_transport='tls', cot_address='127.0.0.1',
                                 tak_tls_client_mode='enroll', tak_enroll_username='alice',
                                 tak_enroll_password='pw')
        self.cfg_b = dataclasses.replace(self.cfg_a, enroll_username='bob')

    def mgr(self, cfg, **kw):
        return EnrollmentManager(cfg, self.store, client_factory=FakeClient, backoff_store={}, **kw)

    def test_testing_another_identity_does_not_break_the_running_link(self):
        a = self.mgr(self.cfg_a)
        self.assertEqual(a.check(), 'enrolled')
        b = self.mgr(self.cfg_b)                     # the throwaway manager behind Test connection
        self.assertEqual(b.ensure_certificate(), 'enrolled')
        self.assertEqual(a.current_material().subject, 'CN=alice')     # still alice's
        self.assertEqual(b.current_material().subject, 'CN=bob')
        self.assertNotEqual(enrollment.cache_subdir(self.cfg_a), enrollment.cache_subdir(self.cfg_b))

    def test_password_is_not_part_of_the_cache_identity(self):
        same = dataclasses.replace(self.cfg_a, enroll_password='other')
        self.assertEqual(enrollment.cache_subdir(self.cfg_a), enrollment.cache_subdir(same))
        for change in (dict(host='other.example'), dict(enroll_port=9999), dict(client_uid='x')):
            self.assertNotEqual(enrollment.cache_subdir(self.cfg_a),
                                enrollment.cache_subdir(dataclasses.replace(self.cfg_a, **change)))

    def test_missing_cache_wakes_the_running_manager(self):
        a = self.mgr(self.cfg_a)
        a.check()
        a.start()
        self.addCleanup(a.stop)
        calls = FakeClient.calls
        import shutil
        shutil.rmtree(os.path.join(self.store.cert_dir, enrollment.cache_subdir(self.cfg_a)))
        with self.assertRaises(CertError):
            a.current_material()
        self.assertTrue(wait_for(lambda: FakeClient.calls > calls, timeout=8))
        self.assertTrue(wait_for(lambda: self._ok(a), timeout=8))

    @staticmethod
    def _ok(mgr):
        try:
            mgr.current_material()
            return True
        except CertError:
            return False

    def test_result_of_a_stopped_manager_is_not_written(self):
        FakeClient.gate = threading.Event()
        a = self.mgr(self.cfg_a)
        t = threading.Thread(target=a.check, daemon=True)
        t.start()
        self.assertTrue(wait_for(lambda: FakeClient.calls == 1))
        a.stop()                                      # generation moves on while enrolling
        FakeClient.gate.set()
        t.join(10)
        sub = os.path.join(self.store.cert_dir, enrollment.cache_subdir(self.cfg_a))
        self.assertFalse(os.path.exists(os.path.join(sub, 'cert.pem')))
        self.assertFalse(os.path.exists(os.path.join(sub, 'meta.json')))

    def test_flat_cache_is_adopted_when_it_matches(self):
        first = self.mgr(self.cfg_a)
        first.check()
        sub = enrollment.cache_subdir(self.cfg_a)
        # rewrite the result the way the old version stored it: flat under enroll/
        for name in ('key.pem', 'cert.pem', 'meta.json'):
            self.store.write_atomic('enroll/' + name, self.store.read(sub + '/' + name))
        import shutil
        shutil.rmtree(os.path.join(self.store.cert_dir, sub))
        calls = FakeClient.calls
        fresh = self.mgr(self.cfg_a)
        self.assertEqual(fresh.current_material().subject, 'CN=alice')
        self.assertEqual(FakeClient.calls, calls)                       # no network
        self.assertFalse(self.store.exists('enroll/cert.pem'))          # moved, not copied

    def test_flat_cache_for_another_identity_is_ignored(self):
        other = self.mgr(self.cfg_b)
        other.check()
        sub_b = enrollment.cache_subdir(self.cfg_b)
        for name in ('key.pem', 'cert.pem', 'meta.json'):
            self.store.write_atomic('enroll/' + name, self.store.read(sub_b + '/' + name))
        a = self.mgr(self.cfg_a)
        with self.assertRaises(CertError):
            a.current_material()
        self.assertTrue(self.store.exists('enroll/cert.pem'))           # left alone


class TransportRegression(unittest.TestCase):

    def test_updates_are_purged_before_the_epoch_moves(self):
        seen = []
        server = TcpServer(on_conn=lambda conn, buf, i: (conn.close(), False)[1] if i == 1 else None)
        self.addCleanup(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = CotTransport(cfg, materials=lambda: None)
        original = t._purge_updates

        def spy():
            seen.append(t._epoch)
            return original()
        t._purge_updates = spy
        t.start()
        self.addCleanup(t.stop)
        self.assertTrue(wait_for(lambda: t.connection_epoch == 2, timeout=8))
        self.assertEqual(seen, [1])        # the purge ran while the epoch was still the old one

    def test_engine_resend_after_reconnect_survives(self):
        # The resend an engine queues the instant it sees the new epoch must be delivered.
        server = TcpServer(on_conn=lambda conn, buf, i: (conn.close(), False)[1] if i == 1 else None)
        self.addCleanup(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = CotTransport(cfg, materials=lambda: None)
        original = t._purge_updates
        started = threading.Event()

        def slow_purge():
            started.set()
            time.sleep(0.3)               # widen the window between purge and epoch bump
            return original()
        t._purge_updates = slow_purge
        t.start()
        self.addCleanup(t.stop)

        def watcher():
            while t.connection_epoch < 2:
                time.sleep(0.005)
            t.enqueue([('update', 'resend', b'<resend/>')])
        threading.Thread(target=watcher, daemon=True).start()
        self.assertTrue(wait_for(lambda: len(server.received) == 2
                                 and b'<resend/>' in bytes(server.received[1]), timeout=10))

    def test_sender_survives_unexpected_exceptions(self):
        calls = []

        def materials():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError('boom from cert resolution')
            raise TypeError('and again')
        cfg = default_cfg(tak_transport='tls', cot_address='127.0.0.1', cot_port=1)
        t = CotTransport(cfg, materials=materials)
        t.start()
        self.addCleanup(t.stop)
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertIn('boom from cert resolution', t.get_stats()['last_error'])
        self.assertTrue(t._thread.is_alive())
        self.assertTrue(wait_for(lambda: len(calls) >= 2, timeout=6))   # retried after the backoff
        self.assertEqual(t.get_stats()['state'], 'error')


class EngineRegression(unittest.TestCase):

    def test_overrunning_emit_thread_is_never_revived(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = FakeClock()
        gate = threading.Event()
        first = threading.Event()
        calls = []

        def source():
            calls.append(threading.current_thread())
            if len(calls) == 1:
                first.set()
                gate.wait(10)             # the first tick overruns the stop join
            return []
        FakeTransport.instances = []
        eng = TakEngine(cert_dir=os.path.join(tmp.name, 'c'), drone_source=source,
                        gps_engine=FakeGps(), monotonic=clock.monotonic, wallclock=clock.wallclock,
                        transport_factory=FakeTransport, enrollment_factory=FakeEnrollment)
        self.addCleanup(eng.stop)
        self.addCleanup(gate.set)
        with patch.object(engine_mod, '_STOP_JOIN_S', 0.2):
            eng.configure(default_cfg(cot_enabled=True, tak_sensor_enabled=False))
            self.assertTrue(first.wait(5))
            old = [t for t in threading.enumerate() if t.name == 'tak-emit']
            self.assertEqual(len(old), 1)
            eng.configure(default_cfg(cot_enabled=True, cot_port=7777, tak_sensor_enabled=False))
        gate.set()
        old[0].join(5)
        self.assertFalse(old[0].is_alive())               # it exited; it was not revived
        time.sleep(0.3)
        self.assertEqual(len([t for t in threading.enumerate() if t.name == 'tak-emit']), 1)

    def test_superseded_session_does_not_touch_the_new_state(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = FakeClock()
        drones = [('K', make_device(clock))]
        gate = threading.Event()
        entered = threading.Event()
        state = {'n': 0}

        def source():
            state['n'] += 1
            if state['n'] == 1:
                entered.set()
                gate.wait(10)
            return list(drones)
        FakeTransport.instances = []
        eng = TakEngine(cert_dir=os.path.join(tmp.name, 'c'), drone_source=source,
                        gps_engine=FakeGps(), monotonic=clock.monotonic, wallclock=clock.wallclock,
                        transport_factory=FakeTransport, enrollment_factory=FakeEnrollment,
                        start_threads=False)
        self.addCleanup(eng.stop)
        eng.configure(default_cfg(cot_enabled=True, tak_sensor_enabled=False))
        old = threading.Thread(target=eng.tick, daemon=True)
        old.start()
        self.assertTrue(entered.wait(5))
        eng.configure(default_cfg(cot_enabled=True, cot_port=7778, tak_sensor_enabled=False))
        gate.set()
        old.join(5)
        self.assertEqual(eng._drawn, {})                  # the old tick wrote nothing here
        self.assertEqual(FakeTransport.instances[-1].batches, [])
        self.assertEqual(FakeTransport.instances[0].batches, [])


    def test_tick_racing_stop_still_sends_its_deletes(self):
        # A drone vanishes in the same tick as stop(): that tick removed the UIDs
        # from _drawn, so its batch is the only place their deletes exist.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = FakeClock()
        drones = [('K', make_device(clock, operator_lat=0.0, operator_lon=0.0))]
        gate = threading.Event()
        entered = threading.Event()
        block = {'on': False}

        class Gps(FakeGps):
            def to_dict(self):
                if block['on'] and threading.current_thread().name == 'tak-emit':
                    entered.set()
                    gate.wait(5)
                return super().to_dict()
        FakeTransport.instances = []
        eng = TakEngine(cert_dir=os.path.join(tmp.name, 'c'), drone_source=lambda: list(drones),
                        gps_engine=Gps(), monotonic=clock.monotonic, wallclock=clock.wallclock,
                        transport_factory=FakeTransport, enrollment_factory=FakeEnrollment)
        self.addCleanup(eng.stop)
        self.addCleanup(gate.set)
        eng.configure(default_cfg(cot_enabled=True, tak_sensor_enabled=False))
        self.assertTrue(wait_for(lambda: len(eng._drawn) == 1, 5))
        tr = FakeTransport.instances[-1]
        drones.clear()
        block['on'] = True
        eng.notify_detection()
        self.assertTrue(entered.wait(5))
        threading.Timer(0.1, gate.set).start()      # the tick resumes while stop() joins
        eng.stop()
        kinds = [k for b in tr.batches for (k, u, p) in (b[1] if isinstance(b, tuple) else b)]
        self.assertEqual(kinds.count('delete'), 1)


class CertRegression(unittest.TestCase):

    def test_unencrypted_key_with_a_password_loads(self):
        key, cert = make_cert(CA, 'plain', client=True)
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        mat = certs.normalize_client(pem(cert), key_pem, 'atakatak')
        self.assertEqual(mat.subject, 'CN=plain')
        pkcs1 = key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption())
        self.assertEqual(certs.normalize_client(pem(cert) + pkcs1, None, 'x').subject, 'CN=plain')

    def test_wrong_password_for_an_encrypted_key_still_reported(self):
        from tak_support import key_pem_encrypted
        key, cert = make_cert(CA, 'enc', client=True)
        with self.assertRaises(CertError) as cm:
            certs.normalize_client(pem(cert), key_pem_encrypted(key, 'right'), 'wrong')
        self.assertIn('Wrong password', str(cm.exception))


class PackageSecretsRegression(ApiCase):

    def package(self, host='evil.example', port=8089, proto='ssl'):
        entries = [('count', '1'), ('connectString0', '%s:%d:%s' % (host, port, proto)),
                   ('caLocation0', 'ts.p12'), ('caPassword0', 'atakatak'),
                   ('enrollForCertificateWithTrust0', 'true')]
        pref = ('<preferences><preference name="cot_streams">%s</preference></preferences>'
                % ''.join('<entry key="%s">%s</entry>' % e for e in entries)).encode()
        return b64(build_zip({'config.pref': pref,
                              'ts.p12': make_p12(None, None, [CA.cert], 'atakatak')}))

    def seed(self):
        for k, v in (('cot_address', 'good.example'), ('cot_port', '8089'),
                     ('tak_transport', 'tls'), ('tak_enroll_password', 'enroll-secret'),
                     ('tak_auth_password', 'auth-secret'), ('tak_tls_client_password', 'cert-secret'),
                     ('tak_enroll_username', 'alice'), ('cot_enabled', 'true')):
            self.db.set_setting(k, v)

    def test_new_server_clears_secrets_of_the_old_one(self):
        self.seed()
        r = self.call(api.api_tak_package, {'data_b64': self.package('evil.example')})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(sorted(r.body['cleared_secrets']),
                         ['tak_auth_password', 'tak_enroll_password', 'tak_tls_client_password'])
        for key in ('tak_enroll_password', 'tak_auth_password', 'tak_tls_client_password'):
            self.assertEqual(self.stored(key), '', key)
            self.assertEqual(r.body['settings'][key], '')
        self.assertEqual(self.stored('tak_enroll_username'), 'alice')   # not a secret
        self.assertEqual(self.stored('cot_address'), 'evil.example')

    def test_port_change_alone_clears_too(self):
        self.seed()
        r = self.call(api.api_tak_package, {'data_b64': self.package('good.example', 9999)})
        self.assertIn('tak_enroll_password', r.body['cleared_secrets'])
        self.assertEqual(self.stored('tak_enroll_password'), '')

    def test_same_server_keeps_secrets(self):
        self.seed()
        r = self.call(api.api_tak_package, {'data_b64': self.package('GOOD.example', 8089)})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body['cleared_secrets'], [])
        self.assertEqual(self.stored('tak_enroll_password'), 'enroll-secret')

    def test_transport_downgrade_on_same_server_clears_secrets(self):
        self.seed()
        r = self.call(api.api_tak_package, {'data_b64': self.package('good.example', 8089, 'tcp')})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.stored('tak_transport'), 'tcp')
        self.assertIn('tak_auth_password', r.body['cleared_secrets'])
        self.assertEqual(self.stored('tak_auth_password'), '')
        self.assertEqual(self.stored('tak_enroll_password'), '')

    def test_only_secrets_that_were_set_are_listed(self):
        self.db.set_setting('tak_enroll_password', 'x')
        r = self.call(api.api_tak_package, {'data_b64': self.package('evil.example')})
        self.assertEqual(r.body['cleared_secrets'], ['tak_enroll_password'])

    def test_import_never_enables_the_output(self):
        self.db.set_setting('cot_enabled', 'false')
        self.call(api.api_tak_package, {'data_b64': self.package('evil.example')})
        self.assertEqual(self.stored('cot_enabled'), 'false')
        self.assertFalse(self.engine.enabled)

    def test_failed_import_clears_nothing(self):
        self.seed()
        r = self.call(api.api_tak_package, {'data_b64': b64(b'junk')})
        self.assertEqual(r.status, 400)
        self.assertEqual(self.stored('tak_enroll_password'), 'enroll-secret')


if __name__ == '__main__':
    unittest.main()
