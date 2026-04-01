"""
Elasticsearch / OpenSearch integration engine for Sparrow DroneID.

Indexes drone detections and alerts into Elasticsearch or OpenSearch as
ECS 8.17 compliant documents with a custom ``droneid.*`` namespace.

Architecture
------------
- :class:`SearchClient` — abstract base wrapping ES / OpenSearch client
  divergences (ILM vs ISM, API-key auth, dashboard push).
- :class:`DocumentBuilder` — pure-function ECS document construction from
  :class:`DroneIDDevice` and :class:`AlertEvent` models.
- :class:`BulkBuffer` — thread-safe bounded buffer with swap-and-flush
  semantics and drop-oldest back-pressure.
- :class:`ElasticsearchEngine` — top-level lifecycle (init / configure /
  start / stop / get_status) that owns a flush thread and coordinates
  bootstrap, bulk indexing, reconnection, and health probes.

This module is designed as a reference architecture for search-engine
integrations in the Sparrow product family.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    AlertEvent,
    DroneIDDevice,
    IdType,
    UAType,
    altitude_class,
    bearing,
    bearing_cardinal,
    drone_state,
    haversine,
)

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────

ECS_VERSION = "8.17.0"

# Buffer / back-pressure limits
_BUFFER_MAX = 10_000
_MAX_HELD_BATCHES = 50

# Reconnection
_MAX_BACKOFF_S = 60.0
_INITIAL_BACKOFF_S = 1.0
_HEALTH_PROBE_INTERVAL_S = 30.0
_HEARTBEAT_INTERVAL_S = 60.0

# BVLOS threshold (meters) — FAA advisory: 400 m visual line-of-sight
_BVLOS_THRESHOLD_M = 400.0

# Suppress urllib3 InsecureRequestWarning once globally when any client
# disables TLS verification, rather than on every client creation.
_URLLIB3_WARNINGS_SUPPRESSED = False


def _suppress_urllib3_warnings() -> None:
    """Suppress InsecureRequestWarning once (idempotent)."""
    global _URLLIB3_WARNINGS_SUPPRESSED
    if not _URLLIB3_WARNINGS_SUPPRESSED:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _URLLIB3_WARNINGS_SUPPRESSED = True


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 with Z suffix.

    This is the canonical timestamp format for all ES documents.
    Kibana/OSD interprets Z-suffixed timestamps as UTC and converts
    to the user's local timezone for display.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string to a timezone-aware UTC datetime.

    Handles both ``Z`` suffix and ``+00:00`` offset.  Returns a
    timezone-aware datetime so arithmetic is always unambiguous.
    Falls back to ``datetime.now(timezone.utc)`` on parse failure.
    """
    if not ts:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# Search Client Abstraction
# ═══════════════════════════════════════════════════════════════════════════

class SearchClient(ABC):
    """Thin abstraction over the Elasticsearch / OpenSearch Python clients.

    Subclasses handle import-time differences, ILM vs ISM, and auth
    mechanisms so that :class:`ElasticsearchEngine` never touches the
    concrete client library directly.
    """

    @abstractmethod
    def ping(self) -> bool:
        """Return *True* if the cluster responds to a lightweight probe."""

    @abstractmethod
    def cluster_info(self) -> Dict:
        """Return cluster info from ``GET /`` (name, version, tagline)."""

    @abstractmethod
    def bulk(self, actions: Sequence[Dict]) -> Tuple[int, List[Dict]]:
        """Execute a bulk index request.

        Returns ``(success_count, error_items)`` where each error item is
        a dict with at least ``{"_id": ..., "error": ...}``.
        """

    @abstractmethod
    def put_index_template(self, name: str, body: Dict) -> None:
        """Create or update a composable index template."""

    @abstractmethod
    def create_initial_index(self, index_name: str, alias_name: str) -> bool:
        """Create the first backing index with a write alias.

        Returns *True* if the index was created, *False* if it (or the
        alias) already exists.  Raises on unexpected errors.
        """

    @abstractmethod
    def alias_exists(self, alias_name: str) -> bool:
        """Check whether *alias_name* exists on any index."""

    @abstractmethod
    def put_lifecycle_policy(self, name: str, body: Dict) -> None:
        """Create or update a lifecycle policy (ILM or ISM)."""

    @abstractmethod
    def get_lifecycle_policies(self) -> Dict[str, Dict]:
        """Return a dict of ``{policy_name: policy_body}``."""

    @abstractmethod
    def push_dashboards(self, url: str, auth: Dict, verify_tls: bool,
                        ndjson_bytes: bytes, overwrite: bool) -> Dict:
        """Push an NDJSON saved-objects payload to Kibana / OSD.

        *url* is the base Kibana/OSD URL.  Returns the import response.
        """

    @abstractmethod
    def close(self) -> None:
        """Release underlying transport resources."""


class ElasticsearchClient(SearchClient):
    """Concrete client backed by ``elasticsearch-py >= 8``."""

    def __init__(self, url: str, auth_method: str, username: str,
                 password: str, api_key: str, verify_tls: bool):
        from elasticsearch import Elasticsearch, helpers as es_helpers
        self._helpers = es_helpers

        kwargs: Dict[str, Any] = {
            "hosts": [url],
            "verify_certs": verify_tls,
            "request_timeout": 30,
        }
        if not verify_tls:
            _suppress_urllib3_warnings()
            kwargs["ssl_show_warn"] = False

        if auth_method == "basic" and username:
            kwargs["basic_auth"] = (username, password)
        elif auth_method == "apikey" and api_key:
            kwargs["api_key"] = api_key

        self._client = Elasticsearch(**kwargs)

    # -- Probes ------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False

    def cluster_info(self) -> Dict:
        resp = self._client.info()
        raw = resp.body if hasattr(resp, 'body') else resp
        import json
        return json.loads(json.dumps(dict(raw), default=str))

    # -- Bulk --------------------------------------------------------------

    def bulk(self, actions: Sequence[Dict]) -> Tuple[int, List[Dict]]:
        success, errors = self._helpers.bulk(
            self._client, actions,
            raise_on_error=False, raise_on_exception=False,
        )
        return success, errors if isinstance(errors, list) else []

    # -- Index templates ---------------------------------------------------

    def put_index_template(self, name: str, body: Dict) -> None:
        self._client.indices.put_index_template(name=name, body=body)

    def create_initial_index(self, index_name: str, alias_name: str) -> bool:
        try:
            self._client.indices.create(
                index=index_name,
                body={"aliases": {alias_name: {"is_write_index": True}}},
            )
            return True
        except Exception as exc:
            if _is_resource_exists(exc):
                return False
            raise

    def alias_exists(self, alias_name: str) -> bool:
        try:
            return self._client.indices.exists_alias(name=alias_name)
        except Exception:
            return False

    # -- Lifecycle ---------------------------------------------------------

    def put_lifecycle_policy(self, name: str, body: Dict) -> None:
        self._client.ilm.put_lifecycle(name=name, policy=body)

    def get_lifecycle_policies(self) -> Dict[str, Dict]:
        try:
            resp = self._client.ilm.get_lifecycle()
            # elasticsearch-py 8.x returns ObjectApiResponse; coerce to plain dict.
            # The response is {policy_name: {version, modified_date, policy: {...}}, ...}
            if hasattr(resp, 'body'):
                raw = resp.body
            elif hasattr(resp, 'keys'):
                raw = resp
            else:
                raw = {}
            # Deep-coerce to plain dicts (ObjectApiResponse nests can cause
            # JSON serialization issues)
            import json
            return json.loads(json.dumps(dict(raw), default=str))
        except Exception:
            return {}

    # -- Dashboards --------------------------------------------------------

    def push_dashboards(self, url: str, auth: Dict, verify_tls: bool,
                        ndjson_bytes: bytes, overwrite: bool) -> Dict:
        return _push_dashboards_http(url, auth, verify_tls,
                                     ndjson_bytes, overwrite)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


