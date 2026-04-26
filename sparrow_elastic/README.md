# Sparrow Elastic Bridge

ECS 8.17 bridge between the Sparrow WiFi agent and Elasticsearch or OpenSearch.
The bridge polls the sparrow-wifi agent over HTTP, translates raw WiFi and
Bluetooth scan observations into ECS-compliant JSON documents, enriches them
with vendor OUI lookups, device classification, and Fingerbank data (optional),
and bulk-indexes them into a rolling index series managed by ILM (Elasticsearch)
or ISM (OpenSearch).

---

## Contents

1. [Quickstart](#quickstart)
2. [Engine Selection](#engine-selection)
3. [Auth Modes](#auth-modes)
4. [Bootstrap Behavior](#bootstrap-behavior)
5. [Index Structure](#index-structure)
6. [Reference Data Refresh](#reference-data-refresh)
7. [Fingerbank Opt-In](#fingerbank-opt-in)
8. [Device Classifier Rules](#device-classifier-rules)
9. [Data Flow and Key Fields](#data-flow-and-key-fields)
10. [Pre-Computed Pattern-of-Life Fields](#pre-computed-pattern-of-life-fields)
11. [Channel Model](#channel-model)
12. [Dashboards](#dashboards)
13. [Legacy sparrowwifi-home Indices](#legacy-sparrowwifi-home-indices)
14. [Troubleshooting](#troubleshooting)
15. [CLI Reference](#cli-reference)
16. [Files Layout](#files-layout)

---

## Quickstart

```bash
pip install -r requirements-elastic.txt
./sparrow-elastic.py --elasticserver http://user:pass@host:9200
```

The bridge will bootstrap the required index templates, ILM/ISM policies, and
write aliases on first start, then begin polling the sparrow-wifi agent on
`127.0.0.1:8020` and indexing documents continuously.

To target a remote agent and a named wireless interface:

```bash
./sparrow-elastic.py \
    --elasticserver http://elastic:changeme@elasticsearch.example.com:9200 \
    --sparrowagent agent.example.com \
    --sparrowport 8020 \
    --wifiinterface wlan0
```

---

## Engine Selection

The bridge supports both Elasticsearch and OpenSearch. Select the engine with
`--engine` or the `SPARROW_ES_ENGINE` environment variable:

```bash
./sparrow-elastic.py --engine opensearch --elasticserver http://user:pass@host:9200
```

| Value | Description |
|---|---|
| `elasticsearch` | Default. Uses `elasticsearch-py>=8,<9`. |
| `opensearch` | Uses `opensearch-py>=2.0,<3.0`. |

**Important:** Do not upgrade `opensearch-py` to 3.x. Version 3.x breaks
compatibility with some OpenSearch analytics Docker images. The pin
`opensearch-py>=2.0,<3.0` in `requirements-elastic.txt` is intentional; do
not change it.

When running against OpenSearch, ILM (Elasticsearch Index Lifecycle Management)
is automatically replaced by ISM (OpenSearch Index State Management). Policy
JSON and template components are resolved accordingly at bootstrap time.

---

## Auth Modes

The bridge supports three authentication modes. Precedence is:
**api_key > basic_auth > none**.

### None (anonymous)

```bash
./sparrow-elastic.py --elasticserver http://host:9200
```

### Basic Auth

Embed credentials in the URL:

```bash
./sparrow-elastic.py --elasticserver http://user:pass@host:9200
```

Or pass them as flags:

```bash
./sparrow-elastic.py --elasticserver http://host:9200 --username elastic --password changeme
```

### API Key (Elasticsearch only)

```bash
./sparrow-elastic.py --elasticserver http://host:9200 --api-key <base64-encoded-id:key>
```

API key auth is not supported on OpenSearch. If `--api-key` is supplied when
`--engine opensearch` is active, the bridge will log a WARN and fall back to
no-auth (or basic auth if credentials are also present).

---

## Bootstrap Behavior

On first start the bridge runs a one-time bootstrap sequence:

1. Creates the ILM lifecycle policy (`sparrow-wifi-ilm`, `sparrow-bt-ilm`) or
   ISM policy on OpenSearch.
2. Registers component templates (`sparrow-wifi-components/`, `sparrow-bt-components/`).
3. Creates the composable index template that binds to the `sparrow-wifi-*`
   and `sparrow-bt-*` index patterns.
4. Creates the initial backing index and write alias (`sparrow-wifi` /
   `sparrow-bt`).

Bootstrap is idempotent. Re-running the bridge against a cluster that already
has the templates and policies in place is safe — all steps use `PUT` with
`create=false` or check for existence first. There is no risk of data loss or
template overwrite on a healthy cluster.

If bootstrap fails (cluster unreachable, permission denied), a WARN is logged
and the bridge continues running. It will retry bootstrap on each subsequent
flush until it succeeds.

---

## Index Structure

| Item | WiFi | Bluetooth |
|---|---|---|
| Index pattern | `sparrow-wifi-*` | `sparrow-bt-*` |
| Write alias | `sparrow-wifi` | `sparrow-bt` |
| Lifecycle (ES) | ILM policy `sparrow-wifi-ilm` | ILM policy `sparrow-bt-ilm` |
| Lifecycle (OS) | ISM policy `sparrow-wifi-ism` | ISM policy `sparrow-bt-ism` |

Default policy tiers:

| Phase | Action | Trigger |
|---|---|---|
| Hot | Write | Day 0 |
| Warm | Read-only, shrink | 7 days or 10 GB |
| Cold | Read-only, freeze | 14 days (7d warm + 7d threshold) |
| Delete | Remove | 90 days |

Override the policy name with `--ilm-policy` to use a custom policy.

---

## Reference Data Refresh

Five seed data files are bundled under `sparrow_elastic/data/`:

| File | Content | Refresh cadence |
|---|---|---|
| `manuf` | IEEE OUI vendor database | 30 days |
| `bt_sig_company_ids.json` | Bluetooth SIG company ID assignments | 90 days |
| `bt_sig_appearance_values.json` | Bluetooth SIG GAP appearance values | 90 days |
| `bt_sig_service_uuids.json` | Bluetooth SIG assigned service UUIDs | 90 days |
| `apple_continuity_subtypes.json` | Apple Continuity protocol type codes | 90 days |

A background thread checks the modification timestamps of each file at startup
and periodically during operation. When a file exceeds its refresh cadence the
bridge downloads a fresh copy from the authoritative upstream source.

To force an immediate refresh of all data files and then exit (without
connecting to Elasticsearch):

```bash
./sparrow-elastic.py --refresh-data
```

The Fingerbank DB is **not** bundled (the file is approximately 150 MB). It is
downloaded on the first refresh cycle when Fingerbank opt-in is enabled. See
the next section.

---

## Fingerbank Opt-In

Fingerbank provides device fingerprinting beyond MAC OUI lookup. It is disabled
by default.

To enable Fingerbank enrichment, do one of the following:

- Set `fingerbank_api_key` in the config file or via the `SPARROW_FINGERBANK_API_KEY`
  environment variable (uses the live Fingerbank API).
- Drop a pre-downloaded `fingerbank.db` SQLite file at
  `sparrow_elastic/data/fingerbank.db` (offline mode, no API key required).

The offline DB is downloaded automatically on the first `--refresh-data` run
when a Fingerbank API key is present. Subsequent refreshes update it when
upstream publishes a newer version.

Fingerbank classification acts as an **additive refiner** only. It never
overrides Tier 1 classification signals (Class-of-Device, BLE Appearance value,
Apple Continuity type). Tier 1 results always take precedence; Fingerbank fills
in confidence for devices that Tier 1 cannot classify.

---

## Device Classifier Rules

Approximately 64 seed classification rules are stored at
`sparrow_elastic/data/device_classifier_rules.json`. The rules are
git-tracked and operator-editable.

Each rule matches against observed device attributes (OUI prefix, Appearance
value, CoD bitmask, SSID pattern, BLE service UUID) and assigns a
`device.class_guess` label and `device.class_confidence` score (0.0–1.0).

Rules are evaluated in priority order; the first matching rule wins.

To hot-reload rules without restarting the bridge, call `reload_rules()` on
the running classifier instance, or send a `SIGHUP` to the bridge process.

---

## Data Flow and Key Fields

Each WiFi or Bluetooth observation is transformed into a single ECS 8.17
document. The key fields are:

### Standard ECS fields

| Field | Description |
|---|---|
| `@timestamp` | UTC time of the observation (from agent or bridge clock) |
| `ecs.version` | `8.17.0` |
| `event.kind` | `event` |
| `event.category` | `network` |
| `event.type` | `info` |
| `event.ingested` | UTC time the document was built by the bridge |
| `observer.hostname` | Sparrow agent host (set via `--agent-name` or auto from hostname) |
| `observer.type` | `sensor` |
| `source.mac` | Canonicalized MAC address (upper-case, colon-separated) |
| `source.mac_vendor` | OUI vendor string resolved from the bundled manuf file |

### Device classification

| Field | Description |
|---|---|
| `device.id` | SHA-256 of the canonical MAC address |
| `device.class_guess` | Best-guess device class label (e.g. `laptop`, `phone`, `iot`) |
| `device.class_confidence` | Classifier confidence score, 0.0–1.0 |
| `device.class_evidence` | JSON array of evidence tags that drove the classification |

### Signal

| Field | Description |
|---|---|
| `signal.strength_dbm` | RSSI in dBm |
| `signal.strength_mw` | RSSI converted to milliwatts |
| `signal.quality` | Normalized signal quality 0–5 |

### RF

| Field | Description |
|---|---|
| `rf.band` | `2.4GHz` or `5GHz` |
| `rf.frequency_mhz` | Center frequency in MHz |
| `rf.channel_occupied_set` | Array of 20 MHz channel numbers occupied by the BSS |
| `rf.signature.controller_candidate` | `true` if the transmitter shows controller signatures |

### Pre-computed temporal fields

| Field | Description |
|---|---|
| `observed.hour_utc` | Hour of day (0–23) at observation time |
| `observed.day_of_week_utc` | Day of week (0=Mon … 6=Sun) at observation time |
| `observed.age_seconds` | Seconds since the network was first seen |
| `observed.first_seen` | UTC timestamp of first observation |
| `observed.last_seen` | UTC timestamp of most recent observation |

### WiFi-specific nest (`wifi.*`)

| Field | Description |
|---|---|
| `wifi.ssid` | Network SSID (UTF-8) |
| `wifi.bssid` | Access point MAC address |
| `wifi.security` | Security suite string (e.g. `WPA2-PSK`) |
| `wifi.channel.primary` | Primary 20 MHz channel number |
| `wifi.channel.secondary` | Secondary channel (HT/VHT bonding) |
| `wifi.channel.width_mhz` | Channel width: 20, 40, 80, or 160 |
| `wifi.channel.occupied_set` | Array of all occupied 20 MHz channels |
| `wifi.capabilities` | Raw capability flags from beacon |

### Bluetooth-specific nest (`bluetooth.*`)

| Field | Description |
|---|---|
| `bluetooth.address` | BT/BLE device address |
| `bluetooth.name` | Advertised device name (if present) |
| `bluetooth.appearance` | BLE GAP Appearance value (integer) |
| `bluetooth.appearance_label` | Human-readable Appearance label from BT SIG |
| `bluetooth.company_id` | BLE manufacturer company ID |
| `bluetooth.company_name` | Company name resolved from BT SIG company ID list |
| `bluetooth.cod` | Classic Class-of-Device bitmask |
| `bluetooth.service_uuids` | Array of advertised service UUIDs |
| `bluetooth.adv_payload` | Parsed Apple Continuity or other known payload type |

---

## Pre-Computed Pattern-of-Life Fields

`observed.hour_utc` and `observed.day_of_week_utc` are computed at index
time (when the document is built by the bridge) rather than at query time.
This design choice avoids Kibana scripted fields and Painless runtime
calculations on every visualization render.

The practical benefit is that dashboards displaying "devices by hour of day"
or "activity heatmap by day/hour" execute as simple `terms` aggregations on
keyword or integer fields — standard Elasticsearch performance, no scripted
field overhead, and no requirement for the Kibana user to configure runtime
fields manually.

---

## Channel Model

WiFi access points operating in HT (802.11n) and VHT (802.11ac) modes bond
multiple 20 MHz channels into wider channels. The bridge models this as a set
of occupied 20 MHz channels stored in two mirrored fields:

- `wifi.channel.occupied_set` — WiFi-index-specific
- `rf.channel_occupied_set` — cross-index fusion field

Example: a VHT80 access point on primary channel 44 occupies channels
36, 40, 44, and 48. The bridge stores `occupied_set = [36, 40, 44, 48]`
and `channel.width_mhz = 80`.

The `rf.channel_occupied_set` field uses the same values and is intended
for future cross-index queries that join WiFi observations with other RF
datasets (spectrum sweeps, SDR captures) sharing the same field definition.

Supported configurations:

| Width | Example primary | Occupied set |
|---|---|---|
| 20 MHz | 1 | [1] |
| 40 MHz | 6 | [6, 10] |
| 80 MHz | 44 | [36, 40, 44, 48] |
| 160 MHz | 50 | [36, 40, 44, 48, 52, 56, 60, 64] |

---

## Dashboards

Import the bundled dashboards into Kibana with the installer script:

```bash
python install_dashboards.py \
    --kibana-url http://kibana:5601 \
    --username elastic \
    --password elastic
```

To overwrite existing dashboards (e.g. after an upgrade):

```bash
python install_dashboards.py \
    --kibana-url http://kibana:5601 \
    --username elastic --password elastic \
    --overwrite
```

### Bundled dashboards

| Dashboard | Description |
|---|---|
| **Sparrow WiFi — Situational Awareness** | Real-time overview of all observed networks: signal strength, security modes, manufacturer breakdown, geographic heat by channel. |
| **Sparrow WiFi — Pattern of Life** | Temporal activity heatmaps (hour-of-day, day-of-week), first/last seen timelines, and recurring network behavior over time. |
| **Sparrow WiFi — New Device Detection** | Highlights networks and devices observed for the first time within the look-back window; useful for change detection. |
| **Sparrow WiFi — Spectrum Planning** | Channel utilization overlays for 2.4 GHz and 5 GHz bands; assists with access-point channel assignment decisions. |
| **Legacy Preserved** | Legacy sparrowwifi-home visualizations ported to the new index pattern for operators with existing data. |

The installer also imports `index_patterns.ndjson`, which registers the
`sparrow-wifi-*` and `sparrow-bt-*` Kibana index patterns.

---

## Legacy sparrowwifi-home Indices

Operators who have existing data in legacy `sparrowwifi-home*` indices can run
the new bridge without disrupting that data. The new templates only target
`sparrow-wifi-*` and `sparrow-bt-*` patterns; they will not match, modify, or
remap any `sparrowwifi-home*` index.

The file `sparrow_elastic/dashboards/legacy_preserved.ndjson` contains the
legacy visualizations ported to target the new `sparrow-wifi-*` index pattern.
Import it with the installer if you want the legacy views available in the new
Kibana space.

---

## Troubleshooting

### Bootstrap failure

If the bridge cannot connect to the cluster at startup, bootstrap is skipped
and a WARN message is logged:

```
WARN sparrow_elastic.elasticsearch_client: bootstrap failed: <reason> — will retry
```

The bridge keeps running and retries bootstrap on the next flush cycle. This
means the bridge can start before the cluster is available and will
self-recover once connectivity is established.

### Mapping rejection with `dynamic: strict`

The templates use `dynamic: strict` to prevent unintended field explosion. If
you see a mapping rejection error in the bulk response, a new field is being
sent that is not in the template. Options:

- Check `tests/test_mapping_compliance.py` — run it against your deployment.
- If you have added a custom field, add it to the appropriate component template
  under `sparrow_elastic/templates/`.
- If the bridge version is newer than the deployed template, re-run bootstrap
  to update the template.

### Fingerbank rate limit

When using the live Fingerbank API, rate-limit errors (HTTP 429) are logged at
DEBUG level and the bridge falls back to OUI-only enrichment for that document.
To avoid rate limits on high-volume deployments, switch to offline DB mode:

1. Run `./sparrow-elastic.py --refresh-data` with a valid `fingerbank_api_key`
   to download `fingerbank.db`.
2. Remove the API key from the config. The bridge will use the offline DB
   automatically.

### ES vs OpenSearch template divergence

Elasticsearch composable templates use component template references;
OpenSearch does not support them in the same way. The bridge resolves this by
inlining all component template content into a single flat template body when
targeting OpenSearch:

```python
template = resolve_template(for_opensearch=True)
```

If you see template creation failures on OpenSearch, check that
`--engine opensearch` is set correctly. Do not pass an ES template body
directly to an OpenSearch cluster.

---

## CLI Reference

All flags correspond to `sparrow-elastic.py`. Environment variables are listed
in parentheses where available.

| Flag | Default | Env | Description |
|---|---|---|---|
| `--elasticserver URL` | `""` | `SPARROW_ES_URL` | Elasticsearch/OpenSearch server URL. Required unless `--refresh-data`. |
| `--engine {elasticsearch,opensearch}` | `elasticsearch` | `SPARROW_ES_ENGINE` | Backend engine selection. |
| `--username STR` | `""` | `SPARROW_ES_USERNAME` | HTTP Basic auth username. |
| `--password STR` | `""` | `SPARROW_ES_PASSWORD` | HTTP Basic auth password. |
| `--api-key STR` | `""` | `SPARROW_ES_API_KEY` | Elasticsearch API key (base64 id:key). Not supported on OpenSearch. |
| `--verify-tls` | `True` | — | Verify TLS certificates (default). |
| `--no-verify-tls` | — | — | Disable TLS certificate verification. |
| `--sparrowagent HOST` | `127.0.0.1` | — | Sparrow WiFi agent hostname or IP. |
| `--sparrowport PORT` | `8020` | — | Sparrow WiFi agent HTTP port. |
| `--wifiinterface NAME` | `""` | — | Wireless interface on the agent. Uses first available when omitted. |
| `--wifi-alias NAME` | `sparrow-wifi` | — | Write alias for WiFi documents. |
| `--bt-alias NAME` | `sparrow-bt` | — | Write alias for Bluetooth documents. Empty string disables BT indexing. |
| `--ilm-policy NAME` | `""` | — | Override ILM/ISM policy name. Defaults to `sparrow-wifi-ilm` / `sparrow-bt-ilm`. |
| `--scandelay SECONDS` | `15.0` | — | Seconds between agent scan cycles. |
| `--flush-interval SECONDS` | `5.0` | — | Maximum seconds between bulk flushes. |
| `--batch-size N` | `500` | — | Document count that triggers an early flush. |
| `--fingerbank-api-key STR` | `""` | `SPARROW_FINGERBANK_API_KEY` | Fingerbank live-API key for device enrichment. |
| `--agent-name STR` | `""` | — | Observer identifier in every document. Defaults to system hostname. |
| `--dont-create-indices` | `False` | — | Skip bootstrap (template + policy creation). |
| `--refresh-data` | `False` | — | Refresh all bundled reference data files then exit. |
| `--debug` | `False` | — | Enable DEBUG-level logging. |

---

## Files Layout

```
sparrow_elastic/                    # Python package
    __init__.py
    settings.py                     # INI + env config loader
    client_factory.py               # SearchClientFactory entry point
    search_client.py                # Abstract SearchClient base
    elasticsearch_client.py         # Elasticsearch-specific client
    opensearch_client.py            # OpenSearch-specific client
    document_builder.py             # ECS 8.17 document construction
    device_classifier.py            # Device classification engine
    fingerbank_client.py            # Fingerbank API + offline DB client
    data_refresh.py                 # Background reference data refresh
    bulk_buffer.py                  # Thread-safe bulk document buffer
    channel_utils.py                # WiFi channel / occupied-set math
    signal_utils.py                 # RSSI unit conversion
    mac_utils.py                    # MAC canonicalization and OUI lookup
    ecs_helpers.py                  # ECS timestamp and constant helpers
    controller_signature.py         # RF controller candidate detection
    ble_adv_parser.py               # BLE advertisement payload parser
    data/                           # Bundled reference data (git-tracked)
        manuf                       # IEEE OUI vendor database
        bt_sig_company_ids.json
        bt_sig_appearance_values.json
        bt_sig_service_uuids.json
        apple_continuity_subtypes.json
        device_classifier_rules.json
        sparrow-wifi-ilm.json       # ES ILM policy
        sparrow-bt-ilm.json
        sparrow-wifi-ism.json       # OpenSearch ISM policy
        sparrow-bt-ism.json
    templates/                      # Index template JSON bodies
        sparrow-wifi-template.json
        sparrow-bt-template.json
        sparrow-wifi-components/    # ES component templates (WiFi)
        sparrow-bt-components/      # ES component templates (BT)
    dashboards/                     # Kibana saved-objects NDJSON
        index_patterns.ndjson
        sparrow_wifi_situational_awareness.ndjson
        sparrow_wifi_pattern_of_life.ndjson
        sparrow_wifi_new_device_detection.ndjson
        sparrow_wifi_spectrum_planning.ndjson
        legacy_preserved.ndjson

sparrow-elastic.py                  # Bridge entry point (repo root)
install_dashboards.py               # Kibana dashboard installer (repo root)
requirements-elastic.txt            # Python dependencies
sparrow-elastic.conf.example        # INI config example
sparrow-elastic.env.example         # Shell env file example (systemd)
init.d_scripts/
    sparrow-elastic.service.example # systemd unit template
```
