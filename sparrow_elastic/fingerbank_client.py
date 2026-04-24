"""sparrow_elastic.fingerbank_client — opt-in Fingerbank device enrichment.

Fingerbank (https://fingerbank.org) provides OUI + DHCP-fingerprint +
user-agent → device model / type lookups.  This module is operator-activated:
it requires either a ``fingerbank_api_key`` in settings OR a local
``sparrow_elastic/data/fingerbank.db`` offline database.

Two lookup modes (preferred order):
  1. Offline DB  — ``sparrow_elastic/data/fingerbank.db`` (SQLite), refreshed
                   weekly via data_refresh.  Queried locally; no API quota.
  2. Live API    — ``https://api.fingerbank.org/api/v2/combinations/interrogate``
                   Used only when the offline DB is absent or returns no hit.
                   Requires an API key.  Negative results are cached in-memory
                   for TTL seconds to conserve quota.

Public API
----------
lookup(mac, dhcp_fingerprint, user_agent) -> Optional[FingerbankResult]
enrich_classification(per_class, fb_result) -> per_class (mutated + returned)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package data directory
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_DEFAULT_OFFLINE_DB = os.path.join(_DATA_DIR, "fingerbank.db")

# ---------------------------------------------------------------------------
# Module-level state: settings cache + negative-result TTL cache
# ---------------------------------------------------------------------------

# Settings injected by configure() — or None to read from defaults / env.
_api_key: Optional[str] = None
_offline_db_path: Optional[str] = None  # None → use _DEFAULT_OFFLINE_DB if present

# Negative-result cache: mac -> expiry_timestamp (float)
_NEG_CACHE: Dict[str, float] = {}
_NEG_CACHE_TTL: float = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def configure(api_key: Optional[str] = None,
              offline_db_path: Optional[str] = None) -> None:
    """Inject runtime settings.

    Called once at bridge startup from the resolved settings dict.  Both
    parameters are optional; omitting them leaves the module operating on its
    defaults (no API key, default DB path).

    Args:
        api_key:        Fingerbank API key string, or None / empty to disable
                        live-API fallback.
        offline_db_path: Explicit path to the SQLite offline DB.  When None
                         the module uses the bundled default path.
    """
    global _api_key, _offline_db_path
    _api_key = api_key or None
    _offline_db_path = offline_db_path or None
    _NEG_CACHE.clear()
    logger.debug(
        "fingerbank_client: configured (api_key=%s, offline_db=%s)",
        "SET" if _api_key else "NONE",
        _offline_db_path or _DEFAULT_OFFLINE_DB,
    )


# ---------------------------------------------------------------------------
# FingerbankResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FingerbankResult:
    """Result from a Fingerbank device lookup.

    Attributes:
        device_model:  Human-readable device model name, e.g. "Apple iPhone 13 Pro Max".
        device_type:   Device type string as returned by Fingerbank, e.g. "Phone".
        confidence:    0.0–1.0 normalised from Fingerbank's 0–100 score.
        source:        "offline_db" or "live_api".
        raw:           Raw response dict, useful for debugging.
    """
    device_model: str
    device_type: str
    confidence: float
    source: str
    raw: dict


# ---------------------------------------------------------------------------
# Type taxonomy mapping
# ---------------------------------------------------------------------------

# Maps fragments of Fingerbank device_type strings → our class_guess taxonomy.
# The check is case-insensitive; first match wins (ordering matters).
_TYPE_MAP: List[Tuple[str, str]] = [
    # Drone controller — nothing in Fingerbank's taxonomy, but keep a slot.
    # Phones / mobile
    ("iphone",          "phone"),
    ("android",         "phone"),
    ("smartphone",      "phone"),
    ("mobile device",   "phone"),
    ("phone",           "phone"),
    # Laptops / desktops
    ("laptop",          "laptop"),
    ("desktop",         "laptop"),
    ("windows",         "laptop"),
    ("macos",           "laptop"),
    ("linux",           "laptop"),
    # Printers
    ("print server",    "printer"),
    ("printer",         "printer"),
    # Wearables
    ("fitness tracker", "wearable"),
    ("smart glasses",   "wearable"),
    ("wearable",        "wearable"),
    ("watch",           "wearable"),
    # Headsets / audio
    ("earbuds",         "headset"),
    ("headset",         "headset"),
    ("audio device",    "headset"),
    # Speakers / assistants
    ("home assistant",  "speaker"),
    ("smart speaker",   "speaker"),
    ("speaker",         "speaker"),
    # Access points / routers
    ("access point",    "ap"),
    ("router",          "ap"),
    ("network device",  "ap"),
    # IoT / smart home
    ("smart home",      "iot"),
    ("thermostat",      "iot"),
    ("camera",          "iot"),
    ("doorbell",        "iot"),
    ("iot device",      "iot"),
    # Vehicles
    ("automotive",      "vehicle"),
    ("vehicle",         "vehicle"),
    ("car",             "vehicle"),
]


def _map_device_type(device_type: str) -> Optional[str]:
    """Map a Fingerbank device_type string to our class_guess taxonomy.

    Returns None for unmapped / empty types so the caller can skip the
    contribution cleanly.
    """
    if not device_type:
        return None
    lower = device_type.lower()
    for fragment, cls in _TYPE_MAP:
        if fragment in lower:
            return cls
    return None


# ---------------------------------------------------------------------------
# Offline DB query
# ---------------------------------------------------------------------------

def _resolve_db_path() -> str:
    """Return the effective offline DB path."""
    return _offline_db_path if _offline_db_path else _DEFAULT_OFFLINE_DB


def _query_offline_db(db_path: str, mac: str) -> Optional[FingerbankResult]:
    """Query the bundled SQLite DB for the best-match device by MAC OUI.

    Uses a simple JOIN on combinations + devices, ordering by score DESC.
    Tolerant of schema drift — any sqlite3 exception returns None.

    SQL (best-effort):
        SELECT d.name, d.type, c.score
        FROM combinations c
        JOIN devices d ON d.id = c.device_id
        WHERE c.mac = ?
        ORDER BY c.score DESC LIMIT 1

    Args:
        db_path: Absolute path to the SQLite file.
        mac:     Full MAC address string (normalised to OUI by the caller).

    Returns:
        FingerbankResult or None on miss / error.
    """
    if not os.path.isfile(db_path):
        return None

    # Use the first 6 hex chars (OUI) for matching, colon-less, uppercase.
    oui = mac.replace(":", "").replace("-", "").upper()[:6]
    if len(oui) < 6:
        return None

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            cursor = conn.execute(
                """
                SELECT d.name, d.type, c.score
                FROM combinations c
                JOIN devices d ON d.id = c.device_id
                WHERE c.mac = ?
                ORDER BY c.score DESC LIMIT 1
                """,
                (oui,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("fingerbank_client: offline DB query error: %s", exc)
        return None

    if row is None:
        return None

    name, dev_type, score = row
    confidence = max(0.0, min(1.0, (score or 0) / 100.0))
    raw = {"name": name, "type": dev_type, "score": score, "source": "offline_db"}
    return FingerbankResult(
        device_model=name or "",
        device_type=dev_type or "",
        confidence=confidence,
        source="offline_db",
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Live API query
# ---------------------------------------------------------------------------

def _is_neg_cached(mac: str) -> bool:
    """Return True if this MAC is in the negative cache and has not expired."""
    expiry = _NEG_CACHE.get(mac)
    if expiry is None:
        return False
    if time.monotonic() >= expiry:
        del _NEG_CACHE[mac]
        return False
    return True


def _set_neg_cache(mac: str) -> None:
    """Record a negative result for *mac* in the in-memory cache."""
    _NEG_CACHE[mac] = time.monotonic() + _NEG_CACHE_TTL


def _query_live_api(api_key: str, mac: str,
                    timeout: float = 5.0) -> Optional[FingerbankResult]:
    """POST to the Fingerbank live API for a device lookup.

    Uses stdlib urllib (no requests dep).  Caches negative results for
    _NEG_CACHE_TTL seconds to avoid repeat-miss quota churn.

    API reference: https://api.fingerbank.org/api/v2/combinations/interrogate
    Method: POST (JSON body) or GET with query params.  We use POST/JSON.

    Args:
        api_key: Fingerbank API key.
        mac:     Full MAC address.
        timeout: HTTP timeout in seconds.

    Returns:
        FingerbankResult on success, None otherwise.
    """
    if _is_neg_cached(mac):
        logger.debug("fingerbank_client: negative-cache hit for %s", mac)
        return None

    url = "https://api.fingerbank.org/api/v2/combinations/interrogate"
    payload = json.dumps({"mac": mac, "key": api_key}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.debug(
                    "fingerbank_client: live API returned HTTP %d for %s",
                    resp.status, mac,
                )
                _set_neg_cache(mac)
                return None
            body = resp.read()
    except urllib.error.URLError as exc:
        logger.debug("fingerbank_client: live API network error for %s: %s", mac, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("fingerbank_client: live API error for %s: %s", mac, exc)
        return None

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("fingerbank_client: live API non-JSON response for %s: %s", mac, exc)
        _set_neg_cache(mac)
        return None

    # Fingerbank v2 interrogate response structure:
    # {"device": {"name": "...", "device_type": {"name": "..."}}, "score": 75, ...}
    # The exact schema can drift; be tolerant.
    device = data.get("device") or {}
    device_name = device.get("name") or ""
    dtype_obj = device.get("device_type") or {}
    device_type = dtype_obj.get("name") or "" if isinstance(dtype_obj, dict) else ""
    score = data.get("score") or 0

    if not device_name and not device_type:
        logger.debug("fingerbank_client: live API empty result for %s", mac)
        _set_neg_cache(mac)
        return None

    confidence = max(0.0, min(1.0, score / 100.0))
    return FingerbankResult(
        device_model=device_name,
        device_type=device_type,
        confidence=confidence,
        source="live_api",
        raw=data,
    )


# ---------------------------------------------------------------------------
# Public: lookup
# ---------------------------------------------------------------------------

def lookup(
    mac: Optional[str],
    dhcp_fingerprint: Optional[str] = None,  # reserved for future use
    user_agent: Optional[str] = None,         # reserved for future use
) -> Optional[FingerbankResult]:
    """Look up a device via Fingerbank (offline DB preferred; live API fallback).

    Returns a FingerbankResult or None when:
    - All inputs are None / empty.
    - Neither api_key nor offline DB is available (module is effectively disabled).
    - Offline DB queried but no hit; live API not tried (no api_key).
    - Live API returned no match.
    - Any error path (logged at DEBUG — enrichment is best-effort).

    Args:
        mac:               Device MAC address string (any common format).
        dhcp_fingerprint:  DHCP option fingerprint (reserved; not yet used).
        user_agent:        User-agent string (reserved; not yet used).

    Returns:
        FingerbankResult or None.
    """
    if not mac:
        return None

    db_path = _resolve_db_path()
    db_available = os.path.isfile(db_path)
    key_available = bool(_api_key)

    if not db_available and not key_available:
        logger.debug(
            "fingerbank_client: disabled (no offline DB at %s and no api_key)", db_path
        )
        return None

    # 1. Try offline DB first.
    if db_available:
        result = _query_offline_db(db_path, mac)
        if result is not None:
            return result
        logger.debug(
            "fingerbank_client: offline DB miss for %s — %s",
            mac, "falling through to live API" if key_available else "no api_key, done",
        )

    # 2. Fall through to live API.
    if not key_available:
        return None

    return _query_live_api(_api_key, mac)


# ---------------------------------------------------------------------------
# Public: enrich_classification
# ---------------------------------------------------------------------------

# Confidence ladder for Fingerbank contributions:
# - Exact model match is not currently detectable without additional heuristics,
#   so we use a two-tier ladder based on mapping quality:
_CONF_TYPE_ONLY = 0.55    # We mapped device_type to a class
_CONF_CAP = 0.75          # Never exceed this (Tier 1 always wins via prob-OR)


def enrich_classification(
    per_class: Dict[str, List[Tuple[float, str]]],
    fb_result: Optional[FingerbankResult],
) -> Dict[str, List[Tuple[float, str]]]:
    """Inject a Fingerbank-derived evidence entry into *per_class*.

    Mutates and returns *per_class* so it can be passed directly to
    device_classifier.combine_matches().

    The contributed confidence is capped at _CONF_CAP (0.75) so Tier 1
    signals (cod_major 0.9, appearance 0.9, Apple Continuity 0.95) always
    outweigh a Fingerbank-only match via the probabilistic-OR combiner.
    When Fingerbank agrees with Tier 1, the combiner bumps combined confidence
    cleanly.

    Evidence tag format: "fingerbank:<device_model>".

    Args:
        per_class:  The dict of {class_guess -> [(confidence, tag), ...]} built
                    by device_classifier.classify() (or an empty dict).
        fb_result:  FingerbankResult from lookup(), or None.

    Returns:
        per_class (same object, mutated in place).
    """
    if fb_result is None:
        return per_class

    cls = _map_device_type(fb_result.device_type)
    if cls is None:
        # Unknown / unmapped type — do not contribute.
        logger.debug(
            "fingerbank_client: unmapped device_type %r — skipping enrichment",
            fb_result.device_type,
        )
        return per_class

    # Determine confidence: cap at _CONF_CAP.
    raw_conf = min(fb_result.confidence, _CONF_CAP)
    # Use the type-only tier if the Fingerbank confidence itself is low.
    contributed_conf = max(_CONF_TYPE_ONLY, raw_conf) if raw_conf > 0.0 else _CONF_TYPE_ONLY
    contributed_conf = min(contributed_conf, _CONF_CAP)

    tag = f"fingerbank:{fb_result.device_model}" if fb_result.device_model else "fingerbank:unknown_model"

    if cls not in per_class:
        per_class[cls] = []
    per_class[cls].append((contributed_conf, tag))

    logger.debug(
        "fingerbank_client: enriched class=%s conf=%.3f tag=%s",
        cls, contributed_conf, tag,
    )
    return per_class
