#!/usr/bin/python3
"""Sparrow WiFi/Bluetooth -> Elasticsearch/OpenSearch bridge (ECS 8.17).

Polls a Sparrow WiFi agent over HTTP, builds ECS 8.17 documents via
sparrow_elastic, and bulk-indexes them into an ES/OS cluster.

Usage
-----
  sparrow-elastic.py --elasticserver https://my-es:9200 [options]

See --help for the full flag reference.

Environment variable fallbacks (CLI flag takes precedence when both set):
  SPARROW_ES_URL            -> --elasticserver
  SPARROW_ES_USERNAME       -> --username
  SPARROW_ES_PASSWORD       -> --password
  SPARROW_ES_API_KEY        -> --api-key
  SPARROW_ES_ENGINE         -> --engine
  SPARROW_FINGERBANK_API_KEY -> --fingerbank-api-key
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# sparrow_elastic package imports
# ---------------------------------------------------------------------------
# All imports are absolute so this script can be executed directly from the
# repo root (where sparrow_elastic/ is a sibling directory on sys.path).

from sparrow_elastic.bulk_buffer import BulkBuffer
from sparrow_elastic.client_factory import build_client
from sparrow_elastic.data_refresh import refresh_all, start_background_refresh
from sparrow_elastic.device_classifier import combine_matches
from sparrow_elastic.document_builder import (
    build_bt_document,
    build_wifi_document,
    compute_doc_id,
)
from sparrow_elastic.fingerbank_client import (
    configure as fb_configure,
    enrich_classification,
    lookup as fb_lookup,
)
from sparrow_elastic.settings import fingerbank_enabled
from sparrow_elastic.templates import load_component, resolve_template

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("sparrow_elastic_bridge")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sparrow WiFi/Bluetooth -> Elasticsearch/OpenSearch bridge (ECS 8.17).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Connection
    p.add_argument(
        "--elasticserver",
        default=os.environ.get("SPARROW_ES_URL", ""),
        metavar="URL",
        required=False,   # validated after parse when --refresh-data is absent
        help="Elasticsearch/OpenSearch server URL. Required unless --refresh-data.",
    )
    p.add_argument(
        "--engine",
        default=os.environ.get("SPARROW_ES_ENGINE", "elasticsearch"),
        choices=["elasticsearch", "opensearch"],
        help="Backend engine.",
    )
    p.add_argument(
        "--username",
        default=os.environ.get("SPARROW_ES_USERNAME", ""),
        metavar="STR",
        help="HTTP Basic auth username.",
    )
    p.add_argument(
        "--password",
        default=os.environ.get("SPARROW_ES_PASSWORD", ""),
        metavar="STR",
        help="HTTP Basic auth password.",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("SPARROW_ES_API_KEY", ""),
        metavar="STR",
        help="Elasticsearch API key (base64 encoded id:key).",
    )
    tls_group = p.add_mutually_exclusive_group()
    tls_group.add_argument(
        "--verify-tls",
        dest="verify_tls",
        action="store_true",
        default=True,
        help="Verify TLS certificates (default).",
    )
    tls_group.add_argument(
        "--no-verify-tls",
        dest="verify_tls",
        action="store_false",
        help="Disable TLS certificate verification.",
    )

    # Agent targeting
    p.add_argument(
        "--sparrowagent",
        default="127.0.0.1",
        metavar="HOST",
        help="Sparrow WiFi agent hostname or IP.",
    )
    p.add_argument(
        "--sparrowport",
        type=int,
        default=8020,
        metavar="PORT",
        help="Sparrow WiFi agent HTTP port.",
    )
    p.add_argument(
        "--wifiinterface",
        default="",
        metavar="NAME",
        help="Wireless interface on the agent to scan. When omitted, the first available interface is used.",
    )

    # Index aliases
    p.add_argument(
        "--wifi-alias",
        default="sparrow-wifi",
        metavar="NAME",
        help="Elasticsearch alias / index for WiFi documents.",
    )
    p.add_argument(
        "--bt-alias",
        default="sparrow-bt",
        metavar="NAME",
        help="Elasticsearch alias / index for Bluetooth documents. Set to empty string to disable BT.",
    )

    # ILM/ISM policy names
    p.add_argument(
        "--ilm-policy",
        default="",
        metavar="NAME",
        help=(
            "Override ILM/ISM policy name for both indices. "
            "Defaults: sparrow-wifi-ilm (WiFi) and sparrow-bt-ilm (BT)."
        ),
    )

    # Timing
    p.add_argument(
        "--scandelay",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="Seconds between agent scan cycles.",
    )
    p.add_argument(
        "--flush-interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Maximum seconds between bulk flushes (flush also triggers at --batch-size).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        metavar="N",
        help="Number of buffered documents that triggers an early flush.",
    )

    # Fingerbank
    p.add_argument(
        "--fingerbank-api-key",
        default=os.environ.get("SPARROW_FINGERBANK_API_KEY", ""),
        metavar="STR",
        help="Fingerbank live-API key for device enrichment (optional).",
    )

    # Observer
    p.add_argument(
        "--agent-name",
        default="",
        metavar="STR",
        help="Observer identifier injected into every document. Defaults to hostname.",
    )

    # Startup behaviour
    p.add_argument(
        "--dont-create-indices",
        action="store_true",
        default=False,
        help="Skip bootstrap (template + policy + initial index creation).",
    )
    p.add_argument(
        "--refresh-data",
        action="store_true",
        default=False,
        help="Refresh all bundled reference data files then exit (no scanning).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )

    return p


# ---------------------------------------------------------------------------
# Remote-agent HTTP helpers
# ---------------------------------------------------------------------------
# These functions talk to the Sparrow WiFi agent HTTP API.
# They return raw JSON dicts — no WirelessNetwork / BluetoothDevice objects —
# so that the bridge has no dependency on the sparrow-wifi Python source tree.
# The dict shapes match what wirelessengine.toJsondict() and
# BluetoothDevice.toJsondict() produce, which is exactly what the document
# builders consume.

def _http_get(url: str, timeout: float = 6.0) -> Tuple[int, str]:
    """Perform a GET request and return (status_code, body_text).

    Returns (-1, "") on any network or timeout error.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return -1, ""


