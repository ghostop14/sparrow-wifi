"""
HTTP API Handler for Sparrow DroneID.

Provides a multithreaded HTTP server with:
- APIRouter decorator-based route dispatch
- IP/subnet allowlist and Bearer token authentication (API paths only)
- Static file serving for the frontend SPA
- Tile proxy with optional disk cache
"""
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict, Any, Tuple, Callable
from urllib.parse import urlparse, parse_qs, unquote

import requests as _requests

from .droneid_engine import DroneIDEngine, CaptureManager, check_prerequisites
from .gps_engine import GPSEngine
from .alert_engine import AlertEngine
from .cot_engine import CotEngine
from .database import Database
from .export import generate_kml
from .cert_manager import CertManager


# ---------------------------------------------------------------------------
# Module-level engine references — populated by app.py via set_engines()
# ---------------------------------------------------------------------------

_droneid_engine: Optional[DroneIDEngine] = None
_gps_engine: Optional[GPSEngine] = None
_alert_engine: Optional[AlertEngine] = None
_cot_engine: Optional[CotEngine] = None
_db: Optional[Database] = None
_data_dir: Optional[str] = None
_html_dir: Optional[str] = None
_cert_manager: Optional[CertManager] = None

# Startup timestamp for uptime calculation
_start_time: datetime = datetime.now(timezone.utc)

# Persistent HTTP session for tile proxy upstream fetches
_tile_session: _requests.Session = _requests.Session()
_tile_session.headers.update({'User-Agent': 'SparrowDroneID/1.0'})


def set_engines(droneid: DroneIDEngine, gps: GPSEngine, alert: AlertEngine,
                cot: CotEngine, db: Database, data_dir: str, html_dir: str,
                cert_manager: CertManager = None) -> None:
    """Called by app.py at startup to wire engine references into this module."""
    global _droneid_engine, _gps_engine, _alert_engine, _cot_engine
    global _db, _data_dir, _html_dir, _cert_manager
    _droneid_engine = droneid
    _gps_engine = gps
    _alert_engine = alert
    _cot_engine = cot
    _db = db
    _data_dir = data_dir
    _html_dir = html_dir
    _cert_manager = cert_manager


# ---------------------------------------------------------------------------
# Tile upstream URL table
# ---------------------------------------------------------------------------

_TILE_UPSTREAM = {
    'osm':           'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'esri_satellite': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
}

_TILE_CONTENT_TYPE = {
    'osm':           'image/png',
    'esri_satellite': 'image/jpeg',
}


# ---------------------------------------------------------------------------
# Static file serving type map (mirrors http_agent_template pattern)
# ---------------------------------------------------------------------------

_DIRECT_SERVE_TYPES = {
    'html':  {'content-type': 'text/html',              'read_type': 'r'},
    'htm':   {'content-type': 'text/html',              'read_type': 'r'},
    'js':    {'content-type': 'text/javascript',        'read_type': 'r'},
    'mjs':   {'content-type': 'text/javascript',        'read_type': 'r'},
    'map':   {'content-type': 'text/javascript',        'read_type': 'r'},
    'css':   {'content-type': 'text/css',               'read_type': 'r'},
    'json':  {'content-type': 'application/json',       'read_type': 'r'},
    'svg':   {'content-type': 'image/svg+xml',          'read_type': 'rb'},
    'png':   {'content-type': 'image/png',              'read_type': 'rb'},
    'jpg':   {'content-type': 'image/jpeg',             'read_type': 'rb'},
    'jpeg':  {'content-type': 'image/jpeg',             'read_type': 'rb'},
    'ico':   {'content-type': 'image/x-icon',           'read_type': 'rb'},
    'woff':  {'content-type': 'font/woff',              'read_type': 'rb'},
    'woff2': {'content-type': 'font/woff2',             'read_type': 'rb'},
    'ttf':   {'content-type': 'font/ttf',               'read_type': 'rb'},
    'pdf':   {'content-type': 'application/pdf',        'read_type': 'rb'},
}


# ---------------------------------------------------------------------------
# API Router
# ---------------------------------------------------------------------------

