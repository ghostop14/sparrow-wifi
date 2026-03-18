"""
SQLite Database Access Layer for Sparrow DroneID.

Thread-safe singleton with connection pooling.
Tables: detections, alerts, settings.
"""
import sqlite3
import threading
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from contextlib import contextmanager

from .models import DroneIDDevice, AlertEvent, AlertRule, DEFAULT_ALERT_RULES
import json


class Database:
    """Thread-safe SQLite database with WAL mode for concurrent read/write."""

    def __init__(self, db_path: str = "data/sparrow_droneid.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure data directory exists
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA foreign_keys = ON")
        return self._local.connection

    @contextmanager
    def get_cursor(self):
        """Context manager for database cursor with auto-commit."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self):
        """Initialize database schema and default data."""
        with self.get_cursor() as cursor:
            # Detections table — one row per received ODID frame
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial_number TEXT NOT NULL,
                    registration_id TEXT DEFAULT '',
                    id_type INTEGER DEFAULT 0,
                    ua_type INTEGER DEFAULT 0,
                    drone_lat REAL DEFAULT 0.0,
                    drone_lon REAL DEFAULT 0.0,
                    drone_alt_geo REAL DEFAULT 0.0,
                    drone_alt_baro REAL DEFAULT 0.0,
                    drone_height_agl REAL DEFAULT 0.0,
                    speed REAL DEFAULT 0.0,
                    direction REAL DEFAULT 0.0,
                    vertical_speed REAL DEFAULT 0.0,
                    operator_lat REAL DEFAULT 0.0,
                    operator_lon REAL DEFAULT 0.0,
                    operator_alt REAL DEFAULT 0.0,
                    operator_id TEXT DEFAULT '',
                    self_id_text TEXT DEFAULT '',
                    mac_address TEXT DEFAULT '',
                    rssi INTEGER DEFAULT 0,
                    protocol TEXT DEFAULT 'astm_nan',
                    receiver_lat REAL DEFAULT 0.0,
                    receiver_lon REAL DEFAULT 0.0,
                    receiver_alt REAL DEFAULT 0.0,
                    timestamp TEXT NOT NULL
                )
            """)

            # Alert log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    serial_number TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    drone_lat REAL DEFAULT 0.0,
                    drone_lon REAL DEFAULT 0.0,
                    drone_height_agl REAL DEFAULT 0.0,
                    state TEXT NOT NULL DEFAULT 'ACTIVE',
                    acknowledged_by TEXT DEFAULT '',
                    acknowledged_at TEXT DEFAULT '',
                    resolved_at TEXT DEFAULT ''
                )
            """)

            # Settings table — key-value pairs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Migrate existing DBs: add new alert columns if they don't exist yet.
            # This must run BEFORE the idx_alerts_state index creation below.
            # SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so use try/except.
            for col_def in (
                "ADD COLUMN state TEXT NOT NULL DEFAULT 'ACTIVE'",
                "ADD COLUMN acknowledged_by TEXT DEFAULT ''",
                "ADD COLUMN acknowledged_at TEXT DEFAULT ''",
                "ADD COLUMN resolved_at TEXT DEFAULT ''",
            ):
                try:
                    cursor.execute(f"ALTER TABLE alerts {col_def}")
                except Exception:
                    pass  # Column already exists

            # Indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detections_serial ON detections(serial_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detections_timestamp ON detections(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detections_serial_ts ON detections(serial_number, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts(state)")

            self._init_defaults(cursor)

    def _init_defaults(self, cursor):
        """Populate default settings."""
        defaults = {
            'port': '8097',
            'bind_address': '0.0.0.0',
            'https_enabled': 'false',
            'https_cert_name': '',
            'auth_token': '',
            'allowed_ips': '',
            'gps_mode': 'gpsd',
            'gps_static_lat': '0.0',
            'gps_static_lon': '0.0',
            'gps_static_alt': '0.0',
            'retention_days': '14',
            'cot_enabled': 'false',
            'cot_address': '239.2.3.1',
            'cot_port': '6969',
            'alert_audio_enabled': 'true',
            'alert_visual_enabled': 'true',
            'alert_script_enabled': 'false',
            'alert_script_path': '',
            'alert_rules': json.dumps([r.to_dict() for r in DEFAULT_ALERT_RULES]),
            'alert_slack_enabled': 'false',
            'alert_slack_webhook_url': '',
            'alert_slack_display_name': 'Sparrow DroneID',
            'tile_cache_enabled': 'true',
            'monitor_interface': '',
            'operator_name': '',
            'airport_geozone_radius_mi': '2.0',
            'vendor_serial_prefixes': '',
            'vendor_mac_oui': '',
            'vendor_codes_url': '',
        }
        for key, value in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # ==================== Detection Operations ====================

    def insert_detection(self, device: DroneIDDevice,
                         receiver_lat: float = 0.0, receiver_lon: float = 0.0,
                         receiver_alt: float = 0.0) -> int:
        """Insert a detection record. Returns the row id."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO detections (
                    serial_number, registration_id, id_type, ua_type,
                    drone_lat, drone_lon, drone_alt_geo, drone_alt_baro, drone_height_agl,
                    speed, direction, vertical_speed,
                    operator_lat, operator_lon, operator_alt,
                    operator_id, self_id_text,
                    mac_address, rssi, protocol,
                    receiver_lat, receiver_lon, receiver_alt,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                device.serial_number, device.registration_id, device.id_type, device.ua_type,
                device.drone_lat, device.drone_lon, device.drone_alt_geo,
                device.drone_alt_baro, device.drone_height_agl,
                device.speed, device.direction, device.vertical_speed,
                device.operator_lat, device.operator_lon, device.operator_alt,
                device.operator_id, device.self_id_text,
                device.mac_address, device.rssi, device.protocol,
                receiver_lat, receiver_lon, receiver_alt,
                device.last_seen or datetime.utcnow().isoformat() + 'Z',
            ))
            return cursor.lastrowid

    def get_active_drones(self, max_age_seconds: int = 180) -> List[Dict]:
        """Get the latest detection for each drone seen within max_age_seconds.

        Returns one row per unique serial_number with the most recent data.
        """
        cutoff = (datetime.utcnow() - timedelta(seconds=max_age_seconds)).isoformat() + 'Z'
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT d.*, fs.first_seen FROM detections d
                INNER JOIN (
                    SELECT serial_number, MAX(timestamp) as max_ts
                    FROM detections
                    WHERE timestamp > ?
                    GROUP BY serial_number
                ) latest ON d.serial_number = latest.serial_number AND d.timestamp = latest.max_ts
                INNER JOIN (
                    SELECT serial_number, MIN(timestamp) as first_seen
                    FROM detections
                    GROUP BY serial_number
                ) fs ON d.serial_number = fs.serial_number
                ORDER BY d.timestamp DESC
            """, (cutoff,))
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row['last_seen'] = row['timestamp']
            return rows

    def get_drone_by_serial(self, serial: str) -> Optional[Dict]:
        """Get the latest detection for a specific drone."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM detections
                WHERE serial_number = ?
                ORDER BY timestamp DESC LIMIT 1
            """, (serial,))
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            cursor.execute(
                "SELECT MIN(timestamp) as first_seen FROM detections WHERE serial_number = ?",
                (serial,)
            )
            fs = cursor.fetchone()
            result['first_seen'] = fs['first_seen'] if fs else result['timestamp']
            result['last_seen'] = result['timestamp']
            return result

    def get_drone_track(self, serial: str, minutes: int = 5) -> List[Dict]:
        """Get track points for a drone over the last N minutes."""
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + 'Z'
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT drone_lat, drone_lon, drone_alt_geo, drone_height_agl,
                       speed, direction, rssi, timestamp
                FROM detections
                WHERE serial_number = ? AND timestamp > ?
                ORDER BY timestamp DESC
            """, (serial, cutoff))
            return [dict(row) for row in cursor.fetchall()]

    # ==================== History / Replay Operations ====================

    def get_history(self, from_ts: str, to_ts: str, serial: str = None,
                    limit: int = 10000, offset: int = 0) -> Tuple[List[Dict], int]:
        """Get detection records for a time range. Returns (records, total_count)."""
        with self.get_cursor() as cursor:
            params = [from_ts, to_ts]
            where = "WHERE timestamp >= ? AND timestamp <= ?"
            if serial:
                where += " AND serial_number = ?"
                params.append(serial)

            # Total count
            cursor.execute(f"SELECT COUNT(*) as cnt FROM detections {where}", params)
            total = cursor.fetchone()['cnt']

            # Paginated results
            cursor.execute(f"""
                SELECT serial_number, ua_type, drone_lat, drone_lon, drone_height_agl,
                       speed, direction, operator_lat, operator_lon,
                       rssi, protocol, receiver_lat, receiver_lon, timestamp
                FROM detections {where}
                ORDER BY timestamp ASC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            records = [dict(row) for row in cursor.fetchall()]
            return records, total

    def get_history_serials(self, from_ts: str, to_ts: str) -> List[Dict]:
        """Get summary per drone serial for a time range."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT serial_number, ua_type, protocol,
                       MIN(timestamp) as first_seen,
                       MAX(timestamp) as last_seen,
                       COUNT(*) as detection_count,
                       MAX(rssi) as max_rssi,
                       MAX(self_id_text) as self_id_text
                FROM detections
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY serial_number
                ORDER BY last_seen DESC
            """, (from_ts, to_ts))
            rows = [dict(row) for row in cursor.fetchall()]
            from .models import UAType
            for row in rows:
                ua = row.get('ua_type', 0)
                row['ua_type_name'] = UAType(ua).display_name if 0 <= ua <= 15 else "Unknown"
            return rows

    def get_history_timeline(self, from_ts: str, to_ts: str,
                             bucket_seconds: int = 10) -> List[Dict]:
        """Get time-bucketed summary for replay timeline."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT serial_number, timestamp
                FROM detections
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (from_ts, to_ts))
            rows = cursor.fetchall()

        # Bucket in Python (SQLite datetime bucketing is awkward)
        if not rows:
            return []

        buckets = {}
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'].replace('Z', ''))
                # Round down to bucket boundary
                epoch = ts.timestamp()
                bucket_epoch = int(epoch // bucket_seconds) * bucket_seconds
                bucket_key = datetime.utcfromtimestamp(bucket_epoch).isoformat() + 'Z'
            except (ValueError, AttributeError):
                continue

            if bucket_key not in buckets:
                buckets[bucket_key] = set()
            buckets[bucket_key].add(row['serial_number'])

        return [
            {
                'timestamp': ts,
                'active_drones': len(serials),
                'serials': sorted(serials),
            }
            for ts, serials in sorted(buckets.items())
        ]

    # ==================== Alert Operations ====================

    def insert_alert(self, event: AlertEvent) -> int:
        """Insert an alert event. Returns the row id."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO alerts (timestamp, alert_type, serial_number, detail,
                                   drone_lat, drone_lon, drone_height_agl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                event.timestamp or datetime.utcnow().isoformat() + 'Z',
                event.alert_type, event.serial_number, event.detail,
                event.drone_lat, event.drone_lon, event.drone_height_agl,
            ))
            return cursor.lastrowid

    def get_alerts(self, from_ts: str = None, to_ts: str = None,
                   limit: int = 100, offset: int = 0,
                   state: str = None) -> Tuple[List[Dict], int]:
        """Get alert log entries. Returns (alerts, total_count).

        Args:
            state: Optional filter — 'ACTIVE', 'ACKNOWLEDGED', or 'RESOLVED'.
                   None returns all states.
        """
        with self.get_cursor() as cursor:
            where_parts = []
            params = []
            if from_ts:
                where_parts.append("timestamp >= ?")
                params.append(from_ts)
            if to_ts:
                where_parts.append("timestamp <= ?")
                params.append(to_ts)
            if state:
                where_parts.append("state = ?")
                params.append(state)

            where = "WHERE " + " AND ".join(where_parts) if where_parts else ""

            cursor.execute(f"SELECT COUNT(*) as cnt FROM alerts {where}", params)
            total = cursor.fetchone()['cnt']

            cursor.execute(f"""
                SELECT * FROM alerts {where}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            alerts = [dict(row) for row in cursor.fetchall()]
            return alerts, total

    def acknowledge_alert(self, alert_id: int, operator: str = '') -> bool:
        """Set a single alert to ACKNOWLEDGED state. Returns True if a row was updated."""
        now = datetime.utcnow().isoformat() + 'Z'
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE alerts
                SET state = 'ACKNOWLEDGED', acknowledged_by = ?, acknowledged_at = ?
                WHERE id = ? AND state = 'ACTIVE'
            """, (operator, now, alert_id))
            return cursor.rowcount > 0

    def acknowledge_all_active(self, operator: str = '') -> int:
        """Acknowledge all ACTIVE alerts. Returns the count of rows updated."""
        now = datetime.utcnow().isoformat() + 'Z'
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE alerts
                SET state = 'ACKNOWLEDGED', acknowledged_by = ?, acknowledged_at = ?
                WHERE state = 'ACTIVE'
            """, (operator, now))
            return cursor.rowcount

    def resolve_alert(self, alert_id: int) -> bool:
        """Set an alert to RESOLVED state. Returns True if a row was updated."""
        now = datetime.utcnow().isoformat() + 'Z'
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE alerts
                SET state = 'RESOLVED', resolved_at = ?
                WHERE id = ? AND state != 'RESOLVED'
            """, (now, alert_id))
            return cursor.rowcount > 0

    # ==================== Settings Operations ====================

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get a setting value."""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default

    def set_setting(self, key: str, value: str) -> bool:
        """Set a setting value."""
        with self.get_cursor() as cursor:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        return True

    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings as a dict."""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT key, value FROM settings")
            return {row['key']: row['value'] for row in cursor.fetchall()}

    # ==================== Data Maintenance ====================

    def purge_detections(self, before_ts: str) -> int:
        """Delete detection records older than the given timestamp."""
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM detections WHERE timestamp < ?", (before_ts,))
            return cursor.rowcount

    def purge_alerts(self, before_ts: str) -> int:
        """Delete alert records older than the given timestamp."""
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM alerts WHERE timestamp < ?", (before_ts,))
            return cursor.rowcount

    def run_retention_purge(self, retention_days: int) -> Tuple[int, int]:
        """Purge data older than retention_days. Returns (detections_deleted, alerts_deleted)."""
        if retention_days <= 0:
            return 0, 0
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat() + 'Z'
        d = self.purge_detections(cutoff)
        a = self.purge_alerts(cutoff)
        return d, a

    def get_stats(self) -> Dict:
        """Get database statistics."""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM detections")
            detection_count = cursor.fetchone()['cnt']

            cursor.execute("SELECT COUNT(*) as cnt FROM alerts")
            alert_count = cursor.fetchone()['cnt']

            cursor.execute("SELECT COUNT(DISTINCT serial_number) as cnt FROM detections")
            unique_serials = cursor.fetchone()['cnt']

            cursor.execute("SELECT MIN(timestamp) as oldest FROM detections")
            row = cursor.fetchone()
            oldest = row['oldest'] if row else None

            cursor.execute("SELECT MAX(timestamp) as newest FROM detections")
            row = cursor.fetchone()
            newest = row['newest'] if row else None

        # DB file size
        try:
            db_size = os.path.getsize(self.db_path)
        except OSError:
            db_size = 0

        # Tile cache size
        tile_dir = os.path.join(os.path.dirname(self.db_path), 'tiles')
        tile_size = 0
        if os.path.isdir(tile_dir):
            for dirpath, _dirnames, filenames in os.walk(tile_dir):
                for f in filenames:
                    try:
                        tile_size += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass

        return {
            'db_size_bytes': db_size,
            'detection_count': detection_count,
            'alert_count': alert_count,
            'unique_serials': unique_serials,
            'oldest_record': oldest,
            'newest_record': newest,
            'tile_cache_size_bytes': tile_size,
        }

    def get_unique_serial_count(self) -> int:
        """Get count of unique drone serial numbers."""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(DISTINCT serial_number) as cnt FROM detections")
            return cursor.fetchone()['cnt']


# --------------- Global Singleton -----------------------------------------

_db: Optional[Database] = None


def get_db(db_path: str = None) -> Database:
    """Get or create the global database instance."""
    global _db
    if _db is None:
        _db = Database(db_path or "data/sparrow_droneid.db")
    return _db
