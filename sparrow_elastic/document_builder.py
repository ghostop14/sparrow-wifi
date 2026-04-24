"""ECS 8.17 document builder for sparrow_elastic.

Public API
----------
build_wifi_document(net, obs, now_utc)   -> dict
build_bt_document(dev, obs, now_utc)     -> dict
compute_doc_id(doc)                      -> str

``net`` and ``dev`` are raw agent observation dicts whose keys follow the
sparrow-wifi wirelessengine.py / sparrowbluetooth.py toJsondict() shapes.
``obs`` is the observer context dict; ``now_utc`` is a timezone-aware UTC
datetime used for event.ingested and temporal observed.* fields.
"""

import hashlib
import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from sparrow_elastic.channel_utils import (
    band_for_frequency,
    channel_for_frequency,
    compute_occupied_set,
)
from sparrow_elastic.controller_signature import is_controller_candidate
from sparrow_elastic.device_classifier import classify
from sparrow_elastic.ble_adv_parser import parse_adv_payload
from sparrow_elastic.ecs_helpers import to_es_timestamp, DAY_OF_WEEK
from sparrow_elastic.mac_utils import canonicalize_mac, mac_flags
from sparrow_elastic.signal_utils import dbm_to_mw, quality_0_to_5

logger = logging.getLogger(__name__)

# ECS version we claim compliance with
_ECS_VERSION = "8.17.0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_mac(raw: Optional[str]) -> str:
    """Canonicalize a MAC address; return empty string on failure."""
    if not raw:
        return ""
    try:
        return canonicalize_mac(raw)
    except ValueError:
        logger.debug("document_builder: malformed MAC %r; skipping", raw)
        return ""


def _parse_dt(raw) -> Optional[datetime]:
    """Parse a datetime from a string or datetime object.

    Returns a timezone-aware UTC datetime, or None on failure.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        dt = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(str(raw), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            # Last resort: dateutil (may or may not be installed)
            try:
                from dateutil import parser as _du_parser
                dt = _du_parser.parse(str(raw))
            except Exception:
                return None

    # Normalise to UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _to_float(v) -> Optional[float]:
    """Convert v to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str_to_bool(v) -> bool:
    """Convert a sparrow 'True'/'False' string or bool to bool."""
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _geo_point(lat: Optional[float], lon: Optional[float]) -> Optional[dict]:
    """Return an ES geo_point dict, or None if lat/lon is missing or sentinel 0,0."""
    if lat is None or lon is None:
        return None
    if lat == 0.0 and lon == 0.0:
        # Sparrow uses 0.0, 0.0 as "no GPS fix" sentinel
        return None
    return {"lat": lat, "lon": lon}


def _observed_temporal(now_utc: datetime) -> dict:
    """Build the time-of-day / day-of-week observed sub-fields."""
    hour_utc = now_utc.hour
    dow_utc = DAY_OF_WEEK[now_utc.weekday()]

    # Local temporal fields -- use the system local timezone
    try:
        local_dt = now_utc.astimezone(tz=None)  # tz=None -> local timezone
        tz_name = local_dt.tzname() or "UTC"
        hour_local = local_dt.hour
        dow_local = DAY_OF_WEEK[local_dt.weekday()]
    except Exception:
        hour_local = hour_utc
        dow_local = dow_utc
        tz_name = "UTC"

    return {
        "hour_utc": hour_utc,
        "day_of_week_utc": dow_utc,
        "hour_local": hour_local,
        "day_of_week_local": dow_local,
        "timezone_local": tz_name,
    }


def _build_observer(obs: dict) -> dict:
    """Build the observer ECS section from the obs context dict."""
    observer: dict = {
        "id": obs.get("id") or socket.gethostname(),
        "hostname": obs.get("hostname") or socket.gethostname(),
        "type": "wireless-sensor",
        "vendor": "sparrow-wifi",
        "product": "sparrow-wifi",
    }

    gps_status = obs.get("gps_status")
    if gps_status:
        observer["gps"] = {"status": gps_status}

    geo = obs.get("geo")
    if geo and isinstance(geo, dict):
        lat = _to_float(geo.get("lat"))
        lon = _to_float(geo.get("lon"))
        alt = _to_float(geo.get("alt"))
        gp = _geo_point(lat, lon)
        if gp is not None:
            observer_geo: dict = {"location": gp}
            if alt is not None:
                observer_geo["altitude"] = alt
            observer["geo"] = observer_geo

    return observer


