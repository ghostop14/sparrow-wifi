"""
Server-side cache manager for airport geozones and FAA no-fly zones.

Downloads and caches:
- OurAirports airport list (airports.csv)
- FAA Special Use Airspace GeoJSON (restricted/prohibited zones)

Cache files are refreshed in background threads when stale (>48 h).
"""
import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2

import requests

log = logging.getLogger(__name__)

# --------------- Constants ---------------------------------------------------

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
NOFLY_URL = (
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/ArcGIS/rest/services"
    "/Special_Use_Airspace/FeatureServer/0/query"
)
NOFLY_PARAMS = {
    "where": "TYPE_CODE IN ('P','R')",
    "outFields": "NAME,TYPE_CODE,STATE",
    "outSR": "4326",
    "f": "geojson",
}

AIRPORT_TYPES = {"large_airport", "medium_airport", "small_airport"}
CACHE_TTL_SECONDS = 48 * 3600
EARTH_RADIUS_MI = 3958.8


# --------------- Cache manager -----------------------------------------------

class GeozoneCache:
    """Thread-safe cache for airport and FAA no-fly zone data."""

    def __init__(self, data_dir: str) -> None:
        self._cache_dir = os.path.join(data_dir, "geozones")
        os.makedirs(self._cache_dir, exist_ok=True)

        self._airports_path = os.path.join(self._cache_dir, "airports.json")
        self._nofly_path = os.path.join(self._cache_dir, "nofly.json")

        self._refresh_lock = threading.Lock()
        self._session = requests.Session()

    # --------------- Public API ----------------------------------------------

    def get_airports(
        self, receiver_lat: float, receiver_lon: float, radius_mi: float = 50
    ) -> list:
        """Return airports within radius_mi of the receiver position.

        Triggers a background refresh when the cache is stale.  Returns an
        empty list on the first run before the download completes.
        """
        if self._is_stale(self._airports_path):
            self._refresh_in_background(self.refresh_airports)

        airports = self._load_airports()
        return [
            a for a in airports
            if self._haversine_mi(receiver_lat, receiver_lon, a["lat"], a["lon"])
            <= radius_mi
        ]

    def get_nofly_zones(
        self, receiver_lat: float, receiver_lon: float, radius_mi: float = 100
    ) -> dict:
        """Return FAA no-fly GeoJSON features near the receiver position.

        Uses the centroid of the first ring of each polygon for distance
        filtering.  Triggers a background refresh when the cache is stale.
        """
        if self._is_stale(self._nofly_path):
            self._refresh_in_background(self.refresh_nofly)

        features = self._load_nofly_features()
        nearby = []
        for feat in features:
            centroid = self._feature_centroid(feat)
            if centroid is None:
                continue
            clat, clon = centroid
            if self._haversine_mi(receiver_lat, receiver_lon, clat, clon) <= radius_mi:
                nearby.append(feat)

        return {"type": "FeatureCollection", "features": nearby}

    # --------------- Refresh methods -----------------------------------------

    def refresh_airports(self) -> None:
        """Download and cache the OurAirports airport list."""
        log.info("Refreshing airport cache from %s", AIRPORTS_URL)
        try:
            resp = self._session.get(AIRPORTS_URL, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Airport download failed: %s", exc)
            return

        airports = []
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            if row.get("type") not in AIRPORT_TYPES:
                continue
            try:
                airports.append({
                    "ident": row["ident"],
                    "name": row["name"],
                    "type": row["type"],
                    "lat": float(row["latitude_deg"]),
                    "lon": float(row["longitude_deg"]),
                    "country": row.get("iso_country", ""),
                })
            except (KeyError, ValueError):
                continue

        payload = {
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "airports": airports,
        }
        self._atomic_write(self._airports_path, payload)
        log.info("Airport cache written: %d entries", len(airports))

    def refresh_nofly(self) -> None:
        """Download and cache FAA restricted/prohibited airspace GeoJSON."""
        log.info("Refreshing no-fly zone cache from FAA ArcGIS")
        try:
            resp = self._session.get(NOFLY_URL, params=NOFLY_PARAMS, timeout=30)
            resp.raise_for_status()
            geojson = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("No-fly zone download failed: %s", exc)
            return

        geojson["fetched_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._atomic_write(self._nofly_path, geojson)
        features = len(geojson.get("features", []))
        log.info("No-fly zone cache written: %d features", features)

    # --------------- Internal helpers ----------------------------------------

    def _is_stale(self, path: str) -> bool:
        """Return True if the cache file is missing or older than CACHE_TTL_SECONDS."""
        if not os.path.isfile(path):
            return True
        age = time.time() - os.path.getmtime(path)
        return age > CACHE_TTL_SECONDS

    def _refresh_in_background(self, target) -> None:
        """Spawn a daemon thread to run target(), guarded by a lock."""
        if not self._refresh_lock.acquire(blocking=False):
            return  # refresh already in progress
        def _run():
            try:
                target()
            finally:
                self._refresh_lock.release()
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _haversine_mi(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Return the great-circle distance in miles between two lat/lon points."""
        lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 2 * EARTH_RADIUS_MI * atan2(sqrt(a), sqrt(1 - a))

    def _load_airports(self) -> list:
        """Load airports from cache, returning an empty list on any error."""
        try:
            with open(self._airports_path, "r", encoding="utf-8") as fh:
                return json.load(fh).get("airports", [])
        except (OSError, ValueError):
            return []

    def _load_nofly_features(self) -> list:
        """Load no-fly GeoJSON features from cache, returning empty list on error."""
        try:
            with open(self._nofly_path, "r", encoding="utf-8") as fh:
                return json.load(fh).get("features", [])
        except (OSError, ValueError):
            return []

    @staticmethod
    def _feature_centroid(feature: dict):
        """Return (lat, lon) centroid of a GeoJSON feature's first ring.

        Returns None if the geometry cannot be parsed.
        """
        try:
            geom = feature.get("geometry", {})
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if gtype == "Polygon":
                ring = coords[0]
            elif gtype == "MultiPolygon":
                ring = coords[0][0]
            else:
                return None
            # First coordinate pair in the ring: [lon, lat]
            lon, lat = ring[0][0], ring[0][1]
            return lat, lon
        except (AttributeError, IndexError, KeyError, TypeError):
            return None

    @staticmethod
    def _atomic_write(path: str, data: dict) -> None:
        """Write data as JSON to path via a temp file for atomicity."""
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
