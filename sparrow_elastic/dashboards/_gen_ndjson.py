"""
Generator script — run once to produce all NDJSON dashboard files.
Not deployed; kept alongside the dashboards for maintenance reference.
"""

import json
import os

DASHBOARDS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _ss(index_ref="kibanaSavedObjectMeta.searchSourceJSON.index",
        extra_filters=None):
    """Return serialised searchSourceJSON string."""
    obj = {
        "query": {"query": "", "language": "kuery"},
        "filter": extra_filters or [],
        "indexRefName": index_ref,
    }
    return json.dumps(obj)


def viz(vid, title, vis_type, aggs, params,
        index_id="sparrow-wifi", extra_filters=None, description=""):
    """Build a legacy visualization saved-object dict (one NDJSON line)."""
    vis_state = {
        "title": title,
        "type": vis_type,
        "params": params,
        "aggs": aggs,
    }
    return {
        "id": vid,
        "type": "visualization",
        "attributes": {
            "title": title,
            "visState": json.dumps(vis_state),
            "uiStateJSON": "{}",
            "description": description,
            "version": 1,
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": _ss(extra_filters=extra_filters),
            },
        },
        "references": [
            {
                "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                "type": "index-pattern",
                "id": index_id,
            }
        ],
        "coreMigrationVersion": "8.17.0",
        "typeMigrationVersion": "8.0.0",
        "managed": False,
    }


def dashboard(did, title, panels, viz_ids, index_id="sparrow-wifi",
              description="", time_from="now-24h", time_to="now"):
    """Build a dashboard saved-object dict."""
    # Build references list: one per referenced visualization
    references = []
    for i, vid in enumerate(viz_ids):
        references.append({
            "name": f"panel_{i}",
            "type": "visualization",
            "id": vid,
        })
    # Also reference the index pattern
    references.append({
        "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
        "type": "index-pattern",
        "id": index_id,
    })

    return {
        "id": did,
        "type": "dashboard",
        "attributes": {
            "title": title,
            "description": description,
            "hits": 0,
            "timeRestore": False,
            "timeTo": time_to,
            "timeFrom": time_from,
            "refreshInterval": {"pause": True, "value": 0},
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(
                    {"query": {"query": "", "language": "kuery"}, "filter": []}
                ),
            },
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False,
                                       "hidePanelTitles": False}),
            "panelsJSON": json.dumps(panels),
            "version": 1,
        },
        "references": references,
        "coreMigrationVersion": "8.17.0",
        "typeMigrationVersion": "8.3.0",
        "managed": False,
    }


