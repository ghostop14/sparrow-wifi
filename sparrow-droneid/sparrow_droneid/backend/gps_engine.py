"""
GPS engine for Sparrow DroneID.

Provides receiver position via three modes:
  - none   : no GPS; all coordinates are 0.0
  - gpsd   : poll gpsd via native JSON protocol on a daemon thread
  - static : fixed user-configured coordinates
"""

import json
import logging
import socket
from threading import Thread, Lock
from time import sleep

from .models import GPSMode

log = logging.getLogger(__name__)

_GPSD_HOST = '127.0.0.1'
_GPSD_PORT = 2947
_RECONNECT_INTERVAL = 5.0   # seconds between reconnection attempts
_SOCKET_TIMEOUT = 3.0        # read timeout per recv()


class _GpsdPoller(Thread):
    """Background daemon thread that reads position from gpsd via its native
    JSON protocol.  Handles gpsd restarts, late GPS dongle attachment, and
    transitions from no-fix → fix transparently.
    """

    def __init__(self, engine: "GPSEngine") -> None:
        super().__init__(daemon=True, name="GpsdPoller")
        self._engine = engine
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ---- internal helpers ------------------------------------------------

    def _connect(self) -> socket.socket:
        """Open a TCP connection to gpsd and send the WATCH command."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_SOCKET_TIMEOUT)
        sock.connect((_GPSD_HOST, _GPSD_PORT))
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        return sock

    def _process_tpv(self, tpv: dict) -> None:
        """Extract position from a gpsd TPV message."""
        mode = tpv.get('mode', 0)
        if mode >= 2:  # 2D or 3D fix
            lat = tpv.get('lat')
            lon = tpv.get('lon')
            if lat is not None and lon is not None:
                alt = tpv.get('alt', tpv.get('altMSL', 0.0)) or 0.0
                spd = tpv.get('speed', 0.0) or 0.0
                self._engine._set_position(
                    fix=True,
                    lat=float(lat),
                    lon=float(lon),
                    alt=float(alt),
                    speed=float(spd),
                )
                self._engine._set_gps_error('')
                return
        # No usable fix
        self._engine._set_position(fix=False, lat=0.0, lon=0.0,
                                   alt=0.0, speed=0.0)

    # ---- main loop -------------------------------------------------------

    def run(self) -> None:
        buf = b''
        sock = None

        while not self._stop:
            # (Re)connect if needed
            if sock is None:
                try:
                    sock = self._connect()
                    self._engine._set_gps_error('')
                    log.info("GpsdPoller: connected to gpsd at %s:%d",
                             _GPSD_HOST, _GPSD_PORT)
                except OSError as e:
                    self._engine._set_gps_error(f"gpsd connect failed: {e}")
                    self._engine._set_position(fix=False, lat=0.0, lon=0.0,
                                               alt=0.0, speed=0.0)
                    sleep(_RECONNECT_INTERVAL)
                    continue

            # Read from socket
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    # gpsd closed the connection — reconnect
                    log.warning("GpsdPoller: gpsd closed connection, reconnecting")
                    sock.close()
                    sock = None
                    buf = b''
                    continue
                buf += chunk
            except socket.timeout:
                # No data within timeout — loop back and try again
                continue
            except OSError as e:
                log.warning("GpsdPoller: socket error: %s, reconnecting", e)
                sock.close()
                sock = None
                buf = b''
                self._engine._set_gps_error(f"gpsd read error: {e}")
                sleep(_RECONNECT_INTERVAL)
                continue

            # Process complete JSON lines
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if msg.get('class') == 'TPV':
                    self._process_tpv(msg)

        # Cleanup
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class GPSEngine:
    """Thread-safe GPS position provider.

    Supports three modes:
      GPSMode.NONE   — always returns zero coordinates, fix=False
      GPSMode.GPSD   — polls gpsd via native JSON protocol
      GPSMode.STATIC — returns user-supplied fixed coordinates, fix=True
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._mode: GPSMode = GPSMode.NONE
        self._fix: bool = False
        self._lat: float = 0.0
        self._lon: float = 0.0
        self._alt: float = 0.0
        self._speed: float = 0.0
        self._poller: "_GpsdPoller | None" = None
        self._gps_error: str = ''

    # ------------------------------------------------------------------
    # Configuration / lifecycle
    # ------------------------------------------------------------------

    def configure(self, mode: str | GPSMode,
                  static_lat: float = 0.0,
                  static_lon: float = 0.0,
                  static_alt: float = 0.0) -> None:
        """Switch GPS mode.

        Args:
            mode: One of GPSMode.NONE / 'none', GPSMode.GPSD / 'gpsd',
                  GPSMode.STATIC / 'static'.
            static_lat: Latitude for static mode.
            static_lon: Longitude for static mode.
            static_alt: Altitude (m) for static mode.
        """
        mode = GPSMode(mode)  # normalise string → enum

        # Stop any existing poller before switching mode.
        self.stop()

        with self._lock:
            self._mode = mode
            self._fix = False
            self._lat = 0.0
            self._lon = 0.0
            self._alt = 0.0
            self._speed = 0.0
            if mode != GPSMode.GPSD:
                # Clear any previous gpsd error when switching away from gpsd mode
                self._gps_error = ''

        if mode == GPSMode.STATIC:
            with self._lock:
                self._fix = True
                self._lat = float(static_lat)
                self._lon = float(static_lon)
                self._alt = float(static_alt)
        elif mode == GPSMode.GPSD:
            self.start()

    def start(self) -> None:
        """Start the gpsd polling thread (no-op if not in gpsd mode or already running)."""
        with self._lock:
            if self._mode != GPSMode.GPSD:
                return
            if self._poller is not None and self._poller.is_alive():
                return

        poller = _GpsdPoller(self)
        with self._lock:
            self._poller = poller
        poller.start()

    def stop(self) -> None:
        """Stop the gpsd polling thread (no-op in other modes)."""
        with self._lock:
            poller = self._poller
            self._poller = None

        if poller is not None:
            poller.stop()

    # ------------------------------------------------------------------
    # Internal write path (called by _GpsdPoller)
    # ------------------------------------------------------------------

    def _set_position(self, *, fix: bool, lat: float, lon: float,
                      alt: float, speed: float) -> None:
        with self._lock:
            self._fix = fix
            self._lat = lat
            self._lon = lon
            self._alt = alt
            self._speed = speed

    def _set_gps_error(self, error: str) -> None:
        """Thread-safe setter for the GPS error string (called by _GpsdPoller)."""
        with self._lock:
            self._gps_error = error

    # ------------------------------------------------------------------
    # Thread-safe property getters
    # ------------------------------------------------------------------

    @property
    def mode(self) -> GPSMode:
        with self._lock:
            return self._mode

    @property
    def fix(self) -> bool:
        with self._lock:
            return self._fix

    @property
    def latitude(self) -> float:
        with self._lock:
            return self._lat

    @property
    def longitude(self) -> float:
        with self._lock:
            return self._lon

    @property
    def altitude(self) -> float:
        with self._lock:
            return self._alt

    @property
    def speed(self) -> float:
        with self._lock:
            return self._speed

    # ------------------------------------------------------------------
    # Public convenience API
    # ------------------------------------------------------------------

    def get_receiver_position(self) -> tuple[float, float, float]:
        """Return (latitude, longitude, altitude) as a single atomic read."""
        with self._lock:
            return (self._lat, self._lon, self._alt)

    def to_dict(self) -> dict:
        """Serialise current state to a JSON-safe dict."""
        with self._lock:
            return {
                "mode":      self._mode.value,
                "fix":       self._fix,
                "latitude":  self._lat,
                "longitude": self._lon,
                "altitude":  self._alt,
                "speed":     self._speed,
                "gps_error": self._gps_error,
            }
