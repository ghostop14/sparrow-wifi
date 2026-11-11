"""
TakEngine: drives the TAK / Cursor-on-Target output.

Threading (see TAK_INTEGRATION.md):
  * the detection thread only calls :meth:`notify_detection` (``Event.set``);
  * the ``tak-emit`` thread snapshots drone state once per tick, diffs the
    *desired* set of CoT items against the set already *drawn*, and hands one
    ordered batch to the transport;
  * the transport's ``tak-sender`` thread owns the socket;
  * the ``tak-enroll`` thread (TLS + enroll mode only) owns enrollment.

Lifecycle is one rule: every drawn UID that leaves the desired set gets exactly
one delete. That covers a drone vanishing, a MAC->serial key migration, an
operator/line going away and a settings change.
"""
import logging
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from ..models import DroneIDDevice
from . import cot
from .certs import CertStore, TlsMaterials, iso_z, resolve_tls_materials
from .config import TICK_S, TakConfig
from .enrollment import EnrollmentManager
from .transport import CotTransport

logger = logging.getLogger(__name__)

_COALESCE_S = 0.25
_ERROR_LOG_INTERVAL_S = 60.0
_STOP_JOIN_S = 2.0
_FLUSH_S = 2.0


@dataclass
class _Drawn:
    kind: str            # 'drone' | 'operator' | 'line' | 'sensor'
    cot_type: str


@dataclass
class _Sched:
    sent_m: float
    last_seen: str
    type_sig: tuple


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_names(cfg: Optional[TakConfig]) -> dict:
    if cfg is not None:
        return {'station_name': cfg.station_name, 'client_uid': cfg.client_uid}
    from .config import normalize, DEFAULTS
    c, _ = normalize(dict(DEFAULTS))
    return {'station_name': c.station_name, 'client_uid': c.client_uid}