def write_ndjson(filename, objects):
    """Write list of dicts as NDJSON (one JSON object per line)."""
    path = os.path.join(DASHBOARDS_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        for obj in objects:
            fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
    print(f"  wrote {path}  ({len(objects)} objects, "
          f"{os.path.getsize(path):,} bytes)")


def panel_entry(panel_index, viz_id, title, grid_x, grid_y, grid_w, grid_h):
    """Build a dashboard panel grid entry."""
    return {
        "panelIndex": str(panel_index),
        "gridData": {"x": grid_x, "y": grid_y, "w": grid_w, "h": grid_h,
                     "i": str(panel_index)},
        "version": "8.17.0",
        "type": "visualization",
        "id": viz_id,
        "title": title,
        "embeddableConfig": {"enhancements": {}},
    }


# ---------------------------------------------------------------------------
# 8a — index_patterns.ndjson
# ---------------------------------------------------------------------------

def gen_index_patterns():
    objects = [
        {
            "id": "sparrow-wifi",
            "type": "index-pattern",
            "attributes": {
                "name": "sparrow-wifi",
                "title": "sparrow-wifi-*",
                "timeFieldName": "@timestamp",
            },
            "references": [],
            "managed": False,
            "coreMigrationVersion": "8.17.0",
            "typeMigrationVersion": "8.0.0",
            "version": "WzEsMV0=",
        },
        {
            "id": "sparrow-bt",
            "type": "index-pattern",
            "attributes": {
                "name": "sparrow-bt",
                "title": "sparrow-bt-*",
                "timeFieldName": "@timestamp",
            },
            "references": [],
            "managed": False,
            "coreMigrationVersion": "8.17.0",
            "typeMigrationVersion": "8.0.0",
            "version": "WzIsMV0=",
        },
    ]
    write_ndjson("index_patterns.ndjson", objects)


# ---------------------------------------------------------------------------
# 8b-A — sparrow_wifi_situational_awareness.ndjson
# ---------------------------------------------------------------------------

def gen_situational_awareness():
    # 1. Metric: Unique APs (cardinality of source.mac)
    v1 = viz(
        "sw-sa-metric-unique-aps",
        "Sparrow WiFi — Unique APs (last 5m)",
        "metric",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "cardinality",
                "schema": "metric",
                "params": {"field": "source.mac", "customLabel": "Unique APs"},
            }
        ],
        {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {"percentageMode": False, "useRanges": False,
                       "colorSchema": "Green to Red", "metricColorMode": "None",
                       "colorsRange": [{"from": 0, "to": 10000}],
                       "labels": {"show": True}, "invertColors": False,
                       "style": {"bgFill": "#000", "bgColor": False,
                                 "labelColor": False, "subText": "",
                                 "fontSize": 60}},
        },
        description="Count of unique MAC addresses seen in the selected time window",
    )

    # 2. Metric: Unique SSIDs (cardinality of wifi.ssid)
    v2 = viz(
        "sw-sa-metric-unique-ssids",
        "Sparrow WiFi — Unique SSIDs",
        "metric",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "cardinality",
                "schema": "metric",
                "params": {"field": "wifi.ssid", "customLabel": "Unique SSIDs"},
            }
        ],
        {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {"percentageMode": False, "useRanges": False,
                       "colorSchema": "Green to Red", "metricColorMode": "None",
                       "colorsRange": [{"from": 0, "to": 10000}],
                       "labels": {"show": True}, "invertColors": False,
                       "style": {"bgFill": "#000", "bgColor": False,
                                 "labelColor": False, "subText": "",
                                 "fontSize": 60}},
        },
        description="Count of unique SSIDs seen in the selected time window",
    )

    # 3. Data table: Top SSIDs with security
    v3 = viz(
        "sw-sa-table-top-ssids",
        "Sparrow WiFi — Top SSIDs",
        "table",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "wifi.ssid",
                    "size": 20,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "otherBucketLabel": "Other",
                    "missingBucket": False,
                    "customLabel": "SSID",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "max",
                "schema": "metric",
                "params": {"field": "signal.strength_dbm",
                           "customLabel": "Max RSSI (dBm)"},
            },
            {
                "id": "4",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "wifi.security",
                    "size": 5,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "Security",
                },
            },
        ],
        {
            "perPage": 20,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "sort": {"columnIndex": None, "direction": None},
            "showTotal": False,
            "totalFunc": "sum",
        },
        description="Top 20 SSIDs with security type and max signal strength",
    )

    # 4. Histogram: APs by signal strength (bar)
    v4 = viz(
        "sw-sa-bar-signal-hist",
        "Sparrow WiFi — AP Signal Strength Distribution",
        "histogram",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "AP Count"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "histogram",
                "schema": "segment",
                "params": {
                    "field": "signal.strength_dbm",
                    "interval": 10,
                    "extended_bounds": {},
                    "customLabel": "RSSI bucket (dBm)",
                },
            },
        ],
        {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "AP Count"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "histogram",
                    "mode": "stacked",
                    "data": {"label": "AP Count", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        description="Distribution of APs by RSSI bucket (10 dBm intervals)",
    )

    # 5. Observations over time — line, date_histogram, split by observer.id
    v5 = viz(
        "sw-sa-line-obs-over-time",
        "Sparrow WiFi — Observations Over Time by Sensor",
        "line",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "date_histogram",
                "schema": "segment",
                "params": {
                    "field": "@timestamp",
                    "timeRange": {"from": "now-24h", "to": "now"},
                    "useNormalizedEsInterval": True,
                    "scaleMetricValues": False,
                    "interval": "auto",
                    "drop_partials": False,
                    "min_doc_count": 1,
                    "extended_bounds": {},
                    "customLabel": "Time",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "terms",
                "schema": "group",
                "params": {
                    "field": "observer.id",
                    "size": 5,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "Sensor",
                },
            },
        ],
        {
            "type": "line",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "Observations"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "line",
                    "mode": "normal",
                    "data": {"label": "Observations", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                    "interpolate": "linear",
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        description="WiFi scan event rate over time, split by sensor/observer",
    )

    viz_ids = [v1["id"], v2["id"], v3["id"], v4["id"], v5["id"]]

    panels = [
        panel_entry(1, v1["id"], v1["attributes"]["title"], 0, 0, 12, 8),
        panel_entry(2, v2["id"], v2["attributes"]["title"], 12, 0, 12, 8),
        panel_entry(3, v3["id"], v3["attributes"]["title"], 24, 0, 24, 16),
        panel_entry(4, v4["id"], v4["attributes"]["title"], 0, 8, 24, 16),
        panel_entry(5, v5["id"], v5["attributes"]["title"], 0, 24, 48, 16),
    ]

    db = dashboard(
        "sw-dashboard-situational-awareness",
        "Sparrow WiFi — Situational Awareness",
        panels, viz_ids,
        description="Real-time WiFi network census: unique APs, SSIDs, signal distribution, and per-sensor activity",
    )

    write_ndjson("sparrow_wifi_situational_awareness.ndjson",
                 [v1, v2, v3, v4, v5, db])


# ---------------------------------------------------------------------------
# 8b-B — sparrow_wifi_pattern_of_life.ndjson
# ---------------------------------------------------------------------------

def gen_pattern_of_life():
    # 1. Histogram: device age buckets (range agg)
    v1 = viz(
        "sw-pol-bar-age-buckets",
        "Sparrow WiFi — Device Age Distribution",
        "histogram",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "cardinality",
                "schema": "metric",
                "params": {"field": "source.mac", "customLabel": "Unique Devices"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "range",
                "schema": "bucket",
                "params": {
                    "field": "observed.age_seconds",
                    "ranges": [
                        {"from": 0, "to": 300, "label": "< 5 min (new)"},
                        {"from": 300, "to": 1800, "label": "5-30 min"},
                        {"from": 1800, "to": 21600, "label": "30 min – 6 hr"},
                        {"from": 21600, "to": 999999999, "label": "> 6 hr (persistent)"},
                    ],
                    "customLabel": "Device Age",
                },
            },
        ],
        {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "Unique Devices"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "histogram",
                    "mode": "stacked",
                    "data": {"label": "Unique Devices", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        description="Unique device count bucketed by how long they have been observed",
    )

    # 2. Pie: class distribution
    v2 = viz(
        "sw-pol-pie-class-dist",
        "Sparrow WiFi — Device Class Distribution",
        "pie",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Count"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "device.class_guess",
                    "size": 10,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": True,
                    "otherBucketLabel": "Other",
                    "missingBucket": True,
                    "missingBucketLabel": "Unknown",
                    "customLabel": "Device Class",
                },
            },
        ],
        {
            "type": "pie",
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "isDonut": True,
            "labels": {"show": False, "values": True, "last_level": True,
                       "truncate": 100},
            "palette": {"type": "palette", "name": "default"},
        },
        description="Top 10 device class guesses (AP, phone, laptop, etc.)",
    )

    # 3a. Metric: randomized MAC count
    v3a = viz(
        "sw-pol-metric-mac-rand",
        "Sparrow WiFi — Randomized MAC Count",
        "metric",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Randomized MACs"},
            }
        ],
        {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {"percentageMode": False, "useRanges": False,
                       "colorSchema": "Green to Red", "metricColorMode": "None",
                       "colorsRange": [{"from": 0, "to": 10000}],
                       "labels": {"show": True}, "invertColors": False,
                       "style": {"bgFill": "#000", "bgColor": False,
                                 "labelColor": False, "subText": "",
                                 "fontSize": 60}},
        },
        extra_filters=[
            {
                "meta": {
                    "index": "sparrow-wifi",
                    "negate": False,
                    "disabled": False,
                    "alias": None,
                    "type": "phrase",
                    "key": "wifi.mac.randomized",
                    "params": {"query": True},
                },
                "query": {"match_phrase": {"wifi.mac.randomized": True}},
                "$state": {"store": "appState"},
            }
        ],
        description="Count of devices with randomized/locally-administered MAC addresses",
    )

    # 3b. Metric: total observations (for ratio context)
    v3b = viz(
        "sw-pol-metric-total-obs",
        "Sparrow WiFi — Total Observations",
        "metric",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Total Observations"},
            }
        ],
        {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {"percentageMode": False, "useRanges": False,
                       "colorSchema": "Green to Red", "metricColorMode": "None",
                       "colorsRange": [{"from": 0, "to": 10000000}],
                       "labels": {"show": True}, "invertColors": False,
                       "style": {"bgFill": "#000", "bgColor": False,
                                 "labelColor": False, "subText": "",
                                 "fontSize": 60}},
        },
        description="Total WiFi observation count in the selected time window",
    )

    # 4. Heatmap: day-of-week × hour
    v4 = viz(
        "sw-pol-heatmap-dow-hour",
        "Sparrow WiFi — Activity Heatmap (Day × Hour UTC)",
        "heatmap",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "observed.day_of_week_utc",
                    "size": 7,
                    "order": "asc",
                    "orderBy": "_key",
                    "otherBucket": False,
                    "customLabel": "Day of Week",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "histogram",
                "schema": "group",
                "params": {
                    "field": "observed.hour_utc",
                    "interval": 1,
                    "min_doc_count": 0,
                    "extended_bounds": {"min": 0, "max": 23},
                    "customLabel": "Hour (UTC)",
                },
            },
        ],
        {
            "type": "heatmap",
            "addTooltip": True,
            "addLegend": True,
            "enableHover": False,
            "legendPosition": "right",
            "times": [],
            "colorsNumber": 8,
            "colorSchema": "Greens",
            "setColorRange": False,
            "colorsRange": [],
            "invertColors": False,
            "percentageMode": False,
            "valueAxes": [
                {
                    "show": False,
                    "id": "ValueAxis-1",
                    "type": "value",
                    "scale": {"type": "linear", "defaultYExtents": False},
                    "labels": {"show": False, "rotate": 0, "overwriteColor": False,
                               "color": "black"},
                }
            ],
        },
        description="WiFi activity heatmap: day of week vs hour of day (UTC)",
    )

    viz_ids = [v1["id"], v2["id"], v3a["id"], v3b["id"], v4["id"]]
    panels = [
        panel_entry(1, v1["id"], v1["attributes"]["title"], 0, 0, 24, 16),
        panel_entry(2, v2["id"], v2["attributes"]["title"], 24, 0, 24, 16),
        panel_entry(3, v3a["id"], v3a["attributes"]["title"], 0, 16, 24, 8),
        panel_entry(4, v3b["id"], v3b["attributes"]["title"], 24, 16, 24, 8),
        panel_entry(5, v4["id"], v4["attributes"]["title"], 0, 24, 48, 18),
    ]

    db = dashboard(
        "sw-dashboard-pattern-of-life",
        "Sparrow WiFi — Pattern of Life",
        panels, viz_ids,
        description="Long-term device behavior patterns: age distribution, class mix, MAC randomization, and temporal activity heatmap",
    )

    write_ndjson("sparrow_wifi_pattern_of_life.ndjson",
                 [v1, v2, v3a, v3b, v4, db])


