# Sparrow-WiFi

Sparrow-WiFi is a 2.4 GHz and 5 GHz WiFi and Bluetooth spectral awareness tool for Linux. It integrates WiFi scanning, Bluetooth Low Energy and Classic discovery, software-defined radio spectrum analysis (HackRF, Ubertooth), GPS tracking, FAA RemoteID drone detection, and drone/rover-mounted remote operations into a single platform. Written entirely in Python 3.

The project includes two applications and a headless agent, all exposing JSON-based REST APIs for integration with external tools and automation:

| Component | Interface | Purpose |
|-----------|-----------|---------|
| **Sparrow-WiFi** | PyQt5 desktop GUI | WiFi/BT scanning, spectrum analysis, source tracking, wardriving |
| **Sparrow Agent** | Headless HTTP server | Remote scanning, drone/rover deployments, third-party integration |
| **Sparrow DroneID** | Web-based (browser) | FAA RemoteID drone detection via WiFi and Bluetooth LE |

Both the Sparrow Agent and Sparrow DroneID expose REST APIs that allow other applications to query scan results, trigger scans, retrieve drone detections, and integrate wireless/drone awareness into their own workflows.

---

## What's New (April 2026)

This release covers three significant improvements over the prior version:

- **Sparrow Agent: single-flight WiFi scan coalescing.** When multiple HTTP clients (the DroneID app, the Elasticsearch bridge, the GUI) hit `/wireless/networks/<iface>` simultaneously, the agent previously kicked off N redundant `iw scan` calls that serialized on the per-interface lock, multiplying scan latency by the number of clients. The first request is now the "leader" that actually scans; concurrent requests wait on a `threading.Event` and share the leader's result. Also includes lock-creation TOCTOU fix and exception-safety on per-interface locks.
- **Sparrow DroneID** &mdash; new web-based application for FAA RemoteID detection via WiFi (ASTM F3411 NAN, beacon vendor IEs, DJI proprietary) and Bluetooth LE (BT4/BT5 Legacy advertising). Includes geozone overlays, multi-state alerts with Slack integration, KML export, Cursor-on-Target output, and a multi-device responsive web UI. See the [Sparrow DroneID](#sparrow-droneid-web-application) section below.
- **Modernized Elasticsearch / OpenSearch bridge** &mdash; the `sparrow-elastic.py` bridge has been rewritten to produce **ECS 8.17** documents (was ECS 1.5), now supports both **Elasticsearch 8.x and OpenSearch 2.x**, bootstraps composable index templates with ILM/ISM lifecycle policies and rollover write aliases automatically, performs OUI vendor enrichment and rule-based device classification (with optional Fingerbank fingerprinting), and ships four bundled Kibana dashboards plus six legacy-preserved visualizations. The legacy ECS 1.5 bridge is preserved at `legacy/sparrow-elastic.py`. See [Elasticsearch / OpenSearch Integration](#elasticsearch--opensearch-integration).

---

## Sparrow-WiFi (Desktop GUI)

The original Sparrow application provides a comprehensive GUI-based replacement for tools like inSSIDer and LinSSID, with capabilities well beyond basic scanning:

- **WiFi scanning** &mdash; 2.4 GHz and 5 GHz SSID discovery, signal strength, channel utilization
- **Source tracking** &mdash; Hunt mode with high sample rates and telemetry windows for locating WiFi and Bluetooth sources
- **Spectrum analysis** &mdash; Real-time 2.4/5 GHz spectral overlays via Ubertooth One or HackRF One
- **Bluetooth** &mdash; BLE advertisement scanning, iBeacon detection/advertising, Ubertooth promiscuous mode for classic + LE
- **Remote agent** &mdash; Headless agent (`sparrowwifiagent.py`) for distributed scanning, drone/rover-mounted operations, and Raspberry Pi deployments
- **GPS integration** &mdash; gpsd, static coordinates, or MAVLink (drone GPS)
- **Mapping** &mdash; Google Maps / OpenStreetMap visualization of scan results with GPS tracks
- **Import/Export** &mdash; CSV, JSON, and raw `iw scan` output
- **Elasticsearch / OpenSearch** &mdash; ECS 8.17 compliant indexing of WiFi and Bluetooth scan data with ILM/ISM lifecycle management, device classification, optional Fingerbank fingerprinting, and bundled Kibana dashboards
- **Falcon plugin** &mdash; Aircrack-ng integration for penetration testing (monitor mode, hidden SSID discovery, deauth, WEP/WPA capture)

### Screenshots

<p align="center">
  <img src="./sparrow-screenshot.png" width="800"/>
</p>

<p align="center">
  <img src="./telemetry-screenshot.png" width="600"/>
</p>

---

## Sparrow DroneID (Web Application)

A standalone web-based drone detection and tracking system that decodes FAA-mandated Remote Identification (RemoteID) broadcasts. Runs as a Python HTTP server with a browser-based UI accessible from any device on the network.

### Capabilities

- **WiFi capture** &mdash; Decodes ASTM F3411 NAN action frames, beacon vendor IEs, and DJI proprietary DroneID
- **Bluetooth LE capture** &mdash; Decodes ASTM F3411 BT4/BT5 Legacy advertising (UUID 0xFFFA)
- **Real-time map** &mdash; Leaflet-based map with quadcopter icons, heading indicators, operator position markers, and drone-to-operator lines
- **At-a-glance labels** &mdash; Operator ID and altitude AGL displayed under each drone icon on the map
- **Detail popups** &mdash; Click a drone for serial, registration ID, operator ID, type, speed, heading, altitude, bearing/range from receiver, BVLOS status
- **Alert system** &mdash; Configurable alerts for new drones, altitude violations, speed violations, and signal loss with audio tones, visual toasts, and Slack webhook notifications
- **Alert acknowledgment** &mdash; Three-state workflow (Active/Acknowledged/Resolved) with operator identity, shared across all connected devices
- **Airport geozones** &mdash; Automatic download and display of nearby airports (OurAirports data) and FAA Prohibited/Restricted airspace polygons, cached locally for offline operation
- **GPS** &mdash; gpsd integration or configurable static coordinates
- **History & replay** &mdash; SQLite-backed detection history with timeline replay and KML export
- **Cursor-on-Target (CoT)** &mdash; Multicast CoT output for SA integration
- **Multi-device** &mdash; Web UI works on desktop, tablet, and phone simultaneously
- **Metric / Imperial** &mdash; Full unit preference support throughout the UI and alerts

Web UI runs at `http://localhost:8097` once started. See [Installation](#sparrow-droneid-web-application-1) below for setup, and the [API reference](sparrow-droneid/sparrow_drone_id_api.md) for programmatic access.

---

## System Requirements

| Requirement | Sparrow-WiFi (GUI) | Sparrow DroneID (Web) |
|-------------|-------------------|----------------------|
| **OS** | Ubuntu 20.04+, Kali 2020.3+, Debian 11+ | Ubuntu 20.04+, Kali, Debian 11+, Raspberry Pi OS |
| **Python** | 3.8+ | 3.8+ |
| **Root** | Required (iw scan) | Required (monitor mode, BLE) |
| **WiFi adapter** | Any with `iw` support | Monitor-mode capable (e.g., rtl8812au, Intel AX200) |
| **Bluetooth** | Optional (hci adapter, Ubertooth) | Optional (any BLE-capable adapter for RemoteID) |
| **GPS** | Optional (gpsd) | Optional (gpsd or static coordinates) |
| **Display** | X11/Wayland desktop | Headless OK (web browser on any device) |

---

## Installation

### Sparrow-WiFi (Desktop GUI)

```bash
git clone https://github.com/ghostop14/sparrow-wifi
cd sparrow-wifi
```

System packages (Ubuntu 22.04+ / Debian 12+ / Kali rolling):

```bash
sudo apt install python3-pip python3-pyqt5 python3-pyqt5.qtchart \
                 gpsd gpsd-clients python3-tk python3-setuptools
```

> **Kali users:** PyQt5, PyQtChart, and aircrack-ng (for the Falcon plugin) are typically pre-installed. You'll mostly just need `gpsd`, `gpsd-clients`, and the Python deps below.

Python dependencies &mdash; choose either approach:

**Option A: System-wide install with `--break-system-packages`** &mdash; simplest, fits how the GUI/agent get launched (root-owned scripts):

```bash
# Modern systems (Ubuntu 24.04+, Kali rolling 2023+, Debian 12+) require this
# flag because Python is marked externally-managed (PEP 668). Sparrow runs as
# root anyway, so system-wide install is consistent with how it executes.
sudo pip3 install --break-system-packages -r requirements.txt
```

**Option B: Virtual environment** &mdash; isolated, no system pip warnings, preferred by some operators:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Run with sudo using the venv interpreter:
sudo venv/bin/python3 ./sparrow-wifi.py
```

Either way, run:

```bash
sudo ./sparrow-wifi.py
```

### Sparrow DroneID (Web Application)

```bash
cd sparrow-droneid

# System tools — tcpdump for WiFi monitor mode, bluez for BLE RemoteID
sudo apt install tcpdump bluez

# Python dependencies — pick one of:
sudo pip3 install --break-system-packages -r sparrow_droneid/requirements.txt
# or:
python3 -m venv venv && source venv/bin/activate && pip install -r sparrow_droneid/requirements.txt

# Run (either entry point works):
sudo python3 sparrow_droneid/app.py
# or: sudo python3 -m sparrow_droneid
```

Open `http://localhost:8097` in a browser. Configure the monitor interface and GPS in Settings, then click Start.

### Elasticsearch / OpenSearch Bridge (optional)

```bash
sudo pip3 install --break-system-packages -r requirements-elastic.txt
# or via venv as above
```

See [Elasticsearch / OpenSearch Integration](#elasticsearch--opensearch-integration) below.

---

## WiFi Adapter Notes

Most WiFi adapters work for basic scanning. Sparrow-WiFi supports multiple interface enumeration backends (`iw`, `iwconfig`, `nmcli`) so it works on systems that may not have `iw` installed (e.g., RHEL/Fedora with NetworkManager only).

For monitor mode (required by Sparrow DroneID and the Falcon plugin), adapter and driver support varies:

- **Recommended:** Alfa AWUS036ACH (rtl8812au), Alfa AWUS036AXML (mt7921au)
- **Works well:** Intel AX200/AX210 (iwlwifi) for scanning; monitor mode frame delivery varies by firmware version
- **Test first:** `iw phy <phy> info | grep monitor` or `iwconfig <iface>` to verify capabilities

For Sparrow DroneID specifically, the adapter must deliver raw 802.11 frames in monitor mode. Some Intel adapters report monitor mode as supported but silently drop frames at the firmware level. The application detects this and warns you.

---

## Bluetooth

Sparrow-WiFi supports several Bluetooth scanning modes:

| Mode | Hardware | What You See |
|------|----------|-------------|
| BLE advertisement scan | Standard BT adapter | LE devices that are actively advertising |
| Promiscuous scan | Ubertooth One + Blue Hydra | All BLE and Classic BT devices in range |
| iBeacon advertising | Standard BT adapter | Advertise your own iBeacons |
| **RemoteID scan** | Standard BT adapter | **FAA-compliant drone identification (DroneID only)** |

A standard built-in or USB Bluetooth adapter is sufficient for BLE advertisement scanning and RemoteID drone detection. Test your adapter with `bluetoothctl scan on`.

For full promiscuous discovery of both Classic and BLE devices, you'll need an [Ubertooth One](https://greatscottgadgets.com/ubertoothone/) and [Blue Hydra](https://github.com/ZeroChaos-/blue_hydra) installed into `/opt/bluetooth/blue_hydra`. This is optional and not required for basic BLE or RemoteID scanning.

---

## Spectrum Analysis

Real-time spectral overlays on top of WiFi channel views:

### Ubertooth One
- 2.4 GHz only, 1 MHz resolution
- Test with: `ubertooth-specan-ui`

### HackRF One
- 2.4 GHz (0.5 MHz resolution) and 5 GHz (2 MHz resolution)
- One band at a time; combine with Ubertooth for simultaneous dual-band
- Use an appropriate dual-band antenna (standard HackRF antenna is rated to 1 GHz only)
- Note: RP-SMA to SMA adapter needed for most WiFi antennas
- Test with: `hackrf_sweep`

<p align="center">
  <img src="./spectrum-screenshot.png" width="500"/>
</p>

---

## GPS

Both applications use gpsd for GPS. Quick setup:

```bash
# Install
sudo apt install gpsd gpsd-clients

# Test with a USB GPS receiver
sudo gpsd -D 2 -N /dev/ttyUSB0

# Verify
xgps    # or: cgps -s
```

For production, configure `/etc/default/gpsd` with your device path and restart the service.

Sparrow DroneID also supports static coordinates (configured in Settings) for fixed-site installations without a GPS receiver.

---

## Remote Agent and API Integration

The Sparrow agent (`sparrowwifiagent.py`) is a headless HTTP server that exposes all of Sparrow's WiFi and Bluetooth scanning capabilities as a JSON-based REST API. This is how the Sparrow-WiFi GUI communicates with remote sensors, but the API is open for any application to use.

**Use cases:**
- Deploy on a Raspberry Pi, drone, or rover for remote/mobile scanning
- Integrate WiFi and Bluetooth situational awareness into your own applications
- Feed scan data into SIEM, dashboards, or alerting pipelines
- Automate scanning with scripts (trigger scans, pull results via curl/Python/etc.)

Sparrow DroneID has its own REST API as well ([API reference](sparrow-droneid/sparrow_drone_id_api.md)), providing programmatic access to drone detections, alert management, geozones, and system configuration.

### Running the Agent

```bash
sudo ./sparrowwifiagent.py
```

Listens on port 8020 by default. Key options:

| Flag | Purpose |
|------|---------|
| `--port PORT` | HTTP listen port |
| `--allowedips IP1,IP2` | Restrict client connections |
| `--staticcoord LAT,LON,ALT` | Use fixed GPS coordinates |
| `--mavlinkgps 3dr` | Pull GPS from Solo 3DR drone |
| `--recordinterface IFACE` | Auto-record on startup (headless) |
| `--userpileds` | Use Raspberry Pi LEDs for status |
| `--sendannounce` | UDP broadcast for agent discovery |

See `--help` for the full list.

### Example API calls

```bash
# List wireless interfaces the agent can scan
curl http://sensor:8020/wireless/interfaces

# Trigger a WiFi scan and pull results (multiple concurrent callers
# get coalesced into a single iw scan; first caller is the leader)
curl http://sensor:8020/wireless/networks/wlan0

# Filter to specific frequencies
curl "http://sensor:8020/wireless/networks/wlan0?frequencies=2412,2437,2462"

# Query GPS status
curl http://sensor:8020/gps/status

# Start a Bluetooth Low Energy advertisement scan
curl http://sensor:8020/bluetooth/discoverystarta

# Pull current BT discovery results
curl http://sensor:8020/bluetooth/discoverystatus
```

For Sparrow DroneID, see the dedicated [API reference](sparrow-droneid/sparrow_drone_id_api.md).

> **Production note:** the agent listens on all interfaces by default. For deployments outside a trusted network, use `--allowedips` to restrict callers, run behind a reverse proxy with TLS, or bind to a private interface only.

---

## Falcon / Aircrack-ng Plugin

Advanced wireless penetration testing integration. Provides point-and-click access to:

- Hidden SSID discovery via airodump-ng
- Client station enumeration (connected AP, probed SSIDs)
- Targeted and broadcast deauthentication
- WEP IV capture
- WPA handshake capture with automatic hash extraction (requires JTR `wpapcap2john`)

### Prerequisites

```bash
# Kali users: aircrack-ng + JTR are usually pre-installed.
# Ubuntu / Debian / Raspberry Pi OS:
sudo apt install aircrack-ng john
```

Verify `airmon-ng`, `airodump-ng`, and `wpapcap2john` are on your PATH after install.

### Disclaimer

***Active penetration testing is subject to legal regulations. It is your responsibility to obtain appropriate authorization before using these tools.***

---

## Elasticsearch / OpenSearch Integration

The `sparrow_elastic` package provides an **ECS 8.17** bridge that polls the Sparrow WiFi agent and bulk-indexes WiFi and Bluetooth observations into Elasticsearch 8.x or OpenSearch 2.x. It bootstraps composable index templates, ILM/ISM lifecycle policies, and rollover write aliases automatically; performs OUI vendor enrichment and rule-based device classification (with optional Fingerbank fingerprinting); and ships pre-built Kibana dashboards.

### Quickstart

```bash
# 1. Start the remote agent (it provides scan data via HTTP)
sudo ./sparrowwifiagent.py

# 2. Install bridge dependencies
sudo pip3 install --break-system-packages -r requirements-elastic.txt

# 3. Run the bridge
./sparrow-elastic.py --elasticserver http://user:pass@host:9200 --wifiinterface wlan1

# 4. (Optional) Import the bundled Kibana dashboards
python3 install_dashboards.py --kibana-url http://kibana:5601 \
    --username elastic --password '<password>'
```

> **Credential hygiene:** embedding `user:pass@` in `--elasticserver` is convenient but the URL becomes visible in `ps`, `journalctl`, and shell history. For production, use `--username`/`--password` flags, environment variables (`SPARROW_ES_USERNAME`, `SPARROW_ES_PASSWORD`), or the `EnvironmentFile=` pattern in the included systemd unit example.

### What the bridge ships with

- **5 Kibana dashboards** &mdash; Situational Awareness, Pattern of Life, New Device Detection, Spectrum Planning (with SSID × Channel signal-strength heatmap), and Bluetooth Situational Awareness (with truly-new-device Vega panel and estimated-range proximity table)
- **6 legacy-preserved visualizations** &mdash; field-renamed clones of the original `Sparrow*` viz so old muscle memory keeps working
- **Device classifier** &mdash; 64-rule seed table covering drone controllers (DJI/Autel/Skydio/Parrot/Yuneec), BT Class of Device, GAP Appearance, Apple Continuity subtypes, and OUI vendor heuristics
- **Reference-data refresh** &mdash; bundled Wireshark `manuf`, BT SIG company IDs, service UUIDs, GAP appearance values, and Apple Continuity subtype tables, with a 30/90-day self-refresh background thread
- **Pre-flight compatibility check** &mdash; refuses to write into legacy ECS 1.5 indices and prints clear remediation steps instead of silently corrupting data

For full operator documentation (engine selection, auth modes, dashboard import, reference data, complete CLI reference) see [sparrow_elastic/README.md](sparrow_elastic/README.md).

Sample configuration files are in the repo root and `init.d_scripts/`:

- `sparrow-elastic.conf.example` &mdash; INI-style config with all supported keys
- `sparrow-elastic.env.example` &mdash; shell-format env file for systemd deployments
- `init.d_scripts/sparrow-elastic.service.example` &mdash; systemd unit template

### Migration from the legacy ECS 1.5 bridge

The pre-2026 bridge wrote ECS 1.5 documents into operator-named indices via `--wifiindex` / `--btindex`. The new bridge writes ECS 8.17 documents into rollover-managed write aliases (default `sparrow-wifi` / `sparrow-bt`).

**The legacy script is preserved at `legacy/sparrow-elastic.py`** alongside its `.txt` template and ILM policy files. Running it still requires the legacy environment (manual template + ILM setup).

**Flag changes (with backwards compatibility):**

| Legacy flag         | New flag           | Notes                                                  |
|---------------------|--------------------|--------------------------------------------------------|
| `--wifiindex NAME`  | `--wifi-alias NAME`| Legacy spelling still accepted as a deprecated alias.  |
| `--btindex NAME`    | `--bt-alias NAME`  | Legacy spelling still accepted as a deprecated alias.  |
| `--dont-create-indices` | unchanged       | Skips bootstrap.                                       |
| `--elasticserver`, `--sparrowagent`, `--sparrowport`, `--wifiinterface`, `--scandelay` | unchanged | |

A legacy invocation like:

```bash
./sparrow-elastic.py --elasticserver=http://user:pass@host:9200 \
                     --wifiinterface=wlan1 \
                     --wifiindex=sparrowwifi-home \
                     --btindex=sparrowbt-home
```

still parses and runs &mdash; but the bridge now refuses to write into a pre-existing index whose mapping doesn't carry the ECS 8.17 schema marker, exiting with three remediation options (use a different alias, wipe and re-bootstrap, or run the legacy bridge). For a clean install, just drop `--wifiindex` / `--btindex` and accept the new defaults.

---

## Drone / Rover Operations

The remote agent can be deployed on a Raspberry Pi mounted on a drone or rover for mobile wireless surveying. Tested on a Solo 3DR drone with GPS integration via MAVLink.

### Autonomous Recording

```bash
# On the Pi: auto-start, pull drone GPS, record to local files
sudo python3 ./sparrowwifiagent.py --userpileds --sendannounce --mavlinkgps 3dr --recordinterface wlan0
```

LED indicators (Raspberry Pi):
1. Both off &mdash; Initializing
2. Red heartbeat &mdash; GPS present, not synchronized
3. Red solid &mdash; GPS synchronized
4. Green solid &mdash; Agent ready, serving requests

Recordings can be retrieved via the Sparrow-WiFi GUI's agent management interface.

### Pi Setup Notes

- Use Raspberry Pi OS (Bookworm or later) with Python 3.8+
- Disable the onboard WiFi to enable 5 GHz scanning with USB adapters: add `dtoverlay=disable-wifi` to `/boot/firmware/config.txt` on Bookworm and later, or `/boot/config.txt` on older releases
- Install prerequisites: `sudo pip3 install --break-system-packages -r requirements.txt` (or use a venv as in the [Installation](#installation) section)

---

## Project Structure

```
sparrow-wifi/
  sparrow-wifi.py            # Desktop GUI entry point
  sparrowwifiagent.py        # Headless remote agent
  sparrow-elastic.py         # Elasticsearch / OpenSearch bridge (ECS 8.17)
  install_dashboards.py      # One-shot Kibana dashboard installer
  requirements.txt           # Python dependencies (GUI)
  requirements-elastic.txt   # Python dependencies (Elasticsearch bridge)
  wirelessengine.py          # WiFi scan engine (iw)
  sparrowbluetooth.py        # Bluetooth scan engine
  sparrowhackrf.py           # HackRF spectrum engine
  sparrowmap.py              # Map generation
  plugins/                   # Falcon and other plugins
  sparrow_elastic/           # ES/OS bridge package
    *.py                     # Client abstraction, document builder, classifier...
    templates/               # Composable index templates (ES + OS variants)
    policies/                # ILM (ES) and ISM (OS) lifecycle policy JSON
    dashboards/              # Kibana NDJSON: 5 dashboards + legacy-preserved
    data/                    # Bundled reference data (manuf, BT SIG, classifier rules)
    README.md                # Full bridge operator documentation
  legacy/                    # Pre-2026 ECS 1.5 bridge, frozen for reference
    sparrow-elastic.py       # Legacy bridge (still runnable)
    sparrow_elastic_*.txt    # Legacy index templates and ILM policy
  sparrow-droneid/           # DroneID web application
    sparrow_droneid/
      app.py                 # Entry point (sudo python3 app.py)
      __main__.py            # Allows: sudo python3 -m sparrow_droneid
      requirements.txt       # Python dependencies (DroneID)
      backend/               # API server, capture engine, database
      frontend/              # HTML, JS, CSS (served by backend)
    sparrow_drone_id_api.md  # REST API reference
```

---

## License

This project is licensed under the terms included in the repository. See the LICENSE file for details.
