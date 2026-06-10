"""
Supervisor — per-path (WiFi + BLE) self-healing state machine.

Owned by SparrowDroneID and started after all engines are initialised.
Reads thin engine health-input getters and calls equally thin engine
hooks to restart/rebind, never touching asyncio internals or scan state
directly.

Design principles
-----------------
- All counter/timestamp reads are lock-free: the GIL guarantees atomicity
  for simple int/float/bool attribute reads in CPython.
- restart_wifi_capture() and request_ble_rebind() are the ONLY calls made
  on the engine; both are documented as thread-safe in droneid_engine.py.
- os._exit is injected so unit tests can intercept it.
- No logging on the hot path (advertisement callback).  Heartbeat logging
  happens on a 60-second cadence to avoid log spam.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Supervisor tick cadence (seconds)
SUPERVISOR_TICK_S = 5.0

# Heartbeat log interval (seconds)
HEARTBEAT_INTERVAL_S = 60.0

# WiFi thresholds
WIFI_STALL_WINDOW_S = 60.0         # frames-frozen window before restart
MAX_INPLACE_RESTARTS = 5           # within RESTART_WINDOW_S → os._exit
RESTART_WINDOW_S = 300.0           # rolling window for restart accounting
WIFI_BACKOFF_STEPS = [5, 10, 20, 40, 60]  # inter-restart backoff (seconds)

# BLE thresholds
BLE_WATCHDOG_INITIAL_S = 30.0      # fast first check (startup coex wedge)
BLE_WATCHDOG_FLOOR_S = 75.0        # steady-state minimum window
BLE_WATCHDOG_CAP_S = 600.0         # maximum backoff
MAX_WEDGE_REBINDS_CONSEC = 6       # consecutive wedge rebinds before degraded

# HCI-RX probe sub-window (seconds) — how long we watch RX bytes climb
HCI_PROBE_DURATION_S = 10.0


# ── Rate-limited logger ──────────────────────────────────────────────────────

class RateLimitedLogger:
    """Emit a log record at most once per *min_interval* seconds per key.

    When a message has been suppressed at least once, the next emission
    appends ``(suppressed N)`` so the operator knows how many were dropped.
    Thread-safe (GIL protects the dict mutation).
    """

    def __init__(self, logger: logging.Logger, min_interval: float = 5.0):
        self._log = logger
        self._min_interval = min_interval
        # key → (last_emit_mono, suppressed_count)
        self._state: Dict[str, tuple] = {}

    def warning(self, key: str, msg: str, *args) -> None:
        self._emit(logging.WARNING, key, msg, args)

    def error(self, key: str, msg: str, *args) -> None:
        self._emit(logging.ERROR, key, msg, args)

    def _emit(self, level: int, key: str, msg: str, args: tuple) -> None:
        now = time.monotonic()
        last_t, suppressed = self._state.get(key, (0.0, 0))
        if now - last_t < self._min_interval:
            self._state[key] = (last_t, suppressed + 1)
            return
        full_msg = msg
        if suppressed > 0:
            full_msg = msg + f' (suppressed {suppressed})'
        self._state[key] = (now, 0)
        self._log.log(level, full_msg, *args)


# Module-level rate-limited logger used by the engine shim (imported by
# droneid_engine.py for parse-loop warnings).
rate_log = RateLimitedLogger(log)


# ── Backoff policy ───────────────────────────────────────────────────────────

class BackoffPolicy:
    """Tracks inter-restart backoff using a fixed step table."""

    def __init__(self, steps: list):
        self._steps = steps
        self._idx = 0

    @property
    def current(self) -> float:
        return float(self._steps[min(self._idx, len(self._steps) - 1)])

    def advance(self) -> None:
        self._idx = min(self._idx + 1, len(self._steps) - 1)

    def reset(self) -> None:
        self._idx = 0


# ── HCI RX byte reader ───────────────────────────────────────────────────────

def _read_hci_rx_bytes() -> Optional[int]:
    """Return the RX byte counter from ``hciconfig hci0``, or None on error."""
    try:
        r = subprocess.run(
            ['hciconfig', 'hci0'],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.splitlines():
            if 'RX bytes:' in line:
                m = re.search(r'RX bytes:(\d+)', line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None


# ── Supervisor ───────────────────────────────────────────────────────────────

class Supervisor:
    """Monitors WiFi capture and BLE scanner health; heals them when broken.

    Parameters
    ----------
    engine:
        DroneIDEngine instance (provides wifi_health_inputs / ble_health_inputs
        and restart_wifi_capture / request_ble_rebind hooks).
    get_maintenance_tick:
        Zero-arg callable returning the monotonic timestamp of the last
        maintenance-loop iteration (from app.py).
    get_flush_tick:
        Zero-arg callable returning the monotonic timestamp of the last
        ES flush-loop iteration (or 0.0 if ES is not configured).
    exit_hook:
        Callable invoked instead of ``os._exit`` — replace in tests.
    """

    def __init__(
        self,
        engine,
        get_maintenance_tick: Callable[[], float],
        get_flush_tick: Callable[[], float],
        exit_hook: Callable[[int], None] = os._exit,
    ):
        self._engine = engine
        self._get_maintenance_tick = get_maintenance_tick
        self._get_flush_tick = get_flush_tick
        self._exit_hook = exit_hook

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # --- Public heartbeat ---
        self._supervisor_last_tick: float = 0.0
        self._started_at: float = 0.0            # supervisor start (BLE unavailable grace)

        # --- WiFi supervisor state ---
        self._wifi_state: str = 'idle'           # healthy|idle|idle_unverified|restarting|degraded|down
        self._wifi_backoff = BackoffPolicy(WIFI_BACKOFF_STEPS)
        self._wifi_restart_ts: Deque[float] = deque()  # rolling RESTART_WINDOW_S
        self._wifi_next_restart_at: float = 0.0
        self._wifi_last_frame_count: int = 0     # snapshot for stall detection
        self._wifi_stall_since: float = 0.0      # when freeze was first noticed

        # --- BLE supervisor state ---
        self._ble_state: str = 'idle'
        self._ble_watchdog_window: float = BLE_WATCHDOG_INITIAL_S
        self._ble_window_start: float = 0.0
        self._ble_adv_count_at_window_start: int = 0
        self._ble_consec_no_ads: int = 0         # consecutive windows with zero ads (wedge path)
        self._ble_ever_healthy: bool = False      # cleared the initial 30s window

        # HCI-RX probe state (idle_unverified path at cap)
        self._ble_hci_probe_state: Optional[str] = None  # None | 'measuring'
        self._ble_hci_probe_at: float = 0.0
        self._ble_hci_rx_baseline: Optional[int] = None

        # WiFi frame count captured at BLE-window-start — the BLE oracle's
        # baseline for "did WiFi frames climb across this BLE window".  Owned
        # EXCLUSIVELY by _supervise_ble; never mutated by _supervise_wifi (which
        # owns _wifi_last_frame_count).  Sharing one baseline across both
        # directions is the same-tick clobber bug this avoids.
        self._wifi_frame_at_ble_window_start: int = 0

        # Heartbeat accounting
        self._heartbeat_at: float = 0.0

        # Rate-limited logger for supervisor messages
        self._rlog = RateLimitedLogger(log)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the supervisor background thread."""
        self._started_at = time.monotonic()
        self._ble_window_start = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='supervisor',
        )
        self._thread.start()
        log.info("Supervisor: started (tick=%.0fs WiFi stall=%.0fs "
                 "BLE floor=%.0fs cap=%.0fs)",
                 SUPERVISOR_TICK_S, WIFI_STALL_WINDOW_S,
                 BLE_WATCHDOG_FLOOR_S, BLE_WATCHDOG_CAP_S)

    def stop(self) -> None:
        """Stop the supervisor."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=SUPERVISOR_TICK_S + 2)
        log.info("Supervisor: stopped")

    # ── Main loop ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.wait(SUPERVISOR_TICK_S):
            try:
                self._tick()
            except Exception as exc:
                log.error("Supervisor tick error: %s", exc)

    def _tick(self) -> None:
        self._supervisor_last_tick = time.monotonic()
        engine = self._engine

        # Only supervise while monitoring is active
        if not engine.wifi_health_inputs().get('monitoring', False):
            self._wifi_state = 'idle'
            self._ble_state = 'idle'
            # Reset watchdog windows so they restart cleanly on next start
            self._ble_window_start = time.monotonic()
            self._ble_adv_count_at_window_start = 0
            self._wifi_frame_at_ble_window_start = 0
            self._ble_watchdog_window = BLE_WATCHDOG_INITIAL_S
            self._ble_ever_healthy = False
            self._ble_consec_no_ads = 0
            self._ble_hci_probe_state = None
            self._wifi_stall_since = 0.0
            self._wifi_restart_ts.clear()
            self._wifi_next_restart_at = 0.0
            self._wifi_backoff.reset()
            self._heartbeat()
            return

        wifi_inp = engine.wifi_health_inputs()
        ble_inp = engine.ble_health_inputs()

        self._supervise_wifi(wifi_inp, ble_inp)
        self._supervise_ble(ble_inp, wifi_inp)
        self._heartbeat()

    # ── WiFi supervision ─────────────────────────────────────────────────────

    def _supervise_wifi(self, inp: Dict[str, Any], ble_inp: Dict[str, Any]) -> None:
        now = time.monotonic()

        # ── Machinery-dead check (P0 — restart immediately, oracle irrelevant)
        machinery_dead = (
            not inp.get('tcpdump_alive', True)
            or not inp.get('parse_alive', True)
            or inp.get('eof', False)
        )
        if machinery_dead:
            reason = (
                'tcpdump exited' if not inp.get('tcpdump_alive', True) else
                'parse thread dead' if not inp.get('parse_alive', True) else
                f"EOF ({inp.get('eof_reason', '')})"
            )
            self._rlog.warning('wifi_dead',
                               "Supervisor WiFi: machinery dead (%s) — restart-in-place",
                               reason)
            self._wifi_state = 'restarting'
            self._attempt_wifi_restart(now, 'quick')
            return

        # ── Stall check (frames frozen for WIFI_STALL_WINDOW_S)
        frame_count = inp.get('frame_count', 0)
        if frame_count != self._wifi_last_frame_count:
            # Frames are flowing — healthy
            self._wifi_last_frame_count = frame_count
            self._wifi_stall_since = 0.0
            self._wifi_state = 'healthy'
            self._wifi_backoff.reset()
            # Clear any iwlwifi monitor warning when frames start flowing
            if self._engine._monitor_warning:
                self._engine._monitor_warning = ''
                log.info("Supervisor WiFi: frames flowing — monitor warning cleared")
            return

        # Frames not advancing
        if self._wifi_stall_since == 0.0:
            self._wifi_stall_since = now

        stall_age = now - self._wifi_stall_since
        if stall_age < WIFI_STALL_WINDOW_S:
            # Not yet at the stall threshold — stay in whatever state we're in
            return

        # Stall confirmed — consult the BLE oracle.  _ble_adv_count_at_window_start
        # is owned by _supervise_ble, which runs AFTER _supervise_wifi this tick,
        # so we read last tick's value: "did a BLE ad arrive since the last
        # BLE-healthy tick (~one SUPERVISOR_TICK_S ago)".
        ble_rf_present = (
            ble_inp.get('adv_count', 0) > self._ble_adv_count_at_window_start
            and self._ble_state not in ('down',)
        )

        if ble_rf_present:
            # BLE oracle confirms RF — stall suspected, not idle
            self._rlog.warning('wifi_stall',
                               "Supervisor WiFi: frames frozen %.0fs, BLE oracle "
                               "confirms RF present — restart-in-place",
                               stall_age)
            self._wifi_state = 'restarting'
            self._attempt_wifi_restart(now, 'quick')
        else:
            # Both paths quiet — idle_unverified (never restart)
            self._wifi_state = 'idle_unverified'
            driver = inp.get('driver', '')
            if driver and 'iwlwifi' in driver and not self._engine._monitor_warning:
                hint = (
                    "WARNING: No WiFi frames received and BLE also quiet. "
                    "If using an Intel AX200/AX210 adapter, the firmware may "
                    "be filtering monitor-mode frames. Consider an external USB adapter."
                )
                self._engine._monitor_warning = hint
                log.warning("Supervisor WiFi: %s", hint)

    def _attempt_wifi_restart(self, now: float, mode: str) -> None:
        """Gate a restart against the inter-restart backoff and rolling budget."""
        # Check backoff
        if now < self._wifi_next_restart_at:
            return  # Still cooling off — skip this tick

        # Prune old records outside the rolling window
        while self._wifi_restart_ts and (now - self._wifi_restart_ts[0]) > RESTART_WINDOW_S:
            self._wifi_restart_ts.popleft()

        if len(self._wifi_restart_ts) >= MAX_INPLACE_RESTARTS:
            log.critical(
                "Supervisor WiFi: %d restarts within %.0fs — "
                "escalating to process exit for systemd restart",
                MAX_INPLACE_RESTARTS, RESTART_WINDOW_S,
            )
            self._wifi_state = 'down'
            self._exit_hook(1)
            return

        # The final attempt before the exit threshold does a full reset
        n = len(self._wifi_restart_ts)
        effective_mode = 'full' if n == MAX_INPLACE_RESTARTS - 1 else mode

        log.warning(
            "Supervisor WiFi: restart #%d (mode=%s backoff=%.0fs)",
            n + 1, effective_mode, self._wifi_backoff.current,
        )

        try:
            self._engine.restart_wifi_capture(mode=effective_mode)
        except Exception as exc:
            log.error("Supervisor WiFi: restart_wifi_capture failed: %s", exc)

        # Record this restart
        backoff = self._wifi_backoff.current
        self._wifi_backoff.advance()
        self._wifi_restart_ts.append(now)
        self._wifi_next_restart_at = now + backoff

        # Reset stall tracking so we don't immediately re-trigger
        self._wifi_stall_since = 0.0
        wifi_inp = self._engine.wifi_health_inputs()
        self._wifi_last_frame_count = wifi_inp.get('frame_count', 0)

    # ── BLE supervision ──────────────────────────────────────────────────────

    def _supervise_ble(self, inp: Dict[str, Any], wifi_inp: Dict[str, Any]) -> None:
        now = time.monotonic()
        adv_count = inp.get('adv_count', 0)

        # ── Scanner-thread dead.  Distinguish a scanner that was once running
        # and died (→ down, a real fault) from one that never started because
        # there is no BLE adapter / bleak is absent (→ unavailable, not a fault).
        # scanner_started_at > 0 means _start_scanner succeeded at least once.
        if not inp.get('scanner_alive', True):
            ever_started = inp.get('scanner_started_at', 0.0) > 0.0
            if ever_started:
                if self._ble_state != 'down':
                    log.warning("Supervisor BLE: scanner thread died after running — BLE down")
                self._ble_state = 'down'
                return
            # Never started — only conclude 'unavailable' after a startup grace
            # (the BLE thread may still be coming up on the first few ticks).
            if (now - self._started_at) > BLE_WATCHDOG_INITIAL_S:
                if self._ble_state != 'unavailable':
                    log.info("Supervisor BLE: no BLE adapter/scanner present — BLE unavailable")
                self._ble_state = 'unavailable'
            return

        # ── HCI probe completion (mid-window check — runs before window eval)
        if self._ble_hci_probe_state == 'measuring':
            if (now - self._ble_hci_probe_at) >= HCI_PROBE_DURATION_S:
                rx_now = _read_hci_rx_bytes()
                rx_delta = 0
                if rx_now is not None and self._ble_hci_rx_baseline is not None:
                    rx_delta = rx_now - self._ble_hci_rx_baseline
                self._ble_hci_probe_state = None

                if rx_delta <= 0:
                    # RX bytes did not climb — adapter is wedged without WiFi confirmation
                    log.info(
                        "Supervisor BLE: HCI-RX probe: bytes delta=%d — "
                        "idle_unverified probe rebind",
                        rx_delta,
                    )
                    self._engine.request_ble_rebind('idle_probe')
                else:
                    log.info(
                        "Supervisor BLE: HCI-RX probe: bytes delta=%d — "
                        "adapter active, staying idle_unverified",
                        rx_delta,
                    )

        # ── ads delta since window start
        adv_delta = adv_count - self._ble_adv_count_at_window_start

        if adv_delta > 0:
            # BLE healthy — reset window
            if self._ble_state not in ('healthy', 'idle'):
                log.info("Supervisor BLE: advertisements resumed — watchdog reset")
            self._ble_state = 'healthy'
            self._ble_ever_healthy = True
            self._ble_watchdog_window = BLE_WATCHDOG_FLOOR_S
            self._ble_window_start = now
            self._ble_adv_count_at_window_start = adv_count
            self._wifi_frame_at_ble_window_start = wifi_inp.get('frame_count', 0)
            self._ble_consec_no_ads = 0
            self._ble_hci_probe_state = None
            return

        # ── Window not yet elapsed
        if (now - self._ble_window_start) < self._ble_watchdog_window:
            return

        # ── Window elapsed with zero ads — evaluate oracle.  Compare against the
        # WiFi frame count captured when THIS BLE window opened (≥75s ago at the
        # floor), using our own baseline — NOT _wifi_last_frame_count, which
        # _supervise_wifi advances to the live value earlier in the same tick.
        wifi_frames_climbing = (
            wifi_inp.get('frame_count', 0) > self._wifi_frame_at_ble_window_start
            and self._wifi_state not in ('down', 'idle')
        )

        if wifi_frames_climbing:
            # WiFi oracle confirms RF present — wedge suspected
            self._ble_consec_no_ads += 1
            elapsed = int(now - self._ble_window_start)

            if self._ble_consec_no_ads <= MAX_WEDGE_REBINDS_CONSEC:
                log.warning(
                    "Supervisor BLE: no advertisements for %ds, WiFi oracle "
                    "confirms RF — wedge suspected, rebind #%d",
                    elapsed, self._ble_consec_no_ads,
                )
                self._ble_state = 'restarting'
                self._engine.request_ble_rebind('wedge_suspected')
            else:
                if self._ble_state != 'degraded':
                    log.warning(
                        "Supervisor BLE: %d consecutive rebinds without ad "
                        "resumption — marking BLE degraded, slow-probing",
                        self._ble_consec_no_ads,
                    )
                self._ble_state = 'degraded'
                # Keep slow-probing at cap window
                self._ble_watchdog_window = BLE_WATCHDOG_CAP_S

            # Reset window at floor for next check
            self._ble_window_start = now
            self._ble_adv_count_at_window_start = adv_count
            self._wifi_frame_at_ble_window_start = wifi_inp.get('frame_count', 0)
            if self._ble_state != 'degraded':
                self._ble_watchdog_window = BLE_WATCHDOG_FLOOR_S

        else:
            # Both paths quiet — idle_unverified (never restart)
            self._ble_consec_no_ads = 0  # reset: no evidence of RF
            self._ble_watchdog_window = min(self._ble_watchdog_window * 2.0, BLE_WATCHDOG_CAP_S)
            self._ble_state = 'idle_unverified'
            self._ble_window_start = now
            self._ble_adv_count_at_window_start = adv_count
            self._wifi_frame_at_ble_window_start = wifi_inp.get('frame_count', 0)

            # Initiate HCI probe when newly at the cap
            at_cap = self._ble_watchdog_window >= BLE_WATCHDOG_CAP_S
            if at_cap and self._ble_hci_probe_state is None:
                rx0 = _read_hci_rx_bytes()
                if rx0 is not None:
                    self._ble_hci_rx_baseline = rx0
                    self._ble_hci_probe_at = now
                    self._ble_hci_probe_state = 'measuring'
                    log.info(
                        "Supervisor BLE: at cap window (%.0fs), starting "
                        "HCI-RX probe (baseline=%d bytes)",
                        BLE_WATCHDOG_CAP_S, rx0,
                    )

    # ── Heartbeat ────────────────────────────────────────────────────────────

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if (now - self._heartbeat_at) < HEARTBEAT_INTERVAL_S:
            return
        self._heartbeat_at = now

        wi = self._engine.wifi_health_inputs()
        bi = self._engine.ble_health_inputs()
        fc = wi.get('frame_count', 0)
        ac = bi.get('adv_count', 0)
        fc_delta = fc - getattr(self, '_hb_last_fc', fc)
        ac_delta = ac - getattr(self, '_hb_last_ac', ac)
        self._hb_last_fc = fc
        self._hb_last_ac = ac

        drones = 0
        try:
            drones = len(self._engine.get_active_drones(max_age=60))
        except Exception:
            pass

        log.info(
            "heartbeat: wifi=%s frames=+%d(%d) ble=%s ads=+%d(%d) "
            "drones=%d wifi_restarts=%d ble_resets=%d",
            self._wifi_state, fc_delta, fc,
            self._ble_state, ac_delta, ac,
            drones,
            wi.get('restart_count', 0),
            bi.get('reset_count', 0),
        )

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return a dict suitable for inclusion in /api/v1/health."""
        return {
            'wifi_state': self._wifi_state,
            'ble_state': self._ble_state,
            'supervisor_last_tick': self._supervisor_last_tick,
        }

    def get_thread_ages(self) -> Dict[str, float]:
        """Return age in seconds for supervised threads."""
        now = time.monotonic()
        sup_age = (now - self._supervisor_last_tick) if self._supervisor_last_tick else -1.0
        maint_tick = self._get_maintenance_tick()
        maint_age = (now - maint_tick) if maint_tick else -1.0
        flush_tick = self._get_flush_tick()
        flush_age = (now - flush_tick) if flush_tick else -1.0
        return {
            'supervisor_age_s': round(sup_age, 1),
            'maintenance_age_s': round(maint_age, 1),
            'flush_age_s': round(flush_age, 1),
        }
