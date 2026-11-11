"""
Cursor-on-Target (CoT) XML builders for the TAK output.

Pure functions: no I/O, no clocks (the caller passes ``now``). Every string
that came from a drone's broadcast (self-ID, operator ID, serial, ...) is
untrusted, so it is stripped of characters XML 1.0 forbids, truncated and
escaped before it reaches the XML.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from ..models import DroneIDDevice, IdType, UAType
from .config import STALE_CAP_S, TakConfig

UID_SAFE_RE = re.compile(r'[^A-Za-z0-9._-]')
_XML_INVALID_RE = re.compile(
    '[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]')
_FIELD_MAX = 64
UNKNOWN_M = '9999999.0'
_ROTARY_UA_TYPES = frozenset({2, 3, 13})
_LINE_COLORS = {'h': '-65536', 'f': '-16711681', 'n': '-16711936', 'u': '-256'}
XML_PREFIX = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
DELETE_STALE_S = 20.0

SENSOR_TYPE = 'a-f-G-E-S'
LINE_TYPE = 'u-d-f'
DELETE_TYPE = 't-x-d-d'


# ---------------------------------------------------------------------------
# Escaping and formatting
# ---------------------------------------------------------------------------

def strip_invalid_xml(value: str) -> str:
    """Remove every character that XML 1.0 does not allow."""
    return _XML_INVALID_RE.sub('', value)


def xml_attr(value: str) -> str:
    """Escape *value* for use inside a double-quoted attribute."""
    v = strip_invalid_xml(str(value))
    return (v.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;')
             .replace('\t', '&#9;').replace('\n', '&#10;').replace('\r', '&#13;'))


def xml_text(value: str) -> str:
    """Escape *value* for use as element text."""
    v = strip_invalid_xml(str(value))
    return (v.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;'))


def clean_field(value) -> str:
    """A drone-supplied string, made safe: invalid chars removed, whitespace
    controls flattened to a space (so a field cannot forge a remarks line),
    truncated to 64 characters. Not yet escaped."""
    v = strip_invalid_xml('' if value is None else str(value))
    v = re.sub(r'[\t\r\n]', ' ', v).strip()
    return v[:_FIELD_MAX]


def fmt_time(dt: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.mmmZ`` in UTC."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (dt.microsecond // 1000)


def parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (``Z`` or offset) to an aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stale_time(stale: datetime, now: datetime) -> datetime:
    """Clamp an absolute stale time to [now + 1 s, now + 86400 s]."""
    lo = now + timedelta(seconds=1)
    hi = now + timedelta(seconds=STALE_CAP_S)
    if stale < lo:
        return lo
    if stale > hi:
        return hi
    return stale


def sanitize_uid_part(s: str) -> str:
    out = UID_SAFE_RE.sub('_', s or '')[:96]
    return out or 'unknown'


def id_tail(dev: DroneIDDevice) -> str:
    src = dev.serial_number or dev.registration_id or dev.mac_address or ''
    alnum = ''.join(c for c in src.upper() if c.isalnum() and c.isascii())
    return alnum[-6:] if alnum else 'UNKN'


# ---------------------------------------------------------------------------
# Type selection
# ---------------------------------------------------------------------------

def affiliation_for(disposition: str, cfg: TakConfig) -> str:
    if disposition == 'friendly':
        return 'f'
    if disposition == 'threat':
        return 'h'
    return cfg.unknown_affiliation


def drone_cot_type(ua_type: int, affiliation: str, family: str) -> str:
    rotary = ua_type in _ROTARY_UA_TYPES
    if family == 'civil':
        return 'a-%s-A-C-H' % affiliation if rotary else 'a-%s-A-C-F-q' % affiliation
    return 'a-%s-A-M-F-Q-r' % affiliation if rotary else 'a-%s-A-M-F-Q' % affiliation


@dataclass(frozen=True)
class DroneView:
    key: str
    drone_uid: str
    operator_uid: str
    line_uid: str
    affiliation: str
    drone_type: str
    operator_type: str
    callsign: str
    operator_callsign: str
    operator_is_takeoff: bool
    stale_at: datetime
    type_sig: tuple          # (drone_type, disposition, military, law_enforcement)
    station: str = ''        # receiver name for the 'Sensor:' remark line


def station_segment(cfg: TakConfig) -> str:
    return sanitize_uid_part(cfg.station_name)


def sensor_uid(cfg: TakConfig) -> str:
    return '%s-%s-sensor' % (cfg.uid_prefix, station_segment(cfg))


def has_operator_position(dev: DroneIDDevice) -> bool:
    return dev.operator_lat != 0.0 or dev.operator_lon != 0.0


def has_takeoff_position(dev: DroneIDDevice) -> bool:
    return dev.takeoff_lat != 0.0 or dev.takeoff_lon != 0.0


def drone_view(key: str, dev: DroneIDDevice, cfg: TakConfig) -> DroneView:
    """Build the identifiers, types and stale time for one tracked drone.

    Raises ValueError when ``dev.last_seen`` cannot be parsed (the stale time
    is anchored to it).
    """
    last_seen = parse_iso(dev.last_seen)
    if last_seen is None:
        raise ValueError('unparsable last_seen: %r' % (dev.last_seen,))
    aff = affiliation_for(dev.disposition, cfg)
    drone_type = drone_cot_type(dev.ua_type, aff, cfg.drone_type_family)
    drone_uid = '%s-%s-drone-%s' % (cfg.uid_prefix, station_segment(cfg),
                                    sanitize_uid_part(key))
    tail = id_tail(dev)
    takeoff_only = not has_operator_position(dev) and has_takeoff_position(dev)
    return DroneView(
        key=key,
        drone_uid=drone_uid,
        operator_uid=drone_uid + '-op',
        line_uid=drone_uid + '-line',
        affiliation=aff,
        drone_type=drone_type,
        operator_type='a-%s-G' % aff,
        callsign='UAS-' + tail,
        operator_callsign=('HOME-' if takeoff_only else 'OP-') + tail,
        operator_is_takeoff=takeoff_only,
        stale_at=last_seen + timedelta(seconds=cfg.stale_after_s),
        type_sig=(drone_type, dev.disposition, bool(dev.military),
                  bool(dev.law_enforcement)),
        station=cfg.station_name,
    )


# ---------------------------------------------------------------------------
# Event assembly
# ---------------------------------------------------------------------------

def _event(uid: str, cot_type: str, now: datetime, stale: datetime, how: str,
           point: str, detail: str) -> bytes:
    ts = fmt_time(now)
    xml = ('%s<event version="2.0" uid="%s" type="%s" time="%s" start="%s" stale="%s" how="%s">'
           '%s<detail>%s</detail></event>') % (
        XML_PREFIX, xml_attr(uid), xml_attr(cot_type), ts, ts,
        fmt_time(stale), how, point, detail)
    return xml.encode('utf-8')


def _point(lat: float, lon: float, hae: Optional[float], ce: str, le: str) -> str:
    return '<point lat="%.7f" lon="%.7f" hae="%s" ce="%s" le="%s"/>' % (
        lat, lon, UNKNOWN_M if hae is None else '%.1f' % hae, ce, le)


def _ua_name(ua_type: int) -> str:
    try:
        return UAType(ua_type).display_name
    except ValueError:
        return 'Unknown'


def _id_type_name(id_type: int) -> str:
    try:
        return IdType(id_type).display_name
    except ValueError:
        return 'Unknown'


def _last_seen_text(dev: DroneIDDevice) -> str:
    dt = parse_iso(dev.last_seen)
    if dt is None:
        return clean_field(dev.last_seen)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def build_drone(view: DroneView, dev: DroneIDDevice, cfg: TakConfig,
                now_w: datetime) -> bytes:
    """The drone point event."""
    is_ssid = dev.protocol == 'wifi_ssid'
    hae = None if dev.drone_alt_geo == 0.0 else dev.drone_alt_geo
    if is_ssid:
        point = _point(dev.drone_lat, dev.drone_lon, hae, '500.0', UNKNOWN_M)
    else:
        point = _point(dev.drone_lat, dev.drone_lon, hae, '10.0', '15.0')

    key = clean_field(dev.get_key())
    lines = []
    if is_ssid:
        lines.append("Wi-Fi SSID detection (no Remote ID); position is the receiver's")
    else:
        lines.append('Remote ID drone (Sparrow DroneID)')
    lines.append('ID: %s (%s)' % (key, _id_type_name(dev.id_type)))
    if dev.registration_id:
        lines.append('Registration: ' + clean_field(dev.registration_id))
    if dev.mac_address:
        lines.append('MAC: ' + clean_field(dev.mac_address))
    lines.append('Type: ' + _ua_name(dev.ua_type))
    lines.append('Class: ' + ('military (operator flag)' if dev.military else 'civil'))
    flags = []
    if dev.military:
        flags.append('military')
    if dev.law_enforcement:
        flags.append('law enforcement')
    if flags:
        lines.append('Flags: ' + ', '.join(flags))
    lines.append('Disposition: ' + clean_field(dev.disposition))
    alt = 'Altitude: ' + ('unknown HAE' if hae is None else '%.1f m HAE' % hae)
    if dev.drone_height_agl != 0.0:
        alt += ', %.1f m AGL' % dev.drone_height_agl
    lines.append(alt)
    lines.append('Speed: %.2f m/s, course %.0f deg, vertical %.1f m/s' % (
        dev.speed, dev.direction, dev.vertical_speed))
    if dev.operator_id:
        lines.append('Operator ID: ' + clean_field(dev.operator_id))
    if dev.self_id_text:
        lines.append('Self-ID: ' + clean_field(dev.self_id_text))
    lines.append('RSSI: %d dBm (%s)' % (dev.rssi, clean_field(dev.protocol)))
    lines.append('Last seen: ' + _last_seen_text(dev))
    lines.append('Sensor: ' + clean_field(cfg.station_name))

    detail = '<contact callsign="%s"/>' % xml_attr(view.callsign)
    if not (dev.speed == 0.0 and dev.direction == 0.0) and not is_ssid:
        detail += '<track course="%.1f" speed="%.2f"/>' % (dev.direction, dev.speed)
    detail += '<remarks>%s</remarks>' % xml_text('\n'.join(lines))
    detail += (
        '<__droneid serial="%s" registration_id="%s" mac="%s" key="%s" ua_type="%d" '
        'height_agl="%.1f" operator_lat="%.7f" operator_lon="%.7f" operator_id="%s" '
        'self_id="%s" rssi="%d" protocol="%s" disposition="%s" military="%s" '
        'law_enforcement="%s"/>') % (
        xml_attr(clean_field(dev.serial_number or dev.registration_id or dev.mac_address)),
        xml_attr(clean_field(dev.registration_id)), xml_attr(clean_field(dev.mac_address)),
        xml_attr(key), dev.ua_type, dev.drone_height_agl,
        dev.operator_lat, dev.operator_lon,
        xml_attr(clean_field(dev.operator_id)), xml_attr(clean_field(dev.self_id_text)),
        dev.rssi, xml_attr(clean_field(dev.protocol)),
        xml_attr(clean_field(dev.disposition)),
        'true' if dev.military else 'false',
        'true' if dev.law_enforcement else 'false')
    return _event(view.drone_uid, view.drone_type, now_w,
                  _stale_time(view.stale_at, now_w), 'm-g', point, detail)


def operator_position(dev: DroneIDDevice) -> Optional[Tuple[float, float, Optional[float]]]:
    """(lat, lon, hae-or-None) of the operator, else of the takeoff point."""
    if has_operator_position(dev):
        return dev.operator_lat, dev.operator_lon, (
            dev.operator_alt if dev.operator_alt != 0.0 else None)
    if has_takeoff_position(dev):
        return dev.takeoff_lat, dev.takeoff_lon, None
    return None


def build_operator(view: DroneView, dev: DroneIDDevice, now_w: datetime) -> bytes:
    """The operator (or takeoff) point event; stale follows the drone's."""
    pos = operator_position(dev)
    if pos is None:
        raise ValueError('drone has neither an operator nor a takeoff position')
    lat, lon, hae = pos
    if view.operator_is_takeoff:
        lines = ['Takeoff point for %s (no live operator position)' % view.callsign]
    else:
        lines = ['Remote ID operator position for %s' % view.callsign]
    if dev.operator_id:
        lines.append('Operator ID: ' + clean_field(dev.operator_id))
    lines.append('Sensor: ' + clean_field(view.station))
    point = _point(lat, lon, hae, '10.0', UNKNOWN_M)
    detail = ('<contact callsign="%s"/><link uid="%s" type="%s" relation="p-p"/>'
              '<remarks>%s</remarks>') % (
        xml_attr(view.operator_callsign), xml_attr(view.drone_uid),
        xml_attr(view.drone_type), xml_text('\n'.join(lines)))
    return _event(view.operator_uid, view.operator_type, now_w,
                  _stale_time(view.stale_at, now_w), 'm-g', point, detail)


