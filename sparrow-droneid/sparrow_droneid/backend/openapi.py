"""
OpenAPI 3.0.3 specification builder for Sparrow DroneID.

This module is the single source of truth for all API schemas and provides:
  - SCHEMAS dict  — component schemas registered via _schema()
  - Helper functions for building inline spec fragments on route decorators
  - build_openapi_spec(router) — assembles the full OpenAPI document
  - validate_request(spec, query_params, json_data) — lightweight request validation

Schemas are derived directly from models.py dataclasses and the actual
response shapes emitted by api_handler.py route handlers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# API version
# ---------------------------------------------------------------------------

API_VERSION = '1.0.0'

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

TAGS = [
    {'name': 'System',        'description': 'Server status and version'},
    {'name': 'Monitoring',    'description': 'Start / stop packet capture and monitor status'},
    {'name': 'Detections',    'description': 'Live drone detection list and per-drone detail'},
    {'name': 'History',       'description': 'Historical detection records and timeline replay'},
    {'name': 'Export',        'description': 'KML and other data export formats'},
    {'name': 'Alerts',        'description': 'Alert rules, log, and acknowledgement'},
    {'name': 'GPS',           'description': 'Receiver GPS position'},
    {'name': 'CoT',           'description': 'Cursor-on-Target (TAK) output configuration'},
    {'name': 'Tiles',         'description': 'Map tile proxy (OSM / ESRI)'},
    {'name': 'Data',          'description': 'Database statistics and data-purge operations'},
    {'name': 'Geozones',      'description': 'Airport and no-fly zone data'},
    {'name': 'Settings',      'description': 'Application-wide settings'},
    {'name': 'Certificates',  'description': 'TLS certificate management'},
    {'name': 'Vendor Codes',  'description': 'Drone vendor / manufacturer lookup tables'},
    {'name': 'WiFi SSID',     'description': 'WiFi SSID drone-detection patterns and scanner status'},
]

# ---------------------------------------------------------------------------
# Internal schema registry
# ---------------------------------------------------------------------------

SCHEMAS: Dict[str, dict] = {}


def _schema(name: str, obj: dict) -> dict:
    """Register *obj* under *name* in SCHEMAS and return a $ref to it."""
    SCHEMAS[name] = obj
    return {'$ref': f'#/components/schemas/{name}'}


def _ref(name: str) -> dict:
    """Return a $ref to an already-registered schema."""
    return {'$ref': f'#/components/schemas/{name}'}


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _str(**kw) -> dict:
    return {'type': 'string', **kw}


def _int(**kw) -> dict:
    return {'type': 'integer', **kw}


def _num(**kw) -> dict:
    return {'type': 'number', **kw}


def _bool(**kw) -> dict:
    return {'type': 'boolean', **kw}


def _nullable(schema: dict) -> dict:
    return {**schema, 'nullable': True}


def _obj(properties: dict, required: Optional[List[str]] = None, **kw) -> dict:
    s = {'type': 'object', 'properties': properties, **kw}
    if required:
        s['required'] = required
    return s


def _arr(items: dict, **kw) -> dict:
    return {'type': 'array', 'items': items, **kw}


# ===========================================================================
# Component Schemas
# ===========================================================================

# ---------------------------------------------------------------------------
# ErrorResponse  (all error bodies)
# ---------------------------------------------------------------------------

_schema('ErrorResponse', _obj({
    'error': _obj({
        'code':    _str(description='Machine-readable error code string'),
        'message': _str(description='Human-readable explanation'),
        'detail':  {'type': 'object', 'nullable': True,
                    'description': 'Optional structured detail (field errors, etc.)'},
    }, required=['code', 'message']),
}, required=['error']))

# ---------------------------------------------------------------------------
# PaginationMeta
# ---------------------------------------------------------------------------

_schema('PaginationMeta', _obj({
    'total_count':    _int(description='Total matching records in the database'),
    'returned_count': _int(description='Number of records included in this response'),
    'limit':          _int(description='Maximum records requested'),
    'offset':         _int(description='Offset from the start of the result set'),
}, required=['total_count', 'returned_count', 'limit', 'offset']))

# ---------------------------------------------------------------------------
# ReceiverPosition  (embedded in DroneListResponse)
# ---------------------------------------------------------------------------

_schema('ReceiverPosition', _obj({
    'lat':     _nullable(_num(description='Receiver latitude (decimal degrees)')),
    'lon':     _nullable(_num(description='Receiver longitude (decimal degrees)')),
    'alt':     _nullable(_num(description='Receiver altitude (metres MSL)')),
    'gps_fix': _bool(description='True when the receiver has a valid GPS fix'),
    'source':  _str(
        enum=['none', 'gpsd', 'static'],
        description='GPS source mode',
    ),
}))

# ---------------------------------------------------------------------------
# DroneSummary  — full drone detection object (DroneIDDevice.to_dict() shape)
# ---------------------------------------------------------------------------

_schema('DroneSummary', _obj({
    # Identity
    'serial_number':    _str(description='ANSI/CTA-2063-A serial or empty string'),
    'registration_id':  _str(description='CAA registration ID or empty string'),
    'id_type':          _int(description='ASTM F3411 ID type (0=None … 4=Specific Session)'),
    'id_type_name':     _str(description='Human-readable ID type name'),
    'ua_type':          _int(description='UA type integer (0=None … 15=Other)'),
    'ua_type_name':     _str(description='Human-readable UA type name'),
    # Drone position
    'drone_lat':        _num(description='Drone latitude (decimal degrees)'),
    'drone_lon':        _num(description='Drone longitude (decimal degrees)'),
    'drone_alt_geo':    _num(description='Drone geodetic altitude (metres MSL)'),
    'drone_alt_baro':   _num(description='Drone barometric altitude (metres)'),
    'drone_height_agl': _num(description='Drone height above ground level (metres)'),
    # Velocity
    'speed':            _num(description='Horizontal speed (m/s)'),
    'direction':        _num(description='Track direction (degrees true, 0=North)'),
    'vertical_speed':   _num(description='Vertical speed (m/s, positive=up)'),
    # Operator info
    'operator_lat':     _num(description='Operator latitude (decimal degrees)'),
    'operator_lon':     _num(description='Operator longitude (decimal degrees)'),
    'operator_alt':     _num(description='Operator altitude (metres)'),
    'operator_id':      _str(description='Operator identifier string'),
    'self_id_text':     _str(description='Free-text self-identification from drone'),
    # Takeoff point (populated by French RemoteID; distinct from operator pos)
    'takeoff_lat':      _num(description='Takeoff latitude (decimal degrees) — French RID'),
    'takeoff_lon':      _num(description='Takeoff longitude (decimal degrees) — French RID'),
    # Auth
    'auth_type':        _int(description='Authentication type code'),
    'auth_data':        _str(description='Authentication data (hex string)'),
    # RF metadata
    'mac_address':      _str(description='Source MAC address'),
    'rssi':             _int(description='Received signal strength (dBm)'),
    'channel':          _int(description='WiFi channel number'),
    'frequency':        _int(description='Frequency (MHz)'),
    # Protocol
    'protocol':         _str(
        enum=['astm_nan', 'astm_beacon', 'astm_ble', 'dji_proprietary', 'french', 'wifi_ssid'],
        description='Detection protocol',
    ),
    # Timestamps
    'first_seen':       _str(description='ISO 8601 UTC timestamp of first detection'),
    'last_seen':        _str(description='ISO 8601 UTC timestamp of most recent detection'),
    'time_in_area_seconds': _int(description='Seconds elapsed between first and last detection'),
    # Vendor enrichment (injected by alert_engine)
    'vendor':           _str(description='Manufacturer / vendor name or empty string'),
    # Derived fields (nested under "derived" key)
    'derived': _obj({
        'state':                  _str(
            enum=['active', 'aging', 'stale'],
            description='Detection freshness state',
        ),
        'altitude_class':         _str(
            enum=['GROUND', 'LOW', 'MEDIUM', 'HIGH', 'ILLEGAL'],
            description='Altitude classification relative to FAA 400ft limit',
        ),
        'range_m':                _nullable(_num(description='Distance from receiver to drone (metres)')),
        'bearing_deg':            _nullable(_num(description='Bearing from receiver to drone (degrees true)')),
        'bearing_cardinal':       _nullable(_str(description='16-point cardinal direction (e.g. NNE)')),
        'operator_range_m':       _nullable(_num(description='Distance from receiver to operator (metres)')),
        'operator_bearing_deg':   _nullable(_num(description='Bearing from receiver to operator (degrees true)')),
        'operator_bearing_cardinal': _nullable(_str(description='16-point cardinal direction to operator')),
    }),
}))

# ---------------------------------------------------------------------------
# AgeBandCounts  (counts block inside DroneListResponse)
# ---------------------------------------------------------------------------

_schema('AgeBandCounts', _obj({
    'active': _int(description='Drones seen within the last 30 seconds'),
    'aging':  _int(description='Drones seen 30-90 seconds ago'),
    'stale':  _int(description='Drones seen 90-180 seconds ago'),
}, required=['active', 'aging', 'stale']))

# ---------------------------------------------------------------------------
# DroneListResponse  — GET /api/drones
# ---------------------------------------------------------------------------

_schema('DroneListResponse', _obj({
    'errcode':   _int(),
    'errmsg':    _str(),
    'receiver':  _ref('ReceiverPosition'),
    'drones':    _arr(_ref('DroneSummary')),
    'counts':    _ref('AgeBandCounts'),
    'timestamp': _str(description='ISO 8601 UTC timestamp of the response'),
}, required=['errcode', 'errmsg', 'receiver', 'drones', 'counts', 'timestamp']))

# ---------------------------------------------------------------------------
# TrackPoint  — single position sample in a drone track
# ---------------------------------------------------------------------------

_schema('TrackPoint', _obj({
    'drone_lat':        _num(description='Latitude (decimal degrees)'),
    'drone_lon':        _num(description='Longitude (decimal degrees)'),
    'drone_alt_geo':    _num(description='Geodetic altitude (metres MSL)'),
    'drone_height_agl': _num(description='Height above ground level (metres)'),
    'speed':            _num(description='Horizontal speed (m/s)'),
    'direction':        _num(description='Track direction (degrees true)'),
    'rssi':             _int(description='Received signal strength (dBm)'),
    'timestamp':        _str(description='ISO 8601 UTC timestamp'),
}))

# ---------------------------------------------------------------------------
# DroneDetailResponse  — GET /api/drones/{serial}
# ---------------------------------------------------------------------------

_schema('DroneDetailResponse', _obj({
    'errcode': _int(),
    'errmsg':  _str(),
    'drone':   _ref('DroneSummary'),
    'track':   _arr(_ref('TrackPoint')),
}, required=['errcode', 'errmsg', 'drone', 'track']))

# ---------------------------------------------------------------------------
# HistoryRecord  — one row from GET /api/history
# (subset of detection columns actually selected by database.get_history)
# ---------------------------------------------------------------------------

_schema('HistoryRecord', _obj({
    'serial_number':   _str(),
    'ua_type':         _int(),
    'drone_lat':       _num(),
    'drone_lon':       _num(),
    'drone_height_agl': _num(),
    'speed':           _num(),
    'direction':       _num(),
    'operator_lat':    _num(),
    'operator_lon':    _num(),
    'rssi':            _int(),
    'protocol':        _str(),
    'receiver_lat':    _num(),
    'receiver_lon':    _num(),
    'timestamp':       _str(description='ISO 8601 UTC timestamp'),
}))

# ---------------------------------------------------------------------------
# HistorySerial  — one row from GET /api/history/serials
# ---------------------------------------------------------------------------

_schema('HistorySerial', _obj({
    'serial_number':   _str(),
    'ua_type':         _int(),
    'ua_type_name':    _str(),
    'protocol':        _str(),
    'first_seen':      _str(),
    'last_seen':       _str(),
    'detection_count': _int(),
    'max_rssi':        _int(),
    'self_id_text':    _str(),
}))

# ---------------------------------------------------------------------------
# TimelineBucket  — one entry from GET /api/history/timeline
# ---------------------------------------------------------------------------

_schema('TimelineBucket', _obj({
    'timestamp':     _str(description='ISO 8601 UTC bucket start time'),
    'active_drones': _int(description='Number of distinct drones active in this bucket'),
    'serials':       _arr(_str(), description='Sorted list of serial numbers active in this bucket'),
}, required=['timestamp', 'active_drones', 'serials']))

# ---------------------------------------------------------------------------
# AlertEvent  — fired alert log row (from alerts table SELECT *)
# ---------------------------------------------------------------------------

_schema('AlertEvent', _obj({
    'id':               _int(description='Auto-increment alert row ID'),
    'timestamp':        _str(description='ISO 8601 UTC time the alert fired'),
    'alert_type':       _str(
        enum=['new_drone', 'altitude_max', 'speed_max', 'signal_lost'],
        description='Alert type code',
    ),
    'serial_number':    _str(description='Drone serial number that triggered the alert'),
    'detail':           _str(description='Human-readable alert detail message'),
    'drone_lat':        _num(description='Drone latitude at alert time'),
    'drone_lon':        _num(description='Drone longitude at alert time'),
    'drone_height_agl': _num(description='Drone AGL height at alert time (metres)'),
    'state':            _str(
        enum=['ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'],
        description='Current alert state',
    ),
    'acknowledged_by':  _str(description='Operator who acknowledged the alert'),
    'acknowledged_at':  _str(description='ISO 8601 UTC time of acknowledgement'),
    'resolved_at':      _str(description='ISO 8601 UTC time of resolution'),
}))

# ---------------------------------------------------------------------------
# AlertRule  — a single alert rule definition (AlertRule.to_dict() shape)
# ---------------------------------------------------------------------------

_schema('AlertRule', _obj({
    'id':          _int(description='Rule ID'),
    'name':        _str(description='Human-readable rule name'),
    'type':        _str(
        enum=['new_drone', 'altitude_max', 'speed_max', 'signal_lost'],
        description='Alert type this rule applies to',
    ),
    'enabled':     _bool(description='Whether this rule is active'),
    'audio_sound': _str(description='Sound identifier to play on trigger'),
    'params':      _obj({}, description='Rule-specific parameters (e.g. max_altitude_m)'),
}, required=['id', 'name', 'type', 'enabled']))

# ---------------------------------------------------------------------------
# AlertConfig  — GET/PUT /api/alerts/config response
# ---------------------------------------------------------------------------

_schema('AlertConfig', _obj({
    'rules':              _arr(_ref('AlertRule')),
    'audio_enabled':      _bool(),
    'visual_enabled':     _bool(),
    'script_enabled':     _bool(),
    'script_path':        _str(),
    'slack_enabled':      _bool(),
    'slack_webhook_url':  _str(),
    'slack_display_name': _str(),
}, required=['rules', 'audio_enabled', 'visual_enabled']))

# ---------------------------------------------------------------------------
# GpsStatus  — GET /api/gps  (GPSEngine.to_dict() shape)
# ---------------------------------------------------------------------------

_schema('GpsStatus', _obj({
    'mode':      _str(enum=['none', 'gpsd', 'static'], description='GPS source mode'),
    'fix':       _bool(description='True when a valid position fix is available'),
    'latitude':  _num(description='Receiver latitude (decimal degrees)'),
    'longitude': _num(description='Receiver longitude (decimal degrees)'),
    'altitude':  _num(description='Receiver altitude (metres MSL)'),
    'speed':     _num(description='Receiver speed (m/s, from gpsd only)'),
    'gps_error': _str(description='Non-empty error string when GPS has a problem'),
}, required=['mode', 'fix', 'latitude', 'longitude', 'altitude']))

# ---------------------------------------------------------------------------
# CotStatus  — GET /api/cot/status  (CotEngine.get_status() shape)
# ---------------------------------------------------------------------------

_schema('CotStatus', _obj({
    'enabled':      _bool(description='True when CoT output is active'),
    'address':      _str(description='UDP multicast destination address'),
    'port':         _int(description='UDP destination port'),
    'events_sent':  _int(description='Number of CoT events sent since startup'),
}, required=['enabled', 'address', 'port', 'events_sent']))

# ---------------------------------------------------------------------------
# MonitorStatus  — GET /api/monitor/status  (DroneIDEngine.get_status() shape)
# ---------------------------------------------------------------------------

_schema('MonitorStatus', _obj({
    'monitoring':           _bool(description='True when capture is running'),
    'interface':            _str(description='WiFi interface in use'),
    'channel':              _int(description='WiFi channel being monitored'),
    'started_at':           _nullable(_str(description='ISO 8601 UTC start time or null')),
    'duration_seconds':     _int(description='Seconds since monitoring started'),
    'frame_count':          _int(description='Total 802.11 frames received'),
    'droneid_frame_count':  _int(description='Remote ID frames parsed'),
    'capture_errors':       _int(description='Capture error count'),
    'monitor_warning':      _str(description='Non-empty when a monitor mode warning is active'),
    'ble_enabled':          _bool(description='True when BLE capture is running alongside WiFi'),
    'ble_frame_count':      _int(description='BLE Remote ID frames received'),
}, required=['monitoring']))

# ---------------------------------------------------------------------------
# InterfaceInfo  — entry in GET /api/interfaces response
# ---------------------------------------------------------------------------

_schema('InterfaceInfo', _obj({
    'name':             _str(description='Interface name (e.g. wlan0, wlan0mon)'),
    'monitor_capable':  _bool(description='True when the interface supports monitor mode'),
}, required=['name', 'monitor_capable']))

# ---------------------------------------------------------------------------
# CertInfo  — certificate details (CertManager._build_cert_info() shape)
# ---------------------------------------------------------------------------

_schema('CertInfo', _obj({
    'name':               _str(description='Certificate name (filename without extension)'),
    'common_name':        _str(description='CN field from the certificate subject'),
    'subject':            _str(description='Full subject string from openssl'),
    'issuer':             _str(description='Full issuer string from openssl'),
    'serial':             _str(description='Certificate serial number'),
    'not_before':         _str(description='Certificate validity start (openssl date string)'),
    'not_after':          _str(description='Certificate validity end (openssl date string)'),
    'is_self_signed':     _bool(description='True when subject == issuer'),
    'has_key':            _bool(description='True when a private key file is present'),
    'has_csr':            _bool(description='True when a CSR file is present'),
    'fingerprint_sha256': _str(description='SHA-256 fingerprint (colon-separated hex)'),
}, required=['name', 'common_name']))

# ---------------------------------------------------------------------------
# VendorCodeStats  — response body for vendor-codes endpoints
# ---------------------------------------------------------------------------

_schema('VendorCodeStats', _obj({
    'serial_prefixes':      {
        'type': 'object',
        'additionalProperties': _str(),
        'description': 'Map of serial-number prefix → manufacturer name',
    },
    'mac_oui': {
        'type': 'object',
        'additionalProperties': _str(),
        'description': 'Map of MAC OUI (uppercase colon-separated) → manufacturer name',
    },
    'serial_prefix_count': _int(description='Number of serial prefix entries'),
    'mac_oui_count':       _int(description='Number of MAC OUI entries'),
}, required=['serial_prefix_count', 'mac_oui_count']))

# ---------------------------------------------------------------------------
# WifiSsidPattern  — a single SSID matching pattern
# ---------------------------------------------------------------------------

_schema('WifiSsidPattern', _obj({
    'pattern': _str(description='Python-compatible regex for SSID matching'),
    'label':   _str(description='Human-readable label for this pattern'),
}, required=['pattern']))

# ---------------------------------------------------------------------------
# WifiSsidStatus  — GET /api/wifi-ssid/status  (WifiSsidScanner.get_status())
# ---------------------------------------------------------------------------

_schema('WifiSsidStatus', _obj({
    'enabled':            _bool(),
    'agent_url':          _str(description='HTTP URL of the Sparrow WiFi agent being polled'),
    'agent_interface':    _str(description='Interface requested from agent, or "(auto)"'),
    'active_interface':   _str(description='Interface the agent is currently using'),
    'poll_interval':      _int(description='Polling interval (seconds)'),
    'pattern_count':      _int(description='Number of active SSID patterns'),
    'last_poll_time':     _nullable(_str(description='ISO 8601 UTC time of last successful poll')),
    'last_poll_ok':       _nullable(_bool(description='True if the last poll succeeded')),
    'last_data_age_s':    _nullable(_num(description='Seconds since last data was received')),
    'stale':              _bool(description='True when data age exceeds 4× poll interval'),
    'session_match_count': _int(description='Total SSID-pattern matches this session'),
    'running':            _bool(description='True when the poll thread is alive'),
}))

# ---------------------------------------------------------------------------
# Settings  — GET/PUT /api/settings response (coerced settings dict)
# ---------------------------------------------------------------------------

_schema('Settings', _obj({
    'port':                      _int(description='HTTP listen port'),
    'bind_address':              _str(description='Bind address (e.g. 0.0.0.0)'),
    'https_enabled':             _bool(),
    'https_cert_name':           _str(description='Name of the TLS certificate to use'),
    'auth_token':                _str(description='"(set)" when a token is configured, else empty'),
    'allowed_ips':               _str(description='Comma-separated IP / CIDR allowlist'),
    'gps_mode':                  _str(enum=['none', 'gpsd', 'static']),
    'gps_static_lat':            _num(),
    'gps_static_lon':            _num(),
    'gps_static_alt':            _num(),
    'retention_days':            _int(description='Database record retention period (days)'),
    'cot_enabled':               _bool(),
    'cot_address':               _str(description='CoT multicast address'),
    'cot_port':                  _int(description='CoT UDP port'),
    'alert_audio_enabled':       _bool(),
    'alert_visual_enabled':      _bool(),
    'alert_script_enabled':      _bool(),
    'alert_script_path':         _str(),
    'alert_slack_enabled':       _bool(),
    'alert_slack_webhook_url':   _str(),
    'alert_slack_display_name':  _str(),
    'tile_cache_enabled':        _bool(),
    'monitor_interface':         _str(description='Preferred WiFi interface for monitoring'),
    'operator_name':             _str(),
    'airport_geozone_radius_mi': _str(description='Radius (miles) for airport query'),
    'display_units':             _str(enum=['metric', 'imperial']),
    'vendor_codes_url':          _str(description='URL for remote vendor codes update'),
    'wifi_ssid_enabled':         _bool(),
    'wifi_ssid_agent_url':       _str(),
    'wifi_ssid_agent_interface': _str(),
    'wifi_ssid_poll_interval':   _str(description='Poll interval in seconds (stored as string)'),
}))

# ---------------------------------------------------------------------------
# DataStats  — GET /api/data/stats  (Database.get_stats() + retention_days)
# ---------------------------------------------------------------------------

_schema('DataStats', _obj({
    'db_size_bytes':         _int(description='SQLite database file size (bytes)'),
    'detection_count':       _int(description='Total detection records'),
    'alert_count':           _int(description='Total alert records'),
    'unique_serials':        _int(description='Distinct drone serial numbers seen'),
    'oldest_record':         _nullable(_str(description='ISO 8601 timestamp of oldest detection')),
    'newest_record':         _nullable(_str(description='ISO 8601 timestamp of newest detection')),
    'tile_cache_size_bytes': _int(description='Total bytes used by tile cache on disk'),
    'retention_days':        _int(description='Configured data-retention period (days)'),
}))

# ---------------------------------------------------------------------------
# StatusResponse  — GET /api/status
# ---------------------------------------------------------------------------

_schema('StatusResponse', _obj({
    'errcode':                _int(),
    'errmsg':                 _str(),
    'version':                _str(description='API version string'),
    'monitoring':             _bool(description='True when packet capture is active'),
    'monitor_interface':      _str(description='Active capture interface'),
    'monitor_channel':        _int(description='Active capture channel'),
    'monitor_duration_seconds': _int(description='Seconds since monitoring started'),
    'frame_count':            _int(description='Total frames captured this session'),
    'active_drone_count':     _int(description='Drones with detections in the last 180s'),
    'total_drones_seen':      _int(description='Unique serials in the database'),
    'gps_fix':                _bool(),
    'receiver_lat':           _num(),
    'receiver_lon':           _num(),
    'receiver_alt':           _num(),
    'uptime_seconds':         _int(description='Server uptime in seconds'),
    'db_size_bytes':          _int(description='SQLite database size (bytes)'),
    'retention_days':         _int(description='Data retention period (days)'),
}, required=['errcode', 'errmsg', 'version']))


# ===========================================================================
# Public helper functions for inline route-decorator specs
# ===========================================================================

def qparam(name: str, schema_type: str, description: str,
           required: bool = False, default: Any = None,
           enum: Optional[List] = None) -> dict:
    """Build an OpenAPI query parameter object.

    Args:
        name:        Parameter name as it appears in the query string.
        schema_type: JSON Schema primitive type ('string', 'integer', 'number', 'boolean').
        description: Human-readable description.
        required:    Whether the parameter must be present (default False).
        default:     Default value (added to schema when not None).
        enum:        Allowed values list (added to schema when not None).

    Returns:
        OpenAPI Parameter Object dict.
    """
    schema: dict = {'type': schema_type}
    if default is not None:
        schema['default'] = default
    if enum is not None:
        schema['enum'] = enum
    p = {
        'name': name,
        'in': 'query',
        'description': description,
        'required': required,
        'schema': schema,
    }
    return p


def path_param(name: str, schema_type: str, description: str) -> dict:
    """Build an OpenAPI path parameter object.

    Args:
        name:        Parameter name matching the ``{name}`` placeholder in the path pattern.
        schema_type: JSON Schema primitive type.
        description: Human-readable description.

    Returns:
        OpenAPI Parameter Object dict.
    """
    return {
        'name': name,
        'in': 'path',
        'required': True,
        'description': description,
        'schema': {'type': schema_type},
    }


def json_body(schema_ref: str, description: str = '', required: bool = True) -> dict:
    """Build a requestBody that references a component schema by name.

    Args:
        schema_ref:  Schema name (key in SCHEMAS / #/components/schemas/{name}).
        description: Optional description of the request body.
        required:    Whether the body must be present (default True).

    Returns:
        OpenAPI Request Body Object dict.
    """
    return {
        'description': description,
        'required': required,
        'content': {
            'application/json': {
                'schema': {'$ref': f'#/components/schemas/{schema_ref}'},
            },
        },
    }


def json_body_inline(properties: dict, required_props: Optional[List[str]] = None,
                     description: str = '') -> dict:
    """Build a requestBody with an inline schema rather than a $ref.

    Args:
        properties:     Dict of property_name → OpenAPI Schema Object.
        required_props: List of required property names (optional).
        description:    Optional description of the request body.

    Returns:
        OpenAPI Request Body Object dict.
    """
    schema: dict = {'type': 'object', 'properties': properties}
    if required_props:
        schema['required'] = required_props
    return {
        'description': description,
        'required': True,
        'content': {
            'application/json': {'schema': schema},
        },
    }


def response_ref(schema_name: str, description: str = 'Success') -> dict:
    """Build a 200 response object that references a component schema.

    Args:
        schema_name: Schema name (key in SCHEMAS).
        description: Response description string.

    Returns:
        Dict suitable for use as ``{'200': response_ref(...)}`` in an operation.
    """
    return {
        'description': description,
        'content': {
            'application/json': {
                'schema': {'$ref': f'#/components/schemas/{schema_name}'},
            },
        },
    }


def response_inline(properties: dict, description: str = 'Success') -> dict:
    """Build a 200 response object with an inline schema.

    Args:
        properties: Dict of property_name → OpenAPI Schema Object.
        description: Response description string.

    Returns:
        Dict suitable for use as ``{'200': response_inline(...)}`` in an operation.
    """
    return {
        'description': description,
        'content': {
            'application/json': {
                'schema': {
                    'type': 'object',
                    'properties': properties,
                },
            },
        },
    }


# ===========================================================================
# OpenAPI document assembly
# ===========================================================================

def build_openapi_spec(router) -> dict:
    """Assemble a complete OpenAPI 3.0.3 document from the registered router.

    Walks ``router.routes`` (pattern → method → handler) and converts each
    entry to an OpenAPI path item.  Each operation receives bearer-auth
    security.

    Args:
        router: The ``APIRouter`` instance from api_handler.py.

    Returns:
        A dict that can be serialised to JSON / YAML as the OpenAPI spec.
    """
    paths: dict = {}

    for pattern, method_handlers in router.routes.items():
        # Convert {param} → OpenAPI path parameter syntax (already compatible).
        # Collect path parameter names for the parameters list.
        param_names = re.findall(r'\{(\w+)\}', pattern)

        path_item: dict = {}

        for method, handler in method_handlers.items():
            if method.upper() == 'OPTIONS':
                continue  # Not worth documenting CORS preflight

            # Retrieve per-handler spec metadata if the handler was decorated
            # with spec information; otherwise produce a minimal stub.
            spec: dict = getattr(handler, '_openapi_spec', {})

            operation: dict = {
                'operationId': handler.__name__,
                'summary':     spec.get('summary', handler.__name__.replace('api_', '').replace('_', ' ').title()),
                'tags':        spec.get('tags', []),
                'security':    [{'bearerAuth': []}],
            }

            if 'description' in spec:
                operation['description'] = spec['description']

            # Merge path parameters from the URL pattern with any declared
            # in spec['parameters'].
            declared_params: List[dict] = list(spec.get('parameters', []))
            declared_names = {p['name'] for p in declared_params if p.get('in') == 'path'}
            auto_path_params = [
                path_param(n, 'string', f'Path parameter: {n}')
                for n in param_names
                if n not in declared_names
            ]
            all_params = auto_path_params + declared_params
            if all_params:
                operation['parameters'] = all_params

            if 'requestBody' in spec:
                operation['requestBody'] = spec['requestBody']

            # Build responses block
            responses: dict = {}
            if 'responses' in spec:
                responses.update(spec['responses'])
            if '200' not in responses:
                responses['200'] = {'description': 'Success'}
            # Standard error responses
            responses.setdefault('400', {
                'description': 'Bad request',
                'content': {'application/json': {'schema': _ref('ErrorResponse')}},
            })
            responses.setdefault('401', {
                'description': 'Authentication required',
                'content': {'application/json': {'schema': _ref('ErrorResponse')}},
            })
            responses.setdefault('500', {
                'description': 'Internal server error',
                'content': {'application/json': {'schema': _ref('ErrorResponse')}},
            })
            operation['responses'] = responses

            path_item[method.lower()] = operation

        if path_item:
            paths[pattern] = path_item

    return {
        'openapi': '3.0.3',
        'info': {
            'title':   'Sparrow DroneID API',
            'version': API_VERSION,
            'description': (
                'REST API for the Sparrow DroneID server.\n\n'
                'All `/api/*` endpoints require Bearer token authentication '
                'when an `auth_token` is configured in settings.\n\n'
                'Timestamps are ISO 8601 UTC strings (e.g. `2024-01-15T12:00:00Z`).'
            ),
        },
        'servers': [
            {'url': '/', 'description': 'Sparrow DroneID server (current host)'},
        ],
        'tags': TAGS,
        'paths': paths,
        'components': {
            'schemas': SCHEMAS,
            'securitySchemes': {
                'bearerAuth': {
                    'type':         'http',
                    'scheme':       'bearer',
                    'bearerFormat': 'Token',
                    'description':  'API token configured in /api/settings (auth_token)',
                },
            },
        },
    }


# ===========================================================================
# Request validation
# ===========================================================================

def validate_request(spec: dict, query_params: Dict[str, str],
                     json_data: Optional[dict]) -> Optional[str]:
    """Lightweight validation of an incoming request against a route's spec dict.

    Checks:
      1. Required query parameters are present (non-empty string).
      2. A required request body is present.
      3. Required fields within the request body are present.

    This is intentionally minimal — full JSON Schema validation is not
    performed.  It complements, rather than replaces, the explicit field
    checks already in each handler.

    Args:
        spec:         The per-handler ``_openapi_spec`` dict (same structure
                      used by build_openapi_spec).
        query_params: Parsed query string as ``{name: value}`` strings.
        json_data:    Parsed JSON body dict, or None.

    Returns:
        An error message string if validation fails, or ``None`` if OK.
    """
    # --- Query parameters ---
    for param in spec.get('parameters', []):
        if param.get('in') != 'query':
            continue
        if not param.get('required', False):
            continue
        name = param['name']
        if not query_params.get(name, '').strip():
            return f"Required query parameter '{name}' is missing or empty"

    # --- Request body ---
    request_body = spec.get('requestBody', {})
    if request_body.get('required', False):
        if json_data is None:
            return 'Request body is required'

        # If the body schema is inline (not a $ref), check required properties.
        content = request_body.get('content', {})
        json_content = content.get('application/json', {})
        schema = json_content.get('schema', {})

        # Only validate inline schemas; $ref schemas are too complex to resolve here.
        if '$ref' not in schema and 'properties' in schema:
            for field in schema.get('required', []):
                if field not in json_data:
                    return f"Required body field '{field}' is missing"

    return None
