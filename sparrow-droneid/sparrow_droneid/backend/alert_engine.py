"""
Alert evaluation and dispatch engine for Sparrow DroneID.

Evaluates incoming drone detections against configurable rules and dispatches
alert events to the database, pending queue, optional external scripts, and
optional Slack webhook notifications.
"""
import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone


def _utcnow_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
from typing import Dict, List, Optional, Tuple

import requests as _requests

from .models import (AlertEvent, AlertRule, AlertType, DEFAULT_ALERT_RULES,
                     DroneIDDevice, Protocol, UAType,
                     haversine, bearing, bearing_cardinal)
from .database import Database

log = logging.getLogger(__name__)

# Friendly protocol names for operator-facing messages
_PROTOCOL_DISPLAY = {
    Protocol.ASTM_BLE.value: "Bluetooth",
    Protocol.ASTM_NAN.value: "WiFi NAN",
    Protocol.ASTM_BEACON.value: "WiFi Beacon",
    Protocol.DJI_PROPRIETARY.value: "WiFi (DJI)",
    Protocol.FRENCH.value: "French RemoteID",
    Protocol.WIFI_SSID.value: "WiFi SSID",
}



class AlertEngine:
    """Evaluates drone detections against alert rules and dispatches events.

    Thread-safe: _pending_alerts and _known_serials are protected by
    _lock.  _alerted_lost is accessed only from the periodic
    check_signal_lost() caller and does not require separate locking
    provided that caller is single-threaded (e.g. one background timer).
    """

    def __init__(self, db: Database, gps_engine=None) -> None:
        self._db = db
        self._gps = gps_engine
        self._lock = threading.Lock()

        # Set of drone keys (serial / registration / MAC) seen at least once.
        # Used to differentiate "new drone" from a returning one.
        self._known_serials: set = set()

        # Alerts fired for signal-lost but not yet cleared by reappearance.
        self._alerted_lost: set = set()

        # Per-drone, per-rule-type violation dedup: key -> set of alert type strings.
        # Prevents altitude/speed alerts from firing on every frame while condition persists.
        self._alerted_violations: Dict[str, set] = {}

        # Alerts waiting to be consumed by the frontend polling endpoint.
        self._pending_alerts: List[dict] = []

        # Optional callback for external consumers (e.g. Elasticsearch engine).
        # Signature: on_alert(alert_event: AlertEvent, device: DroneIDDevice)
        self.on_alert = None

        # Deferred new-drone alerts: wait a few seconds for more frames to
        # arrive so the alert has position, altitude, vendor, etc.
        # key -> (first_seen_monotonic, latest_device)
        self._pending_new: Dict[str, Tuple[float, DroneIDDevice]] = {}
        _NEW_DRONE_DELAY = 4.0  # seconds
        self._new_drone_delay = _NEW_DRONE_DELAY

        self._load_config()
        self._load_vendor_codes()

    # ------------------------------------------------------------------ #
    # Configuration                                                        #
    # ------------------------------------------------------------------ #

    def _load_config(self) -> None:
        """Read all alert configuration from DB settings (no lock needed —
        called from __init__ and reload_config before public access)."""
        raw_rules = self._db.get_setting('alert_rules', '')
        if raw_rules:
            try:
                rule_dicts = json.loads(raw_rules)
                self._rules: List[AlertRule] = [AlertRule.from_dict(r) for r in rule_dicts]
            except (json.JSONDecodeError, TypeError):
                log.warning("alert_engine: corrupt alert_rules in DB; using defaults")
                self._rules = list(DEFAULT_ALERT_RULES)
        else:
            self._rules = list(DEFAULT_ALERT_RULES)

        def _bool(key: str, default: bool) -> bool:
            val = self._db.get_setting(key, str(default).lower())
            return val.lower() in ('1', 'true', 'yes')

        self._audio_enabled: bool  = _bool('alert_audio_enabled',  True)
        self._visual_enabled: bool = _bool('alert_visual_enabled', True)
        self._script_enabled: bool = _bool('alert_script_enabled', False)
        self._script_path: str     = self._db.get_setting('alert_script_path', '') or ''

        # When False, drones tagged Friendly do not fire alerts. Default True
        # so nothing changes until the operator opts into the suppression.
        self._friendly_alerts_enabled: bool = _bool('alert_friendly_enabled', True)

        # Slack webhook notifications
        self._slack_enabled: bool    = _bool('alert_slack_enabled', False)
        self._slack_webhook_url: str = self._db.get_setting('alert_slack_webhook_url', '') or ''
        self._slack_display_name: str = self._db.get_setting('alert_slack_display_name', 'Sparrow DroneID') or 'Sparrow DroneID'

        # API-based alert delivery: ECS-shaped POST to a generic alert
        # ingest endpoint. base_url is the full root including any path
        # prefix (e.g. https://host:port/some/api/root); the notifier
        # appends /v1/alerts and /v1/alerts/verify.
        self._api_enabled: bool      = _bool('alert_api_enabled', False)
        self._api_base_url: str      = self._db.get_setting('alert_api_base_url', '') or ''
        self._api_domain: str        = self._db.get_setting('alert_api_domain', '') or ''
        self._api_token: str         = self._db.get_setting('alert_api_token', '') or ''
        self._api_verify_tls: bool   = _bool('alert_api_verify_tls', True)
        # operator_name flows into the observer.name field on outbound
        # alerts; falls back to a generic label if not configured.
        self._operator_name: str     = self._db.get_setting('operator_name', '') or ''

    def reload_config(self) -> None:
        """Re-read alert configuration from the database."""
        self._load_config()

    # ------------------------------------------------------------------ #
    # Vendor codes                                                         #
    # ------------------------------------------------------------------ #

    def _load_vendor_codes(self) -> None:
        """Read vendor identification tables from DB settings.

        _serial_prefixes: dict mapping 4-char CTA-2063-A prefix → manufacturer name.
        _mac_oui:         dict mapping uppercase OUI string (e.g. '60:60:1F') → name.
        """
        raw_serial = self._db.get_setting('vendor_serial_prefixes', '')
        if raw_serial:
            try:
                self._serial_prefixes: Dict[str, str] = json.loads(raw_serial)
            except (json.JSONDecodeError, TypeError):
                log.warning("alert_engine: corrupt vendor_serial_prefixes in DB; using empty table")
                self._serial_prefixes = {}
        else:
            self._serial_prefixes = {}

        raw_oui = self._db.get_setting('vendor_mac_oui', '')
        if raw_oui:
            try:
                self._mac_oui: Dict[str, str] = json.loads(raw_oui)
            except (json.JSONDecodeError, TypeError):
                log.warning("alert_engine: corrupt vendor_mac_oui in DB; using empty table")
                self._mac_oui = {}
        else:
            self._mac_oui = {}

    def reload_vendor_codes(self) -> None:
        """Re-read vendor codes from the database (called after updates via API)."""
        self._load_vendor_codes()
        log.info("alert_engine: vendor codes reloaded (%d serial prefixes, %d OUIs)",
                 len(self._serial_prefixes), len(self._mac_oui))

    def get_vendor_codes(self) -> dict:
        """Return current vendor code tables as a plain dict."""
        return {
            'serial_prefixes': dict(self._serial_prefixes),
            'mac_oui': dict(self._mac_oui),
        }

    def resolve_vendor(self, serial: str = '', mac: str = '',
                       protocol: str = '') -> str:
        """Look up manufacturer from protocol, serial prefix, or MAC OUI."""
        if protocol == Protocol.DJI_PROPRIETARY.value:
            return 'DJI'
        for prefix, mfr in self._serial_prefixes.items():
            if serial.startswith(prefix):
                return mfr
        mac_upper = mac.upper()
        for oui, mfr in self._mac_oui.items():
            if mac_upper.startswith(oui.upper()):
                return mfr
        return ''

    def get_config(self) -> dict:
        """Return current alert configuration as a plain dict."""
        return {
            'rules': [r.to_dict() for r in self._rules],
            'audio_enabled':  self._audio_enabled,
            'visual_enabled': self._visual_enabled,
            'script_enabled': self._script_enabled,
            'script_path':    self._script_path,
            'friendly_alerts_enabled': self._friendly_alerts_enabled,
            'slack_enabled':      self._slack_enabled,
            'slack_webhook_url':  self._slack_webhook_url,
            'slack_display_name': self._slack_display_name,
        }

    def set_config(self, config: dict) -> None:
        """Persist updated alert configuration to DB and reload in-memory state."""
        if 'rules' in config:
            self._db.set_setting('alert_rules', json.dumps(config['rules']))
        if 'audio_enabled' in config:
            self._db.set_setting('alert_audio_enabled', str(config['audio_enabled']).lower())
        if 'visual_enabled' in config:
            self._db.set_setting('alert_visual_enabled', str(config['visual_enabled']).lower())
        if 'script_enabled' in config:
            self._db.set_setting('alert_script_enabled', str(config['script_enabled']).lower())
        if 'script_path' in config:
            self._db.set_setting('alert_script_path', config['script_path'])
        if 'friendly_alerts_enabled' in config:
            self._db.set_setting('alert_friendly_enabled', str(config['friendly_alerts_enabled']).lower())
        if 'slack_enabled' in config:
            self._db.set_setting('alert_slack_enabled', str(config['slack_enabled']).lower())
        if 'slack_webhook_url' in config:
            self._db.set_setting('alert_slack_webhook_url', config['slack_webhook_url'])
        if 'slack_display_name' in config:
            self._db.set_setting('alert_slack_display_name', config['slack_display_name'])
        self.reload_config()

    # ------------------------------------------------------------------ #
    # Public evaluation interface                                          #
    # ------------------------------------------------------------------ #

    def evaluate(self, device: DroneIDDevice) -> None:
        """Evaluate a freshly-received detection against all enabled rules.

        Must be called from the detection ingestion path (one call per frame).
        """
        # BLE devices cycle through multiple BasicID serials; use MAC for
        # stable dedup so alerts fire once per physical device, not per serial.
        if device.protocol == 'astm_ble' and device.mac_address:
            key = device.mac_address
        else:
            key = device.get_key()
        if not key:
            return

        # Friendly-drone suppression. When the operator tags a drone Friendly
        # and disables friendly alerts, skip all rule evaluation so repeat
        # launches (e.g. SAR sorties) don't flood the alert panel. We still
        # register the key as known so re-enabling alerts later doesn't fire
        # a stale new_drone alert.
        if device.disposition == 'friendly' and not self._friendly_alerts_enabled:
            with self._lock:
                self._known_serials.add(key)
                self._pending_new.pop(key, None)
                self._alerted_lost.discard(key)
                self._alerted_violations.pop(key, None)
            return

        # Register a brand-new drone into the deferred-announce buffer BEFORE
        # evaluating the threshold rules. Doing it here (rather than inside the
        # new_drone rule branch) lets a co-occurring violation force the
        # identity alert to be emitted first — see _announce_new_drone_now() —
        # regardless of the order rules happen to be iterated in. Gated on the
        # new_drone rule being enabled, mirroring the original behavior where
        # registration only ran when that rule was active.
        new_drone_enabled = any(
            r.enabled and r.type == AlertType.NEW_DRONE.value for r in self._rules
        )
        if new_drone_enabled:
            with self._lock:
                if key not in self._known_serials:
                    self._known_serials.add(key)
                    # Defer: wait for more frames to fill in fields.
                    self._pending_new[key] = (time.monotonic(), device)
                elif key in self._pending_new:
                    # Update with latest (merged) device data.
                    self._pending_new[key] = (self._pending_new[key][0], device)

        for rule in self._rules:
            if not rule.enabled:
                continue

            rtype = rule.type

            if rtype == AlertType.NEW_DRONE.value:
                # Registration happened above; here we only flush pending
                # new-drone alerts whose deferral window has elapsed.
                self._flush_pending_new()

            elif rtype == AlertType.ALTITUDE_MAX.value:
                max_alt = rule.params.get('max_altitude_m', 122.0)
                if device.drone_height_agl > max_alt:
                    with self._lock:
                        violations = self._alerted_violations.setdefault(key, set())
                        already = AlertType.ALTITUDE_MAX.value in violations
                        if not already:
                            violations.add(AlertType.ALTITUDE_MAX.value)
                    if not already:
                        # Identity before condition: emit a still-pending
                        # new_drone for this drone first so the operator sees
                        # "new drone" ahead of its altitude violation.
                        self._announce_new_drone_now(key, device)
                        self._fire_alert(
                            AlertType.ALTITUDE_MAX.value, device,
                            f"AGL {device.drone_height_agl:.1f} m exceeds limit {max_alt} m",
                        )
                else:
                    # Condition cleared — allow alert to fire again if it recurs.
                    was_violated = False
                    with self._lock:
                        violations = self._alerted_violations.get(key, set())
                        was_violated = AlertType.ALTITUDE_MAX.value in violations
                        violations.discard(AlertType.ALTITUDE_MAX.value)
                    if was_violated:
                        self._auto_resolve(AlertType.ALTITUDE_MAX.value, key)

            elif rtype == AlertType.SPEED_MAX.value:
                max_spd = rule.params.get('max_speed_mps', 44.7)
                if device.speed > max_spd:
                    with self._lock:
                        violations = self._alerted_violations.setdefault(key, set())
                        already = AlertType.SPEED_MAX.value in violations
                        if not already:
                            violations.add(AlertType.SPEED_MAX.value)
                    if not already:
                        # Identity before condition (see altitude branch).
                        self._announce_new_drone_now(key, device)
                        self._fire_alert(
                            AlertType.SPEED_MAX.value, device,
                            f"Speed {device.speed:.1f} m/s exceeds limit {max_spd} m/s",
                        )
                else:
                    # Condition cleared — allow alert to fire again if it recurs.
                    was_violated = False
                    with self._lock:
                        violations = self._alerted_violations.get(key, set())
                        was_violated = AlertType.SPEED_MAX.value in violations
                        violations.discard(AlertType.SPEED_MAX.value)
                    if was_violated:
                        self._auto_resolve(AlertType.SPEED_MAX.value, key)

            # SIGNAL_LOST is not evaluated here; handled by check_signal_lost().

        # Mark drone as seen (clears any previously alerted-lost entry).
        was_lost = False
        with self._lock:
            was_lost = key in self._alerted_lost
            self._alerted_lost.discard(key)
        if was_lost:
            self._auto_resolve(AlertType.SIGNAL_LOST.value, key)

    def check_signal_lost(self, active_drones: Dict[str, DroneIDDevice]) -> None:
        """Periodically check for drones that have gone silent.

        Args:
            active_drones: Mapping of drone-key -> DroneIDDevice for every
                           drone the caller considers currently tracked.
        """
        # Find the signal_lost rule (there should be at most one).
        lost_rule: Optional[AlertRule] = None
        for rule in self._rules:
            if rule.enabled and rule.type == AlertType.SIGNAL_LOST.value:
                lost_rule = rule
                break

        if lost_rule is None:
            return

        timeout = float(lost_rule.params.get('timeout_seconds', 180))
        now = datetime.now(timezone.utc)

        for key, device in active_drones.items():
            if device.disposition == 'friendly' and not self._friendly_alerts_enabled:
                # Signal-lost is a nuisance for SAR/friendly ops; the operator
                # already knows the drone is theirs. Clear any stale state.
                with self._lock:
                    self._alerted_lost.discard(key)
                continue
            try:
                last_seen = datetime.fromisoformat(
                    device.last_seen.replace('Z', '+00:00')
                )
            except (ValueError, AttributeError):
                continue

            age = (now - last_seen).total_seconds()
            if age <= timeout:
                continue

            with self._lock:
                already_alerted = key in self._alerted_lost
                if not already_alerted:
                    self._alerted_lost.add(key)

            if not already_alerted:
                self._fire_alert(
                    AlertType.SIGNAL_LOST.value, device,
                    f"No signal for {int(age)} s (timeout {int(timeout)} s)",
                )

    def _announce_new_drone_now(self, key: str, device: DroneIDDevice) -> None:
        """Force-emit a still-pending new_drone alert for ``key`` immediately,
        bypassing the deferral window, so a co-occurring threshold alert never
        precedes the drone's identity announcement.

        No-op when the drone isn't pending — i.e. it was already announced, or
        the new_drone rule is disabled (the key was never buffered). Uses the
        buffered (merged) device if present, else the caller's current frame.
        """
        fire = False
        with self._lock:
            pending = self._pending_new.pop(key, None)
            if pending is not None:
                device = pending[1] or device
                fire = True
        if fire:
            self._fire_alert(
                AlertType.NEW_DRONE.value, device,
                f"First detection of {key}",
            )

    def _flush_pending_new(self) -> None:
        """Fire new-drone alerts for drones that have been pending long enough."""
        now = time.monotonic()
        ready: List[Tuple[str, DroneIDDevice]] = []
        with self._lock:
            for key in list(self._pending_new):
                ts, device = self._pending_new[key]
                if now - ts >= self._new_drone_delay:
                    ready.append((key, device))
                    del self._pending_new[key]
        for key, device in ready:
            self._fire_alert(
                AlertType.NEW_DRONE.value, device,
                f"First detection of {key}",
            )

    def forget_drones(self, keys) -> None:
        """Drop all per-drone alert state for drones that have left tracking.

        Called from the maintenance loop with the keys cleanup_stale() just
        evicted from the engine's active set. Discarding from _known_serials
        re-arms the new_drone alert so a genuine reappearance hours later
        alerts again (rather than being silently treated as already-known for
        the life of the process). Also bounds the otherwise-unbounded growth
        of these sets.
        """
        if not keys:
            return
        with self._lock:
            for key in keys:
                self._known_serials.discard(key)
                self._alerted_lost.discard(key)
                self._alerted_violations.pop(key, None)
                self._pending_new.pop(key, None)

    def get_pending_alerts(self) -> List[dict]:
        """Return and clear the list of pending alert dicts.

        Consumed by the frontend polling endpoint.
        """
        with self._lock:
            pending = list(self._pending_alerts)
            self._pending_alerts.clear()
        return pending

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _auto_resolve(self, alert_type: str, key: str) -> None:
        """Auto-resolve the most recent active or acknowledged alert of this type for this drone."""
        try:
            alerts, _ = self._db.get_alerts(limit=20)
            for a in alerts:
                if (a.get('alert_type') == alert_type
                        and a.get('serial_number') == key
                        and a.get('state', 'ACTIVE') != 'RESOLVED'):
                    self._db.resolve_alert(a['id'])
                    log.info("alert_engine: auto-resolved %s for %s (id=%s)", alert_type, key, a['id'])
                    break
        except Exception:
            log.exception("alert_engine: failed to auto-resolve %s for %s", alert_type, key)

    def _fire_alert(self, alert_type: str, device: DroneIDDevice, detail: str) -> None:
        """Create an AlertEvent, persist it, enqueue for frontend, and run script."""
        # ---- Resolve receiver position once ----
        rx_lat = rx_lon = None
        if self._gps:
            _rl, _rn, _ra = self._gps.get_receiver_position()
            # Treat (0, 0) as absent — same convention used throughout the file.
            if _rl != 0.0 or _rn != 0.0:
                rx_lat, rx_lon = _rl, _rn

        has_drone = device.drone_lat != 0.0 or device.drone_lon != 0.0
        has_op = device.operator_lat != 0.0 or device.operator_lon != 0.0

        # ---- Compute geo BEFORE the DB insert so the persisted row is complete ----
        event_range_m = event_bearing_deg = None
        event_op_lat = event_op_lon = None
        event_op_range_m = event_op_bearing_deg = None

        if rx_lat is not None:
            if has_drone:
                event_range_m = round(haversine(rx_lat, rx_lon, device.drone_lat, device.drone_lon), 1)
                event_bearing_deg = round(bearing(rx_lat, rx_lon, device.drone_lat, device.drone_lon), 1)
            if has_op:
                # Do NOT fall back to takeoff_lat/lon — semantically distinct
                event_op_lat = device.operator_lat
                event_op_lon = device.operator_lon
                event_op_range_m = round(haversine(rx_lat, rx_lon, device.operator_lat, device.operator_lon), 1)
                event_op_bearing_deg = round(bearing(rx_lat, rx_lon, device.operator_lat, device.operator_lon), 1)

        event = AlertEvent(
            timestamp=_utcnow_iso_z(),
            alert_type=alert_type,
            serial_number=device.get_key(),
            detail=detail,
            drone_lat=device.drone_lat,
            drone_lon=device.drone_lon,
            drone_height_agl=device.drone_height_agl,
            range_m=event_range_m,
            bearing_deg=event_bearing_deg,
            operator_lat=event_op_lat,
            operator_lon=event_op_lon,
            operator_range_m=event_op_range_m,
            operator_bearing_deg=event_op_bearing_deg,
            receiver_lat=rx_lat,
            receiver_lon=rx_lon,
        )

        try:
            row_id = self._db.insert_alert(event)
            event.id = row_id
        except Exception:
            log.exception("alert_engine: failed to persist alert to DB")

        alert_dict = event.to_dict()

        # Enrich with device identity for Slack/script consumers
        alert_dict['operator_id'] = device.operator_id or ''
        alert_dict['registration_id'] = device.registration_id or ''
        alert_dict['self_id_text'] = device.self_id_text or ''
        alert_dict['mac_address'] = device.mac_address or ''
        alert_dict['speed'] = device.speed
        alert_dict['direction'] = device.direction
        alert_dict['rssi'] = device.rssi

        # UA type display name
        try:
            alert_dict['ua_type_name'] = UAType(device.ua_type).display_name
        except (ValueError, KeyError):
            alert_dict['ua_type_name'] = ''

        # Protocol — raw value for scripts, friendly name for display
        proto_raw = device.protocol or ''
        alert_dict['protocol'] = proto_raw
        alert_dict['protocol_display'] = _PROTOCOL_DISPLAY.get(proto_raw, proto_raw)

        # Manufacturer from protocol, serial-number prefix, or MAC OUI.
        alert_dict['vendor'] = self.resolve_vendor(
            serial=device.serial_number or '',
            mac=device.mac_address or '',
            protocol=proto_raw,
        )

        # Promote geo to alert_dict for Slack/script/API consumers
        if event_range_m is not None:
            alert_dict['bearing_cardinal'] = bearing_cardinal(event_bearing_deg)
        if event_op_range_m is not None:
            alert_dict['operator_bearing_cardinal'] = bearing_cardinal(event_op_bearing_deg)

        with self._lock:
            self._pending_alerts.append(alert_dict)

        log.info("alert_engine: %s — %s — %s", alert_type, event.serial_number, detail)

        # Fire external callback (e.g. Elasticsearch indexing)
        if self.on_alert:
            try:
                self.on_alert(event, device)
            except Exception:
                pass

        if self._script_enabled and self._script_path:
            self._run_script(alert_dict)

        if self._slack_enabled and self._slack_webhook_url:
            self._post_slack(alert_dict)

        if self._api_enabled and self._api_base_url and self._api_token:
            self._post_api(alert_dict)

    def _run_script(self, alert_dict: dict) -> None:
        """Invoke the external alert script in a daemon thread.

        The script receives the JSON-encoded alert as its first positional
        argument.  Errors are logged but never propagate to the caller.
        """
        payload = json.dumps(alert_dict)

        def _invoke() -> None:
            try:
                subprocess.Popen(
                    [self._script_path, payload],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                log.exception("alert_engine: script invocation failed: %s", self._script_path)

        t = threading.Thread(target=_invoke, daemon=True, name="alert-script")
        t.start()

    def _get_display_units(self) -> str:
        """Read display_units from DB (defaults to 'metric')."""
        return self._db.get_setting('display_units', 'metric') or 'metric'

    @staticmethod
    def _fmt_range(range_m: float, imperial: bool) -> str:
        """Format range with appropriate units."""
        if imperial:
            yards = range_m * 1.09361
            miles = range_m / 1609.34
            if miles < 0.2:
                return f"{yards:.0f} yd"
            return f"{miles:.1f} mi"
        if range_m < 1000:
            return f"{range_m:.0f} m"
        return f"{range_m / 1000:.1f} km"

    @staticmethod
    def _fmt_alt(meters: float, imperial: bool) -> str:
        if imperial:
            return f"{meters * 3.28084:.0f} ft"
        return f"{meters:.1f} m"

    @staticmethod
    def _fmt_speed(mps: float, imperial: bool) -> str:
        if imperial:
            return f"{mps * 2.23694:.1f} mph"
        return f"{mps:.1f} m/s"

    def _format_alert_message(self, alert_dict: dict, slack: bool = True) -> str:
        """Build a human-readable alert summary for operators.

        Prioritises actionable info: what kind of drone, where to look
        (range/bearing), altitude, speed — in that order.

        When ``slack`` is True the text carries Slack mrkdwn (the siren
        emoji, *bold*, `code`, _italics_).  When False it is plain text for
        generic consumers (the outbound API / ECS), which render mrkdwn as
        literal characters.
        """
        # Markup helpers — apply Slack mrkdwn only when targeting Slack.
        bold = (lambda s: f"*{s}*") if slack else (lambda s: s)
        code = (lambda s: f"`{s}`") if slack else (lambda s: s)
        ital = (lambda s: f"_{s}_") if slack else (lambda s: s)
        siren = ":rotating_light: " if slack else ""

        imperial = self._get_display_units() == 'imperial'

        alert_type = alert_dict.get('alert_type', 'unknown')
        serial = alert_dict.get('serial_number', 'Unknown')
        detail = alert_dict.get('detail', '')
        agl = alert_dict.get('drone_height_agl', 0)
        lat = alert_dict.get('drone_lat', 0)
        lon = alert_dict.get('drone_lon', 0)
        op_id = alert_dict.get('operator_id', '')
        reg_id = alert_dict.get('registration_id', '')
        ua_type = alert_dict.get('ua_type_name', '')
        self_id = alert_dict.get('self_id_text', '')
        protocol_display = alert_dict.get('protocol_display', '')
        speed = alert_dict.get('speed', 0)
        direction = alert_dict.get('direction', 0)
        vendor = alert_dict.get('vendor', '')
        range_m = alert_dict.get('range_m')
        bearing_deg = alert_dict.get('bearing_deg')
        bearing_card = alert_dict.get('bearing_cardinal', '')
        rssi = alert_dict.get('rssi', 0)

        type_labels = {
            'new_drone': 'New Drone Detected',
            'altitude_max': 'Altitude Violation',
            'speed_max': 'Speed Violation',
            'signal_lost': 'Signal Lost',
        }
        header = type_labels.get(alert_type, alert_type.replace('_', ' ').title())

        # --- Build message ---
        parts = [f"{siren}{bold(header)}"]

        # Identity block — what is it?
        id_parts = []
        if vendor:
            id_parts.append(vendor)
        if ua_type and ua_type != "None / Not Declared":
            id_parts.append(ua_type)
        if id_parts:
            parts.append(' '.join(id_parts))

        parts.append(f"Serial: {code(serial)}")
        if op_id:
            parts.append(f"Operator: {code(op_id)}")
        if reg_id:
            parts.append(f"Reg: {code(reg_id)}")
        if protocol_display:
            parts.append(f"Protocol: {protocol_display}")
        if self_id:
            parts.append(f"Description: {ital(self_id)}")

        # Where to look — range/bearing from sensor
        if range_m is not None and bearing_deg is not None:
            range_str = self._fmt_range(range_m, imperial)
            parts.append(f"Range: {range_str}  Bearing: {bearing_deg:.0f}° ({bearing_card})")

        # Position & altitude
        if lat != 0 or lon != 0:
            parts.append(f"Pos: {lat:.6f}, {lon:.6f}")
        if agl and agl != 0:
            parts.append(f"Alt: {self._fmt_alt(agl, imperial)} AGL")

        # Movement
        if speed and speed > 0:
            parts.append(f"Speed: {self._fmt_speed(speed, imperial)}  HDG: {direction:.0f}°")

        # RSSI
        if rssi and rssi != 0:
            parts.append(f"RSSI: {rssi} dBm")

        # Detail (e.g. violation specifics) — only if it adds value
        if detail and alert_type != AlertType.NEW_DRONE.value:
            parts.append(ital(detail))

        return '\n'.join(parts)

    def _post_slack(self, alert_dict: dict) -> None:
        """Post an alert notification to Slack via webhook in a daemon thread."""
        text = self._format_alert_message(alert_dict, slack=True)
        url = self._slack_webhook_url
        name = self._slack_display_name

        def _send():
            try:
                resp = _requests.post(
                    url,
                    json={'username': name, 'text': text},
                    timeout=10,
                )
                resp.raise_for_status()
            except Exception:
                log.exception("alert_engine: Slack webhook post failed")

        t = threading.Thread(target=_send, daemon=True, name="alert-slack")
        t.start()

    @staticmethod
    def test_slack(webhook_url: str, display_name: str = 'Sparrow DroneID') -> dict:
        """Send a test message to Slack. Returns {success, message/error}."""
        if not webhook_url:
            return {'success': False, 'error': 'Webhook URL is required'}
        try:
            resp = _requests.post(
                webhook_url,
                json={
                    'username': display_name,
                    'text': f'Test message from {display_name}',
                },
                timeout=10,
            )
            resp.raise_for_status()
            return {'success': True, 'message': 'Test message sent'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------ #
    # API-based alert delivery                                            #
    # ------------------------------------------------------------------ #

    # Suppress the InsecureRequestWarning that urllib3 logs every time a
    # request is made with verify=False.  Operators who disable TLS
    # verification have already made that choice in settings; one log
    # warning per process is enough.  Best-effort.
    try:
        import urllib3 as _urllib3
        _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    # Map alert types to ECS severity numerics (lower = more urgent).
    # Mirrors the convention used by sibling bridges (AIS, ACARS, ADS-B).
    _API_SEVERITY_MAP = {
        'new_drone':    40,   # warning — operator should look
        'altitude_max': 40,
        'speed_max':    40,
        'signal_lost':  70,   # info — informational
    }

    def _build_api_payload(self, alert_dict: dict, is_test: bool = False) -> dict:
        """Build the ECS-shaped JSON body for one outbound alert.

        Generic payload — no project-specific terms in the wire format.
        observer / source.geo / rule / event / labels follow ECS conventions
        so the receiving system can route without app knowledge.
        """
        alert_type = alert_dict.get('alert_type', 'unknown')
        type_labels = {
            'new_drone':    'New drone detected',
            'altitude_max': 'Drone altitude violation',
            'speed_max':    'Drone speed violation',
            'signal_lost':  'Drone signal lost',
        }
        if is_test:
            rule_name = 'API test message'
            rule_category = 'test'
            severity_num = 70
            action = 'test'
            message = f'Test message from {self._operator_name or "Sparrow DroneID"}'
        else:
            rule_name = type_labels.get(
                alert_type, alert_type.replace('_', ' ').title())
            rule_category = 'drone_detection'
            severity_num = self._API_SEVERITY_MAP.get(alert_type, 70)
            action = alert_type
            # Same human-readable summary as Slack, but plain text — the
            # receiving system isn't Slack and would show mrkdwn literally.
            message = self._format_alert_message(alert_dict, slack=False)

        observer: Dict = {
            'name': self._operator_name or 'Sparrow DroneID',
            'type': 'drone-sensor',
        }
        if self._gps:
            rx_lat, rx_lon, _rx_alt = self._gps.get_receiver_position()
            if rx_lat != 0.0 or rx_lon != 0.0:
                observer['geo'] = {'location': {
                    'lat': float(rx_lat), 'lon': float(rx_lon)}}

        body: Dict = {
            'domain': self._api_domain,
            'alert': {
                'message': message,
                'observer': observer,
                'rule':  {'name': rule_name, 'category': rule_category},
                'event': {
                    'severity': severity_num,
                    'category': 'network',
                    'action':   action,
                },
                'labels': {
                    'serial':      alert_dict.get('serial_number', '') or '',
                    'vendor':      alert_dict.get('vendor', '') or '',
                    'ua_type':     alert_dict.get('ua_type_name', '') or '',
                    'alert_type':  alert_type,
                },
                'details': {
                    'operator_id':       alert_dict.get('operator_id', ''),
                    'registration_id':   alert_dict.get('registration_id', ''),
                    'self_id_text':      alert_dict.get('self_id_text', ''),
                    'mac_address':       alert_dict.get('mac_address', ''),
                    'protocol':          alert_dict.get('protocol', ''),
                    'rssi':              alert_dict.get('rssi'),
                    # Receiver -> drone
                    'range_m':           alert_dict.get('range_m'),
                    'bearing_deg':       alert_dict.get('bearing_deg'),
                    'bearing_cardinal':  alert_dict.get('bearing_cardinal', ''),
                    # Receiver -> operator/controller (the pilot). The platform
                    # alert map can't plot a second point, but these render in
                    # the generic alert detail readout so an operator can call
                    # out where the controller is, not just the drone.
                    'operator_lat':              alert_dict.get('operator_lat'),
                    'operator_lon':              alert_dict.get('operator_lon'),
                    'operator_range_m':          alert_dict.get('operator_range_m'),
                    'operator_bearing_deg':      alert_dict.get('operator_bearing_deg'),
                    'operator_bearing_cardinal': alert_dict.get('operator_bearing_cardinal', ''),
                    'speed_mps':         alert_dict.get('speed'),
                    'direction_deg':     alert_dict.get('direction'),
                    'altitude_m_agl':    alert_dict.get('drone_height_agl'),
                    'detail':            alert_dict.get('detail', ''),
                },
            },
        }

        # Source geo: drone position when broadcast.  (0, 0) is treated as
        # absent — the device hasn't transmitted a position yet.
        drone_lat = alert_dict.get('drone_lat')
        drone_lon = alert_dict.get('drone_lon')
        if drone_lat and drone_lon and (drone_lat != 0.0 or drone_lon != 0.0):
            body['alert']['source'] = {'geo': {'location': {
                'lat': float(drone_lat), 'lon': float(drone_lon)}}}
        return body

    def _post_api(self, alert_dict: dict) -> None:
        """Dispatch an alert to the configured API endpoint in a daemon thread.

        Fire-and-forget — failures are logged but never block detection or
        the rest of the alert pipeline (script / Slack / DB / on_alert).
        Retries 503 with exponential backoff; aborts on 4xx without retry.
        """
        url = self._api_base_url.rstrip('/') + '/v1/alerts'
        token = self._api_token
        domain = self._api_domain
        verify_tls = self._api_verify_tls
        payload = self._build_api_payload(alert_dict, is_test=False)

        def _send():
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type':  'application/json',
            }
            backoff = 1.0
            for attempt in range(4):  # 1 initial + 3 retries on 503
                try:
                    resp = _requests.post(url, json=payload, headers=headers,
                                          timeout=15, verify=verify_tls)
                except _requests.RequestException as exc:
                    log.warning("alert_engine: API POST network error "
                                "(attempt %d/4): %s", attempt + 1, exc)
                    if attempt < 3:
                        time.sleep(backoff)
                        backoff *= 2
                    continue
                if resp.status_code == 201:
                    return
                if resp.status_code == 503 and attempt < 3:
                    log.warning(
                        "alert_engine: API returned 503 (attempt %d/4) — "
                        "retrying in %.1fs", attempt + 1, backoff)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                if resp.status_code == 200:
                    # Some endpoints reply 200 with status:dropped when the
                    # domain is disabled upstream.  Log and stop.
                    try:
                        if resp.json().get('status') == 'dropped':
                            log.warning(
                                "alert_engine: API reports domain '%s' is "
                                "disabled — alert dropped upstream", domain)
                            return
                    except ValueError:
                        pass
                log.error("alert_engine: API POST failed status=%d body=%s",
                          resp.status_code, resp.text[:200])
                return

        t = threading.Thread(target=_send, daemon=True, name='alert-api')
        t.start()

    @staticmethod
    def _api_verify_request(base_url: str, domain: str, token: str,
                            verify_tls: bool) -> dict:
        """Call the verify endpoint. Returns {success, message/error}.

        Pure static helper: receives all credentials so the api-test handler
        can call it directly without depending on instance state.  Error
        messages are operator-actionable (auth vs URL vs network).
        """
        if not base_url:
            return {'success': False, 'error': 'API endpoint root is required.'}
        if not domain:
            return {'success': False, 'error': 'Domain is required.'}
        if not token:
            return {'success': False, 'error': 'Bearer token is required.'}
        url = base_url.rstrip('/') + '/v1/alerts/verify'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }
        try:
            resp = _requests.post(url, json={'domain': domain},
                                  headers=headers, timeout=10,
                                  verify=verify_tls)
        except _requests.exceptions.SSLError as exc:
            return {'success': False,
                    'error': f'TLS verification failed: {exc}. '
                             'Disable "Verify TLS" if the endpoint uses a '
                             'self-signed or private-CA certificate.'}
        except _requests.exceptions.ConnectionError as exc:
            return {'success': False,
                    'error': f'Could not reach the server: {exc}. '
                             'Check the endpoint root URL and network access.'}
        except _requests.exceptions.Timeout:
            return {'success': False,
                    'error': 'Request timed out after 10s.'}
        except _requests.RequestException as exc:
            return {'success': False, 'error': f'Request error: {exc}'}

        if resp.status_code == 200:
            try:
                ok = resp.json().get('status') == 'ok'
            except ValueError:
                ok = False
            if ok:
                return {'success': True,
                        'message': f"Authentication verified for domain '{domain}'."}
            return {'success': False,
                    'error': f'Server returned 200 but unexpected body: '
                             f'{resp.text[:200]}'}
        if resp.status_code == 401:
            return {'success': False,
                    'error': 'Authentication failed (401) — check the '
                             'domain and bearer token.'}
        if resp.status_code == 404:
            return {'success': False,
                    'error': 'Endpoint not found (404) — check the API root '
                             'URL includes any required path prefix.'}
        if resp.status_code == 503:
            return {'success': False,
                    'error': 'Server reported temporary unavailability (503).'}
        return {'success': False,
                'error': f'Unexpected response status {resp.status_code}: '
                         f'{resp.text[:200]}'}

    def test_api_auth(self) -> dict:
        """Convenience wrapper: verify with the engine's currently-loaded
        API credentials.  Caller typically just reloads config first via
        reload_config() so settings just saved are visible."""
        return self._api_verify_request(
            self._api_base_url, self._api_domain, self._api_token,
            self._api_verify_tls)

    def test_api_send(self) -> dict:
        """Send a synthetic test alert to the configured endpoint.  Returns
        {success, message/error}.  Uses rule.category='test' and a sentinel
        serial so the receiver UI can recognise it as a test artifact."""
        base_url = self._api_base_url
        domain = self._api_domain
        token = self._api_token
        verify_tls = self._api_verify_tls
        if not base_url:
            return {'success': False, 'error': 'API endpoint root is required.'}
        if not domain:
            return {'success': False, 'error': 'Domain is required.'}
        if not token:
            return {'success': False, 'error': 'Bearer token is required.'}

        synthetic = {
            'alert_type':      'test',
            'serial_number':   'TEST-0000',
            'detail':          'Synthetic alert generated by the Sparrow DroneID Send-Test action.',
            'drone_lat':       0.0,
            'drone_lon':       0.0,
            'drone_height_agl': 0,
            'vendor':          'Sparrow DroneID (test)',
            'ua_type_name':    'Test',
        }
        payload = self._build_api_payload(synthetic, is_test=True)
        url = base_url.rstrip('/') + '/v1/alerts'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }
        try:
            resp = _requests.post(url, json=payload, headers=headers,
                                  timeout=10, verify=verify_tls)
        except _requests.exceptions.SSLError as exc:
            return {'success': False,
                    'error': f'TLS verification failed: {exc}. '
                             'Disable "Verify TLS" if the endpoint uses a '
                             'self-signed or private-CA certificate.'}
        except _requests.exceptions.ConnectionError as exc:
            return {'success': False,
                    'error': f'Could not reach the server: {exc}.'}
        except _requests.exceptions.Timeout:
            return {'success': False, 'error': 'Request timed out after 10s.'}
        except _requests.RequestException as exc:
            return {'success': False, 'error': f'Request error: {exc}'}

        if resp.status_code == 201:
            try:
                alert_id = resp.json().get('alert_id', '?')
            except ValueError:
                alert_id = '?'
            return {'success': True,
                    'message': f'Test alert accepted (alert_id={alert_id}).'}
        if resp.status_code == 200:
            try:
                if resp.json().get('status') == 'dropped':
                    return {'success': False,
                            'error': f"Server accepted the request but "
                                     f"reports domain '{domain}' is disabled "
                                     f"upstream — alert dropped."}
            except ValueError:
                pass
        if resp.status_code == 401:
            return {'success': False,
                    'error': 'Authentication failed (401) — check domain and token.'}
        if resp.status_code == 400:
            return {'success': False,
                    'error': f'Server rejected payload (400): {resp.text[:200]}'}
        if resp.status_code == 503:
            return {'success': False,
                    'error': 'Server reported temporary unavailability (503).'}
        return {'success': False,
                'error': f'Unexpected response status {resp.status_code}: '
                         f'{resp.text[:200]}'}
