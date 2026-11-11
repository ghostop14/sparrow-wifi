"""CotTransport and the connection probe, against localhost sockets only."""
import os
import socket
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import default_cfg, make_ca, make_cert, pem, write  # noqa: E402

from backend.tak import certs, transport  # noqa: E402
from backend.tak.transport import CotTransport, TransportError  # noqa: E402


def wait_for(pred, timeout=8.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def no_tls():
    raise AssertionError('materials must not be requested for non-TLS transports')


class TcpServer:
    """Accepts connections; each connection's bytes are appended to ``received``."""

    def __init__(self, on_conn=None):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self.received = []          # list of bytearray, one per connection
        self.on_conn = on_conn
        self._stop = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            buf = bytearray()
            self.received.append(buf)
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn, buf, len(self.conns)),
                             daemon=True).start()

    def _serve(self, conn, buf, index):
        try:
            if self.on_conn:
                if self.on_conn(conn, buf, index) is False:
                    return
            conn.settimeout(0.2)
            while not self._stop:
                try:
                    data = conn.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf.extend(data)
        except OSError:
            return

    def close(self):
        self._stop = True
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


class TransportCase(unittest.TestCase):
    def tearDown(self):
        for item in getattr(self, '_cleanup', []):
            item()

    def later(self, fn):
        self.__dict__.setdefault('_cleanup', []).append(fn)

    def start(self, cfg, **kw):
        kw.setdefault('materials', no_tls)
        t = CotTransport(cfg, **kw)
        t.start()
        self.later(t.stop)
        return t


class TestUdp(TransportCase):

    def test_unicast_receives_exact_bytes(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(('127.0.0.1', 0))
        rx.settimeout(5)
        self.later(rx.close)
        cfg = default_cfg(tak_transport='udp_unicast', cot_address='127.0.0.1',
                          cot_port=rx.getsockname()[1])
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: t.connection_epoch == 1))
        t.enqueue([('update', 'u1', b'<event a="1"/>'), ('delete', 'u2', b'<event b="2"/>')])
        self.assertEqual(rx.recvfrom(4096)[0], b'<event a="1"/>')
        self.assertEqual(rx.recvfrom(4096)[0], b'<event b="2"/>')
        self.assertTrue(t.flush(2.0))
        stats = t.get_stats()
        self.assertEqual(stats['messages_sent'], 2)
        self.assertEqual(stats['state'], 'connected')
        self.assertTrue(stats['connected'])
        self.assertIsNotNone(stats['last_send_at'])

    def test_multicast_via_loopback_interface(self):
        group, port = '239.255.42.99', 0
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rx.bind(('', 0))
        port = rx.getsockname()[1]
        mreq = struct.pack('4s4s', socket.inet_aton(group), socket.inet_aton('127.0.0.1'))
        try:
            rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as exc:
            rx.close()
            self.skipTest('multicast join on 127.0.0.1 is not possible here: %s' % exc)
        self.later(rx.close)
        rx.settimeout(5)
        cfg = default_cfg(cot_address=group, cot_port=port, tak_multicast_interface='lo')
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: t.connection_epoch == 1))
        t.enqueue([('update', 'u', b'<event mc="1"/>')])
        try:
            data = rx.recvfrom(4096)[0]
        except socket.timeout:
            self.skipTest('multicast loopback delivery is not possible in this sandbox')
        self.assertEqual(data, b'<event mc="1"/>')

    def test_socket_options(self):
        cfg = default_cfg(tak_transport='udp_broadcast', cot_address='127.255.255.255')
        sock, dest = transport.open_udp_socket(cfg)
        self.later(sock.close)
        self.assertEqual(dest, ('127.255.255.255', 6969))
        self.assertTrue(sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST))
        cfg = default_cfg(tak_multicast_ttl=7, tak_multicast_interface='lo')
        sock, dest = transport.open_udp_socket(cfg)
        self.later(sock.close)
        self.assertEqual(sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL), 7)
        self.assertTrue(sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP))
        self.assertEqual(dest, ('239.2.3.1', 6969))

    def test_interface_without_ipv4_is_a_transport_error(self):
        cfg = default_cfg(tak_multicast_interface='nosuch0')
        with self.assertRaises(TransportError) as cm:
            transport.open_udp_socket(cfg)
        self.assertIn('Interface nosuch0 has no IPv4 address', str(cm.exception))

    def test_unresolvable_unicast_is_reported_not_raised(self):
        cfg = default_cfg(tak_transport='udp_unicast', cot_address='no-such-host.invalid')
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertIn('Could not resolve host', t.get_stats()['last_error'])
        self.assertEqual(t.get_stats()['state'], 'error')

    def test_list_interfaces(self):
        ifaces = transport.list_ipv4_interfaces()
        self.assertIsInstance(ifaces, list)
        for item in ifaces:
            self.assertEqual(set(item), {'name', 'ipv4'})
            self.assertNotEqual(item['name'], 'lo')
        self.assertEqual(transport.interface_ipv4('lo'), '127.0.0.1')
        self.assertIsNone(transport.interface_ipv4('nosuch0'))


