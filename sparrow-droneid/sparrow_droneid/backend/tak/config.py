"""
TAK output configuration: setting keys, defaults, validation and normalisation.

Pure module (stdlib only; the one piece of I/O is ``socket.gethostname()``).
The settings live in the application's ``settings`` table as strings; this
module turns a flat ``{key: str}`` dict into an immutable :class:`TakConfig`
and validates operator input before it is stored.

The legacy keys ``cot_enabled``, ``cot_address`` and ``cot_port`` remain the
canonical enable / host / port of the TAK output; every other key is ``tak_*``.
"""
import ipaddress
import math
import re
import socket
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

TRANSPORTS = ('udp_multicast', 'udp_broadcast', 'udp_unicast', 'tcp', 'tls')
CLIENT_MODES = ('upload', 'path', 'enroll')
CA_MODES = ('none', 'upload', 'path')
UNKNOWN_AFFILIATIONS = ('u', 'n')
TYPE_FAMILIES = ('uav', 'civil')

TICK_S = 1.0
STALE_CAP_S = 86400.0
EVICTION_AGE_S = 300.0          # DroneIDEngine.cleanup_stale(300) in app.py
SECRET_MASK = '(set)'

DEFAULT_PORT_BY_TRANSPORT = {
    'udp_multicast': 6969,
    'udp_broadcast': 6969,
    'udp_unicast': 4242,    # ATAK's default UDP input
    'tcp': 8087,            # TAK Server plain input
    'tls': 8089,            # TAK Server TLS input
}

# (key, default stored string) in table order.
_KEY_TABLE: Tuple[Tuple[str, str], ...] = (
    ('cot_enabled', 'false'),
    ('cot_address', '239.2.3.1'),
    ('cot_port', '6969'),
    ('tak_transport', 'udp_multicast'),
    ('tak_multicast_interface', ''),
    ('tak_multicast_ttl', '1'),
    ('tak_auth_username', ''),
    ('tak_auth_password', ''),
    ('tak_client_uid', ''),
    ('tak_station_name', ''),
    ('tak_uid_prefix', 'sparrow-droneid'),
    ('tak_tls_client_mode', 'upload'),
    ('tak_tls_client_cert_path', ''),
    ('tak_tls_client_key_path', ''),
    ('tak_tls_client_password', ''),
    ('tak_tls_ca_mode', 'none'),
    ('tak_tls_ca_path', ''),
    ('tak_tls_ca_password', ''),
    ('tak_tls_verify', 'true'),
    ('tak_tls_check_hostname', 'true'),
    ('tak_enroll_port', '8446'),
    ('tak_enroll_username', ''),
    ('tak_enroll_password', ''),
    ('tak_send_interval_s', '2'),
    ('tak_refresh_interval_s', '10'),
    ('tak_stale_after_s', '30'),
    ('tak_delete_after_s', '180'),
    ('tak_send_deletes', 'true'),
    ('tak_unknown_affiliation', 'u'),
    ('tak_drone_type_family', 'uav'),
    ('tak_operator_enabled', 'true'),
    ('tak_line_enabled', 'true'),
    ('tak_include_ssid_detections', 'false'),
    ('tak_sensor_enabled', 'true'),
    ('tak_sensor_heartbeat_s', '10'),
    ('tak_sensor_stale_s', ''),
)

TAK_KEYS: Tuple[str, ...] = tuple(k for k, _ in _KEY_TABLE)
DEFAULTS: Dict[str, str] = dict(_KEY_TABLE)

SECRET_KEYS = frozenset({'tak_auth_password', 'tak_tls_client_password',
                         'tak_tls_ca_password', 'tak_enroll_password'})
BOOL_KEYS = frozenset({
    'cot_enabled', 'tak_tls_verify', 'tak_tls_check_hostname', 'tak_send_deletes',
    'tak_operator_enabled', 'tak_line_enabled', 'tak_include_ssid_detections',
    'tak_sensor_enabled',
})
INT_KEYS = frozenset({'cot_port', 'tak_multicast_ttl', 'tak_enroll_port'})
FLOAT_KEYS = frozenset({
    'tak_send_interval_s', 'tak_refresh_interval_s', 'tak_stale_after_s',
    'tak_delete_after_s', 'tak_sensor_heartbeat_s',
})