def _build_wifi_related_hash(net: dict) -> tuple:
    """Build the related.hash from WiFi fingerprint fields.

    Uses: vendor_ie_ouis, wps_uuid, supported_rates, ht_capabilities.

    Returns:
        (hash_hex_str, strength_keyword) or (None, None) when no inputs present.
    """
    parts = []
    vendor_ie_ouis  = net.get("vendor_ie_ouis")
    wps_uuid        = net.get("wps_uuid")
    supported_rates = net.get("supported_rates")
    ht_capabilities = net.get("ht_capabilities")

    if vendor_ie_ouis:
        parts.append("ouis:" + ",".join(sorted(str(o) for o in vendor_ie_ouis)))
    if wps_uuid:
        parts.append("wps:" + str(wps_uuid))
    if supported_rates:
        parts.append("rates:" + str(supported_rates))
    if ht_capabilities:
        parts.append("ht:" + str(ht_capabilities))

    if not parts:
        return (None, None)

    n_inputs = len(parts)
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()  # noqa: S324

    return (h, "strong" if n_inputs >= 4 else "weak")


# ---------------------------------------------------------------------------
# Public: compute_doc_id
# ---------------------------------------------------------------------------

def compute_doc_id(doc: dict) -> str:
    """Derive a deterministic _id from the document for idempotent bulk retries.

    SHA-256 over pipe-delimited (observer.id, source.mac, @timestamp,
    event.dataset), returning the first 32 hex characters.

    Args:
        doc: A document dict produced by build_wifi_document() or
             build_bt_document().

    Returns:
        32-character lowercase hex string.
    """
    observer_id = doc.get("observer", {}).get("id", "unknown")
    source_mac  = doc.get("source", {}).get("mac", "unknown")
    ts          = doc.get("@timestamp", "")
    dataset     = doc.get("event", {}).get("dataset", "")
    raw = f"{observer_id}|{source_mac}|{ts}|{dataset}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Public: build_wifi_document
# ---------------------------------------------------------------------------

