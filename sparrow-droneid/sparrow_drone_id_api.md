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
- [Geozones](#geozones)
- [GPS](#gps)
- [TAK / Cursor on Target](#tak--cursor-on-target)
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
  "ble_enabled": true,
  "ble_frame_count": 0,
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

Start Remote ID capture on the specified interface. Switches the interface to monitor mode on channel 6 if not already in monitor mode. BLE capture is started automatically alongside WiFi capture (no additional configuration required).

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
  "ble_enabled": true,
  "ble_frame_count": 38,
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
| `astm_ble` | ASTM F3411 via BLE Legacy advertising (BT4/BT5, ASTM F3411-22a §A3) |
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
| `state` | string | No | Filter by alert state: `ACTIVE`, `ACKNOWLEDGED`, or `RESOLVED`. Omit to return all states |
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
      "drone_height_agl": 42.0,
      "state": "ACKNOWLEDGED",
      "acknowledged_by": "OPS1",
      "acknowledged_at": "2026-03-17T14:35:00Z",
      "resolved_at": null
    }
  ],
  "total_count": 7
}
```

**`state` field values:**

| Value | Description |
|-------|-------------|
| `ACTIVE` | Alert is unacknowledged and the condition has not cleared |
| `ACKNOWLEDGED` | Alert has been acknowledged by an operator |
| `RESOLVED` | The triggering condition has cleared (e.g. drone departed) |

**External script invocation:**

When `script_enabled` is true and `script_path` is set, the script is called on each alert event:

```bash
/path/to/script.sh '<json_payload>'
```

The JSON payload matches the alert log entry structure above.

### PUT /api/alerts/acknowledge

Bulk-acknowledge all currently ACTIVE alerts. Sets their state to `ACKNOWLEDGED` and records the operator callsign.

**Request body:**

```json
{
  "operator": "OPS1"
}
```

**Response:**

```json
{
  "errcode": 0,
  "count": 3
}
```

`count` is the number of alerts that were transitioned from `ACTIVE` to `ACKNOWLEDGED`.

### PUT /api/alerts/{alert_id}/acknowledge

Acknowledge a single alert by ID.

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `alert_id` | int | Alert ID from the alert log |

**Request body:**

```json
{
  "operator": "OPS1"
}
```

**Response:**

```json
{
  "errcode": 0
}
```

**Error conditions:**
- Alert not found → `errcode: 2`
- Alert is not in ACTIVE state → `errcode: 3`

---

## Geozones

Provides airport proximity lookup and FAA no-fly zone data for map overlay and proximity alerting.

### GET /api/geozones/airports

Returns airports within a specified radius of a coordinate.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `lat` | float | Yes | Center latitude (WGS84) |
| `lon` | float | Yes | Center longitude (WGS84) |
| `radius_mi` | float | No | Search radius in miles (default 50) |

**Response:**

```json
{
  "errcode": 0,
  "airports": [
    {
      "ident": "KJFK",
      "name": "John F Kennedy International Airport",
      "type": "large_airport",
      "lat": 40.63972,
      "lon": -73.77889,
      "country": "US"
    },
    {
      "ident": "KLGA",
      "name": "LaGuardia Airport",
      "type": "large_airport",
      "lat": 40.77724,
      "lon": -73.87261,
      "country": "US"
    }
  ]
}
```

**`type` field values:** `large_airport`, `medium_airport`, `small_airport`, `heliport`, `seaplane_base`, `closed`

### GET /api/geozones/nofly

Returns FAA Prohibited and Restricted airspace zones that intersect with the area near the specified coordinate, as GeoJSON features.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `lat` | float | Yes | Query latitude (WGS84) |
| `lon` | float | Yes | Query longitude (WGS84) |

**Response:**

```json
{
  "errcode": 0,
  "features": [
    {
      "type": "Feature",
      "properties": {
        "name": "P-56A",
        "type": "prohibited",
        "lower_alt": "SFC",
        "upper_alt": "UNLIMITED"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[-77.0364, 38.8976], ["..."]]]
      }
    }
  ]
}
```

Each element of `features` is a standard GeoJSON Feature object. The `geometry` may be `Polygon` or `MultiPolygon`. `properties.type` is `prohibited` or `restricted`.

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

## TAK / Cursor on Target

Sparrow DroneID publishes drones, their operators, a drone-to-operator link line and the receiver itself to TAK clients and TAK Server as Cursor-on-Target (CoT). There is one output, configured with the `cot_*` and `tak_*` settings. Operator documentation (transports, certificates, enrollment, symbols, timing, troubleshooting) is in [TAK_INTEGRATION.md](TAK_INTEGRATION.md), which also shows the exact CoT shapes.

Conventions: success bodies are the domain object; errors are `{"error": {"code", "message", "detail"?}}`. Codes used: `VALIDATION_ERROR` (400), `CONFLICT` (409), `PAYLOAD_TOO_LARGE` (413), `INTERNAL_ERROR` (500), `SERVICE_UNAVAILABLE` (503, engine absent or the `cryptography` package missing for certificate, package and enrollment routes). Request bodies are capped at 16 MiB (413).

### Legacy endpoints (unchanged shape)

`GET /api/v1/cot/status`

```json
{"enabled": false, "address": "239.2.3.1", "port": 6969, "events_sent": 0,
 "transport": null, "state": "disabled", "connected": false, "send_errors": 0, "last_error": null}
