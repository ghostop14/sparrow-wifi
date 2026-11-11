"""TakEngine: lifecycle diffing, scheduling and stop behaviour (fake transport + clock)."""
import os
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from tak_support import (FakeClock, FakeEnrollment, FakeGps, FakeTransport,  # noqa: E402
                         default_cfg, iso_z, make_device)

from backend.tak.engine import TakEngine  # noqa: E402

PFX = 'sparrow-droneid-rx1-drone-'


def parse(payload):
    return ET.fromstring(payload)


class EngineCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.drones = []
        self.gps = FakeGps()
        FakeTransport.instances = []

    def make(self, start_threads=False, **cfg_kw):
        eng = TakEngine(cert_dir=os.path.join(self.tmp.name, 'c'),
                        drone_source=lambda: list(self.drones), gps_engine=self.gps,
                        monotonic=self.clock.monotonic, wallclock=self.clock.wallclock,
                        transport_factory=FakeTransport, enrollment_factory=FakeEnrollment,
                        start_threads=start_threads)
        cfg_kw.setdefault('cot_enabled', True)
        cfg_kw.setdefault('tak_station_name', 'rx1')
        cfg_kw.setdefault('tak_sensor_enabled', False)
        eng.configure(default_cfg(**cfg_kw))
        self.addCleanup(eng.stop)
        return eng

    @property
    def tr(self):
        return FakeTransport.instances[-1]

    def add(self, key='1581F5FHD23450019', **kw):
        dev = make_device(self.clock, **kw)
        self.drones.append((key, dev))
        return dev

    def touch(self, dev):
        dev.last_seen = iso_z(self.clock.wallclock())

    def batch_kinds(self, batch):
        return [(k, u.replace(PFX, '')) for k, u, _p in batch]


