"""
GPS engine for Sparrow DroneID.

Provides receiver position via three modes:
  - none   : no GPS; all coordinates are 0.0
  - gpsd   : poll gpsd via the gps3 library on a daemon thread
  - static : fixed user-configured coordinates
"""

from threading import Thread, Lock
from time import sleep

from .models import GPSMode

# Optional dependency — import at module level so we can probe availability once.
try:
    from gps3.agps3threaded import AGPS3mechanism
    _GPS3_AVAILABLE = True
except ImportError:
    _GPS3_AVAILABLE = False

_POLL_INTERVAL = 1.0  # seconds between gpsd reads


class _GpsdPoller(Thread):
    """Background daemon thread that reads position from gpsd via gps3."""

    def __init__(self, engine: "GPSEngine") -> None:
        super().__init__(daemon=True, name="GpsdPoller")
        self._engine = engine
        self._stop = False
        self._agps = AGPS3mechanism()
        self._agps.stream_data()
        self._agps.run_thread()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            try:
                ds = self._agps.data_stream

                lat = float(ds.lat) if type(ds.lat) is not str else None
                lon = float(ds.lon) if type(ds.lon) is not str else None
                alt = float(ds.alt) if type(ds.alt) is not str else None
                spd = float(ds.speed) if type(ds.speed) is not str else None

                # Only update if we have a usable fix (lat + lon present).
                if lat is not None and lon is not None:
                    self._engine._set_position(
                        fix=True,
                        lat=lat,
                        lon=lon,
                        alt=alt if alt is not None else 0.0,
                        speed=spd if spd is not None else 0.0,
                    )
                else:
                    self._engine._set_position(fix=False, lat=0.0, lon=0.0,
                                                alt=0.0, speed=0.0)
            except Exception:
                # Never let a read error kill the thread.
                pass

            sleep(_POLL_INTERVAL)


class GPSEngine:
    """Thread-safe GPS position provider.

    Supports three modes:
      GPSMode.NONE   — always returns zero coordinates, fix=False
      GPSMode.GPSD   — polls gpsd; requires gps3 library
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

        if mode == GPSMode.GPSD and not _GPS3_AVAILABLE:
            # Degrade gracefully rather than crashing.
            mode = GPSMode.NONE

        with self._lock:
            self._mode = mode
            self._fix = False
            self._lat = 0.0
            self._lon = 0.0
            self._alt = 0.0
            self._speed = 0.0

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
            }