def build_wifi_document(net: dict, obs: dict, now_utc: datetime) -> dict:
    """Build an ECS 8.17 WiFi network observation document.

    Args:
        net:     Raw agent observation dict.  Keys follow
                 wirelessengine.py WirelessNetwork.toJsondict(), plus optional
                 extensions (vendor_ie_ouis, wps_uuid, supported_rates,
                 ht_capabilities, ht/vht/he/eht, mac_vendor,
                 probe_ssid_list, wps_enabled).
        obs:     Observer context dict:
                     id         -- observer identifier
                     hostname   -- real hostname
                     geo        -- dict(lat, lon, alt) or None
                     gps_status -- GPS status keyword or None
        now_utc: Timezone-aware UTC datetime.

    Returns:
        ECS document dict compatible with the sparrow-wifi index mapping
        (dynamic:strict).
    """
    # ------------------------------------------------------------------
    # Raw field extraction (wirelessengine.py WirelessNetwork.toJsondict)
    # ------------------------------------------------------------------
    raw_mac         = net.get("macAddr", "")
    ssid            = net.get("ssid", "")
    mode            = net.get("mode", "")
    security        = net.get("security", "")
    privacy         = net.get("privacy", "")
    cipher          = net.get("cipher", "")
    frequency       = net.get("frequency")        # int MHz or 0/None
    channel_raw     = net.get("channel")          # int or 0/None
    sec_chan_raw    = net.get("secondaryChannel")
    bandwidth       = net.get("bandwidth")        # int MHz
    signal_raw      = net.get("signal")           # int dBm
    station_cnt     = net.get("stationcount")
    utilization     = net.get("utilization")
    first_seen_raw  = net.get("firstseen")
    last_seen_raw   = net.get("lastseen")
    strongest_sig   = net.get("strongestsignal")

    # Per-AP strongest-signal GPS (observer-side GPS comes from obs)
    strongest_lat   = net.get("strongestlat")
    strongest_lon   = net.get("strongestlon")
    strongest_alt   = net.get("strongestalt")

    # Extended fingerprint / capability fields (not in baseline toJsondict;
    # populated by extended agent or test fixtures)
    vendor_ie_ouis  = net.get("vendor_ie_ouis")   # list[str] or None
    wps_enabled     = net.get("wps_enabled")       # bool or None
    wps_uuid        = net.get("wps_uuid")          # str or None
    ht_cap_flag     = net.get("ht")                # bool or None
    vht_cap_flag    = net.get("vht")               # bool or None
    he_cap_flag     = net.get("he")                # bool or None
    eht_cap_flag    = net.get("eht")               # bool or None
    mac_vendor_str  = net.get("mac_vendor")        # str or None
    probe_ssids     = net.get("probe_ssid_list")   # list[str] or None

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    first_seen_dt = _parse_dt(first_seen_raw) or now_utc
    last_seen_dt  = _parse_dt(last_seen_raw)  or now_utc
    timestamp_str = to_es_timestamp(last_seen_dt)

    # ------------------------------------------------------------------
    # MAC canonicalization + flags
    # ------------------------------------------------------------------
    canon_mac = _safe_mac(raw_mac) or raw_mac or ""
    mac_flag_dict = mac_flags(canon_mac) if canon_mac else {}

    # ------------------------------------------------------------------
    # Frequency / channel / band / occupied-set
    # ------------------------------------------------------------------
    freq_int: Optional[int] = None
    if frequency:
        try:
            freq_int = int(frequency)
            if freq_int == 0:
                freq_int = None
        except (TypeError, ValueError):
            pass

    band: Optional[str] = None
    ch_primary: Optional[int] = None
    occupied_set: Optional[list] = None
    bw_int: Optional[int] = None

    if freq_int:
        band = band_for_frequency(freq_int)
        ch_primary = channel_for_frequency(freq_int)

    # Prefer explicit channel field if freq-derived channel is unavailable
    if ch_primary is None and channel_raw:
        try:
            v = int(channel_raw)
            ch_primary = v if v != 0 else None
        except (TypeError, ValueError):
            pass

    if bandwidth:
        try:
            v = int(bandwidth)
            bw_int = v if v != 0 else None
        except (TypeError, ValueError):
            pass

    if ch_primary is not None and band is not None:
        occupied_set = compute_occupied_set(
            ch_primary, bw_int or 20, band
        )

    sec_chan_int: Optional[int] = None
    if sec_chan_raw:
        try:
            v = int(sec_chan_raw)
            sec_chan_int = v if v != 0 else None
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------
    signal_dbm: Optional[float] = _to_float(signal_raw)
    # sentinel -1000 means "unknown" in sparrow
    if signal_dbm is not None and signal_dbm <= -999:
        signal_dbm = None

    strongest_dbm: Optional[float] = _to_float(strongest_sig)
    if strongest_dbm is not None and strongest_dbm <= -999:
        strongest_dbm = None

    # ------------------------------------------------------------------
    # Device classification (Step 5 will provide real logic)
    # ------------------------------------------------------------------
    class_evidence_dict = {
        "oui_vendor": mac_vendor_str,
        "wifi_ssid": ssid,
        "wifi_vendor_ies": vendor_ie_ouis,
    }
    class_guess, class_conf, class_evidence = classify(class_evidence_dict)

    # ------------------------------------------------------------------
    # Controller-candidate RF signature
    # ------------------------------------------------------------------
    ctrl_candidate = is_controller_candidate(
        rf_band=band or "unknown",
        signal_dbm=signal_dbm,
        device_class=class_guess,
        mac_vendor=mac_vendor_str,
    )

    # ------------------------------------------------------------------
    # Related hash (WiFi fingerprint)
    # ------------------------------------------------------------------
    rel_hash, rel_hash_strength = _build_wifi_related_hash(net)
    device_id = rel_hash if rel_hash else canon_mac

    # ------------------------------------------------------------------
    # GPS (observer-side comes from obs; per-AP strongest_signal below)
    # ------------------------------------------------------------------
    strongest_lat_f = _to_float(strongest_lat)
    strongest_lon_f = _to_float(strongest_lon)
    strongest_alt_f = _to_float(strongest_alt)

    # ------------------------------------------------------------------
    # Temporal
    # ------------------------------------------------------------------
    age_seconds = int((now_utc - first_seen_dt).total_seconds())

    # ------------------------------------------------------------------
    # Assemble document skeleton
    # ------------------------------------------------------------------
    doc: dict = {
        "@timestamp": timestamp_str,
        "ecs": {"version": _ECS_VERSION},
        "event": {
            "kind": "event",
            "category": ["network"],
            "type": ["info"],
            "module": "sparrow-wifi",
            "dataset": "sparrow.wifi",
            "action": "wifi-network-observed",
            "ingested": to_es_timestamp(now_utc),
        },
        "observer": _build_observer(obs),
        "source": {
            "mac": canon_mac,
            "address": canon_mac,
        },
        "device": {
            "id": device_id,
            "class_guess": class_guess,
            "class_confidence": class_conf,
            "class_evidence": class_evidence,
        },
        "related": {
            "mac": [canon_mac] if canon_mac else [],
        },
        "observed": {
            "first_seen": to_es_timestamp(first_seen_dt),
            "last_seen": to_es_timestamp(last_seen_dt),
            "age_seconds": age_seconds,
            **_observed_temporal(now_utc),
        },
    }

    # ------------------------------------------------------------------
    # related.hash (omit entirely when no fingerprint inputs)
    # ------------------------------------------------------------------
    if rel_hash is not None:
        doc["related"]["hash"] = rel_hash
        doc["related"]["hash_strength"] = rel_hash_strength

    # ------------------------------------------------------------------
    # signal.*
    # ------------------------------------------------------------------
    if signal_dbm is not None:
        sig_doc: dict = {"strength_dbm": signal_dbm}
        mw = dbm_to_mw(signal_dbm)
        if mw is not None:
            sig_doc["strength_mw"] = mw
        qual = quality_0_to_5(signal_dbm)
        if qual is not None:
            sig_doc["strength_quality_0_5"] = qual
        doc["signal"] = sig_doc

    # ------------------------------------------------------------------
    # rf.*
    # ------------------------------------------------------------------
    rf_doc: dict = {
        "signature": {"controller_candidate": ctrl_candidate},
    }
    if freq_int:
        rf_doc["frequency_mhz"] = freq_int
    if band:
        rf_doc["band"] = band
    if occupied_set is not None:
        rf_doc["channel_occupied_set"] = occupied_set
    doc["rf"] = rf_doc

    # ------------------------------------------------------------------
    # wifi.*
    # ------------------------------------------------------------------
    wifi: dict = {}

    # ssid / ssid_hidden
    if ssid:
        wifi["ssid"] = ssid
        wifi["ssid_hidden"] = False
    else:
        wifi["ssid_hidden"] = True

    if canon_mac:
        wifi["bssid"] = canon_mac

    if security:
        wifi["security"] = security
    if cipher:
        wifi["cipher"] = cipher
    if privacy:
        wifi["privacy"] = privacy
    if mode:
        wifi["mode"] = mode

    if mac_vendor_str:
        wifi["mac_vendor"] = mac_vendor_str

    # wifi.mac flags
    if mac_flag_dict:
        wifi["mac"] = {
            "locally_administered": mac_flag_dict.get("locally_administered", False),
            "randomized": mac_flag_dict.get("randomized", False),
        }

    # wifi.channel
    chan_doc: dict = {}
    if ch_primary is not None:
        chan_doc["primary"] = ch_primary
    if sec_chan_int is not None:
        chan_doc["secondary"] = sec_chan_int
    if bw_int is not None:
        chan_doc["width_mhz"] = bw_int
    if occupied_set is not None:
        chan_doc["occupied_set"] = occupied_set
    if chan_doc:
        wifi["channel"] = chan_doc

    # wifi.capabilities
    caps: dict = {}
    if ht_cap_flag is not None:
        caps["ht"] = bool(ht_cap_flag)
    if vht_cap_flag is not None:
        caps["vht"] = bool(vht_cap_flag)
    if he_cap_flag is not None:
        caps["he"] = bool(he_cap_flag)
    if eht_cap_flag is not None:
        caps["eht"] = bool(eht_cap_flag)
    if caps:
        wifi["capabilities"] = caps

    # wifi.qbss
    qbss: dict = {}
    if station_cnt is not None:
        try:
            cnt = int(station_cnt)
            if cnt >= 0:
                qbss["station_count"] = cnt
        except (TypeError, ValueError):
            pass
    if utilization is not None:
        try:
            util = float(utilization)
            if util >= 0.0:
                qbss["channel_utilization"] = util
        except (TypeError, ValueError):
            pass
    if qbss:
        wifi["qbss"] = qbss

    # wifi.wps
    wps_doc: dict = {}
    if wps_enabled is not None:
        wps_doc["enabled"] = bool(wps_enabled)
    if wps_uuid:
        wps_doc["uuid"] = str(wps_uuid)
    if wps_doc:
        wifi["wps"] = wps_doc

    # wifi.vendor_ie
    if vendor_ie_ouis:
        wifi["vendor_ie"] = {"ouis": list(vendor_ie_ouis)}

    # wifi.probe
    if probe_ssids:
        wifi["probe"] = {"ssid_list": list(probe_ssids)}

    # wifi.strongest_signal (with per-AP geo)
    strongest_doc: dict = {}
    if strongest_dbm is not None:
        strongest_doc["strength_dbm"] = strongest_dbm
    strongest_gp = _geo_point(strongest_lat_f, strongest_lon_f)
    if strongest_gp is not None:
        sg_geo: dict = {"location": strongest_gp}
        if strongest_alt_f is not None:
            sg_geo["altitude"] = strongest_alt_f
        strongest_doc["geo"] = sg_geo
    if strongest_doc:
        wifi["strongest_signal"] = strongest_doc

    doc["wifi"] = wifi

    return doc


