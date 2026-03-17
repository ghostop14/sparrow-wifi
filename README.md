# Sparrow-WiFi

A Linux-based wireless situational awareness platform combining WiFi scanning, Bluetooth discovery, RF spectrum analysis, and drone remote identification (RemoteID) in one integrated toolset.

## What's in the Box

Sparrow-WiFi is actually two applications that share a codebase:

| Component | Interface | Purpose |
|-----------|-----------|---------|
| **Sparrow-WiFi** | PyQt5 desktop GUI | WiFi/BT scanning, spectrum analysis, source tracking, wardriving |
| **Sparrow DroneID** | Web-based (browser) | FAA RemoteID drone detection via WiFi and Bluetooth LE |

Both run on Linux and are written entirely in Python 3.

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
- **Elasticsearch** &mdash; ECS 1.5 compliant indexing of WiFi and Bluetooth scan data
- **Falcon plugin** &mdash; Aircrack-ng integration for penetration testing (monitor mode, hidden SSID discovery, deauth, WEP/WPA capture)

### Screenshots

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/sparrow-screenshot.png" width="800"/>
</p>

<p align="center">
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/telemetry-screenshot.png" width="600"/>
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

### Quick Start

```bash
cd sparrow-droneid
pip3 install -r sparrow_droneid/requirements.txt
sudo apt install tcpdump iw bluez
sudo python3 -m sparrow_droneid
```

The web UI is available at `http://localhost:8097`. Configure the monitor interface and GPS in Settings, then click Start.

For full documentation, see [`sparrow-droneid/README.md`](sparrow-droneid/README.md) and the [API reference](sparrow-droneid/sparrow_drone_id_api.md).

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

Install system packages and Python dependencies:

```bash
# Ubuntu 22.04+ / Debian 12+
sudo apt install python3-pip python3-pyqt5 python3-pyqt5.qtchart \
                 gpsd gpsd-clients python3-tk python3-setuptools

pip3 install -r requirements.txt
```

Run:

```bash
sudo ./sparrow-wifi.py
```

#### Virtual Environment (optional)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo ./sparrow-wifi.py
```

### Sparrow DroneID (Web Application)

```bash
cd sparrow-droneid

# Python dependencies
pip3 install -r sparrow_droneid/requirements.txt

# System tools for WiFi monitor mode capture
sudo apt install tcpdump iw iproute2

# System tools for BLE RemoteID capture (optional but recommended)
sudo apt install bluez

# Run
sudo python3 -m sparrow_droneid
```

Open `http://localhost:8097` in a browser.

---

## WiFi Adapter Notes

For basic scanning (`iw scan`), most WiFi adapters work. For monitor mode (required by Sparrow DroneID and the Falcon plugin), adapter and driver support varies:

- **Recommended:** Alfa AWUS036ACH (rtl8812au), Alfa AWUS036AXML (mt7921au)
- **Works well:** Intel AX200/AX210 (iwlwifi) for scanning; monitor mode frame delivery varies by firmware version
- **Test first:** Run `iw phy <phy> info | grep monitor` to verify monitor mode support

For Sparrow DroneID specifically, the adapter must deliver raw 802.11 frames in monitor mode. Some Intel adapters report monitor mode as supported but silently drop frames at the firmware level. The application detects this and warns you.

---

## Bluetooth

Sparrow-WiFi supports several Bluetooth scanning modes:

| Mode | Hardware | What You See |
|------|----------|-------------|
| BLE advertisement scan | Standard BT adapter | LE devices that are actively advertising |
| Promiscuous scan | Ubertooth One | All BLE and Classic BT devices in range |
| iBeacon advertising | Standard BT adapter | Advertise your own iBeacons |
| **RemoteID scan** | Standard BT adapter | **FAA-compliant drone identification (DroneID only)** |

Test your adapter first: `hcitool dev` should list it. For BLE scanning: `bluetoothctl scan on`.

For Ubertooth promiscuous mode, install [Blue Hydra](https://github.com/ZeroChaos-/blue_hydra) into `/opt/bluetooth/blue_hydra`.

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
  <img src="https://github.com/ghostop14/sparrow-wifi/blob/master/spectrum-screenshot.png" width="500"/>
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

## Remote Agent

The headless agent provides all scanning capabilities via a REST API:

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

---

## Falcon / Aircrack-ng Plugin

Advanced wireless penetration testing integration. Provides point-and-click access to:

- Hidden SSID discovery via airodump-ng
- Client station enumeration (connected AP, probed SSIDs)
- Targeted and broadcast deauthentication
- WEP IV capture
- WPA handshake capture with automatic hash extraction (requires JTR `wpapcap2john`)

### Prerequisites

Install aircrack-ng and JTR, ensuring `airmon-ng`, `airodump-ng`, and `wpapcap2john` are in your PATH.

### Disclaimer

***Active penetration testing is subject to legal regulations. It is your responsibility to obtain appropriate authorization before using these tools.***

---

## Elasticsearch Integration

Feed scan data into Elasticsearch with ECS 1.5 compliance:

```bash
# Start the agent first
sudo ./sparrowwifiagent.py

# Bridge to Elasticsearch
python3 sparrow-elastic.py \
    --elasticserver https://user:pass@elastic.example.com:9200 \
    --wifiindex sparrowwifi-site1 \
    --btindex sparrowbt-site1
```

WiFi and Bluetooth indices must be separate (different document schemas). See `sparrow-elastic.py --help` for all options.

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
- Disable the onboard WiFi to enable 5 GHz scanning with USB adapters: add `dtoverlay=disable-wifi` to `/boot/config.txt`
- Install prerequisites: `pip3 install -r requirements.txt`

---

## Project Structure

```
sparrow-wifi/
  sparrow-wifi.py           # Desktop GUI entry point
  sparrowwifiagent.py        # Headless remote agent
  sparrow-elastic.py         # Elasticsearch bridge
  requirements.txt           # Python dependencies (GUI)
  wirelessengine.py          # WiFi scan engine (iw)
  sparrowbluetooth.py        # Bluetooth scan engine
  sparrowhackrf.py           # HackRF spectrum engine
  sparrowmap.py              # Map generation
  plugins/                   # Falcon and other plugins
  sparrow-droneid/           # DroneID web application
    sparrow_droneid/
      __main__.py            # Entry point
      requirements.txt       # Python dependencies (DroneID)
      backend/               # API server, capture engine, database
      frontend/              # HTML, JS, CSS (served by backend)
    sparrow_drone_id_api.md  # REST API reference
```

---

## License

This project is licensed under the terms included in the repository. See the LICENSE file for details.
