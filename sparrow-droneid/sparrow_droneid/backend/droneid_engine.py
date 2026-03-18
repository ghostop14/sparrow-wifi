"""
DroneID capture engine and ODID frame parser.

Manages monitor-mode WiFi capture via tcpdump and decodes:
- ASTM F3411 via Wi-Fi NAN action frames (OUI 50:6F:9A)
- ASTM F3411 via Wi-Fi beacon vendor IE (OUI FA:0B:BC)
- DJI proprietary DroneID via beacon vendor IE (OUI 26:37:12)
"""
import asyncio
import logging
import struct
import subprocess
import shutil
import threading
import os
from datetime import datetime
from time import sleep

from .models import (
    DroneIDDevice, WifiInterface, Protocol, UAType,
    rssi_trend as calc_rssi_trend,
)

# --------------- Constants ------------------------------------------------

# ODID message types (ASTM F3411-22a)
ODID_MSG_BASIC_ID = 0
ODID_MSG_LOCATION = 1
ODID_MSG_AUTH = 2
ODID_MSG_SELF_ID = 3
ODID_MSG_SYSTEM = 4
ODID_MSG_OPERATOR_ID = 5
ODID_MSG_PACK = 0xF

ODID_MSG_SIZE = 25

# OUIs
OUI_WIFI_ALLIANCE = b'\x50\x6f\x9a'
OUI_ASTM_BEACON = b'\xfa\x0b\xbc'
OUI_DJI = b'\x26\x37\x12'

# Vendor type bytes
NAN_OUI_TYPE = 0x13
ASTM_BEACON_OUI_TYPE = 0x0D

# BLE constants
BLE_ASTM_UUID = "0000fffa-0000-1000-8000-00805f9b34fb"
BLE_SERVICE_DATA_HEADER_LEN = 2  # app_code(1) + counter(1)

# BLE throttle: seconds between DB writes / alert callbacks per drone.
# In-memory state is always updated immediately for API queries.
_BLE_EMIT_INTERVAL = 3.0

# 802.11 frame type/subtype values
SUBTYPE_BEACON = 0x08
SUBTYPE_PROBE_RESP = 0x05
SUBTYPE_ACTION = 0x0D

# Radiotap field definitions: (bit_position, size_bytes, alignment)
_RT_FIELD_INFO = [
    (0, 8, 8),   # TSFT
    (1, 1, 1),   # Flags
    (2, 1, 1),   # Rate
    (3, 4, 2),   # Channel (freq(2) + flags(2))
    (4, 2, 2),   # FHSS
    (5, 1, 1),   # dBm Antenna Signal
]


# --------------- ODID Message Parser --------------------------------------

