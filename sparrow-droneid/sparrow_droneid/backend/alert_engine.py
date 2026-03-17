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
from datetime import datetime
from typing import Dict, List, Optional

import requests as _requests

from .models import AlertEvent, AlertRule, AlertType, DEFAULT_ALERT_RULES, DroneIDDevice
from .database import Database

log = logging.getLogger(__name__)


class AlertEngine:
    """Evaluates drone detections against alert rules and dispatches events.

    Thread-safe: _pending_alerts and _known_serials are protected by
    _lock.  _alerted_lost is accessed only from the periodic
    check_signal_lost() caller and does not require separate locking
    provided that caller is single-threaded (e.g. one background timer).
    """

    def __init__(self, db: Database) -> None:
        self._db = db
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

        self._load_config()

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

        # Slack webhook notifications
        self._slack_enabled: bool    = _bool('alert_slack_enabled', False)
        self._slack_webhook_url: str = self._db.get_setting('alert_slack_webhook_url', '') or ''
        self._slack_display_name: str = self._db.get_setting('alert_slack_display_name', 'Sparrow DroneID') or 'Sparrow DroneID'

    def reload_config(self) -> None:
        """Re-read alert configuration from the database."""
        self._load_config()

    def get_config(self) -> dict:
        """Return current alert configuration as a plain dict."""
        return {
            'rules': [r.to_dict() for r in self._rules],
            'audio_enabled':  self._audio_enabled,
            'visual_enabled': self._visual_enabled,
            'script_enabled': self._script_enabled,
            'script_path':    self._script_path,
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

        for rule in self._rules:
            if not rule.enabled:
                continue

            rtype = rule.type

            if rtype == AlertType.NEW_DRONE.value:
                with self._lock:
                    is_new = key not in self._known_serials
                    self._known_serials.add(key)
                if is_new:
                    self._fire_alert(
                        AlertType.NEW_DRONE.value, device,
                        f"First detection of {key}",
                    )

            elif rtype == AlertType.ALTITUDE_MAX.value:
                max_alt = rule.params.get('max_altitude_m', 122.0)
                if device.drone_height_agl > max_alt:
                    with self._lock:
                        violations = self._alerted_violations.setdefault(key, set())
                        already = AlertType.ALTITUDE_MAX.value in violations
                        if not already:
                            violations.add(AlertType.ALTITUDE_MAX.value)
                    if not already:
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
        now = datetime.utcnow()

        for key, device in active_drones.items():
            try:
                last_seen = datetime.fromisoformat(
                    device.last_seen.replace('Z', '+00:00').replace('+00:00', '')
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
        event = AlertEvent(
            timestamp=datetime.utcnow().isoformat() + 'Z',
            alert_type=alert_type,
            serial_number=device.get_key(),
            detail=detail,
            drone_lat=device.drone_lat,
            drone_lon=device.drone_lon,
            drone_height_agl=device.drone_height_agl,
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
        alert_dict['ua_type_name'] = getattr(device, 'ua_type_name', '') or ''
        alert_dict['protocol'] = device.protocol or ''
        alert_dict['mac_address'] = device.mac_address or ''
        alert_dict['speed'] = device.speed
        alert_dict['direction'] = device.direction

        with self._lock:
            self._pending_alerts.append(alert_dict)

        log.info("alert_engine: %s — %s — %s", alert_type, event.serial_number, detail)

        if self._script_enabled and self._script_path:
            self._run_script(alert_dict)

        if self._slack_enabled and self._slack_webhook_url:
            self._post_slack(alert_dict)

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

    def _format_slack_message(self, alert_dict: dict) -> str:
        """Build a Slack-formatted alert message."""
        alert_type = alert_dict.get('alert_type', 'unknown')
        serial = alert_dict.get('serial_number', 'Unknown')
        detail = alert_dict.get('detail', '')
        agl = alert_dict.get('drone_height_agl', 0)
        lat = alert_dict.get('drone_lat', 0)
        lon = alert_dict.get('drone_lon', 0)
        op_id = alert_dict.get('operator_id', '')
        reg_id = alert_dict.get('registration_id', '')
        ua_type = alert_dict.get('ua_type_name', '')
        protocol = alert_dict.get('protocol', '')
        speed = alert_dict.get('speed', 0)
        direction = alert_dict.get('direction', 0)

        type_labels = {
            'new_drone': 'New Drone Detected',
            'altitude_max': 'Altitude Violation',
            'speed_max': 'Speed Violation',
            'signal_lost': 'Signal Lost',
        }
        header = type_labels.get(alert_type, alert_type.replace('_', ' ').title())

        parts = [f"*{header}*"]
        parts.append(f"Serial: `{serial}`")
        if op_id:
            parts.append(f"Operator ID: `{op_id}`")
        if reg_id:
            parts.append(f"Registration: `{reg_id}`")
        if ua_type:
            parts.append(f"Type: {ua_type}")
        if detail:
            parts.append(f"Detail: {detail}")
        if lat != 0 or lon != 0:
            parts.append(f"Position: {lat:.6f}, {lon:.6f}")
        if agl and agl != 0:
            parts.append(f"Alt AGL: {agl:.1f} m")
        if speed and speed > 0:
            parts.append(f"Speed: {speed:.1f} m/s  HDG {direction:.0f}°")
        if protocol:
            parts.append(f"Protocol: {protocol}")
        return '\n'.join(parts)

    def _post_slack(self, alert_dict: dict) -> None:
        """Post an alert notification to Slack via webhook in a daemon thread."""
        text = self._format_slack_message(alert_dict)
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
