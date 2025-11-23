from __future__ import annotations

import time
import gzip
import json
from typing import Any, Dict, List, Optional

import httpx

from .models import Agent, ScanType


class AgentHTTPError(RuntimeError):
    pass


class AgentClient:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.base_url = agent.base_url.rstrip('/')
        self.headers = {}
        if agent.api_key:
            self.headers["X-API-Key"] = agent.api_key

    def _get_response(self, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", 60.0)
        response = httpx.get(url, headers=self.headers, timeout=timeout, **kwargs)
        if response.status_code >= 400:
            raise AgentHTTPError(f"Agent {self.agent.name} returned {response.status_code}: {response.text}")
        return response

    def _get(self, path: str, **kwargs) -> Dict[str, Any]:
        response = self._get_response(path, **kwargs)
        return response.json()

    def _post(self, path: str, data: Dict[str, Any] | None = None, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = httpx.post(url, json=data or {}, headers=self.headers, timeout=kwargs.get("timeout", 60.0))
        if response.status_code >= 400:
            raise AgentHTTPError(f"Agent {self.agent.name} returned {response.status_code}: {response.text}")
        return response.json()

    def get_interfaces(self) -> Dict[str, Any]:
        return self._get('/wireless/interfaces')

    def wifi_scan(self, interface: str, channels: Optional[List[int]] = None, progress_cb=None) -> Dict[str, Any]:
        if progress_cb:
            progress_cb({'stage': 'running', 'message': f'Scanning Wi-Fi on {interface}'})
        chan_str = ''
        if channels:
            chan_str = '?Frequencies=' + ','.join(str(ch) for ch in channels)
        path = f"/wireless/networks/{interface}{chan_str}"
        result = self._get(path)
        if progress_cb:
            progress_cb({'stage': 'collected', 'networks': len(result.get('networks', []))})
        return result

    def falcon_scan(
        self,
        interface: str,
        channels: Optional[List[int]] = None,
        progress_cb=None,
        poll_interval: float = 2.0,
        timeout: float = 90.0,
    ) -> Dict[str, Any]:
        # Start capture if not already running
        self._get(f"/falcon/startscan/{interface}")
        if channels:
            # The Falcon agent determines channels via config; channel hints are stored for UI only
            pass
        start_time = time.time()
        result = None
        while True:
            running_resp = self._get(f"/falcon/scanrunning/{interface}")
            is_running = running_resp.get('errcode') == 0
            snapshot = self._get('/falcon/getscanresults')
            result = snapshot
            if progress_cb:
                progress_cb(
                    {
                        'stage': 'running' if is_running else 'finalizing',
                        'running': is_running,
                        'status': running_resp,
                        'snapshot': snapshot,
                    }
                )
            if not is_running:
                break
            if (time.time() - start_time) > timeout:
                raise TimeoutError("Falcon scan timed out")
            time.sleep(poll_interval)

        return result

    def bluetooth_discovery(self, active: bool = True, duration: float = 5.0, progress_cb=None) -> Dict[str, Any]:
        if progress_cb:
            progress_cb({'stage': 'running', 'message': f"Bluetooth discovery ({'active' if active else 'passive'})"})
        if active:
            self._get('/bluetooth/discoverystarta')
        else:
            self._get('/bluetooth/discoverystartp')
        time.sleep(duration)
        status = self._get('/bluetooth/discoverystatus')
        if progress_cb:
            progress_cb({'stage': 'collected', 'devices': len(status.get('devices', [])) if isinstance(status, dict) else None})
        return status

    def bluetooth_clear(self) -> Dict[str, Any]:
        return self._get('/bluetooth/discoveryclear')

    def bluetooth_stop(self) -> Dict[str, Any]:
        return self._get('/bluetooth/discoverystop')

    def bluetooth_running(self) -> Dict[str, Any]:
        return self._get('/bluetooth/running')

    def falcon_start_monitor(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/startmonmode/{interface}')

    def falcon_stop_monitor(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/stopmonmode/{interface}')

    def falcon_scan_running(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/scanrunning/{interface}')

    def falcon_start_scan(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/startscan/{interface}')

    def falcon_stop_scan(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/stopscan/{interface}')

    def falcon_get_results(self) -> Dict[str, Any]:
        return self._get('/falcon/getscanresults')

    def falcon_deauth(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post('/falcon/deauth', payload)

    def falcon_stop_deauth(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post('/falcon/stopdeauth', payload)

    def falcon_stop_all_deauths(self, interface: str) -> Dict[str, Any]:
        return self._get(f'/falcon/stopalldeauths/{interface}')

    def falcon_get_deauths(self) -> Dict[str, Any]:
        return self._get('/falcon/getalldeauths')

    def falcon_start_crack(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post('/falcon/startcrack', payload)

    def gps_status(self) -> Dict[str, Any]:
        return self._get('/gps/status')

    def hackrf_status(self) -> Dict[str, Any]:
        return self._get('/spectrum/hackrfstatus')

    def hackrf_start(self, band: str) -> Dict[str, Any]:
        if band == '24':
            return self._get('/spectrum/scan/start24')
        if band == '5':
            return self._get('/spectrum/scan/start5')
        raise ValueError('Band must be "24" or "5"')

    def hackrf_stop(self) -> Dict[str, Any]:
        return self._get('/spectrum/scan/stop')

    def hackrf_channel_data(self) -> Dict[str, Any]:
        response = self._get_response('/spectrum/scan/status')
        content = response.content
        if response.headers.get('Content-Encoding', '').lower() == 'gzip':
            try:
                content = gzip.decompress(content)
            except OSError:
                # Some agents set the header but return plain JSON; fall back gracefully
                pass
        try:
            return json.loads(content.decode('utf-8'))
        except Exception as exc:
            raise AgentHTTPError(f"Invalid spectrum response from {self.agent.name}: {exc}") from exc


def execute_scan(
    agent: Agent,
    scan_type: ScanType,
    *,
    interface: str | None,
    channels: List[int] | None,
    extras: Dict[str, Any] | None,
    progress_cb=None,
) -> Dict[str, Any]:
    client = AgentClient(agent)
    if scan_type == ScanType.WIFI:
        if not interface:
            raise ValueError('Wi-Fi scans require an interface name')
        return client.wifi_scan(interface, channels, progress_cb=progress_cb)
    if scan_type == ScanType.FALCON:
        if not interface:
            raise ValueError('Falcon scans require an interface name')
        poll_interval = float(extras.get('poll_interval', 2.0)) if extras else 2.0
        timeout = float(extras.get('timeout', 90.0)) if extras else 90.0
        return client.falcon_scan(
            interface,
            channels,
            progress_cb=progress_cb,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    if scan_type == ScanType.BLUETOOTH:
        duration = float(extras.get('duration', 5.0)) if extras else 5.0
        active = bool(extras.get('active', True)) if extras else True
        return client.bluetooth_discovery(active=active, duration=duration, progress_cb=progress_cb)
    raise ValueError(f'Unsupported scan type: {scan_type}')