class ODIDParser:
    """Pure-Python parser for ASTM F3411 Open Drone ID messages."""

    @staticmethod
    def parse_basic_id(data: bytes, device: DroneIDDevice):
        """Parse Basic ID message (type 0)."""
        if len(data) < ODID_MSG_SIZE:
            return
        id_ua_byte = data[1]
        device.id_type = (id_ua_byte >> 4) & 0x0F
        device.ua_type = id_ua_byte & 0x0F
        id_bytes = data[2:22]
        id_str = id_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace').strip()
        if device.id_type == 1:  # Serial number
            device.serial_number = id_str
        elif device.id_type == 2:  # Registration
            device.registration_id = id_str
        elif device.id_type == 3:  # UTM UUID
            if not device.serial_number:
                device.serial_number = id_str
        elif device.id_type == 4:  # Specific Session
            if not device.serial_number:
                device.serial_number = id_str
        else:
            # id_type 0 (None) — often contains model name (e.g. "DJIMavicPro"),
            # not a true serial. Store as self_id_text for display only.
            if id_str and not device.self_id_text:
                device.self_id_text = id_str

    @staticmethod
    def parse_location(data: bytes, device: DroneIDDevice):
        """Parse Location/Vector message (type 1)."""
        if len(data) < ODID_MSG_SIZE:
            return
        status_byte = data[1]
        ew_dir = (status_byte >> 3) & 0x01
        speed_mult = (status_byte >> 2) & 0x01

        # Direction: 0-179 -> 0-358 degrees (2-degree steps)
        raw_dir = data[2]
        direction = raw_dir * 2.0
        if ew_dir:
            direction += 180.0
        if direction >= 360.0:
            direction -= 360.0
        device.direction = direction

        # Speed
        raw_speed = data[3]
        if speed_mult:
            device.speed = raw_speed * 0.75 + 63.75
        else:
            device.speed = raw_speed * 0.25

        # Vertical speed (signed)
        raw_vspeed = struct.unpack_from('<b', data, 4)[0]
        device.vertical_speed = raw_vspeed * 0.5

        # Lat/Lon (int32, degrees * 1e7)
        lat_raw, lon_raw = struct.unpack_from('<ii', data, 5)
        if lat_raw != 0 or lon_raw != 0:
            device.drone_lat = lat_raw / 1e7
            device.drone_lon = lon_raw / 1e7

        # Altitudes (uint16, * 0.5 - 1000 metres)
        # 0xFFFF is the ASTM F3411 sentinel meaning "unknown/not available".
        # 0x0000 encodes exactly -1000m which is also physically unreasonable;
        # treat it as unknown too to avoid spurious -1000m readings.
        baro_alt, geo_alt, height_agl = struct.unpack_from('<HHH', data, 13)
        if baro_alt not in (0x0000, 0xFFFF):
            device.drone_alt_baro = baro_alt * 0.5 - 1000.0
        if geo_alt not in (0x0000, 0xFFFF):
            device.drone_alt_geo = geo_alt * 0.5 - 1000.0
        if height_agl not in (0x0000, 0xFFFF):
            device.drone_height_agl = height_agl * 0.5 - 1000.0

    @staticmethod
    def parse_self_id(data: bytes, device: DroneIDDevice):
        """Parse Self-ID message (type 3)."""
        if len(data) < ODID_MSG_SIZE:
            return
        text_bytes = data[2:25]
        device.self_id_text = text_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace').strip()

    @staticmethod
    def parse_system(data: bytes, device: DroneIDDevice):
        """Parse System message (type 4)."""
        if len(data) < ODID_MSG_SIZE:
            return
        # Operator lat/lon (int32, degrees * 1e7)
        op_lat_raw, op_lon_raw = struct.unpack_from('<ii', data, 2)
        if op_lat_raw != 0 or op_lon_raw != 0:
            device.operator_lat = op_lat_raw / 1e7
            device.operator_lon = op_lon_raw / 1e7

        # Operator altitude (at offset 17, uint16, * 0.5 - 1000)
        # 0x0000 and 0xFFFF are both sentinels meaning "unknown/not available"
        if len(data) >= 19:
            op_alt = struct.unpack_from('<H', data, 17)[0]
            if op_alt not in (0x0000, 0xFFFF):
                device.operator_alt = op_alt * 0.5 - 1000.0

    @staticmethod
    def parse_operator_id(data: bytes, device: DroneIDDevice):
        """Parse Operator ID message (type 5)."""
        if len(data) < ODID_MSG_SIZE:
            return
        id_bytes = data[2:22]
        device.operator_id = id_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace').strip()

    @classmethod
    def parse_message(cls, data: bytes, device: DroneIDDevice):
        """Parse a single 25-byte ODID message."""
        if len(data) < ODID_MSG_SIZE:
            return
        header = data[0]
        msg_type = (header >> 4) & 0x0F

        if msg_type == ODID_MSG_BASIC_ID:
            cls.parse_basic_id(data, device)
        elif msg_type == ODID_MSG_LOCATION:
            cls.parse_location(data, device)
        elif msg_type == ODID_MSG_SELF_ID:
            cls.parse_self_id(data, device)
        elif msg_type == ODID_MSG_SYSTEM:
            cls.parse_system(data, device)
        elif msg_type == ODID_MSG_OPERATOR_ID:
            cls.parse_operator_id(data, device)
        # Type 2 (Auth) and others are ignored in v1

    @classmethod
    def parse_message_pack(cls, data: bytes) -> DroneIDDevice:
        """Parse an ODID message pack (multiple 25-byte messages)."""
        device = DroneIDDevice()
        if len(data) < 2:
            return device

        # Message pack header: byte 0 = proto/type, byte 1 = count info
        offset = 0
        header = data[0]
        msg_type = (header >> 4) & 0x0F

        if msg_type == ODID_MSG_PACK:
            # Message pack container
            if len(data) < 2:
                return device
            msg_count = data[1] & 0x0F
            offset = 2
            for _ in range(msg_count):
                if offset + ODID_MSG_SIZE > len(data):
                    break
                cls.parse_message(data[offset:offset + ODID_MSG_SIZE], device)
                offset += ODID_MSG_SIZE
        else:
            # Single message
            cls.parse_message(data[:ODID_MSG_SIZE], device)

        return device


# --------------- DJI Proprietary Parser -----------------------------------

class DJIParser:
    """Parser for DJI proprietary DroneID in vendor-specific beacon IEs."""

    @staticmethod
    def parse(data: bytes) -> DroneIDDevice:
        """Parse DJI proprietary DroneID payload (after OUI bytes).

        Expected layout (after OUI — data[0] is the first post-OUI byte):
          Byte  0    : vendor subtype (0x10 = flight info; others ignored)
          Byte  1    : version
          Bytes 2-3  : sequence number (uint16 LE)
          Bytes 4-11 : serial number (8 bytes ASCII, null-terminated)
          Bytes 12-15: drone latitude  (int32 LE, degrees × 1e-7)
          Bytes 16-19: drone longitude (int32 LE, degrees × 1e-7)
          Bytes 20-21: geodetic altitude (int16 LE, metres)
          Bytes 22-23: height AGL       (int16 LE, metres)
          Bytes 24-25: speed_x (int16 LE, cm/s → × 0.01 m/s)
          Bytes 26-27: speed_y (int16 LE, cm/s → × 0.01 m/s)
          Bytes 28-31: home / operator latitude  (int32 LE, degrees × 1e-7)
          Bytes 32-35: home / operator longitude (int32 LE, degrees × 1e-7)

        DJI firmware versions differ; fields beyond byte 20 are best-effort.
        """
        device = DroneIDDevice(protocol=Protocol.DJI_PROPRIETARY.value)
        device.ua_type = UAType.HELICOPTER.value  # DJI drones are multirotor

        if len(data) < 4:
            return device

        vendor_subtype = data[0]
        if vendor_subtype != 0x10:
            return device  # Not flight info; other subtypes not yet decoded

        # Serial number: bytes 4-11 (8 bytes ASCII)
        try:
            if len(data) >= 12:
                sn_bytes = data[4:12]
                sn = sn_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace').strip()
                if sn:
                    device.serial_number = 'DJI-' + sn
        except (IndexError, UnicodeDecodeError):
            pass

        # Position
        try:
            if len(data) >= 20:
                lat_raw, lon_raw = struct.unpack_from('<ii', data, 12)
                if lat_raw != 0 or lon_raw != 0:
                    device.drone_lat = round(lat_raw / 1e7, 7)
                    device.drone_lon = round(lon_raw / 1e7, 7)
        except struct.error:
            pass

        # Altitudes
        try:
            if len(data) >= 22:
                alt = struct.unpack_from('<h', data, 20)[0]
                device.drone_alt_geo = float(alt)
        except struct.error:
            pass

        try:
            if len(data) >= 24:
                hagl = struct.unpack_from('<h', data, 22)[0]
                device.drone_height_agl = float(hagl)
        except struct.error:
            pass

        # Speed (cm/s components → m/s magnitude)
        try:
            if len(data) >= 28:
                import math
                vx, vy = struct.unpack_from('<hh', data, 24)
                speed_x = vx * 0.01
                speed_y = vy * 0.01
                device.speed = round((speed_x ** 2 + speed_y ** 2) ** 0.5, 2)
                if vx != 0 or vy != 0:
                    device.direction = round((math.degrees(math.atan2(vx, vy)) + 360) % 360, 1)
        except struct.error:
            pass

        # Home / operator position
        try:
            if len(data) >= 36:
                home_lat, home_lon = struct.unpack_from('<ii', data, 28)
                if home_lat != 0 or home_lon != 0:
                    device.operator_lat = round(home_lat / 1e7, 7)
                    device.operator_lon = round(home_lon / 1e7, 7)
        except struct.error:
            pass

        return device