class APIRouter:
    """Simple decorator-based route dispatcher with path parameter extraction."""

    def __init__(self):
        self.routes: Dict[str, Dict[str, Callable]] = {}

    def add_route(self, method: str, pattern: str, handler: Callable) -> None:
        if pattern not in self.routes:
            self.routes[pattern] = {}
        self.routes[pattern][method.upper()] = handler

    def match(self, method: str, path: str) -> Tuple[Optional[Callable], Dict[str, str]]:
        """Match a path against registered routes. Returns (handler, path_params)."""
        for pattern, handlers in self.routes.items():
            regex_pattern = re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', pattern)
            regex_pattern = f'^{regex_pattern}$'
            m = re.match(regex_pattern, path)
            if m:
                if method.upper() in handlers:
                    return handlers[method.upper()], m.groupdict()
                if method.upper() == 'OPTIONS':
                    return None, {'_allowed_methods': list(handlers.keys())}
        return None, {}

    def route(self, method: str, pattern: str):
        """Decorator: @router.route('GET', '/api/foo')"""
        def decorator(func: Callable):
            self.add_route(method, pattern, func)
            return func
        return decorator


# Global router — all @router.route decorators below register into this instance.
router = APIRouter()


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

class MultithreadHTTPServer(ThreadingMixIn, HTTPServer):
    """Thread-per-request HTTP server."""
    daemon_threads = True


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------