```

The first four keys are as before; the rest are additive.

`PUT /api/v1/cot/config` with `{"enabled": true, "address": "239.2.3.1", "port": 6969}` returns `{}`. When `address` or `port` is present the transport is set to `udp_multicast` (this endpoint has always described multicast output); `enabled` alone only toggles. Invalid values are a 400.

### GET /api/v1/tak/status

Live status: `enabled`, `running`, `state` (`disabled`, `connecting`, `connected`, `ready` for an open UDP socket, `error`), `transport`, `destination`, `connected`, `connected_since`, `sent`, `send_errors`, `reconnects`, `queue_depth`, `queue_drops`, `last_error`, `last_error_at`, `last_send_at`, `items` (`drones`, `operators`, `lines`, `sensor` currently drawn), `deletes_sent`, `deletes_on_stop`, `sensor_point` (`enabled`, `publishing`, `reason`, `last_sent_at`, `stale_s`), `tls` (null unless TLS: `version`, `cipher`, `server_subject`, `client_mode`, `client_subject`, `client_not_after`, `client_days_left`, `ca_mode`, `ca_subjects`), `enrollment` (null unless enroll mode), `config_warnings`, `defaults` (`station_name`, `client_uid`).

### GET /api/v1/tak/config, PUT /api/v1/tak/config

`GET` returns `{"settings": {...every TAK setting, typed...}, "effective": {"station_name", "client_uid", "sensor_stale_s", "sensor_stale_auto"}, "default_ports": {"udp_multicast": 6969, "udp_broadcast": 6969, "udp_unicast": 4242, "tcp": 8087, "tls": 8089}}`. Secrets are returned as `"(set)"` or `""`.

`PUT` takes any subset of the settings. A secret that is absent or `"(set)"` keeps its stored value, `""` clears it, anything else replaces it. The merged result is validated first; on error nothing is written and the response is 400 with `detail.errors = [{"field", "message"}, ...]`. Unknown keys are a 400 listing them. The response is the `GET` body. The engine is reconfigured only when the effective configuration changed (a Save that re-sends unchanged values does not flicker the map).

### POST /api/v1/tak/test

Body: candidate settings (any subset), merged over the stored ones; `{}` tests the saved configuration. Nothing is saved and no CoT is sent. Always 200 for connection outcomes:

```json
{"ok": false, "message": "", "error": "Server rejected our client certificate after the handshake: ...",
 "duration_ms": 2034,
 "details": {"transport": "tls", "destination": "takserver.local:8089", "tls_version": "TLSv1.3",
             "cipher": "TLS_AES_256_GCM_SHA384", "server_subject": "CN=takserver",
             "client_subject": "CN=sparrow,O=TAK", "client_not_after": "2027-11-11T14:00:00Z",
             "enrollment": "enrolled"}}
