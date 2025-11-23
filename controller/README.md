# Sparrow Multi-Agent Controller (Proof of Concept)

This directory contains an HTTP controller that aggregates multiple `sparrowwifiagent` instances, proxies scan commands, and stores their responses in SQLite.  The intent is to keep all remote agents untouched and provide a single place to trigger Wi-Fi/Falcon/Bluetooth scans and visualize their results via a simple web UI.

## Features
- Register remote agents (hostname/IP, port, descriptive metadata, reported capabilities)
- Trigger scans (basic Wi-Fi, Falcon advanced, Bluetooth discovery) against one or more agents
- Persist raw responses in SQLite for table/map rendering and historical queries
- Provide REST endpoints plus a WebSocket stream for the JavaScript front end
- Leaflet-based map that aggregates Wi-Fi/Falcon observations (plus Bluetooth devices) from every agent in real time
- Falcon monitor-mode controls (start/stop monitor mode, start/stop scans, status)
- Optional continuous scans per agent/interface with controller-managed scheduling
- Bluetooth results tab with live map markers plus per-agent Falcon panels for networks/clients and inline actions (deauth/capture)
- Leave hooks that can later forward the aggregated data into Elastic

## Running the controller
```bash
cd controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The service stores its SQLite database in `controller/state/controller.db` by default.  Set `CONTROLLER_DB_URL` to override (e.g., point to PostgreSQL) and `CONTROLLER_ELASTIC_URL` once you are ready to forward events to Elastic.

## Development notes
- The API is documented through the built-in FastAPI schema.  Once uvicorn is running, browse to `http://localhost:8000/docs`.
- The placeholder UI lives in `controller/frontend` and is served statically from `/`.
- Future exporter hooks can subscribe to the internal event bus located in `app/events.py`.

## Falcon controls
- Use the **Falcon Monitor & Scan Control** section on the dashboard to toggle monitor mode per agent and start/stop dedicated Falcon scans.
- Monitor-mode commands simply forward to the agent's `/falcon/startmonmode` and `/falcon/stopmonmode` endpoints; supply the managed interface (e.g., `wlan0`) when entering monitor mode and the resulting monitor interface (typically `wlan0mon`) when launching scans.
- The **Refresh Status** button queries `/falcon/scanrunning/<iface>` for the selected agent and interface and appends the response to the Falcon log pane.

## Mock agents for testing
You can spin up mock agents that simulate Wi-Fi, Falcon, and Bluetooth data so the controller can be exercised without any radios.

```
cd controller
source .venv/bin/activate
python tests/mock_agent.py --name mock-east --port 9001 --lat 40.7128 --lon -74.0060
# In another terminal:
python tests/mock_agent.py --name mock-west --port 9002 --lat 34.0522 --lon -118.2437
```

With the controller running, register each mock agent via the UI (URL `http://localhost:9001`, etc.) or run the automated workflow script:

```
python tests/demo_workflow.py --controller http://localhost:8000 \\
    --agent mock-east http://localhost:9001 \\
    --agent mock-west http://localhost:9002
```

The script registers each agent, toggles monitor mode on `wlan0`, and launches both Wi-Fi and Falcon scans so that the dashboard receives live map data and WebSocket events.