_IFACE_RE = re.compile(r'^[A-Za-z0-9_.:@-]{1,15}$')
_CLIENT_UID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
_UID_PREFIX_RE = re.compile(r'^[A-Za-z0-9._-]{1,48}$')
_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    r'(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('0', 'false', 'no', 'off')


@dataclass(frozen=True)
class TakConfig:
    enabled: bool
    transport: str
    host: str
    port: int
    multicast_interface: str
    multicast_ttl: int
    auth_username: str
    auth_password: str
    client_uid: str            # resolved (never blank)
    station_name: str          # resolved (never blank)
    uid_prefix: str
    tls_client_mode: str
    tls_client_cert_path: str
    tls_client_key_path: str
    tls_client_password: str
    tls_ca_mode: str
    tls_ca_path: str
    tls_ca_password: str
    tls_verify: bool
    tls_check_hostname: bool   # already forced False when tls_verify is False
    enroll_port: int
    enroll_username: str
    enroll_password: str
    send_interval_s: float
    refresh_interval_s: float
    stale_after_s: float
    delete_after_s: float
    send_deletes: bool
    unknown_affiliation: str
    drone_type_family: str
    operator_enabled: bool
    line_enabled: bool
    include_ssid_detections: bool
    sensor_enabled: bool
    sensor_heartbeat_s: float
    sensor_stale_s: float      # resolved (auto applied)

    def __repr__(self) -> str:  # never print secrets
        return (f"TakConfig(enabled={self.enabled}, transport={self.transport!r}, "
                f"host={self.host!r}, port={self.port})")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def effective_sensor_heartbeat(hb: float) -> float:
    """Heartbeats fire on emit ticks, so the real period is ceil(hb / tick)."""
    return math.ceil(hb / TICK_S) * TICK_S


def auto_sensor_stale(hb: float) -> float:
    return max(30.0, 3.0 * effective_sensor_heartbeat(hb))


def _fmt(v: float) -> str:
    return '%g' % v