def requestRemoteGPS(
    remote_ip: str, remote_port: int
) -> Tuple[int, str, Optional[dict]]:
    """Fetch GPS status from the remote agent.

    Returns:
        (errcode, errmsg, gps_dict) where gps_dict has keys:
            gps_installed, gps_running, is_valid, lat, lon, alt, speed
        or None on failure / no fix.
    """
    url = f"http://{remote_ip}:{remote_port}/gps/status"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            is_valid = str(j.get("gpssynch", "false")).lower() in ("true", "1")
            gps: dict = {
                "gps_installed": str(j.get("gpsinstalled", "false")).lower() in ("true", "1"),
                "gps_running": str(j.get("gpsrunning", "false")).lower() in ("true", "1"),
                "is_valid": is_valid,
                "lat": 0.0,
                "lon": 0.0,
                "alt": 0.0,
                "speed": 0.0,
            }
            if is_valid and "gpspos" in j:
                pos = j["gpspos"]
                gps["lat"] = float(pos.get("latitude", 0.0))
                gps["lon"] = float(pos.get("longitude", 0.0))
                gps["alt"] = float(pos.get("altitude", 0.0))
                gps["speed"] = float(pos.get("speed", 0.0))
            return 0, "", gps
        except Exception as exc:
            return -2, f"Error parsing GPS response: {exc}", None
    return -1, f"HTTP {status} from agent GPS endpoint", None


def requestRemoteInterfaces(
    remote_ip: str, remote_port: int
) -> Tuple[int, Optional[List[str]]]:
    """Fetch the list of wireless interfaces from the remote agent.

    Returns:
        (status_code, list_of_interface_names) or (status_code, None) on error.
    """
    url = f"http://{remote_ip}:{remote_port}/wireless/interfaces"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return status, j.get("interfaces", [])
        except Exception:
            return status, None
    return status, None