class TestTcp(TransportCase):

    def test_auth_first_then_cot(self):
        server = TcpServer()
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port,
                          tak_auth_username='us"er', tak_auth_password='p<w>', tak_client_uid='uid1')
        t = self.start(cfg)
        t.enqueue([('update', 'a', b'<event one/>'), ('update', 'b', b'<event two/>')])
        self.assertTrue(wait_for(lambda: t.get_stats()['messages_sent'] == 2))
        self.assertTrue(wait_for(lambda: b'<event two/>' in bytes(server.received[0])))
        got = bytes(server.received[0])
        self.assertTrue(got.startswith(
            b'<auth><cot username="us&quot;er" password="p&lt;w&gt;" uid="uid1"/></auth>'), got)
        self.assertEqual(got.split(b'</auth>', 1)[1], b'<event one/><event two/>')

    def test_no_auth_when_username_blank(self):
        server = TcpServer()
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = self.start(cfg)
        t.enqueue([('update', 'a', b'<event one/>')])
        self.assertTrue(wait_for(lambda: server.received and bytes(server.received[0]) == b'<event one/>'))

    def test_reconnect_purges_updates_keeps_deletes(self):
        server = TcpServer(on_conn=lambda conn, buf, i: (conn.close(), False)[1] if i == 1 else None)
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port,
                          tak_auth_username='u', tak_auth_password='p')
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: t.get_stats()['state'] == 'error'))
        self.assertEqual(t.connection_epoch, 1)
        t.enqueue([('update', 'stale-geom', b'<event old/>'), ('delete', 'gone', b'<event del/>')])
        self.assertTrue(wait_for(lambda: t.connection_epoch == 2, timeout=6))
        self.assertTrue(wait_for(lambda: len(server.received) == 2
                                 and b'<event del/>' in bytes(server.received[1])))
        got = bytes(server.received[1])
        self.assertTrue(got.startswith(b'<auth>'))               # auth first, then the delete
        self.assertNotIn(b'<event old/>', got)
        stats = t.get_stats()
        self.assertEqual(stats['reconnects'], 1)
        self.assertGreaterEqual(stats['queue_drops'], 1)

    def test_server_closing_midstream_triggers_reconnect(self):
        server = TcpServer()
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: server.conns))
        server.conns[0].close()
        self.assertTrue(wait_for(lambda: t.connection_epoch == 2, timeout=6))
        self.assertEqual(t.get_stats()['reconnects'], 1)

    def test_refused_then_connects(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()                                          # nothing listens on port
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=port)
        t = self.start(cfg)
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertIn('Connection refused', t.get_stats()['last_error'])
        self.assertFalse(t.get_stats()['connected'])
        server = None
        end = time.monotonic() + 8
        while server is None and time.monotonic() < end:
            try:
                srv = socket.socket()
                srv.bind(('127.0.0.1', port))
                srv.listen(1)
                server = srv
            except OSError:
                time.sleep(0.1)
        self.assertIsNotNone(server)
        self.later(server.close)
        self.assertTrue(wait_for(lambda: t.get_stats()['connected'], timeout=10))

    def test_queue_cap_drops_oldest_update_first(self):
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1')
        t = CotTransport(cfg, materials=no_tls)                    # never started
        t.enqueue([('delete', 'keep-me', b'd')])
        for i in range(1004):
            t.enqueue([('update', 'u%d' % i, b'x')])
        stats = t.get_stats()
        self.assertEqual(stats['queue_depth'], 1000)
        self.assertEqual(stats['queue_drops'], 5)
        self.assertEqual(t._queue[0][1], 'keep-me')                # the delete survived
        self.assertEqual(t._queue[1][1], 'u5')                     # u0..u4 were dropped
        # a batch larger than the minimum cap raises the cap to twice its size
        big = CotTransport(cfg, materials=no_tls)
        big.enqueue([('update', 'u', b'x')] * 1500)
        self.assertEqual(big.get_stats()['queue_drops'], 0)

    def test_queue_overflow_with_only_deletes_drops_oldest(self):
        t = CotTransport(default_cfg(tak_transport='tcp'), materials=no_tls)
        for i in range(1003):
            t.enqueue([('delete', 'd%d' % i, b'x')])
        self.assertEqual(t._queue[0][1], 'd3')
        self.assertEqual(t.get_stats()['queue_drops'], 3)

    def test_flush_true_after_delivery_false_when_dead(self):
        server = TcpServer()
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = self.start(cfg)
        t.enqueue([('update', 'a', b'<e/>')])
        self.assertTrue(t.flush(5.0))
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        dead = self.start(default_cfg(tak_transport='tcp', cot_address='127.0.0.1',
                                      cot_port=dead_port))
        dead.enqueue([('update', 'a', b'<e/>')])
        t0 = time.monotonic()
        self.assertFalse(dead.flush(0.4))
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_flush_with_nothing_queued(self):
        t = CotTransport(default_cfg(tak_transport='tcp'), materials=no_tls)
        self.assertTrue(t.flush(0.1))

    def test_stop_returns_quickly_while_connecting_to_a_blackhole(self):
        hole = socket.socket()
        hole.bind(('127.0.0.1', 0))
        hole.listen(0)
        fillers = []
        for _ in range(4):                                       # fill the accept backlog
            s = socket.socket()
            s.setblocking(False)
            try:
                s.connect(hole.getsockname())
            except (BlockingIOError, OSError):
                pass
            fillers.append(s)
        self.later(lambda: [x.close() for x in fillers] + [hole.close()])
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1',
                          cot_port=hole.getsockname()[1])
        t = CotTransport(cfg, materials=no_tls, connect_timeout_s=0.5)
        t.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        t.stop()
        self.assertLess(time.monotonic() - t0, 3.5)
        self.assertEqual(t.get_stats()['state'], 'stopped')

    def test_input_draining_does_not_stall_the_sender(self):
        sent_all = threading.Event()

        def chatty(conn, buf, index):
            def blast():
                try:
                    conn.sendall(b'x' * (8 * 1024 * 1024))
                    sent_all.set()
                except OSError:
                    pass
            threading.Thread(target=blast, daemon=True).start()
        server = TcpServer(on_conn=chatty)
        self.later(server.close)
        cfg = default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=server.port)
        t = self.start(cfg)
        payload = b'<event>' + b'y' * 1000 + b'</event>'
        for _ in range(200):
            t.enqueue([('update', 'u', payload)])
        self.assertTrue(t.flush(15.0))
        self.assertTrue(sent_all.wait(15))                       # the server's 8 MB went out
        self.assertEqual(t.get_stats()['messages_sent'], 200)
        self.assertTrue(wait_for(lambda: len(server.received[0]) == 200 * len(payload)))