```

UDP tests open and close a socket; TCP connects and closes; TLS connects, handshakes and waits 2 s for the server's verdict on the client certificate. In enroll mode a test may perform a real enrollment (honouring the failure backoff).

### Certificates

- `GET /api/v1/tak/certs` returns `{"client": CertInfo|null, "ca": CaInfo|null, "enrolled": CertInfo|null}`.
- `POST /api/v1/tak/certs/client` with `{"cert_b64", "key_b64"?, "password"?, "filename"?}` (decoded files at most 256 KiB). The format (.p12, PEM pair, combined PEM, DER certificate) is detected from the content. The key is stored decrypted (mode 0600); the password is never stored. Sets `tak_tls_client_mode` to `upload`. 201 `{"client": CertInfo}`.
- `POST /api/v1/tak/certs/ca` with `{"data_b64", "password"?, "filename"?}`. Sets `tak_tls_ca_mode` to `upload`. 201 `{"ca": CaInfo}`.
- `DELETE /api/v1/tak/certs/client`, `DELETE /api/v1/tak/certs/ca`: 200 `{"deleted": true|false}`.

`CertInfo`: `subject`, `issuer`, `not_after`, `days_left`, `expired`, `source` (`upload`, `package`, `enrollment`), `filename`, `format` (`p12`, `pem`), `stored_at`, `chain_length`. `CaInfo`: `count`, `subjects`, `earliest_not_after`, `source`, `filename`, `format`, `stored_at`.

### Enrollment

- `GET /api/v1/tak/enrollment`: `{"status": "disabled"}` or the status object `status` (`pending`, `enrolling`, `enrolled`, `cached`, `failed`), `last_attempt_at`, `last_success_at`, `next_attempt_at`, `last_error`, `cert_subject`, `cert_not_after`.
- `POST /api/v1/tak/enroll`: requires the saved settings to use TLS with the `enroll` client mode and an enrollment username and password (else 409). Waits up to 40 s and returns the status object. After a failure the next attempt is delayed (5 min, doubling to 1 h); a request inside that window returns the status with `next_attempt_at` and `last_error` set.

### POST /api/v1/tak/package

Body `{"data_b64": "<base64 zip>", "filename"?}` (decoded at most 2 MiB). Imports an ATAK enrollment package, a certificate package or an iTAK package; the zip is read in memory and nothing is extracted. Everything is validated before anything is written; a bad package is a 400 and changes nothing. Applied: transport, host, port, CA truststore and client mode (`enroll` or `upload`), `tak_tls_verify` and `tak_tls_check_hostname` true. Never changed: `cot_enabled`, the enrollment credentials.

```json
{"kind": "enrollment", "description": "TAK 127.0.0.1", "host": "127.0.0.1", "port": 8089,
 "transport": "tls", "imported": {"ca": {"count": 2, "subjects": ["CN=..."]}, "client": null},
 "enroll_required": true,
 "next_step": "Enter the enrollment username and password for this TAK Server, then Save.",
 "warnings": [], "settings": {"...": "..."}}
