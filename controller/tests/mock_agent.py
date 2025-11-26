"""Minimal FastAPI app that emulates a sparrowwifiagent for testing the controller UI."""
from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import gzip
import json

from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(title="Mock Sparrow WiFi Agent")


@dataclass
class AgentState:
    name: str
    location: Tuple[float, float]
    interfaces: List[str] = field(default_factory=lambda: ["wlan0", "wlan1"])
    monitor_interfaces: Dict[str, str] = field(default_factory=dict)
    falcon_running: Dict[str, bool] = field(default_factory=dict)
    last_scan_snapshot: Dict[str, List[Dict]] = field(default_factory=dict)
    bluetooth_devices: List[Dict] = field(default_factory=list)
    hackrf_band: str | None = None
    hackrf_running: bool = False

    def networks(self) -> List[Dict]:
        lat, lon = self.location
        results = []
        for idx in range(6):
            nlat = lat + random.uniform(-0.002, 0.002)
            nlon = lon + random.uniform(-0.002, 0.002)
            results.append(
                {
                    "type": "wifi-ap",
                    "macAddr": f"00:11:22:33:{idx:02x}:{random.randint(0, 255):02x}",
                    "ssid": f"{self.name}-AP-{idx}",
                    "mode": "AP",
                    "security": "WPA2",
                    "privacy": "CCMP",
                    "cipher": "CCMP",
                    "frequency": 2412 + 5 * idx,
                    "channel": idx + 1,
                    "secondaryChannel": 0,
                    "secondaryChannelLocation": "",
                    "thirdChannel": 0,
                    "signal": -30 - idx * 5,
                    "stationcount": random.randint(1, 15),
                    "utilization": 0.1,
                    "strongestsignal": -25,
                    "bandwidth": 20,
                    "firstseen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "lastseen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": str(nlat),
                    "lon": str(nlon),
                    "alt": "0",
                    "speed": "0",
                    "gpsvalid": "True",
                    "strongestlat": str(nlat),
                    "strongestlon": str(nlon),
                    "strongestalt": "0",
                    "strongestspeed": "0",
                    "strongestgpsvalid": "True",
                }
            )
        self.last_scan_snapshot["networks"] = results
        return results

    def clients(self) -> List[Dict]:
        lat, lon = self.location
        clients = []
        for idx in range(3):
            nlat = lat + random.uniform(-0.001, 0.001)
            nlon = lon + random.uniform(-0.001, 0.001)
            clients.append(
                {
                    "macAddr": f"aa:bb:cc:dd:ee:{idx:02x}",
                    "apMacAddr": self.last_scan_snapshot.get("networks", [{}])[0].get("macAddr", ""),
                    "channel": idx + 1,
                    "power": -40 - idx * 4,
                    "firstseen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "lastseen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": str(nlat),
                    "lon": str(nlon),
                    "alt": "0",
                    "speed": "0",
                    "gpsvalid": "True",
                }
            )
        return clients


state: AgentState = AgentState(name="MockAgent", location=(0.0, 0.0))


class BluetoothDevice(BaseModel):
    mac: str
    name: str
    rssi: int


def gps_dict(location: Tuple[float, float]) -> Dict:
    return {
        "latitude": location[0],
        "longitude": location[1],
        "altitude": 0,
        "speed": 0,
    }


def gps_status_dict(location: Tuple[float, float]) -> Dict:
    lat, lon = location
    return {
        "gpsinstalled": "True",
        "gpsrunning": "True",
        "gpssynch": "True",
        "gpspos": {
            "latitude": lat,
            "longitude": lon,
            "altitude": 0,
            "speed": 0,
        },
    }


@app.get("/wireless/interfaces")
def get_interfaces():
    interfaces = {iface: {"mac": f"de:ad:be:ef:{idx:02x}:00"} for idx, iface in enumerate(state.interfaces)}
    return {"interfaces": interfaces}


@app.get("/wireless/networks/{interface}")
def wireless_networks(interface: str):
    networks = state.networks()
    return {"errCode": 0, "errString": "", "networks": networks, "gps": gps_dict(state.location)}


@app.get("/gps/status")
def gps_status():
    return gps_status_dict(state.location)


@app.get("/falcon/startmonmode/{interface}")
def falcon_start_monitor(interface: str):
    state.monitor_interfaces[interface] = f"{interface}mon"
    return {"errcode": 0, "errmsg": ""}


@app.get("/falcon/stopmonmode/{interface}")
def falcon_stop_monitor(interface: str):
    state.monitor_interfaces.pop(interface, None)
    return {"errcode": 0, "errmsg": ""}


@app.get("/falcon/startscan/{interface}")
def falcon_start_scan(interface: str):
    state.falcon_running[interface] = True
    state.last_scan_snapshot["networks"] = state.networks()
    return {"errcode": 0, "errmsg": ""}


@app.get("/falcon/stopscan/{interface}")
def falcon_stop_scan(interface: str):
    state.falcon_running[interface] = False
    return {"errcode": 0, "errmsg": ""}