def build_line(view: DroneView, dev: DroneIDDevice, now_w: datetime) -> bytes:
    """Dashed drone -> operator line; stale follows the drone's."""
    pos = operator_position(dev)
    if pos is None:
        raise ValueError('drone has neither an operator nor a takeoff position')
    olat, olon, _ = pos
    color = _LINE_COLORS.get(view.affiliation, _LINE_COLORS['u'])
    link_name = view.callsign + ' link'
    point = _point(dev.drone_lat, dev.drone_lon, None, UNKNOWN_M, UNKNOWN_M)
    detail = (
        '<link point="%.7f,%.7f"/><link point="%.7f,%.7f"/>'
        '<__shapeExtras cpvis="true" editable="false"/><labels_on value="false"/>'
        '<contact callsign="%s"/><remarks>%s</remarks>'
        '<color value="%s"/><strokeColor value="%s"/><strokeWeight value="2.0"/>'
        '<strokeStyle value="dashed"/>') % (
        dev.drone_lat, dev.drone_lon, olat, olon,
        xml_attr(link_name),
        xml_text('%s to %s' % (view.callsign, view.operator_callsign)),
        color, color)
    return _event(view.line_uid, LINE_TYPE, now_w,
                  _stale_time(view.stale_at, now_w), 'h-e', point, detail)