# ---------------------------------------------------------------------------
# 8b-C — sparrow_wifi_new_device_detection.ndjson
# ---------------------------------------------------------------------------

NEW_DEVICE_FILTER = [
    {
        "meta": {
            "index": "sparrow-wifi",
            "negate": False,
            "disabled": False,
            "alias": "New devices (<5m)",
            "type": "range",
            "key": "observed.age_seconds",
            "params": {"gte": 0, "lt": 300},
        },
        "range": {"observed.age_seconds": {"gte": 0, "lt": 300}},
        "$state": {"store": "appState"},
    }
]

CONTROLLER_FILTER = [
    {
        "meta": {
            "index": "sparrow-wifi",
            "negate": False,
            "disabled": False,
            "alias": "Controller candidates (<10m)",
            "type": "combined",
            "relation": "AND",
            "params": [],
        },
        "query": {
            "bool": {
                "must": [
                    {"term": {"rf.signature.controller_candidate": True}},
                    {"range": {"observed.age_seconds": {"gte": 0, "lt": 600}}},
                ]
            }
        },
        "$state": {"store": "appState"},
    }
]

MAC_RAND_NEW_FILTER = [
    {
        "meta": {
            "index": "sparrow-wifi",
            "negate": False,
            "disabled": False,
            "alias": "Randomized + new (<5m)",
            "type": "combined",
            "relation": "AND",
            "params": [],
        },
        "query": {
            "bool": {
                "must": [
                    {"term": {"wifi.mac.randomized": True}},
                    {"range": {"observed.age_seconds": {"gte": 0, "lt": 300}}},
                ]
            }
        },
        "$state": {"store": "appState"},
    }
]


