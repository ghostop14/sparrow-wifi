from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

import httpx

from .config import get_settings
from .events import event_bus


class ElasticExporter:
    def __init__(self, endpoint: str, wifi_index: str, bt_index: str, timeout: float) -> None:
        self.endpoint = endpoint.rstrip('/')
        self.wifi_index = wifi_index
        self.bt_index = bt_index
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout)

    def handle_scan_completed(self, payload: Dict[str, Any]) -> None:
        scan_type = payload.get('scan_type')
        response = payload.get('response') or {}
        agent_id = payload.get('agent_id')
        agent_name = payload.get('agent_name')
        scan_id = payload.get('scan_id')

        docs: List[str] = []
        if scan_type == 'wifi':
            docs.extend(self._prepare_wifi_docs(response, agent_id, agent_name, scan_id))
            index = self.wifi_index
        elif scan_type == 'bluetooth':
            docs.extend(self._prepare_bluetooth_docs(response, agent_id, agent_name, scan_id))
            index = self.bt_index
        else:
            return

        if not docs:
            return
        try:
            self._bulk_index(index, docs)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[ElasticExporter] failed to index to {index}: {exc}")

    def _bulk_index(self, index: str, docs: List[Dict[str, Any]]) -> None:
        # Build NDJSON body
        lines = []
        for doc in docs:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(doc))
        body = "\n".join(lines) + "\n"
        url = f"{self.endpoint}/_bulk"
        resp = self.client.post(url, content=body, headers={"Content-Type": "application/x-ndjson"})
        resp.raise_for_status()
        result = resp.json()
        if result.get("errors"):
            print(f"[ElasticExporter] bulk errors for index {index}: {result}")

    def _prepare_wifi_docs(self, response: Dict[str, Any], agent_id: int, agent_name: str, scan_id: int) -> List[Dict[str, Any]]:
        networks = response.get('networks') or []
        docs: List[Dict[str, Any]] = []
        for net in networks:
            lat = _safe_float(net.get('lat') or net.get('latitude'))
            lon = _safe_float(net.get('lon') or net.get('longitude'))
            ts = _parse_ts(net.get('lastseen') or net.get('lastSeen') or net.get('firstseen') or net.get('firstSeen'))
            doc = {
                "@timestamp": ts,
                "agent": {"id": agent_id, "name": agent_name},
                "scan": {"id": scan_id, "type": "wifi"},
                "wifi": {
                    "ssid": net.get('ssid') or net.get('name'),
                    "mac_addr": net.get('macAddr') or net.get('bssid') or net.get('mac'),
                    "channel": net.get('channel'),
                    "frequency": _safe_float(net.get('frequency')),
                    "signal": _safe_float(net.get('signal') or net.get('power')),
                    "security": net.get('security'),
                    "privacy": net.get('privacy'),
                    "cipher": net.get('cipher'),
                    "mode": net.get('mode'),
                    "bandwidth": _safe_float(net.get('bandwidth')),
                },
            }
            if lat is not None and lon is not None:
                doc["wifi"]["geo"] = {"lat": lat, "lon": lon}
            docs.append(doc)
        return docs

    def _prepare_bluetooth_docs(self, response: Dict[str, Any], agent_id: int, agent_name: str, scan_id: int) -> List[Dict[str, Any]]:
        devices = response.get('devices') or []
        docs: List[Dict[str, Any]] = []
        for dev in devices:
            lat = _safe_float(dev.get('lat') or dev.get('latitude'))
            lon = _safe_float(dev.get('lon') or dev.get('longitude'))
            ts = _parse_ts(dev.get('lastseen') or dev.get('lastSeen') or dev.get('firstseen') or dev.get('firstSeen'))
            doc = {
                "@timestamp": ts,
                "agent": {"id": agent_id, "name": agent_name},
                "scan": {"id": scan_id, "type": "bluetooth"},
                "bluetooth": {
                    "mac_addr": dev.get('mac') or dev.get('macAddr'),
                    "name": dev.get('name'),
                    "rssi": _safe_float(dev.get('rssi') or dev.get('signal')),
                    "tx_power": _safe_float(dev.get('txpower') or dev.get('txPower')),
                    "type": dev.get('bttype') or dev.get('type'),
                },
            }
            if lat is not None and lon is not None:
                doc["bluetooth"]["geo"] = {"lat": lat, "lon": lon}
            docs.append(doc)
        return docs


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _parse_ts(value: Any) -> str:
    try:
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value).isoformat() + "Z"
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.isoformat()
    except Exception:
        pass
    return datetime.utcnow().isoformat() + "Z"


def setup_exporters() -> None:
    settings = get_settings()
    if settings.elastic_url:
        exporter = ElasticExporter(
            settings.elastic_url,
            settings.elastic_index_wifi,
            settings.elastic_index_bluetooth,
            settings.elastic_timeout_seconds,
        )
        event_bus.subscribe('scan.completed', exporter.handle_scan_completed)
