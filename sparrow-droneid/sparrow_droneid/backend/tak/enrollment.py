"""
TAK Server certificate enrollment.

``EnrollmentClient`` performs one enrollment exchange (fetch the CSR subject
template, generate a key, sign a CSR, post it, parse the answer).
``EnrollmentManager`` owns the cached certificate, the renewal schedule and the
failure backoff, and runs on its own ``tak-enroll`` daemon thread.

Never logs or stores the enrollment password, and no exception message built
here interpolates it.
"""
import base64
import hashlib
import json
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import client as http_client
from typing import Callable, Dict, List, Optional

from . import certs
from .certs import CertError, CertStore, TlsMaterials, iso_z

logger = logging.getLogger(__name__)

KEY_SIZE = 4096
RENEW_BEFORE = timedelta(days=14)
FULL_CHECK_INTERVAL = timedelta(hours=24)
BACKOFF_FIRST_S = 300.0
BACKOFF_MAX_S = 3600.0
_LOOP_TICK_S = 60.0
_NAME_ORDER = ('C', 'ST', 'L', 'O', 'OU')

_SHARED_BACKOFF: Dict[str, dict] = {}
_SHARED_BACKOFF_LOCK = threading.Lock()


def identity_key(cfg) -> str:
    """Backoff / cache identity: host, enrollment port, username, password, client uid."""
    raw = '|'.join([cfg.host, str(cfg.enroll_port), cfg.enroll_username,
                    cfg.enroll_password, cfg.client_uid])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


class EnrollError(Exception):
    """An enrollment failure with an operator-facing message."""


@dataclass
class EnrollResult:
    key_pem: bytes
    cert_pem: bytes
    ca_pems: List[bytes]
    subject: str
    not_after: datetime


def _reject_dtd(body: bytes) -> None:
    low = body.lower()
    if b'<!doctype' in low or b'<!entity' in low:
        raise EnrollError('The TAK Server answer contains a DTD, which is not accepted')


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


_PEM_BLOCK_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.DOTALL)


def _to_pem_blocks(value: str) -> List[str]:
    """PEM certificate block(s) from a value with or without armour / ``\\n`` escapes."""
    text = value.replace('\\r', '').replace('\\n', '\n').strip()
    if '-----BEGIN' in text:
        return _PEM_BLOCK_RE.findall(text)
    body = re.sub(r'\s+', '', text)
    if not body:
        return []
    wrapped = '\n'.join(body[i:i + 64] for i in range(0, len(body), 64))
    return ['-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----' % wrapped]