def gen_new_device_detection():
    # 1. Table: new devices (<5m)
    v1 = viz(
        "sw-ndd-table-new-devices",
        "Sparrow WiFi — New Devices (< 5 minutes)",
        "table",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "source.mac",
                    "size": 50,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "MAC Address",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "wifi.ssid",
                    "size": 3,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "SSID",
                },
            },
            {
                "id": "4",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "device.class_guess",
                    "size": 3,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "Device Class",
                },
            },
        ],
        {
            "perPage": 25,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "sort": {"columnIndex": None, "direction": None},
            "showTotal": False,
            "totalFunc": "sum",
        },
        extra_filters=NEW_DEVICE_FILTER,
        description="Devices first observed less than 5 minutes ago",
    )

    # 2. Line chart: new device rate per hour
    v2 = viz(
        "sw-ndd-line-new-rate",
        "Sparrow WiFi — New Device Rate Over Time",
        "line",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "New Devices"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "date_histogram",
                "schema": "segment",
                "params": {
                    "field": "@timestamp",
                    "useNormalizedEsInterval": True,
                    "scaleMetricValues": False,
                    "interval": "1h",
                    "drop_partials": False,
                    "min_doc_count": 1,
                    "extended_bounds": {},
                    "customLabel": "Time",
                },
            },
        ],
        {
            "type": "line",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "New Device Count"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "line",
                    "mode": "normal",
                    "data": {"label": "New Devices", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                    "interpolate": "linear",
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        extra_filters=NEW_DEVICE_FILTER,
        description="Rate of new device appearances per hour over time",
    )

    # 3. Table: new drone-controllers
    v3 = viz(
        "sw-ndd-table-controllers",
        "Sparrow WiFi — New Drone Controller Candidates",
        "table",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "source.mac",
                    "size": 25,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "MAC Address",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "terms",
                "schema": "bucket",
                "params": {
                    "field": "wifi.ssid",
                    "size": 3,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "SSID",
                },
            },
        ],
        {
            "perPage": 25,
            "showPartialRows": False,
            "showMetricsAtAllLevels": False,
            "sort": {"columnIndex": None, "direction": None},
            "showTotal": False,
            "totalFunc": "sum",
        },
        extra_filters=CONTROLLER_FILTER,
        description="Recently seen devices flagged as potential drone RC controllers",
    )

    # 4. Metric: new randomized MAC devices
    v4 = viz(
        "sw-ndd-metric-rand-new",
        "Sparrow WiFi — New Randomized MACs",
        "metric",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "New Randomized MACs"},
            }
        ],
        {
            "addTooltip": True,
            "addLegend": False,
            "type": "metric",
            "metric": {"percentageMode": False, "useRanges": False,
                       "colorSchema": "Green to Red", "metricColorMode": "None",
                       "colorsRange": [{"from": 0, "to": 10000}],
                       "labels": {"show": True}, "invertColors": False,
                       "style": {"bgFill": "#000", "bgColor": False,
                                 "labelColor": False, "subText": "",
                                 "fontSize": 60}},
        },
        extra_filters=MAC_RAND_NEW_FILTER,
        description="Devices with randomized MACs seen for the first time in the last 5 minutes",
    )

    viz_ids = [v1["id"], v2["id"], v3["id"], v4["id"]]
    panels = [
        panel_entry(1, v1["id"], v1["attributes"]["title"], 0, 0, 24, 18),
        panel_entry(2, v2["id"], v2["attributes"]["title"], 24, 0, 24, 18),
        panel_entry(3, v3["id"], v3["attributes"]["title"], 0, 18, 36, 16),
        panel_entry(4, v4["id"], v4["attributes"]["title"], 36, 18, 12, 8),
    ]

    db = dashboard(
        "sw-dashboard-new-device-detection",
        "Sparrow WiFi — New Device Detection",
        panels, viz_ids,
        description="Alert-style dashboard highlighting devices seen for the first time, potential drone controllers, and privacy-probing randomized MACs",
    )

    write_ndjson("sparrow_wifi_new_device_detection.ndjson",
                 [v1, v2, v3, v4, db])


