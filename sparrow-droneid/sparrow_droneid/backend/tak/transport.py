"""
CoT transport: a sender thread that owns the socket, plus the connection probe.

The emit thread only ever calls :meth:`CotTransport.enqueue` (a bounded,
never-blocking hand-off); every socket operation (connect, TLS handshake,
``<auth>``, writes, draining anything the server sends back, reconnect with
backoff) happens on the ``tak-sender`` thread.
"""
import collections
import errno
import fcntl
import logging
import select
import socket
import ssl
import struct
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Deque, List, Optional, Tuple
from xml.sax.saxutils import escape

from .certs import (CertError, TlsMaterials, build_ssl_context, iso_z,
                    resolve_tls_materials)
from .config import TakConfig

logger = logging.getLogger(__name__)

_SIOCGIFADDR = 0x8915
_QUEUE_MIN_CAP = 1000
_BACKOFF_MAX_S = 30.0
_STABLE_LINK_S = 10.0
_LOG_EVERY = 100


class TransportError(Exception):
    """A transport-level problem with an operator-facing message."""


class _ServerRejected(Exception):
    """Raised by the probe when the server drops us after the TLS handshake."""


# ---------------------------------------------------------------------------
# Error descriptions (shared with the probe and enrollment)
# ---------------------------------------------------------------------------

def describe_error(exc: BaseException, host: str = '', port: int = 0) -> str:
    """An operator-facing one-liner for a connection failure."""
    dest = '%s:%s' % (host, port) if host else ''
    if isinstance(exc, _ServerRejected):
        return str(exc)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return 'Server certificate verification failed: %s' % (
            getattr(exc, 'verify_message', '') or exc)
    if isinstance(exc, ssl.SSLError):
        text = str(exc)
        low = text.lower()
        if 'certificate' in low or 'unknown ca' in low or 'internal error' in low:
            hint = ''
            if 'internal error' in low:
                hint = (' (TAK Server answers an untrusted client certificate with a bare'
                        ' "internal error" alert)')
            return 'Server rejected our client certificate: %s%s' % (text, hint)
        return 'TLS error: %s' % text
    if isinstance(exc, ConnectionRefusedError):
        return 'Connection refused (is the port right / server up?)'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'Timed out connecting to %s' % dest if dest else 'Timed out'
    if isinstance(exc, socket.gaierror):
        return 'Could not resolve host%s' % ((' ' + host) if host else '')
    if isinstance(exc, (CertError, TransportError)):
        return str(exc)
    if isinstance(exc, OSError) and exc.errno == errno.ENETUNREACH:
        return 'Network is unreachable'
    return str(exc) or exc.__class__.__name__


def _attr(value: str) -> str:
    cleaned = ''.join(ch for ch in value if ch >= ' ' and ch != '\x7f')
    return escape(cleaned, {'"': '&quot;'})


def build_auth(username: str, password: str, uid: str) -> bytes:
    return ('<auth><cot username="%s" password="%s" uid="%s"/></auth>' % (
        _attr(username), _attr(password), _attr(uid))).encode('utf-8')


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