class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Sparrow DroneID.

    Handles:
    - /api/* — JSON API endpoints with optional auth
    - everything else — static file serving from html_dir (no auth)
    """

    server_version = 'SparrowDroneID/1.0'

    def __init__(self, *args, **kwargs):
        self.body_data: Optional[bytes] = None
        self.json_data: Optional[Dict] = None
        self.query_params: Dict[str, str] = {}
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_message(self, format, *args):
        """Suppress default per-request logging; app.py configures its own."""
        pass

    # ------------------------------------------------------------------
    # Client IP helper
    # ------------------------------------------------------------------

    def get_client_ip(self) -> str:
        forwarded = self.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return self.client_address[0]

    # ------------------------------------------------------------------
    # Request parsing helpers
    # ------------------------------------------------------------------

    def parse_body(self) -> None:
        """Read and JSON-decode the request body."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            content_length = 0

        if content_length > 0:
            self.body_data = self.rfile.read(content_length)
            content_type = self.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    self.json_data = json.loads(self.body_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.json_data = None

    def parse_query_string(self) -> None:
        parsed = urlparse(self.path)
        self.query_params = {}
        for key, values in parse_qs(parsed.query).items():
            self.query_params[key] = values[0] if values else ''

    def _qparam(self, key: str, default: str = '') -> str:
        return self.query_params.get(key, default)

    def _qparam_int(self, key: str, default: int) -> int:
        try:
            return int(self.query_params[key])
        except (KeyError, ValueError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def check_auth(self) -> bool:
        """Verify IP allowlist and/or Bearer token.

        Returns True if the request is authorized.
        Sends a 401/403 response and returns False if not.
        Both checks must pass when both are configured.
        """
        if _db is None:
            return True

        # --- IP allowlist ---
        allowed_ips_raw = _db.get_setting('allowed_ips', '') or ''
        if allowed_ips_raw.strip():
            client_ip = self.get_client_ip()
            if not _ip_is_allowed(client_ip, allowed_ips_raw):
                self._send_error_json(403, 3, 'Connections not authorized from your IP address')
                return False

        # --- Bearer token ---
        required_token = _db.get_setting('auth_token', '') or ''
        if required_token:
            auth_header = self.headers.get('Authorization', '')
            token = None
            if auth_header.startswith('Bearer '):
                token = auth_header[len('Bearer '):]
            if not token:
                # Fallback: check _token query parameter (for file downloads)
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                token_list = params.get('_token', [])
                if token_list:
                    token = token_list[0]
            if not token:
                self._send_error_json(401, 401, 'Authentication required')
                return False
            if token != required_token:
                self._send_error_json(401, 401, 'Authentication required')
                return False

        return True

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_cors_headers(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, http_status: int, errcode: int, errmsg: str) -> None:
        self._send_json({'errcode': errcode, 'errmsg': errmsg}, status=http_status)

    def _ok(self, extra: Dict = None) -> Dict:
        """Build a success response dict, merging in any extra fields."""
        d = {'errcode': 0, 'errmsg': ''}
        if extra:
            d.update(extra)
        return d

    def _send_raw(self, data: bytes, content_type: str, status: int = 200,
                  extra_headers: Dict[str, str] = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, url_path: str) -> None:
        """Serve frontend static files from html_dir."""
        html_dir = _html_dir
        if html_dir is None:
            self._send_error_json(503, 5, 'Frontend not available')
            return

        # Strip leading slashes; normalise to prevent directory traversal.
        clean = url_path.lstrip('/')
        if not clean:
            clean = 'index.html'

        # Security: reject any path with ..
        if '..' in clean.split('/'):
            self._send_error_json(403, 3, 'Forbidden')
            return

        filepath = os.path.join(html_dir, clean)

        # If a directory is requested, try index.html inside it.
        if os.path.isdir(filepath):
            filepath = os.path.join(filepath, 'index.html')

        if os.path.isfile(filepath):
            _, ext = os.path.splitext(filepath)
            ext = ext.lstrip('.').lower()
            type_info = _DIRECT_SERVE_TYPES.get(ext)

            if type_info:
                try:
                    with open(filepath, type_info['read_type']) as fh:
                        contents = fh.read()
                    if isinstance(contents, str):
                        contents = contents.encode('utf-8')
                    self._send_raw(contents, type_info['content-type'])
                except OSError as e:
                    self._send_error_json(500, 5, f'Error reading file: {e}')
            else:
                # Unknown extension — let mimetypes guess.
                ct, _ = mimetypes.guess_type(filepath)
                ct = ct or 'application/octet-stream'
                try:
                    with open(filepath, 'rb') as fh:
                        contents = fh.read()
                    self._send_raw(contents, ct)
                except OSError as e:
                    self._send_error_json(500, 5, f'Error reading file: {e}')
        else:
            # SPA fallback: serve index.html for unknown paths so the JS
            # router can handle them.
            index_path = os.path.join(html_dir, 'index.html')
            if os.path.isfile(index_path):
                try:
                    with open(index_path, 'r') as fh:
                        contents = fh.read().encode('utf-8')
                    self._send_raw(contents, 'text/html')
                except OSError:
                    self._send_error_json(404, 2, 'Not found')
            else:
                self._send_error_json(404, 2, 'Not found')

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    def _handle_api(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        handler, params = router.match(method, path)

        if handler:
            try:
                handler(self, **params)
            except Exception as e:
                traceback.print_exc()
                self._send_error_json(500, 5, f'Internal server error: {e}')
        elif method == 'OPTIONS':
            self.send_response(204)
            self._send_cors_headers()
            allowed = params.get('_allowed_methods', [])
            if allowed:
                self.send_header('Access-Control-Allow-Methods', ', '.join(allowed))
            self.end_headers()
        else:
            self._send_error_json(404, 2, 'API endpoint not found')

    def do_GET(self):
        self.parse_query_string()
        parsed_path = urlparse(self.path).path
        if parsed_path.startswith('/api/'):
            if not self.check_auth():
                return
            self._handle_api('GET')
        else:
            self._serve_static(unquote(parsed_path))

    def do_POST(self):
        self.parse_query_string()
        self.parse_body()
        if not self.check_auth():
            return
        self._handle_api('POST')

    def do_PUT(self):
        self.parse_query_string()
        self.parse_body()
        if not self.check_auth():
            return
        self._handle_api('PUT')

    def do_DELETE(self):
        self.parse_query_string()
        if not self.check_auth():
            return
        self._handle_api('DELETE')

    def do_OPTIONS(self):
        self.parse_query_string()
        # OPTIONS (CORS preflight) never requires auth.
        self._handle_api('OPTIONS')


# ---------------------------------------------------------------------------
# Auth helper — IP allowlist check
# ---------------------------------------------------------------------------

def _ip_is_allowed(client_ip: str, allowed_ips_raw: str) -> bool:
    """Return True if client_ip matches any entry in the comma-separated
    allowed_ips string (supports individual IPs and CIDR notation)."""
    try:
        client_addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for entry in allowed_ips_raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if '/' in entry:
                if client_addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if client_addr == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _coerce_settings(raw: Dict[str, str]) -> Dict[str, Any]:
    """Convert flat string settings dict to typed values for API response."""
    bool_keys = {
        'https_enabled', 'cot_enabled',
        'alert_audio_enabled', 'alert_visual_enabled', 'alert_script_enabled',
        'tile_cache_enabled',
    }
    int_keys = {'port', 'cot_port', 'retention_days'}
    float_keys = {'gps_static_lat', 'gps_static_lon', 'gps_static_alt'}

    result: Dict[str, Any] = {}
    for k, v in raw.items():
        if k == 'auth_token':
            # Never reveal the actual token value.
            result[k] = '(set)' if v else ''
        elif k in bool_keys:
            result[k] = v.lower() in ('1', 'true', 'yes')
        elif k in int_keys:
            try:
                result[k] = int(v)
            except (ValueError, TypeError):
                result[k] = v
        elif k in float_keys:
            try:
                result[k] = float(v)
            except (ValueError, TypeError):
                result[k] = v
        else:
            result[k] = v
    return result


# ===========================================================================
#  API Route Handlers
# ===========================================================================

# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@router.route('GET', '/api/status')
def api_status(req: RequestHandler):
    engine_status = _droneid_engine.get_status() if _droneid_engine else {}
    gps_dict = _gps_engine.to_dict() if _gps_engine else {}
    db_stats = _db.get_stats() if _db else {}
    uptime = int((datetime.now(timezone.utc) - _start_time).total_seconds())
    active_count = len(_droneid_engine.get_active_drones()) if _droneid_engine else 0
    unique_total = _db.get_unique_serial_count() if _db else 0
    retention = int(_db.get_setting('retention_days', '14') or '14') if _db else 14

    req._send_json(req._ok({
        'version': '1.0.0',
        'monitoring': engine_status.get('monitoring', False),
        'monitor_interface': engine_status.get('interface', ''),
        'monitor_channel': engine_status.get('channel', 6),
        'monitor_duration_seconds': engine_status.get('duration_seconds', 0),
        'frame_count': engine_status.get('frame_count', 0),
        'active_drone_count': active_count,
        'total_drones_seen': unique_total,
        'gps_fix': gps_dict.get('fix', False),
        'receiver_lat': gps_dict.get('latitude', 0.0),
        'receiver_lon': gps_dict.get('longitude', 0.0),
        'receiver_alt': gps_dict.get('altitude', 0.0),
        'uptime_seconds': uptime,
        'db_size_bytes': db_stats.get('db_size_bytes', 0),
        'retention_days': retention,
    }))


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

@router.route('GET', '/api/interfaces')
def api_interfaces(req: RequestHandler):
    interfaces = CaptureManager.get_interfaces()
    req._send_json(req._ok({'interfaces': interfaces}))


@router.route('POST', '/api/monitor/start')
def api_monitor_start(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error_json(503, 5, 'Engine not available')
        return

    body = req.json_data or {}
    interface = body.get('interface', '').strip()
    channel = int(body.get('channel', 6))

    if not interface:
        req._send_error_json(400, 1, 'interface is required')
        return

    if _droneid_engine.monitoring:
        req._send_error_json(409, 3, 'Already monitoring')
        return

    # Validate that the interface exists
    ifaces = CaptureManager.get_interfaces()
    iface_names = [i['name'] for i in ifaces]
    if interface not in iface_names:
        req._send_error_json(404, 2, f'Interface {interface!r} not found')
        return

    # Validate monitor capability
    iface_info = next((i for i in ifaces if i['name'] == interface), {})
    if not iface_info.get('monitor_capable', False):
        req._send_error_json(400, 3, f'Interface {interface!r} does not support monitor mode')
        return

    try:
        _droneid_engine.start(interface, channel)
    except RuntimeError as e:
        req._send_error_json(409, 3, str(e))
        return
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to start capture: {e}')
        return

    status = _droneid_engine.get_status()
    req._send_json(req._ok({
        'interface': status['interface'],
        'channel': status['channel'],
        'status': 'monitoring',
    }))


@router.route('POST', '/api/monitor/stop')
def api_monitor_stop(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error_json(503, 5, 'Engine not available')
        return

    if not _droneid_engine.monitoring:
        req._send_error_json(409, 3, 'Not currently monitoring')
        return

    status_before = _droneid_engine.get_status()
    duration = status_before.get('duration_seconds', 0)
    frames = status_before.get('frame_count', 0)
    drones = len(_droneid_engine.get_active_drones(max_age=0))

    try:
        _droneid_engine.stop()
    except Exception as e:
        req._send_error_json(500, 5, f'Error stopping capture: {e}')
        return

    req._send_json(req._ok({
        'status': 'stopped',
        'session_duration_seconds': duration,
        'frames_captured': frames,
        'drones_detected': drones,
    }))


@router.route('GET', '/api/monitor/status')
def api_monitor_status(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error_json(503, 5, 'Engine not available')
        return
    status = _droneid_engine.get_status()
    req._send_json(req._ok(status))


# ---------------------------------------------------------------------------
# Detections (Live)
# ---------------------------------------------------------------------------

@router.route('GET', '/api/drones')
def api_drones(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error_json(503, 5, 'Engine not available')
        return

    max_age = req._qparam_int('max_age', 180)
    drones = _droneid_engine.get_active_drones(max_age=max_age)

    # Build receiver block from GPS engine
    gps = _gps_engine.to_dict() if _gps_engine else {}
    receiver = {
        'lat': gps.get('latitude', 0.0),
        'lon': gps.get('longitude', 0.0),
        'alt': gps.get('altitude', 0.0),
        'gps_fix': gps.get('fix', False),
        'source': gps.get('mode', 'none'),
    }

    # Compute counts by age band
    now = datetime.utcnow()
    active_count = aging_count = stale_count = 0
    for d in drones:
        try:
            last = datetime.fromisoformat(d.get('last_seen', '').replace('Z', ''))
            age = (now - last).total_seconds()
        except (ValueError, AttributeError):
            age = 9999
        if age <= 30:
            active_count += 1
        elif age <= 90:
            aging_count += 1
        else:
            stale_count += 1

    req._send_json(req._ok({
        'receiver': receiver,
        'drones': drones,
        'counts': {
            'active': active_count,
            'aging': aging_count,
            'stale': stale_count,
        },
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }))


@router.route('GET', '/api/drones/{serial}')
def api_drone_detail(req: RequestHandler, serial: str):
    if _droneid_engine is None:
        req._send_error_json(503, 5, 'Engine not available')
        return

    serial = unquote(serial)
    track_minutes = req._qparam_int('track_minutes', 5)

    drone_dict, track = _droneid_engine.get_drone_detail(serial, track_minutes)

    if drone_dict is None:
        req._send_error_json(404, 2, f'Drone {serial!r} not found')
        return

    req._send_json(req._ok({'drone': drone_dict, 'track': track}))


# ---------------------------------------------------------------------------
# History / Replay
# ---------------------------------------------------------------------------

@router.route('GET', '/api/history')
def api_history(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error_json(400, 1, 'from and to query parameters are required')
        return

    serial = req._qparam('serial') or None
    limit = req._qparam_int('limit', 10000)
    offset = req._qparam_int('offset', 0)

    try:
        records, total = _db.get_history(from_ts, to_ts, serial, limit, offset)
    except Exception as e:
        req._send_error_json(500, 5, f'Database error: {e}')
        return

    req._send_json(req._ok({
        'records': records,
        'total_count': total,
        'returned_count': len(records),
    }))


@router.route('GET', '/api/history/serials')
def api_history_serials(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error_json(400, 1, 'from and to query parameters are required')
        return

    try:
        serials = _db.get_history_serials(from_ts, to_ts)
    except Exception as e:
        req._send_error_json(500, 5, f'Database error: {e}')
        return

    req._send_json(req._ok({'serials': serials}))


@router.route('GET', '/api/history/timeline')
def api_history_timeline(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error_json(400, 1, 'from and to query parameters are required')
        return

    bucket_seconds = req._qparam_int('bucket_seconds', 10)

    try:
        buckets = _db.get_history_timeline(from_ts, to_ts, bucket_seconds)
    except Exception as e:
        req._send_error_json(500, 5, f'Database error: {e}')
        return

    req._send_json(req._ok({'buckets': buckets}))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.route('GET', '/api/export/kml')
def api_export_kml(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error_json(400, 1, 'from and to query parameters are required')
        return

    serial = req._qparam('serial') or None

    gps = _gps_engine.to_dict() if _gps_engine else {}
    rx_lat = gps.get('latitude', None) if gps.get('fix') else None
    rx_lon = gps.get('longitude', None) if gps.get('fix') else None
    rx_alt = gps.get('altitude', 0.0) if gps.get('fix') else None

    try:
        kml_str = generate_kml(
            _db, from_ts, to_ts,
            serial=serial,
            receiver_lat=rx_lat,
            receiver_lon=rx_lon,
            receiver_alt=rx_alt,
        )
    except Exception as e:
        req._send_error_json(500, 5, f'KML generation failed: {e}')
        return

    kml_bytes = kml_str.encode('utf-8')
    safe_from = from_ts.replace(':', '-').replace(' ', '_')
    safe_to = to_ts.replace(':', '-').replace(' ', '_')
    filename = f'sparrow_droneid_export_{safe_from}_{safe_to}.kml'

    req._send_raw(
        kml_bytes,
        'application/vnd.google-earth.kml+xml',
        extra_headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.route('GET', '/api/alerts/config')
def api_alerts_config_get(req: RequestHandler):
    if _alert_engine is None:
        req._send_error_json(503, 5, 'Alert engine not available')
        return
    config = _alert_engine.get_config()
    req._send_json(req._ok(config))


@router.route('PUT', '/api/alerts/config')
def api_alerts_config_put(req: RequestHandler):
    if _alert_engine is None:
        req._send_error_json(503, 5, 'Alert engine not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return
    try:
        _alert_engine.set_config(req.json_data)
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to update alert config: {e}')
        return
    req._send_json(req._ok())


@router.route('POST', '/api/alerts/slack-test')
def api_alerts_slack_test(req: RequestHandler):
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return
    webhook_url = req.json_data.get('webhook_url', '')
    display_name = req.json_data.get('display_name', 'Sparrow DroneID')
    result = AlertEngine.test_slack(webhook_url, display_name)
    resp = req._ok()
    resp.update(result)
    req._send_json(resp)


@router.route('GET', '/api/alerts/log')
def api_alerts_log(req: RequestHandler):
    from_ts = req._qparam('from') or None
    to_ts = req._qparam('to') or None
    limit = req._qparam_int('limit', 100)
    offset = req._qparam_int('offset', 0)

    try:
        alerts, total = _db.get_alerts(from_ts, to_ts, limit, offset)
    except Exception as e:
        req._send_error_json(500, 5, f'Database error: {e}')
        return

    req._send_json(req._ok({'alerts': alerts, 'total_count': total}))


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------

@router.route('GET', '/api/gps')
def api_gps(req: RequestHandler):
    if _gps_engine is None:
        req._send_error_json(503, 5, 'GPS engine not available')
        return
    gps_dict = _gps_engine.to_dict()
    req._send_json(req._ok(gps_dict))


# ---------------------------------------------------------------------------
# Cursor on Target (CoT)
# ---------------------------------------------------------------------------

@router.route('GET', '/api/cot/status')
def api_cot_status(req: RequestHandler):
    if _cot_engine is None:
        req._send_error_json(503, 5, 'CoT engine not available')
        return
    req._send_json(req._ok(_cot_engine.get_status()))


@router.route('PUT', '/api/cot/config')
def api_cot_config(req: RequestHandler):
    if _cot_engine is None:
        req._send_error_json(503, 5, 'CoT engine not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return

    body = req.json_data
    enabled = bool(body.get('enabled', _cot_engine.enabled))
    address = str(body.get('address', _cot_engine.address))
    try:
        port = int(body.get('port', _cot_engine.port))
    except (ValueError, TypeError):
        req._send_error_json(400, 1, 'port must be an integer')
        return

    try:
        _cot_engine.configure(enabled, address, port)
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to configure CoT: {e}')
        return

    # Persist to DB so settings survive restart
    if _db:
        _db.set_setting('cot_enabled', str(enabled).lower())
        _db.set_setting('cot_address', address)
        _db.set_setting('cot_port', str(port))

    req._send_json(req._ok())


# ---------------------------------------------------------------------------
# Map Tiles
# ---------------------------------------------------------------------------

@router.route('GET', '/api/tiles/{source}/{z}/{x}/{y}')
def api_tiles(req: RequestHandler, source: str, z: str, x: str, y: str):
    y = y.rsplit('.', 1)[0]  # strip .png/.jpg suffix if present
    if source not in _TILE_UPSTREAM:
        req._send_error_json(400, 1, f'Unknown tile source {source!r}. Supported: {list(_TILE_UPSTREAM)}')
        return

    # --- Cache lookup ---
    cache_enabled = True
    if _db:
        cache_enabled = _db.get_setting('tile_cache_enabled', 'true').lower() in ('1', 'true', 'yes')

    ext = 'jpg' if source == 'esri_satellite' else 'png'
    cache_path: Optional[str] = None

    if _data_dir and cache_enabled:
        cache_path = os.path.join(_data_dir, 'tiles', source, z, x, f'{y}.{ext}')
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, 'rb') as fh:
                    tile_data = fh.read()
                req._send_raw(tile_data, _TILE_CONTENT_TYPE[source])
                return
            except OSError:
                pass  # Fall through to upstream fetch

    # --- Upstream fetch ---
    upstream_url = _TILE_UPSTREAM[source].format(z=z, x=x, y=y)
    try:
        resp = _tile_session.get(upstream_url, timeout=10)
        if resp.status_code == 200:
            tile_data = resp.content
        else:
            tile_data = None
    except Exception:
        tile_data = None

    if tile_data is None:
        # Upstream unreachable — serve stale cache if available, else 503
        if cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, 'rb') as fh:
                    tile_data = fh.read()
                req._send_raw(tile_data, _TILE_CONTENT_TYPE[source])
                return
            except OSError:
                pass
        req._send_error_json(503, 5, 'Tile upstream unavailable and no cached tile')
        return

    # --- Save to cache ---
    if cache_path and cache_enabled:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'wb') as fh:
                fh.write(tile_data)
        except OSError:
            pass  # Non-fatal: serve tile even if we couldn't cache it

    req._send_raw(tile_data, _TILE_CONTENT_TYPE[source])


# ---------------------------------------------------------------------------
# Data Maintenance
# ---------------------------------------------------------------------------

@router.route('GET', '/api/data/stats')
def api_data_stats(req: RequestHandler):
    if _db is None:
        req._send_error_json(503, 5, 'Database not available')
        return
    stats = _db.get_stats()
    retention = int(_db.get_setting('retention_days', '14') or '14')
    stats['retention_days'] = retention
    req._send_json(req._ok(stats))


@router.route('POST', '/api/data/purge')
def api_data_purge(req: RequestHandler):
    if _db is None:
        req._send_error_json(503, 5, 'Database not available')
        return

    body = req.json_data or {}
    before_ts = body.get('before', '').strip()
    if not before_ts:
        req._send_error_json(400, 1, 'before timestamp is required')
        return

    try:
        detections_deleted = _db.purge_detections(before_ts)
        alerts_deleted = _db.purge_alerts(before_ts)
    except Exception as e:
        req._send_error_json(500, 5, f'Purge failed: {e}')
        return

    req._send_json(req._ok({
        'detections_deleted': detections_deleted,
        'alerts_deleted': alerts_deleted,
    }))


@router.route('POST', '/api/data/purge-tiles')
def api_data_purge_tiles(req: RequestHandler):
    if _data_dir is None:
        req._send_error_json(503, 5, 'Data directory not configured')
        return

    body = req.json_data or {}
    source_filter = body.get('source', '').strip() or None

    tile_base = os.path.join(_data_dir, 'tiles')
    tiles_deleted = 0
    bytes_freed = 0

    if not os.path.isdir(tile_base):
        req._send_json(req._ok({'tiles_deleted': 0, 'bytes_freed': 0}))
        return

    try:
        if source_filter:
            # Purge only the named source subdirectory
            source_dir = os.path.join(tile_base, source_filter)
            if os.path.isdir(source_dir):
                for dirpath, _dirnames, filenames in os.walk(source_dir):
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        try:
                            bytes_freed += os.path.getsize(fpath)
                            os.remove(fpath)
                            tiles_deleted += 1
                        except OSError:
                            pass
                try:
                    shutil.rmtree(source_dir, ignore_errors=True)
                except OSError:
                    pass
        else:
            # Purge everything under tiles/
            for dirpath, _dirnames, filenames in os.walk(tile_base):
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        bytes_freed += os.path.getsize(fpath)
                        os.remove(fpath)
                        tiles_deleted += 1
                    except OSError:
                        pass
            try:
                shutil.rmtree(tile_base, ignore_errors=True)
            except OSError:
                pass
    except Exception as e:
        req._send_error_json(500, 5, f'Tile purge failed: {e}')
        return

    req._send_json(req._ok({
        'tiles_deleted': tiles_deleted,
        'bytes_freed': bytes_freed,
    }))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# Keys whose change requires a server restart to take effect.
_RESTART_REQUIRED_KEYS = frozenset({
    'port', 'bind_address', 'https_enabled', 'https_cert_name',
})

# Settings keys that map directly to typed values (all others are passed through as strings).
_SETTINGS_WRITABLE = frozenset({
    'port', 'bind_address',
    'https_enabled', 'https_cert_name',
    'auth_token', 'allowed_ips',
    'gps_mode', 'gps_static_lat', 'gps_static_lon', 'gps_static_alt',
    'retention_days',
    'cot_enabled', 'cot_address', 'cot_port',
    'alert_audio_enabled', 'alert_visual_enabled',
    'alert_script_enabled', 'alert_script_path',
    'alert_slack_enabled', 'alert_slack_webhook_url', 'alert_slack_display_name',
    'tile_cache_enabled',
    'monitor_interface',
})


@router.route('GET', '/api/settings')
def api_settings_get(req: RequestHandler):
    if _db is None:
        req._send_error_json(503, 5, 'Database not available')
        return
    raw = _db.get_all_settings()
    coerced = _coerce_settings(raw)
    req._send_json(req._ok({'settings': coerced}))


@router.route('PUT', '/api/settings')
def api_settings_put(req: RequestHandler):
    if _db is None:
        req._send_error_json(503, 5, 'Database not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return

    restart_required = False
    for key, value in req.json_data.items():
        if key not in _SETTINGS_WRITABLE:
            # Silently skip unknown / non-writable keys.
            continue
        # Normalise value to string for DB storage
        if isinstance(value, bool):
            str_value = 'true' if value else 'false'
        else:
            str_value = str(value)

        _db.set_setting(key, str_value)

        if key in _RESTART_REQUIRED_KEYS:
            restart_required = True

    data = req.json_data

    # Apply GPS settings live if any GPS-related key changed.
    if any(k in data for k in ('gps_mode', 'gps_static_lat', 'gps_static_lon', 'gps_static_alt')):
        if _gps_engine:
            _gps_engine.configure(
                mode=_db.get_setting('gps_mode', 'none'),
                static_lat=float(_db.get_setting('gps_static_lat', '0.0')),
                static_lon=float(_db.get_setting('gps_static_lon', '0.0')),
                static_alt=float(_db.get_setting('gps_static_alt', '0.0')),
            )

    # Apply CoT settings live if any CoT-related key changed.
    if any(k in data for k in ('cot_enabled', 'cot_address', 'cot_port')):
        if _cot_engine:
            _cot_engine.configure(
                enabled=_db.get_setting('cot_enabled', 'false').lower() == 'true',
                address=_db.get_setting('cot_address', '239.2.3.1'),
                port=int(_db.get_setting('cot_port', '6969')),
            )

    # Re-read and return the updated settings
    raw = _db.get_all_settings()
    coerced = _coerce_settings(raw)

    req._send_json(req._ok({
        'settings': coerced,
        'restart_required': restart_required,
    }))

# ---------------------------------------------------------------------------
# Certificate Management
# ---------------------------------------------------------------------------

@router.route('GET', '/api/certs')
def api_certs_list(req: RequestHandler):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return
    try:
        certs = _cert_manager.list_certs()
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to list certs: {e}')
        return
    req._send_json(req._ok({'certs': certs}))


@router.route('POST', '/api/certs/self-signed')
def api_certs_self_signed(req: RequestHandler):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return

    body = req.json_data
    common_name = body.get('common_name', '').strip()
    if not common_name:
        req._send_error_json(400, 1, 'common_name is required')
        return

    try:
        days = int(body.get('days', 365))
    except (ValueError, TypeError):
        req._send_error_json(400, 1, 'days must be an integer')
        return

    try:
        key_size = int(body.get('key_size', 2048))
    except (ValueError, TypeError):
        req._send_error_json(400, 1, 'key_size must be an integer')
        return

    try:
        cert_info = _cert_manager.generate_self_signed(
            common_name=common_name,
            days=days,
            key_size=key_size,
        )
    except RuntimeError as e:
        req._send_error_json(500, 5, str(e))
        return
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to generate certificate: {e}')
        return

    req._send_json(req._ok({'cert': cert_info}))


@router.route('POST', '/api/certs/csr')
def api_certs_csr(req: RequestHandler):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return

    body = req.json_data
    common_name = body.get('common_name', '').strip()
    if not common_name:
        req._send_error_json(400, 1, 'common_name is required')
        return

    organization = body.get('organization', '')
    country = body.get('country', '')

    try:
        key_size = int(body.get('key_size', 2048))
    except (ValueError, TypeError):
        req._send_error_json(400, 1, 'key_size must be an integer')
        return

    try:
        csr_info = _cert_manager.generate_csr(
            common_name=common_name,
            organization=organization,
            country=country,
            key_size=key_size,
        )
    except RuntimeError as e:
        req._send_error_json(500, 5, str(e))
        return
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to generate CSR: {e}')
        return

    req._send_json(req._ok({'csr': csr_info}))


@router.route('POST', '/api/certs/import')
def api_certs_import(req: RequestHandler):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error_json(400, 1, 'JSON body required')
        return

    body = req.json_data
    name = body.get('name', '').strip()
    cert_pem = body.get('cert_pem', '').strip()
    key_pem = body.get('key_pem', None)

    if not name:
        req._send_error_json(400, 1, 'name is required')
        return
    if not cert_pem:
        req._send_error_json(400, 1, 'cert_pem is required')
        return

    try:
        cert_info = _cert_manager.import_cert(
            name=name,
            cert_pem=cert_pem,
            key_pem=key_pem if key_pem else None,
        )
    except (ValueError, RuntimeError) as e:
        req._send_error_json(400, 1, str(e))
        return
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to import certificate: {e}')
        return

    req._send_json(req._ok({'cert': cert_info}))


@router.route('GET', '/api/certs/{name}')
def api_cert_detail(req: RequestHandler, name: str):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return

    name = unquote(name)
    try:
        cert_info = _cert_manager.get_cert_info(name)
    except FileNotFoundError as e:
        req._send_error_json(404, 2, str(e))
        return
    except RuntimeError as e:
        req._send_error_json(500, 5, str(e))
        return
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to get cert info: {e}')
        return

    req._send_json(req._ok({'cert': cert_info}))


@router.route('DELETE', '/api/certs/{name}')
def api_cert_delete(req: RequestHandler, name: str):
    if _cert_manager is None:
        req._send_error_json(503, 5, 'Certificate manager not available')
        return

    name = unquote(name)
    try:
        deleted = _cert_manager.delete_cert(name)
    except Exception as e:
        req._send_error_json(500, 5, f'Failed to delete cert: {e}')
        return

    if not deleted:
        req._send_error_json(404, 2, f'Certificate {name!r} not found')
        return

    req._send_json(req._ok({'deleted': True, 'name': name}))