class OpenSearchClient(SearchClient):
    """Concrete client backed by ``opensearch-py >= 2``."""

    def __init__(self, url: str, auth_method: str, username: str,
                 password: str, verify_tls: bool):
        from opensearchpy import OpenSearch, helpers as os_helpers
        self._helpers = os_helpers

        kwargs: Dict[str, Any] = {
            "hosts": [url],
            "verify_certs": verify_tls,
            "timeout": 30,
        }
        if not verify_tls:
            _suppress_urllib3_warnings()
            kwargs["ssl_show_warn"] = False

        if auth_method == "basic" and username:
            kwargs["http_auth"] = (username, password)

        self._client = OpenSearch(**kwargs)

    def ping(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False

    def cluster_info(self) -> Dict:
        resp = self._client.info()
        import json
        return json.loads(json.dumps(dict(resp), default=str))

    def bulk(self, actions: Sequence[Dict]) -> Tuple[int, List[Dict]]:
        success, errors = self._helpers.bulk(
            self._client, actions,
            raise_on_error=False, raise_on_exception=False,
        )
        return success, errors if isinstance(errors, list) else []

    def put_index_template(self, name: str, body: Dict) -> None:
        self._client.indices.put_index_template(name=name, body=body)

    def create_initial_index(self, index_name: str, alias_name: str) -> bool:
        try:
            self._client.indices.create(
                index=index_name,
                body={"aliases": {alias_name: {"is_write_index": True}}},
            )
            return True
        except Exception as exc:
            if _is_resource_exists(exc):
                return False
            raise

    def alias_exists(self, alias_name: str) -> bool:
        try:
            return self._client.indices.exists_alias(name=alias_name)
        except Exception:
            return False

    def put_lifecycle_policy(self, name: str, body: Dict) -> None:
        # OpenSearch uses ISM (Index State Management) instead of ILM.
        self._client.transport.perform_request(
            "PUT",
            f"/_plugins/_ism/policies/{name}",
            body=body,
        )

    def get_lifecycle_policies(self) -> Dict[str, Dict]:
        try:
            resp = self._client.transport.perform_request(
                "GET", "/_plugins/_ism/policies",
            )
            # opensearch-py may return the body directly or wrap it
            if hasattr(resp, 'body'):
                resp = resp.body
            elif isinstance(resp, str):
                import json as _json
                resp = _json.loads(resp)
            policies = resp.get("policies", [])
            return {p.get("_id", p.get("policy_id", "unknown")): p.get("policy", {}) for p in policies}
        except Exception as exc:
            logger.debug("OpenSearch ISM policy query failed: %s", exc)
            return {}

    def push_dashboards(self, url: str, auth: Dict, verify_tls: bool,
                        ndjson_bytes: bytes, overwrite: bool) -> Dict:
        return _push_dashboards_http(url, auth, verify_tls,
                                     ndjson_bytes, overwrite)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


# ─── Shared helpers for client implementations ───────────────────────────

def _is_resource_exists(exc: Exception) -> bool:
    """Return *True* if *exc* represents a 400/409 resource-already-exists."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (400, 409):
        msg = str(exc).lower()
        return "resource_already_exists" in msg or "already exists" in msg
    return False


def _push_dashboards_http(url: str, auth: Dict, verify_tls: bool,
                          ndjson_bytes: bytes, overwrite: bool) -> Dict:
    import json
    """POST saved-objects NDJSON to Kibana / OpenSearch Dashboards."""
    import requests

    endpoint = f"{url.rstrip('/')}/api/saved_objects/_import"
    if overwrite:
        endpoint += "?overwrite=true"

    headers = {"kbn-xsrf": "true"}  # Required by both Kibana and OSD
    if auth.get("method") == "basic":
        req_auth = (auth.get("username", ""), auth.get("password", ""))
    else:
        req_auth = None
    if auth.get("method") == "apikey" and auth.get("api_key"):
        headers["Authorization"] = f"ApiKey {auth['api_key']}"

    resp = requests.post(
        endpoint,
        headers=headers,
        auth=req_auth,
        files={"file": ("dashboards.ndjson", ndjson_bytes, "application/ndjson")},
        verify=verify_tls,
        timeout=30,
    )
    if resp.status_code >= 400:
        # Bulk import failed — try importing one object at a time to
        # identify which one is causing the error.
        lines = [l for l in ndjson_bytes.decode("utf-8").splitlines() if l.strip()]
        failed_objects = []
        succeeded = 0
        for line in lines:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            single_resp = requests.post(
                endpoint,
                headers=headers,
                auth=req_auth,
                files={"file": ("single.ndjson", (line + "\n").encode("utf-8"),
                                "application/ndjson")},
                verify=verify_tls,
                timeout=15,
            )
            if single_resp.status_code >= 400:
                try:
                    err_detail = single_resp.json()
                except Exception:
                    err_detail = single_resp.text[:200]
                failed_objects.append({
                    "id": obj.get("id", "?"),
                    "type": obj.get("type", "?"),
                    "status": single_resp.status_code,
                    "error": err_detail,
                })
            else:
                # Check for per-object errors in successful response
                try:
                    result = single_resp.json()
                    for err in result.get("errors", []):
                        failed_objects.append({
                            "id": err.get("id", obj.get("id", "?")),
                            "type": err.get("type", obj.get("type", "?")),
                            "error": err.get("error", {}),
                        })
                    if not result.get("errors"):
                        succeeded += 1
                except Exception:
                    succeeded += 1
        if failed_objects:
            raise RuntimeError(
                f"{succeeded} objects imported, {len(failed_objects)} failed: "
                f"{json.dumps(failed_objects, default=str)}")
        # All objects imported individually even though bulk failed
        return {"success": True, "successCount": succeeded}
    return resp.json()


def _create_search_client(backend_type: str, url: str, auth_method: str,
                          username: str, password: str, api_key: str,
                          verify_tls: bool) -> SearchClient:
    """Factory that returns the correct :class:`SearchClient` subclass."""
    if backend_type == "opensearch":
        return OpenSearchClient(
            url=url, auth_method=auth_method, username=username,
            password=password, verify_tls=verify_tls,
        )
    return ElasticsearchClient(
        url=url, auth_method=auth_method, username=username,
        password=password, api_key=api_key, verify_tls=verify_tls,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ECS Document Builder
# ═══════════════════════════════════════════════════════════════════════════

class DocumentBuilder:
    """Construct ECS 8.17 documents from DroneID detection data.

    This class is stateless — all methods are pure functions that receive
    their inputs as arguments.  It is safe to call from any thread.
    """

    @staticmethod
    def build_detection(device: DroneIDDevice,
                        receiver_lat: float,
                        receiver_lon: float,
                        receiver_alt: float,
                        observer_name: str,
                        observer_hostname: str,
                        rssi_trend_value: str = "stable",
                        vendor: str = "",
                        time_in_area_s: int = 0,
                        ) -> Dict[str, Any]:
        """Build a full ECS detection document."""
        now_iso = _utc_now_iso()
        ts = device.last_seen or now_iso

        has_drone_pos = device.drone_lat != 0.0 or device.drone_lon != 0.0
        has_operator_pos = device.operator_lat != 0.0 or device.operator_lon != 0.0
        has_receiver_pos = receiver_lat != 0.0 or receiver_lon != 0.0

        # Drone-to-operator geometry
        operator_distance_m = None
        bvlos = True  # Default: BVLOS if no operator position
        if has_drone_pos and has_operator_pos:
            operator_distance_m = round(
                haversine(device.drone_lat, device.drone_lon,
                          device.operator_lat, device.operator_lon), 1)
            bvlos = operator_distance_m > _BVLOS_THRESHOLD_M

        # Receiver-relative ranges
        drone_range = None
        drone_bearing = None
        drone_bearing_card = None
        operator_range = None
        operator_bearing_deg = None
        operator_bearing_card = None

        if has_receiver_pos and has_drone_pos:
            drone_range = round(haversine(receiver_lat, receiver_lon,
                                          device.drone_lat, device.drone_lon), 1)
            _b = bearing(receiver_lat, receiver_lon,
                         device.drone_lat, device.drone_lon)
            drone_bearing = round(_b, 1)
            drone_bearing_card = bearing_cardinal(_b)

        if has_receiver_pos and has_operator_pos:
            operator_range = round(haversine(receiver_lat, receiver_lon,
                                             device.operator_lat,
                                             device.operator_lon), 1)
            _ob = bearing(receiver_lat, receiver_lon,
                          device.operator_lat, device.operator_lon)
            operator_bearing_deg = round(_ob, 1)
            operator_bearing_card = bearing_cardinal(_ob)

        alt_class = altitude_class(device.drone_height_agl).value
        state = drone_state(device.last_seen).value

        # Severity: simple heuristic (0-100) combining proximity, altitude, BVLOS
        severity = _compute_severity(
            drone_range, alt_class, bvlos, device.speed, time_in_area_s)

        doc: Dict[str, Any] = {
            # ── ECS Core (REQUIRED) ──
            "@timestamp": ts,
            "ecs": {"version": ECS_VERSION},

            # ── ECS Event ──
            "event": {
                "kind": "event",
                "category": ["intrusion_detection"],
                "type": ["info"],
                "module": "sparrow-droneid",
                "dataset": "sparrow_droneid.detection",
                "action": "drone-detected",
                "severity": severity,
                "duration": time_in_area_s * 1_000_000_000,  # nanoseconds
                "created": now_iso,
            },

            # ── ECS Observer (the Sparrow sensor) ──
            "observer": {
                "type": "sensor",
                "vendor": "Sparrow",
                "product": "DroneID",
                "name": observer_name,
                "hostname": observer_hostname,
            },

            # ── ECS Source (the drone) ──
            "source": {
                "mac": device.mac_address,
            },

            # ── ECS Related ──
            "related": {
                "hosts": [device.serial_number] if device.serial_number else [],
                "user": [device.operator_id] if device.operator_id else [],
            },

            # ── Custom: droneid.* ──
            "droneid": {
                "serial_number": device.serial_number,
                "registration_id": device.registration_id,
                "id_type": device.id_type,
                "id_type_name": _safe_id_type_name(device.id_type),
                "ua_type": device.ua_type,
                "ua_type_name": _safe_ua_type_name(device.ua_type),
                "vendor": vendor,
                "protocol": device.protocol,

                "drone": {
                    "lat": device.drone_lat,
                    "lon": device.drone_lon,
                    "alt_geo": device.drone_alt_geo,
                    "alt_baro": device.drone_alt_baro,
                    "height_agl": device.drone_height_agl,
                    "altitude_class": alt_class,
                    "speed": device.speed,
                    "direction": device.direction,
                    "vertical_speed": device.vertical_speed,
                },

                "operator": {
                    "lat": device.operator_lat,
                    "lon": device.operator_lon,
                    "alt": device.operator_alt,
                    "id": device.operator_id,
                    "self_id_text": device.self_id_text,
                    "distance_m": operator_distance_m,
                    "bvlos": bvlos,
                },

                "rf": {
                    "rssi": device.rssi,
                    "rssi_trend": rssi_trend_value,
                    "mac_address": device.mac_address,
                    "channel": device.channel,
                    "frequency": device.frequency,
                },

                "range": {
                    "drone_m": drone_range,
                    "drone_bearing_deg": drone_bearing,
                    "drone_bearing_cardinal": drone_bearing_card,
                    "operator_m": operator_range,
                    "operator_bearing_deg": operator_bearing_deg,
                    "operator_bearing_cardinal": operator_bearing_card,
                },

                "state": state,
                "first_seen": device.first_seen,
                "last_seen": device.last_seen,
                "time_in_area_s": time_in_area_s,
            },
        }

        # Geo-point fields (null-coerce sentinels)
        if has_drone_pos:
            doc["source"]["geo"] = {
                "location": {"lat": device.drone_lat, "lon": device.drone_lon},
            }
        if has_receiver_pos:
            doc["observer"]["geo"] = {
                "location": {"lat": receiver_lat, "lon": receiver_lon},
                "altitude": receiver_alt,
            }
        if has_operator_pos:
            doc["droneid"]["operator"]["location"] = {
                "lat": device.operator_lat, "lon": device.operator_lon,
            }

        return doc

    @staticmethod
    def build_alert(alert: AlertEvent,
                    device: Optional[DroneIDDevice],
                    receiver_lat: float,
                    receiver_lon: float,
                    receiver_alt: float,
                    observer_name: str,
                    observer_hostname: str,
                    ) -> Dict[str, Any]:
        """Build an ECS alert document."""
        now_iso = _utc_now_iso()
        ts = alert.timestamp or now_iso

        has_receiver_pos = receiver_lat != 0.0 or receiver_lon != 0.0

        doc: Dict[str, Any] = {
            "@timestamp": ts,
            "ecs": {"version": ECS_VERSION},
            "event": {
                "kind": "alert",
                "category": ["intrusion_detection"],
                "type": ["indicator"],
                "module": "sparrow-droneid",
                "dataset": "sparrow_droneid.alert",
                "action": "drone-alert",
                "severity": 75,
                "created": now_iso,
            },
            "observer": {
                "type": "sensor",
                "vendor": "Sparrow",
                "product": "DroneID",
                "name": observer_name,
                "hostname": observer_hostname,
            },
            "related": {
                "hosts": [alert.serial_number] if alert.serial_number else [],
            },
            "droneid": {
                "serial_number": alert.serial_number,
                "alert": {
                    "type": alert.alert_type,
                    "detail": alert.detail,
                    "state": "ACTIVE",
                },
            },
        }

        if has_receiver_pos:
            doc["observer"]["geo"] = {
                "location": {"lat": receiver_lat, "lon": receiver_lon},
                "altitude": receiver_alt,
            }

        has_alert_pos = alert.drone_lat != 0.0 or alert.drone_lon != 0.0
        if has_alert_pos:
            doc["source"] = {
                "geo": {
                    "location": {"lat": alert.drone_lat, "lon": alert.drone_lon},
                },
            }

        return doc

    @staticmethod
    def build_heartbeat(receiver_lat: float,
                        receiver_lon: float,
                        receiver_alt: float,
                        observer_name: str,
                        observer_hostname: str,
                        heartbeat_data: Dict[str, Any],
                        ) -> Dict[str, Any]:
        """Build an ECS heartbeat/metric document.

        *heartbeat_data* is a dict from the app layer containing live
        sensor state: active_drones, monitoring, interface, uptime_s, etc.
        """
        now_iso = _utc_now_iso()
        has_receiver_pos = receiver_lat != 0.0 or receiver_lon != 0.0

        doc: Dict[str, Any] = {
            "@timestamp": now_iso,
            "ecs": {"version": ECS_VERSION},
            "event": {
                "kind": "metric",
                "category": ["host"],
                "type": ["info"],
                "module": "sparrow-droneid",
                "dataset": "sparrow_droneid.heartbeat",
                "action": "sensor-heartbeat",
                "created": now_iso,
            },
            "observer": {
                "type": "sensor",
                "vendor": "Sparrow",
                "product": "DroneID",
                "name": observer_name,
                "hostname": observer_hostname,
            },
            "droneid": {
                "heartbeat": {
                    "active_drones": heartbeat_data.get("active_drones", 0),
                    "monitoring": heartbeat_data.get("monitoring", False),
                    "interface": heartbeat_data.get("interface", ""),
                    "uptime_s": heartbeat_data.get("uptime_s", 0),
                    "frame_count": heartbeat_data.get("frame_count", 0),
                    "gps_fix": heartbeat_data.get("gps_fix", False),
                },
            },
        }

        if has_receiver_pos:
            doc["observer"]["geo"] = {
                "location": {"lat": receiver_lat, "lon": receiver_lon},
                "altitude": receiver_alt,
            }

        return doc

    @staticmethod
    def compute_doc_id(serial_number: str, observer_name: str,
                       timestamp_str: str) -> str:
        """Deterministic ``_id`` for retry idempotency.

        Uses epoch milliseconds (not the raw ISO string) so timezone
        formatting differences never produce duplicate IDs.
        """
        dt = _parse_iso_utc(timestamp_str)
        epoch_ms = int(dt.timestamp() * 1000)

        raw = f"{serial_number}:{observer_name}:{epoch_ms}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─── Severity heuristic ──────────────────────────────────────────────────

def _compute_severity(range_m: Optional[float], alt_class: str,
                      bvlos: bool, speed: float,
                      dwell_s: int) -> int:
    """Simple combined severity score 0-100."""
    score = 0

    # Proximity (closer = higher severity)
    if range_m is not None:
        if range_m < 200:
            score += 30
        elif range_m < 500:
            score += 20
        elif range_m < 1000:
            score += 10

    # Altitude class
    alt_scores = {"ILLEGAL": 25, "HIGH": 15, "MEDIUM": 10, "LOW": 5, "GROUND": 0}
    score += alt_scores.get(alt_class, 0)

    # BVLOS
    if bvlos:
        score += 15

    # Speed (fast = higher concern)
    if speed > 30:
        score += 10
    elif speed > 15:
        score += 5

    # Dwell (loitering)
    if dwell_s > 600:
        score += 20
    elif dwell_s > 300:
        score += 10
    elif dwell_s > 60:
        score += 5

    return min(score, 100)


def _safe_id_type_name(id_type: int) -> str:
    try:
        return IdType(id_type).display_name
    except ValueError:
        return "Unknown"


def _safe_ua_type_name(ua_type: int) -> str:
    try:
        return UAType(ua_type).display_name
    except ValueError:
        return "Unknown"


# ═══════════════════════════════════════════════════════════════════════════
# Bulk Buffer
# ═══════════════════════════════════════════════════════════════════════════

class BulkBuffer:
    """Thread-safe bounded buffer with drop-oldest back-pressure.

    :meth:`append` is called from detection threads (WiFi / BLE).
    :meth:`swap` is called from the flush thread.  The lock is held only
    for the pointer swap — never during the HTTP call.
    """

    def __init__(self, max_size: int = _BUFFER_MAX) -> None:
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=max_size)
        self._docs_dropped: int = 0

    def append(self, action: Dict) -> None:
        """Append a bulk action dict.  Drops oldest if at capacity."""
        with self._lock:
            if len(self._buffer) == self._buffer.maxlen:
                self._buffer.popleft()
                self._docs_dropped += 1
                if self._docs_dropped % 100 == 1:
                    logger.warning(
                        "ES buffer full, dropping oldest event "
                        "(total dropped: %d)", self._docs_dropped)
            self._buffer.append(action)

    def swap(self) -> List[Dict]:
        """Atomically drain the buffer and return its contents."""
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
            return items

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def docs_dropped(self) -> int:
        with self._lock:
            return self._docs_dropped


# ═══════════════════════════════════════════════════════════════════════════
# Index Template
# ═══════════════════════════════════════════════════════════════════════════

def build_index_template(prefix: str, shards: int, replicas: int,
                         ilm_policy: str,
                         backend_type: str = "elasticsearch") -> Dict:
    """Return a composable index template body for the ECS + droneid mapping."""
    settings: Dict[str, Any] = {
        "number_of_shards": shards,
        "number_of_replicas": replicas,
    }
    # ILM settings only apply to Elasticsearch. OpenSearch ISM attaches
    # policies via ism_template in the policy body, not index settings.
    if ilm_policy and backend_type != "opensearch":
        settings["index.lifecycle.name"] = ilm_policy
        settings["index.lifecycle.rollover_alias"] = prefix

    return {
        "index_patterns": [f"{prefix}-*"],
        "priority": 200,
        "template": {
            "settings": settings,
            "mappings": {
                "dynamic": "false",
                "properties": {
                    "@timestamp": {"type": "date"},
                    "ecs": {
                        "properties": {
                            "version": {"type": "keyword"},
                        },
                    },
                    "event": {
                        "properties": {
                            "kind": {"type": "keyword"},
                            "category": {"type": "keyword"},
                            "type": {"type": "keyword"},
                            "module": {"type": "keyword"},
                            "dataset": {"type": "keyword"},
                            "action": {"type": "keyword"},
                            "severity": {"type": "long"},
                            "duration": {"type": "long"},
                            "created": {"type": "date"},
                        },
                    },
                    "observer": {
                        "properties": {
                            "type": {"type": "keyword"},
                            "vendor": {"type": "keyword"},
                            "product": {"type": "keyword"},
                            "name": {"type": "keyword"},
                            "hostname": {"type": "keyword"},
                            "geo": {
                                "properties": {
                                    "location": {"type": "geo_point"},
                                    "altitude": {"type": "float"},
                                },
                            },
                        },
                    },
                    "source": {
                        "properties": {
                            "mac": {"type": "keyword"},
                            "geo": {
                                "properties": {
                                    "location": {"type": "geo_point"},
                                },
                            },
                        },
                    },
                    "related": {
                        "properties": {
                            "hosts": {"type": "keyword"},
                            "user": {"type": "keyword"},
                        },
                    },
                    "droneid": {
                        "properties": {
                            "serial_number": {"type": "keyword"},
                            "registration_id": {"type": "keyword"},
                            "id_type": {"type": "integer"},
                            "id_type_name": {"type": "keyword"},
                            "ua_type": {"type": "integer"},
                            "ua_type_name": {"type": "keyword"},
                            "vendor": {"type": "keyword"},
                            "protocol": {"type": "keyword"},
                            "drone": {
                                "properties": {
                                    "lat": {"type": "float"},
                                    "lon": {"type": "float"},
                                    "alt_geo": {"type": "float"},
                                    "alt_baro": {"type": "float"},
                                    "height_agl": {"type": "float"},
                                    "altitude_class": {"type": "keyword"},
                                    "speed": {"type": "float"},
                                    "direction": {"type": "float"},
                                    "vertical_speed": {"type": "float"},
                                },
                            },
                            "operator": {
                                "properties": {
                                    "lat": {"type": "float"},
                                    "lon": {"type": "float"},
                                    "alt": {"type": "float"},
                                    "location": {"type": "geo_point"},
                                    "id": {"type": "keyword"},
                                    "self_id_text": {"type": "text",
                                                     "fields": {
                                                         "keyword": {"type": "keyword",
                                                                     "ignore_above": 256},
                                                     }},
                                    "distance_m": {"type": "float"},
                                    "bvlos": {"type": "boolean"},
                                },
                            },
                            "rf": {
                                "properties": {
                                    "rssi": {"type": "integer"},
                                    "rssi_trend": {"type": "keyword"},
                                    "mac_address": {"type": "keyword"},
                                    "channel": {"type": "integer"},
                                    "frequency": {"type": "integer"},
                                },
                            },
                            "range": {
                                "properties": {
                                    "drone_m": {"type": "float"},
                                    "drone_bearing_deg": {"type": "float"},
                                    "drone_bearing_cardinal": {"type": "keyword"},
                                    "operator_m": {"type": "float"},
                                    "operator_bearing_deg": {"type": "float"},
                                    "operator_bearing_cardinal": {"type": "keyword"},
                                },
                            },
                            "state": {"type": "keyword"},
                            "first_seen": {"type": "date"},
                            "last_seen": {"type": "date"},
                            "time_in_area_s": {"type": "integer"},
                            "alert": {
                                "properties": {
                                    "type": {"type": "keyword"},
                                    "state": {"type": "keyword"},
                                    "detail": {"type": "text"},
                                    "acknowledged_by": {"type": "keyword"},
                                    "acknowledged_at": {"type": "date"},
                                },
                            },
                            "heartbeat": {
                                "properties": {
                                    "active_drones": {"type": "integer"},
                                    "monitoring": {"type": "boolean"},
                                    "interface": {"type": "keyword"},
                                    "uptime_s": {"type": "long"},
                                    "frame_count": {"type": "long"},
                                    "gps_fix": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def build_default_ilm_policy(hot_days: int = 7, warm_days: int = 30,
                             delete_days: int = 90) -> Dict:
    """Return an ILM policy body with configurable phase durations.

    Phases: hot (active writes + rollover) → warm (read-only + force-merge)
    → delete (purge).
    """
    return {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {
                        "max_age": f"{hot_days}d",
                        "max_primary_shard_size": "50gb",
                    },
                },
            },
            "warm": {
                "min_age": f"{warm_days}d",
                "actions": {
                    "readonly": {},
                    "forcemerge": {
                        "max_num_segments": 1,
                    },
                },
            },
            "delete": {
                "min_age": f"{delete_days}d",
                "actions": {
                    "delete": {},
                },
            },
        },
    }


def build_default_ism_policy(prefix: str, hot_days: int = 7,
                             warm_days: int = 30,
                             delete_days: int = 90) -> Dict:
    """Return an ISM policy body for OpenSearch with configurable durations.

    States: hot (active writes + rollover) → warm (read-only + force-merge)
    → delete (purge).
    """
    return {
        "policy": {
            "description": f"Sparrow DroneID lifecycle policy for {prefix}",
            "default_state": "hot",
            "states": [
                {
                    "name": "hot",
                    "actions": [
                        {
                            "rollover": {
                                "min_index_age": f"{hot_days}d",
                                "min_primary_shard_size": "50gb",
                            },
                        },
                    ],
                    "transitions": [
                        {"state_name": "warm",
                         "conditions": {"min_index_age": f"{warm_days}d"}},
                    ],
                },
                {
                    "name": "warm",
                    "actions": [
                        {"read_only": {}},
                        {"force_merge": {"max_num_segments": 1}},
                    ],
                    "transitions": [
                        {"state_name": "delete",
                         "conditions": {"min_index_age": f"{delete_days}d"}},
                    ],
                },
                {
                    "name": "delete",
                    "actions": [{"delete": {}}],
                    "transitions": [],
                },
            ],
            "ism_template": {
                "index_patterns": [f"{prefix}-*"],
                "priority": 200,
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════════════════

class EngineStatus:
    """Mutable status counters, protected by a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.docs_indexed: int = 0
        self.docs_failed: int = 0
        self.docs_dropped: int = 0
        self.last_flush_time: str = ""
        self.last_error: str = ""
        self.last_error_time: str = ""

    def record_flush(self, indexed: int, failed: int) -> None:
        with self._lock:
            self.docs_indexed += indexed
            self.docs_failed += failed
            self.last_flush_time = (
                _utc_now_iso())

    def record_error(self, error: str) -> None:
        with self._lock:
            self.last_error = error
            self.last_error_time = (
                _utc_now_iso())

    def sync_dropped(self, buffer_dropped: int) -> None:
        with self._lock:
            self.docs_dropped = buffer_dropped

    def to_dict(self) -> Dict:
        with self._lock:
            return {
                "docs_indexed": self.docs_indexed,
                "docs_failed": self.docs_failed,
                "docs_dropped": self.docs_dropped,
                "last_flush_time": self.last_flush_time,
                "last_error": self.last_error,
                "last_error_time": self.last_error_time,
            }


class ElasticsearchEngine:
    """Top-level engine: lifecycle management, flush thread, bootstrap.

    Usage::

        engine = ElasticsearchEngine()
        engine.configure(enabled=True, backend_type="elasticsearch", ...)
        # engine.start() is called by configure() when enabled.
        # ...
        engine.add_detection(device, rx_lat, rx_lon, rx_alt, ...)
        # ...
        engine.stop()
    """

    def __init__(self) -> None:
        # Configuration (set via configure())
        self._enabled: bool = False
        self._backend_type: str = "elasticsearch"
        self._url: str = ""
        self._auth_method: str = "none"
        self._username: str = ""
        self._password: str = ""
        self._api_key: str = ""
        self._verify_tls: bool = True
        self._agent_name: str = ""
        self._index_prefix: str = "sparrow-droneid"
        self._shards: int = 2
        self._replicas: int = 0
        self._ilm_policy: str = ""
        self._bulk_size: int = 100
        self._flush_interval: int = 5

        # Runtime state
        self._client: Optional[SearchClient] = None
        self._buffer = BulkBuffer()
        self._held_batches: deque = deque(maxlen=_MAX_HELD_BATCHES)
        self._held_lock = threading.Lock()  # protects _held_batches
        self._status = EngineStatus()
        self._hostname: str = socket.gethostname()

        self._bootstrap_needed: bool = True
        self._healthy: bool = False
        self._backoff: float = _INITIAL_BACKOFF_S
        self._last_health_probe: float = 0.0
        self._last_heartbeat: float = 0.0

        self._flush_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._config_lock = threading.Lock()

        # Optional callback that returns heartbeat data dict.
        # Signature: get_heartbeat_data() -> dict with keys:
        #   active_drones, monitoring, interface, uptime_s, frame_count, gps_fix,
        #   receiver_lat, receiver_lon, receiver_alt
        self.get_heartbeat_data = None

    # ─── Configuration / Lifecycle ────────────────────────────────────────

    def configure(self, *,
                  enabled: bool = False,
                  backend_type: str = "elasticsearch",
                  url: str = "",
                  auth_method: str = "none",
                  username: str = "",
                  password: str = "",
                  api_key: str = "",
                  verify_tls: bool = True,
                  agent_name: str = "",
                  index_prefix: str = "sparrow-droneid",
                  shards: int = 2,
                  replicas: int = 0,
                  ilm_policy: str = "",
                  bulk_size: int = 100,
                  flush_interval: int = 5,
                  ) -> None:
        """Apply new configuration.

        Handles all transitions: enable, disable, reconfigure.
        Connection-affecting changes trigger a full stop→start cycle.
        """
        with self._config_lock:
            was_enabled = self._enabled
            needs_restart = (
                enabled and was_enabled and (
                    url != self._url
                    or backend_type != self._backend_type
                    or auth_method != self._auth_method
                    or username != self._username
                    or password != self._password
                    or api_key != self._api_key
                    or verify_tls != self._verify_tls
                    or index_prefix != self._index_prefix
                )
            )

            # Store new config
            self._enabled = enabled
            self._backend_type = backend_type
            self._url = url
            self._auth_method = auth_method
            self._username = username
            self._password = password
            self._api_key = api_key
            self._verify_tls = verify_tls
            self._agent_name = agent_name
            self._index_prefix = index_prefix
            self._shards = shards
            self._replicas = replicas
            self._ilm_policy = ilm_policy
            self._bulk_size = bulk_size
            self._flush_interval = flush_interval

        # Lifecycle transitions (outside config lock)
        if not enabled:
            if was_enabled:
                self.stop()
            return

        if needs_restart:
            self.stop()
            self.start()
        elif not was_enabled:
            self.start()

    def start(self) -> None:
        """Start the flush thread if configuration is valid."""
        validation_error = self._validate_config()
        if validation_error:
            logger.warning("ES engine not starting: %s", validation_error)
            self._status.record_error(f"not_configured: {validation_error}")
            return

        # Create client
        try:
            self._client = _create_search_client(
                backend_type=self._backend_type,
                url=self._url,
                auth_method=self._auth_method,
                username=self._username,
                password=self._password,
                api_key=self._api_key,
                verify_tls=self._verify_tls,
            )
        except Exception as exc:
            logger.warning("ES client creation failed: %s", exc)
            self._status.record_error(f"client_error: {exc}")
            return

        self._bootstrap_needed = True
        self._healthy = False
        self._backoff = _INITIAL_BACKOFF_S
        self._stop_event.clear()

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="es-flush")
        self._flush_thread.start()
        logger.info("ES engine started (backend=%s, prefix=%s)",
                     self._backend_type, self._index_prefix)

    def stop(self) -> None:
        """Stop the flush thread, best-effort flush, release client."""
        self._stop_event.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=5.0)
            self._flush_thread = None

        # Best-effort final flush (attempt if bootstrap completed, even if
        # last flush failed — the cluster may be reachable right now)
        if self._client is not None and not self._bootstrap_needed:
            remaining = self._buffer.swap()
            if remaining:
                try:
                    self._do_flush(remaining)
                except Exception:
                    pass

        if self._client is not None:
            self._client.close()
            self._client = None

        self._healthy = False
        logger.info("ES engine stopped")

    def _validate_config(self) -> Optional[str]:
        """Return an error string if config is invalid, else None."""
        if not self._url or not self._url.strip():
            return "Cluster URL required"
        if "://" not in self._url:
            return "Cluster URL must include scheme (http:// or https://)"
        if self._backend_type not in ("elasticsearch", "opensearch"):
            return f"Unknown backend type: {self._backend_type}"
        # Soft warnings (log but don't block)
        if self._auth_method == "basic" and not self._username:
            logger.warning("ES: Basic auth selected but username is empty")
        if self._auth_method == "apikey" and not self._api_key:
            logger.warning("ES: API Key auth selected but key is empty")
        if (self._backend_type == "opensearch"
                and self._auth_method == "apikey"):
            logger.warning(
                "ES: API Key auth not supported by OpenSearch — "
                "falling back to no auth")
        return None

    @property
    def _held_batch_count(self) -> int:
        with self._held_lock:
            return len(self._held_batches)

    # ─── Public Event API ─────────────────────────────────────────────────

    def add_detection(self, device: DroneIDDevice,
                      receiver_lat: float, receiver_lon: float,
                      receiver_alt: float,
                      rssi_trend_value: str = "stable",
                      vendor: str = "",
                      time_in_area_s: int = 0) -> None:
        """Enqueue a detection for bulk indexing.  Thread-safe, never raises."""
        if not self._enabled or self._client is None:
            return
        try:
            doc = DocumentBuilder.build_detection(
                device=device,
                receiver_lat=receiver_lat,
                receiver_lon=receiver_lon,
                receiver_alt=receiver_alt,
                observer_name=self._agent_name or self._hostname,
                observer_hostname=self._hostname,
                rssi_trend_value=rssi_trend_value,
                vendor=vendor,
                time_in_area_s=time_in_area_s,
            )
            doc_id = DocumentBuilder.compute_doc_id(
                device.serial_number,
                self._agent_name or self._hostname,
                device.last_seen,
            )
            action = {
                "_index": self._index_prefix,
                "_id": doc_id,
                "_source": doc,
            }
            self._buffer.append(action)
        except Exception as exc:
            logger.debug("ES add_detection error: %s", exc)

    def add_alert(self, alert: AlertEvent,
                  device: Optional[DroneIDDevice],
                  receiver_lat: float, receiver_lon: float,
                  receiver_alt: float) -> None:
        """Enqueue an alert for bulk indexing.  Thread-safe, never raises."""
        if not self._enabled or self._client is None:
            return
        try:
            doc = DocumentBuilder.build_alert(
                alert=alert,
                device=device,
                receiver_lat=receiver_lat,
                receiver_lon=receiver_lon,
                receiver_alt=receiver_alt,
                observer_name=self._agent_name or self._hostname,
                observer_hostname=self._hostname,
            )
            # Alerts get a unique _id (alert_id + observer + timestamp)
            doc_id = DocumentBuilder.compute_doc_id(
                f"alert-{alert.id}-{alert.serial_number}",
                self._agent_name or self._hostname,
                alert.timestamp,
            )
            action = {
                "_index": self._index_prefix,
                "_id": doc_id,
                "_source": doc,
            }
            self._buffer.append(action)
        except Exception as exc:
            logger.debug("ES add_alert error: %s", exc)

    # ─── Status ───────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """Return engine status for the /api/v1/es/status endpoint."""
        self._status.sync_dropped(self._buffer.docs_dropped)
        status = self._status.to_dict()
        status.update({
            "enabled": self._enabled,
            "connected": self._client is not None and self._healthy,
            "healthy": self._healthy,
            "backend_type": self._backend_type,
            "docs_in_buffer": self._buffer.depth,
            "held_batches": self._held_batch_count,
            "backoff_seconds": round(self._backoff, 1),
            "bootstrap_complete": not self._bootstrap_needed,
        })
        return status

    # ─── Flush Thread ─────────────────────────────────────────────────────

    def _flush_loop(self) -> None:
        """Persistent flush thread: bootstrap → flush → health probe."""
        while not self._stop_event.is_set():
            try:
                self._flush_tick()
            except Exception as exc:
                logger.warning("ES flush tick error: %s", exc)
                self._status.record_error(str(exc))

            # Sleep: use flush_interval or backoff, whichever is longer
            sleep_time = max(self._flush_interval, self._backoff)
            self._stop_event.wait(timeout=sleep_time)

    def _flush_tick(self) -> None:
        """Single iteration of the flush loop."""
        if self._client is None:
            return

        # ── Bootstrap (retry until success) ──
        if self._bootstrap_needed:
            if self._attempt_bootstrap():
                self._bootstrap_needed = False
                self._backoff = _INITIAL_BACKOFF_S
                logger.info("ES bootstrap complete (prefix=%s)",
                             self._index_prefix)
            else:
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
                return  # Can't flush without bootstrap

        # ── Periodic heartbeat ──
        self._maybe_emit_heartbeat()

        # ── Flush buffer ──
        batch = self._buffer.swap()
        if not batch and not self._held_batches:
            # Nothing to flush — run a health probe if it's time
            self._maybe_health_probe()
            return

        # Prepend any held batches (oldest first)
        with self._held_lock:
            has_held = len(self._held_batches) > 0
            if has_held:
                all_actions = []
                while self._held_batches:
                    all_actions.extend(self._held_batches.popleft())
                all_actions.extend(batch)
                batch = all_actions

        # ── Execute bulk ──
        try:
            success, errors = self._do_flush(batch)
            self._status.record_flush(success, len(errors))
            self._healthy = True
            self._backoff = _INITIAL_BACKOFF_S

            if errors:
                # Check for 404 (index deleted externally)
                for err in errors:
                    if _is_404_error(err):
                        logger.warning("ES index not found — re-bootstrapping")
                        self._bootstrap_needed = True
                        break

            if self._held_batches:
                logger.info("ES cluster recovered, flushing held batches")

        except Exception as exc:
            # Hold the batch for retry
            self._healthy = False
            self._status.record_error(str(exc))

            with self._held_lock:
                if len(self._held_batches) >= _MAX_HELD_BATCHES:
                    dropped = self._held_batches.popleft()
                    logger.warning(
                        "ES max held batches reached, dropping oldest "
                        "(%d events)", len(dropped))
                self._held_batches.append(batch)
                held_count = len(self._held_batches)

            self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
            logger.warning(
                "ES cluster unreachable, holding %d batches (backoff: %.0fs): %s",
                held_count, self._backoff, exc)

    def _do_flush(self, actions: List[Dict]) -> Tuple[int, List[Dict]]:
        """Execute bulk indexing.  Raises on connection failure."""
        if not actions:
            return 0, []
        return self._client.bulk(actions)

    def _maybe_health_probe(self) -> None:
        """Periodic ping to detect cluster recovery."""
        now = monotonic()
        if now - self._last_health_probe < _HEALTH_PROBE_INTERVAL_S:
            return
        self._last_health_probe = now

        try:
            alive = self._client.ping()
            if alive and not self._healthy:
                logger.info("ES cluster is reachable")
            self._healthy = alive
        except Exception:
            self._healthy = False

    def _maybe_emit_heartbeat(self) -> None:
        """Emit a sensor heartbeat document every 60 seconds."""
        now = monotonic()
        if now - self._last_heartbeat < _HEARTBEAT_INTERVAL_S:
            return
        self._last_heartbeat = now

        if not self.get_heartbeat_data:
            return
        try:
            data = self.get_heartbeat_data()
            doc = DocumentBuilder.build_heartbeat(
                receiver_lat=data.get("receiver_lat", 0.0),
                receiver_lon=data.get("receiver_lon", 0.0),
                receiver_alt=data.get("receiver_alt", 0.0),
                observer_name=self._agent_name or self._hostname,
                observer_hostname=self._hostname,
                heartbeat_data=data,
            )
            # Heartbeat _id: observer + minute-bucket (one per sensor per minute)
            ts = _utc_now_iso()
            doc_id = DocumentBuilder.compute_doc_id(
                "heartbeat", self._agent_name or self._hostname, ts)
            self._buffer.append({
                "_index": self._index_prefix,
                "_id": doc_id,
                "_source": doc,
            })
        except Exception as exc:
            logger.debug("ES heartbeat error: %s", exc)

    # ─── Bootstrap ────────────────────────────────────────────────────────

    def _attempt_bootstrap(self) -> bool:
        """Run the idempotent bootstrap sequence.  Returns True on success."""
        prefix = self._index_prefix
        try:
            # 1. Index template
            template = build_index_template(
                prefix=prefix,
                shards=self._shards,
                replicas=self._replicas,
                ilm_policy=self._ilm_policy,
                backend_type=self._backend_type,
            )
            self._client.put_index_template(name=prefix, body=template)
            logger.debug("ES index template '%s' applied", prefix)

            # 2. Lifecycle policy (create default if none configured)
            if self._ilm_policy:
                try:
                    existing = self._client.get_lifecycle_policies()
                    if self._ilm_policy not in existing:
                        if self._backend_type == "opensearch":
                            body = build_default_ism_policy(prefix)
                        else:
                            body = build_default_ilm_policy()
                        self._client.put_lifecycle_policy(
                            self._ilm_policy, body)
                        logger.info("ES lifecycle policy '%s' created",
                                     self._ilm_policy)
                except Exception as exc:
                    logger.warning(
                        "ES lifecycle policy setup failed (non-fatal): %s", exc)

            # 3. Initial rollover index + write alias
            alias_name = prefix
            if not self._client.alias_exists(alias_name):
                first_index = f"{prefix}-000001"
                created = self._client.create_initial_index(
                    first_index, alias_name)
                if created:
                    logger.info("ES initial index '%s' with alias '%s' created",
                                 first_index, alias_name)
                else:
                    logger.debug("ES alias '%s' already exists (another sensor "
                                  "bootstrapped first)", alias_name)

            return True

        except Exception as exc:
            error_msg = str(exc)
            # Distinguish permission errors for clearer logging
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status == 403:
                logger.warning(
                    "ES bootstrap failed — insufficient permissions: %s. "
                    "Check that the configured user has manage_index_templates "
                    "and manage_ilm privileges.", error_msg)
            else:
                logger.warning("ES bootstrap failed (will retry): %s",
                                error_msg)
            self._status.record_error(f"bootstrap_failed: {error_msg}")
            return False

    # ─── Dashboard Push (called from API handler) ─────────────────────────

    def push_dashboards(self, dashboards_url: str,
                        dashboards_auth: Dict,
                        dashboards_verify_tls: bool,
                        ndjson_bytes: bytes,
                        overwrite: bool) -> Dict:
        """Push dashboard NDJSON to Kibana / OSD.

        Called from the API handler — not the flush thread.
        """
        if self._client is None:
            raise RuntimeError("ES engine not running")
        return self._client.push_dashboards(
            url=dashboards_url,
            auth=dashboards_auth,
            verify_tls=dashboards_verify_tls,
            ndjson_bytes=ndjson_bytes,
            overwrite=overwrite,
        )

    # ─── Lifecycle Policy Query (called from API handler) ─────────────────

    def get_lifecycle_policies(self) -> Dict[str, Dict]:
        """Return available ILM/ISM policies.  Returns {} on failure."""
        if self._client is None:
            return {}
        try:
            return self._client.get_lifecycle_policies()
        except Exception:
            return {}

    def create_lifecycle_policy(self, name: str,
                               hot_days: int = 7,
                               warm_days: int = 30,
                               delete_days: int = 90) -> Dict:
        """Create a lifecycle policy with the given *name* and phase durations.

        Returns ``{"ok": True}`` on success, ``{"ok": False, "error": ...}``
        on failure.
        """
        if self._client is None:
            return {"ok": False, "error": "Engine not running"}
        try:
            if self._backend_type == "opensearch":
                body = build_default_ism_policy(
                    self._index_prefix, hot_days, warm_days, delete_days)
            else:
                body = build_default_ilm_policy(hot_days, warm_days, delete_days)
            self._client.put_lifecycle_policy(name, body)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ─── Ad-hoc Cluster Queries (before engine is fully running) ──────────

    @staticmethod
    def query_lifecycle_policies(backend_type: str, url: str,
                                 auth_method: str, username: str,
                                 password: str, api_key: str,
                                 verify_tls: bool) -> Dict[str, Dict]:
        """Query ILM/ISM policies using an ad-hoc temporary client.

        This works even when the engine isn't started — the user is still
        filling in settings and wants to see available policies.
        """
        client = None
        try:
            client = _create_search_client(
                backend_type=backend_type, url=url,
                auth_method=auth_method, username=username,
                password=password, api_key=api_key,
                verify_tls=verify_tls,
            )
            return client.get_lifecycle_policies()
        except Exception:
            return {}
        finally:
            if client is not None:
                client.close()

    # ─── Test Connection (called from API handler) ────────────────────────

    def test_connection(self) -> Dict:
        """Test the cluster connection by fetching cluster info.

        Returns cluster name and version on success, or a descriptive
        error on failure.  Unlike a bare ``ping()``, this validates that
        the endpoint is actually an Elasticsearch / OpenSearch cluster.
        """
        if self._client is None:
            return {"ok": False, "error": "Engine not running"}
        try:
            info = self._client.cluster_info()
            if not info:
                return {"ok": False,
                        "error": "No response — check URL scheme (http vs https)"}
            # Validate we got a real ES/OpenSearch response
            version = info.get("version", {})
            cluster_name = info.get("cluster_name") or info.get("name", "")
            version_number = version.get("number", "") if isinstance(version, dict) else ""
            tagline = info.get("tagline", "")

            if not version_number:
                return {"ok": False,
                        "error": "Response missing version — is this an "
                                 "Elasticsearch / OpenSearch cluster?"}

            return {
                "ok": True,
                "cluster_name": cluster_name,
                "version": version_number,
                "tagline": tagline,
            }
        except Exception as exc:
            error_msg = str(exc)
            # Provide helpful hints for common misconfigurations
            if "SSL" in error_msg or "CERTIFICATE" in error_msg.upper():
                error_msg += " — try disabling TLS verification or switching to http://"
            elif "ConnectionError" in error_msg or "Connection refused" in error_msg:
                error_msg += " — check URL and port"
            return {"ok": False, "error": error_msg}


def _is_404_error(error_item: Dict) -> bool:
    """Check if a bulk error item indicates a missing index (404)."""
    if isinstance(error_item, dict):
        for action_type in ("index", "create", "update"):
            info = error_item.get(action_type, {})
            if info.get("status") == 404:
                return True
    return False