```

`kind` is `enrollment`, `certificate` or `truststore_only`.

### GET /api/v1/tak/interfaces

`{"interfaces": [{"name": "eth0", "ipv4": "192.168.1.10"}]}`: IPv4 interfaces (without `lo`) for the multicast interface choice.

### TAK settings

| Key | Default | Notes |
|---|---|---|
| `cot_enabled` | `false` | Enable the output. |
| `cot_address` | `239.2.3.1` | Multicast group, broadcast address or host (also the enrollment host). |
| `cot_port` | `6969` | 1-65535. |
| `tak_transport` | `udp_multicast` | `udp_multicast`, `udp_broadcast`, `udp_unicast`, `tcp`, `tls`. |
| `tak_multicast_interface` | `` | Interface name; blank = OS routing. |
| `tak_multicast_ttl` | `1` (`32` on an upgraded install) | 1-255. |
| `tak_auth_username`, `tak_auth_password` | `` | `<auth>` sent after a TCP/TLS connect. Password is a secret. |
| `tak_client_uid` | `` | Blank = `sparrow-droneid-<hostname>`. |
| `tak_station_name` | `` | Blank = hostname. Sensor callsign; part of every UID. |
| `tak_uid_prefix` | `sparrow-droneid` | |
| `tak_tls_client_mode` | `upload` | `upload`, `path`, `enroll`. |
| `tak_tls_client_cert_path`, `tak_tls_client_key_path`, `tak_tls_client_password` | `` | Path mode; the password is a secret. |
| `tak_tls_ca_mode` | `none` | `none` (system store), `upload`, `path`. |
| `tak_tls_ca_path`, `tak_tls_ca_password` | `` | Path mode; the password is a secret. |
| `tak_tls_verify`, `tak_tls_check_hostname` | `true` | |
| `tak_enroll_port`, `tak_enroll_username`, `tak_enroll_password` | `8446`, ``, `` | Enrollment; the password is a secret. |
| `tak_send_interval_s` | `2` | 0.5-60. |
| `tak_refresh_interval_s` | `10` | 1-300, at least the send interval. |
| `tak_stale_after_s` | `30` | At least 3 x max(send, refresh); at most the delete time. |
| `tak_delete_after_s` | `180` | At most 300. |
| `tak_send_deletes` | `true` | |
| `tak_unknown_affiliation` | `u` | `u` or `n`. |
| `tak_drone_type_family` | `uav` | `uav` or `civil`. |
| `tak_operator_enabled`, `tak_line_enabled` | `true` | |
| `tak_include_ssid_detections` | `false` | |
| `tak_sensor_enabled`, `tak_sensor_heartbeat_s`, `tak_sensor_stale_s` | `true`, `10`, `` | Blank stale = auto `max(30, 3 x heartbeat)`. |

All of them are also readable and writable through `GET`/`PUT /api/v1/settings`, where the same validation applies and secrets are masked the same way.

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
    "tak_transport": "udp_multicast",
    "tak_multicast_ttl": 1,
    "tak_auth_password": "",
    "tak_send_interval_s": 2.0,
    "tak_stale_after_s": 30.0,
    "tak_delete_after_s": 180.0,
    "alert_audio_enabled": true,
    "alert_visual_enabled": true,
    "alert_script_enabled": false,
    "alert_script_path": "",
    "tile_cache_enabled": true,
    "monitor_interface": "",
    "airport_geozone_radius_mi": 2.0,
    "operator_name": ""
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

**New settings added in this version:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `airport_geozone_radius_mi` | float | 2.0 | Radius of airport exclusion zone circles drawn on the map (miles) |
| `operator_name` | string | `""` | Operator callsign pre-filled in alert acknowledgment dialogs and recorded in `acknowledged_by` |

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
| `protocol` | string | Detection protocol — `astm_nan`, `astm_beacon`, `astm_ble`, or `dji_proprietary` (see [protocol values](#get-apidrones)) |
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
| `state` | string | `ACTIVE`, `ACKNOWLEDGED`, or `RESOLVED` |
| `acknowledged_by` | string | Operator callsign that acknowledged the alert, or `null` |
| `acknowledged_at` | string | ISO 8601 UTC timestamp of acknowledgment, or `null` |
| `resolved_at` | string | ISO 8601 UTC timestamp when the condition cleared, or `null` |

---

## Rate Limits

No server-side rate limiting is implemented. Clients polling for live data should use a 1-2 second interval. More frequent polling provides no benefit as drone broadcasts arrive approximately once per second.

## WebSocket

Not implemented. All live data is accessed via polling. This ensures compatibility with any HTTP client for integration purposes.

## Versioning

The API does not use URL versioning. Breaking changes will be communicated via the `version` field in `/api/status`. Non-breaking additions (new fields, new endpoints) may be added without version changes.
