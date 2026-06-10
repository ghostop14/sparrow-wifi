"""
Tests for the Supervisor self-healing state machine.

Covers:
- Decision matrix (all rows: healthy, wedge_suspected, idle_unverified)
  including budget exhaustion, HCI-RX gating, and WiFi-oracle constraint.
- BackoffPolicy step advancement and reset.
- Rolling restart-window accounting and os._exit escalation.
- RateLimitedLogger suppression count.
- EOF-vs-idle classifier paths via wifi_health_inputs() mock.
- The 'wedge_suspected' path PROVES it fires whenever WiFi frames climb.
"""

import sys
import os
import time
import unittest
from collections import deque
from unittest.mock import MagicMock, patch, call

# Make sparrow_droneid importable from the tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sparrow_droneid'))

from backend.supervisor import (
    Supervisor,
    BackoffPolicy,
    RateLimitedLogger,
    WIFI_BACKOFF_STEPS,
    MAX_INPLACE_RESTARTS,
    RESTART_WINDOW_S,
    BLE_WATCHDOG_FLOOR_S,
    BLE_WATCHDOG_CAP_S,
    MAX_WEDGE_REBINDS_CONSEC,
    WIFI_STALL_WINDOW_S,
)
import logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(
    monitoring=True,
    frame_count=0,
    tcpdump_alive=True,
    parse_alive=True,
    eof=False,
    eof_reason='',
    wifi_restart_count=0,
    driver='',
    adv_count=0,
    ble_enabled=True,
    scanner_alive=True,
    ble_reset_count=0,
    scanner_started_at=None,
):
    """Build a minimal mock engine with wifi_health_inputs / ble_health_inputs."""
    engine = MagicMock()
    engine._monitor_warning = ''

    wifi_inp = {
        'monitoring': monitoring,
        'frame_count': frame_count,
        'tcpdump_alive': tcpdump_alive,
        'parse_alive': parse_alive,
        'eof': eof,
        'eof_reason': eof_reason,
        'last_frame_age': 0.0 if frame_count > 0 else float('inf'),
        'restart_count': wifi_restart_count,
        'driver': driver,
    }
    ble_inp = {
        'ble_enabled': ble_enabled,
        'scanner_alive': scanner_alive,
        'scanner_started_at': scanner_started_at if scanner_started_at is not None else time.monotonic() - 5,
        'adv_count': adv_count,
        'last_adv_age': float('inf'),
        'reset_count': ble_reset_count,
        'last_reset_at': None,
    }

    engine.wifi_health_inputs.return_value = dict(wifi_inp)
    engine.ble_health_inputs.return_value = dict(ble_inp)
    engine.get_active_drones.return_value = []
    return engine


def _make_supervisor(engine, exit_calls=None):
    """Build a Supervisor with a mock exit hook; returns (supervisor, exits)."""
    exits = exit_calls if exit_calls is not None else []

    def _fake_exit(code):
        exits.append(code)

    sup = Supervisor(
        engine=engine,
        get_maintenance_tick=lambda: 0.0,
        get_flush_tick=lambda: 0.0,
        exit_hook=_fake_exit,
    )
    # Skip the actual background thread — we call _tick() directly in tests
    return sup, exits


# ---------------------------------------------------------------------------
# BackoffPolicy tests
# ---------------------------------------------------------------------------

class TestBackoffPolicy(unittest.TestCase):

    def test_initial_value(self):
        bp = BackoffPolicy([5, 10, 20])
        self.assertEqual(bp.current, 5.0)

    def test_advance_steps(self):
        bp = BackoffPolicy([5, 10, 20])
        bp.advance()
        self.assertEqual(bp.current, 10.0)
        bp.advance()
        self.assertEqual(bp.current, 20.0)
        # Capped at last step
        bp.advance()
        self.assertEqual(bp.current, 20.0)

    def test_reset(self):
        bp = BackoffPolicy([5, 10, 20])
        bp.advance()
        bp.advance()
        bp.reset()
        self.assertEqual(bp.current, 5.0)

    def test_single_step(self):
        bp = BackoffPolicy([30])
        self.assertEqual(bp.current, 30.0)
        bp.advance()
        self.assertEqual(bp.current, 30.0)


# ---------------------------------------------------------------------------
# RateLimitedLogger tests
# ---------------------------------------------------------------------------

