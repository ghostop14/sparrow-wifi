"""
DroneID capture engine and ODID frame parser.

Manages monitor-mode WiFi capture via tcpdump and decodes:
- ASTM F3411 via Wi-Fi NAN action frames (OUI 50:6F:9A)
- ASTM F3411 via Wi-Fi beacon vendor IE (OUI FA:0B:BC)
- DJI proprietary DroneID via beacon vendor IE (OUI 26:37:12)
"""
import struct
import subprocess
import re
import shutil
import threading
import os
from datetime import datetime
from time import sleep

from .models import (
    DroneIDDevice, WifiInterface, Protocol, UAType,
    rssi_trend as calc_rssi_trend,
)
from .database import Database


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

# 802.11 frame type/subtype values
SUBTYPE_BEACON = 0x08
SUBTYPE_PROBE_RESP = 0x05
SUBTYPE_ACTION = 0x0D

# Regex for parsing tcpdump header lines
# Primary RSSI pattern: matches "-68dBm", "-68 dBm", "-68dB" (with word-boundary)
_RE_SIGNAL = re.compile(r'(-?\d+)\s*dBm?\b', re.IGNORECASE)
# Fallback: matches "signal -72" or "signal: -72" when no dBm suffix is present
_RE_SIGNAL_BARE = re.compile(r'signal\s*:?\s*(-\d+)\b', re.IGNORECASE)
_RE_HEX_LINE = re.compile(r'^\s+0x[0-9a-f]+:\s+(.+)$')
_RE_FRAME_HEADER = re.compile(r'^\d{2}:\d{2}:\d{2}\.\d+')
_RE_SA = re.compile(r'SA:([0-9a-fA-F:]{17})')


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
        else:
            if not device.serial_number:
                device.serial_number = id_str

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
            device.speed = (raw_speed * 10.0 + 255.0 * 0.25) * 0.25
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
        """Launch tcpdump for ODID-relevant frame capture."""
        bpf = '(type mgt subtype action) or (type mgt subtype beacon) or (type mgt subtype probe-resp)'
        cmd = [
            'tcpdump', '-i', interface,
            '-e',                   # Print link-layer header (for SA, signal)
            '-l',                   # Line-buffered output
            '-x',                   # Hex dump of frame
            '--immediate-mode',     # Immediate output
            '-s', '0',              # Full frame capture
            bpf,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
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

        self._parse_thread = threading.Thread(target=self._parse_loop, daemon=True)
        self._parse_thread.start()

    def stop(self):
        """Stop monitoring and restore interface."""
        self._monitoring = False
        CaptureManager.stop_capture(self._capture_proc)
        self._capture_proc = None

        if self._parse_thread:
            self._parse_thread.join(timeout=5)
            self._parse_thread = None

        if self._interface:
            try:
                CaptureManager.stop_monitor(self._interface)
            except Exception:
                pass
            self._interface = ""

    def _parse_loop(self):
        """Read tcpdump output and parse ODID frames."""
        proc = self._capture_proc
        if proc is None or proc.stdout is None:
            return

        hex_lines = []
        header_info = {}  # rssi, mac from the tcpdump text header

        for raw_line in iter(proc.stdout.readline, b''):
            if not self._monitoring:
                break

            try:
                line = raw_line.decode('ascii', errors='replace').rstrip()
            except Exception:
                continue

            # Check if this is a hex continuation line
            hex_match = _RE_HEX_LINE.match(line)
            if hex_match:
                hex_lines.append(hex_match.group(1))
                continue

            # This is a new frame header — process the previous frame
            if hex_lines and _RE_FRAME_HEADER.match(line):
                self._frame_count += 1
                try:
                    frame_bytes = self._hex_to_bytes(hex_lines)
                    self._process_frame(frame_bytes, header_info)
                except Exception:
                    self._capture_errors += 1
                hex_lines = []

            # Parse the new header line for RSSI and MAC
            if _RE_FRAME_HEADER.match(line):
                header_info = self._parse_header_line(line)
                hex_lines = []

        # Process last frame
        if hex_lines:
            try:
                frame_bytes = self._hex_to_bytes(hex_lines)
                self._process_frame(frame_bytes, header_info)
            except Exception:
                pass

    @staticmethod
    def _hex_to_bytes(hex_lines):
        """Convert tcpdump hex output lines to bytes."""
        hex_str = ''
        for line in hex_lines:
            # Remove the offset prefix and join hex pairs
            hex_str += line.replace(' ', '')
        return bytes.fromhex(hex_str)

    @staticmethod
    def _parse_header_line(line):
        """Extract RSSI and source MAC from tcpdump header line.

        tcpdump can emit RSSI in two formats:
          - "-68dBm" or "-68 dBm"  (matched by _RE_SIGNAL)
          - "signal -72"           (matched by _RE_SIGNAL_BARE fallback)
        """
        info = {'rssi': 0, 'mac': ''}
        sig = _RE_SIGNAL.search(line)
        if sig:
            try:
                info['rssi'] = int(sig.group(1))
            except ValueError:
                pass
        else:
            # Fallback: "signal -72" without explicit dBm suffix
            sig2 = _RE_SIGNAL_BARE.search(line)
            if sig2:
                try:
                    info['rssi'] = int(sig2.group(1))
                except ValueError:
                    pass
        sa = _RE_SA.search(line)
        if sa:
            info['mac'] = sa.group(1).lower()
        return info

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

        now = datetime.utcnow().isoformat() + 'Z'
        key = device.get_key()

        self._droneid_frame_count += 1

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
