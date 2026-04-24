# Legacy Elasticsearch Bridge

This directory contains the original Sparrow-WiFi Elasticsearch integration,
preserved for reference and backward compatibility with pre-April-2026 Kibana
deployments.

## Status: FROZEN

These files are not maintained. No bug fixes or feature updates will be applied
here. For new installations use the `sparrow_elastic/` package at the repo root.

## Contents

| File | Purpose |
|------|---------|
| `sparrow-elastic.py` | Original ECS 1.5 bridge script (WiFi + BT) |
| `sparrow_elastic_sparrowwifi_index_template.txt` | Legacy WiFi index template |
| `sparrow_elastic_sparrowbt_index_template.txt` | Legacy Bluetooth index template |
| `sparrow_elastic_lifecycle_policy.txt` | Legacy ILM lifecycle policy |

## Index Patterns

| Version | WiFi pattern | Bluetooth pattern |
|---------|-------------|-------------------|
| Legacy (this dir) | `sparrowwifi-home*` | `sparrowbt-home*` |
| New (`sparrow_elastic/`) | `sparrow-wifi-*` | `sparrow-bt-*` |

The old and new patterns do not overlap. Existing Kibana dashboards built
against `sparrowwifi-home*` / `sparrowbt-home*` will continue to work as long
as the legacy index data is present.

## Behavior Differences (Legacy vs New)

- **MAC format**: legacy bridge emits whatever format the scanner returns
  (mixed case, inconsistent separators). New bridge always canonicalizes to
  `AA:BB:CC:DD:EE:FF` (uppercase, colon-separated).
- **Field mangling**: legacy bridge renames and drops fields to fit ECS 1.5.
  New bridge targets ECS 8.x with clean field names and no silent drops.
- **AP documents**: legacy bridge may emit multiple documents per AP across
  scan cycles with differing channel counts. New bridge emits a single document
  per AP with channels stored as a `wifi.channel.occupied_set` array.
- **BLE address type**: not classified in legacy bridge. New bridge populates
  `ble.addr_type` (`universal`, `random_static`, `random_resolvable`,
  `random_nonresolvable`).
