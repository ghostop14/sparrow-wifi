# Sparrow DroneID API Reference

**Version:** 1.0.0
**Base URL:** `http://<host>:<port>/api`
**Default Port:** 8097
**Content Type:** All API responses are `application/json` unless otherwise noted.

## Table of Contents

- [Authentication](#authentication)
- [Error Handling](#error-handling)
- [System](#system)
- [Monitoring](#monitoring)
- [Detections (Live)](#detections-live)
- [History / Replay](#history--replay)
- [Export](#export)
- [Alerts](#alerts)
- [GPS](#gps)
- [Cursor on Target (CoT)](#cursor-on-target-cot)
- [Map Tiles](#map-tiles)
- [Data Maintenance](#data-maintenance)
- [Settings](#settings)
- [Data Models](#data-models)

---

## Authentication

Authentication is optional and controlled by two mechanisms in settings:

1. **IP/Subnet Allowlist** (`allowed_ips`): Comma-separated list of IPs or CIDR subnets. When set, connections from unlisted addresses receive `403 Forbidden`. Empty = allow all.

2. **Bearer Token** (`auth_token`): A static API token configured in settings. When set, all API requests must include:
   ```
   Authorization: Bearer <token>
   ```
   Empty = no token required.

Both mechanisms can be used independently or together. When both are set, a request must satisfy both (correct IP AND valid token).

**Unauthorized responses:**

```json
{
  "errcode": 401,
  "errmsg": "Authentication required"
}
```

```json
{
  "errcode": 403,
  "errmsg": "Connections not authorized from your IP address"
}
```

---

## Error Handling

All API errors return a JSON object with `errcode` and `errmsg` fields. HTTP status codes are used appropriately (200, 400, 401, 403, 404, 500).

**Standard error response:**

```json
{
  "errcode": <int>,
  "errmsg": "<human-readable error message>"
}
```

**Common error codes:**

| errcode | Meaning |
|---------|---------|
| 0 | Success (no error) |
| 1 | Bad request / invalid parameters |
| 2 | Resource not found |
| 3 | Operation not permitted in current state |
| 5 | Internal server error |

---

## System

### GET /api/status

Returns the current application state.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "version": "1.0.0",
  "monitoring": false,
  "monitor_interface": "wlan0mon",
  "monitor_channel": 6,
  "monitor_duration_seconds": 0,
  "frame_count": 0,
  "active_drone_count": 0,
  "total_drones_seen": 0,
  "gps_fix": true,
  "receiver_lat": 38.8977,
  "receiver_lon": -77.0365,
  "receiver_alt": 15.0,
  "uptime_seconds": 3600,
  "db_size_bytes": 1048576,
  "retention_days": 14
}
```

---

## Monitoring

### GET /api/interfaces

Enumerate available WiFi interfaces and their capabilities.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "interfaces": [
    {
      "name": "wlan0",
      "mac_address": "aa:bb:cc:dd:ee:ff",
      "mode": "managed",
      "monitor_capable": true,
      "driver": "ath9k_htc",
      "phy": "phy0"
    },
    {
      "name": "wlan1",
      "mac_address": "11:22:33:44:55:66",
      "mode": "monitor",
      "monitor_capable": true,
      "driver": "rtl8812au",
      "phy": "phy1"
    }
  ]
}
```

### POST /api/monitor/start

Start Remote ID capture on the specified interface. Switches the interface to monitor mode on channel 6 if not already in monitor mode.

**Request body:**

```json
{
  "interface": "wlan0"
}
```

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "interface": "wlan0mon",
  "channel": 6,
  "status": "monitoring"
}
```

**Error conditions:**
- Interface not found → `errcode: 2`
- Interface does not support monitor mode → `errcode: 3`
- Already monitoring → `errcode: 3`
- tcpdump failed to start → `errcode: 5`

### POST /api/monitor/stop

Stop the current capture session. Restores the interface to managed mode.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "status": "stopped",
  "session_duration_seconds": 1847,
  "frames_captured": 23456,
  "drones_detected": 3
}
```

### GET /api/monitor/status

Returns current monitoring session status.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "monitoring": true,
  "interface": "wlan0mon",
  "channel": 6,
  "started_at": "2026-03-17T14:30:00Z",
  "duration_seconds": 1847,
  "frame_count": 23456,
  "droneid_frame_count": 142,
  "capture_errors": 0
}
```

---

## Detections (Live)

### GET /api/drones

Returns all currently active (non-aged-out) drones with the latest data and derived fields.

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_age` | int | 180 | Maximum seconds since last seen to include. 0 = all tracked |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "receiver": {
    "lat": 38.8977,
    "lon": -77.0365,
    "alt": 15.0,
    "gps_fix": true,
    "source": "gpsd"
  },
  "drones": [
    {
      "serial_number": "1FA123456789ABCD",
      "registration_id": "",
      "id_type": 1,
      "id_type_name": "Serial Number (ANSI/CTA-2063-A)",
      "ua_type": 3,
      "ua_type_name": "Helicopter (or Multirotor)",
      "drone_lat": 38.8984,
      "drone_lon": -77.0352,
      "drone_alt_geo": 57.2,
      "drone_alt_baro": 55.8,
      "drone_height_agl": 42.0,
      "speed": 3.2,
      "direction": 22.5,
      "vertical_speed": 0.0,
      "operator_lat": 38.8971,
      "operator_lon": -77.0368,
      "operator_alt": 14.0,
      "operator_id": "",
      "self_id_text": "Survey flight",
      "mac_address": "aa:bb:cc:dd:ee:ff",
      "rssi": -68,
      "rssi_trend": "stable",
      "protocol": "astm_nan",
      "first_seen": "2026-03-17T14:33:12Z",
      "last_seen": "2026-03-17T14:36:45Z",
      "time_in_area_seconds": 213,
      "derived": {
        "range_m": 152.4,
        "bearing_deg": 47.2,
        "bearing_cardinal": "NE",
        "operator_range_m": 7.3,
        "operator_bearing_deg": 198.5,
        "operator_bearing_cardinal": "SSW",
        "altitude_class": "MEDIUM",
        "state": "active"
      }
    }
  ],
  "counts": {
    "active": 1,
    "aging": 0,
    "stale": 0
  },
  "timestamp": "2026-03-17T14:36:47Z"
}
```

**`protocol` field values:**

| Value | Description |
|-------|-------------|
| `astm_nan` | ASTM F3411 via Wi-Fi NAN action frames |
| `astm_beacon` | ASTM F3411 via Wi-Fi beacon vendor-specific IE (OUI FA:0B:BC) |
| `dji_proprietary` | DJI proprietary DroneID via beacon vendor-specific IE (OUI 26:37:12) |

**`id_type` values (ASTM F3411):**

| Value | Name |
|-------|------|
| 0 | None |
| 1 | Serial Number (ANSI/CTA-2063-A) |
| 2 | CAA Assigned Registration ID |
| 3 | UTM Assigned UUID |
| 4 | Specific Session ID |

**`ua_type` values (ASTM F3411):**

| Value | Name |
|-------|------|
| 0 | None / Not Declared |
| 1 | Aeroplane |
| 2 | Helicopter (or Multirotor) |
| 3 | Gyroplane |
| 4 | Hybrid Lift (VTOL) |
| 5 | Ornithopter |
| 6 | Glider |
| 7 | Kite |
| 8 | Free Balloon |
| 9 | Captive Balloon |
| 10 | Airship |
| 11 | Free Fall / Parachute |
| 12 | Rocket |
| 13 | Tethered Powered Aircraft |
| 14 | Ground Obstacle |
| 15 | Other |

**`altitude_class` values (derived from drone_height_agl):**

| Value | AGL Range | Description |
|-------|-----------|-------------|
| `GROUND` | < 3m | On ground or launching/landing |
| `LOW` | 3-30m | Below treeline |
| `MEDIUM` | 30-120m | Normal flight |
| `HIGH` | 120-400m | Near legal ceiling |
| `ILLEGAL` | > 400m (122m) | Above FAA 400ft limit |

**`state` values (derived from last_seen age):**

| Value | Age | Description |
|-------|-----|-------------|
| `active` | 0-30s | Currently broadcasting |
| `aging` | 30-90s | Recently seen |
| `stale` | 90-180s | May have departed |

### GET /api/drones/{serial}

Returns detailed information and recent track for a specific drone.

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `serial` | string | URL-encoded serial number or registration ID |

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `track_minutes` | int | 5 | Minutes of track history to include |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "drone": {
    "...": "(same fields as in /api/drones list)"
  },
  "track": [
    {
      "drone_lat": 38.8984,
      "drone_lon": -77.0352,
      "drone_alt_geo": 57.2,
      "drone_height_agl": 42.0,
      "speed": 3.2,
      "direction": 22.5,
      "rssi": -68,
      "timestamp": "2026-03-17T14:36:45Z"
    },
    {
      "drone_lat": 38.8983,
      "drone_lon": -77.0351,
      "drone_alt_geo": 56.8,
      "drone_height_agl": 41.6,
      "speed": 3.1,
      "direction": 24.0,
      "rssi": -69,
      "timestamp": "2026-03-17T14:36:44Z"
    }
  ]
}
```

---

## History / Replay

### GET /api/history

Returns detection records for a time range. Used for replay and forensic review.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from` | string | Yes | ISO 8601 UTC start time |
| `to` | string | Yes | ISO 8601 UTC end time |
| `serial` | string | No | Filter by serial number |
| `limit` | int | No | Maximum records to return (default 10000) |
| `offset` | int | No | Pagination offset (default 0) |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "records": [
    {
      "serial_number": "1FA123456789ABCD",
      "ua_type": 3,
      "drone_lat": 38.8984,
      "drone_lon": -77.0352,
      "drone_height_agl": 42.0,
      "speed": 3.2,
      "direction": 22.5,
      "operator_lat": 38.8971,
      "operator_lon": -77.0368,
      "rssi": -68,
      "protocol": "astm_nan",
      "receiver_lat": 38.8977,
      "receiver_lon": -77.0365,
      "timestamp": "2026-03-17T14:36:45Z"
    }
  ],
  "total_count": 4523,
  "returned_count": 4523
}
```

### GET /api/history/serials

Returns a list of unique drone serial numbers seen in a time range, with summary statistics.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from` | string | Yes | ISO 8601 UTC start time |
| `to` | string | Yes | ISO 8601 UTC end time |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "serials": [
    {
      "serial_number": "1FA123456789ABCD",
      "ua_type": 3,
      "ua_type_name": "Helicopter (or Multirotor)",
      "protocol": "astm_nan",
      "first_seen": "2026-03-17T14:33:12Z",
      "last_seen": "2026-03-17T14:36:45Z",
      "detection_count": 213,
      "max_rssi": -62,
      "self_id_text": "Survey flight"
    }
  ]
}
```

### GET /api/history/timeline

Returns time-bucketed summary data for rendering a replay timeline.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from` | string | Yes | ISO 8601 UTC start time |
| `to` | string | Yes | ISO 8601 UTC end time |
| `bucket_seconds` | int | No | Time bucket size (default 10) |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "buckets": [
    {
      "timestamp": "2026-03-17T14:33:10Z",
      "active_drones": 1,
      "serials": ["1FA123456789ABCD"]
    },
    {
      "timestamp": "2026-03-17T14:33:20Z",
      "active_drones": 2,
      "serials": ["1FA123456789ABCD", "9CC4500001234567"]
    }
  ]
}
```

---

## Export

### GET /api/export/kml

Downloads a KML file containing drone tracks and operator positions for a time range.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from` | string | Yes | ISO 8601 UTC start time |
| `to` | string | Yes | ISO 8601 UTC end time |
| `serial` | string | No | Filter by serial number. Omit for all drones |

**Response:**

- **Content-Type:** `application/vnd.google-earth.kml+xml`
- **Content-Disposition:** `attachment; filename="sparrow_droneid_export_<from>_<to>.kml"`

The KML file contains:
- A folder per drone serial number
- Drone flight track as a `LineString` placemark
- Individual position placemarks with timestamps (for time-enabled playback in Google Earth)
- Operator position placemarks (where available)
- Receiver position placemark
- Altitude data encoded for 3D visualization (`altitudeMode: absolute`)

---

## Alerts

### GET /api/alerts/config

Returns the current alert configuration.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "audio_enabled": true,
  "visual_enabled": true,
  "script_enabled": false,
  "script_path": "",
  "rules": [
    {
      "id": 1,
      "name": "New drone detected",
      "type": "new_drone",
      "enabled": true,
      "audio_sound": "chime",
      "params": {}
    },
    {
      "id": 2,
      "name": "Altitude violation",
      "type": "altitude_max",
      "enabled": true,
      "audio_sound": "alert",
      "params": {
        "max_altitude_m": 122
      }
    },
    {
      "id": 3,
      "name": "Speed violation",
      "type": "speed_max",
      "enabled": true,
      "audio_sound": "alert",
      "params": {
        "max_speed_mps": 44.7
      }
    },
    {
      "id": 4,
      "name": "Signal lost",
      "type": "signal_lost",
      "enabled": true,
      "audio_sound": "chime",
      "params": {
        "timeout_seconds": 180
      }
    }
  ]
}
```

### PUT /api/alerts/config

Update alert configuration.

**Request body:** Same structure as the GET response.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": ""
}
```

### GET /api/alerts/log

Returns the alert event history.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from` | string | No | ISO 8601 UTC start time |
| `to` | string | No | ISO 8601 UTC end time |
| `limit` | int | No | Maximum records (default 100) |
| `offset` | int | No | Pagination offset |

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "alerts": [
    {
      "id": 42,
      "timestamp": "2026-03-17T14:33:12Z",
      "alert_type": "new_drone",
      "serial_number": "1FA123456789ABCD",
      "detail": "New drone detected: Helicopter (or Multirotor), RSSI -68 dBm",
      "drone_lat": 38.8984,
      "drone_lon": -77.0352,
      "drone_height_agl": 42.0
    }
  ],
  "total_count": 7
}
```

**External script invocation:**

When `script_enabled` is true and `script_path` is set, the script is called on each alert event:

```bash
/path/to/script.sh '<json_payload>'
```

The JSON payload matches the alert log entry structure above.

---

## GPS

### GET /api/gps

Returns the current receiver GPS position and source.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "mode": "gpsd",
  "fix": true,
  "latitude": 38.8977,
  "longitude": -77.0365,
  "altitude": 15.0,
  "speed": 0.0
}
```

**`mode` values:** `none`, `gpsd`, `static`

When `mode` is `none`, `fix` will always be `false` and coordinates will be `0.0`. Derived fields on drones (range, bearing) will not be calculated.

---

## Cursor on Target (CoT)

### GET /api/cot/status

Returns the current CoT output configuration and status.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "enabled": false,
  "address": "239.2.3.1",
  "port": 6969,
  "events_sent": 0
}
```

### PUT /api/cot/config

Update CoT output configuration.

**Request body:**

```json
{
  "enabled": true,
  "address": "239.2.3.1",
  "port": 6969
}
```

**Response:**

```json
{
  "errcode": 0,
  "errmsg": ""
}
```

**CoT event format:**

Each active drone emits a CoT event on every position update:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0"
       uid="sparrow-droneid-1FA123456789ABCD"
       type="a-n-A-C-F-q"
       time="2026-03-17T14:36:45Z"
       start="2026-03-17T14:36:45Z"
       stale="2026-03-17T14:36:55Z"
       how="m-f">
  <point lat="38.8984" lon="-77.0352" hae="57.2" ce="10.0" le="15.0"/>
  <detail>
    <track course="22.5" speed="3.2"/>
    <remarks>
      SN:1FA123456789ABCD UA:Multirotor AGL:42.0m Self-ID:Survey flight
    </remarks>
    <contact callsign="DRONE-1FA1"/>
    <__droneid
      serial="1FA123456789ABCD"
      ua_type="3"
      height_agl="42.0"
      operator_lat="38.8971"
      operator_lon="-77.0368"
      operator_id=""
      self_id="Survey flight"
      rssi="-68"
      protocol="astm_nan"/>
  </detail>
</event>
```

**CoT type field:** `a-n-A-C-F-q` = atom, neutral, air, civilian, fixed-wing, UAV. Can be overridden per-drone if threat classification is implemented in a future version.

**Stale time:** 10 seconds after event time (configurable, should be > 2x expected update interval).

---

## Map Tiles

### GET /api/tiles/{source}/{z}/{x}/{y}.png

Proxies and caches map tiles from the configured tile source. The UI requests tiles through this endpoint to enable offline caching.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `source` | Tile source: `osm`, `esri_satellite` |
| `z` | Zoom level |
| `x` | Tile column |
| `y` | Tile row |

**Response:**

- **Content-Type:** `image/png` (or `image/jpeg` for Esri satellite)
- Returns the tile image directly

**Caching behavior:**
- Tiles are cached in `<data_dir>/tiles/<source>/<z>/<x>/<y>.png`
- Cached tiles are served directly without contacting the upstream CDN
- If upstream is unreachable and the tile is cached, the cached version is served
- If upstream is unreachable and the tile is NOT cached, returns `503 Service Unavailable`

**Tile sources:**

| Source | Upstream URL Pattern |
|--------|---------------------|
| `osm` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` |
| `esri_satellite` | `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` |

---

## Data Maintenance

### GET /api/data/stats

Returns database statistics.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "db_size_bytes": 1048576,
  "detection_count": 45230,
  "alert_count": 7,
  "unique_serials": 12,
  "oldest_record": "2026-03-03T08:15:00Z",
  "newest_record": "2026-03-17T14:36:45Z",
  "tile_cache_size_bytes": 52428800,
  "retention_days": 14
}
```

### POST /api/data/purge

Manually purge detection and alert data older than a specified date.

**Request body:**

```json
{
  "before": "2026-03-10T00:00:00Z"
}
```

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "detections_deleted": 12450,
  "alerts_deleted": 3
}
```

### POST /api/data/purge-tiles

Purge cached map tiles.

**Request body:**

```json
{
  "source": "osm"
}
```

Omit `source` to purge all cached tiles.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "tiles_deleted": 1523,
  "bytes_freed": 52428800
}
```

---

## Settings

### GET /api/settings

Returns all application settings.

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "settings": {
    "port": 8097,
    "bind_address": "127.0.0.1",
    "https_enabled": false,
    "https_cert_path": "",
    "https_key_path": "",
    "auth_token": "",
    "allowed_ips": "",
    "gps_mode": "none",
    "gps_static_lat": 0.0,
    "gps_static_lon": 0.0,
    "gps_static_alt": 0.0,
    "retention_days": 14,
    "cot_enabled": false,
    "cot_address": "239.2.3.1",
    "cot_port": 6969,
    "alert_audio_enabled": true,
    "alert_visual_enabled": true,
    "alert_script_enabled": false,
    "alert_script_path": "",
    "tile_cache_enabled": true,
    "monitor_interface": ""
  }
}
```

### PUT /api/settings

Update one or more settings. Only include the keys you want to change.

**Request body:**

```json
{
  "gps_mode": "static",
  "gps_static_lat": 38.8977,
  "gps_static_lon": -77.0365
}
```

**Response:**

```json
{
  "errcode": 0,
  "errmsg": "",
  "settings": { "...(full updated settings object)" },
  "restart_required": false
}
```

**`restart_required`** will be `true` if any of these settings were changed: `port`, `bind_address`, `https_enabled`, `https_cert_path`, `https_key_path`.

**Note:** The `auth_token` value is write-only. GET responses will return `"auth_token": "(set)"` or `"auth_token": ""` to indicate whether a token is configured, but will never return the actual token value.

---

## Data Models

### DroneIDDevice

The core data model representing a detected drone. All drone-related endpoints return objects with these fields.

| Field | Type | Description |
|-------|------|-------------|
| `serial_number` | string | UAS serial number from Basic ID message |
| `registration_id` | string | CAA registration ID (alternative to serial) |
| `id_type` | int | ID type enum (see [id_type values](#get-apidrones)) |
| `id_type_name` | string | Human-readable ID type |
| `ua_type` | int | UA type enum (see [ua_type values](#get-apidrones)) |
| `ua_type_name` | string | Human-readable UA type |
| `drone_lat` | float | Drone latitude (WGS84) |
| `drone_lon` | float | Drone longitude (WGS84) |
| `drone_alt_geo` | float | Drone geometric altitude (m, WGS84 HAE) |
| `drone_alt_baro` | float | Drone barometric altitude (m) |
| `drone_height_agl` | float | Drone height above ground level (m) |
| `speed` | float | Ground speed (m/s) |
| `direction` | float | Track direction (degrees, 0=North, CW) |
| `vertical_speed` | float | Vertical speed (m/s, positive=up) |
| `operator_lat` | float | Operator latitude (WGS84) |
| `operator_lon` | float | Operator longitude (WGS84) |
| `operator_alt` | float | Operator altitude (m) |
| `operator_id` | string | Operator registration / license ID |
| `self_id_text` | string | Free-form text description from operator |
| `mac_address` | string | Transmitter MAC address (may be randomized) |
| `rssi` | int | Received signal strength (dBm) |
| `rssi_trend` | string | `strengthening`, `stable`, `weakening` |
| `protocol` | string | Detection protocol (see [protocol values](#get-apidrones)) |
| `first_seen` | string | ISO 8601 UTC timestamp of first detection |
| `last_seen` | string | ISO 8601 UTC timestamp of most recent detection |
| `time_in_area_seconds` | int | Duration since first detection |
| `derived` | object | Computed fields (see below) |

### Derived Fields

Computed from drone position, receiver position, and temporal data. Only populated when receiver GPS is available (except `state`).

| Field | Type | Description |
|-------|------|-------------|
| `range_m` | float | Distance from receiver to drone (meters) |
| `bearing_deg` | float | Bearing from receiver to drone (degrees) |
| `bearing_cardinal` | string | Cardinal direction (N, NE, NNE, etc.) |
| `operator_range_m` | float | Distance from receiver to operator (meters) |
| `operator_bearing_deg` | float | Bearing from receiver to operator (degrees) |
| `operator_bearing_cardinal` | string | Cardinal direction to operator |
| `altitude_class` | string | Altitude classification (see values above) |
| `state` | string | Age-based state: `active`, `aging`, `stale` |

### Alert Event

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-increment ID |
| `timestamp` | string | ISO 8601 UTC |
| `alert_type` | string | `new_drone`, `altitude_max`, `speed_max`, `signal_lost` |
| `serial_number` | string | Drone that triggered the alert |
| `detail` | string | Human-readable alert description |
| `drone_lat` | float | Drone position at time of alert |
| `drone_lon` | float | Drone position at time of alert |
| `drone_height_agl` | float | Drone AGL altitude at time of alert |

---

## Rate Limits

No server-side rate limiting is implemented. Clients polling for live data should use a 1-2 second interval. More frequent polling provides no benefit as drone broadcasts arrive approximately once per second.

## WebSocket

Not implemented. All live data is accessed via polling. This ensures compatibility with any HTTP client for integration purposes.

## Versioning

The API does not use URL versioning. Breaking changes will be communicated via the `version` field in `/api/status`. Non-breaking additions (new fields, new endpoints) may be added without version changes.