# --------------- Frame Extractor ------------------------------------------

class FrameExtractor:
    """Extract ODID payloads from raw 802.11 frame bytes."""

    @staticmethod
    def _scan_vendor_ies(data: bytes, offset: int):
        """Scan tagged parameters for vendor-specific IEs.

        Yields (oui, oui_type, payload) tuples for each vendor-specific IE found.
        """
        while offset + 2 <= len(data):
            tag = data[offset]
            length = data[offset + 1]
            if offset + 2 + length > len(data):
                break
            if tag == 221 and length >= 4:  # Vendor-specific IE
                oui = data[offset + 2:offset + 5]
                oui_type = data[offset + 5]
                payload = data[offset + 6:offset + 2 + length]
                yield oui, oui_type, payload
            offset += 2 + length

    @staticmethod
    def extract_from_action(frame: bytes):
        """Try to extract ODID data from an action frame.

        Returns DroneIDDevice or None.
        """
        # 802.11 MAC header for action frames is 24 bytes
        if len(frame) < 26:
            return None

        body_offset = 24
        if body_offset + 2 > len(frame):
            return None

        category = frame[body_offset]
        action_code = frame[body_offset + 1]

        # Public Action (4) + Vendor Specific (9)
        if category != 0x04 or action_code != 0x09:
            return None

        if body_offset + 5 > len(frame):
            return None

        oui = frame[body_offset + 2:body_offset + 5]
        if oui != OUI_WIFI_ALLIANCE:
            return None

        oui_type = frame[body_offset + 5]
        if oui_type != NAN_OUI_TYPE:
            return None

        # NAN frame — scan for Service Descriptor attributes containing ODID
        # The NAN body starts after the OUI Type byte
        nan_offset = body_offset + 6

        # Scan NAN attributes (each: attr_id(1) + length(2LE) + body)
        while nan_offset + 3 <= len(frame):
            attr_id = frame[nan_offset]
            attr_len = struct.unpack_from('<H', frame, nan_offset + 1)[0]
            attr_body_start = nan_offset + 3

            if attr_body_start + attr_len > len(frame):
                break

            # Service Descriptor Attribute (0x03) or
            # Service Descriptor Extension (0x0E) may contain ODID data
            if attr_id in (0x03, 0x0E):
                attr_body = frame[attr_body_start:attr_body_start + attr_len]
                # SDF header layout (NAN Spec §Table 82):
                #   Bytes 0-5  : Service ID hash (6 bytes)
                #   Byte  6    : Instance ID
                #   Byte  7    : Requester Instance ID
                #   Byte  8    : Service Control bitmap
                #     bit 1 = Service-specific info present
                #   Byte  9    : Service-specific info length (present when bit 1 set)
                #   Bytes 10.. : Service-specific info = ODID message pack
                if len(attr_body) >= 10:
                    svc_ctrl = attr_body[8]
                    if svc_ctrl & 0x02:  # Service-specific info present
                        ssi_len = attr_body[9]
                        ssi_start = 10
                        ssi_end = ssi_start + ssi_len
                        if ssi_end <= len(attr_body) and ssi_len >= 2:
                            try:
                                device = ODIDParser.parse_message_pack(
                                    attr_body[ssi_start:ssi_end]
                                )
                                if device.get_key():
                                    device.protocol = Protocol.ASTM_NAN.value
                                    return device
                            except Exception:
                                pass

            nan_offset += 3 + attr_len

        return None

    @staticmethod
    def extract_from_beacon_or_probe(frame: bytes):
        """Try to extract ODID data from a beacon or probe response frame.

        Returns DroneIDDevice or None.
        """
        # 802.11 MAC header = 24 bytes
        # Fixed params: timestamp(8) + interval(2) + capabilities(2) = 12 bytes
        ie_offset = 24 + 12
        if ie_offset >= len(frame):
            return None

        for oui, oui_type, payload in FrameExtractor._scan_vendor_ies(frame, ie_offset):
            # ASTM F3411 beacon method
            if oui == OUI_ASTM_BEACON and oui_type == ASTM_BEACON_OUI_TYPE:
                try:
                    device = ODIDParser.parse_message_pack(payload)
                    if device.get_key():
                        device.protocol = Protocol.ASTM_BEACON.value
                        return device
                except Exception:
                    continue

            # DJI proprietary
            if oui == OUI_DJI:
                try:
                    device = DJIParser.parse(bytes([oui_type]) + payload)
                    if device.get_key():
                        return device
                except Exception:
                    continue

        return None