def requestRemoteNetworks(
    remote_ip: str, remote_port: int, interface: str,
    channel_list: Optional[List[int]] = None,
) -> Tuple[int, str, Optional[List[dict]]]:
    """Scan and fetch WiFi networks via the remote agent.

    Args:
        remote_ip:    Agent IP/hostname.
        remote_port:  Agent HTTP port.
        interface:    Wireless interface name to scan.
        channel_list: Optional list of channel frequencies (MHz) to restrict scan.

    Returns:
        (errcode, errmsg, list_of_network_dicts)
        Network dicts follow the WirelessNetwork.toJsondict() shape.
    """
    url = f"http://{remote_ip}:{remote_port}/wireless/networks/{interface}"
    if channel_list:
        url += "?frequencies=" + ",".join(str(c) for c in channel_list)
    # Scan can take several seconds; use a generous timeout.
    status, body = _http_get(url, timeout=20.0)
    if status == 200:
        try:
            j = json.loads(body)
            return j.get("errCode", 0), j.get("errString", ""), j.get("networks", [])
        except Exception as exc:
            return -2, f"Error parsing networks response: {exc}", None
    return -1, f"HTTP {status} from agent networks endpoint", None


def startRemoteBluetoothDiscoveryScan(
    agent_ip: str, agent_port: int, ubertooth: bool = False
) -> Tuple[int, str]:
    """Start a Bluetooth discovery scan on the remote agent."""
    endpoint = "discoverystartp" if ubertooth else "discoverystarta"
    url = f"http://{agent_ip}:{agent_port}/bluetooth/{endpoint}"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return j.get("errcode", 0), j.get("errmsg", "")
        except Exception:
            return -1, "Error parsing response"
    return -2, f"Bad response from agent [{status}]"


def stopRemoteBluetoothDiscoveryScan(
    agent_ip: str, agent_port: int
) -> Tuple[int, str]:
    """Stop the Bluetooth discovery scan on the remote agent."""
    url = f"http://{agent_ip}:{agent_port}/bluetooth/discoverystop"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return j.get("errcode", 0), j.get("errmsg", "")
        except Exception:
            return -1, "Error parsing response"
    return -2, f"Bad response from agent [{status}]"


def getRemoteBluetoothRunningServices(
    agent_ip: str, agent_port: int
) -> Tuple[int, str, bool, bool, bool, bool]:
    """Query running status of Bluetooth services on the remote agent.

    Returns:
        (errcode, errmsg, has_bluetooth, has_ubertooth,
         spectrum_scan_running, discovery_scan_running)
    """
    url = f"http://{agent_ip}:{agent_port}/bluetooth/running"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return (
                j.get("errcode", 0),
                j.get("errmsg", ""),
                bool(j.get("hasbluetooth", False)),
                bool(j.get("hasubertooth", False)),
                bool(j.get("spectrumscanrunning", False)),
                bool(j.get("discoveryscanrunning", False)),
            )
        except Exception:
            return -1, "Error parsing response", False, False, False, False
    return -2, f"Bad response from agent [{status}]", False, False, False, False


def clearRemoteBluetoothDeviceList(
    agent_ip: str, agent_port: int
) -> Tuple[int, str]:
    """Clear the accumulated device list on the remote agent."""
    url = f"http://{agent_ip}:{agent_port}/bluetooth/discoveryclear"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return j.get("errcode", 0), j.get("errmsg", "")
        except Exception:
            return -1, "Error parsing response"
    return -2, f"Bad response from agent [{status}]"


def getRemoteBluetoothDiscoveryStatus(
    agent_ip: str, agent_port: int
) -> Tuple[int, str, Optional[List[dict]]]:
    """Fetch accumulated Bluetooth discovery results from the remote agent.

    Returns:
        (errcode, errmsg, list_of_device_dicts)
        Device dicts follow the BluetoothDevice.toJsondict() shape.
    """
    url = f"http://{agent_ip}:{agent_port}/bluetooth/discoverystatus"
    status, body = _http_get(url)
    if status == 200:
        try:
            j = json.loads(body)
            return j.get("errcode", 0), j.get("errmsg", ""), j.get("devices", [])
        except Exception as exc:
            return -1, f"Error parsing BT discovery response: {exc}", None
    return -2, f"Bad response from agent [{status}]", None


