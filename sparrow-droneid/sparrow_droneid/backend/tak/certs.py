"""
Client certificates, CA truststores and the on-disk certificate store.

Everything an operator can hand us (a .p12, a PEM pair, a combined PEM, a DER
certificate) is detected by content, never by filename, and normalised at
upload time into PEM files under ``<data_dir>/tak_certs/`` with the private
key decrypted (mode 0600), so the runtime needs a single TLS code path and
never a password.

Only cryptography APIs available in 3.4.8 (Ubuntu 22.04) are used. In
particular: no ``pkcs12.load_pkcs12``, no ``x509.load_pem_x509_certificates``
and no unconditional ``not_valid_after_utc``; ``backend=`` is never passed.
"""
import fcntl
import json
import os
import re
import ssl
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:  # cryptography is a soft dependency: UDP and TCP work without it.
    import cryptography  # noqa: F401
    HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - exercised only where it is missing
    HAS_CRYPTOGRAPHY = False

_DIR_LOCK = threading.Lock()

_PEM_CERT_RE = re.compile(
    rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.DOTALL)
_PEM_KEY_RE = re.compile(
    rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----',
    re.DOTALL)

WRONG_PASSWORD = 'Wrong password for the .p12 / key'


class CertError(ValueError):
    """An operator-facing certificate problem (the message is shown as-is)."""


@dataclass
class ClientMaterial:
    cert_chain_pem: bytes      # leaf first
    key_pem: bytes             # PKCS#8, unencrypted
    subject: str
    issuer: str
    not_after: datetime        # UTC


@dataclass
class CaMaterial:
    bundle_pem: bytes
    count: int
    subjects: List[str]
    earliest_not_after: datetime


@dataclass
class TlsMaterials:
    cert_file: str
    key_file: str
    ca_file: Optional[str]
    subject: str = ''
    not_after: Optional[datetime] = None
    source: str = ''


def _require_crypto() -> None:
    if not HAS_CRYPTOGRAPHY:
        raise CertError('cryptography is not installed')


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> str:
    """'pem', 'der_cert' or 'p12', judged by content only."""
    if data.lstrip()[:11] == b'-----BEGIN ':
        return 'pem'
    if HAS_CRYPTOGRAPHY:
        from cryptography import x509
        try:
            x509.load_der_x509_certificate(data)
            return 'der_cert'
        except Exception:
            pass
    return 'p12'


def cert_not_after(cert) -> datetime:
    """Expiry of *cert* as an aware UTC datetime on every cryptography version."""
    aware = getattr(cert, 'not_valid_after_utc', None)
    if aware is not None:
        return aware
    return cert.not_valid_after.replace(tzinfo=timezone.utc)


def _pem_certs(data: bytes) -> list:
    from cryptography import x509
    certs = []
    for block in _PEM_CERT_RE.findall(data):
        try:
            certs.append(x509.load_pem_x509_certificate(block))
        except ValueError as exc:
            raise CertError('Unreadable certificate in the PEM data: %s' % exc)
    return certs


def _spki(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(serialization.Encoding.DER,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)


def _cert_pem(cert) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.PEM)


def _load_key(block: bytes, password: str):
    from cryptography.hazmat.primitives import serialization
    try:
        return serialization.load_pem_private_key(
            block, password.encode('utf-8') if password else None)
    except TypeError:
        raise CertError(WRONG_PASSWORD + ' (the key is encrypted and needs a password)')
    except ValueError as exc:
        msg = str(exc).lower()
        if 'password' in msg or 'decrypt' in msg or 'bad' in msg:
            raise CertError(WRONG_PASSWORD)
        raise CertError('Unreadable private key: %s' % exc)


def _load_p12(data: bytes, password: str):
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        return pkcs12.load_key_and_certificates(
            data, password.encode('utf-8') if password else None)
    except ValueError as exc:
        msg = str(exc).lower()
        if 'password' in msg or 'mac' in msg or 'decrypt' in msg or 'invalid' in msg:
            raise CertError(WRONG_PASSWORD)
        raise CertError('Unreadable .p12 file: %s' % exc)