def _parse_bool(s: str) -> Optional[bool]:
    low = s.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _parse_float(s: str) -> Optional[float]:
    try:
        v = float(s.strip())
    except (ValueError, AttributeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _is_ipv4(s: str) -> Optional[ipaddress.IPv4Address]:
    try:
        return ipaddress.IPv4Address(s)
    except ValueError:
        return None


def _address_error(transport: str, address: str) -> Optional[str]:
    """Return an operator-facing error for *address* under *transport*, or None."""
    if transport == 'udp_multicast':
        ip = _is_ipv4(address)
        if ip is None or not ip.is_multicast:
            return 'cot_address must be an IPv4 multicast group (224.0.0.0 - 239.255.255.255) for UDP multicast'
        return None
    if transport == 'udp_broadcast':
        if _is_ipv4(address) is None:
            return 'cot_address must be an IPv4 address for UDP broadcast (e.g. 192.168.1.255)'
        return None
    # udp_unicast, tcp, tls: hostname or IP literal
    if _HOSTNAME_RE.match(address):
        return None
    try:
        ipaddress.ip_address(address)
        return None
    except ValueError:
        return 'cot_address must be a hostname or an IP address'


def _hostname(hostname: Optional[str]) -> str:
    if hostname:
        return hostname
    try:
        return socket.gethostname() or 'sparrow'
    except OSError:
        return 'sparrow'


# ---------------------------------------------------------------------------
# read / merge / mask
# ---------------------------------------------------------------------------

def read_raw(db) -> Dict[str, str]:
    """Stored string values for every TAK key (defaults for missing rows)."""
    return {key: db.get_setting_str(key, DEFAULTS[key]) for key in TAK_KEYS}


def merge_patch(stored_raw: Dict[str, str], patch: dict) -> Dict[str, str]:
    """Apply an API body onto stored strings (decision D11).

    Keys outside TAK_KEYS are ignored (the caller handles them). A secret key
    that is absent or carries the mask ``"(set)"`` keeps the stored value;
    ``""`` clears it; anything else replaces it.
    """
    merged = dict(stored_raw)
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        if key in SECRET_KEYS:
            if value == SECRET_MASK:
                continue
            merged[key] = '' if value is None else (
                value if isinstance(value, str) else str(value))
            continue
        if value is None:
            merged[key] = ''
        elif isinstance(value, bool):
            merged[key] = 'true' if value else 'false'
        elif isinstance(value, str):
            merged[key] = value.strip()
        else:
            merged[key] = str(value)
    return merged


def masked(raw: Dict[str, str]) -> dict:
    """Coerced dict for API output; secrets show ``(set)`` / ``''``."""
    out: dict = {}
    for key in TAK_KEYS:
        v = raw.get(key, DEFAULTS[key])
        if key in SECRET_KEYS:
            out[key] = SECRET_MASK if v else ''
        elif key in BOOL_KEYS:
            b = _parse_bool(v)
            out[key] = b if b is not None else v
        elif key in INT_KEYS:
            i = _parse_int(v)
            out[key] = i if i is not None else v
        elif key in FLOAT_KEYS:
            f = _parse_float(v)
            out[key] = f if f is not None else v
        else:
            out[key] = v
    return out


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def validate(raw: Dict[str, str]) -> List[dict]:
    """Validate a complete raw settings dict. Empty list = valid."""
    errors: List[dict] = []

    def err(field: str, message: str) -> None:
        errors.append({'field': field, 'message': message})

    def g(key: str) -> str:
        v = raw.get(key)
        return DEFAULTS[key] if v is None else v

    # --- booleans ---------------------------------------------------------
    bools: Dict[str, bool] = {}
    for key in sorted(BOOL_KEYS, key=TAK_KEYS.index):
        b = _parse_bool(g(key))
        if b is None:
            err(key, f'{key} must be true or false')
            b = _parse_bool(DEFAULTS[key]) or False
        bools[key] = b

    # --- enums --------------------------------------------------------------
    transport = g('tak_transport')
    if transport not in TRANSPORTS:
        err('tak_transport', 'tak_transport must be one of: ' + ', '.join(TRANSPORTS))
    for key, allowed in (('tak_tls_client_mode', CLIENT_MODES),
                         ('tak_tls_ca_mode', CA_MODES),
                         ('tak_unknown_affiliation', UNKNOWN_AFFILIATIONS),
                         ('tak_drone_type_family', TYPE_FAMILIES)):
        if g(key) not in allowed:
            err(key, f'{key} must be one of: ' + ', '.join(allowed))
    client_mode = g('tak_tls_client_mode')
    ca_mode = g('tak_tls_ca_mode')

    # --- address / port -----------------------------------------------------
    address = g('cot_address')
    if address:
        if transport in TRANSPORTS:
            msg = _address_error(transport, address)
            if msg:
                err('cot_address', msg)
        if len(address) > 253:
            err('cot_address', 'cot_address is too long')
    elif bools['cot_enabled']:
        err('cot_address', 'cot_address is required when the TAK output is enabled')

    port = _parse_int(g('cot_port'))
    if port is None or not 1 <= port <= 65535:
        err('cot_port', 'cot_port must be an integer from 1 to 65535')
    enroll_port = _parse_int(g('tak_enroll_port'))
    if enroll_port is None or not 1 <= enroll_port <= 65535:
        err('tak_enroll_port', 'tak_enroll_port must be an integer from 1 to 65535')

    # --- multicast ----------------------------------------------------------
    iface = g('tak_multicast_interface')
    if iface and not _IFACE_RE.match(iface):
        err('tak_multicast_interface',
            'tak_multicast_interface must be an interface name (up to 15 letters, digits or . _ : @ -)')
    ttl = _parse_int(g('tak_multicast_ttl'))
    if ttl is None or not 1 <= ttl <= 255:
        err('tak_multicast_ttl', 'tak_multicast_ttl must be an integer from 1 to 255')

    # --- identity / auth ------------------------------------------------------
    user = g('tak_auth_username')
    if len(user) > 128 or _CONTROL_RE.search(user):
        err('tak_auth_username', 'tak_auth_username must be at most 128 characters with no control characters')
    if len(g('tak_auth_password')) > 256:
        err('tak_auth_password', 'tak_auth_password must be at most 256 characters')
    cuid = g('tak_client_uid')
    if cuid and not _CLIENT_UID_RE.match(cuid):
        err('tak_client_uid',
            'tak_client_uid may contain only letters, digits and . _ : - (1-128 characters)')
    station = g('tak_station_name')
    if station and (len(station) > 64 or not station.isprintable()):
        err('tak_station_name', 'tak_station_name must be 1-64 printable characters')
    if not _UID_PREFIX_RE.match(g('tak_uid_prefix')):
        err('tak_uid_prefix',
            'tak_uid_prefix may contain only letters, digits and . _ - (1-48 characters)')

    # --- TLS ----------------------------------------------------------------
    for key in ('tak_tls_client_cert_path', 'tak_tls_client_key_path', 'tak_tls_ca_path'):
        if len(g(key)) > 4096:
            err(key, f'{key} must be at most 4096 characters')
    for key in ('tak_tls_client_password', 'tak_tls_ca_password', 'tak_enroll_password'):
        if len(g(key)) > 256:
            err(key, f'{key} must be at most 256 characters')
    if len(g('tak_enroll_username')) > 128:
        err('tak_enroll_username', 'tak_enroll_username must be at most 128 characters')
    if transport == 'tls':
        if client_mode == 'path' and not g('tak_tls_client_cert_path'):
            err('tak_tls_client_cert_path',
                'tak_tls_client_cert_path is required when the client certificate is read from a file')
        if ca_mode == 'path' and not g('tak_tls_ca_path'):
            err('tak_tls_ca_path',
                'tak_tls_ca_path is required when the CA truststore is read from a file')
        if client_mode == 'enroll' and bools['cot_enabled']:
            if not g('tak_enroll_username'):
                err('tak_enroll_username',
                    'tak_enroll_username is required for certificate enrollment')
            if not g('tak_enroll_password'):
                err('tak_enroll_password',
                    'tak_enroll_password is required for certificate enrollment')

    # --- timing ---------------------------------------------------------------
    send = _parse_float(g('tak_send_interval_s'))
    refresh = _parse_float(g('tak_refresh_interval_s'))
    stale = _parse_float(g('tak_stale_after_s'))
    delete = _parse_float(g('tak_delete_after_s'))
    if send is None or not 0.5 <= send <= 60:
        err('tak_send_interval_s', 'tak_send_interval_s must be a number from 0.5 to 60')
        send = None
    if refresh is None or not 1 <= refresh <= 300:
        err('tak_refresh_interval_s', 'tak_refresh_interval_s must be a number from 1 to 300')
        refresh = None
    elif send is not None and refresh < send:
        err('tak_refresh_interval_s',
            'tak_refresh_interval_s must be at least the send interval (%s s)' % _fmt(send))
    if delete is None or not delete <= 300 or delete <= 0:
        err('tak_delete_after_s', 'tak_delete_after_s must be a number up to 300')
        delete = None
    if stale is None or stale <= 0:
        err('tak_stale_after_s', 'tak_stale_after_s must be a positive number')
        stale = None
    if stale is not None:
        if send is not None and refresh is not None:
            need = 3 * max(send, refresh)
            if stale < need:
                err('tak_stale_after_s',
                    'tak_stale_after_s must be at least 3 x max(send interval, refresh interval) = %s s'
                    % _fmt(need))
        if delete is not None and stale > delete:
            err('tak_stale_after_s', 'tak_stale_after_s must not exceed tak_delete_after_s')
    if delete is not None and stale is not None and delete < stale:
        err('tak_delete_after_s', 'tak_delete_after_s must be at least tak_stale_after_s')

    # --- sensor ---------------------------------------------------------------
    hb = _parse_float(g('tak_sensor_heartbeat_s'))
    if hb is None or not 1 <= hb <= 300:
        err('tak_sensor_heartbeat_s', 'tak_sensor_heartbeat_s must be a number from 1 to 300')
        hb = None
    sstale_raw = g('tak_sensor_stale_s')
    if sstale_raw:
        sstale = _parse_float(sstale_raw)
        if sstale is None:
            err('tak_sensor_stale_s', 'tak_sensor_stale_s must be blank (auto) or a number of seconds')
        else:
            lo = 3 * effective_sensor_heartbeat(hb) if hb is not None else 3.0
            if not lo <= sstale <= STALE_CAP_S:
                err('tak_sensor_stale_s',
                    'tak_sensor_stale_s must be blank (auto) or between %s and %s seconds'
                    % (_fmt(lo), _fmt(STALE_CAP_S)))
    return errors


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize(raw: Dict[str, str], *, hostname: Optional[str] = None
              ) -> Tuple[TakConfig, List[str]]:
    """Build a TakConfig from stored strings. Never raises.

    A value that fails validation (a hand-edited database) is clamped into
    range (numbers) or replaced by its default (enums/formats); one warning
    string is returned per corrected key.
    """
    warnings: List[str] = []

    def g(key: str) -> str:
        v = raw.get(key)
        return DEFAULTS[key] if v is None else v

    def warn(key: str, used) -> None:
        warnings.append('setting %s out of range (%r); using %s' % (key, g(key), used))

    def get_bool(key: str) -> bool:
        b = _parse_bool(g(key))
        if b is None:
            b = bool(_parse_bool(DEFAULTS[key]))
            warn(key, b)
        return b

    def get_enum(key: str, allowed: tuple) -> str:
        v = g(key)
        if v not in allowed:
            warn(key, DEFAULTS[key])
            return DEFAULTS[key]
        return v

    def get_int(key: str, lo: int, hi: int, default: int) -> int:
        i = _parse_int(g(key))
        if i is None:
            warn(key, default)
            return default
        c = int(_clamp(i, lo, hi))
        if c != i:
            warn(key, c)
        return c

    def get_float(key: str, lo: float, hi: float, default: float) -> float:
        f = _parse_float(g(key))
        if f is None:
            warn(key, _fmt(default))
            return default
        c = _clamp(f, lo, hi)
        if c != f:
            warn(key, _fmt(c))
        return c

    def get_text(key: str, ok, maxlen: int = 0) -> str:
        v = g(key)
        if v and (not ok(v) or (maxlen and len(v) > maxlen)):
            warn(key, repr(DEFAULTS[key]))
            return DEFAULTS[key]
        return v

    enabled = get_bool('cot_enabled')
    transport = get_enum('tak_transport', TRANSPORTS)

    host = g('cot_address')
    if not host:
        if enabled:
            warn('cot_address', DEFAULTS['cot_address'])
        host = DEFAULTS['cot_address']
    elif _address_error(transport, host):
        warn('cot_address', DEFAULTS['cot_address'])
        host = DEFAULTS['cot_address']
    port = get_int('cot_port', 1, 65535, DEFAULT_PORT_BY_TRANSPORT[transport])

    iface = get_text('tak_multicast_interface', lambda v: bool(_IFACE_RE.match(v)))
    ttl = get_int('tak_multicast_ttl', 1, 255, int(DEFAULTS['tak_multicast_ttl']))

    auth_user = g('tak_auth_username')
    if len(auth_user) > 128 or _CONTROL_RE.search(auth_user):
        warn('tak_auth_username', "''")
        auth_user = ''
    auth_pass = g('tak_auth_password')[:256]

    h = _hostname(hostname)
    client_uid = get_text('tak_client_uid', lambda v: bool(_CLIENT_UID_RE.match(v)))
    if not client_uid:
        client_uid = re.sub(r'[^A-Za-z0-9._:-]', '_', 'sparrow-droneid-' + h)[:128]
    station = g('tak_station_name')
    if station and (len(station) > 64 or not station.isprintable()):
        warn('tak_station_name', "''")
        station = ''
    if not station:
        station = h[:64]
    uid_prefix = g('tak_uid_prefix')
    if not _UID_PREFIX_RE.match(uid_prefix):
        warn('tak_uid_prefix', DEFAULTS['tak_uid_prefix'])
        uid_prefix = DEFAULTS['tak_uid_prefix']

    client_mode = get_enum('tak_tls_client_mode', CLIENT_MODES)
    ca_mode = get_enum('tak_tls_ca_mode', CA_MODES)
    verify = get_bool('tak_tls_verify')
    check_host = get_bool('tak_tls_check_hostname') and verify
    enroll_port = get_int('tak_enroll_port', 1, 65535, int(DEFAULTS['tak_enroll_port']))

    # Timing, in the documented order: send -> refresh (>= send) -> delete
    # (<= 300) -> stale (>= 3 x max(send, refresh), <= delete; when that cannot
    # hold, delete is raised to min(300, 3 x max(...)) and stale = delete).
    # Each key gets at most one warning, decided by comparing the final value
    # with what was stored.
    def stored_float(key: str, default: float) -> Optional[float]:
        return _parse_float(g(key))

    def note(key: str, original: Optional[float], final: float) -> None:
        if original is None or original != final:
            warn(key, _fmt(final))

    send_in = stored_float('tak_send_interval_s', 2.0)
    send = _clamp(send_in if send_in is not None else 2.0, 0.5, 60.0)
    note('tak_send_interval_s', send_in, send)

    refresh_in = stored_float('tak_refresh_interval_s', 10.0)
    refresh = _clamp(refresh_in if refresh_in is not None else 10.0, 1.0, 300.0)
    refresh = max(refresh, send)
    note('tak_refresh_interval_s', refresh_in, refresh)

    delete_in = stored_float('tak_delete_after_s', 180.0)
    delete = _clamp(delete_in if delete_in is not None else 180.0, 1.0, EVICTION_AGE_S)
    stale_in = stored_float('tak_stale_after_s', 30.0)
    stale = stale_in if stale_in is not None else 30.0
    need = 3 * max(send, refresh)
    if need > delete:
        delete = min(EVICTION_AGE_S, need)
        stale = delete
    else:
        stale = _clamp(stale, need, delete)
    note('tak_delete_after_s', delete_in, delete)
    note('tak_stale_after_s', stale_in, stale)

    hb = get_float('tak_sensor_heartbeat_s', 1.0, 300.0, 10.0)
    sstale_raw = g('tak_sensor_stale_s')
    if not sstale_raw:
        sstale = auto_sensor_stale(hb)
    else:
        sv = _parse_float(sstale_raw)
        lo = 3 * effective_sensor_heartbeat(hb)
        if sv is None:
            warn('tak_sensor_stale_s', 'auto')
            sstale = auto_sensor_stale(hb)
        else:
            sstale = _clamp(sv, lo, STALE_CAP_S)
            if sstale != sv:
                warn('tak_sensor_stale_s', _fmt(sstale))

    cfg = TakConfig(
        enabled=enabled, transport=transport, host=host, port=port,
        multicast_interface=iface, multicast_ttl=ttl,
        auth_username=auth_user, auth_password=auth_pass,
        client_uid=client_uid, station_name=station, uid_prefix=uid_prefix,
        tls_client_mode=client_mode,
        tls_client_cert_path=g('tak_tls_client_cert_path')[:4096],
        tls_client_key_path=g('tak_tls_client_key_path')[:4096],
        tls_client_password=g('tak_tls_client_password')[:256],
        tls_ca_mode=ca_mode, tls_ca_path=g('tak_tls_ca_path')[:4096],
        tls_ca_password=g('tak_tls_ca_password')[:256],
        tls_verify=verify, tls_check_hostname=check_host,
        enroll_port=enroll_port, enroll_username=g('tak_enroll_username')[:128],
        enroll_password=g('tak_enroll_password')[:256],
        send_interval_s=send, refresh_interval_s=refresh,
        stale_after_s=stale, delete_after_s=delete,
        send_deletes=get_bool('tak_send_deletes'),
        unknown_affiliation=get_enum('tak_unknown_affiliation', UNKNOWN_AFFILIATIONS),
        drone_type_family=get_enum('tak_drone_type_family', TYPE_FAMILIES),
        operator_enabled=get_bool('tak_operator_enabled'),
        line_enabled=get_bool('tak_line_enabled'),
        include_ssid_detections=get_bool('tak_include_ssid_detections'),
        sensor_enabled=get_bool('tak_sensor_enabled'),
        sensor_heartbeat_s=hb, sensor_stale_s=sstale,
    )
    return cfg, warnings