# ---------------------------------------------------------------------------
# Fingerbank enrichment post-processor
# ---------------------------------------------------------------------------

def apply_fingerbank(doc: dict, settings: dict) -> None:
    """Enrich a document's device classification with Fingerbank data in-place.

    This function is called after build_wifi_document() or build_bt_document()
    returns.  It synthesises a per_class dict from the existing device.*
    classification, adds Fingerbank's contribution, then re-runs combine_matches
    to produce the final winner.

    Design rationale:
        The document builder calls classify() internally and returns the winner
        triple, but does not expose the intermediate per_class dict.  Rather
        than modifying the document builder API, we reconstruct a synthetic
        per_class here using the existing class_guess and class_confidence as
        a single evidence entry.  This approximation works correctly because:

        - If Tier 1 fired strongly (e.g., conf 0.95), the rebuilt per_class
          carries that weight and Fingerbank at 0.55–0.75 cannot overturn it
          via probabilistic-OR.
        - If Tier 1 was absent (class_guess == "unknown", conf 0.0), Fingerbank
          becomes the sole contributor and its class_guess wins cleanly.
        - If Fingerbank agrees with Tier 1, combine_matches boosts the combined
          confidence via the probabilistic-OR formula as intended.

        When Fingerbank's class differs from the existing winner and the
        existing confidence is high, combine_matches will keep the existing
        winner.  In that case Fingerbank's evidence tag will NOT appear in the
        final output (evidence_list only collects the winner's tags).

    Args:
        doc:      ECS document dict produced by a build_*_document() call.
                  Modified in place.
        settings: Settings dict from load_settings(); used to check whether
                  Fingerbank is enabled.
    """
    if not fingerbank_enabled(settings):
        return

    mac = doc.get("source", {}).get("mac")
    if not mac:
        return

    fb_result = fb_lookup(mac)
    if fb_result is None:
        return

    existing_class = doc["device"]["class_guess"]
    existing_conf = doc["device"]["class_confidence"]
    existing_tags = list(doc["device"].get("class_evidence") or [])

    # Reconstruct per_class from the existing classification.
    per_class: Dict[str, List] = {}
    if existing_class != "unknown" and existing_conf > 0.0:
        # Approximate: treat the full confidence as a single evidence entry.
        # Using the first tag (or a synthetic base tag) as the representative.
        tag = existing_tags[0] if existing_tags else f"{existing_class}_base"
        per_class[existing_class] = [(existing_conf, tag)]
        # Re-add remaining tags as zero-marginal-gain entries (conf already
        # baked into existing_conf via prob-OR; adding them here would
        # double-count, so we keep only the first tag to carry the confidence).

    # Inject Fingerbank's contribution.
    enrich_classification(per_class, fb_result)

    # Re-run combiner to pick the winner.
    winner_class, winner_conf, winner_tags = combine_matches(per_class)

    doc["device"]["class_guess"] = winner_class
    doc["device"]["class_confidence"] = winner_conf
    doc["device"]["class_evidence"] = winner_tags


# ---------------------------------------------------------------------------
# Bootstrap sequence
# ---------------------------------------------------------------------------

