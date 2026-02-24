#!/usr/bin/python3
#
# test_sparrowmap.py - Smoke-test for MapEngineOSM
#
# Generates two HTML map files to /tmp/ and opens them in the browser:
#   1. Scatter map  - simulates a WiFi neighbourhood scan (no path)
#   2. Telemetry map - simulates a drive-scan (markers connected by polyline)
#
# Each marker's click popup shows lat/lon and the stacked SSID list.

import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sparrowmap import MapMarker, MapEngineOSM, MapEngineBase

# ---------------------------------------------------------------------------
# Dummy data - a small grid of access points around a central coordinate.
# Using a real-ish area: downtown Annapolis, MD (easily recognisable on OSM).
# ---------------------------------------------------------------------------
CENTER_LAT  = 38.9784
CENTER_LON  = -76.4922

def make_marker(lat, lon, ssids, bar_count, gps_valid=True):
    m = MapMarker()
    m.latitude  = lat
    m.longitude = lon
    m.barCount  = bar_count
    m.gpsValid  = gps_valid
    if isinstance(ssids, list):
        for s in ssids:
            m.addLabel(s)
    else:
        m.addLabel(ssids)
    return m

# -- Test 1: scatter scan map ------------------------------------------------
# Nine locations arranged in a 3x3 grid; each has 1-3 overlapping SSIDs and
# varying signal strength so we can verify bar icons 1-4 all render.

SCATTER_MARKERS = [
    make_marker(CENTER_LAT + 0.002, CENTER_LON - 0.003, ["HomeNet_2G", "HomeNet_5G"], 4),
    make_marker(CENTER_LAT + 0.002, CENTER_LON,         ["XFINITY_Guest"],            3),
    make_marker(CENTER_LAT + 0.002, CENTER_LON + 0.003, ["CoffeeShop_WiFi"],          2),
    make_marker(CENTER_LAT,         CENTER_LON - 0.003, ["linksys", "linksys-5G", "DIRECT-xx-HP Printer"], 4),
    make_marker(CENTER_LAT,         CENTER_LON,         ["ATT-WIFI-1234"],             3),
    make_marker(CENTER_LAT,         CENTER_LON + 0.003, ["TP-LINK_A1B2"],              1),
    make_marker(CENTER_LAT - 0.002, CENTER_LON - 0.003, ["NETGEAR_Ext"],              2),
    make_marker(CENTER_LAT - 0.002, CENTER_LON,         ["Verizon-MiFi-9999"],        1),
    make_marker(CENTER_LAT - 0.002, CENTER_LON + 0.003, ["Hidden SSID"],              4),
    # One marker with gpsValid=False - should be silently skipped
    make_marker(0.0, 0.0, ["Should not appear"], 3, gps_valid=False),
]

# -- Test 2: telemetry/drive-path map ----------------------------------------
# A single SSID tracked along a short path (8 waypoints heading NE).

TELEM_MARKERS = []
ssid = "WarDrive_Target"
for i in range(8):
    offset = i * 0.0008
    bc = [4, 4, 3, 3, 2, 2, 1, 1][i]
    m = make_marker(
        CENTER_LAT + offset,
        CENTER_LON + offset * 0.6,
        ssid if (i == 0 or i == 7) else [],   # label only first & last
        bc
    )
    TELEM_MARKERS.append(m)

# ---------------------------------------------------------------------------
# Generate maps
# ---------------------------------------------------------------------------
SCATTER_FILE = "/tmp/test_osm_scatter.html"
TELEM_FILE   = "/tmp/test_osm_telem.html"

print("Generating scatter map  ->", SCATTER_FILE)
ok1 = MapEngineOSM.createMap(
    SCATTER_FILE,
    "Sparrow OSM Test - Scatter Scan",
    SCATTER_MARKERS,
    connectMarkers=False,
    openWhenDone=False,
    mapType=MapEngineBase.MAP_TYPE_DEFAULT,
)

print("Generating telemetry map ->", TELEM_FILE)
ok2 = MapEngineOSM.createMap(
    TELEM_FILE,
    "Sparrow OSM Test - Telemetry Path",
    TELEM_MARKERS,
    connectMarkers=True,
    openWhenDone=False,
    mapType=MapEngineBase.MAP_TYPE_DEFAULT,
)

# ---------------------------------------------------------------------------
# Report results and open in browser
# ---------------------------------------------------------------------------
status = {True: "OK", False: "FAILED"}
print(f"Scatter map:   {status[ok1]}")
print(f"Telemetry map: {status[ok2]}")

if not ok1 or not ok2:
    print("One or more maps failed to write - check /tmp permissions.")
    sys.exit(1)

print("\nOpening both maps in browser...")
webbrowser.open("file://" + SCATTER_FILE)
webbrowser.open("file://" + TELEM_FILE)

print("\nWhat to verify:")
print("  - Map tiles load (OSM streets visible)")
print("  - Blue dots appear at all 9 scatter locations (none at 0,0)")
print("  - Clicking a dot shows a Bootstrap popover with lat/lon + SSID list")
print("  - Telemetry map shows 8 dots connected by a blue polyline")
print("  - Map fills the full browser window (not a fixed 1024x768 box)")