# --------------- Capture Manager ------------------------------------------

class CaptureManager:
    """Manages WiFi interface mode switching and tcpdump capture."""

    @staticmethod
    def get_interfaces():
        """Enumerate WiFi interfaces with monitor-mode capability."""
        interfaces = []

        # Get interface list from iw dev
        try:
            result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=5)
            output = result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return interfaces

        current_phy = ""
        current_iface = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('phy#'):
                current_phy = line.rstrip()
            elif line.startswith('Interface '):
                if current_iface.get('name'):
                    interfaces.append(current_iface)
                current_iface = {
                    'name': line.split()[1],
                    'phy': current_phy,
                    'mode': 'managed',
                    'mac_address': '',
                    'monitor_capable': False,
                    'driver': '',
                }
            elif line.startswith('addr '):
                current_iface['mac_address'] = line.split()[1]
            elif line.startswith('type '):
                current_iface['mode'] = line.split()[1]

        if current_iface.get('name'):
            interfaces.append(current_iface)

        # Check monitor capability per phy
        try:
            result = subprocess.run(['iw', 'phy'], capture_output=True, text=True, timeout=5)
            phy_output = result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return [WifiInterface(**i).to_dict() for i in interfaces]

        phy_monitor = set()
        current_phy_name = ""
        in_modes = False
        for line in phy_output.splitlines():
            stripped = line.strip()
            if line.startswith('Wiphy '):
                current_phy_name = 'phy#' + stripped.split()[-1].replace('phy', '')
                in_modes = False
            elif 'Supported interface modes:' in stripped:
                in_modes = True
            elif in_modes:
                if stripped.startswith('*'):
                    if 'monitor' in stripped.lower():
                        phy_monitor.add(current_phy_name)
                elif not stripped.startswith('*') and stripped:
                    in_modes = False

        # Get driver info
        for iface in interfaces:
            iface['monitor_capable'] = iface['phy'] in phy_monitor
            try:
                driver_path = f"/sys/class/net/{iface['name']}/device/driver"
                if os.path.islink(driver_path):
                    iface['driver'] = os.path.basename(os.readlink(driver_path))
            except OSError:
                pass

        return [WifiInterface(**i).to_dict() for i in interfaces]

    @staticmethod
    def start_monitor(interface, channel=6):
        """Switch interface to monitor mode on the given channel."""
        cmds = [
            ['ip', 'link', 'set', interface, 'down'],
            ['iw', 'dev', interface, 'set', 'type', 'monitor'],
            ['ip', 'link', 'set', interface, 'up'],
            ['iw', 'dev', interface, 'set', 'channel', str(channel)],
        ]
        for cmd in cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise RuntimeError(f"Failed: {' '.join(cmd)}: {result.stderr.strip()}")
        return interface

    @staticmethod
    def stop_monitor(interface):
        """Restore interface to managed mode."""
        cmds = [
            ['ip', 'link', 'set', interface, 'down'],
            ['iw', 'dev', interface, 'set', 'type', 'managed'],
            ['ip', 'link', 'set', interface, 'up'],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            except Exception:
                pass

    @staticmethod
    def start_capture(interface):
        """Launch tcpdump for ODID-relevant frame capture (raw pcap output)."""
        # Raw frame-control byte filter: 0xd0=Action, 0x80=Beacon, 0x50=Probe-Resp
        # (the compound 'type mgt subtype X or ...' syntax fails on libpcap 1.10)
        bpf = 'wlan[0] == 0xd0 or wlan[0] == 0x80 or wlan[0] == 0x50'
        cmd = [
            'tcpdump', '-i', interface,
            '-w', '-',              # Raw pcap to stdout
            '-U',                   # Packet-buffered output
            '--immediate-mode',
            '-s', '0',              # Full frame capture
            bpf,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        return proc

    @staticmethod
    def stop_capture(proc):
        """Gracefully stop tcpdump."""
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass


# --------------- DroneID Engine -------------------------------------------

class DroneIDEngine:
    """Orchestrates capture, parsing, and tracking of detected drones."""

    def __init__(self, db, gps_engine):
        self._db = db
        self._gps = gps_engine

        # Active drone tracking (in-memory)
        self._active_drones = {}   # key -> DroneIDDevice
        self._rssi_history = {}    # key -> list of RSSI values
        self._lock = threading.Lock()

        # Monitoring state
        self._monitoring = False
        self._interface = ""
        self._channel = 6
        self._started_at = None
        self._capture_proc = None
        self._parse_thread = None

        # Counters
        self._frame_count = 0
        self._droneid_frame_count = 0
        self._capture_errors = 0

        # Monitor mode health check
        self._monitor_warning = ""  # Non-empty = problem detected

        # BLE scanning state
        self._ble_thread = None
        self._ble_loop = None        # asyncio event loop owned by BLE thread
        self._ble_frame_count = 0
        self._ble_enabled = False    # True when BLE adapter was found and scan started
        self._ble_last_emit = {}     # MAC -> monotonic timestamp of last DB/alert emit

        # Callback for alert engine
        self.on_detection = None

    @property
    def monitoring(self):
        return self._monitoring

    def start(self, interface, channel=6):
        """Start monitoring on the given interface."""
        if self._monitoring:
            raise RuntimeError("Already monitoring")

        self._interface = CaptureManager.start_monitor(interface, channel)
        self._channel = channel
        self._capture_proc = CaptureManager.start_capture(self._interface)
        self._monitoring = True
        self._started_at = datetime.utcnow()
        self._frame_count = 0
        self._droneid_frame_count = 0
        self._capture_errors = 0
        self._monitor_warning = ""

        self._parse_thread = threading.Thread(target=self._parse_loop, daemon=True)
        self._parse_thread.start()

        # Start BLE scan in a daemon thread with its own asyncio event loop
        self._ble_frame_count = 0
        self._ble_enabled = False
        self._ble_thread = threading.Thread(target=self._ble_thread_main, daemon=True,
                                            name='ble-scan')
        self._ble_thread.start()

        # Background health check: verify frames are actually arriving
        threading.Thread(target=self._monitor_health_check, daemon=True,
                         name='monitor-health').start()

    def stop(self):
        """Stop monitoring and restore interface."""
        self._monitoring = False
        CaptureManager.stop_capture(self._capture_proc)
        self._capture_proc = None

        if self._parse_thread:
            self._parse_thread.join(timeout=5)
            self._parse_thread = None

        # Signal the BLE event loop to stop and wait for its thread
        if self._ble_loop is not None:
            try:
                self._ble_loop.call_soon_threadsafe(self._ble_loop.stop)
            except Exception:
                pass
        if self._ble_thread:
            self._ble_thread.join(timeout=5)
            self._ble_thread = None
        self._ble_loop = None

        if self._interface:
            try:
                CaptureManager.stop_monitor(self._interface)
            except Exception:
                pass
            self._interface = ""

    def _parse_loop(self):
        """Read raw pcap stream from tcpdump and parse ODID frames."""
        proc = self._capture_proc
        if proc is None or proc.stdout is None:
            return

        f = proc.stdout

        # Read pcap global header (24 bytes)
        global_hdr = self._read_exact(f, 24)
        if global_hdr is None or len(global_hdr) < 24:
            return

        magic = struct.unpack_from('<I', global_hdr, 0)[0]
        if magic == 0xa1b2c3d4:
            endian = '<'
        elif magic == 0xd4c3b2a1:
            endian = '>'
        else:
            return  # Not a pcap stream

        link_type = struct.unpack_from(f'{endian}I', global_hdr, 20)[0]
        if link_type != 127:  # IEEE802_11_RADIO (radiotap + 802.11)
            return

        # Read packets
        while self._monitoring:
            rec_hdr = self._read_exact(f, 16)
            if rec_hdr is None or len(rec_hdr) < 16:
                break

            incl_len = struct.unpack_from(f'{endian}I', rec_hdr, 8)[0]

            if incl_len == 0 or incl_len > 65536:
                break

            pkt_data = self._read_exact(f, incl_len)
            if pkt_data is None or len(pkt_data) < incl_len:
                break

            self._frame_count += 1

            try:
                self._process_pcap_frame(pkt_data)
            except Exception:
                self._capture_errors += 1

    @staticmethod
    def _read_exact(f, n):
        """Read exactly *n* bytes from binary stream *f*."""
        data = b''
        while len(data) < n:
            chunk = f.read(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _process_pcap_frame(self, pkt_data):
        """Parse radiotap + 802.11 frame from raw pcap packet data."""
        if len(pkt_data) < 8:
            return

        # Radiotap header: version(1), pad(1), length(2LE)
        rt_len = struct.unpack_from('<H', pkt_data, 2)[0]
        if rt_len > len(pkt_data):
            return

        rssi = self._extract_radiotap_rssi(pkt_data[:rt_len])

        # 802.11 frame starts after radiotap header
        frame = pkt_data[rt_len:]
        if len(frame) < 24:
            return

        # For management frames addr2 (bytes 10-15) = SA
        sa_bytes = frame[10:16]
        mac = ':'.join(f'{b:02x}' for b in sa_bytes)

        header_info = {'rssi': rssi, 'mac': mac}
        self._process_frame(frame, header_info)

    @staticmethod
    def _extract_radiotap_rssi(rt_data):
        """Extract dBm Antenna Signal from radiotap header."""
        if len(rt_data) < 8:
            return 0

        present = struct.unpack_from('<I', rt_data, 4)[0]

        # Skip past any extended present-flag words
        offset = 4
        p = present
        while p & (1 << 31):
            offset += 4
            if offset + 4 > len(rt_data):
                return 0
            p = struct.unpack_from('<I', rt_data, offset)[0]
        offset += 4  # past last present word

        # Walk fields in order until we reach bit 5 (dBm Antenna Signal)
        for bit, size, align in _RT_FIELD_INFO:
            if not (present & (1 << bit)):
                continue
            if align > 1:
                offset = (offset + align - 1) & ~(align - 1)
            if bit == 5:
                if offset < len(rt_data):
                    return struct.unpack_from('b', rt_data, offset)[0]
                return 0
            offset += size

        return 0

    def _process_frame(self, frame_bytes, header_info):
        """Identify frame type and extract ODID data."""
        if len(frame_bytes) < 2:
            return

        # 802.11 frame control is first 2 bytes
        fc = struct.unpack_from('<H', frame_bytes, 0)[0]
        frame_type = (fc >> 2) & 0x03     # bits 2-3
        subtype = (fc >> 4) & 0x0F        # bits 4-7

        if frame_type != 0:  # Not management frame
            return

        device = None
        if subtype == SUBTYPE_ACTION:
            device = FrameExtractor.extract_from_action(frame_bytes)
        elif subtype in (SUBTYPE_BEACON, SUBTYPE_PROBE_RESP):
            device = FrameExtractor.extract_from_beacon_or_probe(frame_bytes)

        if device is None or not device.get_key():
            return

        # Populate RF metadata from tcpdump header
        device.rssi = header_info.get('rssi', 0)
        device.mac_address = header_info.get('mac', '')
        device.channel = self._channel
        device.frequency = 2437 if self._channel == 6 else 2412 + (self._channel - 1) * 5

        self._droneid_frame_count += 1
        self._track_device(device)

    def _track_device(self, device: DroneIDDevice):
        """Update tracking state, persist to DB, and fire the detection callback.

        Called from both the WiFi parse path and the BLE scan path so the
        downstream logic (merging, RSSI history, DB write, alerts) is shared.
        The caller is responsible for setting device.rssi / device.mac_address
        before calling this method.
        """
        now = datetime.utcnow().isoformat() + 'Z'
        key = device.get_key()

        # Update active drones dict
        with self._lock:
            if key in self._active_drones:
                existing = self._active_drones[key]
                device.first_seen = existing.first_seen
                # Merge: keep existing data for fields the new frame didn't populate
                if not device.serial_number and existing.serial_number:
                    device.serial_number = existing.serial_number
                if not device.registration_id and existing.registration_id:
                    device.registration_id = existing.registration_id
                if not device.self_id_text and existing.self_id_text:
                    device.self_id_text = existing.self_id_text
                if not device.operator_id and existing.operator_id:
                    device.operator_id = existing.operator_id
                if device.operator_lat == 0.0 and existing.operator_lat != 0.0:
                    device.operator_lat = existing.operator_lat
                    device.operator_lon = existing.operator_lon
                    device.operator_alt = existing.operator_alt
                if device.ua_type == 0 and existing.ua_type != 0:
                    device.ua_type = existing.ua_type
            else:
                device.first_seen = now

            device.last_seen = now
            self._active_drones[key] = device

            # RSSI history
            if key not in self._rssi_history:
                self._rssi_history[key] = []
            self._rssi_history[key].append(device.rssi)
            if len(self._rssi_history[key]) > 10:
                self._rssi_history[key] = self._rssi_history[key][-10:]

        # Persist to database
        rx_lat, rx_lon, rx_alt = self._gps.get_receiver_position()
        try:
            self._db.insert_detection(device, rx_lat, rx_lon, rx_alt)
        except Exception:
            self._capture_errors += 1

        # Fire callback (alert engine, CoT engine)
        if self.on_detection:
            try:
                self.on_detection(device)
            except Exception:
                pass

    def _track_ble_device(self, device: DroneIDDevice):
        """Track a BLE RemoteID device, merging individual messages by MAC.

        BLE broadcasts single ODID messages (BasicID, Location, System, etc.)
        at ~20/sec.  We merge every advertisement into the in-memory state
        immediately, but throttle DB writes and alert/callback firing to at
        most once per drone per _BLE_EMIT_INTERVAL seconds.
        """
        import time as _time
        now_ts = _time.monotonic()
        now = datetime.utcnow().isoformat() + 'Z'
        key = device.mac_address  # always use MAC for BLE
        is_new = False

        with self._lock:
            if key in self._active_drones:
                existing = self._active_drones[key]
                device.first_seen = existing.first_seen
                # Merge: keep existing data for fields the new message didn't set
                if not device.serial_number and existing.serial_number:
                    device.serial_number = existing.serial_number
                if not device.registration_id and existing.registration_id:
                    device.registration_id = existing.registration_id
                if not device.self_id_text and existing.self_id_text:
                    device.self_id_text = existing.self_id_text
                if not device.operator_id and existing.operator_id:
                    device.operator_id = existing.operator_id
                if device.drone_lat == 0.0 and existing.drone_lat != 0.0:
                    device.drone_lat = existing.drone_lat
                    device.drone_lon = existing.drone_lon
                if device.drone_alt_geo == 0.0 and existing.drone_alt_geo != 0.0:
                    device.drone_alt_geo = existing.drone_alt_geo
                    device.drone_alt_baro = existing.drone_alt_baro
                    device.drone_height_agl = existing.drone_height_agl
                if device.speed == 0.0 and existing.speed != 0.0:
                    device.speed = existing.speed
                    device.direction = existing.direction
                    device.vertical_speed = existing.vertical_speed
                if device.operator_lat == 0.0 and existing.operator_lat != 0.0:
                    device.operator_lat = existing.operator_lat
                    device.operator_lon = existing.operator_lon
                    device.operator_alt = existing.operator_alt
                if device.ua_type == 0 and existing.ua_type != 0:
                    device.ua_type = existing.ua_type
                if not device.protocol and existing.protocol:
                    device.protocol = existing.protocol
            else:
                device.first_seen = now
                is_new = True

            device.last_seen = now
            self._active_drones[key] = device

            # RSSI history
            if key not in self._rssi_history:
                self._rssi_history[key] = []
            self._rssi_history[key].append(device.rssi)
            if len(self._rssi_history[key]) > 10:
                self._rssi_history[key] = self._rssi_history[key][-10:]

        # Throttle DB writes and alert callbacks to avoid flooding.
        # The in-memory state (above) is always up-to-date for API queries.
        last_emit = self._ble_last_emit.get(key, 0.0)
        if not is_new and (now_ts - last_emit) < _BLE_EMIT_INTERVAL:
            return  # silently merged — skip DB/alerts until interval elapses
        self._ble_last_emit[key] = now_ts

        # Persist to database
        rx_lat, rx_lon, rx_alt = self._gps.get_receiver_position()
        try:
            self._db.insert_detection(device, rx_lat, rx_lon, rx_alt)
        except Exception:
            self._capture_errors += 1

        # Fire callback (alert engine, CoT engine)
        if self.on_detection:
            try:
                self.on_detection(device)
            except Exception:
                pass

    # ----- BLE scan -------------------------------------------------------

    def _ble_thread_main(self):
        """Entry point for the BLE daemon thread.

        Creates a dedicated asyncio event loop and runs _ble_scan_loop() on it.
        On exit (monitoring stopped or no BLE adapter) the loop is closed.
        """
        loop = asyncio.new_event_loop()
        self._ble_loop = loop
        try:
            loop.run_until_complete(self._ble_scan_loop())
        except Exception as exc:
            logging.warning("DroneID BLE thread exited with error: %s", exc)
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._ble_loop = None

    @staticmethod
    def _ensure_bluetooth_ready():
        """Ensure bluetoothd is running and the HCI adapter is available.

        On many systems bluetoothd starts late or the adapter needs a driver
        rebind before BlueZ exposes org.bluez.Adapter1 on DBus.
        """
        import shutil as _sh

        # 1. Make sure bluetoothd is running
        try:
            result = subprocess.run(
                ['pidof', 'bluetoothd'], capture_output=True, timeout=3
            )
            if result.returncode != 0:
                daemon = _sh.which('bluetoothd') or '/usr/libexec/bluetooth/bluetoothd'
                subprocess.Popen(
                    [daemon], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                logging.info("DroneID BLE: started bluetoothd")
                sleep(2)
        except Exception:
            pass

        # 2. Bring the adapter up
        try:
            subprocess.run(
                ['hciconfig', 'hci0', 'up'],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        # 3. Check if Adapter1 is on DBus; if not, rebind the btusb driver
        try:
            probe = subprocess.run(
                ['dbus-send', '--system', '--dest=org.bluez', '--print-reply',
                 '/org/bluez/hci0',
                 'org.freedesktop.DBus.Introspectable.Introspect'],
                capture_output=True, text=True, timeout=5
            )
            if 'Adapter1' not in probe.stdout:
                logging.info("DroneID BLE: Adapter1 not on DBus — rebinding btusb driver")
                # Find the USB device backing hci0
                dev_link = os.readlink('/sys/class/bluetooth/hci0/device')
                usb_id = os.path.basename(dev_link)  # e.g. "3-14:1.0"
                unbind = f'/sys/bus/usb/drivers/btusb/unbind'
                bind = f'/sys/bus/usb/drivers/btusb/bind'
                with open(unbind, 'w') as f:
                    f.write(usb_id)
                sleep(2)
                with open(bind, 'w') as f:
                    f.write(usb_id)
                sleep(3)

                # Restart bluetoothd so it picks up the fresh adapter
                subprocess.run(['killall', 'bluetoothd'],
                               capture_output=True, timeout=3)
                sleep(1)
                daemon = _sh.which('bluetoothd') or '/usr/libexec/bluetooth/bluetoothd'
                subprocess.Popen(
                    [daemon], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                sleep(3)
                logging.info("DroneID BLE: btusb rebound, bluetoothd restarted")
        except Exception as exc:
            logging.debug("DroneID BLE: adapter init check failed: %s", exc)

    async def _ble_scan_loop(self):
        """Continuously scan for BLE Remote ID advertisements (ASTM F3411).

        Runs until self._monitoring becomes False.  Uses BleakScanner in
        detection_callback mode so each advertisement is processed immediately
        rather than waiting for a full discover() batch.
        """
        try:
            from bleak import BleakScanner
        except ImportError:
            logging.warning(
                "DroneID BLE: bleak not installed — BLE Remote ID scanning disabled. "
                "Install with: pip3 install bleak"
            )
            return

        # Ensure the Bluetooth stack is ready before attempting to scan
        try:
            self._ensure_bluetooth_ready()
        except Exception as exc:
            logging.debug("DroneID BLE: adapter init error (non-fatal): %s", exc)

        def _on_advertisement(ble_device, advertisement_data):
            """Called by BleakScanner for every received BLE advertisement."""
            if not self._monitoring:
                return

            # Only process advertisements that carry ASTM F3411 service data
            service_data = advertisement_data.service_data
            if not service_data or BLE_ASTM_UUID not in service_data:
                return

            payload = service_data[BLE_ASTM_UUID]

            # Strip the 2-byte header: [app_code(1)][counter(1)] leaving the
            # 25-byte ODID message (or message pack starting at offset 2)
            if len(payload) < BLE_SERVICE_DATA_HEADER_LEN + 1:
                return

            odid_bytes = payload[BLE_SERVICE_DATA_HEADER_LEN:]

            try:
                device = ODIDParser.parse_message_pack(odid_bytes)
            except Exception as exc:
                logging.debug("DroneID BLE: parse error from %s: %s", ble_device.address, exc)
                return

            # BLE broadcasts individual messages, not packs — always key by
            # MAC so Location/System/etc. merge with the BasicID entry.
            device.mac_address = ble_device.address
            device.protocol = Protocol.ASTM_BLE.value
            device.rssi = advertisement_data.rssi if advertisement_data.rssi is not None else 0

            self._ble_frame_count += 1
            self._track_ble_device(device)

        # bluetoothd / DBus may not be ready at app startup — retry a few times
        scanner = None
        for attempt in range(6):
            if not self._monitoring:
                return
            try:
                scanner = BleakScanner(detection_callback=_on_advertisement)
                await scanner.start()
                break
            except Exception as exc:
                if attempt < 5:
                    delay = 2 * (attempt + 1)
                    logging.info(
                        "DroneID BLE: adapter not ready (attempt %d/6), "
                        "retrying in %ds: %s", attempt + 1, delay, exc
                    )
                    await asyncio.sleep(delay)
                else:
                    self._ble_enabled = False
                    logging.warning(
                        "DroneID BLE: no adapter after 6 attempts — "
                        "BLE scanning disabled: %s", exc
                    )
                    return

        self._ble_enabled = True
        logging.info("DroneID BLE: scanner started")

        try:
            while self._monitoring:
                await asyncio.sleep(0.5)
        finally:
            if scanner is not None:
                await scanner.stop()

    # ----- Active drone queries -------------------------------------------

    def get_active_drones(self, max_age=180):
        """Get list of currently tracked drones with derived fields."""
        rx_lat, rx_lon, rx_alt = self._gps.get_receiver_position()
        now = datetime.utcnow()
        result = []

        with self._lock:
            for key, device in list(self._active_drones.items()):
                # Check age
                try:
                    last = datetime.fromisoformat(device.last_seen.replace('Z', ''))
                    age = (now - last).total_seconds()
                except (ValueError, AttributeError):
                    age = 9999

                if max_age > 0 and age > max_age:
                    continue

                d = device.to_dict(rx_lat, rx_lon, rx_alt)
                # Add RSSI trend
                d['rssi_trend'] = calc_rssi_trend(self._rssi_history.get(key, []))
                result.append(d)

        return result

    def get_drone_detail(self, serial, track_minutes=5):
        """Get detailed info + track for a specific drone."""
        rx_lat, rx_lon, rx_alt = self._gps.get_receiver_position()

        with self._lock:
            device = self._active_drones.get(serial)

        drone_dict = None
        if device:
            drone_dict = device.to_dict(rx_lat, rx_lon, rx_alt)
            drone_dict['rssi_trend'] = calc_rssi_trend(self._rssi_history.get(serial, []))
        else:
            # Try from DB
            row = self._db.get_drone_by_serial(serial)
            if row:
                drone_dict = row

        track = self._db.get_drone_track(serial, track_minutes)
        return drone_dict, track

    def _monitor_health_check(self):
        """Background check: verify the adapter is actually delivering frames.

        Some drivers (notably iwlwifi on Intel AX200/AX201/AX203/AX210) report
        monitor mode as supported but the firmware silently drops all frames.
        We wait a few seconds after capture starts and check if any raw 802.11
        frames have arrived.  If not, set a warning for the user.
        """
        # Wait 8 seconds — even on a quiet channel, APs beacon every ~100ms
        # so we should see dozens of frames if the adapter is working
        for _ in range(16):
            sleep(0.5)
            if not self._monitoring:
                return
            if self._frame_count > 0:
                # Frames are flowing — adapter works
                driver = self._get_driver_name()
                if driver:
                    print(f"  Monitor:  Receiving frames on {self._interface} (driver: {driver})")
                return

        # Zero frames after 8 seconds — something is wrong
        driver = self._get_driver_name()
        driver_hint = f" (driver: {driver})" if driver else ""
        msg = (
            f"WARNING: No frames received on {self._interface}{driver_hint} after 8 seconds. "
            f"The adapter may not support monitor mode at the firmware level."
        )
        self._monitor_warning = msg
        print(f"  {msg}")

        if driver and 'iwlwifi' in driver:
            print(
                "  NOTE: Intel iwlwifi adapters (AX200/AX201/AX203/AX210) often report "
                "monitor mode as supported but the firmware filters all frames. "
                "Use an external USB adapter (Alfa, Realtek RTL8812AU, Atheros) instead."
            )

    @staticmethod
    def _get_driver_name():
        """Try to identify the WiFi driver from sysfs."""
        try:
            for iface_dir in os.listdir('/sys/class/net/'):
                driver_link = f'/sys/class/net/{iface_dir}/device/driver'
                if os.path.islink(driver_link):
                    driver = os.path.basename(os.readlink(driver_link))
                    if driver in ('iwlwifi', 'ath9k_htc', 'rtl8812au', 'rt2800usb',
                                  'mt76x2u', 'rtl88xxau', 'ath10k_pci', 'ath11k_pci',
                                  'brcmfmac', 'rtw88_8822bu', 'rtw89_8852be'):
                        return driver
        except OSError:
            pass
        return None

    def get_status(self):
        """Get monitoring status."""
        duration = 0
        if self._started_at and self._monitoring:
            duration = int((datetime.utcnow() - self._started_at).total_seconds())

        return {
            'monitoring': self._monitoring,
            'interface': self._interface,
            'channel': self._channel,
            'started_at': self._started_at.isoformat() + 'Z' if self._started_at else None,
            'duration_seconds': duration,
            'frame_count': self._frame_count,
            'droneid_frame_count': self._droneid_frame_count,
            'capture_errors': self._capture_errors,
            'monitor_warning': self._monitor_warning,
            'ble_enabled': self._ble_enabled,
            'ble_frame_count': self._ble_frame_count,
        }

    def cleanup_stale(self, max_age=300):
        """Remove drones from active tracking that haven't been seen recently."""
        now = datetime.utcnow()
        with self._lock:
            stale_keys = []
            for key, device in self._active_drones.items():
                try:
                    last = datetime.fromisoformat(device.last_seen.replace('Z', ''))
                    if (now - last).total_seconds() > max_age:
                        stale_keys.append(key)
                except (ValueError, AttributeError):
                    stale_keys.append(key)
            for key in stale_keys:
                del self._active_drones[key]
                self._rssi_history.pop(key, None)


def check_prerequisites():
    """Check that required tools are available. Returns list of error messages."""
    errors = []
    if os.geteuid() != 0:
        errors.append("Must run as root for monitor mode capture. Use sudo.")
    if not shutil.which('tcpdump'):
        errors.append("tcpdump not found. Install with: sudo apt install tcpdump")
    if not shutil.which('iw'):
        errors.append("iw not found. Install with: sudo apt install iw")
    return errors