# ---------------------------------------------------------------------------
# Public: build_bt_document
# ---------------------------------------------------------------------------

def build_bt_document(dev: dict, obs: dict, now_utc: datetime) -> dict:
    """Build an ECS 8.17 Bluetooth device observation document.

    Args:
        dev:     Raw agent observation dict.  Keys follow
                 sparrowbluetooth.py BluetoothDevice.toJsondict(), plus
                 optional extension key ``adv_hex`` (not yet populated by
                 the agent).
        obs:     Observer context dict (same shape as build_wifi_document).
        now_utc: Timezone-aware UTC datetime.

    Returns:
        ECS document dict compatible with the sparrow-bt index mapping
        (dynamic:strict).
    """
    # ------------------------------------------------------------------
    # Raw field extraction (BluetoothDevice.toJsondict())
    # ------------------------------------------------------------------
    raw_mac          = dev.get("macAddr", "")
    bt_name          = dev.get("name", "")
    bt_company       = dev.get("company", "")
    bt_manufacturer  = dev.get("manufacturer", "")
    bt_description   = dev.get("bluetoothdescription", "")
    bt_type          = dev.get("bttype")          # 1=Classic, 2=LE
    rssi_raw         = dev.get("rssi")
    tx_power_raw     = dev.get("txpower")
    tx_power_valid   = dev.get("txpowervalid", "False")
    ibeacon_range    = dev.get("ibeaconrange")
    uuid_str         = dev.get("uuid", "")
    first_seen_raw   = dev.get("firstseen")
    last_seen_raw    = dev.get("lastseen")

    # GPS (flat fields as in toJsondict; bt mapping has no geo.altitude)
    gps_lat          = dev.get("lat")
    gps_lon          = dev.get("lon")

    # BLE raw advertising payload (agent extension -- not yet in baseline)
    adv_hex          = dev.get("adv_hex")

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    first_seen_dt = _parse_dt(first_seen_raw) or now_utc
    last_seen_dt  = _parse_dt(last_seen_raw)  or now_utc
    timestamp_str = to_es_timestamp(last_seen_dt)

    # ------------------------------------------------------------------
    # MAC canonicalization
    # ------------------------------------------------------------------
    canon_mac = _safe_mac(raw_mac) or raw_mac or ""
    is_ble = (bt_type is None) or (int(bt_type) == 2 if bt_type is not None else True)
    mac_flag_dict = mac_flags(canon_mac, is_ble=is_ble) if canon_mac else {}

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------
    rssi_dbm: Optional[float] = _to_float(rssi_raw)
    if rssi_dbm is not None and rssi_dbm <= -999:
        rssi_dbm = None

    tx_power_dbm: Optional[float] = _to_float(tx_power_raw)
    tx_power_valid_b = _str_to_bool(tx_power_valid)
    ibeacon_range_f  = _to_float(ibeacon_range)

    # ------------------------------------------------------------------
    # BLE advertising payload (stub returns {})
    # ------------------------------------------------------------------
    adv_parsed = parse_adv_payload(adv_hex)

    # ------------------------------------------------------------------
    # Device classification
    # ------------------------------------------------------------------
    class_evidence_dict = {
        "oui_vendor": bt_manufacturer or bt_company or None,
        "bt_name": bt_name,
        "bt_company": bt_company,
    }
    class_guess, class_conf, class_evidence = classify(class_evidence_dict)
    device_id = canon_mac

    # ------------------------------------------------------------------
    # RF (Bluetooth is always 2.4 GHz in the sparrow model)
    # ------------------------------------------------------------------
    bt_band = "2_4ghz"
    ctrl_candidate = is_controller_candidate(
        rf_band=bt_band,
        signal_dbm=rssi_dbm,
        device_class=class_guess,
        mac_vendor=bt_manufacturer or bt_company or None,
    )

    # ------------------------------------------------------------------
    # GPS
    # ------------------------------------------------------------------
    gps_lat_f = _to_float(gps_lat)
    gps_lon_f = _to_float(gps_lon)

    # ------------------------------------------------------------------
    # Temporal
    # ------------------------------------------------------------------
    age_seconds = int((now_utc - first_seen_dt).total_seconds())

    # ------------------------------------------------------------------
    # Assemble document skeleton
    # ------------------------------------------------------------------
    doc: dict = {
        "@timestamp": timestamp_str,
        "ecs": {"version": _ECS_VERSION},
        "event": {
            "kind": "event",
            "category": ["network"],
            "type": ["info"],
            "module": "sparrow-wifi",
            "dataset": "sparrow.bluetooth",
            "action": "bluetooth-device-observed",
            "ingested": to_es_timestamp(now_utc),
        },
        "observer": _build_observer(obs),
        "source": {
            "mac": canon_mac,
            "address": canon_mac,
        },
        "device": {
            "id": device_id,
            "class_guess": class_guess,
            "class_confidence": class_conf,
            "class_evidence": class_evidence,
        },
        "related": {
            "mac": [canon_mac] if canon_mac else [],
        },
        "rf": {
            "band": bt_band,
            "signature": {"controller_candidate": ctrl_candidate},
        },
        "observed": {
            "first_seen": to_es_timestamp(first_seen_dt),
            "last_seen": to_es_timestamp(last_seen_dt),
            "age_seconds": age_seconds,
            **_observed_temporal(now_utc),
        },
    }

    # ------------------------------------------------------------------
    # signal.*
    # ------------------------------------------------------------------
    if rssi_dbm is not None:
        sig_doc: dict = {"strength_dbm": rssi_dbm}
        mw = dbm_to_mw(rssi_dbm)
        if mw is not None:
            sig_doc["strength_mw"] = mw
        qual = quality_0_to_5(rssi_dbm)
        if qual is not None:
            sig_doc["strength_quality_0_5"] = qual
        doc["signal"] = sig_doc

    # ------------------------------------------------------------------
    # bluetooth.*
    # ------------------------------------------------------------------
    bt: dict = {}

    if bt_name:
        bt["name"] = bt_name
    if bt_company:
        bt["company"] = bt_company
    if bt_manufacturer:
        bt["manufacturer"] = bt_manufacturer
    if bt_description:
        bt["description"] = bt_description

    # bluetooth.type: "classic" or "ble"
    if bt_type is not None:
        try:
            bt["type"] = "classic" if int(bt_type) == 1 else "ble"
        except (TypeError, ValueError):
            bt["type"] = "ble"

    # bluetooth.mac flags
    if mac_flag_dict:
        bt["mac"] = {
            "randomized": mac_flag_dict.get("randomized", False),
            "type": mac_flag_dict.get("addr_type", "unknown"),
        }

    # bluetooth.advertising / beacon / apple from parsed adv payload
    adv_doc: dict = {}
    beacon_doc: dict = {}
    apple_doc: dict = {}

    if adv_parsed:
        for key, val in adv_parsed.items():
            if key.startswith("advertising."):
                adv_doc[key[len("advertising."):]] = val
            elif key.startswith("beacon."):
                beacon_doc[key[len("beacon."):]] = val
            elif key.startswith("apple."):
                apple_doc[key[len("apple."):]] = val

    # Native tx_power from sparrow BluetoothDevice (independent of adv parse)
    if tx_power_valid_b and tx_power_dbm is not None:
        adv_doc["tx_power_dbm"] = tx_power_dbm

    if adv_doc:
        bt["advertising"] = adv_doc
    if beacon_doc:
        bt["beacon"] = beacon_doc
    if apple_doc:
        bt["apple"] = apple_doc

    # bluetooth.ibeacon_range_m
    if ibeacon_range_f is not None and ibeacon_range_f >= 0:
        bt["ibeacon_range_m"] = ibeacon_range_f

    # bluetooth.uuid
    if uuid_str:
        bt["uuid"] = uuid_str

    # bluetooth.geo (GPS where device was last observed)
    bt_gp = _geo_point(gps_lat_f, gps_lon_f)
    if bt_gp is not None:
        bt["geo"] = {"location": bt_gp}

    doc["bluetooth"] = bt

    return doc