def normalize_client(cert_bytes: bytes, key_bytes: Optional[bytes],
                     password: str) -> ClientMaterial:
    """Turn any supported client-certificate input into leaf-first PEM + PKCS#8 key."""
    _require_crypto()
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    kind = sniff(cert_bytes)
    if kind == 'p12':
        key, cert, extra = _load_p12(cert_bytes, password)
        if cert is None:
            raise CertError('The .p12 has no client certificate')
        if key is None:
            raise CertError('The .p12 has no private key')
        certs = [cert] + list(extra or [])
    else:
        if kind == 'der_cert':
            certs = [x509.load_der_x509_certificate(cert_bytes)]
        else:
            certs = _pem_certs(cert_bytes)
        if not certs:
            raise CertError('No certificate found in the file')
        if key_bytes:
            key_source = key_bytes
            if sniff(key_bytes) != 'pem':
                raise CertError('The key file is not a PEM private key')
        else:
            key_source = cert_bytes if kind == 'pem' else b''
        blocks = _PEM_KEY_RE.findall(key_source)
        if not blocks:
            raise CertError('No private key found: choose a key file, a .p12 or a combined PEM')
        key = _load_key(blocks[0], password)

    key_spki = _spki(key.public_key())
    leaf = None
    for c in certs:
        if _spki(c.public_key()) == key_spki:
            leaf = c
            break
    if leaf is None:
        raise CertError('The private key does not match the certificate')
    ordered = [leaf] + [c for c in certs if c is not leaf]
    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return ClientMaterial(
        cert_chain_pem=b''.join(_cert_pem(c) for c in ordered),
        key_pem=key_pem,
        subject=leaf.subject.rfc4514_string(),
        issuer=leaf.issuer.rfc4514_string(),
        not_after=cert_not_after(leaf))


def normalize_ca(data: bytes, password: str) -> CaMaterial:
    """Turn a truststore (.p12, PEM bundle or DER certificate) into a PEM bundle."""
    _require_crypto()
    from cryptography import x509

    kind = sniff(data)
    if kind == 'p12':
        _key, cert, extra = _load_p12(data, password)
        certs = ([cert] if cert is not None else []) + list(extra or [])
    elif kind == 'der_cert':
        certs = [x509.load_der_x509_certificate(data)]
    else:
        certs = _pem_certs(data)
    if not certs:
        raise CertError('No CA certificates found')
    return CaMaterial(
        bundle_pem=b''.join(_cert_pem(c) for c in certs),
        count=len(certs),
        subjects=[c.subject.rfc4514_string() for c in certs],
        earliest_not_after=min(cert_not_after(c) for c in certs))


def pem_cert_summary(pem: bytes) -> Optional[dict]:
    """Subject/issuer/expiry of the first certificate in a PEM file, or None."""
    if not HAS_CRYPTOGRAPHY:
        return None
    try:
        certs = _pem_certs(pem)
    except CertError:
        return None
    if not certs:
        return None
    leaf = certs[0]
    return {'subject': leaf.subject.rfc4514_string(),
            'issuer': leaf.issuer.rfc4514_string(),
            'not_after': cert_not_after(leaf),
            'chain_length': len(certs),
            'subjects': [c.subject.rfc4514_string() for c in certs],
            'earliest_not_after': min(cert_not_after(c) for c in certs)}