class TestDescribe(unittest.TestCase):

    def test_messages(self):
        d = transport.describe_error
        self.assertIn('Connection refused', d(ConnectionRefusedError()))
        self.assertIn('Timed out connecting to h:9', d(socket.timeout(), 'h', 9))
        self.assertIn('Could not resolve host', d(socket.gaierror(-2, 'x'), 'h', 9))
        self.assertIn('Server certificate verification failed',
                      d(ssl.SSLCertVerificationError(1, 'bad')))
        self.assertIn('Server rejected our client certificate',
                      d(ssl.SSLError(1, '[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca')))
        msg = d(ssl.SSLError(1, '[SSL: TLSV1_ALERT_INTERNAL_ERROR] internal error'))
        self.assertIn('Server rejected our client certificate', msg)
        self.assertIn('bare', msg)
        self.assertEqual(d(OSError('plain')), 'plain')

    def test_build_auth_escapes(self):
        self.assertEqual(transport.build_auth('a&b', 'x"y', 'u'),
                         b'<auth><cot username="a&amp;b" password="x&quot;y" uid="u"/></auth>')


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------

class TlsCase(TransportCase):
    """A mutual-TLS server on localhost with generated certificates."""

    @classmethod
    def setUpClass(cls):
        cls.ca = make_ca('Server CA')
        cls.other_ca = make_ca('Other CA')
        cls.srv_key, cls.srv_cert = make_cert(cls.ca, 'srv', sans=('127.0.0.1', 'localhost'))
        cls.bad_name_key, cls.bad_name_cert = make_cert(cls.ca, 'srv', sans=('other.example',))
        cls.cli_key, cls.cli_cert = make_cert(cls.ca, 'client', client=True)
        cls.bad_cli_key, cls.bad_cli_cert = make_cert(cls.other_ca, 'intruder', client=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = certs.CertStore(os.path.join(self.tmp.name, 'tak_certs'))

    def files(self, key, cert, name):
        c = write(os.path.join(self.tmp.name, name + '.crt'), pem(cert))
        k = write(os.path.join(self.tmp.name, name + '.key'), pem(key))
        return c, k

    def make_server(self, cert=None, key=None, client_ca=None, require_client=True,
                    on_conn=None):
        cert = cert or self.srv_cert
        key = key or self.srv_key
        c, k = self.files(key, cert, 'srv%d' % id(cert))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3 if require_client else ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(c, k)
        if require_client:
            ca_file = write(os.path.join(self.tmp.name, 'cca.pem'), pem((client_ca or self.ca).cert))
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=ca_file)
        server = TlsServer(ctx, on_conn)
        self.later(server.close)
        return server

    def install_client(self, key=None, cert=None, ca=None):
        key = key or self.cli_key
        cert = cert or self.cli_cert
        self.store.store_client(certs.normalize_client(pem(cert), pem(key), ''),
                                source='upload', filename='', fmt='pem')
        if ca is not False:
            self.store.store_ca(certs.normalize_ca(pem((ca or self.ca).cert), ''),
                                source='upload', filename='', fmt='pem')

    def cfg(self, server, **kw):
        base = dict(tak_transport='tls', cot_address='127.0.0.1', cot_port=server.port,
                    tak_tls_ca_mode='upload')
        base.update(kw)
        return default_cfg(**base)

    def materials(self, cfg):
        return lambda: certs.resolve_tls_materials(cfg, self.store)


