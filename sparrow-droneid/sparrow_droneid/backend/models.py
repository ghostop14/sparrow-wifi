"""
Data models for Sparrow DroneID.

Dataclasses for drone detections, alerts, and configuration.
Enums for ASTM F3411 field values.
Utility functions for geospatial calculations.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import IntEnum, Enum
from math import radians, sin, cos, sqrt, atan2, degrees
from typing import Optional, Dict, List
import json


# --------------- Enums (ASTM F3411-22a) ----------------------------------

class IdType(IntEnum):
    NONE = 0
    SERIAL_NUMBER = 1       # ANSI/CTA-2063-A
    CAA_REGISTRATION = 2
    UTM_UUID = 3
    SPECIFIC_SESSION = 4

    @property
    def display_name(self) -> str:
        _names = {
            0: "None",
            1: "Serial Number (ANSI/CTA-2063-A)",
            2: "CAA Assigned Registration ID",
            3: "UTM Assigned UUID",
            4: "Specific Session ID",
        }
        return _names.get(self.value, "Unknown")


class UAType(IntEnum):
    NONE = 0
    AEROPLANE = 1
    HELICOPTER = 2      # or Multirotor
    GYROPLANE = 3
    HYBRID_LIFT = 4     # VTOL
    ORNITHOPTER = 5
    GLIDER = 6
    KITE = 7
    FREE_BALLOON = 8
    CAPTIVE_BALLOON = 9
    AIRSHIP = 10
    FREE_FALL = 11      # Parachute
    ROCKET = 12
    TETHERED = 13       # Tethered Powered Aircraft
    GROUND_OBSTACLE = 14
    OTHER = 15

    @property
    def display_name(self) -> str:
        _names = {
            0: "None / Not Declared",
            1: "Aeroplane",
            2: "Helicopter / Multirotor",
            3: "Gyroplane",
            4: "Hybrid Lift (VTOL)",
            5: "Ornithopter",
            6: "Glider",
            7: "Kite",
            8: "Free Balloon",
            9: "Captive Balloon",
            10: "Airship",
            11: "Free Fall / Parachute",
            12: "Rocket",
            13: "Tethered Powered Aircraft",
            14: "Ground Obstacle",
            15: "Other",
        }
        return _names.get(self.value, "Unknown")


class Protocol(str, Enum):
    ASTM_NAN = "astm_nan"
    ASTM_BEACON = "astm_beacon"
    ASTM_BLE = "astm_ble"
    DJI_PROPRIETARY = "dji_proprietary"


class AltitudeClass(str, Enum):
    GROUND = "GROUND"       # < 3m AGL
    LOW = "LOW"             # 3-30m AGL
    MEDIUM = "MEDIUM"       # 30-120m AGL
    HIGH = "HIGH"           # 120-400m AGL (near FAA 400ft limit)
    ILLEGAL = "ILLEGAL"     # > 400m AGL (122m ≈ 400ft)


class DroneState(str, Enum):
    ACTIVE = "active"       # 0-30s since last seen
    AGING = "aging"         # 30-90s
    STALE = "stale"         # 90-180s


class GPSMode(str, Enum):
    NONE = "none"
    GPSD = "gpsd"
    STATIC = "static"


class AlertType(str, Enum):
    NEW_DRONE = "new_drone"
    ALTITUDE_MAX = "altitude_max"
    SPEED_MAX = "speed_max"
    SIGNAL_LOST = "signal_lost"


# --------------- Utility Functions ----------------------------------------

_EARTH_RADIUS_M = 6_371_000.0

# Thresholds
_ALT_GROUND = 3.0
_ALT_LOW = 30.0
_ALT_MEDIUM = 120.0

# State age thresholds (seconds)
_STATE_ACTIVE = 30.0
_STATE_AGING = 90.0


def altitude_class(height_agl: float) -> AltitudeClass:
    """Classify drone altitude above ground level."""
    if height_agl is None or height_agl < _ALT_GROUND:
        return AltitudeClass.GROUND
    elif height_agl < _ALT_LOW:
        return AltitudeClass.LOW
    elif height_agl < _ALT_MEDIUM:
        return AltitudeClass.MEDIUM
    elif height_agl < 122.0:  # 400ft = 121.92m
        return AltitudeClass.HIGH
    else:
        return AltitudeClass.ILLEGAL


def drone_state(last_seen_str: str, now: datetime = None) -> DroneState:
    """Determine drone state from last-seen timestamp age."""
    if now is None:
        now = datetime.utcnow()
    try:
        last_seen = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00').replace('+00:00', ''))
    except (ValueError, AttributeError):
        return DroneState.STALE
    age = (now - last_seen).total_seconds()
    if age <= _STATE_ACTIVE:
        return DroneState.ACTIVE
    elif age <= _STATE_AGING:
        return DroneState.AGING
    else:
        return DroneState.STALE


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    rlat1, rlon1, rlat2, rlon2 = radians(lat1), radians(lon1), radians(lat2), radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return _EARTH_RADIUS_M * 2 * atan2(sqrt(a), sqrt(1 - a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from point 1 to point 2 (0=North, CW)."""
    rlat1, rlon1, rlat2, rlon2 = radians(lat1), radians(lon1), radians(lat2), radians(lon2)
    dlon = rlon2 - rlon1
    x = sin(dlon) * cos(rlat2)
    y = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


