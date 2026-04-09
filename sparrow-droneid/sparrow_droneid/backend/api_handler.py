"""
HTTP API Handler for Sparrow DroneID.

Provides a multithreaded HTTP server with:
- APIRouter decorator-based route dispatch
- IP/subnet allowlist and Bearer token authentication (API paths only)
- Static file serving for the frontend SPA
- Tile proxy with optional disk cache
- OpenAPI 3.0 compliance via /api/v1/openapi.json
- Legacy /api/ → /api/v1/ redirect with Deprecation headers
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
from .elasticsearch_engine import ElasticsearchEngine
from .database import Database
from .export import generate_kml
from .cert_manager import CertManager
from .wifi_ssid_scanner import WifiSsidScanner
from .openapi import (qparam, path_param, json_body, json_body_inline,
                      response_ref, response_inline, build_openapi_spec)
from .errors import ErrorCode


# ---------------------------------------------------------------------------
# Module-level engine references — populated by app.py via set_engines()
# ---------------------------------------------------------------------------

_droneid_engine: Optional[DroneIDEngine] = None
_gps_engine: Optional[GPSEngine] = None
_alert_engine: Optional[AlertEngine] = None
_cot_engine: Optional[CotEngine] = None
_es_engine: Optional[ElasticsearchEngine] = None
_db: Optional[Database] = None
_data_dir: Optional[str] = None
_html_dir: Optional[str] = None
_cert_manager: Optional[CertManager] = None
_wifi_ssid_scanner: Optional[WifiSsidScanner] = None

# Startup timestamp for uptime calculation
_start_time: datetime = datetime.now(timezone.utc)

# Persistent HTTP session for tile proxy upstream fetches
_tile_session: _requests.Session = _requests.Session()
_tile_session.headers.update({'User-Agent': 'SparrowDroneID/1.0'})
_tile_adapter = _requests.adapters.HTTPAdapter(pool_maxsize=20, pool_connections=4)
_tile_session.mount('https://', _tile_adapter)
_tile_session.mount('http://', _tile_adapter)

# OpenAPI spec cache (built lazily on first request)
_openapi_cache: Optional[bytes] = None


def set_engines(droneid: DroneIDEngine, gps: GPSEngine, alert: AlertEngine,
                cot: CotEngine, db: Database, data_dir: str, html_dir: str,
                cert_manager: CertManager = None,
                wifi_ssid_scanner: WifiSsidScanner = None,
                es_engine: ElasticsearchEngine = None) -> None:
    """Called by app.py at startup to wire engine references into this module."""
    global _droneid_engine, _gps_engine, _alert_engine, _cot_engine, _es_engine
    global _db, _data_dir, _html_dir, _cert_manager, _wifi_ssid_scanner
    _droneid_engine = droneid
    _gps_engine = gps
    _alert_engine = alert
    _cot_engine = cot
    _es_engine = es_engine
    _db = db
    _data_dir = data_dir
    _html_dir = html_dir
    _cert_manager = cert_manager
    _wifi_ssid_scanner = wifi_ssid_scanner


# ---------------------------------------------------------------------------
# Tile upstream URL table
# ---------------------------------------------------------------------------

_TILE_UPSTREAM = {
    'osm':            'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'esri_satellite': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    'esri_labels':    'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
}

_TILE_CONTENT_TYPE = {
    'osm':            'image/png',
    'esri_satellite': 'image/jpeg',
    'esri_labels':    'image/png',
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

    def route(self, method: str, pattern: str, spec: dict = None):
        """Decorator: @router.route('GET', '/api/v1/foo', spec={...})"""
        def decorator(func: Callable):
            if spec is not None:
                func._openapi_spec = spec
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
        self._deprecated_path: bool = False
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
                self._send_error(403, ErrorCode.FORBIDDEN, 'Connections not authorized from your IP address')
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
                self._send_error(401, ErrorCode.AUTH_REQUIRED, 'Authentication required')
                return False
            if token != required_token:
                self._send_error(401, ErrorCode.AUTH_REQUIRED, 'Authentication required')
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
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self._send_cors_headers()
            if getattr(self, '_deprecated_path', False):
                self.send_header('Deprecation', 'true')
                self.send_header('Sunset', '2026-09-01')
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # Client disconnected before response was sent

    def _send_error(self, http_status: int, code: str, message: str, detail: dict = None) -> None:
        """Send standardized OpenAPI error response."""
        body = {'error': {'code': code, 'message': message}}
        if detail:
            body['error']['detail'] = detail
        self._send_json(body, status=http_status)

    def _send_ok(self, data: dict, status: int = 200) -> None:
        """Send success response — domain data directly, no wrapper."""
        self._send_json(data, status=status)

    # LEGACY — remove after burn-in period
    def _send_error_json(self, http_status: int, errcode: int, errmsg: str) -> None:
        self._send_json({'errcode': errcode, 'errmsg': errmsg}, status=http_status)

    # LEGACY — remove after burn-in period
    def _ok(self, extra: Dict = None) -> Dict:
        """Build a success response dict, merging in any extra fields."""
        d = {'errcode': 0, 'errmsg': ''}
        if extra:
            d.update(extra)
        return d

    def _send_raw(self, data: bytes, content_type: str, status: int = 200,
                  extra_headers: Dict[str, str] = None) -> None:
        try:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
            self._send_cors_headers()
            if getattr(self, '_deprecated_path', False):
                self.send_header('Deprecation', 'true')
                self.send_header('Sunset', '2026-09-01')
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, url_path: str) -> None:
        """Serve frontend static files from html_dir."""
        html_dir = _html_dir
        if html_dir is None:
            self._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Frontend not available')
            return

        # Strip leading slashes; normalise to prevent directory traversal.
        clean = url_path.lstrip('/')
        if not clean:
            clean = 'index.html'

        # Security: reject any path with ..
        if '..' in clean.split('/'):
            self._send_error(403, ErrorCode.FORBIDDEN, 'Forbidden')
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
                    self._send_error(500, ErrorCode.INTERNAL_ERROR, f'Error reading file: {e}')
            else:
                # Unknown extension — let mimetypes guess.
                ct, _ = mimetypes.guess_type(filepath)
                ct = ct or 'application/octet-stream'
                try:
                    with open(filepath, 'rb') as fh:
                        contents = fh.read()
                    self._send_raw(contents, ct)
                except OSError as e:
                    self._send_error(500, ErrorCode.INTERNAL_ERROR, f'Error reading file: {e}')
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
                    self._send_error(404, ErrorCode.NOT_FOUND, 'Not found')
            else:
                self._send_error(404, ErrorCode.NOT_FOUND, 'Not found')

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    def _handle_api(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        self._deprecated_path = False

        handler, params = router.match(method, path)

        # Legacy /api/ fallback → try /api/v1/
        if handler is None and path.startswith('/api/') and not path.startswith('/api/v1/'):
            v1_path = '/api/v1/' + path[len('/api/'):]
            handler, params = router.match(method, v1_path)
            if handler:
                self._deprecated_path = True

        if handler:
            try:
                handler(self, **params)
            except BrokenPipeError:
                pass  # Client disconnected
            except Exception as e:
                traceback.print_exc()
                try:
                    self._send_error(500, ErrorCode.INTERNAL_ERROR, f'Internal server error: {e}')
                except BrokenPipeError:
                    pass
        elif method == 'OPTIONS':
            self.send_response(204)
            self._send_cors_headers()
            allowed = params.get('_allowed_methods', [])
            if allowed:
                self.send_header('Access-Control-Allow-Methods', ', '.join(allowed))
            self.end_headers()
        else:
            self._send_error(404, ErrorCode.NOT_FOUND, 'API endpoint not found')

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
        'alert_slack_enabled',
        'tile_cache_enabled',
        'wifi_ssid_enabled',
        'es_enabled', 'es_verify_tls', 'es_dashboards_verify_tls',
    }
    int_keys = {'port', 'cot_port', 'retention_days',
                'es_shards', 'es_replicas', 'es_bulk_size', 'es_flush_interval'}
    float_keys = {'gps_static_lat', 'gps_static_lon', 'gps_static_alt'}
    # Sensitive fields: show '(set)' if non-empty, '' if empty — never reveal value.
    sensitive_keys = {
        'auth_token',
        'es_password', 'es_api_key',
        'es_dashboards_password', 'es_dashboards_api_key',
    }

    result: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in sensitive_keys:
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

@router.route('GET', '/api/v1/status', spec={
    'summary': 'Server status and version',
    'tags': ['System'],
    'responses': {'200': response_ref('StatusResponse', 'Server status summary')},
})
def api_status(req: RequestHandler):
    engine_status = _droneid_engine.get_status() if _droneid_engine else {}
    gps_dict = _gps_engine.to_dict() if _gps_engine else {}
    db_stats = _db.get_stats() if _db else {}
    uptime = int((datetime.now(timezone.utc) - _start_time).total_seconds())
    active_count = len(_droneid_engine.get_active_drones()) if _droneid_engine else 0
    unique_total = _db.get_unique_serial_count() if _db else 0
    retention = int(_db.get_setting('retention_days', '14') or '14') if _db else 14

    req._send_ok({
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
    })


@router.route('GET', '/api/v1/openapi.json', spec={
    'summary': 'OpenAPI 3.0 specification',
    'tags': ['System'],
    'responses': {'200': response_inline({}, 'OpenAPI 3.0.3 JSON document')},
})
def api_openapi_spec(req: RequestHandler):
    global _openapi_cache
    if _openapi_cache is None:
        spec = build_openapi_spec(router)
        _openapi_cache = json.dumps(spec, indent=2).encode('utf-8')
    req._send_raw(_openapi_cache, 'application/json')


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/interfaces', spec={
    'summary': 'List available WiFi interfaces',
    'tags': ['Monitoring'],
    'responses': {
        '200': response_inline(
            {'interfaces': {'type': 'array', 'items': {'$ref': '#/components/schemas/InterfaceInfo'}}},
            'List of available interfaces',
        ),
    },
})
def api_interfaces(req: RequestHandler):
    interfaces = CaptureManager.get_interfaces()
    req._send_ok({'interfaces': interfaces})


@router.route('POST', '/api/v1/monitor/start', spec={
    'summary': 'Start packet capture on a WiFi interface',
    'tags': ['Monitoring'],
    'requestBody': json_body_inline(
        {'interface': {'type': 'string', 'description': 'Interface name'},
         'channel':   {'type': 'integer', 'description': 'WiFi channel (default 6)'}},
        required_props=['interface'],
        description='Interface and channel to monitor',
    ),
    'responses': {
        '200': response_inline(
            {'interface': {'type': 'string'},
             'channel':   {'type': 'integer'},
             'status':    {'type': 'string'}},
            'Monitoring started',
        ),
        '404': {'description': 'Interface not found', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '409': {'description': 'Already monitoring or conflict', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_monitor_start(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Engine not available')
        return

    body = req.json_data or {}
    interface = body.get('interface', '').strip()
    channel = int(body.get('channel', 6))

    if not interface:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'interface is required')
        return

    if _droneid_engine.monitoring:
        req._send_error(409, ErrorCode.CONFLICT, 'Already monitoring')
        return

    # Validate that the interface exists
    ifaces = CaptureManager.get_interfaces()
    iface_names = [i['name'] for i in ifaces]
    if interface not in iface_names:
        req._send_error(404, ErrorCode.NOT_FOUND, f'Interface {interface!r} not found')
        return

    # Validate monitor capability
    iface_info = next((i for i in ifaces if i['name'] == interface), {})
    if not iface_info.get('monitor_capable', False):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, f'Interface {interface!r} does not support monitor mode')
        return

    try:
        _droneid_engine.start(interface, channel)
    except RuntimeError as e:
        req._send_error(409, ErrorCode.CONFLICT, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to start capture: {e}')
        return

    status = _droneid_engine.get_status()
    req._send_ok({
        'interface': status['interface'],
        'channel': status['channel'],
        'status': 'monitoring',
    })


@router.route('POST', '/api/v1/monitor/stop', spec={
    'summary': 'Stop active packet capture',
    'tags': ['Monitoring'],
    'responses': {
        '200': response_inline(
            {'status':                   {'type': 'string'},
             'session_duration_seconds': {'type': 'integer'},
             'frames_captured':          {'type': 'integer'},
             'drones_detected':          {'type': 'integer'}},
            'Session summary after stop',
        ),
        '409': {'description': 'Not currently monitoring', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_monitor_stop(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Engine not available')
        return

    if not _droneid_engine.monitoring:
        req._send_error(409, ErrorCode.CONFLICT, 'Not currently monitoring')
        return

    status_before = _droneid_engine.get_status()
    duration = status_before.get('duration_seconds', 0)
    frames = status_before.get('frame_count', 0)
    drones = len(_droneid_engine.get_active_drones(max_age=0))

    try:
        _droneid_engine.stop()
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Error stopping capture: {e}')
        return

    req._send_ok({
        'status': 'stopped',
        'session_duration_seconds': duration,
        'frames_captured': frames,
        'drones_detected': drones,
    })


@router.route('GET', '/api/v1/monitor/status', spec={
    'summary': 'Get current monitor status',
    'tags': ['Monitoring'],
    'responses': {
        '200': response_ref('MonitorStatus', 'Current monitor status'),
        '503': {'description': 'Engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_monitor_status(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Engine not available')
        return
    status = _droneid_engine.get_status()
    req._send_ok(status)


# ---------------------------------------------------------------------------
# Detections (Live)
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/drones', spec={
    'summary': 'List currently active drone detections',
    'tags': ['Detections'],
    'parameters': [
        qparam('max_age', 'integer', 'Maximum age in seconds for a detection to be included (default 180)', default=180),
    ],
    'responses': {
        '200': response_ref('DroneListResponse', 'Live drone detections with receiver position and counts'),
        '503': {'description': 'Engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_drones(req: RequestHandler):
    if _droneid_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Engine not available')
        return

    max_age = req._qparam_int('max_age', 180)
    drones = _droneid_engine.get_active_drones(max_age=max_age)

    # Enrich with vendor/manufacturer if available
    if _alert_engine:
        for d in drones:
            d['vendor'] = _alert_engine.resolve_vendor(
                serial=d.get('serial_number', ''),
                mac=d.get('mac_address', ''),
                protocol=d.get('protocol', ''),
            )

    # Build receiver block from GPS engine
    gps = _gps_engine.to_dict() if _gps_engine else {}
    receiver = {
        'lat': gps.get('latitude', 0.0),
        'lon': gps.get('longitude', 0.0),
        'alt': gps.get('altitude', 0.0),
        'speed': gps.get('speed', 0.0),
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

    req._send_ok({
        'receiver': receiver,
        'drones': drones,
        'counts': {
            'active': active_count,
            'aging': aging_count,
            'stale': stale_count,
        },
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


@router.route('GET', '/api/v1/drones/{serial}', spec={
    'summary': 'Get detail and track for a single drone',
    'tags': ['Detections'],
    'parameters': [
        path_param('serial', 'string', 'Drone serial number (URL-encoded)'),
        qparam('track_minutes', 'integer', 'Number of minutes of track history to include (default 5)', default=5),
    ],
    'responses': {
        '200': response_ref('DroneDetailResponse', 'Drone detail with track'),
        '404': {'description': 'Drone not found', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_drone_detail(req: RequestHandler, serial: str):
    if _droneid_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Engine not available')
        return

    serial = unquote(serial)
    track_minutes = req._qparam_int('track_minutes', 5)

    drone_dict, track = _droneid_engine.get_drone_detail(serial, track_minutes)

    if drone_dict is None:
        req._send_error(404, ErrorCode.NOT_FOUND, f'Drone {serial!r} not found')
        return

    # Enrich with vendor/manufacturer
    if _alert_engine and drone_dict:
        drone_dict['vendor'] = _alert_engine.resolve_vendor(
            serial=drone_dict.get('serial_number', ''),
            mac=drone_dict.get('mac_address', ''),
            protocol=drone_dict.get('protocol', ''),
        )

    req._send_ok({'drone': drone_dict, 'track': track})


# ---------------------------------------------------------------------------
# History / Replay
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/history', spec={
    'summary': 'Query historical detection records',
    'tags': ['History'],
    'parameters': [
        qparam('from', 'string', 'Start of time range (ISO 8601 UTC)', required=True),
        qparam('to', 'string', 'End of time range (ISO 8601 UTC)', required=True),
        qparam('serial', 'string', 'Filter to a specific serial number'),
        qparam('limit', 'integer', 'Maximum records to return (default 10000)', default=10000),
        qparam('offset', 'integer', 'Offset into result set (default 0)', default=0),
    ],
    'responses': {
        '200': response_inline(
            {'records':    {'type': 'array', 'items': {'$ref': '#/components/schemas/HistoryRecord'}},
             'pagination': {'$ref': '#/components/schemas/PaginationMeta'}},
            'Historical records with pagination metadata',
        ),
    },
})
def api_history(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'from and to query parameters are required')
        return

    serial = req._qparam('serial') or None
    limit = req._qparam_int('limit', 10000)
    offset = req._qparam_int('offset', 0)

    try:
        records, total = _db.get_history(from_ts, to_ts, serial, limit, offset)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    req._send_ok({
        'records': records,
        'pagination': {
            'total_count': total,
            'returned_count': len(records),
            'limit': limit,
            'offset': offset,
        },
    })


@router.route('GET', '/api/v1/history/serials', spec={
    'summary': 'List distinct drone serials seen in a time window',
    'tags': ['History'],
    'parameters': [
        qparam('from', 'string', 'Start of time range (ISO 8601 UTC)', required=True),
        qparam('to', 'string', 'End of time range (ISO 8601 UTC)', required=True),
    ],
    'responses': {
        '200': response_inline(
            {'serials': {'type': 'array', 'items': {'$ref': '#/components/schemas/HistorySerial'}}},
            'List of distinct serials seen in the window',
        ),
    },
})
def api_history_serials(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'from and to query parameters are required')
        return

    try:
        serials = _db.get_history_serials(from_ts, to_ts)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    req._send_ok({'serials': serials})


@router.route('GET', '/api/v1/history/timeline', spec={
    'summary': 'Aggregate detection timeline into time buckets',
    'tags': ['History'],
    'parameters': [
        qparam('from', 'string', 'Start of time range (ISO 8601 UTC)', required=True),
        qparam('to', 'string', 'End of time range (ISO 8601 UTC)', required=True),
        qparam('bucket_seconds', 'integer', 'Bucket width in seconds (default 10)', default=10),
    ],
    'responses': {
        '200': response_inline(
            {'buckets': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineBucket'}}},
            'Timeline buckets',
        ),
    },
})
def api_history_timeline(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'from and to query parameters are required')
        return

    bucket_seconds = req._qparam_int('bucket_seconds', 10)

    try:
        buckets = _db.get_history_timeline(from_ts, to_ts, bucket_seconds)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    req._send_ok({'buckets': buckets})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/export/kml', spec={
    'summary': 'Export detections as a KML file',
    'tags': ['Export'],
    'parameters': [
        qparam('from', 'string', 'Start of time range (ISO 8601 UTC)', required=True),
        qparam('to', 'string', 'End of time range (ISO 8601 UTC)', required=True),
        qparam('serial', 'string', 'Filter to a specific serial number'),
    ],
    'responses': {
        '200': {
            'description': 'KML file download',
            'content': {'application/vnd.google-earth.kml+xml': {'schema': {'type': 'string'}}},
        },
    },
})
def api_export_kml(req: RequestHandler):
    from_ts = req._qparam('from')
    to_ts = req._qparam('to')

    if not from_ts or not to_ts:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'from and to query parameters are required')
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
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'KML generation failed: {e}')
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

@router.route('GET', '/api/v1/alerts/config', spec={
    'summary': 'Get alert configuration and rules',
    'tags': ['Alerts'],
    'responses': {
        '200': response_ref('AlertConfig', 'Current alert configuration'),
        '503': {'description': 'Alert engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_alerts_config_get(req: RequestHandler):
    if _alert_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Alert engine not available')
        return
    config = _alert_engine.get_config()
    req._send_ok(config)


@router.route('PUT', '/api/v1/alerts/config', spec={
    'summary': 'Replace alert configuration and rules',
    'tags': ['Alerts'],
    'requestBody': json_body('AlertConfig', 'Updated alert configuration'),
    'responses': {
        '200': response_inline({}, 'Configuration updated'),
        '503': {'description': 'Alert engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_alerts_config_put(req: RequestHandler):
    if _alert_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Alert engine not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return
    try:
        _alert_engine.set_config(req.json_data)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to update alert config: {e}')
        return
    req._send_ok({})


@router.route('POST', '/api/v1/alerts/slack-test', spec={
    'summary': 'Send a test Slack notification',
    'tags': ['Alerts'],
    'requestBody': json_body_inline(
        {'webhook_url':   {'type': 'string', 'description': 'Slack incoming webhook URL'},
         'display_name':  {'type': 'string', 'description': 'Bot display name'}},
        required_props=['webhook_url'],
        description='Slack webhook parameters',
    ),
    'responses': {
        '200': response_inline(
            {'success': {'type': 'boolean'}, 'message': {'type': 'string'}},
            'Slack test result',
        ),
    },
})
def api_alerts_slack_test(req: RequestHandler):
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return
    webhook_url = req.json_data.get('webhook_url', '')
    display_name = req.json_data.get('display_name', 'Sparrow DroneID')
    result = AlertEngine.test_slack(webhook_url, display_name)
    req._send_ok(result)


@router.route('GET', '/api/v1/alerts/log', spec={
    'summary': 'Query alert event log',
    'tags': ['Alerts'],
    'parameters': [
        qparam('from', 'string', 'Start of time range (ISO 8601 UTC)'),
        qparam('to', 'string', 'End of time range (ISO 8601 UTC)'),
        qparam('limit', 'integer', 'Maximum records to return (default 100)', default=100),
        qparam('offset', 'integer', 'Offset into result set (default 0)', default=0),
        qparam('state', 'string', 'Filter by alert state', enum=['ACTIVE', 'ACKNOWLEDGED', 'RESOLVED']),
    ],
    'responses': {
        '200': response_inline(
            {'alerts':     {'type': 'array', 'items': {'$ref': '#/components/schemas/AlertEvent'}},
             'pagination': {'$ref': '#/components/schemas/PaginationMeta'}},
            'Alert log with pagination metadata',
        ),
    },
})
def api_alerts_log(req: RequestHandler):
    from_ts = req._qparam('from') or None
    to_ts = req._qparam('to') or None
    limit = req._qparam_int('limit', 100)
    offset = req._qparam_int('offset', 0)
    state = req._qparam('state') or None

    # Validate state filter if provided
    if state and state not in ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED'):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, "state must be 'ACTIVE', 'ACKNOWLEDGED', or 'RESOLVED'")
        return

    try:
        alerts, total = _db.get_alerts(from_ts, to_ts, limit, offset, state=state)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    req._send_ok({
        'alerts': alerts,
        'pagination': {
            'total_count': total,
            'returned_count': len(alerts),
            'limit': limit,
            'offset': offset,
        },
    })


@router.route('PUT', '/api/v1/alerts/acknowledge', spec={
    'summary': 'Bulk-acknowledge all ACTIVE alerts',
    'tags': ['Alerts'],
    'requestBody': json_body_inline(
        {'operator': {'type': 'string', 'description': 'Operator name to record on acknowledgements'}},
        description='Acknowledgement details',
    ),
    'responses': {
        '200': response_inline(
            {'count': {'type': 'integer', 'description': 'Number of alerts acknowledged'}},
            'Bulk acknowledgement result',
        ),
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_alerts_acknowledge_all(req: RequestHandler):
    """Bulk-acknowledge all ACTIVE alerts."""
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    body = req.json_data or {}
    operator = str(body.get('operator', '')).strip()

    try:
        count = _db.acknowledge_all_active(operator)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    req._send_ok({'count': count})


@router.route('PUT', '/api/v1/alerts/{alert_id}/acknowledge', spec={
    'summary': 'Acknowledge a single alert by ID',
    'tags': ['Alerts'],
    'parameters': [
        path_param('alert_id', 'integer', 'Alert row ID'),
    ],
    'requestBody': json_body_inline(
        {'operator': {'type': 'string', 'description': 'Operator name to record on acknowledgement'}},
        description='Acknowledgement details',
    ),
    'responses': {
        '200': response_inline({}, 'Alert acknowledged'),
        '404': {'description': 'Alert not found or not in ACTIVE state', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_alert_acknowledge(req: RequestHandler, alert_id: str):
    """Acknowledge a single alert by ID."""
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    try:
        aid = int(alert_id)
    except (ValueError, TypeError):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'alert_id must be an integer')
        return

    body = req.json_data or {}
    operator = str(body.get('operator', '')).strip()

    try:
        updated = _db.acknowledge_alert(aid, operator)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Database error: {e}')
        return

    if not updated:
        req._send_error(404, ErrorCode.NOT_FOUND, f'Alert {aid} not found or not in ACTIVE state')
        return

    req._send_ok({})


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/gps', spec={
    'summary': 'Get receiver GPS position and status',
    'tags': ['GPS'],
    'responses': {
        '200': response_ref('GpsStatus', 'Current GPS status'),
        '503': {'description': 'GPS engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_gps(req: RequestHandler):
    if _gps_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'GPS engine not available')
        return
    gps_dict = _gps_engine.to_dict()
    req._send_ok(gps_dict)


# ---------------------------------------------------------------------------
# Cursor on Target (CoT)
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/cot/status', spec={
    'summary': 'Get CoT output status',
    'tags': ['CoT'],
    'responses': {
        '200': response_ref('CotStatus', 'Current CoT engine status'),
        '503': {'description': 'CoT engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_cot_status(req: RequestHandler):
    if _cot_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'CoT engine not available')
        return
    req._send_ok(_cot_engine.get_status())


@router.route('PUT', '/api/v1/cot/config', spec={
    'summary': 'Configure CoT output',
    'tags': ['CoT'],
    'requestBody': json_body_inline(
        {'enabled': {'type': 'boolean', 'description': 'Enable or disable CoT output'},
         'address': {'type': 'string',  'description': 'UDP multicast destination address'},
         'port':    {'type': 'integer', 'description': 'UDP destination port'}},
        description='CoT configuration',
    ),
    'responses': {
        '200': response_inline({}, 'CoT configuration updated'),
        '503': {'description': 'CoT engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_cot_config(req: RequestHandler):
    if _cot_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'CoT engine not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    body = req.json_data
    enabled = bool(body.get('enabled', _cot_engine.enabled))
    address = str(body.get('address', _cot_engine.address))
    try:
        port = int(body.get('port', _cot_engine.port))
    except (ValueError, TypeError):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'port must be an integer')
        return

    try:
        _cot_engine.configure(enabled, address, port)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to configure CoT: {e}')
        return

    # Persist to DB so settings survive restart
    if _db:
        _db.set_setting('cot_enabled', str(enabled).lower())
        _db.set_setting('cot_address', address)
        _db.set_setting('cot_port', str(port))

    req._send_ok({})


# ---------------------------------------------------------------------------
# Map Tiles
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/tiles/{source}/{z}/{x}/{y}', spec={
    'summary': 'Proxy a map tile (with optional disk cache)',
    'tags': ['Tiles'],
    'parameters': [
        path_param('source', 'string', 'Tile source identifier (osm, esri_satellite, or esri_labels)'),
        path_param('z', 'string', 'Zoom level'),
        path_param('x', 'string', 'Tile X coordinate'),
        path_param('y', 'string', 'Tile Y coordinate (may include .png/.jpg suffix)'),
    ],
    'responses': {
        '200': {
            'description': 'Tile image',
            'content': {'image/png': {'schema': {'type': 'string', 'format': 'binary'}},
                        'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}},
        },
        '400': {'description': 'Unknown tile source', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Tile upstream unavailable', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_tiles(req: RequestHandler, source: str, z: str, x: str, y: str):
    y = y.rsplit('.', 1)[0]  # strip .png/.jpg suffix if present
    if source not in _TILE_UPSTREAM:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, f'Unknown tile source {source!r}. Supported: {list(_TILE_UPSTREAM)}')
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
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Tile upstream unavailable and no cached tile')
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

@router.route('GET', '/api/v1/data/stats', spec={
    'summary': 'Get database and storage statistics',
    'tags': ['Data'],
    'responses': {
        '200': response_ref('DataStats', 'Database and tile cache statistics'),
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_data_stats(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    stats = _db.get_stats()
    retention = int(_db.get_setting('retention_days', '14') or '14')
    stats['retention_days'] = retention
    req._send_ok(stats)


@router.route('POST', '/api/v1/data/purge', spec={
    'summary': 'Purge detection and alert records older than a timestamp',
    'tags': ['Data'],
    'requestBody': json_body_inline(
        {'before': {'type': 'string', 'description': 'ISO 8601 UTC timestamp — records older than this will be deleted'}},
        required_props=['before'],
        description='Purge threshold',
    ),
    'responses': {
        '200': response_inline(
            {'detections_deleted': {'type': 'integer'},
             'alerts_deleted':     {'type': 'integer'}},
            'Purge result',
        ),
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_data_purge(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    body = req.json_data or {}
    before_ts = body.get('before', '').strip()
    if not before_ts:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'before timestamp is required')
        return

    try:
        detections_deleted = _db.purge_detections(before_ts)
        alerts_deleted = _db.purge_alerts(before_ts)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Purge failed: {e}')
        return

    req._send_ok({
        'detections_deleted': detections_deleted,
        'alerts_deleted': alerts_deleted,
    })


@router.route('POST', '/api/v1/data/purge-tiles', spec={
    'summary': 'Delete cached map tiles',
    'tags': ['Data'],
    'requestBody': json_body_inline(
        {'source': {'type': 'string', 'description': 'Tile source to purge (omit for all sources)', 'enum': ['osm', 'esri_satellite', 'esri_labels']}},
        description='Optional source filter',
    ),
    'responses': {
        '200': response_inline(
            {'tiles_deleted': {'type': 'integer'},
             'bytes_freed':   {'type': 'integer'}},
            'Tile purge result',
        ),
        '503': {'description': 'Data directory not configured', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_data_purge_tiles(req: RequestHandler):
    if _data_dir is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Data directory not configured')
        return

    body = req.json_data or {}
    source_filter = body.get('source', '').strip() or None

    tile_base = os.path.join(_data_dir, 'tiles')
    tiles_deleted = 0
    bytes_freed = 0

    if not os.path.isdir(tile_base):
        req._send_ok({'tiles_deleted': 0, 'bytes_freed': 0})
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
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Tile purge failed: {e}')
        return

    req._send_ok({
        'tiles_deleted': tiles_deleted,
        'bytes_freed': bytes_freed,
    })


# ---------------------------------------------------------------------------
# Geozones (airports + FAA no-fly zones)
# ---------------------------------------------------------------------------

_geozone_cache = None  # Lazy-initialized GeozoneCache


def _get_geozone_cache():
    """Lazy-init the geozone cache using the same data_dir as tile cache."""
    global _geozone_cache
    if _geozone_cache is None:
        from .geozone_cache import GeozoneCache
        _geozone_cache = GeozoneCache(_data_dir or 'data')
    return _geozone_cache


@router.route('GET', '/api/v1/geozones/airports', spec={
    'summary': 'List airports near a position',
    'tags': ['Geozones'],
    'parameters': [
        qparam('lat', 'number', 'Latitude (decimal degrees); defaults to receiver GPS position'),
        qparam('lon', 'number', 'Longitude (decimal degrees); defaults to receiver GPS position'),
        qparam('radius_mi', 'number', 'Search radius in miles (default 50)', default=50),
    ],
    'responses': {
        '200': response_inline(
            {'airports': {'type': 'array', 'items': {'type': 'object'}}},
            'Airports within the search radius',
        ),
    },
})
def api_geozones_airports(req: RequestHandler):
    cache = _get_geozone_cache()
    lat = float(req._qparam('lat') or 0)
    lon = float(req._qparam('lon') or 0)
    radius = float(req._qparam('radius_mi') or 50)

    if (lat == 0 and lon == 0) and _gps_engine:
        lat, lon, _ = _gps_engine.get_receiver_position()

    airports = cache.get_airports(lat, lon, radius)
    req._send_ok({'airports': airports})


@router.route('GET', '/api/v1/geozones/nofly', spec={
    'summary': 'Get FAA no-fly zone GeoJSON features near a position',
    'tags': ['Geozones'],
    'parameters': [
        qparam('lat', 'number', 'Latitude (decimal degrees); defaults to receiver GPS position'),
        qparam('lon', 'number', 'Longitude (decimal degrees); defaults to receiver GPS position'),
    ],
    'responses': {
        '200': response_inline(
            {'features': {'type': 'array', 'items': {'type': 'object'}}},
            'GeoJSON feature collection of no-fly zones',
        ),
    },
})
def api_geozones_nofly(req: RequestHandler):
    cache = _get_geozone_cache()
    lat = float(req._qparam('lat') or 0)
    lon = float(req._qparam('lon') or 0)

    if (lat == 0 and lon == 0) and _gps_engine:
        lat, lon, _ = _gps_engine.get_receiver_position()

    nofly = cache.get_nofly_zones(lat, lon)
    req._send_ok({'features': nofly.get('features', [])})


# ---------------------------------------------------------------------------
# Vendor Codes
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/vendor-codes', spec={
    'summary': 'Get current vendor code tables',
    'tags': ['Vendor Codes'],
    'responses': {
        '200': response_ref('VendorCodeStats', 'Current vendor code tables with counts'),
        '503': {'description': 'Database or alert engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_vendor_codes_get(req: RequestHandler):
    """Return current vendor code tables from the database."""
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if _alert_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Alert engine not available')
        return
    codes = _alert_engine.get_vendor_codes()
    req._send_ok({
        'serial_prefixes': codes['serial_prefixes'],
        'mac_oui': codes['mac_oui'],
        'serial_prefix_count': len(codes['serial_prefixes']),
        'mac_oui_count': len(codes['mac_oui']),
    })


@router.route('PUT', '/api/v1/vendor-codes', spec={
    'summary': 'Replace vendor code tables',
    'tags': ['Vendor Codes'],
    'requestBody': json_body_inline(
        {'serial_prefixes': {'type': 'object', 'description': 'Map of serial prefix → manufacturer name'},
         'mac_oui':         {'type': 'object', 'description': 'Map of MAC OUI → manufacturer name'}},
        description='Vendor code tables to replace (each key is optional)',
    ),
    'responses': {
        '200': response_ref('VendorCodeStats', 'Updated vendor code tables with counts'),
        '503': {'description': 'Database or alert engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_vendor_codes_put(req: RequestHandler):
    """Replace vendor code tables in the database and reload the alert engine."""
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if _alert_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Alert engine not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    body = req.json_data
    if 'serial_prefixes' in body:
        sp = body['serial_prefixes']
        if not isinstance(sp, dict):
            req._send_error(400, ErrorCode.VALIDATION_ERROR, 'serial_prefixes must be an object')
            return
        _db.set_setting('vendor_serial_prefixes', json.dumps(sp))

    if 'mac_oui' in body:
        oui = body['mac_oui']
        if not isinstance(oui, dict):
            req._send_error(400, ErrorCode.VALIDATION_ERROR, 'mac_oui must be an object')
            return
        _db.set_setting('vendor_mac_oui', json.dumps(oui))

    _alert_engine.reload_vendor_codes()
    codes = _alert_engine.get_vendor_codes()
    req._send_ok({
        'serial_prefixes': codes['serial_prefixes'],
        'mac_oui': codes['mac_oui'],
        'serial_prefix_count': len(codes['serial_prefixes']),
        'mac_oui_count': len(codes['mac_oui']),
    })


@router.route('POST', '/api/v1/vendor-codes/update', spec={
    'summary': 'Fetch and merge vendor codes from a configured remote URL',
    'tags': ['Vendor Codes'],
    'responses': {
        '200': response_inline(
            {'serial_prefix_count':    {'type': 'integer'},
             'mac_oui_count':          {'type': 'integer'},
             'added_serial_prefixes':  {'type': 'integer'},
             'added_mac_ouis':         {'type': 'integer'}},
            'Merge result',
        ),
        '400': {'description': 'vendor_codes_url not configured', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '502': {'description': 'Remote fetch failed', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Database or alert engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_vendor_codes_update(req: RequestHandler):
    """Fetch vendor codes from a remote URL, merge with existing, persist, and reload.

    The remote document must be JSON matching the vendor_codes.json schema:
      { "serial_prefixes": {...}, "mac_oui": {...} }

    Existing entries are preserved; remote entries take precedence on conflicts.
    If no URL is configured (vendor_codes_url is empty) the request is rejected.
    """
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if _alert_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Alert engine not available')
        return

    url = _db.get_setting('vendor_codes_url', '').strip()
    if not url:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'vendor_codes_url is not configured')
        return

    try:
        resp = _requests.get(url, timeout=15)
        resp.raise_for_status()
        remote = resp.json()
    except Exception as e:
        req._send_error(502, ErrorCode.BAD_GATEWAY, f'Failed to fetch vendor codes from remote: {e}')
        return

    if not isinstance(remote, dict):
        req._send_error(502, ErrorCode.BAD_GATEWAY, 'Remote vendor codes document is not a JSON object')
        return

    # Merge: load existing, overlay remote entries (remote wins on conflict).
    current = _alert_engine.get_vendor_codes()

    merged_serial = dict(current['serial_prefixes'])
    remote_serial = remote.get('serial_prefixes', {})
    if isinstance(remote_serial, dict):
        merged_serial.update(remote_serial)

    merged_oui = dict(current['mac_oui'])
    remote_oui = remote.get('mac_oui', {})
    if isinstance(remote_oui, dict):
        merged_oui.update(remote_oui)

    _db.set_setting('vendor_serial_prefixes', json.dumps(merged_serial))
    _db.set_setting('vendor_mac_oui', json.dumps(merged_oui))
    _alert_engine.reload_vendor_codes()

    req._send_ok({
        'serial_prefix_count': len(merged_serial),
        'mac_oui_count': len(merged_oui),
        'added_serial_prefixes': len(merged_serial) - len(current['serial_prefixes']),
        'added_mac_ouis': len(merged_oui) - len(current['mac_oui']),
    })


# ---------------------------------------------------------------------------
# WiFi SSID Detection
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/wifi-ssid/patterns', spec={
    'summary': 'Get SSID drone detection patterns',
    'tags': ['WiFi SSID'],
    'responses': {
        '200': response_inline(
            {'patterns': {'type': 'array', 'items': {'$ref': '#/components/schemas/WifiSsidPattern'}},
             'count':    {'type': 'integer'}},
            'Current SSID patterns',
        ),
        '503': {'description': 'Scanner not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_wifi_ssid_patterns_get(req: RequestHandler):
    """Return current SSID drone detection patterns."""
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if _wifi_ssid_scanner is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'WiFi SSID scanner not available')
        return
    patterns = _wifi_ssid_scanner.get_patterns()
    req._send_ok({'patterns': patterns, 'count': len(patterns)})


@router.route('PUT', '/api/v1/wifi-ssid/patterns', spec={
    'summary': 'Replace SSID drone detection patterns',
    'tags': ['WiFi SSID'],
    'requestBody': json_body_inline(
        {'patterns': {'type': 'array', 'items': {'$ref': '#/components/schemas/WifiSsidPattern'}}},
        required_props=['patterns'],
        description='Replacement patterns list; each regex is validated',
    ),
    'responses': {
        '200': response_inline(
            {'patterns': {'type': 'array', 'items': {'$ref': '#/components/schemas/WifiSsidPattern'}},
             'count':    {'type': 'integer'}},
            'Updated patterns',
        ),
        '503': {'description': 'Scanner not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_wifi_ssid_patterns_put(req: RequestHandler):
    """Replace the SSID drone detection patterns list.

    Body: { "patterns": [ { "pattern": "<regex>", "label": "<label>" }, ... ] }

    Each regex is validated with re.compile — invalid patterns are rejected.
    """
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if _wifi_ssid_scanner is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'WiFi SSID scanner not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    patterns = req.json_data.get('patterns')
    if not isinstance(patterns, list):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'patterns must be an array')
        return

    validated = []
    for i, item in enumerate(patterns):
        if not isinstance(item, dict):
            req._send_error(400, ErrorCode.VALIDATION_ERROR, f'patterns[{i}] must be an object')
            return
        pattern_str = item.get('pattern', '')
        label = item.get('label', '')
        if not pattern_str:
            req._send_error(400, ErrorCode.VALIDATION_ERROR, f'patterns[{i}].pattern is required')
            return
        try:
            import re as _re
            _re.compile(pattern_str)
        except _re.error as exc:
            req._send_error(400, ErrorCode.VALIDATION_ERROR, f'patterns[{i}].pattern is invalid regex: {exc}')
            return
        validated.append({'pattern': pattern_str, 'label': label})

    _db.set_setting('wifi_ssid_patterns', json.dumps(validated))
    _wifi_ssid_scanner.reload_patterns()

    req._send_ok({'patterns': validated, 'count': len(validated)})


@router.route('GET', '/api/v1/wifi-ssid/status', spec={
    'summary': 'Get WiFi SSID scanner status',
    'tags': ['WiFi SSID'],
    'responses': {
        '200': response_ref('WifiSsidStatus', 'WiFi SSID scanner status'),
        '503': {'description': 'Scanner not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_wifi_ssid_status_get(req: RequestHandler):
    """Return WiFi SSID scanner status."""
    if _wifi_ssid_scanner is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'WiFi SSID scanner not available')
        return
    req._send_ok(_wifi_ssid_scanner.get_status())


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
    'operator_name',
    'airport_geozone_radius_mi',
    'display_units',
    'vendor_codes_url',
    'wifi_ssid_enabled', 'wifi_ssid_agent_url', 'wifi_ssid_agent_interface', 'wifi_ssid_poll_interval',
    'es_enabled', 'es_backend_type', 'es_url', 'es_auth_method',
    'es_username', 'es_password', 'es_api_key', 'es_verify_tls',
    'es_agent_name', 'es_dashboards_url', 'es_dashboards_auth_method',
    'es_dashboards_username', 'es_dashboards_password', 'es_dashboards_api_key',
    'es_dashboards_verify_tls', 'es_index_prefix', 'es_shards', 'es_replicas',
    'es_ilm_policy', 'es_bulk_size', 'es_flush_interval',
})


@router.route('GET', '/api/v1/settings', spec={
    'summary': 'Get all application settings',
    'tags': ['Settings'],
    'responses': {
        '200': response_inline(
            {'settings': {'$ref': '#/components/schemas/Settings'}},
            'Current application settings',
        ),
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_settings_get(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    raw = _db.get_all_settings()
    coerced = _coerce_settings(raw)
    req._send_ok({'settings': coerced})


@router.route('PUT', '/api/v1/settings', spec={
    'summary': 'Update application settings',
    'tags': ['Settings'],
    'requestBody': json_body('Settings', 'Settings to update (only writable keys accepted)'),
    'responses': {
        '200': response_inline(
            {'settings':         {'$ref': '#/components/schemas/Settings'},
             'restart_required': {'type': 'boolean', 'description': 'True when a restart is needed for changes to take effect'}},
            'Updated settings with restart flag',
        ),
        '400': {'description': 'Unknown settings keys', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_settings_put(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    # Reject unknown keys
    unknown_keys = [k for k in req.json_data if k not in _SETTINGS_WRITABLE]
    if unknown_keys:
        req._send_error(
            400, ErrorCode.VALIDATION_ERROR,
            f'Unknown settings key(s): {", ".join(sorted(unknown_keys))}',
            detail={'unknown_keys': unknown_keys},
        )
        return

    restart_required = False
    for key, value in req.json_data.items():
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

    # Apply WiFi SSID scanner settings live if any related key changed.
    if any(k in data for k in ('wifi_ssid_enabled', 'wifi_ssid_agent_url', 'wifi_ssid_agent_interface', 'wifi_ssid_poll_interval')):
        if _wifi_ssid_scanner:
            _wifi_ssid_scanner.configure(
                enabled=_db.get_setting('wifi_ssid_enabled', 'false').lower() == 'true',
                agent_url=_db.get_setting('wifi_ssid_agent_url', 'http://127.0.0.1:8020'),
                poll_interval=int(_db.get_setting('wifi_ssid_poll_interval', '20')),
                agent_interface=_db.get_setting('wifi_ssid_agent_interface', '') or '',
            )

    # Apply Elasticsearch engine settings live if any es_* key changed.
    if _es_engine and any(k.startswith('es_') for k in data):
        _es_engine.configure(
            enabled=_db.get_setting('es_enabled', 'false').lower() == 'true',
            backend_type=_db.get_setting('es_backend_type', 'elasticsearch'),
            url=_db.get_setting('es_url', ''),
            auth_method=_db.get_setting('es_auth_method', 'none'),
            username=_db.get_setting('es_username', ''),
            password=_db.get_setting('es_password', ''),
            api_key=_db.get_setting('es_api_key', ''),
            verify_tls=_db.get_setting('es_verify_tls', 'true').lower() == 'true',
            agent_name=_db.get_setting('es_agent_name', ''),
            index_prefix=_db.get_setting('es_index_prefix', 'sparrow-droneid'),
            shards=int(_db.get_setting('es_shards', '2')),
            replicas=int(_db.get_setting('es_replicas', '0')),
            ilm_policy=_db.get_setting('es_ilm_policy', ''),
            bulk_size=int(_db.get_setting('es_bulk_size', '100')),
            flush_interval=int(_db.get_setting('es_flush_interval', '5')),
        )

    # Re-read and return the updated settings
    raw = _db.get_all_settings()
    coerced = _coerce_settings(raw)

    req._send_ok({
        'settings': coerced,
        'restart_required': restart_required,
    })


# ---------------------------------------------------------------------------
# Certificate Management
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/certs', spec={
    'summary': 'List all certificates',
    'tags': ['Certificates'],
    'responses': {
        '200': response_inline(
            {'certs': {'type': 'array', 'items': {'$ref': '#/components/schemas/CertInfo'}}},
            'List of installed certificates',
        ),
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_certs_list(req: RequestHandler):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return
    try:
        certs = _cert_manager.list_certs()
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to list certs: {e}')
        return
    req._send_ok({'certs': certs})


@router.route('POST', '/api/v1/certs/self-signed', spec={
    'summary': 'Generate a self-signed certificate',
    'tags': ['Certificates'],
    'requestBody': json_body_inline(
        {'common_name': {'type': 'string', 'description': 'Certificate CN (hostname or IP)'},
         'days':        {'type': 'integer', 'description': 'Validity period in days (default 365)'},
         'key_size':    {'type': 'integer', 'description': 'RSA key size in bits (default 2048)'}},
        required_props=['common_name'],
        description='Self-signed certificate parameters',
    ),
    'responses': {
        '201': response_inline(
            {'cert': {'$ref': '#/components/schemas/CertInfo'}},
            'Generated certificate details',
        ),
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_certs_self_signed(req: RequestHandler):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    body = req.json_data
    common_name = body.get('common_name', '').strip()
    if not common_name:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'common_name is required')
        return

    try:
        days = int(body.get('days', 365))
    except (ValueError, TypeError):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'days must be an integer')
        return

    try:
        key_size = int(body.get('key_size', 2048))
    except (ValueError, TypeError):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'key_size must be an integer')
        return

    try:
        cert_info = _cert_manager.generate_self_signed(
            common_name=common_name,
            days=days,
            key_size=key_size,
        )
    except RuntimeError as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to generate certificate: {e}')
        return

    req._send_ok({'cert': cert_info}, status=201)


@router.route('POST', '/api/v1/certs/csr', spec={
    'summary': 'Generate a Certificate Signing Request (CSR)',
    'tags': ['Certificates'],
    'requestBody': json_body_inline(
        {'common_name':   {'type': 'string', 'description': 'Certificate CN'},
         'organization':  {'type': 'string', 'description': 'Organization name (O field)'},
         'country':       {'type': 'string', 'description': 'Two-letter country code (C field)'},
         'key_size':      {'type': 'integer', 'description': 'RSA key size in bits (default 2048)'}},
        required_props=['common_name'],
        description='CSR parameters',
    ),
    'responses': {
        '201': response_inline(
            {'csr': {'type': 'object', 'description': 'CSR details including PEM text'}},
            'Generated CSR details',
        ),
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_certs_csr(req: RequestHandler):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    body = req.json_data
    common_name = body.get('common_name', '').strip()
    if not common_name:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'common_name is required')
        return

    organization = body.get('organization', '')
    country = body.get('country', '')

    try:
        key_size = int(body.get('key_size', 2048))
    except (ValueError, TypeError):
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'key_size must be an integer')
        return

    try:
        csr_info = _cert_manager.generate_csr(
            common_name=common_name,
            organization=organization,
            country=country,
            key_size=key_size,
        )
    except RuntimeError as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to generate CSR: {e}')
        return

    req._send_ok({'csr': csr_info}, status=201)


@router.route('POST', '/api/v1/certs/import', spec={
    'summary': 'Import an existing certificate (and optional private key)',
    'tags': ['Certificates'],
    'requestBody': json_body_inline(
        {'name':     {'type': 'string', 'description': 'Certificate name (used as filename stem)'},
         'cert_pem': {'type': 'string', 'description': 'PEM-encoded certificate'},
         'key_pem':  {'type': 'string', 'description': 'PEM-encoded private key (optional)'}},
        required_props=['name', 'cert_pem'],
        description='Certificate import payload',
    ),
    'responses': {
        '201': response_inline(
            {'cert': {'$ref': '#/components/schemas/CertInfo'}},
            'Imported certificate details',
        ),
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_certs_import(req: RequestHandler):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return
    if req.json_data is None:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'JSON body required')
        return

    body = req.json_data
    name = body.get('name', '').strip()
    cert_pem = body.get('cert_pem', '').strip()
    key_pem = body.get('key_pem', None)

    if not name:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'name is required')
        return
    if not cert_pem:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'cert_pem is required')
        return

    try:
        cert_info = _cert_manager.import_cert(
            name=name,
            cert_pem=cert_pem,
            key_pem=key_pem if key_pem else None,
        )
    except (ValueError, RuntimeError) as e:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to import certificate: {e}')
        return

    req._send_ok({'cert': cert_info}, status=201)


@router.route('GET', '/api/v1/certs/{name}', spec={
    'summary': 'Get details for a single certificate',
    'tags': ['Certificates'],
    'parameters': [
        path_param('name', 'string', 'Certificate name (filename stem, URL-encoded)'),
    ],
    'responses': {
        '200': response_inline(
            {'cert': {'$ref': '#/components/schemas/CertInfo'}},
            'Certificate details',
        ),
        '404': {'description': 'Certificate not found', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_cert_detail(req: RequestHandler, name: str):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return

    name = unquote(name)
    try:
        cert_info = _cert_manager.get_cert_info(name)
    except FileNotFoundError as e:
        req._send_error(404, ErrorCode.NOT_FOUND, str(e))
        return
    except RuntimeError as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to get cert info: {e}')
        return

    req._send_ok({'cert': cert_info})


@router.route('DELETE', '/api/v1/certs/{name}', spec={
    'summary': 'Delete a certificate by name',
    'tags': ['Certificates'],
    'parameters': [
        path_param('name', 'string', 'Certificate name (filename stem, URL-encoded)'),
    ],
    'responses': {
        '200': response_inline(
            {'deleted': {'type': 'boolean'}, 'name': {'type': 'string'}},
            'Deletion confirmation',
        ),
        '404': {'description': 'Certificate not found', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Certificate manager not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_cert_delete(req: RequestHandler, name: str):
    if _cert_manager is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Certificate manager not available')
        return

    name = unquote(name)
    try:
        deleted = _cert_manager.delete_cert(name)
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to delete cert: {e}')
        return

    if not deleted:
        req._send_error(404, ErrorCode.NOT_FOUND, f'Certificate {name!r} not found')
        return

    req._send_ok({'deleted': True, 'name': name})


# ---------------------------------------------------------------------------
# Elasticsearch / OpenSearch Integration
# ---------------------------------------------------------------------------

@router.route('GET', '/api/v1/es/status', spec={
    'summary': 'Get Elasticsearch engine status',
    'tags': ['Elasticsearch'],
    'responses': {
        '200': response_inline(
            {'enabled': {'type': 'boolean'}, 'connected': {'type': 'boolean'},
             'healthy': {'type': 'boolean'}, 'backend_type': {'type': 'string'},
             'docs_indexed': {'type': 'integer'}, 'docs_failed': {'type': 'integer'},
             'docs_dropped': {'type': 'integer'}, 'docs_in_buffer': {'type': 'integer'},
             'held_batches': {'type': 'integer'}, 'last_flush_time': {'type': 'string'},
             'last_error': {'type': 'string'}, 'last_error_time': {'type': 'string'}},
            'Elasticsearch engine status',
        ),
        '503': {'description': 'Elasticsearch engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_status(req: RequestHandler):
    if _es_engine is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Elasticsearch engine not available')
        return
    req._send_ok(_es_engine.get_status())


@router.route('POST', '/api/v1/es/test_cluster', spec={
    'summary': 'Test Elasticsearch cluster connectivity',
    'tags': ['Elasticsearch'],
    'responses': {
        '200': response_inline(
            {'ok': {'type': 'boolean'}, 'error': {'type': 'string'}},
            'Cluster connectivity test result',
        ),
        '503': {'description': 'Elasticsearch engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_test_cluster(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    # Try the running engine first; fall back to ad-hoc client
    result = None
    if _es_engine:
        result = _es_engine.test_connection()
    if result is None or (not result.get('ok') and 'not running' in result.get('error', '')):
        url = _db.get_setting('es_url', '')
        if not url:
            req._send_ok({'ok': False, 'error': 'Cluster URL not configured'})
            return
        from .elasticsearch_engine import _create_search_client
        client = None
        try:
            client = _create_search_client(
                backend_type=_db.get_setting('es_backend_type', 'elasticsearch'),
                url=url,
                auth_method=_db.get_setting('es_auth_method', 'none'),
                username=_db.get_setting('es_username', ''),
                password=_db.get_setting('es_password', ''),
                api_key=_db.get_setting('es_api_key', ''),
                verify_tls=_db.get_setting('es_verify_tls', 'true').lower() == 'true',
            )
            info = client.cluster_info()
            if not info:
                result = {'ok': False, 'error': 'No response — check URL scheme (http vs https)'}
            else:
                version = info.get('version', {})
                version_number = version.get('number', '') if isinstance(version, dict) else ''
                if not version_number:
                    result = {'ok': False, 'error': 'Response missing version — is this an Elasticsearch / OpenSearch cluster?'}
                else:
                    result = {
                        'ok': True,
                        'cluster_name': info.get('cluster_name') or info.get('name', ''),
                        'version': version_number,
                        'tagline': info.get('tagline', ''),
                    }
        except Exception as exc:
            error_msg = str(exc)
            if 'SSL' in error_msg or 'TLS' in error_msg or 'CERTIFICATE' in error_msg.upper():
                error_msg += ' — try disabling TLS verification or switching to http://'
            elif 'ConnectionError' in error_msg or 'Connection refused' in error_msg:
                error_msg += ' — check URL and port'
            result = {'ok': False, 'error': error_msg}
        finally:
            if client:
                client.close()
    req._send_ok(result)


@router.route('GET', '/api/v1/es/ilm_policies', spec={
    'summary': 'List available ILM/ISM lifecycle policies',
    'tags': ['Elasticsearch'],
    'responses': {
        '200': response_inline(
            {'policies': {'type': 'object', 'description': 'Map of policy name to policy body'}},
            'Available lifecycle policies',
        ),
        '503': {'description': 'Elasticsearch engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_ilm_policies(req: RequestHandler):
    if _es_engine is None or _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Elasticsearch engine not available')
        return
    # Try the running engine's client first; if not running, create an ad-hoc
    # temporary client from saved settings so policies can be queried before
    # the user has enabled/saved the engine.
    policies = _es_engine.get_lifecycle_policies()
    if not policies and _db.get_setting('es_url', ''):
        from .elasticsearch_engine import ElasticsearchEngine
        policies = ElasticsearchEngine.query_lifecycle_policies(
            backend_type=_db.get_setting('es_backend_type', 'elasticsearch'),
            url=_db.get_setting('es_url', ''),
            auth_method=_db.get_setting('es_auth_method', 'none'),
            username=_db.get_setting('es_username', ''),
            password=_db.get_setting('es_password', ''),
            api_key=_db.get_setting('es_api_key', ''),
            verify_tls=_db.get_setting('es_verify_tls', 'true').lower() == 'true',
        )
    req._send_ok({'policies': policies})


@router.route('POST', '/api/v1/es/ilm_policies', spec={
    'summary': 'Create a default ILM/ISM lifecycle policy',
    'tags': ['Elasticsearch'],
    'requestBody': json_body_inline(
        {'name': {'type': 'string', 'description': 'Policy name to create'},
         'hot_days': {'type': 'integer', 'description': 'Hot phase rollover age in days (default 7)'},
         'warm_days': {'type': 'integer', 'description': 'Warm phase min age in days (default 30)'},
         'delete_days': {'type': 'integer', 'description': 'Delete after this many days (default 90)'}},
        description='Policy creation parameters',
    ),
    'responses': {
        '200': response_inline(
            {'ok': {'type': 'boolean'}, 'error': {'type': 'string'}},
            'Policy creation result',
        ),
        '400': {'description': 'Missing policy name', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Elasticsearch engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_create_ilm_policy(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return
    body = req.json_data or {}
    name = (body.get('name') or '').strip()
    if not name:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'Policy name is required')
        return
    hot_days = int(body.get('hot_days', 7))
    warm_days = int(body.get('warm_days', 30))
    delete_days = int(body.get('delete_days', 90))

    # Try the running engine first; fall back to ad-hoc client from saved settings
    result = None
    if _es_engine:
        result = _es_engine.create_lifecycle_policy(name, hot_days, warm_days, delete_days)
    if result is None or (not result.get('ok') and 'not running' in result.get('error', '')):
        url = _db.get_setting('es_url', '')
        if not url:
            req._send_ok({'ok': False, 'error': 'Cluster URL not configured'})
            return
        from .elasticsearch_engine import (
            _create_search_client, build_default_ilm_policy,
            build_default_ism_policy,
        )
        backend_type = _db.get_setting('es_backend_type', 'elasticsearch')
        client = None
        try:
            client = _create_search_client(
                backend_type=backend_type,
                url=url,
                auth_method=_db.get_setting('es_auth_method', 'none'),
                username=_db.get_setting('es_username', ''),
                password=_db.get_setting('es_password', ''),
                api_key=_db.get_setting('es_api_key', ''),
                verify_tls=_db.get_setting('es_verify_tls', 'true').lower() == 'true',
            )
            if backend_type == 'opensearch':
                prefix = _db.get_setting('es_index_prefix', 'sparrow-droneid')
                policy_body = build_default_ism_policy(prefix, hot_days, warm_days, delete_days)
            else:
                policy_body = build_default_ilm_policy(hot_days, warm_days, delete_days)
            client.put_lifecycle_policy(name, policy_body)
            result = {'ok': True}
        except Exception as e:
            result = {'ok': False, 'error': str(e)}
        finally:
            if client:
                client.close()
    req._send_ok(result)


@router.route('POST', '/api/v1/es/test_dashboards', spec={
    'summary': 'Test Kibana / OpenSearch Dashboards connectivity',
    'tags': ['Elasticsearch'],
    'responses': {
        '200': response_inline(
            {'ok': {'type': 'boolean'}, 'error': {'type': 'string'},
             'status_code': {'type': 'integer'}},
            'Dashboards connectivity test result',
        ),
        '503': {'description': 'Database not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_test_dashboards(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    dashboards_url = _db.get_setting('es_dashboards_url', '').strip()
    if not dashboards_url:
        req._send_ok({'ok': False, 'error': 'Dashboards URL not configured', 'status_code': 0})
        return

    auth_method = _db.get_setting('es_dashboards_auth_method', 'none')
    username = _db.get_setting('es_dashboards_username', '')
    password = _db.get_setting('es_dashboards_password', '')
    api_key = _db.get_setting('es_dashboards_api_key', '')
    verify_tls = _db.get_setting('es_dashboards_verify_tls', 'true').lower() == 'true'

    headers = {'kbn-xsrf': 'true', 'osd-xsrf': 'true'}
    req_auth = None
    if auth_method == 'basic' and username:
        req_auth = (username, password)
    elif auth_method == 'apikey' and api_key:
        headers['Authorization'] = f'ApiKey {api_key}'

    if not verify_tls:
        from .elasticsearch_engine import _suppress_urllib3_warnings
        _suppress_urllib3_warnings()

    try:
        resp = _requests.get(
            f"{dashboards_url.rstrip('/')}/api/status",
            headers=headers,
            auth=req_auth,
            verify=verify_tls,
            timeout=10,
        )
        if resp.status_code >= 400:
            req._send_ok({'ok': False, 'error': f'HTTP {resp.status_code}', 'status_code': resp.status_code})
            return
        # Validate the response is actually Kibana/OSD (not a random HTTP server)
        try:
            data = resp.json()
        except Exception:
            data = {}
        # Kibana/OSD /api/status returns {"name":..., "version":{"number":...}, "status":...}
        version_info = data.get('version', {})
        version_number = version_info.get('number', '') if isinstance(version_info, dict) else ''
        status_info = data.get('status', {})
        if version_number:
            req._send_ok({'ok': True, 'version': version_number, 'status_code': resp.status_code})
        elif data.get('name') or status_info:
            # Looks like a dashboard service but version format differs
            req._send_ok({'ok': True, 'version': '', 'status_code': resp.status_code})
        else:
            req._send_ok({'ok': False, 'error': 'Response does not look like Kibana / OpenSearch Dashboards', 'status_code': resp.status_code})
    except Exception as e:
        error_msg = str(e)
        if 'SSL' in error_msg or 'TLS' in error_msg or 'CERTIFICATE' in error_msg.upper():
            error_msg += ' — try disabling TLS verification or switching to http://'
        elif 'ConnectionError' in error_msg or 'Connection refused' in error_msg:
            error_msg += ' — check URL and port'
        req._send_ok({'ok': False, 'error': error_msg, 'status_code': 0})


@router.route('POST', '/api/v1/es/push_dashboards', spec={
    'summary': 'Push saved-object dashboards to Kibana / OpenSearch Dashboards',
    'tags': ['Elasticsearch'],
    'requestBody': json_body_inline(
        {'overwrite': {'type': 'boolean', 'description': 'Overwrite existing objects (default true)'}},
        description='Push options',
    ),
    'responses': {
        '200': response_inline(
            {'success': {'type': 'boolean'}, 'errors': {'type': 'array', 'items': {'type': 'object'}}},
            'Dashboard push result',
        ),
        '400': {'description': 'Dashboards URL not configured or NDJSON file not found', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
        '503': {'description': 'Elasticsearch engine not available', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}},
    },
})
def api_es_push_dashboards(req: RequestHandler):
    if _db is None:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, 'Database not available')
        return

    body = req.json_data or {}
    overwrite = bool(body.get('overwrite', True))

    dashboards_url = _db.get_setting('es_dashboards_url', '').strip()
    if not dashboards_url:
        req._send_error(400, ErrorCode.VALIDATION_ERROR, 'Dashboards URL (es_dashboards_url) is not configured')
        return

    # Resolve the NDJSON file based on backend type
    backend_type = _db.get_setting('es_backend_type', 'elasticsearch')
    if backend_type == 'opensearch':
        ndjson_filename = 'osd_dashboards.ndjson'
    else:
        ndjson_filename = 'kibana_dashboards.ndjson'

    # NDJSON files are shipped static data, located relative to the source
    # tree (not the runtime _data_dir which holds the database/tile cache).
    ndjson_path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', '..', 'data', ndjson_filename,
    ))

    if not os.path.isfile(ndjson_path):
        req._send_error(400, ErrorCode.VALIDATION_ERROR,
                        f'Dashboard NDJSON file not found: {ndjson_path}')
        return

    try:
        with open(ndjson_path, 'rb') as fh:
            ndjson_bytes = fh.read()
    except OSError as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Failed to read dashboard file: {e}')
        return

    # Build dashboards auth dict
    dashboards_auth = {
        'method': _db.get_setting('es_dashboards_auth_method', 'none'),
        'username': _db.get_setting('es_dashboards_username', ''),
        'password': _db.get_setting('es_dashboards_password', ''),
        'api_key': _db.get_setting('es_dashboards_api_key', ''),
    }
    dashboards_verify_tls = _db.get_setting('es_dashboards_verify_tls', 'true').lower() == 'true'

    # Dashboard push is a direct HTTP POST to Kibana/OSD — does not need
    # the ES engine or cluster client to be running.
    from .elasticsearch_engine import _push_dashboards_http
    try:
        result = _push_dashboards_http(
            url=dashboards_url,
            auth=dashboards_auth,
            verify_tls=dashboards_verify_tls,
            ndjson_bytes=ndjson_bytes,
            overwrite=overwrite,
        )
    except RuntimeError as e:
        req._send_error(503, ErrorCode.SERVICE_UNAVAILABLE, str(e))
        return
    except Exception as e:
        req._send_error(500, ErrorCode.INTERNAL_ERROR, f'Dashboard push failed: {e}')
        return

    req._send_ok({
        'success': result.get('success', False),
        'errors': result.get('errors', []),
    })