def interface_ipv4(name: str) -> Optional[str]:
    """IPv4 address of interface *name*, or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack('256s', name.encode('utf-8')[:15])
        res = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, packed)
        return socket.inet_ntoa(res[20:24])
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


def list_ipv4_interfaces() -> List[dict]:
    """``[{'name': 'eth0', 'ipv4': '192.168.1.10'}]`` without ``lo``."""
    out: List[dict] = []
    try:
        names = [n for _i, n in socket.if_nameindex()]
    except OSError:
        return out
    for name in names:
        if name == 'lo':
            continue
        ip = interface_ipv4(name)
        if ip:
            out.append({'name': name, 'ipv4': ip})
    return out


# ---------------------------------------------------------------------------
# Socket construction (shared by the sender and the probe)
# ---------------------------------------------------------------------------

def open_udp_socket(cfg: TakConfig) -> Tuple[socket.socket, Tuple[str, int]]:
    """Create a configured UDP socket and its destination address."""
    family = socket.AF_INET
    host = cfg.host
    if cfg.transport == 'udp_unicast':
        infos = socket.getaddrinfo(cfg.host, cfg.port, socket.AF_UNSPEC, socket.SOCK_DGRAM)
        if not infos:
            raise socket.gaierror('no address for %s' % cfg.host)
        family = infos[0][0]
        sock = socket.socket(family, socket.SOCK_DGRAM)
        return sock, infos[0][4][:2]
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if cfg.transport == 'udp_multicast':
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, cfg.multicast_ttl)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            if cfg.multicast_interface:
                ip = interface_ipv4(cfg.multicast_interface)
                if not ip:
                    raise TransportError(
                        'Interface %s has no IPv4 address' % cfg.multicast_interface)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(ip))
        elif cfg.transport == 'udp_broadcast':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except BaseException:
        sock.close()
        raise
    return sock, (host, cfg.port)


def _tls_info(sock: ssl.SSLSocket) -> dict:
    cipher = sock.cipher()
    subject = ''
    try:
        peer = sock.getpeercert()
        if peer:
            parts = []
            for rdn in peer.get('subject', ()):
                for k, v in rdn:
                    parts.append('%s=%s' % ({'commonName': 'CN', 'organizationName': 'O',
                                             'organizationalUnitName': 'OU',
                                             'countryName': 'C'}.get(k, k), v))
            subject = ','.join(parts)
    except (ValueError, OSError):
        pass
    return {'version': sock.version(), 'cipher': cipher[0] if cipher else None,
            'server_subject': subject}


def _peer_subject_unverified(sock: ssl.SSLSocket) -> str:
    """Server subject even when verification is off (getpeercert is empty then)."""
    try:
        der = sock.getpeercert(binary_form=True)
        if not der:
            return ''
        from cryptography import x509
        return x509.load_der_x509_certificate(der).subject.rfc4514_string()
    except Exception:
        return ''


def connect_tls(cfg: TakConfig, mats: TlsMaterials, timeout_s: float) -> ssl.SSLSocket:
    ctx = build_ssl_context(verify=cfg.tls_verify, check_hostname=cfg.tls_check_hostname,
                            ca_file=mats.ca_file, cert_file=mats.cert_file,
                            key_file=mats.key_file)
    raw = socket.create_connection((cfg.host, cfg.port), timeout=timeout_s)
    try:
        return ctx.wrap_socket(raw, server_hostname=cfg.host)
    except BaseException:
        raw.close()
        raise


# ---------------------------------------------------------------------------
# CotTransport
# ---------------------------------------------------------------------------

class CotTransport:
    """Bounded send queue + one sender thread for all five transports."""

    def __init__(self, cfg: TakConfig, *, materials: Callable[[], TlsMaterials],
                 monotonic: Callable[[], float] = time.monotonic,
                 connect_timeout_s: float = 10.0, send_timeout_s: float = 5.0) -> None:
        self._cfg = cfg
        self._materials = materials
        self._monotonic = monotonic
        self._connect_timeout_s = connect_timeout_s
        self._send_timeout_s = send_timeout_s

        # Queue state: guarded by _cond.
        self._cond = threading.Condition(threading.Lock())
        self._queue: Deque[Tuple[str, str, bytes]] = collections.deque()
        self._cap = _QUEUE_MIN_CAP
        self._submitted = 0
        self._finished = 0

        # Counters and connection facts: guarded by _stats_lock.
        self._stats_lock = threading.Lock()
        self._state = 'stopped'
        self._epoch = 0
        self._messages_sent = 0
        self._send_errors = 0
        self._reconnects = 0
        self._queue_drops = 0
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[datetime] = None
        self._last_send_at: Optional[datetime] = None
        self._connected_since: Optional[datetime] = None
        self._tls: Optional[dict] = None
        self._connected = False

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._failures_logged = 0

    # ---- public API -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        with self._stats_lock:
            self._state = 'connecting'
        self._thread = threading.Thread(target=self._run, name='tak-sender', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the sender: set the flag, close the socket (unblocks a send), join <= 3 s."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._close_socket()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(3.0)
        self._thread = None
        with self._stats_lock:
            self._state = 'stopped'
            self._connected = False

    def enqueue(self, batch: List[Tuple[str, str, bytes]]) -> None:
        """Queue (kind, uid, payload) tuples; never blocks."""
        if not batch:
            return
        with self._cond:
            self._cap = max(_QUEUE_MIN_CAP, 2 * len(batch))
            for item in batch:
                self._queue.append(item)
                self._submitted += 1
            dropped = 0
            while len(self._queue) > self._cap:
                self._drop_oldest_locked()
                dropped += 1
            self._cond.notify_all()
        if dropped:
            with self._stats_lock:
                self._queue_drops += dropped

    def flush(self, timeout_s: float) -> bool:
        """Wait until everything enqueued so far was written, failed or dropped."""
        with self._cond:
            target = self._submitted
            return self._cond.wait_for(lambda: self._finished >= target, timeout=timeout_s)

    @property
    def connection_epoch(self) -> int:
        with self._stats_lock:
            return self._epoch

    def get_stats(self) -> dict:
        with self._cond:
            depth = len(self._queue)
        with self._stats_lock:
            return {
                'transport': self._cfg.transport,
                'destination': '%s:%s' % (self._cfg.host, self._cfg.port),
                'connected': self._connected,
                'state': self._state,
                'messages_sent': self._messages_sent,
                'send_errors': self._send_errors,
                'reconnects': self._reconnects,
                'queue_depth': depth,
                'queue_drops': self._queue_drops,
                'last_error': self._last_error,
                'last_error_at': iso_z(self._last_error_at),
                'last_send_at': iso_z(self._last_send_at),
                'connected_since': iso_z(self._connected_since),
                'tls': dict(self._tls) if self._tls else None,
            }

    # ---- queue helpers ----------------------------------------------------------

    def _drop_oldest_locked(self) -> None:
        """Drop the oldest update (else the oldest entry). Caller holds _cond."""
        for idx, item in enumerate(self._queue):
            if item[0] == 'update':
                del self._queue[idx]
                break
        else:
            self._queue.popleft()
        self._finished += 1
        # queue_drops is counted by the caller (stats lock order: never nested).

    def _purge_updates(self) -> int:
        with self._cond:
            keep = collections.deque(i for i in self._queue if i[0] != 'update')
            purged = len(self._queue) - len(keep)
            self._queue = keep
            self._finished += purged
            self._cond.notify_all()
        if purged:
            with self._stats_lock:
                self._queue_drops += purged
        return purged

    def _take(self, wait_s: float) -> Optional[Tuple[str, str, bytes]]:
        with self._cond:
            if not self._queue:
                self._cond.wait(wait_s)
            if self._queue and not self._stop.is_set():
                return self._queue.popleft()
        return None

    def _finish_one(self) -> None:
        with self._cond:
            self._finished += 1
            self._cond.notify_all()

    # ---- socket helpers -----------------------------------------------------------

    def _close_socket(self) -> None:
        with self._sock_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _set_socket(self, sock: socket.socket) -> None:
        with self._sock_lock:
            self._sock = sock

    def _sleep(self, seconds: float) -> None:
        end = self._monotonic() + seconds
        while not self._stop.is_set():
            left = end - self._monotonic()
            if left <= 0:
                return
            self._stop.wait(min(0.2, left))

    # ---- sender thread ---------------------------------------------------------------

    def _run(self) -> None:
        cfg = self._cfg
        is_udp = cfg.transport.startswith('udp_')
        backoff = 1.0
        ever_connected = False
        while not self._stop.is_set():
            sock: Optional[socket.socket] = None
            in_flight = None
            try:
                with self._stats_lock:
                    self._state = 'connecting'
                dest = None
                if is_udp:
                    sock, dest = open_udp_socket(cfg)
                    sock.settimeout(self._send_timeout_s)
                elif cfg.transport == 'tcp':
                    sock = socket.create_connection((cfg.host, cfg.port),
                                                    timeout=self._connect_timeout_s)
                else:
                    sock = connect_tls(cfg, self._materials(), self._connect_timeout_s)
                self._set_socket(sock)
                if self._stop.is_set():
                    raise TransportError('stopping')
                connected_at = self._monotonic()
                with self._stats_lock:
                    self._epoch += 1
                    epoch = self._epoch
                    if ever_connected and not is_udp:
                        self._reconnects += 1
                    self._connected = True
                    self._state = 'connected'
                    self._connected_since = datetime.now(timezone.utc)
                    self._tls = None
                    if isinstance(sock, ssl.SSLSocket):
                        self._tls = _tls_info(sock)
                        if not self._tls['server_subject']:
                            self._tls['server_subject'] = _peer_subject_unverified(sock)
                ever_connected = True
                if epoch > 1 and not is_udp:
                    self._purge_updates()
                if not is_udp and cfg.auth_username:
                    sock.settimeout(self._send_timeout_s)
                    sock.sendall(build_auth(cfg.auth_username, cfg.auth_password,
                                            cfg.client_uid))
                self._failures_logged = 0
                while not self._stop.is_set():
                    if backoff > 1.0 and self._monotonic() - connected_at >= _STABLE_LINK_S:
                        backoff = 1.0
                    if not is_udp:
                        self._drain_input(sock)
                    item = self._take(0.2)
                    if item is None:
                        continue
                    in_flight = item
                    if is_udp:
                        sock.sendto(item[2], dest)
                    else:
                        sock.settimeout(self._send_timeout_s)
                        sock.sendall(item[2])
                    in_flight = None
                    self._finish_one()
                    with self._stats_lock:
                        self._messages_sent += 1
                        self._last_send_at = datetime.now(timezone.utc)
            except (OSError, ssl.SSLError, CertError, TransportError, ConnectionError) as exc:
                if self._stop.is_set():
                    break
                if in_flight is not None:
                    self._finish_one()
                message = describe_error(exc, cfg.host, cfg.port)
                with self._stats_lock:
                    if in_flight is not None:
                        self._send_errors += 1
                    self._last_error = message
                    self._last_error_at = datetime.now(timezone.utc)
                    self._connected = False
                    self._state = 'error'
                    self._tls = None
                self._log_failure(message)
            finally:
                self._close_socket()
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop.is_set():
                break
            self._sleep(min(backoff, 1.0) if is_udp else backoff)
            backoff = min(backoff * 2.0, _BACKOFF_MAX_S)
        with self._stats_lock:
            self._connected = False
            self._state = 'stopped'

    def _drain_input(self, sock: socket.socket) -> None:
        """Read and discard whatever the server sent so its send buffer never fills."""
        while True:
            pending = isinstance(sock, ssl.SSLSocket) and sock.pending() > 0
            if not pending:
                readable, _, _ = select.select([sock], [], [], 0)
                if not readable:
                    return
            sock.setblocking(False)
            try:
                data = sock.recv(65536)
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                return
            finally:
                try:
                    sock.settimeout(self._send_timeout_s)
                except OSError:
                    pass
            if data == b'':
                raise ConnectionError('peer closed the connection')

    def _log_failure(self, message: str) -> None:
        self._failures_logged += 1
        if self._failures_logged == 1 or self._failures_logged % _LOG_EVERY == 0:
            logger.warning('TAK %s to %s:%s failed (%d consecutive): %s',
                           self._cfg.transport, self._cfg.host, self._cfg.port,
                           self._failures_logged, message)


# ---------------------------------------------------------------------------
# Connection probe ("Test connection")
# ---------------------------------------------------------------------------

PROBE_TOTAL_TIMEOUT_S = 30.0
_VERDICT_WAIT_S = 2.0


def probe_connection(cfg: TakConfig, cert_store, enrollment=None,
                     connect_timeout_s: float = 10.0,
                     verdict_wait_s: float = _VERDICT_WAIT_S) -> dict:
    """Try the configured connection once and report the outcome.

    Sends nothing. Always returns a dict (``ok``, ``message``, ``error``,
    ``duration_ms``, ``details``); connection failures are results, not
    exceptions. *enrollment* is an object with ``ensure_certificate() -> str``
    and ``current_material()`` (the EnrollmentManager) used in enroll mode.
    """
    started = time.monotonic()
    result: dict = {}
    done = threading.Event()

    def worker() -> None:
        try:
            result.update(_probe(cfg, cert_store, enrollment, connect_timeout_s,
                                 verdict_wait_s))
        except BaseException as exc:  # a probe must never raise into the API layer
            logger.exception('TAK probe crashed')
            result.update({'ok': False, 'message': '', 'error': 'Internal error: %s' % exc,
                           'details': {}})
        finally:
            done.set()

    threading.Thread(target=worker, name='tak-probe', daemon=True).start()
    if not done.wait(PROBE_TOTAL_TIMEOUT_S):
        result = {'ok': False, 'message': '',
                  'error': 'Timed out after %d s' % PROBE_TOTAL_TIMEOUT_S, 'details': {}}
    result.setdefault('details', {})
    result['duration_ms'] = int((time.monotonic() - started) * 1000)
    return result


def _probe(cfg, cert_store, enrollment, connect_timeout_s, verdict_wait_s) -> dict:
    dest = '%s:%s' % (cfg.host, cfg.port)
    details: dict = {'transport': cfg.transport, 'destination': dest}

    def fail(msg: str) -> dict:
        return {'ok': False, 'message': '', 'error': msg, 'details': details}

    if cfg.transport.startswith('udp_'):
        try:
            sock, addr = open_udp_socket(cfg)
        except (OSError, TransportError) as exc:
            return fail(describe_error(exc, cfg.host, cfg.port))
        sock.close()
        via = (' via %s' % cfg.multicast_interface
               if cfg.transport == 'udp_multicast' and cfg.multicast_interface else '')
        return {'ok': True, 'error': '', 'details': details,
                'message': 'UDP is connectionless; nothing was sent. Destination %s%s validated.'
                           % (dest, via)}

    if cfg.transport == 'tcp':
        try:
            sock = socket.create_connection((cfg.host, cfg.port), timeout=connect_timeout_s)
        except (OSError, TransportError) as exc:
            return fail(describe_error(exc, cfg.host, cfg.port))
        _quiet_close(sock)
        return {'ok': True, 'error': '', 'details': details,
                'message': 'Connected to %s over TCP (nothing was sent).' % dest}

    # --- TLS -------------------------------------------------------------------
    if cfg.tls_client_mode == 'enroll':
        if enrollment is None:
            return fail('Certificate enrollment is not configured')
        try:
            status = enrollment.ensure_certificate()
        except Exception as exc:  # EnrollError and friends carry operator text
            return fail(str(exc))
        if status in ('enrolled', 'cached'):
            details['enrollment'] = status
        else:
            snap = enrollment.status()
            return fail('Certificate enrollment failed: %s' % (snap.get('last_error') or status))
    try:
        mats = resolve_tls_materials(cfg, cert_store, enrollment)
    except CertError as exc:
        return fail(str(exc))
    if mats.subject:
        details['client_subject'] = mats.subject
    if mats.not_after:
        details['client_not_after'] = iso_z(mats.not_after)

    try:
        sock = connect_tls(cfg, mats, connect_timeout_s)
    except (OSError, ssl.SSLError, CertError, TransportError) as exc:
        return fail(describe_error(exc, cfg.host, cfg.port))
    try:
        info = _tls_info(sock)
        if not info['server_subject']:
            info['server_subject'] = _peer_subject_unverified(sock)
        details.update(tls_version=info['version'], cipher=info['cipher'],
                       server_subject=info['server_subject'])
        # TLS 1.3: a refused client certificate only shows up after the handshake.
        sock.settimeout(verdict_wait_s)
        try:
            data = sock.recv(1)
        except (socket.timeout, TimeoutError):
            data = None            # still open after the wait: accepted
        except (ssl.SSLError, ConnectionResetError, BrokenPipeError) as exc:
            return fail('Server rejected our client certificate after the handshake: %s' % exc)
        if data == b'':
            return fail('Server closed the connection right after the TLS handshake; it most '
                        'likely rejected our client certificate')
        return {'ok': True, 'error': '', 'details': details,
                'message': 'Connected to %s over %s; the server accepted the connection.'
                           % (dest, info['version'])}
    finally:
        _quiet_close(sock)


def _quiet_close(sock: socket.socket) -> None:
    try:
        sock.close()
    except Exception:
        pass