@app.get("/falcon/scanrunning/{interface}")
def falcon_scanrunning(interface: str):
    running = state.falcon_running.get(interface, False)
    if running:
        return {"errcode": 0, "errmsg": f"scan for {interface} is running"}
    return {"errcode": 1, "errmsg": f"scan for {interface} is not running"}


@app.get("/falcon/getscanresults")
def falcon_get_scan_results():
    networks = state.last_scan_snapshot.get("networks") or state.networks()
    clients = state.clients()
    return {"errCode": 0, "errString": "", "networks": networks, "clients": clients, "gps": gps_dict(state.location)}


@app.get("/bluetooth/discoverystarta")
def bluetooth_discover_active():
    state.bluetooth_devices = generate_bt_devices()
    return {"errcode": 0, "errmsg": ""}


@app.get("/bluetooth/discoverystartp")
def bluetooth_discover_passive():
    state.bluetooth_devices = generate_bt_devices()
    return {"errcode": 0, "errmsg": ""}


@app.get("/bluetooth/discoverystop")
def bluetooth_stop():
    return {"errcode": 0, "errmsg": ""}


@app.get("/bluetooth/discoveryclear")
def bluetooth_clear():
    state.bluetooth_devices = []
    return {"errcode": 0, "errmsg": ""}


@app.get("/bluetooth/discoverystatus")
def bluetooth_status():
    return {"errcode": 0, "errmsg": "", "devices": state.bluetooth_devices}


@app.get("/bluetooth/running")
def bluetooth_running():
    return {
        "errcode": 0,
        "errmsg": "",
        "hasbluetooth": True,
        "spectrumscanrunning": False,
        "discoveryscanrunning": True,
        "beaconrunning": False,
    }


def generate_bt_devices() -> List[Dict]:
    devices = []
    for idx in range(4):
        lat_offset = random.uniform(-0.0008, 0.0008)
        lon_offset = random.uniform(-0.0008, 0.0008)
        lat = state.location[0] + lat_offset
        lon = state.location[1] + lon_offset
        devices.append(
            {
                "mac": f"cc:dd:ee:ff:{idx:02x}:01",
                "name": f"Mock Beacon {idx}",
                "rssi": -40 - idx * 3,
                "lat": str(lat),
                "lon": str(lon),
                "gpsvalid": "True",
            }
        )
    return devices


@app.get("/spectrum/hackrfstatus")
def hackrf_status():
    return {
        "errcode": 0,
        "errmsg": "",
        "hashackrf": True,
        "scan24running": state.hackrf_running and state.hackrf_band == '24',
        "scan5running": state.hackrf_running and state.hackrf_band == '5',
    }


@app.get("/spectrum/scan/{action}")
def hackrf_scan(action: str):
    if action == 'start24':
        state.hackrf_band = '24'
        state.hackrf_running = True
        return {"errcode": 0, "errmsg": ""}
    if action == 'start5':
        state.hackrf_band = '5'
        state.hackrf_running = True
        return {"errcode": 0, "errmsg": ""}
    if action == 'stop':
        state.hackrf_running = False
        state.hackrf_band = None
        return {"errcode": 0, "errmsg": ""}
    if action == 'status':
        band = state.hackrf_band or '24'
        data = fake_channel_data(band)
        payload = {
            "scanrunning": state.hackrf_running,
            "channeldata": data,
        }
        json_bytes = json.dumps(payload).encode('utf-8')
        compressed = gzip.compress(json_bytes)
        return Response(content=compressed, media_type='application/json', headers={"Content-Encoding": "gzip"})
    return {"errcode": 1, "errmsg": "Unknown action"}

# Aliases to match real agent endpoints expected by the controller
@app.get("/spectrum/scanstart24")
def hackrf_scan_start24():
    return hackrf_scan("start24")


@app.get("/spectrum/scanstart5")
def hackrf_scan_start5():
    return hackrf_scan("start5")


@app.get("/spectrum/scanstop")
def hackrf_scan_stop():
    return hackrf_scan("stop")


@app.get("/spectrum/scanstatus")
def hackrf_scan_status():
    return hackrf_scan("status")


def fake_channel_data(band: str) -> Dict[int, float]:
    if band == '5':
        channels = range(36, 64, 2)
    else:
        channels = range(1, 15)
    data = {}
    for ch in channels:
        baseline = -90 + 5 * random.random()
        if random.random() < 0.2:
            baseline += random.uniform(10, 25)
        data[ch] = round(baseline, 1)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mock Sparrow WiFi agent")
    parser.add_argument("--name", default="MockAgent", help="Display name for generated SSIDs")
    parser.add_argument("--port", type=int, default=9001, help="Port to listen on")
    parser.add_argument("--lat", type=float, default=37.7749, help="Base latitude")
    parser.add_argument("--lon", type=float, default=-122.4194, help="Base longitude")
    return parser.parse_args()


def main():
    global state
    args = parse_args()
    state = AgentState(name=args.name, location=(args.lat, args.lon))
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=args.port, reload=False)


if __name__ == "__main__":
    main()