class EnrollmentClient:
    """One enrollment exchange against ``https://host:port/Marti/api/tls``."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 client_uid: str, ssl_context, timeout_s: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self.client_uid = client_uid
        self.ssl_context = ssl_context
        self.timeout_s = timeout_s

    def _auth_header(self) -> str:
        raw = ('%s:%s' % (self.username, self._password)).encode('utf-8')
        return 'Basic ' + base64.b64encode(raw).decode('ascii')

    def _request(self, method: str, path: str, body: Optional[bytes] = None,
                 headers: Optional[dict] = None) -> bytes:
        hdrs = {'Authorization': self._auth_header()}
        hdrs.update(headers or {})
        conn = http_client.HTTPSConnection(self.host, self.port, context=self.ssl_context,
                                           timeout=self.timeout_s)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read(2 * 1024 * 1024)
            status = resp.status
        except EnrollError:
            raise
        except Exception as exc:
            from .transport import describe_error
            raise EnrollError(describe_error(exc, self.host, self.port))
        finally:
            conn.close()
        route = path.split('?', 1)[0]
        if status == 401:
            raise EnrollError('Enrollment login rejected (HTTP 401): check username/password')
        if status != 200:
            raise EnrollError('HTTP %d from %s' % (status, route))
        return data

    # -- steps -----------------------------------------------------------------

    def _subject_template(self) -> Dict[str, str]:
        body = self._request('GET', '/Marti/api/tls/config', headers={'Accept': 'application/xml'})
        _reject_dtd(body)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise EnrollError('TAK Server sent an unreadable /Marti/api/tls/config: %s' % exc)
        entries: Dict[str, str] = {}
        for el in root.iter():
            if _local(el.tag) in ('nameEntry', 'entry'):
                name = el.get('name')
                value = el.get('value')
                if name in _NAME_ORDER and value and name not in entries:
                    entries[name] = value
        return entries

    def enroll(self) -> EnrollResult:
        certs._require_crypto()
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        template = self._subject_template()
        key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
        oids = {'C': NameOID.COUNTRY_NAME, 'ST': NameOID.STATE_OR_PROVINCE_NAME,
                'L': NameOID.LOCALITY_NAME, 'O': NameOID.ORGANIZATION_NAME,
                'OU': NameOID.ORGANIZATIONAL_UNIT_NAME}
        attrs = []
        try:
            for name in _NAME_ORDER:
                if name in template:
                    attrs.append(x509.NameAttribute(oids[name], template[name]))
            attrs.append(x509.NameAttribute(NameOID.COMMON_NAME, self.username))
            csr = (x509.CertificateSigningRequestBuilder()
                   .subject_name(x509.Name(attrs))
                   .sign(key, hashes.SHA256()))
        except ValueError as exc:
            raise EnrollError('TAK Server sent a subject field we cannot use: %s' % exc)
        csr_pem = csr.public_bytes(serialization.Encoding.PEM)

        path = '/Marti/api/tls/signClient/v2?clientUid=%s' % urllib.parse.quote(
            self.client_uid, safe='')
        body = self._request('POST', path, body=csr_pem,
                             headers={'Content-Type': 'application/pkcs10',
                                      'Accept': 'application/json'})
        signed, cas = self._parse_sign_response(body)
        try:
            leaf = x509.load_pem_x509_certificate(signed.encode('ascii'))
        except (ValueError, UnicodeEncodeError) as exc:
            raise EnrollError('TAK Server returned an unreadable certificate: %s' % exc)
        pub = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if (leaf.public_key().public_bytes(*pub) != key.public_key().public_bytes(*pub)):
            raise EnrollError('TAK Server returned a certificate for a different key')
        ca_pems = []
        for block in cas:
            try:
                x509.load_pem_x509_certificate(block.encode('ascii'))
            except (ValueError, UnicodeEncodeError) as exc:
                raise EnrollError('TAK Server returned an unreadable CA certificate: %s' % exc)
            ca_pems.append(block.encode('ascii') + b'\n')
        return EnrollResult(
            key_pem=key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()),
            cert_pem=signed.encode('ascii') + b'\n', ca_pems=ca_pems,
            subject=leaf.subject.rfc4514_string(), not_after=certs.cert_not_after(leaf))

    @staticmethod
    def _parse_sign_response(body: bytes):
        """(signed_cert_pem, [ca_pem, ...]) from a JSON or XML answer."""
        fields: Dict[str, str] = {}
        try:
            obj = json.loads(body.decode('utf-8'))
            if isinstance(obj, dict):
                fields = {k: v for k, v in obj.items() if isinstance(v, str)}
        except (ValueError, UnicodeDecodeError):
            _reject_dtd(body)
            try:
                root = ET.fromstring(body)
            except ET.ParseError as exc:
                raise EnrollError('TAK Server sent an unreadable signing answer: %s' % exc)
            for el in root.iter():
                name = _local(el.tag)
                if (name == 'signedCert' or re.fullmatch(r'ca\d*', name)) and el.text:
                    fields[name] = el.text
        signed_blocks = _to_pem_blocks(fields.get('signedCert', ''))
        if not signed_blocks:
            raise EnrollError('TAK Server did not return a signed certificate')
        cas: List[str] = []
        for name in sorted((n for n in fields if re.fullmatch(r'ca\d*', n)),
                           key=lambda n: (len(n), n)):
            cas.extend(_to_pem_blocks(fields[name]))
        return signed_blocks[0], cas


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class EnrollmentManager:
    """Cached-certificate owner with renewal and failure backoff."""

    def __init__(self, cfg, cert_store: CertStore, *,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic,
                 backoff_store: Optional[Dict[str, dict]] = None,
                 client_factory: Callable[..., EnrollmentClient] = EnrollmentClient) -> None:
        self._cfg = cfg
        self._store = cert_store
        self._wallclock = wallclock
        self._monotonic = monotonic
        self._client_factory = client_factory
        self._backoff = _SHARED_BACKOFF if backoff_store is None else backoff_store

        self._lock = threading.Lock()          # status fields
        self._check_lock = threading.Lock()    # serialises whole checks
        self._status = 'pending'
        self._last_attempt_at: Optional[datetime] = None
        self._last_success_at: Optional[datetime] = None
        self._next_attempt_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._last_full: Optional[datetime] = None

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force_request = False
        self._thread: Optional[threading.Thread] = None
        self._generation = 0

    # ---- identity ---------------------------------------------------------------

    def _identity(self) -> dict:
        c = self._cfg
        return {'host': c.host, 'port': c.enroll_port, 'username': c.enroll_username,
                'client_uid': c.client_uid}

    def identity_key(self) -> str:
        return identity_key(self._cfg)

    # ---- thread -----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='tak-enroll', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._generation += 1       # late results of an in-flight attempt are discarded
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(0.5)
        self._thread = None

    def request_now(self) -> None:
        """Ask the manager thread for a forced check (ignores cache validity)."""
        self._force_request = True
        self._wake.set()

    def enroll_now(self, timeout_s: float = 40.0) -> dict:
        """Forced check on a helper thread, waiting at most *timeout_s*; returns status()."""
        worker = threading.Thread(
            target=lambda: self._safe_check(full=True, force=True),
            name='tak-enroll-now', daemon=True)
        worker.start()
        worker.join(timeout_s)
        return self.status()

    def ensure_certificate(self) -> str:
        """Synchronous full check (the connection probe's path). Returns the status."""
        return self._safe_check(full=True, force=False)

    def _run(self) -> None:
        self._safe_check(full=True, force=False)
        while not self._stop.is_set():
            self._wake.wait(_LOOP_TICK_S)
            self._wake.clear()
            if self._stop.is_set():
                break
            force, self._force_request = self._force_request, False
            now = self._wallclock()
            due = self._last_full is None or now - self._last_full >= FULL_CHECK_INTERVAL
            with self._lock:
                retry_at = self._next_attempt_at
            if retry_at is not None and now >= retry_at:
                due = True      # a failed attempt whose backoff has elapsed
            if force or due:
                self._safe_check(full=True, force=force)

    def _safe_check(self, full: bool, force: bool) -> str:
        try:
            return self.check(full=full, force=force)
        except Exception:
            logger.exception('TAK enrollment check crashed')
            with self._lock:
                self._status = 'failed'
                self._last_error = 'Internal enrollment error (see the log)'
            return 'failed'

    # ---- cache --------------------------------------------------------------------

    def _meta_matches(self, meta: dict) -> bool:
        ident = self._identity()
        return all(meta.get(k) == v for k, v in ident.items())

    def _cached(self):
        """(TlsMaterials | None, reason) for the cached certificate."""
        store = self._store
        cert_pem = store.read('enroll/cert.pem')
        key_pem = store.read('enroll/key.pem')
        meta = store._read_json('enroll/meta.json')
        if cert_pem is None or key_pem is None or meta is None:
            return None, 'no enrolled certificate yet'
        if not self._meta_matches(meta):
            return None, 'the enrolled certificate belongs to a different server or user'
        if not certs.HAS_CRYPTOGRAPHY:
            return None, 'cryptography is not installed'
        summary = certs.pem_cert_summary(cert_pem)
        if summary is None:
            return None, 'the enrolled certificate is unreadable'
        try:
            if not certs.key_matches_cert(cert_pem, key_pem):
                return None, 'the enrolled certificate does not match its key'
        except CertError:
            return None, 'the enrolled certificate is unreadable'
        if summary['not_after'] <= self._wallclock():
            return None, 'the enrolled certificate has expired'
        ca_path = store.path('enroll/ca_chain.pem') if store.exists('enroll/ca_chain.pem') else None
        return TlsMaterials(cert_file=store.path('enroll/cert.pem'),
                            key_file=store.path('enroll/key.pem'), ca_file=ca_path,
                            subject=summary['subject'], not_after=summary['not_after'],
                            source='enrollment'), ''

    def current_material(self) -> TlsMaterials:
        """The cached certificate; never touches the network."""
        mats, reason = self._cached()
        if mats is None:
            st = self.status()
            detail = st.get('last_error') or reason
            raise CertError('Waiting for certificate enrollment (%s: %s)' % (st['status'], detail))
        return mats

    # ---- checks -------------------------------------------------------------------

    def _ssl_context(self):
        c = self._cfg
        ca_file = None
        if c.tls_ca_mode == 'upload' and self._store.exists('ca.pem'):
            ca_file = self._store.path('ca.pem')
        elif c.tls_ca_mode == 'path' and c.tls_ca_path:
            ca_file = self._store.resolve_path_ca(c)
        return certs.build_ssl_context(verify=c.tls_verify, check_hostname=c.tls_check_hostname,
                                       ca_file=ca_file)

    def check(self, full: bool = True, force: bool = False) -> str:
        """Run one check. Enrolls when needed; honours the failure backoff."""
        if not full:
            return self.status()['status']
        with self._check_lock:
            with self._lock:
                generation = self._generation
            now = self._wallclock()
            mats, _reason = self._cached()
            valid = mats is not None
            expiring = valid and mats.not_after - now < RENEW_BEFORE
            need = force or not valid or expiring
            with self._lock:
                self._last_full = now
            if not need:
                with self._lock:
                    self._status = 'cached'
                    self._next_attempt_at = None
                return 'cached'

            if not self._cfg.enroll_username or not self._cfg.enroll_password:
                with self._lock:
                    self._last_error = 'Enrollment username and password are not set'
                    self._status = 'cached' if valid else 'failed'
                return self._status

            key = self.identity_key()
            state = self._backoff_get(key)
            if state and state['next_attempt'] > now:
                with self._lock:
                    self._next_attempt_at = state['next_attempt']
                    self._last_error = 'Enrollment backed off until %s after: %s' % (
                        iso_z(state['next_attempt']), state['error'])
                    self._status = 'cached' if valid else 'failed'
                return self._status

            with self._lock:
                self._status = 'enrolling'
                self._last_attempt_at = now
            try:
                result = self._client_factory(
                    self._cfg.host, self._cfg.enroll_port, self._cfg.enroll_username,
                    self._cfg.enroll_password, self._cfg.client_uid,
                    self._ssl_context()).enroll()
                self._write_result(result)
            except (EnrollError, CertError, OSError) as exc:
                message = str(exc) or exc.__class__.__name__
                fails = (state['fails'] if state else 0) + 1
                delay = min(BACKOFF_FIRST_S * (2 ** (fails - 1)), BACKOFF_MAX_S)
                nxt = self._wallclock() + timedelta(seconds=delay)
                self._backoff_set(key, {'fails': fails, 'next_attempt': nxt, 'error': message})
                logger.warning('TAK enrollment failed (attempt %d, next in %d s): %s',
                               fails, int(delay), message)
                with self._lock:
                    if generation == self._generation:
                        self._last_error = message
                        self._next_attempt_at = nxt
                        self._status = 'cached' if valid else 'failed'
                return self._status
            self._backoff_clear(key)
            with self._lock:
                if generation == self._generation:
                    self._status = 'enrolled'
                    self._last_success_at = self._wallclock()
                    self._last_error = None
                    self._next_attempt_at = None
            return 'enrolled'

    def _write_result(self, result: EnrollResult) -> None:
        store = self._store
        store.write_atomic('enroll/key.pem', result.key_pem)
        store.write_atomic('enroll/cert.pem', result.cert_pem)
        if result.ca_pems:
            store.write_atomic('enroll/ca_chain.pem', b''.join(result.ca_pems))
        else:
            store.remove('enroll/ca_chain.pem')
        meta = dict(self._identity())
        meta.update(enrolled_at=iso_z(self._wallclock()), not_after=iso_z(result.not_after))
        store._write_json('enroll/meta.json', meta)

    # ---- backoff table ----------------------------------------------------------------

    def _backoff_get(self, key: str) -> Optional[dict]:
        with _SHARED_BACKOFF_LOCK:
            return self._backoff.get(key)

    def _backoff_set(self, key: str, state: dict) -> None:
        with _SHARED_BACKOFF_LOCK:
            if len(self._backoff) > 64:
                self._backoff.clear()
            self._backoff[key] = state

    def _backoff_clear(self, key: str) -> None:
        with _SHARED_BACKOFF_LOCK:
            self._backoff.pop(key, None)

    # ---- status -------------------------------------------------------------------------

    def status(self) -> dict:
        mats, _reason = self._cached()
        with self._lock:
            return {
                'status': self._status,
                'last_attempt_at': iso_z(self._last_attempt_at),
                'last_success_at': iso_z(self._last_success_at),
                'next_attempt_at': iso_z(self._next_attempt_at),
                'last_error': self._last_error,
                'cert_subject': mats.subject if mats else None,
                'cert_not_after': iso_z(mats.not_after) if mats else None,
            }