class TlsServer:
    def __init__(self, ctx, on_conn=None):
        self.ctx = ctx
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self.handshake_errors = []
        self.peer_subjects = []
        self.on_conn = on_conn
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            tls = self.ctx.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            self.handshake_errors.append(str(exc))
            conn.close()
            return
        try:
            peer = tls.getpeercert()
            if peer:
                self.peer_subjects.append(peer['subject'])
            if self.on_conn:
                self.on_conn(tls)
            tls.settimeout(0.2)
            while not self._stop:
                try:
                    data = tls.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return
                self.received.extend(data)
        except (ssl.SSLError, OSError):
            return

    def close(self):
        self._stop = True
        self.sock.close()


class TestTlsTransport(TlsCase):

    def test_cot_flows_over_mutual_tls(self):
        server = self.make_server()
        self.install_client()
        cfg = self.cfg(server)
        t = self.start(cfg, materials=self.materials(cfg))
        t.enqueue([('update', 'a', b'<event tls/>')])
        self.assertTrue(wait_for(lambda: bytes(server.received) == b'<event tls/>'))
        stats = t.get_stats()
        self.assertTrue(stats['connected'])
        self.assertIn(stats['tls']['version'], ('TLSv1.3', 'TLSv1.2'))
        self.assertTrue(stats['tls']['cipher'])
        self.assertEqual(stats['tls']['server_subject'], 'CN=srv')
        self.assertTrue(server.peer_subjects)                    # the client cert was presented

    def test_ca_mismatch_fails_verification(self):
        server = self.make_server()
        self.install_client(ca=self.other_ca)                    # trusts the wrong CA
        cfg = self.cfg(server)
        t = self.start(cfg, materials=self.materials(cfg))
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertIn('Server certificate verification failed', t.get_stats()['last_error'])

    def test_hostname_mismatch(self):
        server = self.make_server(cert=self.bad_name_cert, key=self.bad_name_key)
        self.install_client()
        cfg = self.cfg(server)                                   # check_hostname on
        t = self.start(cfg, materials=self.materials(cfg))
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertIn('Server certificate verification failed', t.get_stats()['last_error'])
        t.stop()
        cfg2 = self.cfg(server, tak_tls_check_hostname=False)
        t2 = self.start(cfg2, materials=self.materials(cfg2))
        self.assertTrue(wait_for(lambda: t2.get_stats()['connected']))

    def test_verify_off_connects_and_reports_server_subject(self):
        server = self.make_server(cert=self.bad_name_cert, key=self.bad_name_key)
        self.install_client(ca=False)
        cfg = self.cfg(server, tak_tls_verify=False, tak_tls_ca_mode='none')
        t = self.start(cfg, materials=self.materials(cfg))
        self.assertTrue(wait_for(lambda: t.get_stats()['connected']))
        self.assertEqual(t.get_stats()['tls']['server_subject'], 'CN=srv')

    def test_missing_client_cert_is_reported_with_operator_text(self):
        server = self.make_server()
        cfg = self.cfg(server)
        t = self.start(cfg, materials=self.materials(cfg))
        self.assertTrue(wait_for(lambda: t.get_stats()['last_error']))
        self.assertEqual(t.get_stats()['last_error'], 'No client certificate uploaded')