class TestScheduling(EngineCase):

    def test_new_drone_sends_drone_operator_line_in_one_batch(self):
        eng = self.make()
        self.add()
        eng.tick()
        self.assertEqual(len(self.tr.batches), 1)
        self.assertEqual(self.batch_kinds(self.tr.last_batch()),
                         [('update', '1581F5FHD23450019'), ('update', '1581F5FHD23450019-op'),
                          ('update', '1581F5FHD23450019-line')])
        types = [parse(p).get('type') for _k, _u, p in self.tr.last_batch()]
        self.assertEqual(types, ['a-u-A-M-F-Q-r', 'a-u-G', 'u-d-f'])

    def test_second_tick_inside_send_interval_sends_nothing(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        self.clock.advance(1)
        self.touch(dev)
        eng.tick()
        self.assertEqual(len(self.tr.batches), 1)

    def test_new_frame_after_interval_updates(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        self.clock.advance(2)
        self.touch(dev)
        eng.tick()
        self.assertEqual(len(self.tr.batches), 2)
        self.assertEqual(len(self.tr.last_batch()), 3)

    def test_unchanged_drone_refreshes_every_10s_until_stale_then_nothing(self):
        eng = self.make()
        self.add()
        eng.tick()                                   # t=0, stale at 30
        sent = []
        for t in range(1, 41):
            self.clock.advance(1)
            before = len(self.tr.batches)
            eng.tick()
            if len(self.tr.batches) > before:
                sent.append(t)
        self.assertEqual(sent, [10, 20])             # refreshes at 10 s and 20 s; t=30 is stale
        n = len(self.tr.batches)
        for _ in range(60):
            self.clock.advance(1)
            eng.tick()
        self.assertEqual(len(self.tr.batches), n)    # nothing after the anchored stale time

    def test_stale_anchored_to_last_seen_not_to_send_time(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        self.clock.advance(10)
        eng.tick()                                   # refresh, last_seen unchanged
        stale = parse(self.tr.last_batch()[0][2]).get('stale')
        self.assertEqual(stale, '2026-11-11T14:00:30.000Z')   # last_seen(14:00:00) + 30 s

    def test_never_drawn_stale_drone_is_never_introduced(self):
        eng = self.make()
        self.add(last_seen=iso_z(self.clock.wallclock() - __import__('datetime').timedelta(seconds=45)))
        eng.tick()
        eng.tick()
        self.assertEqual(self.tr.batches, [])

    def test_delete_once_when_age_exceeds_delete_after_with_last_types(self):
        eng = self.make()
        self.add()
        eng.tick()
        self.clock.advance(180)
        eng.tick()                                   # age == 180: not yet
        self.assertFalse([k for k, _u, _p in self.tr.last_batch() if k == 'delete'])
        self.clock.advance(1)
        eng.tick()
        deletes = [(u, p) for _t, b in self.tr.batches for k, u, p in b if k == 'delete']
        self.assertEqual(sorted(u.replace(PFX, '') for u, _ in deletes),
                         ['1581F5FHD23450019', '1581F5FHD23450019-line', '1581F5FHD23450019-op'])
        types = {u.replace(PFX, ''): parse(p).find('detail/link').get('type') for u, p in deletes}
        self.assertEqual(types['1581F5FHD23450019'], 'a-u-A-M-F-Q-r')
        self.assertEqual(types['1581F5FHD23450019-op'], 'a-u-G')
        self.assertEqual(types['1581F5FHD23450019-line'], 'u-d-f')
        for _u, p in deletes:
            self.assertIsNotNone(parse(p).find('detail/__forcedelete'))
        for _ in range(5):                           # exactly once
            self.clock.advance(1)
            eng.tick()
        again = [u for _t, b in self.tr.batches for k, u, _p in b if k == 'delete']
        self.assertEqual(len(again), 3)
        self.assertEqual(eng.get_status()['deletes_sent'], 3)

    def test_key_migration_deletes_old_uids_first_then_new_updates(self):
        eng = self.make()
        mac = 'aa:bb:cc:dd:ee:ff'
        dev = self.add(key=mac, serial_number='', mac_address=mac)
        eng.tick()
        self.clock.advance(1)
        self.drones[:] = []
        new = self.add(key='1581F5FHD23450019', serial_number='1581F5FHD23450019', mac_address=mac)
        self.touch(new)
        eng.tick()
        batch = self.tr.last_batch()
        self.assertEqual([k for k, _u, _p in batch], ['delete'] * 3 + ['update'] * 3)
        self.assertEqual({u for k, u, _p in batch if k == 'delete'},
                         {PFX + 'aa_bb_cc_dd_ee_ff', PFX + 'aa_bb_cc_dd_ee_ff-op',
                          PFX + 'aa_bb_cc_dd_ee_ff-line'})
        self.assertEqual({u for k, u, _p in batch if k == 'update'},
                         {PFX + '1581F5FHD23450019', PFX + '1581F5FHD23450019-op',
                          PFX + '1581F5FHD23450019-line'})

    def test_disposition_change_is_immediate_and_delete_uses_new_type(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        self.clock.advance(0.5)                      # far inside the send interval
        dev.disposition = 'threat'
        eng.tick()
        self.assertEqual(len(self.tr.batches), 2)
        self.assertEqual(parse(self.tr.last_batch()[0][2]).get('type'), 'a-h-A-M-F-Q-r')
        self.assertEqual(parse(self.tr.last_batch()[1][2]).get('type'), 'a-h-G')
        self.drones[:] = []
        self.clock.advance(1)
        eng.tick()
        types = [parse(p).find('detail/link').get('type') for k, _u, p in self.tr.last_batch()
                 if k == 'delete']
        self.assertEqual(sorted(types), ['a-h-A-M-F-Q-r', 'a-h-G', 'u-d-f'])

    def test_flag_change_is_immediate_but_keeps_the_type(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        dev.military = True
        eng.tick()
        self.assertEqual(len(self.tr.batches), 2)
        self.assertEqual(parse(self.tr.last_batch()[0][2]).get('type'), 'a-u-A-M-F-Q-r')

    def test_no_operator_position_means_no_op_or_line(self):
        eng = self.make()
        self.add(operator_lat=0.0, operator_lon=0.0)
        eng.tick()
        self.assertEqual(len(self.tr.last_batch()), 1)

    def test_takeoff_only_draws_home_point(self):
        eng = self.make()
        self.add(operator_lat=0.0, operator_lon=0.0, takeoff_lat=48.85, takeoff_lon=2.35)
        eng.tick()
        batch = self.tr.last_batch()
        self.assertEqual(len(batch), 3)
        self.assertEqual(parse(batch[1][2]).find('detail/contact').get('callsign'), 'HOME-450019')

    def test_operator_disabled_and_line_disabled(self):
        eng = self.make(tak_operator_enabled=False)
        self.add()
        eng.tick()
        self.assertEqual(len(self.tr.last_batch()), 1)
        eng2 = self.make(tak_line_enabled=False)
        eng2.tick()
        self.assertEqual([parse(p).get('type') for _k, _u, p in self.tr.last_batch()],
                         ['a-u-A-M-F-Q-r', 'a-u-G'])

    def test_operator_going_away_deletes_op_and_line(self):
        eng = self.make()
        dev = self.add()
        eng.tick()
        self.clock.advance(2)
        dev.operator_lat = dev.operator_lon = 0.0
        self.touch(dev)
        eng.tick()
        batch = self.tr.last_batch()
        self.assertEqual([k for k, _u, _p in batch], ['delete', 'delete', 'update'])

    def test_ssid_detections_gated(self):
        eng = self.make()
        self.add(protocol='wifi_ssid')
        eng.tick()
        self.assertEqual(self.tr.batches, [])
        eng2 = self.make(tak_include_ssid_detections=True)
        eng2.tick()
        self.assertEqual(len(self.tr.last_batch()), 3)
        self.assertIn('500.0', parse(self.tr.last_batch()[0][2]).find('point').get('ce'))

    def test_drone_at_zero_zero_is_skipped(self):
        eng = self.make()
        self.add(drone_lat=0.0, drone_lon=0.0)
        eng.tick()
        self.assertEqual(self.tr.batches, [])

    def test_unparsable_last_seen_skipped(self):
        eng = self.make()
        self.add(last_seen='not a time')
        eng.tick()
        self.assertEqual(self.tr.batches, [])
        self.assertEqual(eng.get_status()['items']['drones'], 0)

    def test_reconnect_epoch_resends_everything_desired(self):
        eng = self.make(tak_sensor_enabled=True)
        self.add()
        eng.tick()
        n = len(self.tr.batches)
        self.assertEqual(len(self.tr.last_batch()), 4)        # drone, op, line, sensor
        self.clock.advance(1)
        eng.tick()
        self.assertEqual(len(self.tr.batches), n)             # steady state: nothing
        self.tr.connection_epoch = 2
        self.clock.advance(1)
        eng.tick()
        self.assertEqual(len(self.tr.batches), n + 1)
        self.assertEqual(len(self.tr.last_batch()), 4)        # everything again, sensor included

    def test_reconnect_does_not_introduce_stale_drones(self):
        eng = self.make()
        self.add()
        eng.tick()
        self.clock.advance(35)                                # stale but still drawn
        self.tr.connection_epoch = 2
        eng.tick()
        self.assertEqual(len(self.tr.batches), 1)


class TestSensor(EngineCase):

    def test_only_with_fix_and_heartbeat_cadence(self):
        eng = self.make(tak_sensor_enabled=True)
        self.gps.state['fix'] = False
        eng.tick()
        self.assertEqual(self.tr.batches, [])
        self.assertEqual(eng.get_status()['sensor_point']['reason'], 'no GPS fix')
        self.gps.state['fix'] = True
        eng.tick()
        self.assertEqual(len(self.tr.batches), 1)
        self.assertEqual(parse(self.tr.last_batch()[0][2]).get('type'), 'a-f-G-E-S')
        self.assertEqual(eng.get_status()['sensor_point']['reason'], 'publishing')
        sent = []
        for t in range(1, 31):
            self.clock.advance(1)
            before = len(self.tr.batches)
            eng.tick()
            if len(self.tr.batches) > before:
                sent.append(t)
        self.assertEqual(sent, [10, 20, 30])

    def test_zero_position_fix_is_not_published(self):
        eng = self.make(tak_sensor_enabled=True)
        self.gps.state.update(latitude=0.0, longitude=0.0)
        eng.tick()
        self.assertEqual(self.tr.batches, [])

    def test_disabled_reason_and_never_deleted_on_stop(self):
        eng = self.make(tak_sensor_enabled=False)
        eng.tick()
        self.assertEqual(eng.get_status()['sensor_point']['reason'], 'disabled')
        eng2 = self.make(tak_sensor_enabled=True)
        eng2.tick()
        tr = self.tr
        eng2.stop()
        deletes = [u for u in tr.uids('delete')]
        self.assertEqual(deletes, [])                          # the sensor point is never deleted
        self.assertEqual(eng2.get_status()['deletes_on_stop'], 0)

    def test_sensor_point_counts_drones_and_reports_status(self):
        eng = self.make(tak_sensor_enabled=True)
        self.add()
        eng.tick()
        sensor = parse(self.tr.last_batch()[-1][2])
        self.assertIn('Drones tracked: 1', sensor.find('detail/remarks').text)
        st = eng.get_status()['sensor_point']
        self.assertTrue(st['enabled'] and st['publishing'])
        self.assertEqual(st['stale_s'], 30.0)
        self.assertEqual(eng.get_status()['items'],
                         {'drones': 1, 'operators': 1, 'lines': 1, 'sensor': 1})


class TestConfigure(EngineCase):

    def test_equal_config_is_noop(self):
        eng = self.make()
        self.add()
        eng.tick()
        before = len(FakeTransport.instances)
        events = list(self.tr.events)
        eng.configure(default_cfg(cot_enabled=True, tak_station_name='rx1',
                                  tak_sensor_enabled=False))
        self.assertEqual(len(FakeTransport.instances), before)
        self.assertEqual(self.tr.events, events)
        self.assertEqual(self.tr.uids('delete'), [])

    def test_changed_config_deletes_then_flushes_before_stopping_transport(self):
        eng = self.make(tak_sensor_enabled=True)
        self.add()
        eng.tick()
        old = self.tr
        eng.configure(default_cfg(cot_enabled=True, tak_station_name='rx1', cot_port=7000,
                                  tak_sensor_enabled=True))
        self.assertEqual(sorted(u.replace(PFX, '') for u in old.uids('delete')),
                         ['1581F5FHD23450019', '1581F5FHD23450019-line', '1581F5FHD23450019-op'])
        self.assertNotIn('sparrow-droneid-rx1-sensor', old.uids('delete'))
        tail = old.events[-3:]
        self.assertEqual(tail, ['enqueue', ('flush', 2.0), 'stop'])
        self.assertEqual(eng.get_status()['deletes_on_stop'], 3)
        self.assertIsNot(self.tr, old)                         # a fresh transport is running
        self.assertEqual(self.tr.cfg.port, 7000)

    def test_disable_stops_with_deletes(self):
        eng = self.make()
        self.add()
        eng.tick()
        old = self.tr
        eng.configure(default_cfg(cot_enabled=False))
        self.assertEqual(len(old.uids('delete')), 3)
        self.assertEqual(eng.get_status()['state'], 'disabled')
        self.assertFalse(eng.enabled)
        self.assertEqual(len(FakeTransport.instances), 1)

    def test_send_deletes_off_means_no_deletes_anywhere(self):
        eng = self.make(tak_send_deletes=False)
        self.add()
        eng.tick()
        self.clock.advance(300)
        eng.tick()                                             # age beyond the delete window
        eng.stop()
        self.assertEqual(self.tr.uids('delete'), [])
        self.assertEqual(eng.get_status()['deletes_sent'], 0)

    def test_stop_then_same_config_starts_again(self):
        eng = self.make()
        eng.stop()
        eng.configure(default_cfg(cot_enabled=True, tak_station_name='rx1',
                                  tak_sensor_enabled=False))
        self.assertEqual(len(FakeTransport.instances), 2)

    def test_restart_replays_with_the_same_config(self):
        eng = self.make()
        self.add()
        eng.tick()
        eng.restart()
        self.assertEqual(len(FakeTransport.instances), 2)
        self.assertEqual(len(FakeTransport.instances[0].uids('delete')), 3)

    def test_enrollment_manager_only_for_tls_enroll(self):
        eng = self.make()
        self.assertIsNone(eng.enrollment)
        eng.configure(default_cfg(cot_enabled=True, tak_transport='tls', cot_address='h.example',
                                  tak_tls_client_mode='enroll'))
        self.assertIsInstance(eng.enrollment, FakeEnrollment)
        self.assertTrue(eng.enrollment.started)
        first = eng.enrollment
        eng.configure(default_cfg(cot_enabled=False))
        self.assertTrue(first.stopped)
        self.assertIsNone(eng.enrollment)


class TestIsolationAndErrors(EngineCase):

    def test_notify_detection_touches_no_transport_and_is_fast(self):
        eng = self.make()
        eng.tick()
        calls = len(self.tr.events)
        t0 = time.perf_counter()
        for _ in range(1000):
            eng.notify_detection()
        per_call = (time.perf_counter() - t0) / 1000
        self.assertLess(per_call, 0.001)
        self.assertEqual(len(self.tr.events), calls)
        self.assertEqual([t for t, _b in self.tr.batches
                          if t == threading.current_thread().name], [])

    def test_tick_exception_is_counted_and_next_tick_works(self):
        eng = self.make()
        boom = [True]

        def source():
            if boom[0]:
                raise RuntimeError('source failed')
            return list(self.drones)
        eng._drone_source = source
        eng.tick()
        eng.tick()
        self.assertEqual(eng._tick_errors, 2)
        boom[0] = False
        self.add()
        eng.tick()
        self.assertEqual(len(self.tr.batches), 1)

    def test_emit_thread_sends_and_stop_is_quick(self):
        eng = self.make(start_threads=True)
        self.add()
        eng.notify_detection()
        end = time.monotonic() + 5
        while not self.tr.batches and time.monotonic() < end:
            time.sleep(0.02)
        self.assertTrue(self.tr.batches)
        self.assertEqual(self.tr.batches[0][0], 'tak-emit')     # never the caller's thread
        t0 = time.monotonic()
        eng.stop()
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(len(self.tr.uids('delete')), 3)

    def test_status_shape(self):
        eng = self.make()
        self.add()
        eng.tick()
        st = eng.get_status()
        for key in ('enabled', 'running', 'state', 'transport', 'destination', 'connected',
                    'connected_since', 'sent', 'send_errors', 'reconnects', 'queue_depth',
                    'queue_drops', 'last_error', 'last_error_at', 'last_send_at', 'items',
                    'deletes_sent', 'deletes_on_stop', 'sensor_point', 'tls', 'enrollment',
                    'config_warnings', 'defaults'):
            self.assertIn(key, st)
        self.assertEqual(st['state'], 'ready')                  # UDP: socket open
        self.assertIsNone(st['tls'])
        self.assertEqual(st['defaults']['station_name'], 'rx1')
        legacy = eng.legacy_status()
        self.assertEqual(set(legacy), {'enabled', 'address', 'port', 'events_sent', 'transport',
                                       'state', 'connected', 'send_errors', 'last_error'})
        self.assertEqual((legacy['address'], legacy['port']), ('239.2.3.1', 6969))

    def test_disabled_status(self):
        eng = TakEngine(cert_dir=os.path.join(self.tmp.name, 'c'), drone_source=lambda: [],
                        gps_engine=self.gps, transport_factory=FakeTransport,
                        start_threads=False)
        self.assertEqual(eng.get_status()['state'], 'disabled')
        self.assertFalse(eng.get_status()['running'])
        eng.tick()                                               # unconfigured tick is harmless
        self.assertEqual(eng.legacy_status()['port'], 6969)

    def test_config_warnings_are_surfaced(self):
        eng = self.make()
        eng.set_config_warnings(['setting x out of range'])
        self.assertEqual(eng.get_status()['config_warnings'], ['setting x out of range'])


if __name__ == '__main__':
    unittest.main()