_CARDINAL_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def bearing_cardinal(deg: float) -> str:
    """Convert bearing in degrees to 16-point cardinal direction."""
    idx = round(deg / 22.5) % 16
    return _CARDINAL_POINTS[idx]


def rssi_trend(history: List[int], window: int = 5) -> str:
    """Determine RSSI trend from a history of values.

    Returns 'strengthening', 'stable', or 'weakening'.
    """
    if len(history) < 2:
        return "stable"
    recent = history[-window:] if len(history) >= window else history
    if len(recent) < 2:
        return "stable"
    # Simple linear: compare first half average to second half average
    mid = len(recent) // 2
    first_avg = sum(recent[:mid]) / mid
    second_avg = sum(recent[mid:]) / len(recent[mid:])
    delta = second_avg - first_avg
    if delta > 3.0:
        return "strengthening"
    elif delta < -3.0:
        return "weakening"
    return "stable"


# --------------- Data Models ----------------------------------------------

@dataclass
class DroneIDDevice:
    """Core model for a detected drone via Remote ID."""
    serial_number: str = ""
    registration_id: str = ""
    id_type: int = 0
    ua_type: int = 0

    # Drone position
    drone_lat: float = 0.0
    drone_lon: float = 0.0
    drone_alt_geo: float = 0.0
    drone_alt_baro: float = 0.0
    drone_height_agl: float = 0.0

    # Drone velocity
    speed: float = 0.0
    direction: float = 0.0
    vertical_speed: float = 0.0

    # Operator info
    operator_lat: float = 0.0
    operator_lon: float = 0.0
    operator_alt: float = 0.0
    operator_id: str = ""
    self_id_text: str = ""

    # Auth (optional, stored but not heavily used in v1)
    auth_type: int = 0
    auth_data: str = ""

    # RF metadata
    mac_address: str = ""
    rssi: int = 0
    channel: int = 6
    frequency: int = 2437

    # Protocol
    protocol: str = Protocol.ASTM_NAN.value

    # Timestamps
    first_seen: str = ""
    last_seen: str = ""

    def get_key(self) -> str:
        """Primary key for tracking: prefer serial, fall back to registration or MAC."""
        if self.serial_number:
            return self.serial_number
        if self.registration_id:
            return self.registration_id
        return self.mac_address

    def to_dict(self, receiver_lat: float = None, receiver_lon: float = None,
                receiver_alt: float = None) -> dict:
        """Convert to dict with optional derived fields from receiver position."""
        data = asdict(self)
        data['id_type_name'] = IdType(self.id_type).display_name if 0 <= self.id_type <= 4 else "Unknown"
        data['ua_type_name'] = UAType(self.ua_type).display_name if 0 <= self.ua_type <= 15 else "Unknown"

        # Time in area
        try:
            first = datetime.fromisoformat(self.first_seen.replace('Z', ''))
            last = datetime.fromisoformat(self.last_seen.replace('Z', ''))
            data['time_in_area_seconds'] = int((last - first).total_seconds())
        except (ValueError, AttributeError):
            data['time_in_area_seconds'] = 0

        # Derived fields
        derived = {
            'state': drone_state(self.last_seen).value,
            'altitude_class': altitude_class(self.drone_height_agl).value,
        }

        has_receiver = (receiver_lat is not None and receiver_lon is not None
                        and (receiver_lat != 0.0 or receiver_lon != 0.0))
        has_drone_pos = self.drone_lat != 0.0 or self.drone_lon != 0.0
        has_operator_pos = self.operator_lat != 0.0 or self.operator_lon != 0.0

        if has_receiver and has_drone_pos:
            range_m = haversine(receiver_lat, receiver_lon, self.drone_lat, self.drone_lon)
            bearing_deg = bearing(receiver_lat, receiver_lon, self.drone_lat, self.drone_lon)
            derived['range_m'] = round(range_m, 1)
            derived['bearing_deg'] = round(bearing_deg, 1)
            derived['bearing_cardinal'] = bearing_cardinal(bearing_deg)
        else:
            derived['range_m'] = None
            derived['bearing_deg'] = None
            derived['bearing_cardinal'] = None

        if has_receiver and has_operator_pos:
            op_range = haversine(receiver_lat, receiver_lon, self.operator_lat, self.operator_lon)
            op_bearing = bearing(receiver_lat, receiver_lon, self.operator_lat, self.operator_lon)
            derived['operator_range_m'] = round(op_range, 1)
            derived['operator_bearing_deg'] = round(op_bearing, 1)
            derived['operator_bearing_cardinal'] = bearing_cardinal(op_bearing)
        else:
            derived['operator_range_m'] = None
            derived['operator_bearing_deg'] = None
            derived['operator_bearing_cardinal'] = None

        data['derived'] = derived
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'DroneIDDevice':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TrackPoint:
    """A single position sample for track history."""
    drone_lat: float = 0.0
    drone_lon: float = 0.0
    drone_alt_geo: float = 0.0
    drone_height_agl: float = 0.0
    speed: float = 0.0
    direction: float = 0.0
    rssi: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'TrackPoint':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AlertEvent:
    """A fired alert for the alert log."""
    id: int = 0
    timestamp: str = ""
    alert_type: str = ""
    serial_number: str = ""
    detail: str = ""
    drone_lat: float = 0.0
    drone_lon: float = 0.0
    drone_height_agl: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> 'AlertEvent':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AlertRule:
    """An alert rule definition."""
    id: int = 0
    name: str = ""
    type: str = AlertType.NEW_DRONE.value
    enabled: bool = True
    audio_sound: str = "chime"
    params: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'AlertRule':
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class WifiInterface:
    """A detected WiFi interface and its capabilities."""
    name: str = ""
    mac_address: str = ""
    mode: str = "managed"
    monitor_capable: bool = False
    driver: str = ""
    phy: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'WifiInterface':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --------------- Default Alert Rules --------------------------------------

DEFAULT_ALERT_RULES = [
    AlertRule(id=1, name="New drone detected", type=AlertType.NEW_DRONE.value,
             enabled=True, audio_sound="chime", params={}),
    AlertRule(id=2, name="Altitude violation", type=AlertType.ALTITUDE_MAX.value,
             enabled=True, audio_sound="alert", params={"max_altitude_m": 122}),
    AlertRule(id=3, name="Speed violation", type=AlertType.SPEED_MAX.value,
             enabled=True, audio_sound="alert", params={"max_speed_mps": 44.7}),
    AlertRule(id=4, name="Signal lost", type=AlertType.SIGNAL_LOST.value,
             enabled=True, audio_sound="chime", params={"timeout_seconds": 180}),
]