class TakEngine:
    """Owns the transport, the emit thread and (when needed) enrollment."""

    def __init__(self, *, cert_dir: str,
                 drone_source: Callable[[], List[Tuple[str, DroneIDDevice]]],
                 gps_engine,
                 monotonic: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = _utcnow,
                 transport_factory: Callable[..., CotTransport] = CotTransport,
                 enrollment_factory: Callable[..., EnrollmentManager] = EnrollmentManager,
                 start_threads: bool = True) -> None:
        self._cert_store = CertStore(cert_dir)
        self._drone_source = drone_source
        self._gps = gps_engine
        self._monotonic = monotonic
        self._wallclock = wallclock
        self._transport_factory = transport_factory
        self._enrollment_factory = enrollment_factory
        self._start_threads = start_threads

        # Serialises configure()/stop()/start; held for the whole stop+start.
        self._config_lock = threading.RLock()
        # Protects the published status snapshot and the counters below.
        self._status_lock = threading.Lock()

        self._cfg: Optional[TakConfig] = None
        self._warnings: List[str] = []
        self._transport: Optional[CotTransport] = None
        self._enrollment: Optional[EnrollmentManager] = None
        self._last_materials: Optional[TlsMaterials] = None

        self._wake = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Emit-thread state (tests drive tick() directly from one thread).
        self._drawn: Dict[str, _Drawn] = {}
        self._sched: Dict[str, _Sched] = {}
        self._sensor_sent_m: Optional[float] = None
        self._sensor_sent_at: Optional[datetime] = None
        self._last_epoch = 0
        self._last_error_log_m = -1e9

        # Status snapshot + counters (under _status_lock).
        self._deletes_sent = 0
        self._deletes_on_stop = 0
        self._ticks = 0
        self._tick_errors = 0
        self._last_tick_at: Optional[datetime] = None
        self._items = {'drones': 0, 'operators': 0, 'lines': 0, 'sensor': 0}
        self._sensor_reason = 'disabled'

    # ---- public properties ------------------------------------------------------

    @property
    def enabled(self) -> bool:
        cfg = self._cfg
        return bool(cfg is not None and cfg.enabled)

    @property
    def config(self) -> Optional[TakConfig]:
        return self._cfg

    @property
    def enrollment(self) -> Optional[EnrollmentManager]:
        return self._enrollment

    @property
    def cert_store(self) -> CertStore:
        return self._cert_store

    @property
    def config_warnings(self) -> List[str]:
        return list(self._warnings)

    def set_config_warnings(self, warnings: List[str]) -> None:
        """Record normalize() warnings for status; each is logged once."""
        new = [w for w in warnings if w not in self._warnings]
        for w in new:
            logger.warning('TAK: %s', w)
        self._warnings = list(warnings)

    # ---- configure / stop -----------------------------------------------------------

    def configure(self, cfg: TakConfig) -> None:
        """Apply *cfg*. An identical config is a no-op; any change is a full stop + start."""
        with self._config_lock:
            if cfg == self._cfg:
                return
            self._stop_locked()
            self._cfg = cfg
            if not cfg.enabled:
                return
            if cfg.transport == 'tls' and cfg.tls_client_mode == 'enroll':
                self._enrollment = self._enrollment_factory(cfg, self._cert_store)
                self._enrollment.start()
            self._transport = self._transport_factory(cfg, materials=self._resolve_materials)
            self._transport.start()
            self._drawn = {}
            self._sched = {}
            self._sensor_sent_m = None
            self._sensor_sent_at = None
            self._last_epoch = self._transport.connection_epoch
            self._stop_event.clear()
            self._wake.clear()
            if self._start_threads:
                self._thread = threading.Thread(target=self._run, name='tak-emit', daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._config_lock:
            self._stop_locked()
            self._cfg = None

    def restart(self) -> None:
        """Stop (with deletes) and start again with the same config, e.g. after the
        certificate files the TLS link uses were replaced."""
        with self._config_lock:
            cfg = self._cfg
            if cfg is None:
                return
            self._stop_locked()
            self._cfg = None
            self.configure(cfg)

    def _stop_locked(self) -> None:
        transport = self._transport
        if transport is None and self._enrollment is None and self._thread is None:
            return
        cfg = self._cfg
        self._stop_event.set()
        self._wake.set()
        emit_ok = True
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(_STOP_JOIN_S)
            if thread.is_alive():
                logger.warning('TAK: emit thread did not stop in %.0f s; skipping stop-time deletes',
                               _STOP_JOIN_S)
                emit_ok = False
        self._thread = None
        if emit_ok and transport is not None and cfg is not None and cfg.send_deletes:
            now_w = self._wallclock()
            batch = [('delete', uid, cot.build_delete(uid, drawn.cot_type, now_w))
                     for uid, drawn in sorted(self._drawn.items())
                     if drawn.kind != 'sensor']
            if batch:
                transport.enqueue(batch)
                with self._status_lock:
                    self._deletes_on_stop += len(batch)
                if not transport.flush(_FLUSH_S):
                    logger.warning('TAK: stop-time deletes were not delivered within %.0f s',
                                   _FLUSH_S)
        if transport is not None:
            transport.stop()
        if self._enrollment is not None:
            self._enrollment.stop()
        self._transport = None
        self._enrollment = None
        self._drawn = {}
        self._sched = {}
        with self._status_lock:
            self._items = {'drones': 0, 'operators': 0, 'lines': 0, 'sensor': 0}

    def _resolve_materials(self) -> TlsMaterials:
        cfg = self._cfg
        mats = resolve_tls_materials(cfg, self._cert_store, self._enrollment)
        self._last_materials = mats
        return mats

    # ---- detection hook -----------------------------------------------------------------

    def notify_detection(self) -> None:
        """Called on the capture thread for every detection: one ``Event.set``."""
        self._wake.set()

    # ---- emit loop ----------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            woke = self._wake.wait(timeout=TICK_S)
            self._wake.clear()
            if woke and not self._stop_event.is_set():
                self._stop_event.wait(_COALESCE_S)

    def tick(self) -> None:
        """One emit tick; exceptions are counted and logged (at most once a minute)."""
        try:
            self._tick()
        except Exception:
            now_m = self._monotonic()
            with self._status_lock:
                self._tick_errors += 1
            if now_m - self._last_error_log_m >= _ERROR_LOG_INTERVAL_S:
                self._last_error_log_m = now_m
                logger.warning('TAK emit tick failed', exc_info=True)

    def _tick(self) -> None:
        cfg = self._cfg
        transport = self._transport
        if cfg is None or transport is None or not cfg.enabled:
            return
        now_m = self._monotonic()
        now_w = self._wallclock()
        drones = self._drone_source()
        gps = self._gps.to_dict()
        epoch = transport.connection_epoch

        # 1. Reconnect: the transport discarded queued updates; resend what is desired.
        force_all = False
        if epoch != self._last_epoch:
            force_all = True
            self._last_epoch = epoch
            self._sensor_sent_m = None

        # 2. Desired items.
        desired_uids = set()
        wanted: List[Tuple[cot.DroneView, DroneIDDevice, bool, bool]] = []
        for key, dev in drones:
            seen = cot.parse_iso(dev.last_seen)
            if seen is None:
                continue
            if (now_w - seen).total_seconds() > cfg.delete_after_s:
                continue
            if dev.drone_lat == 0.0 and dev.drone_lon == 0.0:
                continue
            if dev.protocol == 'wifi_ssid' and not cfg.include_ssid_detections:
                continue
            view = cot.drone_view(key, dev, cfg)
            has_op = (cfg.operator_enabled and (cot.has_operator_position(dev)
                                                or cot.has_takeoff_position(dev)))
            has_line = has_op and cfg.line_enabled
            desired_uids.add(view.drone_uid)
            if has_op:
                desired_uids.add(view.operator_uid)
            if has_line:
                desired_uids.add(view.line_uid)
            wanted.append((view, dev, has_op, has_line))

        batch: List[Tuple[str, str, bytes]] = []

        # 3. Deletes first.
        deletes = 0
        for uid in list(self._drawn):
            drawn = self._drawn[uid]
            if drawn.kind == 'sensor' or uid in desired_uids:
                continue
            if cfg.send_deletes:
                batch.append(('delete', uid, cot.build_delete(uid, drawn.cot_type, now_w)))
                deletes += 1
            del self._drawn[uid]
        for drone_uid in [u for u in self._sched if u not in desired_uids]:
            del self._sched[drone_uid]

        # 4. Updates.
        for view, dev, has_op, has_line in wanted:
            rec = self._sched.get(view.drone_uid)
            fresh = view.stale_at > now_w
            send = False
            if fresh:
                if rec is None or force_all:
                    send = True
                elif rec.type_sig != view.type_sig:
                    send = True
                elif (rec.last_seen != dev.last_seen
                      and now_m - rec.sent_m >= cfg.send_interval_s):
                    send = True
                elif now_m - rec.sent_m >= cfg.refresh_interval_s:
                    send = True
            if not send:
                continue
            batch.append(('update', view.drone_uid, cot.build_drone(view, dev, cfg, now_w)))
            self._drawn[view.drone_uid] = _Drawn('drone', view.drone_type)
            if has_op:
                batch.append(('update', view.operator_uid, cot.build_operator(view, dev, now_w)))
                self._drawn[view.operator_uid] = _Drawn('operator', view.operator_type)
            if has_line:
                batch.append(('update', view.line_uid, cot.build_line(view, dev, now_w)))
                self._drawn[view.line_uid] = _Drawn('line', cot.LINE_TYPE)
            self._sched[view.drone_uid] = _Sched(now_m, dev.last_seen, view.type_sig)

        # 5. Sensor point.
        reason = 'disabled'
        if cfg.sensor_enabled:
            has_fix = (bool(gps.get('fix'))
                       and (gps.get('latitude', 0.0), gps.get('longitude', 0.0)) != (0.0, 0.0))
            if not has_fix:
                reason = 'no GPS fix'
            else:
                reason = 'publishing'
                if (self._sensor_sent_m is None
                        or now_m - self._sensor_sent_m >= cfg.sensor_heartbeat_s):
                    tracked = sum(1 for d in self._drawn.values() if d.kind == 'drone')
                    batch.append(('update', cot.sensor_uid(cfg),
                                  cot.build_sensor(cfg, gps, now_w, tracked)))
                    self._drawn[cot.sensor_uid(cfg)] = _Drawn('sensor', cot.SENSOR_TYPE)
                    self._sensor_sent_m = now_m
                    self._sensor_sent_at = now_w

        # 6. One hand-off.
        if batch:
            transport.enqueue(batch)

        # 7. Publish status.
        items = {'drones': 0, 'operators': 0, 'lines': 0, 'sensor': 0}
        plural = {'drone': 'drones', 'operator': 'operators', 'line': 'lines', 'sensor': 'sensor'}
        for drawn in self._drawn.values():
            items[plural[drawn.kind]] += 1
        with self._status_lock:
            self._deletes_sent += deletes
            self._ticks += 1
            self._last_tick_at = now_w
            self._items = items
            self._sensor_reason = reason

    # ---- status ------------------------------------------------------------------------

    def get_status(self) -> dict:
        cfg = self._cfg
        transport = self._transport
        enabled = bool(cfg and cfg.enabled)
        stats = transport.get_stats() if transport is not None else None
        with self._status_lock:
            items = dict(self._items)
            deletes_sent = self._deletes_sent
            deletes_on_stop = self._deletes_on_stop
            reason = self._sensor_reason
            sensor_at = self._sensor_sent_at
        if not enabled:
            state = 'disabled'
        elif stats is None:
            state = 'connecting'
        elif stats['state'] == 'connected':
            state = 'ready' if cfg.transport.startswith('udp_') else 'connected'
        elif stats['state'] == 'error':
            state = 'error'
        else:
            state = 'connecting'
        status = {
            'enabled': enabled,
            'running': transport is not None,
            'state': state,
            'transport': cfg.transport if cfg else None,
            'destination': ('%s:%s' % (cfg.host, cfg.port)) if cfg else None,
            'connected': bool(stats and stats['connected']),
            'connected_since': stats['connected_since'] if stats else None,
            'sent': stats['messages_sent'] if stats else 0,
            'send_errors': stats['send_errors'] if stats else 0,
            'reconnects': stats['reconnects'] if stats else 0,
            'queue_depth': stats['queue_depth'] if stats else 0,
            'queue_drops': stats['queue_drops'] if stats else 0,
            'last_error': stats['last_error'] if stats else None,
            'last_error_at': stats['last_error_at'] if stats else None,
            'last_send_at': stats['last_send_at'] if stats else None,
            'items': items,
            'deletes_sent': deletes_sent,
            'deletes_on_stop': deletes_on_stop,
            'sensor_point': {
                'enabled': bool(cfg and cfg.sensor_enabled),
                'publishing': reason == 'publishing',
                'reason': reason if enabled else 'disabled',
                'last_sent_at': iso_z(sensor_at),
                'stale_s': cfg.sensor_stale_s if cfg else None,
            },
            'tls': self._tls_status(cfg, stats) if enabled and cfg.transport == 'tls' else None,
            'enrollment': (self._enrollment.status()
                           if enabled and self._enrollment is not None else None),
            'config_warnings': list(self._warnings),
            'defaults': _default_names(cfg),
        }
        return status

    def _tls_status(self, cfg: TakConfig, stats: Optional[dict]) -> dict:
        tls = (stats or {}).get('tls') or {}
        mats = self._last_materials
        subject = mats.subject if mats else None
        not_after = mats.not_after if mats else None
        if cfg.tls_client_mode == 'upload' and (subject is None or not_after is None):
            info = self._cert_store.client_info()
            if info:
                subject = info.get('subject')
                not_after = _parse_z(info.get('not_after'))
        days_left = None
        if not_after is not None:
            days_left = int((not_after - self._wallclock()).total_seconds() // 86400)
        ca_subjects: List[str] = []
        if cfg.tls_ca_mode == 'upload':
            ca = self._cert_store.ca_info()
            ca_subjects = list(ca['subjects']) if ca else []
        return {
            'version': tls.get('version'),
            'cipher': tls.get('cipher'),
            'server_subject': tls.get('server_subject'),
            'client_mode': cfg.tls_client_mode,
            'client_subject': subject,
            'client_not_after': iso_z(not_after),
            'client_days_left': days_left,
            'ca_mode': cfg.tls_ca_mode,
            'ca_subjects': ca_subjects,
        }

    def legacy_status(self) -> dict:
        """Shape of the legacy ``GET /api/v1/cot/status``."""
        st = self.get_status()
        cfg = self._cfg
        return {
            'enabled': st['enabled'],
            'address': cfg.host if cfg else '239.2.3.1',
            'port': cfg.port if cfg else 6969,
            'events_sent': st['sent'],
            'transport': st['transport'],
            'state': st['state'],
            'connected': st['connected'],
            'send_errors': st['send_errors'],
            'last_error': st['last_error'],
        }


def _parse_z(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None