def _load_policy(kind: str, engine: str) -> dict:
    """Load the ILM (ES) or ISM (OpenSearch) policy JSON for *kind* (wifi/bt)."""
    suffix = "ism" if engine == "opensearch" else "ilm"
    policy_path = os.path.join(
        os.path.dirname(__file__),
        "sparrow_elastic", "policies",
        f"sparrow-{kind}-{suffix}.json",
    )
    try:
        with open(policy_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("bootstrap: could not load policy file %s: %s", policy_path, exc)
        return {}


def bootstrap(client, alias: str, kind: str, engine: str, ilm_override: str) -> bool:
    """Run the bootstrap sequence for one index family (wifi or bt).

    Steps:
      1. Resolve index template (with component inlining for OpenSearch).
      2. Ensure the ILM/ISM policy exists.
      3. Ensure the composable index template is created / updated.
      4. Ensure the initial index and write alias exist.

    Args:
        client:       SearchClient instance.
        alias:        The index alias (e.g. "sparrow-wifi").
        kind:         "wifi" or "bt".
        engine:       "elasticsearch" or "opensearch".
        ilm_override: Non-empty string overrides the default policy name.

    Returns:
        True on success, False if any step raised an exception.
    """
    template_name = f"sparrow-{kind}-template"
    default_policy_name = f"sparrow-{kind}-ilm"
    policy_name = ilm_override or default_policy_name
    initial_index = f"{alias}-000001"

    try:
        logger.info("bootstrap[%s]: resolving template '%s'", kind, template_name)
        template_body = resolve_template(
            template_name,
            for_opensearch=(engine == "opensearch"),
        )
    except Exception as exc:
        logger.warning("bootstrap[%s]: failed to resolve template: %s", kind, exc)
        return False

    policy_body = _load_policy(kind, engine)
    if policy_body:
        try:
            logger.info("bootstrap[%s]: ensuring policy '%s'", kind, policy_name)
            client.ensure_policy(policy_name, policy_body)
        except Exception as exc:
            logger.warning("bootstrap[%s]: ensure_policy failed: %s", kind, exc)
            # Non-fatal: proceed to template + index creation.

    # Upload referenced component templates first (ES path). The OS path has
    # already inlined components and will have no `composed_of` here; its
    # ensure_component_template is a no-op for uniformity.
    composed_of = template_body.get("composed_of", []) or []
    if composed_of:
        base = template_name.rsplit("-template", 1)[0]
        for comp_full_name in composed_of:
            prefix = base + "-"
            role = (
                comp_full_name[len(prefix):]
                if comp_full_name.startswith(prefix)
                else comp_full_name
            )
            try:
                comp_body = load_component(base, role)
                logger.info(
                    "bootstrap[%s]: ensuring component template '%s'",
                    kind, comp_full_name,
                )
                client.ensure_component_template(comp_full_name, comp_body)
            except Exception as exc:
                logger.warning(
                    "bootstrap[%s]: ensure_component_template('%s') failed: %s",
                    kind, comp_full_name, exc,
                )
                return False

    try:
        logger.info("bootstrap[%s]: ensuring template '%s'", kind, template_name)
        client.ensure_template(template_name, template_body)
    except Exception as exc:
        logger.warning("bootstrap[%s]: ensure_template failed: %s", kind, exc)
        return False

    try:
        logger.info(
            "bootstrap[%s]: ensuring initial index '%s' on alias '%s'",
            kind, initial_index, alias,
        )
        client.ensure_initial_index(alias, initial_index)
    except Exception as exc:
        logger.warning("bootstrap[%s]: ensure_initial_index failed: %s", kind, exc)
        return False

    logger.info("bootstrap[%s]: complete", kind)
    return True


# ---------------------------------------------------------------------------
# Flush helper
# ---------------------------------------------------------------------------

class FlushState:
    """Tracks last-flush time for a single buffer so flush_if_due() is stateless."""

    def __init__(self, flush_interval: float, batch_size: int) -> None:
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self._last_flush: float = time.monotonic()

    def is_due(self, depth: int) -> bool:
        """Return True if it's time to flush based on depth or elapsed time."""
        if depth >= self.batch_size:
            return True
        return (time.monotonic() - self._last_flush) >= self.flush_interval

    def mark_flushed(self) -> None:
        self._last_flush = time.monotonic()


def flush_buffer(
    client,
    buf: BulkBuffer,
    state: FlushState,
    alias: str,
    bootstrap_needed_flag: list,  # mutable single-element list used as pointer
) -> None:
    """Flush the buffer to Elasticsearch if flush criteria are met.

    Args:
        client:               SearchClient instance.
        buf:                  BulkBuffer to flush.
        state:                FlushState tracking timing and batch-size thresholds.
        alias:                Index alias name (used for logging only).
        bootstrap_needed_flag: ``[True/False]`` — if flush fails with an
                              index-not-found error, set to True so the main
                              loop knows to re-bootstrap on the next cycle.
    """
    if not state.is_due(buf.depth()):
        return

    actions = buf.swap()
    if not actions:
        state.mark_flushed()
        return

    try:
        success, errors = client.bulk(actions)
        state.mark_flushed()
        if errors:
            # Check for index-not-found errors which indicate bootstrap is needed.
            for err in errors:
                err_type = ""
                try:
                    err_type = (
                        err.get("index", {})
                           .get("error", {})
                           .get("type", "")
                    )
                except Exception:
                    pass
                if "index_not_found" in err_type or "no such index" in err_type.lower():
                    logger.warning(
                        "flush[%s]: index-not-found error — will re-bootstrap", alias
                    )
                    bootstrap_needed_flag[0] = True
                    # Put the actions back into the buffer so they aren't lost.
                    for a in actions:
                        buf.append(a)
                    return
            logger.warning(
                "flush[%s]: bulk partial failure — %d errors out of %d actions",
                alias, len(errors), len(actions),
            )
        else:
            logger.debug("flush[%s]: %d documents indexed successfully", alias, success)
    except Exception as exc:
        logger.warning(
            "flush[%s]: bulk request failed (%s) — %d actions returned to buffer",
            alias, exc, len(actions),
        )
        # Return actions to the buffer so they are retried next cycle.
        for a in actions:
            buf.append(a)


# ---------------------------------------------------------------------------
# Observer GPS update
# ---------------------------------------------------------------------------

def update_observer_gps(obs: dict, agent_ip: str, agent_port: int) -> None:
    """Fetch GPS from the agent and update the observer context in-place."""
    errcode, _errmsg, gps = requestRemoteGPS(agent_ip, agent_port)
    if errcode == 0 and gps and gps.get("is_valid"):
        obs["geo"] = {
            "lat": gps["lat"],
            "lon": gps["lon"],
            "alt": gps["alt"],
        }
        obs["gps_status"] = "locked_3d"
    else:
        obs["geo"] = None
        obs["gps_status"] = "unlocked"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:  # noqa: C901
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Configure logging.
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logger.setLevel(log_level)

    # ------------------------------------------------------------------
    # --refresh-data mode: update bundled reference files then exit.
    # ------------------------------------------------------------------
    if args.refresh_data:
        logger.info("--refresh-data: running full data refresh and exiting")
        results = refresh_all(force=True)
        for name, status in results.items():
            print(f"  {name}: {status}")
        failed = [n for n, s in results.items() if s == "failed"]
        return 1 if failed else 0

    # ------------------------------------------------------------------
    # Validate required flags for normal operation.
    # ------------------------------------------------------------------
    if not args.elasticserver:
        parser.error(
            "--elasticserver is required (or set SPARROW_ES_URL environment variable)"
        )

    # ------------------------------------------------------------------
    # Build settings dict (used by fingerbank and document builder helpers).
    # ------------------------------------------------------------------
    settings: dict = {
        "engine": args.engine,
        "url": args.elasticserver,
        "username": args.username,
        "password": args.password,
        "api_key": args.api_key,
        "verify_certs": args.verify_tls,
        "fingerbank_api_key": args.fingerbank_api_key,
        "fingerbank_offline_db": "",
    }

    # ------------------------------------------------------------------
    # Configure Fingerbank client.
    # ------------------------------------------------------------------
    if args.fingerbank_api_key:
        fb_configure(api_key=args.fingerbank_api_key)
        logger.info("Fingerbank live API enrichment enabled")
    else:
        fb_configure()  # will use offline DB if present
        if fingerbank_enabled(settings):
            logger.info("Fingerbank offline DB enrichment enabled")
        else:
            logger.info("Fingerbank enrichment disabled (no api-key, no offline DB)")

    # ------------------------------------------------------------------
    # Build the search client (with retry on connection failure).
    # ------------------------------------------------------------------
    client = None
    retry_delay = 1.0
    max_retry_delay = 60.0
    logger.info("Connecting to %s (%s)...", args.elasticserver, args.engine)
    while client is None:
        try:
            client = build_client(settings)
            if not client.ping():
                raise ConnectionError("ping() returned False")
            logger.info("Connected to %s", args.elasticserver)
        except Exception as exc:
            logger.warning(
                "Cannot connect to %s: %s — retrying in %.0fs",
                args.elasticserver, exc, retry_delay,
            )
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2.0, max_retry_delay)
            client = None

    # ------------------------------------------------------------------
    # Bootstrap index templates + policies + initial indices.
    # ------------------------------------------------------------------
    wifi_bootstrap_needed = [False]
    bt_bootstrap_needed = [False]

    if not args.dont_create_indices:
        if not bootstrap(client, args.wifi_alias, "wifi", args.engine, args.ilm_policy):
            wifi_bootstrap_needed[0] = True
        if args.bt_alias:
            if not bootstrap(client, args.bt_alias, "bt", args.engine, args.ilm_policy):
                bt_bootstrap_needed[0] = True

    # ------------------------------------------------------------------
    # Start background reference-data refresh thread.
    # ------------------------------------------------------------------
    start_background_refresh(interval_hours=24.0)

    # ------------------------------------------------------------------
    # Resolve the wireless interface to scan.
    # ------------------------------------------------------------------
    agent_ip = args.sparrowagent
    agent_port = args.sparrowport

    if args.wifiinterface:
        wifi_interface = args.wifiinterface
        logger.info("Using configured WiFi interface: %s", wifi_interface)
    else:
        logger.info("Querying agent for available wireless interfaces...")
        status, iface_list = requestRemoteInterfaces(agent_ip, agent_port)
        if not iface_list:
            logger.error(
                "Could not retrieve wireless interfaces from agent at %s:%d "
                "(HTTP %d) — exiting.",
                agent_ip, agent_port, status,
            )
            return 2
        wifi_interface = iface_list[0]
        logger.info("Using first available WiFi interface: %s", wifi_interface)

    # ------------------------------------------------------------------
    # Start Bluetooth discovery scan on the agent.
    # ------------------------------------------------------------------
    bt_enabled = bool(args.bt_alias)
    if bt_enabled:
        errcode, errmsg = startRemoteBluetoothDiscoveryScan(agent_ip, agent_port)
        if errcode != 0:
            logger.warning(
                "Could not start BT discovery scan: %s — "
                "BT scanning disabled for this session",
                errmsg,
            )
            bt_enabled = False
        else:
            logger.info("Bluetooth discovery scan started on agent")

    # ------------------------------------------------------------------
    # Observer context.
    # ------------------------------------------------------------------
    obs: dict = {
        "id": args.agent_name or socket.gethostname(),
        "hostname": socket.gethostname(),
        "geo": None,
        "gps_status": "unknown",
    }

    # ------------------------------------------------------------------
    # Bulk buffers + flush state.
    # ------------------------------------------------------------------
    wifi_buf = BulkBuffer(max_size=10_000)
    bt_buf = BulkBuffer(max_size=10_000)
    wifi_flush = FlushState(args.flush_interval, args.batch_size)
    bt_flush = FlushState(args.flush_interval, args.batch_size)

    logger.info(
        "Bridge running: interface=%s wifi_alias=%s bt_alias=%s "
        "scan_delay=%.0fs flush_interval=%.0fs batch_size=%d",
        wifi_interface, args.wifi_alias, args.bt_alias or "(disabled)",
        args.scandelay, args.flush_interval, args.batch_size,
    )

    # ------------------------------------------------------------------
    # Main scan/flush loop.
    # ------------------------------------------------------------------
    try:
        while True:
            # Per-cycle reference time for bootstrap/flush housekeeping only.
            # Doc-build now_utc is recaptured AFTER each scan returns so it
            # is always >= the agent's observation timestamps (avoids
            # negative age_seconds due to scan-call latency).
            now_utc = datetime.now(tz=timezone.utc)

            # Re-bootstrap if a previous flush detected a missing index.
            if wifi_bootstrap_needed[0] and not args.dont_create_indices:
                logger.info("Re-bootstrapping WiFi index after index-not-found error")
                if bootstrap(client, args.wifi_alias, "wifi", args.engine, args.ilm_policy):
                    wifi_bootstrap_needed[0] = False

            if bt_enabled and bt_bootstrap_needed[0] and not args.dont_create_indices:
                logger.info("Re-bootstrapping BT index after index-not-found error")
                if bootstrap(client, args.bt_alias, "bt", args.engine, args.ilm_policy):
                    bt_bootstrap_needed[0] = False

            # Update observer GPS from the agent.
            update_observer_gps(obs, agent_ip, agent_port)

            # ---- WiFi scan --------------------------------------------------
            errcode, errmsg, networks = requestRemoteNetworks(
                agent_ip, agent_port, wifi_interface
            )
            if errcode == 0 and networks is not None:
                # Recapture after HTTP returns so now_utc >= any lastseen the
                # agent reports for this batch.
                now_utc = datetime.now(tz=timezone.utc)
                logger.debug("WiFi scan: %d networks received", len(networks))
                for net in networks:
                    try:
                        doc = build_wifi_document(net, obs, now_utc)
                        apply_fingerbank(doc, settings)
                        action = {
                            "_op_type": "index",
                            "_index": args.wifi_alias,
                            "_id": compute_doc_id(doc),
                            "_source": doc,
                        }
                        wifi_buf.append(action)
                    except Exception as exc:
                        logger.debug("WiFi doc build error: %s", exc)
            else:
                logger.warning("WiFi scan failed (errcode=%d): %s", errcode, errmsg)

            # ---- Bluetooth scan ---------------------------------------------
            if bt_enabled:
                errcode, errmsg, _hbt, _hub, _spec, disc_running = (
                    getRemoteBluetoothRunningServices(agent_ip, agent_port)
                )

                if disc_running:
                    errcode, errmsg, devices = getRemoteBluetoothDiscoveryStatus(
                        agent_ip, agent_port
                    )
                    if errcode == 0 and devices:
                        # Clear the remote list after fetching so we don't
                        # re-index stale entries on the next cycle.
                        clearRemoteBluetoothDeviceList(agent_ip, agent_port)
                        # Recapture after HTTP returns (see WiFi branch).
                        now_utc = datetime.now(tz=timezone.utc)
                        logger.debug("BT scan: %d devices received", len(devices))
                        for dev in devices:
                            try:
                                doc = build_bt_document(dev, obs, now_utc)
                                apply_fingerbank(doc, settings)
                                action = {
                                    "_op_type": "index",
                                    "_index": args.bt_alias,
                                    "_id": compute_doc_id(doc),
                                    "_source": doc,
                                }
                                bt_buf.append(action)
                            except Exception as exc:
                                logger.debug("BT doc build error: %s", exc)
                    elif errcode != 0:
                        logger.warning("BT discovery status error: %s", errmsg)
                else:
                    # Discovery scan stopped; clear stale list and restart.
                    logger.warning(
                        "BT discovery scan not running — clearing and restarting"
                    )
                    clearRemoteBluetoothDeviceList(agent_ip, agent_port)
                    rc, msg = startRemoteBluetoothDiscoveryScan(agent_ip, agent_port)
                    if rc != 0:
                        logger.warning("BT restart failed: %s", msg)

            # ---- Flush buffers ----------------------------------------------
            flush_buffer(
                client, wifi_buf, wifi_flush,
                args.wifi_alias, wifi_bootstrap_needed,
            )
            if bt_enabled:
                flush_buffer(
                    client, bt_buf, bt_flush,
                    args.bt_alias, bt_bootstrap_needed,
                )

            time.sleep(args.scandelay)

    except KeyboardInterrupt:
        logger.info("Interrupted — stopping BT discovery and exiting")
        if bt_enabled:
            stopRemoteBluetoothDiscoveryScan(agent_ip, agent_port)
    finally:
        # Final flush of any remaining buffered documents.
        logger.info("Final flush before exit...")
        try:
            remaining_wifi = wifi_buf.swap()
            if remaining_wifi:
                client.bulk(remaining_wifi)
                logger.info("Flushed %d remaining WiFi documents", len(remaining_wifi))
            if bt_enabled:
                remaining_bt = bt_buf.swap()
                if remaining_bt:
                    client.bulk(remaining_bt)
                    logger.info("Flushed %d remaining BT documents", len(remaining_bt))
        except Exception as exc:
            logger.warning("Final flush error: %s", exc)
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