# ---------------------------------------------------------------------------
# 8b-D — sparrow_wifi_spectrum_planning.ndjson
# ---------------------------------------------------------------------------

def gen_spectrum_planning():
    # 1. Bar: RSSI-weighted channel occupancy (sum of signal.strength_mw)
    v1 = viz(
        "sw-sp-bar-rssi-channel",
        "Sparrow WiFi — RSSI-Weighted Channel Occupancy",
        "histogram",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "sum",
                "schema": "metric",
                "params": {"field": "signal.strength_mw",
                           "customLabel": "Total Signal Power (mW)"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "wifi.channel.occupied_set",
                    "size": 30,
                    "order": "asc",
                    "orderBy": "_key",
                    "otherBucket": False,
                    "customLabel": "Channel",
                },
            },
        ],
        {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "Signal Power (mW sum)"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "histogram",
                    "mode": "stacked",
                    "data": {"label": "Total Signal Power (mW)", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        description="Per-channel aggregate signal power (mW sum) — proxy for channel saturation",
    )

    # 2. Heatmap: channel × hour
    v2 = viz(
        "sw-sp-heatmap-channel-hour",
        "Sparrow WiFi — Channel Activity by Hour",
        "heatmap",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Observations"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "histogram",
                "schema": "segment",
                "params": {
                    "field": "wifi.channel.primary",
                    "interval": 1,
                    "min_doc_count": 1,
                    "extended_bounds": {},
                    "customLabel": "Primary Channel",
                },
            },
            {
                "id": "3",
                "enabled": True,
                "type": "histogram",
                "schema": "group",
                "params": {
                    "field": "observed.hour_utc",
                    "interval": 1,
                    "min_doc_count": 0,
                    "extended_bounds": {"min": 0, "max": 23},
                    "customLabel": "Hour (UTC)",
                },
            },
        ],
        {
            "type": "heatmap",
            "addTooltip": True,
            "addLegend": True,
            "enableHover": False,
            "legendPosition": "right",
            "times": [],
            "colorsNumber": 8,
            "colorSchema": "Blues",
            "setColorRange": False,
            "colorsRange": [],
            "invertColors": False,
            "percentageMode": False,
            "valueAxes": [
                {
                    "show": False,
                    "id": "ValueAxis-1",
                    "type": "value",
                    "scale": {"type": "linear", "defaultYExtents": False},
                    "labels": {"show": False, "rotate": 0, "overwriteColor": False,
                               "color": "black"},
                }
            ],
        },
        description="WiFi activity heatmap: channel vs hour of day",
    )

    # 3. Pie: band distribution
    v3 = viz(
        "sw-sp-pie-band-dist",
        "Sparrow WiFi — Band Distribution",
        "pie",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Count"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "rf.band",
                    "size": 10,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": True,
                    "otherBucketLabel": "Other",
                    "missingBucket": True,
                    "missingBucketLabel": "Unknown",
                    "customLabel": "Band",
                },
            },
        ],
        {
            "type": "pie",
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "isDonut": True,
            "labels": {"show": False, "values": True, "last_level": True,
                       "truncate": 100},
            "palette": {"type": "palette", "name": "default"},
        },
        description="Distribution of APs across 2.4 GHz, 5 GHz, and 6 GHz bands",
    )

    # 4. Pie: channel width distribution
    v4 = viz(
        "sw-sp-pie-width-dist",
        "Sparrow WiFi — Channel Width Distribution",
        "pie",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "Count"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "wifi.channel.width_mhz",
                    "size": 10,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": True,
                    "otherBucketLabel": "Other",
                    "missingBucket": True,
                    "missingBucketLabel": "Unknown",
                    "customLabel": "Width (MHz)",
                },
            },
        ],
        {
            "type": "pie",
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "isDonut": True,
            "labels": {"show": False, "values": True, "last_level": True,
                       "truncate": 100},
            "palette": {"type": "palette", "name": "default"},
        },
        description="Distribution of channel widths (20/40/80/160 MHz)",
    )

    # 5. Bar: top channels by count
    v5 = viz(
        "sw-sp-bar-top-channels",
        "Sparrow WiFi — Top Channels by AP Count",
        "histogram",
        [
            {
                "id": "1",
                "enabled": True,
                "type": "count",
                "schema": "metric",
                "params": {"customLabel": "AP Count"},
            },
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": "wifi.channel.occupied_set",
                    "size": 20,
                    "order": "desc",
                    "orderBy": "1",
                    "otherBucket": False,
                    "customLabel": "Channel",
                },
            },
        ],
        {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [
                {
                    "id": "CategoryAxis-1",
                    "type": "category",
                    "position": "bottom",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear"},
                    "labels": {"show": True, "truncate": 100},
                    "title": {},
                }
            ],
            "valueAxes": [
                {
                    "id": "ValueAxis-1",
                    "name": "LeftAxis-1",
                    "type": "value",
                    "position": "left",
                    "show": True,
                    "style": {},
                    "scale": {"type": "linear", "mode": "normal"},
                    "labels": {"show": True, "rotate": 0, "filter": False,
                               "truncate": 100},
                    "title": {"text": "AP Count"},
                }
            ],
            "seriesParams": [
                {
                    "show": True,
                    "type": "histogram",
                    "mode": "stacked",
                    "data": {"label": "AP Count", "id": "1"},
                    "valueAxis": "ValueAxis-1",
                    "drawLinesBetweenPoints": True,
                    "lineWidth": 2,
                    "showCircles": True,
                }
            ],
            "addTooltip": True,
            "addLegend": True,
            "legendPosition": "right",
            "times": [],
            "addTimeMarker": False,
            "palette": {"type": "palette", "name": "default"},
        },
        description="Top 20 channels by AP count across occupied channel set",
    )

    viz_ids = [v1["id"], v2["id"], v3["id"], v4["id"], v5["id"]]
    panels = [
        panel_entry(1, v1["id"], v1["attributes"]["title"], 0, 0, 48, 16),
        panel_entry(2, v2["id"], v2["attributes"]["title"], 0, 16, 48, 18),
        panel_entry(3, v3["id"], v3["attributes"]["title"], 0, 34, 16, 16),
        panel_entry(4, v4["id"], v4["attributes"]["title"], 16, 34, 16, 16),
        panel_entry(5, v5["id"], v5["attributes"]["title"], 32, 34, 16, 16),
    ]

    db = dashboard(
        "sw-dashboard-spectrum-planning",
        "Sparrow WiFi — Spectrum Planning",
        panels, viz_ids,
        description="RF spectrum planning view: channel occupancy, activity heatmap, band and width distribution, and channel utilization ranking",
    )

    write_ndjson("sparrow_wifi_spectrum_planning.ndjson",
                 [v1, v2, v3, v4, v5, db])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating NDJSON dashboard files...")
    gen_index_patterns()
    gen_situational_awareness()
    gen_pattern_of_life()
    gen_new_device_detection()
    gen_spectrum_planning()
    print("Done.")