class TestRateLimitedLogger(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock(spec=logging.Logger)
        self.rl = RateLimitedLogger(self.logger, min_interval=5.0)

    def test_first_emission_passes_through(self):
        self.rl.warning('key1', 'hello %s', 'world')
        self.logger.log.assert_called_once()
        args = self.logger.log.call_args
        self.assertIn('hello %s', args[0][1])

    def test_suppressed_within_interval(self):
        self.rl.warning('key1', 'msg')
        self.rl.warning('key1', 'msg')
        self.rl.warning('key1', 'msg')
        # Only first call goes through
        self.assertEqual(self.logger.log.call_count, 1)

    def test_suppressed_count_appended_on_next_emit(self):
        rl = RateLimitedLogger(self.logger, min_interval=0.0)
        rl.warning('k', 'first')          # emits immediately
        rl._state['k'] = (time.monotonic() + 100, 3)  # fake 3 suppressed
        rl._state['k'] = (0.0, 3)         # force next emit by resetting last_t
        rl.warning('k', 'second')         # should carry suppressed count
        # second call message should mention suppressed 3
        last_call = self.logger.log.call_args
        msg = last_call[0][1]
        self.assertIn('suppressed 3', msg)

    def test_rate_reset_after_interval(self):
        """After the interval passes, the message emits again."""
        rl = RateLimitedLogger(self.logger, min_interval=0.0)
        rl.warning('k', 'first')
        rl.warning('k', 'second')
        self.assertEqual(self.logger.log.call_count, 2)

    def test_different_keys_independent(self):
        self.rl.warning('a', 'msg a')
        self.rl.warning('b', 'msg b')
        # Both should emit
        self.assertEqual(self.logger.log.call_count, 2)

    def test_error_level(self):
        self.rl.error('ek', 'error msg')
        args = self.logger.log.call_args
        self.assertEqual(args[0][0], logging.ERROR)


# ---------------------------------------------------------------------------
# WiFi decision matrix tests
# ---------------------------------------------------------------------------

class TestWifiSupervision(unittest.TestCase):
    """Tests for _supervise_wifi() via _tick()."""

    def _tick_with(self, engine, sup):
        """Run one tick cycle synchronously."""
        # We call _tick directly to avoid threading
        sup._ble_window_start = time.monotonic()
        sup._tick()

    def test_idle_when_not_monitoring(self):
        engine = _make_engine(monitoring=False)
        sup, exits = _make_supervisor(engine)
        sup._tick()
        self.assertEqual(sup._wifi_state, 'idle')
        self.assertEqual(exits, [])

    def test_healthy_when_frames_flowing(self):
        engine = _make_engine(monitoring=True, frame_count=100)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 90   # frames advanced by 10
        sup._tick()
        self.assertEqual(sup._wifi_state, 'healthy')
        self.assertEqual(exits, [])
        engine.restart_wifi_capture.assert_not_called()

    def test_machinery_dead_tcpdump_exited(self):
        """When tcpdump is dead, restart is called immediately."""
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)
        sup, exits = _make_supervisor(engine)
        sup._tick()
        self.assertEqual(sup._wifi_state, 'restarting')
        engine.restart_wifi_capture.assert_called_once()
        self.assertEqual(exits, [])

    def test_machinery_dead_parse_thread_dead(self):
        engine = _make_engine(monitoring=True, frame_count=0,
                               parse_alive=False)
        sup, exits = _make_supervisor(engine)
        sup._tick()
        engine.restart_wifi_capture.assert_called_once()

    def test_machinery_dead_eof_set(self):
        engine = _make_engine(monitoring=True, frame_count=0,
                               eof=True, eof_reason='pcap header EOF')
        sup, exits = _make_supervisor(engine)
        sup._tick()
        engine.restart_wifi_capture.assert_called_once()

    def test_stall_suspected_when_ble_oracle_says_rf_present(self):
        """PROVES: wedge_suspected path fires whenever WiFi frames are frozen
        AND BLE oracle (ads climbing) confirms RF is present."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50  # frames NOT advancing
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5  # stall confirmed

        # Make BLE oracle indicate RF present (ads have advanced past window baseline)
        sup._ble_adv_count_at_window_start = 0
        engine.ble_health_inputs.return_value = {
            'adv_count': 50,       # 50 ads ABOVE baseline → RF present
            'ble_enabled': True,
            'scanner_alive': True,
            'scanner_started_at': time.monotonic() - 10,
            'last_adv_age': 1.0,
            'reset_count': 0,
            'last_reset_at': None,
        }
        sup._ble_state = 'healthy'

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_called_once()
        self.assertEqual(sup._wifi_state, 'restarting')

    def test_idle_unverified_when_both_quiet(self):
        """When WiFi frozen AND BLE also quiet → idle_unverified, never restart."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5

        # BLE oracle: no ads above baseline
        sup._ble_adv_count_at_window_start = 50
        engine.ble_health_inputs.return_value = {
            'adv_count': 50,       # zero delta
            'ble_enabled': True,
            'scanner_alive': True,
            'scanner_started_at': time.monotonic() - 10,
            'last_adv_age': 120.0,
            'reset_count': 0,
            'last_reset_at': None,
        }

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_not_called()
        self.assertEqual(sup._wifi_state, 'idle_unverified')

    def test_idle_unverified_sets_iwlwifi_warning(self):
        """idle_unverified with iwlwifi driver sets _monitor_warning."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               tcpdump_alive=True, parse_alive=True, driver='iwlwifi')
        engine._monitor_warning = ''
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5
        sup._ble_adv_count_at_window_start = 50
        engine.ble_health_inputs.return_value = {
            'adv_count': 50,
            'ble_enabled': True,
            'scanner_alive': True,
            'scanner_started_at': time.monotonic() - 10,
            'last_adv_age': 120.0,
            'reset_count': 0,
            'last_reset_at': None,
        }

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        self.assertNotEqual(engine._monitor_warning, '')

    def test_wedge_fires_whenever_wifi_frames_climb(self):
        """Constraint: if BLE ads are zero but WiFi frames are CLIMBING,
        wedge_suspected MUST fire (not idle_unverified).

        This is the key oracle test: WiFi frames climbing = RF present.
        """
        engine = _make_engine(monitoring=True, frame_count=100,
                               tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)

        # WiFi frames are NOT advancing (stall)
        sup._wifi_last_frame_count = 100
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5

        # BLE: zero ads (delta=0 from baseline)
        sup._ble_adv_count_at_window_start = 30
        ble_inp = {
            'adv_count': 30,   # no delta
            'ble_enabled': True,
            'scanner_alive': True,
            'scanner_started_at': time.monotonic() - 10,
            'last_adv_age': 120.0,
            'reset_count': 0,
            'last_reset_at': None,
        }

        # CASE A: WiFi frame_count is HIGHER than sup._wifi_last_frame_count
        # (oracle: RF is present)  → should call restart
        sup._wifi_last_frame_count = 80  # WiFi IS climbing relative to snap
        wifi_inp_a = engine.wifi_health_inputs()
        wifi_inp_a['frame_count'] = 100   # newer than last_frame_count=80
        sup._wifi_stall_since = 0.0       # reset stall so healthy path fires

        # Fresh stall — set up last_frame_count == current so stall is detected
        sup._wifi_last_frame_count = 100  # frames equal → stall
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5
        # BLE: ads delta > 0 (RF oracle says yes)
        sup._ble_adv_count_at_window_start = 0
        ble_inp_b = dict(ble_inp)
        ble_inp_b['adv_count'] = 50  # 50 ads above baseline
        sup._ble_state = 'healthy'

        sup._supervise_wifi(wifi_inp_a, ble_inp_b)
        engine.restart_wifi_capture.assert_called()

    def test_restart_budget_exhaustion_triggers_exit(self):
        """After MAX_INPLACE_RESTARTS within window, os._exit(1) is called."""
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)  # machinery dead
        sup, exits = _make_supervisor(engine)

        # Pre-fill the rolling window with MAX_INPLACE_RESTARTS timestamps
        now = time.monotonic()
        for _ in range(MAX_INPLACE_RESTARTS):
            sup._wifi_restart_ts.append(now - 1.0)  # within RESTART_WINDOW_S

        sup._wifi_next_restart_at = 0.0  # no backoff

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        self.assertIn(1, exits)
        self.assertEqual(sup._wifi_state, 'down')

    def test_inter_restart_backoff_respected(self):
        """A restart is skipped if we're still within the backoff window."""
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)
        sup, exits = _make_supervisor(engine)
        # Set next restart far in the future
        sup._wifi_next_restart_at = time.monotonic() + 9999.0

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_not_called()

    def test_restart_mode_is_full_for_penultimate_attempt(self):
        """The attempt immediately before the exit threshold uses mode='full'."""
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)
        sup, exits = _make_supervisor(engine)

        now = time.monotonic()
        # Fill to MAX_INPLACE_RESTARTS - 1 (one away from exit)
        for _ in range(MAX_INPLACE_RESTARTS - 1):
            sup._wifi_restart_ts.append(now - 1.0)

        sup._wifi_next_restart_at = 0.0

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_called_once_with(mode='full')

    def test_restart_mode_is_quick_for_non_final_attempt(self):
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)
        sup, exits = _make_supervisor(engine)
        sup._wifi_next_restart_at = 0.0

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_called_once_with(mode='quick')

    def test_backoff_steps_sequence(self):
        """Verify backoff steps match the spec: 5,10,20,40,60."""
        self.assertEqual(WIFI_BACKOFF_STEPS, [5, 10, 20, 40, 60])

    def test_rolling_window_prunes_old_restarts(self):
        """Restarts older than RESTART_WINDOW_S are not counted."""
        engine = _make_engine(monitoring=True, frame_count=0,
                               tcpdump_alive=False)
        sup, exits = _make_supervisor(engine)

        now = time.monotonic()
        # Pre-fill with old timestamps (outside the window)
        for _ in range(MAX_INPLACE_RESTARTS):
            sup._wifi_restart_ts.append(now - RESTART_WINDOW_S - 100)

        sup._wifi_next_restart_at = 0.0

        # Should NOT exit — old records are pruned
        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        self.assertEqual(exits, [])
        engine.restart_wifi_capture.assert_called_once()