def key_matches_cert(cert_pem: bytes, key_pem: bytes) -> bool:
    """True when the PEM private key belongs to the first certificate."""
    _require_crypto()
    from cryptography.hazmat.primitives import serialization
    certs = _pem_certs(cert_pem)
    if not certs:
        return False
    try:
        key = serialization.load_pem_private_key(key_pem, None)
    except (ValueError, TypeError):
        return False
    return _spki(key.public_key()) == _spki(certs[0].public_key())


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _days_left(not_after: datetime, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int((not_after - now).total_seconds() // 86400)


# ---------------------------------------------------------------------------
# SSL context shared by the transport, the probe and enrollment
# ---------------------------------------------------------------------------

def build_ssl_context(*, verify: bool, check_hostname: bool,
                      ca_file: Optional[str] = None,
                      cert_file: Optional[str] = None,
                      key_file: Optional[str] = None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if verify:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = check_hostname
        if ca_file:
            ctx.load_verify_locations(cafile=ca_file)
        else:
            ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cert_file:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return ctx


# ---------------------------------------------------------------------------
# On-disk store
# ---------------------------------------------------------------------------

class CertStore:
    """Files under ``cert_dir``: dir mode 0700, files 0600, created on demand."""

    def __init__(self, cert_dir: str) -> None:
        self.cert_dir = cert_dir
        self._rt_lock = threading.Lock()
        self._rt_fingerprints: dict = {}

    # ---- paths ------------------------------------------------------------

    def path(self, relpath: str) -> str:
        return os.path.join(self.cert_dir, relpath)

    def exists(self, relpath: str) -> bool:
        return os.path.isfile(self.path(relpath))

    def read(self, relpath: str) -> Optional[bytes]:
        try:
            with open(self.path(relpath), 'rb') as fh:
                return fh.read()
        except OSError:
            return None

    def _ensure_dir(self, directory: str) -> None:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

    # ---- writing ------------------------------------------------------------

    def write_atomic(self, relpath: str, data: bytes) -> str:
        """Write *data* to ``cert_dir/relpath`` atomically with mode 0600."""
        target = self.path(relpath)
        with _DIR_LOCK:
            self._ensure_dir(self.cert_dir)
            self._ensure_dir(os.path.dirname(target))
            lock_fd = os.open(self.path('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix='.tmp-')
                try:
                    os.chmod(tmp, 0o600)
                    with os.fdopen(fd, 'wb') as fh:
                        fh.write(data)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, target)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
        return target

    def remove(self, *relpaths: str) -> bool:
        removed = False
        with _DIR_LOCK:
            for rel in relpaths:
                try:
                    os.unlink(self.path(rel))
                    removed = True
                except FileNotFoundError:
                    pass
        return removed

    def _write_json(self, relpath: str, obj: dict) -> None:
        self.write_atomic(relpath, json.dumps(obj, sort_keys=True).encode('utf-8'))

    def _read_json(self, relpath: str) -> Optional[dict]:
        raw = self.read(relpath)
        if raw is None:
            return None
        try:
            val = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
        return val if isinstance(val, dict) else None

    @staticmethod
    def _now_iso() -> str:
        return iso_z(datetime.now(timezone.utc)) or ''

    def store_client(self, mat: ClientMaterial, *, source: str, filename: str,
                     fmt: str) -> dict:
        self.write_atomic('client_key.pem', mat.key_pem)
        self.write_atomic('client_cert.pem', mat.cert_chain_pem)
        meta = {'source': source, 'filename': filename, 'format': fmt,
                'subject': mat.subject, 'issuer': mat.issuer,
                'not_after': iso_z(mat.not_after), 'stored_at': self._now_iso()}
        self._write_json('client_meta.json', meta)
        return self.client_info() or meta

    def store_ca(self, mat: CaMaterial, *, source: str, filename: str, fmt: str) -> dict:
        self.write_atomic('ca.pem', mat.bundle_pem)
        meta = {'source': source, 'filename': filename, 'format': fmt,
                'count': mat.count, 'subjects': mat.subjects,
                'stored_at': self._now_iso()}
        self._write_json('ca_meta.json', meta)
        return self.ca_info() or meta

    def delete_client(self) -> bool:
        return self.remove('client_cert.pem', 'client_key.pem', 'client_meta.json')

    def delete_ca(self) -> bool:
        return self.remove('ca.pem', 'ca_meta.json')

    # ---- info -----------------------------------------------------------------

    def _cert_info(self, cert_rel: str, meta: dict, source: str) -> Optional[dict]:
        pem = self.read(cert_rel)
        if pem is None:
            return None
        summary = pem_cert_summary(pem)
        info = {
            'subject': meta.get('subject', ''),
            'issuer': meta.get('issuer', ''),
            'not_after': meta.get('not_after'),
            'days_left': None,
            'expired': False,
            'source': meta.get('source', source),
            'filename': meta.get('filename', ''),
            'format': meta.get('format', 'pem'),
            'stored_at': meta.get('stored_at'),
            'chain_length': 1,
        }
        if summary:
            na = summary['not_after']
            info.update(subject=summary['subject'], issuer=summary['issuer'],
                        not_after=iso_z(na), days_left=_days_left(na),
                        expired=na <= datetime.now(timezone.utc),
                        chain_length=summary['chain_length'])
        return info

    def client_info(self) -> Optional[dict]:
        meta = self._read_json('client_meta.json') or {}
        return self._cert_info('client_cert.pem', meta, 'upload')

    def enrolled_info(self) -> Optional[dict]:
        meta = self._read_json('enroll/meta.json') or {}
        info = self._cert_info('enroll/cert.pem', {
            'subject': '', 'issuer': '', 'not_after': meta.get('not_after'),
            'source': 'enrollment', 'filename': '', 'format': 'pem',
            'stored_at': meta.get('enrolled_at')}, 'enrollment')
        if info is not None:
            info['source'] = 'enrollment'
        return info

    def ca_info(self) -> Optional[dict]:
        pem = self.read('ca.pem')
        if pem is None:
            return None
        meta = self._read_json('ca_meta.json') or {}
        summary = pem_cert_summary(pem)
        return {
            'count': summary['chain_length'] if summary else meta.get('count', 0),
            'subjects': summary['subjects'] if summary else meta.get('subjects', []),
            'earliest_not_after': (iso_z(summary['earliest_not_after']) if summary
                                   else None),
            'source': meta.get('source', 'upload'),
            'filename': meta.get('filename', ''),
            'format': meta.get('format', 'pem'),
            'stored_at': meta.get('stored_at'),
        }

    # ---- path mode (operator files normalised into runtime/) -------------------

    @staticmethod
    def _stat_sig(path: str) -> Tuple[int, int]:
        try:
            st = os.stat(path)
        except OSError:
            raise CertError('File not found: %s' % path)
        return (st.st_mtime_ns, st.st_size)

    @staticmethod
    def _read_operator_file(path: str) -> bytes:
        try:
            with open(path, 'rb') as fh:
                return fh.read(4 * 1024 * 1024 + 1)
        except OSError as exc:
            raise CertError('Cannot read %s: %s' % (path, exc.strerror or exc))

    def resolve_path_material(self, cfg) -> Tuple[str, str]:
        """Normalise the operator's client files into ``runtime/``.

        Rebuilt only when a source file's (mtime, size) or the password changed.
        """
        if not cfg.tls_client_cert_path:
            raise CertError('No client certificate file configured')
        fingerprint = (cfg.tls_client_cert_path, cfg.tls_client_key_path,
                       cfg.tls_client_password,
                       self._stat_sig(cfg.tls_client_cert_path),
                       self._stat_sig(cfg.tls_client_key_path)
                       if cfg.tls_client_key_path else None)
        cert_file = self.path('runtime/client_cert.pem')
        key_file = self.path('runtime/client_key.pem')
        with self._rt_lock:
            if (self._rt_fingerprints.get('client') == fingerprint
                    and os.path.isfile(cert_file) and os.path.isfile(key_file)):
                return cert_file, key_file
            cert_bytes = self._read_operator_file(cfg.tls_client_cert_path)
            key_bytes = (self._read_operator_file(cfg.tls_client_key_path)
                         if cfg.tls_client_key_path else None)
            mat = normalize_client(cert_bytes, key_bytes, cfg.tls_client_password)
            self.write_atomic('runtime/client_key.pem', mat.key_pem)
            self.write_atomic('runtime/client_cert.pem', mat.cert_chain_pem)
            self._rt_fingerprints['client'] = fingerprint
            self._rt_fingerprints['client_info'] = (mat.subject, mat.not_after)
        return cert_file, key_file

    def path_client_summary(self) -> Optional[Tuple[str, datetime]]:
        with self._rt_lock:
            return self._rt_fingerprints.get('client_info')

    def resolve_path_ca(self, cfg) -> str:
        if not cfg.tls_ca_path:
            raise CertError('No CA truststore file configured')
        fingerprint = (cfg.tls_ca_path, cfg.tls_ca_password,
                       self._stat_sig(cfg.tls_ca_path))
        ca_file = self.path('runtime/ca.pem')
        with self._rt_lock:
            if self._rt_fingerprints.get('ca') == fingerprint and os.path.isfile(ca_file):
                return ca_file
            data = self._read_operator_file(cfg.tls_ca_path)
            mat = normalize_ca(data, cfg.tls_ca_password)
            self.write_atomic('runtime/ca.pem', mat.bundle_pem)
            self._rt_fingerprints['ca'] = fingerprint
        return ca_file


def resolve_tls_materials(cfg, store: CertStore, enrollment=None) -> TlsMaterials:
    """Resolve the TLS client certificate and CA for *cfg* (section 6.2).

    Raises CertError with an operator-facing message. Does no network I/O;
    *enrollment* is only asked for its cached certificate.
    """
    _require_crypto()
    mode = cfg.tls_client_mode
    ca_file: Optional[str] = None
    if mode == 'upload':
        if not (store.exists('client_cert.pem') and store.exists('client_key.pem')):
            raise CertError('No client certificate uploaded')
        info = store.client_info() or {}
        cert_file = store.path('client_cert.pem')
        key_file = store.path('client_key.pem')
        subject = info.get('subject', '')
        not_after = _parse_iso_z(info.get('not_after'))
        source = 'upload'
    elif mode == 'path':
        cert_file, key_file = store.resolve_path_material(cfg)
        subject, not_after = store.path_client_summary() or ('', None)
        if not key_file:
            key_file = cert_file
        source = 'path'
    else:
        if enrollment is None:
            raise CertError('Certificate enrollment is not running')
        mats = enrollment.current_material()
        cert_file, key_file = mats.cert_file, mats.key_file
        subject, not_after = mats.subject, mats.not_after
        ca_file = mats.ca_file
        source = 'enrollment'

    if cfg.tls_ca_mode == 'upload':
        if not store.exists('ca.pem'):
            raise CertError('No CA truststore uploaded')
        ca_file = store.path('ca.pem')
    elif cfg.tls_ca_mode == 'path':
        ca_file = store.resolve_path_ca(cfg)
    # ca_mode 'none': system store, except that enrollment's own chain is used.
    return TlsMaterials(cert_file=cert_file, key_file=key_file, ca_file=ca_file,
                        subject=subject, not_after=not_after, source=source)


def _parse_iso_z(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
