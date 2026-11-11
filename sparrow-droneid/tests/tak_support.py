"""
Shared helpers for the TAK tests: generated certificates / CAs / .p12 files /
zip packages, a fake transport and a fake clock. Everything is local; nothing
reads files outside the test's own temporary directories.
"""
import io
import os
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from backend.models import DroneIDDevice
from backend.tak import config as tak_config


def _naive_utc(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def new_key(bits=2048):
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


class Ca:
    def __init__(self, key, cert):
        self.key = key
        self.cert = cert


def make_ca(cn='Test CA', days=3650):
    key = new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_naive_utc(now - timedelta(days=1)))
            .not_valid_after(_naive_utc(now + timedelta(days=days)))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return Ca(key, cert)


def make_cert(ca, cn, sans=('127.0.0.1', 'localhost'), days=365, client=False, key=None):
    """Returns (key, cert). days may be negative (an expired certificate)."""
    import ipaddress
    key = key or new_key()
    now = datetime.now(timezone.utc)
    names = []
    for s in sans:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(s)))
        except ValueError:
            names.append(x509.DNSName(s))
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
               .issuer_name(ca.cert.subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(_naive_utc(now - timedelta(days=max(2, -days + 2))))
               .not_valid_after(_naive_utc(now + timedelta(days=days))))
    if names and not client:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    if client:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    return key, builder.sign(ca.key, hashes.SHA256())


def pem(obj):
    """PEM bytes of a certificate or a private key (PKCS#8, unencrypted)."""
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def key_pem_encrypted(key, password):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.BestAvailableEncryption(password.encode()))


def make_p12(key, cert, cas, password=''):
    enc = (serialization.BestAvailableEncryption(password.encode()) if password
           else serialization.NoEncryption())
    return pkcs12.serialize_key_and_certificates(b'test', key, cert, list(cas or []) or None, enc)


def write(path, data):
    mode = 'wb' if isinstance(data, bytes) else 'w'
    with open(path, mode) as fh:
        fh.write(data)
    return path


class FakeClock:
    def __init__(self, wall=None, mono=1000.0):
        self.wall = wall or datetime(2026, 11, 11, 14, 0, 0, tzinfo=timezone.utc)
        self.mono = mono

    def monotonic(self):
        return self.mono

    def wallclock(self):
        return self.wall

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def make_device(clock, **kw):
    fields = dict(serial_number='1581F5FHD23450019', id_type=1, ua_type=2,
                  drone_lat=32.7157, drone_lon=-117.1611, drone_alt_geo=152.0,
                  drone_height_agl=45.0, speed=5.0, direction=240.0,
                  operator_lat=32.71, operator_lon=-117.17, mac_address='60:60:1F:AA:BB:CC',
                  rssi=-68, protocol='astm_ble', first_seen=iso_z(clock.wallclock()),
                  last_seen=iso_z(clock.wallclock()))
    fields.update(kw)
    return DroneIDDevice(**fields)


class FakeGps:
    def __init__(self, fix=True, lat=32.7, lon=-117.15, alt=12.0, mode='gpsd'):
        self.state = {'mode': mode, 'fix': fix, 'latitude': lat, 'longitude': lon,
                      'altitude': alt, 'speed': 0.0, 'gps_error': ''}

    def to_dict(self):
        return dict(self.state)


class FakeTransport:
    """Records what the engine hands to a transport."""

    instances = []

    def __init__(self, cfg=None, *, materials=None, **kw):
        self.cfg = cfg
        self.materials = materials
        self.batches = []            # (thread name, batch)
        self.events = []             # ordered: 'start', 'enqueue', ('flush', t), 'stop'
        self.connection_epoch = 1
        self.flush_result = True
        self.stats = {'transport': getattr(cfg, 'transport', 'tcp'),
                      'destination': 'x:1', 'connected': True, 'state': 'connected',
                      'messages_sent': 0, 'send_errors': 0, 'reconnects': 0,
                      'queue_depth': 0, 'queue_drops': 0, 'last_error': None,
                      'last_error_at': None, 'last_send_at': None,
                      'connected_since': None, 'tls': None}
        FakeTransport.instances.append(self)

    def start(self):
        self.events.append('start')

    def stop(self):
        self.events.append('stop')

    def enqueue(self, batch):
        self.batches.append((threading.current_thread().name, list(batch)))
        self.events.append('enqueue')

    def flush(self, timeout_s):
        self.events.append(('flush', timeout_s))
        return self.flush_result

    def get_stats(self):
        return dict(self.stats)

    # convenience
    def last_batch(self):
        return self.batches[-1][1] if self.batches else []

    def uids(self, kind=None):
        out = []
        for _t, batch in self.batches:
            out.extend(u for k, u, _p in batch if kind is None or k == kind)
        return out


class FakeEnrollment:
    def __init__(self, cfg=None, store=None, **kw):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {'status': 'cached'}


def build_zip(members, *, compression=zipfile.ZIP_DEFLATED, encrypted_flag_on=None):
    """A zip from {name: bytes}. encrypted_flag_on: a member name whose 'encrypted'
    flag bit is set in the central directory afterwards (the data is not actually
    encrypted; zipfile resets the flag while writing, hence the byte patch)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            zf.writestr(info, data)
    data = bytearray(buf.getvalue())
    if encrypted_flag_on is not None:
        patch_central_directory(data, encrypted_flag_on, flag_bits=0x1)
    return bytes(data)


def patch_central_directory(data, member, *, flag_bits=None, file_size=None,
                            compress_size=None):
    """Patch one member's central-directory record in a zip bytearray, in place."""
    import struct
    pos = -1
    while True:
        pos = data.find(b'PK\x01\x02', pos + 1)
        if pos < 0:
            raise KeyError(member)
        name_len = struct.unpack_from('<H', data, pos + 28)[0]
        if bytes(data[pos + 46:pos + 46 + name_len]) == member.encode():
            break
    if flag_bits is not None:
        flags = struct.unpack_from('<H', data, pos + 8)[0]
        struct.pack_into('<H', data, pos + 8, flags | flag_bits)
    if compress_size is not None:
        struct.pack_into('<I', data, pos + 20, compress_size)
    if file_size is not None:
        struct.pack_into('<I', data, pos + 24, file_size)


def default_cfg(**overrides):
    raw = dict(tak_config.DEFAULTS)
    for k, v in overrides.items():
        if isinstance(v, bool):
            v = 'true' if v else 'false'
        raw[k] = str(v)
    cfg, _warnings = tak_config.normalize(raw, hostname='rx1')
    return cfg
