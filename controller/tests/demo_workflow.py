"""Simple script that registers mock agents with the controller and runs sample scans."""
from __future__ import annotations

import argparse
import time
from typing import List

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controller demo workflow")
    parser.add_argument("--controller", default="http://localhost:8000", help="Controller base URL")
    parser.add_argument("--agent", action="append", nargs=2, metavar=("NAME", "URL"),
                        help="Agent name and base URL (can be passed multiple times)")
    return parser.parse_args()


def main():
    args = parse_args()
    agents = args.agent or [
        ("mock-east", "http://localhost:9001"),
        ("mock-west", "http://localhost:9002"),
    ]

    with httpx.Client(base_url=args.controller) as client:
        agent_ids = register_agents(client, agents)
        for name, agent_id in agent_ids.items():
            print(f"[+] Registered {name} as agent #{agent_id}")

        # Put wlan0 into monitor mode for each agent
        for name, agent_id in agent_ids.items():
            resp = client.post(f"/api/falcon/{agent_id}/monitor/start", json={"interface": "wlan0"})
            print(f"[+] Monitor mode start {name}: {resp.json()}")

        # Kick off Wi-Fi scans
        for name, agent_id in agent_ids.items():
            launch_scan(client, agent_id, "wifi", interface="wlan0", channels=[1, 6, 11])

        # Kick off Falcon scans (monitor interface assumed to be wlan0mon)
        for name, agent_id in agent_ids.items():
            launch_scan(client, agent_id, "falcon", interface="wlan0mon")

        time.sleep(2)
        scans = client.get("/api/scans?limit=10").json()
        print("[+] Recent scan jobs:")
        for scan in scans:
            summary = scan.get('response_payload') or scan.get('error')
            print(f"  - #{scan['id']} agent {scan['agent_id']} type {scan['scan_type']} status {scan['status']} -> {summary}")


def register_agents(client: httpx.Client, agents: List[tuple[str, str]]) -> dict[str, int]:
    agent_ids: dict[str, int] = {}
    for name, url in agents:
        payload = {"name": name, "base_url": url, "capabilities": ["wifi", "falcon", "bluetooth"]}
        resp = client.post("/api/agents", json=payload)
        if resp.status_code == 201:
            agent_ids[name] = resp.json()["id"]
        elif resp.status_code == 400:
            # Already exists; fetch list and reuse id
            agents_resp = client.get("/api/agents").json()
            for existing in agents_resp:
                if existing["name"] == name:
                    agent_ids[name] = existing["id"]
                    break
        else:
            resp.raise_for_status()
    return agent_ids


def launch_scan(client: httpx.Client, agent_id: int, scan_type: str, *, interface: str, channels=None):
    payload = {
        "agent_id": agent_id,
        "scan_type": scan_type,
        "interface": interface,
        "channels": channels,
    }
    resp = client.post("/api/scans", json=payload)
    resp.raise_for_status()
    scan = resp.json()
    print(f"[+] launched {scan_type} scan #{scan['id']} on agent {agent_id} ({interface})")


if __name__ == "__main__":
    main()
