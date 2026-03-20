"""
WiFi SSID Drone Detection Scanner for Sparrow DroneID.

Polls a sparrow-wifi agent HTTP API for WiFi scan results, matches SSIDs
against configurable regex patterns, and creates DroneIDDevice entries that
flow through the existing alert/display pipeline.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import requests as _requests

from .models import DroneIDDevice, Protocol, UAType
from .database import Database

log = logging.getLogger(__name__)

# HTTP session shared across polls (connection reuse)
_session: _requests.Session = _requests.Session()
_session.headers.update({'User-Agent': 'SparrowDroneID-WifiScanner/1.0'})

# How long to wait for HTTP requests.
# Interface discovery is quick; network scans can take 10-15 seconds
# as the agent may trigger a live channel sweep.
_HTTP_TIMEOUT_DISCOVERY = 8
_HTTP_TIMEOUT_SCAN = 20


class WifiSsidScanner:
    """Poll a sparrow-wifi agent for SSIDs and match them against drone patterns.

    Thread safety:
      - _patterns, _enabled, _agent_url, _poll_interval are written under
        _config_lock and read by the poll loop.
      - _known_macs is accessed only from the poll loop thread — no lock needed.
      - on_detection is called from the poll loop thread; the alert engine
        uses its own internal locking so this is safe.
    """

    def __init__(self, db: Database, gps_engine=None,
                 on_detection: Optional[Callable] = None) -> None:
        self._db = db
        self._gps = gps_engine
        self.on_detection = on_detection

        self._config_lock = threading.Lock()
        self._enabled = False
        self._agent_url = 'http://127.0.0.1:8020'
        self._agent_interface = ''  # empty = auto-discover
        self._poll_interval = 20

        # Compiled patterns: list of (compiled_re, label)
        self._patterns: List[Tuple[re.Pattern, str]] = []

        # Background poll thread management
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Dedup: MACs seen in this scanner session — reset on configure(enabled=True)
        self._known_macs: set = set()

        # Diagnostics (written by poll loop, read by status endpoint)
        self._last_poll_time: Optional[float] = None
        self._last_poll_ok: bool = False
        self._last_data_time: Optional[float] = None  # last time we got actual network data
        self._match_count: int = 0

        # Cached interface name discovered from the agent
        self._cached_interface: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Configuration                                                        #
    # ------------------------------------------------------------------ #

    def configure(self, enabled: bool, agent_url: str,
                  poll_interval: int, agent_interface: str = '') -> None:
        """Configure and start/stop the scanner.

        When called with enabled=True the poll thread starts (or restarts if
        already running with different settings).  When called with
        enabled=False the thread is stopped.

        agent_interface: specific WiFi interface on the agent to scan.
            Empty string = auto-discover the first interface.
        """
        was_running = self._enabled and self._thread is not None and self._thread.is_alive()

        with self._config_lock:
            changed = (
                self._enabled != enabled
                or self._agent_url != agent_url
                or self._agent_interface != agent_interface
                or self._poll_interval != poll_interval
            )
            self._enabled = enabled
            self._agent_url = agent_url.rstrip('/')
            self._agent_interface = agent_interface.strip()
            self._poll_interval = max(5, int(poll_interval))

        if not changed and was_running and enabled:
            # No configuration change and thread is already alive — nothing to do.
            return

        # Stop existing thread regardless
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(3, self._poll_interval + 2))
        self._thread = None
        self._stop_event.clear()

        if enabled:
            # Reset session state on (re)start
            self._known_macs = set()
            self._cached_interface = None
            self._load_patterns()

            self._thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name='wifi-ssid-scanner',
            )
            self._thread.start()
            log.info("wifi_ssid_scanner: started — agent %s, interval %ds, %d patterns",
                     self._agent_url, self._poll_interval, len(self._patterns))
        else:
            log.info("wifi_ssid_scanner: stopped")

    def stop(self) -> None:
        """Stop the polling thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    # ------------------------------------------------------------------ #
    # Pattern management                                                   #
    # ------------------------------------------------------------------ #

    def _load_patterns(self) -> None:
        """Read patterns from DB setting and compile them."""
        raw = self._db.get_setting('wifi_ssid_patterns', '')
        patterns: List[Tuple[re.Pattern, str]] = []
        if raw:
            try:
                items = json.loads(raw)
                for item in items:
                    p = item.get('pattern', '')
                    label = item.get('label', '')
                    if p:
                        try:
                            patterns.append((re.compile(p), label))
                        except re.error as exc:
                            log.warning("wifi_ssid_scanner: invalid pattern %r skipped: %s", p, exc)
            except (json.JSONDecodeError, TypeError):
                log.warning("wifi_ssid_scanner: corrupt wifi_ssid_patterns in DB; using empty list")

        with self._config_lock:
            self._patterns = patterns

        log.debug("wifi_ssid_scanner: loaded %d patterns from DB", len(patterns))

    def reload_patterns(self) -> None:
        """Re-read patterns from DB (called after API update)."""
        self._load_patterns()
        log.info("wifi_ssid_scanner: patterns reloaded (%d patterns)", len(self._patterns))

    def get_patterns(self) -> List[dict]:
        """Return current patterns as a plain list of dicts for the API."""
        raw = self._db.get_setting('wifi_ssid_patterns', '')
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    # ------------------------------------------------------------------ #
    # Status                                                               #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        """Return scanner status for the API."""
        with self._config_lock:
            enabled = self._enabled
            agent_url = self._agent_url
            agent_interface = self._agent_interface
            poll_interval = self._poll_interval
            pattern_count = len(self._patterns)
        # Stale = connected but no actual data in 4× poll intervals
        stale_threshold = poll_interval * 4
        now = time.time()
        data_age = (now - self._last_data_time) if self._last_data_time else None
        stale = data_age is not None and data_age > stale_threshold

        return {
            'enabled': enabled,
            'agent_url': agent_url,
            'agent_interface': agent_interface or '(auto)',
            'active_interface': self._cached_interface or '',
            'poll_interval': poll_interval,
            'pattern_count': pattern_count,
            'last_poll_time': (
                datetime.utcfromtimestamp(self._last_poll_time).isoformat() + 'Z'
                if self._last_poll_time is not None else None
            ),
            'last_poll_ok': self._last_poll_ok,
            'last_data_age_s': round(data_age, 1) if data_age is not None else None,
            'stale': stale,
            'session_match_count': self._match_count,
            'running': self._thread is not None and self._thread.is_alive(),
        }

    # ------------------------------------------------------------------ #
    # Poll loop (background thread)                                        #
    # ------------------------------------------------------------------ #

    def _poll_loop(self) -> None:
        """Background thread: poll agent, match SSIDs, fire detections."""
        log.debug("wifi_ssid_scanner: poll loop started")
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("wifi_ssid_scanner: unhandled exception in poll loop")

            # Wait for next poll interval (interruptible by stop_event)
            with self._config_lock:
                interval = self._poll_interval
            self._stop_event.wait(timeout=interval)

        log.debug("wifi_ssid_scanner: poll loop exited")

    def _poll_once(self) -> None:
        """Execute one poll cycle: fetch networks and process matches."""
        with self._config_lock:
            agent_url = self._agent_url
            patterns = list(self._patterns)

        networks = self._poll_agent(agent_url)
        self._last_poll_time = time.time()

        if networks is None:
            self._last_poll_ok = False
            return

        self._last_poll_ok = True
        if len(networks) > 0:
            self._last_data_time = time.time()

        for net in networks:
            ssid = net.get('ssid', '') or ''
            if not ssid:
                continue
            mac = net.get('macAddr', '') or ''
            signal = net.get('signal', 0)
            channel = net.get('channel', 6)

            label = self._match_ssid(ssid, patterns)
            if label is None:
                continue

            if mac in self._known_macs:
                # Already reported this session — update last_seen/rssi through
                # the normal detection flow (the engine handles dedup in-memory).
                # We still call on_detection so the engine can update its state.
                pass

            self._known_macs.add(mac)
            self._match_count += 1

            device = self._build_device(ssid, mac, signal, channel, label)
            if self.on_detection and device is not None:
                try:
                    self.on_detection(device)
                except Exception:
                    log.exception("wifi_ssid_scanner: on_detection callback raised")

    # ------------------------------------------------------------------ #
    # Agent HTTP client                                                    #
    # ------------------------------------------------------------------ #

    def _poll_agent(self, agent_url: str) -> Optional[List[dict]]:
        """HTTP GET to sparrow-wifi agent.  Returns a list of network dicts or None on error."""
        # Use configured interface if set, otherwise auto-discover
        with self._config_lock:
            configured_iface = self._agent_interface

        if configured_iface:
            self._cached_interface = configured_iface
        elif self._cached_interface is None:
            self._cached_interface = self._discover_interface(agent_url)
            if self._cached_interface is None:
                log.warning("wifi_ssid_scanner: could not discover interface from %s", agent_url)
                return None

        iface = self._cached_interface
        url = f"{agent_url}/wireless/networks/{iface}"
        try:
            resp = _session.get(url, timeout=_HTTP_TIMEOUT_SCAN)
            resp.raise_for_status()
            data = resp.json()
        except _requests.exceptions.ConnectionError:
            log.warning("wifi_ssid_scanner: agent at %s is unreachable", agent_url)
            # Invalidate interface cache so we re-discover after agent comes back
            self._cached_interface = None
            return None
        except _requests.exceptions.Timeout:
            # Scan timeout is transient (agent busy, interface slow) — don't
            # flag as a connectivity error. Just skip this cycle.
            log.debug("wifi_ssid_scanner: scan timeout on %s, will retry next cycle", url)
            return []  # empty, not None — keeps last_poll_ok = True
        except Exception as exc:
            log.warning("wifi_ssid_scanner: error polling %s: %s", url, exc)
            self._cached_interface = None
            return None

        err_code = data.get('errCode', -1)
        if err_code == 240:
            # Interface busy (another client is scanning) — not an error,
            # just a request collision.  Skip this cycle silently.
            log.debug("wifi_ssid_scanner: agent interface busy (errCode 240), will retry next cycle")
            return []  # empty list, not None — keeps last_poll_ok = True
        if err_code != 0:
            log.warning("wifi_ssid_scanner: agent returned errCode %s: %s",
                        err_code, data.get('errString', ''))
            return None

        return data.get('networks', []) or []

    def _discover_interface(self, agent_url: str) -> Optional[str]:
        """GET /wireless/interfaces and return the first interface name."""
        url = f"{agent_url}/wireless/interfaces"
        try:
            resp = _session.get(url, timeout=_HTTP_TIMEOUT_DISCOVERY)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("wifi_ssid_scanner: could not fetch interfaces from %s: %s", url, exc)
            return None

        interfaces = data.get('interfaces', []) or []
        if not interfaces:
            log.warning("wifi_ssid_scanner: agent at %s reports no interfaces", agent_url)
            return None

        # The interface list may be plain strings or dicts with a 'name' key
        first = interfaces[0]
        if isinstance(first, dict):
            iface = first.get('name', '') or first.get('interface', '')
        else:
            iface = str(first)

        if not iface:
            log.warning("wifi_ssid_scanner: could not parse interface name from %r", first)
            return None

        log.info("wifi_ssid_scanner: discovered interface %r from agent", iface)
        return iface

    # ------------------------------------------------------------------ #
    # SSID matching                                                        #
    # ------------------------------------------------------------------ #

    def _match_ssid(self, ssid: str,
                    patterns: List[Tuple[re.Pattern, str]]) -> Optional[str]:
        """Check SSID against patterns.  Returns the label if matched, else None."""
        for compiled_re, label in patterns:
            if compiled_re.search(ssid):
                return label
        return None

    # ------------------------------------------------------------------ #
    # Device construction                                                  #
    # ------------------------------------------------------------------ #

    def _build_device(self, ssid: str, mac: str, signal: int,
                      channel: int, label: str) -> Optional[DroneIDDevice]:
        """Build a DroneIDDevice from a WiFi scan match."""
        now = datetime.utcnow().isoformat() + 'Z'

        # Place the drone at the receiver's position (no own GPS available)
        rx_lat, rx_lon, rx_alt = 0.0, 0.0, 0.0
        if self._gps:
            try:
                rx_lat, rx_lon, rx_alt = self._gps.get_receiver_position()
            except Exception:
                pass

        # Estimate frequency from channel (2.4 GHz band)
        if channel <= 14:
            freq = 2407 + channel * 5
        elif channel <= 64:
            freq = 5000 + channel * 5
        else:
            freq = 5000 + channel * 5

        device = DroneIDDevice(
            serial_number=ssid,         # Use SSID as the display key
            registration_id='',
            id_type=0,
            ua_type=UAType.NONE.value,
            drone_lat=rx_lat,
            drone_lon=rx_lon,
            drone_alt_geo=rx_alt,
            drone_alt_baro=0.0,
            drone_height_agl=0.0,
            speed=0.0,
            direction=0.0,
            vertical_speed=0.0,
            operator_lat=0.0,
            operator_lon=0.0,
            operator_alt=0.0,
            operator_id='',
            self_id_text=f"{label} [{ssid}]",
            auth_type=0,
            auth_data='',
            mac_address=mac,
            rssi=int(signal),
            channel=int(channel),
            frequency=freq,
            protocol=Protocol.WIFI_SSID.value,
            first_seen=now,
            last_seen=now,
        )
        return device