class TestProbe(TlsCase):

    def probe(self, cfg, **kw):
        return transport.probe_connection(cfg, self.store, None, connect_timeout_s=3.0,
                                          verdict_wait_s=kw.pop('verdict_wait_s', 0.4), **kw)

    def test_tls_accepted(self):
        server = self.make_server()
        self.install_client()
        res = self.probe(self.cfg(server))
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['details']['server_subject'], 'CN=srv')
        self.assertEqual(res['details']['client_subject'], 'CN=client')
        self.assertIn(res['details']['tls_version'], ('TLSv1.3', 'TLSv1.2'))
        self.assertIsInstance(res['duration_ms'], int)
        self.assertEqual(res['error'], '')

    def test_tls_untrusted_client_cert_is_rejected(self):
        server = self.make_server()                              # trusts clients of CA 'Server CA'
        self.install_client(key=self.bad_cli_key, cert=self.bad_cli_cert)
        res = self.probe(self.cfg(server), verdict_wait_s=2.0)
        self.assertFalse(res['ok'], res)
        self.assertIn('rejected our client certificate', res['error'])
        self.assertTrue(server.handshake_errors)

    def test_tls_server_that_sends_data_is_accepted(self):
        server = self.make_server(on_conn=lambda tls: tls.sendall(b'hello'))
        self.install_client()
        res = self.probe(self.cfg(server))
        self.assertTrue(res['ok'], res)

    def test_tls_server_closing_right_after_handshake_is_rejection(self):
        server = self.make_server(on_conn=lambda tls: tls.close())
        self.install_client()
        res = self.probe(self.cfg(server), verdict_wait_s=2.0)
        self.assertFalse(res['ok'], res)
        self.assertTrue('rejected our client certificate' in res['error']
                        or 'closed the connection' in res['error'], res['error'])

    def test_tls_verification_failure(self):
        server = self.make_server()
        self.install_client(ca=self.other_ca)
        res = self.probe(self.cfg(server))
        self.assertFalse(res['ok'])
        self.assertIn('Server certificate verification failed', res['error'])

    def test_tls_no_client_cert(self):
        server = self.make_server()
        res = self.probe(self.cfg(server))
        self.assertFalse(res['ok'])
        self.assertEqual(res['error'], 'No client certificate uploaded')

    def test_tcp_probe(self):
        server = TcpServer()
        self.later(server.close)
        res = self.probe(default_cfg(tak_transport='tcp', cot_address='127.0.0.1',
                                     cot_port=server.port))
        self.assertTrue(res['ok'])
        self.assertEqual(res['details']['transport'], 'tcp')
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        res = self.probe(default_cfg(tak_transport='tcp', cot_address='127.0.0.1', cot_port=port))
        self.assertFalse(res['ok'])
        self.assertIn('Connection refused', res['error'])

    def test_udp_probe_sends_nothing(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(('127.0.0.1', 0))
        rx.settimeout(0.3)
        self.later(rx.close)
        res = self.probe(default_cfg(tak_transport='udp_unicast', cot_address='127.0.0.1',
                                     cot_port=rx.getsockname()[1]))
        self.assertTrue(res['ok'])
        self.assertIn('nothing was sent', res['message'])
        with self.assertRaises(socket.timeout):
            rx.recvfrom(10)
        bad = self.probe(default_cfg(tak_multicast_interface='nosuch0'))
        self.assertFalse(bad['ok'])
        self.assertIn('no IPv4 address', bad['error'])
        mc = self.probe(default_cfg(tak_multicast_interface='lo'))
        self.assertTrue(mc['ok'])
        self.assertIn('via lo', mc['message'])


if __name__ == '__main__':
    unittest.main()