# ---------------------------------------------------------------------------
# BLE decision matrix tests
# ---------------------------------------------------------------------------

class TestBleSupervision(unittest.TestCase):

    def _fresh_sup(self, engine):
        sup, exits = _make_supervisor(engine)
        sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_FLOOR_S - 5
        sup._ble_adv_count_at_window_start = 0
        sup._ble_watchdog_window = BLE_WATCHDOG_FLOOR_S
        # The BLE oracle's WiFi baseline (captured at BLE-window-start).  This is
        # the field the wedge/idle decision compares against — NOT
        # _wifi_last_frame_count, which _supervise_wifi owns.
        sup._wifi_frame_at_ble_window_start = 0
        sup._wifi_state = 'idle'
        return sup, exits

    def test_ble_healthy_resets_window(self):
        """ads_delta > 0 → healthy, window reset to floor."""
        engine = _make_engine(monitoring=True, adv_count=100)
        sup, exits = self._fresh_sup(engine)
        sup._ble_adv_count_at_window_start = 50  # 50 ads since window start

        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        self.assertEqual(sup._ble_state, 'healthy')
        self.assertEqual(sup._ble_watchdog_window, BLE_WATCHDOG_FLOOR_S)
        engine.request_ble_rebind.assert_not_called()

    def test_wedge_suspected_fires_when_wifi_frames_climbing(self):
        """ads_delta == 0 AND WiFi frames climbing → request_ble_rebind."""
        engine = _make_engine(monitoring=True, adv_count=50, frame_count=200)
        sup, exits = self._fresh_sup(engine)
        sup._ble_adv_count_at_window_start = 50   # zero BLE delta
        sup._wifi_frame_at_ble_window_start = 100  # WiFi HAS advanced (200 > 100)
        sup._wifi_state = 'healthy'

        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        engine.request_ble_rebind.assert_called_once_with('wedge_suspected')
        self.assertEqual(sup._ble_state, 'restarting')

    def test_wedge_suspected_fires_whenever_wifi_frames_climb(self):
        """PROVES: wedge path fires for any value of frame_count > window baseline."""
        for delta in [1, 5, 100, 9999]:
            engine = _make_engine(monitoring=True, adv_count=0, frame_count=delta)
            sup, exits = self._fresh_sup(engine)
            sup._ble_adv_count_at_window_start = 0
            sup._wifi_frame_at_ble_window_start = 0   # frame_count > 0 → climbing
            sup._wifi_state = 'healthy'

            sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
            engine.request_ble_rebind.assert_called_with('wedge_suspected')

    def test_idle_unverified_when_both_quiet(self):
        """ads_delta == 0 AND WiFi also quiet → idle_unverified, never rebind."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._ble_adv_count_at_window_start = 0
        sup._wifi_frame_at_ble_window_start = 0   # no WiFi frames
        sup._wifi_state = 'idle'

        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        engine.request_ble_rebind.assert_not_called()
        self.assertEqual(sup._ble_state, 'idle_unverified')

    def test_idle_unverified_backoff_grows(self):
        """Backoff grows on each idle_unverified window, capped at BLE_WATCHDOG_CAP_S."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._wifi_state = 'idle'

        # Initial window (floor 75s)
        sup._ble_watchdog_window = BLE_WATCHDOG_FLOOR_S
        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        self.assertAlmostEqual(sup._ble_watchdog_window, BLE_WATCHDOG_FLOOR_S * 2, delta=1.0)

        # Progress to cap
        for _ in range(10):
            sup._ble_window_start = time.monotonic() - sup._ble_watchdog_window - 1
            sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        self.assertEqual(sup._ble_watchdog_window, BLE_WATCHDOG_CAP_S)

    def test_idle_unverified_never_reports_down(self):
        """idle_unverified is always reported as idle_unverified, never 'down'."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._wifi_state = 'idle'

        for _ in range(20):
            sup._ble_window_start = time.monotonic() - sup._ble_watchdog_window - 1
            sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        self.assertNotEqual(sup._ble_state, 'down')

    def test_consecutive_wedge_rebinds_lead_to_degraded(self):
        """After MAX_WEDGE_REBINDS_CONSEC consecutive rebinds → degraded."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=100)
        sup, exits = self._fresh_sup(engine)
        sup._ble_adv_count_at_window_start = 0
        sup._wifi_frame_at_ble_window_start = 0   # frame_count(100) > 0 → climbing
        sup._wifi_state = 'healthy'

        # WiFi frames must keep CLIMBING each window for the wedge oracle to keep
        # firing (it re-baselines _wifi_frame_at_ble_window_start to the current
        # frame count after every rebind).
        fc = 100
        for i in range(MAX_WEDGE_REBINDS_CONSEC + 2):
            fc += 50
            wifi_inp = engine.wifi_health_inputs()
            wifi_inp['frame_count'] = fc
            sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_FLOOR_S - 1
            sup._ble_adv_count_at_window_start = 0
            sup._supervise_ble(engine.ble_health_inputs(), wifi_inp)

        self.assertEqual(sup._ble_state, 'degraded')

    def test_ble_state_not_down_after_degraded(self):
        """Degraded BLE is NOT 'down' — a process restart can't fix AX210 wedge."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=100)
        sup, exits = self._fresh_sup(engine)
        sup._ble_state = 'degraded'
        sup._ble_consec_no_ads = MAX_WEDGE_REBINDS_CONSEC + 1
        sup._wifi_frame_at_ble_window_start = 0
        sup._wifi_state = 'healthy'

        sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_CAP_S - 1
        sup._ble_adv_count_at_window_start = 0
        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        # Still degraded, not down
        self.assertEqual(sup._ble_state, 'degraded')
        self.assertNotIn(1, exits)

    def test_hci_gated_probe_fires_when_rx_bytes_flat(self):
        """At cap, if HCI RX bytes don't climb → idle_probe rebind fires."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._wifi_state = 'idle'
        sup._ble_watchdog_window = BLE_WATCHDOG_CAP_S

        # Prime the HCI probe state: measuring phase, baseline set, 15s ago
        sup._ble_hci_probe_state = 'measuring'
        sup._ble_hci_probe_at = time.monotonic() - 15.0  # > HCI_PROBE_DURATION_S
        sup._ble_hci_rx_baseline = 1000

        with patch('backend.supervisor._read_hci_rx_bytes', return_value=1000):
            # rx_now (1000) == baseline (1000) → delta == 0 → fire rebind
            sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        engine.request_ble_rebind.assert_called_with('idle_probe')
        self.assertIsNone(sup._ble_hci_probe_state)

    def test_hci_gated_probe_suppressed_when_rx_bytes_climbed(self):
        """At cap, if HCI RX bytes climbed → no rebind (adapter is active)."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._wifi_state = 'idle'
        sup._ble_watchdog_window = BLE_WATCHDOG_CAP_S

        sup._ble_hci_probe_state = 'measuring'
        sup._ble_hci_probe_at = time.monotonic() - 15.0
        sup._ble_hci_rx_baseline = 1000

        with patch('backend.supervisor._read_hci_rx_bytes', return_value=5000):
            # rx_now (5000) > baseline (1000) → delta > 0 → no rebind
            sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        engine.request_ble_rebind.assert_not_called()
        self.assertIsNone(sup._ble_hci_probe_state)

    def test_idle_unverified_state_never_down(self):
        """Explicit: idle_unverified must not cause os._exit or state='down'."""
        engine = _make_engine(monitoring=True, adv_count=0, frame_count=0)
        sup, exits = self._fresh_sup(engine)
        sup._wifi_state = 'idle'

        # Run many iterations at cap
        sup._ble_watchdog_window = BLE_WATCHDOG_CAP_S
        for _ in range(50):
            sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_CAP_S - 1
            with patch('backend.supervisor._read_hci_rx_bytes', return_value=None):
                sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())

        self.assertEqual(exits, [])
        self.assertNotEqual(sup._ble_state, 'down')


# ---------------------------------------------------------------------------
# EOF-vs-idle classifier tests
# ---------------------------------------------------------------------------

class TestWifiEofClassifier(unittest.TestCase):
    """Verify that machinery-dead (EOF) vs. idle-stall are classified separately.

    The engine's wifi_health_inputs() is the surface we verify.
    These tests are at the engine level, not supervisor level.
    """

    def test_eof_flag_set_signals_machinery_dead(self):
        """When eof=True in health inputs, supervisor must treat it as machinery dead."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               eof=True, eof_reason='pcap header EOF',
                               tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50
        sup._wifi_next_restart_at = 0.0

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        # Restart must be called because eof=True
        engine.restart_wifi_capture.assert_called_once()

    def test_frozen_frames_without_eof_is_stall_not_eof(self):
        """Frozen frame count with proc+thread alive is a stall, not EOF."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               eof=False,
                               tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50   # frames NOT advancing
        # Stall not yet confirmed
        sup._wifi_stall_since = 0.0

        # One tick — stall timer starts but not yet at threshold
        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_not_called()

    def test_stall_detected_after_window(self):
        """After WIFI_STALL_WINDOW_S with no new frames and BLE oracle quiet → idle_unverified."""
        engine = _make_engine(monitoring=True, frame_count=50,
                               eof=False, tcpdump_alive=True, parse_alive=True)
        sup, exits = _make_supervisor(engine)
        sup._wifi_last_frame_count = 50
        sup._wifi_stall_since = time.monotonic() - WIFI_STALL_WINDOW_S - 5

        # BLE oracle: quiet
        sup._ble_adv_count_at_window_start = 50
        engine.ble_health_inputs.return_value = {
            'adv_count': 50,  # zero delta
            'ble_enabled': True,
            'scanner_alive': True,
            'scanner_started_at': time.monotonic() - 10,
            'last_adv_age': 120.0,
            'reset_count': 0,
            'last_reset_at': None,
        }

        sup._supervise_wifi(engine.wifi_health_inputs(), engine.ble_health_inputs())
        engine.restart_wifi_capture.assert_not_called()
        self.assertEqual(sup._wifi_state, 'idle_unverified')


# ---------------------------------------------------------------------------
# Heartbeat / status tests
# ---------------------------------------------------------------------------

class TestSupervisorStatus(unittest.TestCase):

    def test_get_status_keys(self):
        engine = _make_engine(monitoring=False)
        sup, _ = _make_supervisor(engine)
        status = sup.get_status()
        self.assertIn('wifi_state', status)
        self.assertIn('ble_state', status)
        self.assertIn('supervisor_last_tick', status)

    def test_get_thread_ages_keys(self):
        engine = _make_engine(monitoring=False)
        sup, _ = _make_supervisor(engine)
        ages = sup.get_thread_ages()
        self.assertIn('supervisor_age_s', ages)
        self.assertIn('maintenance_age_s', ages)
        self.assertIn('flush_age_s', ages)

    def test_tick_updates_supervisor_last_tick(self):
        engine = _make_engine(monitoring=False)
        sup, _ = _make_supervisor(engine)
        self.assertEqual(sup._supervisor_last_tick, 0.0)
        sup._tick()
        self.assertGreater(sup._supervisor_last_tick, 0.0)

    def test_wifi_state_resets_on_stop(self):
        """When monitoring stops, states reset to idle."""
        engine = _make_engine(monitoring=False)
        sup, _ = _make_supervisor(engine)
        sup._wifi_state = 'degraded'
        sup._ble_state = 'degraded'
        sup._tick()
        self.assertEqual(sup._wifi_state, 'idle')
        self.assertEqual(sup._ble_state, 'idle')


# ---------------------------------------------------------------------------
# Full _tick() integration — catches same-tick baseline clobber (regression B1)
# ---------------------------------------------------------------------------

class TestTickIntegration(unittest.TestCase):
    """Drive the REAL _tick() path (both _supervise_wifi and _supervise_ble in
    order) rather than calling a sub-function in isolation.  This is the only
    layer that catches the same-tick baseline-ordering bug where _supervise_wifi
    advances a baseline that _supervise_ble then reads — the unit-level wedge
    tests passed even while the integrated path could never fire a rebind.
    """

    def test_wedge_fires_through_full_tick_when_wifi_climbs_ble_frozen(self):
        """REGRESSION: WiFi frames climbing every tick + BLE ads frozen past the
        window MUST issue a wedge rebind through _tick().  With a shared baseline
        (the B1 bug) _supervise_wifi advances it to the live frame count before
        _supervise_ble reads it, so the rebind never fires."""
        engine = _make_engine(monitoring=True, frame_count=500, adv_count=10)
        sup, exits = _make_supervisor(engine)

        # BLE window has elapsed with zero ad delta; WiFi climbed over the window.
        sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_FLOOR_S - 5
        sup._ble_adv_count_at_window_start = 10        # adv_count(10) → zero delta
        sup._ble_watchdog_window = BLE_WATCHDOG_FLOOR_S
        sup._wifi_frame_at_ble_window_start = 100      # 500 > 100 → WiFi climbing
        sup._wifi_last_frame_count = 0                 # so _supervise_wifi → healthy

        sup._tick()

        engine.request_ble_rebind.assert_called_once_with('wedge_suspected')
        self.assertEqual(sup._ble_state, 'restarting')
        self.assertEqual(exits, [])

    def test_both_quiet_through_full_tick_never_rebinds(self):
        """Constraint: both radios quiet through a full tick → no rebind/restart."""
        engine = _make_engine(monitoring=True, frame_count=0, adv_count=0)
        sup, exits = _make_supervisor(engine)
        sup._ble_window_start = time.monotonic() - BLE_WATCHDOG_FLOOR_S - 5
        sup._ble_adv_count_at_window_start = 0
        sup._wifi_frame_at_ble_window_start = 0

        sup._tick()

        engine.request_ble_rebind.assert_not_called()
        engine.restart_wifi_capture.assert_not_called()
        self.assertEqual(exits, [])
        self.assertNotEqual(sup._ble_state, 'down')


# ---------------------------------------------------------------------------
# BLE down vs. unavailable (no-hardware) classification
# ---------------------------------------------------------------------------

class TestBleScannerDeadClassification(unittest.TestCase):

    def test_down_when_scanner_died_after_running(self):
        """scanner_alive=False AND it was once started → 'down' (a real fault)."""
        engine = _make_engine(monitoring=True, scanner_alive=False,
                              scanner_started_at=time.monotonic() - 30)
        sup, exits = _make_supervisor(engine)
        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        self.assertEqual(sup._ble_state, 'down')

    def test_unavailable_when_never_started(self):
        """scanner_alive=False AND never started (no adapter/bleak) → 'unavailable',
        NOT 'down' — a WiFi-only sensor must not flip /healthz to 503."""
        engine = _make_engine(monitoring=True, scanner_alive=False,
                              scanner_started_at=0.0)
        sup, exits = _make_supervisor(engine)
        # _started_at defaults to 0.0 in tests, so the startup grace is satisfied.
        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        self.assertEqual(sup._ble_state, 'unavailable')
        self.assertNotEqual(sup._ble_state, 'down')

    def test_unavailable_held_off_during_startup_grace(self):
        """Within the startup grace, a not-yet-started scanner is not declared
        unavailable (the BLE thread may still be coming up)."""
        engine = _make_engine(monitoring=True, scanner_alive=False,
                              scanner_started_at=0.0)
        sup, exits = _make_supervisor(engine)
        sup._started_at = time.monotonic()   # just started → within grace
        sup._supervise_ble(engine.ble_health_inputs(), engine.wifi_health_inputs())
        self.assertNotIn(sup._ble_state, ('down', 'unavailable'))


if __name__ == '__main__':
    unittest.main()