def build_sensor(cfg: TakConfig, gps: dict, now_w: datetime,
                 drones_tracked: int = 0) -> bytes:
    """The receiver's own point. *gps* is ``GPSEngine.to_dict()``."""
    static = gps.get('mode') == 'static'
    alt = gps.get('altitude') or 0.0
    hae = None if alt == 0.0 else alt
    point = _point(gps.get('latitude', 0.0), gps.get('longitude', 0.0), hae,
                   UNKNOWN_M if static else '10.0', '15.0')
    remarks = 'Sparrow DroneID Remote ID receiver\nDrones tracked: %d' % drones_tracked
    detail = '<contact callsign="%s"/><remarks>%s</remarks>' % (
        xml_attr(cfg.station_name), xml_text(remarks))
    stale = _stale_time(now_w + timedelta(seconds=cfg.sensor_stale_s), now_w)
    return _event(sensor_uid(cfg), SENSOR_TYPE, now_w, stale,
                  'h-e' if static else 'm-g', point, detail)


def build_delete(target_uid: str, target_type: str, now_w: datetime) -> bytes:
    """Delete (with ``__forcedelete``) for a previously sent item."""
    point = '<point lat="0.0" lon="0.0" hae="0.0" ce="%s" le="%s"/>' % (UNKNOWN_M, UNKNOWN_M)
    detail = '<link uid="%s" relation="none" type="%s"/><__forcedelete/>' % (
        xml_attr(target_uid), xml_attr(target_type))
    stale = now_w + timedelta(seconds=DELETE_STALE_S)
    return _event(target_uid + '-delete', DELETE_TYPE, now_w, stale, 'h-e', point, detail)
