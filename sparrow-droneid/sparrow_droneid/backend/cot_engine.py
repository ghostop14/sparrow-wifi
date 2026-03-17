"""
Cursor on Target (CoT) UDP multicast output engine for Sparrow DroneID.

Builds ASTM F3411-aware CoT XML events and fires them over UDP multicast so
that any TAK-family consumer (ATAK, WinTAK, TAK Server) can plot drone
detections in real time.
"""
import logging
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .models import DroneIDDevice, UAType

logger = logging.getLogger(__name__)

# CoT timestamp format (ISO 8601 Zulu, millisecond precision)
_COT_TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
# Stale offset applied to the event timestamp
_STALE_OFFSET_S = 10


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime(_COT_TS_FMT)


def _ua_type_name(ua_type_int: int) -> str:
    try:
        return UAType(ua_type_int).display_name
    except ValueError:
        return "Unknown"


class CotEngine:
    """Fire-and-forget CoT UDP multicast output engine."""

    def __init__(self) -> None:
        self.enabled: bool = False
        self.address: str = "239.2.3.1"
        self.port: int = 6969
        self._socket: socket.socket | None = None
        self._events_sent: int = 0

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, enabled: bool, address: str, port: int) -> None:
        """Apply new configuration, opening or closing the socket as needed."""
        addr_changed = address != self.address or port != self.port

        self.enabled = enabled
        self.address = address
        self.port = port

        if not enabled:
            self._close_socket()
            return

        # Re-open when newly enabled or target address/port changed.
        if self._socket is None or addr_changed:
            self._close_socket()
            self._open_socket()

    def stop(self) -> None:
        """Disable the engine and release the socket."""
        self.enabled = False
        self._close_socket()

    # ------------------------------------------------------------------
    # Event sending
    # ------------------------------------------------------------------

    def send_event(self, device: DroneIDDevice) -> None:
        """Build a CoT XML event for *device* and send it via UDP multicast.

        Does nothing if the engine is disabled or the device has no valid
        position fix (both lat and lon are 0.0).
        """
        if not self.enabled or self._socket is None:
            return

        # Require a non-trivial position fix.
        if device.drone_lat == 0.0 and device.drone_lon == 0.0:
            return

        xml_bytes = self._build_event(device)
        try:
            self._socket.sendto(xml_bytes, (self.address, self.port))
            self._events_sent += 1
        except OSError as exc:
            logger.warning("CoT send failed: %s", exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "address": self.address,
            "port": self.port,
            "events_sent": self._events_sent,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_socket(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
            self._socket = sock
        except OSError as exc:
            logger.error("CoT socket creation failed: %s", exc)
            self._socket = None

    def _close_socket(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _build_event(self, device: DroneIDDevice) -> bytes:
        serial = device.serial_number or device.registration_id or device.mac_address
        uid = f"sparrow-droneid-{serial}"
        callsign = f"DRONE-{serial[:4]}" if serial else "DRONE-UNKN"

        now = _now_utc()
        stale = now + timedelta(seconds=_STALE_OFFSET_S)
        ts = _fmt_ts(now)
        ts_stale = _fmt_ts(stale)

        # <event>
        event = ET.Element("event", {
            "version": "2.0",
            "uid": uid,
            "type": "a-n-A-C-F-q",
            "time": ts,
            "start": ts,
            "stale": ts_stale,
            "how": "m-f",
        })

        # <point>
        ET.SubElement(event, "point", {
            "lat": str(device.drone_lat),
            "lon": str(device.drone_lon),
            "hae": str(device.drone_alt_geo),
            "ce": "10.0",
            "le": "15.0",
        })

        # <detail>
        detail = ET.SubElement(event, "detail")

        ET.SubElement(detail, "track", {
            "course": str(device.direction),
            "speed": str(device.speed),
        })

        ua_name = _ua_type_name(device.ua_type)
        remarks_text = (
            f"SN:{serial} "
            f"UA:{ua_name} "
            f"AGL:{device.drone_height_agl}m "
            f"Self-ID:{device.self_id_text}"
        )
        remarks = ET.SubElement(detail, "remarks")
        remarks.text = remarks_text

        ET.SubElement(detail, "contact", {"callsign": callsign})

        ET.SubElement(detail, "__droneid", {
            "serial": serial,
            "ua_type": str(device.ua_type),
            "height_agl": str(device.drone_height_agl),
            "operator_lat": str(device.operator_lat),
            "operator_lon": str(device.operator_lon),
            "operator_id": device.operator_id,
            "self_id": device.self_id_text,
            "rssi": str(device.rssi),
            "protocol": device.protocol,
        })

        xml_declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'
        body = ET.tostring(event, encoding="unicode").encode("utf-8")
        return xml_declaration + body
