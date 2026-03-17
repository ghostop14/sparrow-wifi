"""
KML export module for Sparrow DroneID.

Generates KML documents from detection history for import into Google Earth,
Mission Planner, or any GIS tool that understands KML.
"""
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .database import Database


# --------------- Style constants ------------------------------------------

# One colour per drone, cycling through this palette (AABBGGRR in KML notation)
_TRACK_COLOURS = [
    "ff0000ff",  # red
    "ff00ff00",  # lime
    "ffff0000",  # blue
    "ff00ffff",  # yellow
    "ffff00ff",  # magenta
    "ffffff00",  # cyan
    "ff0080ff",  # orange
    "ffff8000",  # sky blue
]

_RECEIVER_COLOUR = "ff00ff00"   # lime — matches standard "friendly" colour
_OPERATOR_COLOUR = "ff00a5ff"   # orange


# --------------- Helpers --------------------------------------------------

def _sub(parent: ET.Element, tag: str, text: str = None) -> ET.Element:
    """Append a child element, optionally with text content."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _coords(lon: float, lat: float, alt: float) -> str:
    """Format a KML coordinate triple (lon,lat,alt — KML order)."""
    return f"{lon:.7f},{lat:.7f},{alt:.2f}"


def _has_position(lat: float, lon: float) -> bool:
    """Return True only when a position is non-zero and therefore meaningful."""
    return lat != 0.0 or lon != 0.0


def _add_style(doc: ET.Element, style_id: str, line_colour: str,
               icon_colour: str) -> None:
    """Append a shared Style element to a Document."""
    style = _sub(doc, "Style")
    style.set("id", style_id)

    ls = _sub(style, "LineStyle")
    _sub(ls, "color", line_colour)
    _sub(ls, "width", "2")

    ps = _sub(style, "PolyStyle")
    _sub(ps, "fill", "0")

    ics = _sub(style, "IconStyle")
    _sub(ics, "color", icon_colour)
    _sub(ics, "scale", "0.8")
    icon = _sub(ics, "Icon")
    _sub(icon, "href",
         "http://maps.google.com/mapfiles/kml/shapes/airports.png")


# --------------- Public API -----------------------------------------------

def generate_kml(
    db: Database,
    from_ts: str,
    to_ts: str,
    serial: Optional[str] = None,
    receiver_lat: Optional[float] = None,
    receiver_lon: Optional[float] = None,
    receiver_alt: Optional[float] = None,
) -> str:
    """Generate a KML document for detections in the given time window.

    Args:
        db:           Open Database instance.
        from_ts:      ISO-8601 start timestamp (inclusive).
        to_ts:        ISO-8601 end timestamp (inclusive).
        serial:       If given, restrict output to this drone serial number.
        receiver_lat: Receiver latitude for an optional Receiver placemark.
        receiver_lon: Receiver longitude.
        receiver_alt: Receiver altitude in metres (default 0).

    Returns:
        A UTF-8 KML XML string.
    """
    records, _total = db.get_history(from_ts, to_ts, serial)

    # ---- Build document skeleton ----------------------------------------
    ET.register_namespace("", "http://www.opengis.net/kml/2.2")
    kml = ET.Element("kml")
    kml.set("xmlns", "http://www.opengis.net/kml/2.2")
    doc = _sub(kml, "Document")
    _sub(doc, "name", "Sparrow DroneID Export")
    _sub(doc, "description",
         f"DroneID detections from {from_ts} to {to_ts}")

    # ---- Group records by serial ----------------------------------------
    by_serial: Dict[str, List[dict]] = {}
    for rec in records:
        sn = rec.get("serial_number") or "UNKNOWN"
        by_serial.setdefault(sn, []).append(rec)

    # ---- Shared styles (one per drone, cycling palette) -----------------
    serials_ordered = list(by_serial.keys())
    for idx, sn in enumerate(serials_ordered):
        colour = _TRACK_COLOURS[idx % len(_TRACK_COLOURS)]
        _add_style(doc, f"drone_{idx}", colour, colour)

    # Receiver / operator styles
    _add_style(doc, "receiver_style", _RECEIVER_COLOUR, _RECEIVER_COLOUR)
    _add_style(doc, "operator_style", _OPERATOR_COLOUR, _OPERATOR_COLOUR)

    # ---- Optional receiver placemark ------------------------------------
    rec_lat = receiver_lat or 0.0
    rec_lon = receiver_lon or 0.0
    rec_alt = receiver_alt or 0.0
    if _has_position(rec_lat, rec_lon):
        rx_pm = _sub(doc, "Placemark")
        _sub(rx_pm, "name", "Receiver")
        _sub(rx_pm, "styleUrl", "#receiver_style")
        pt = _sub(rx_pm, "Point")
        _sub(pt, "coordinates", _coords(rec_lon, rec_lat, rec_alt))

    # ---- One Folder per drone -------------------------------------------
    for idx, sn in enumerate(serials_ordered):
        drone_records = by_serial[sn]
        style_url = f"#drone_{idx}"

        folder = _sub(doc, "Folder")
        _sub(folder, "name", f"Drone: {sn}")

        # -- Flight track (LineString) ------------------------------------
        track_coords = []
        for r in drone_records:
            lat = r.get("drone_lat", 0.0) or 0.0
            lon = r.get("drone_lon", 0.0) or 0.0
            alt = r.get("drone_height_agl", 0.0) or 0.0
            if _has_position(lat, lon):
                track_coords.append(_coords(lon, lat, alt))

        if track_coords:
            track_pm = _sub(folder, "Placemark")
            _sub(track_pm, "name", f"Track: {sn}")
            _sub(track_pm, "styleUrl", style_url)
            ls = _sub(track_pm, "LineString")
            _sub(ls, "altitudeMode", "absolute")
            _sub(ls, "coordinates", " ".join(track_coords))

        # -- Individual position placemarks -------------------------------
        for r in drone_records:
            lat = r.get("drone_lat", 0.0) or 0.0
            lon = r.get("drone_lon", 0.0) or 0.0
            alt = r.get("drone_height_agl", 0.0) or 0.0
            if not _has_position(lat, lon):
                continue

            ts = r.get("timestamp", "")
            speed = r.get("speed", 0.0) or 0.0
            agl = r.get("drone_height_agl", 0.0) or 0.0
            rssi = r.get("rssi", 0) or 0

            pm = _sub(folder, "Placemark")
            _sub(pm, "name", sn)
            _sub(pm, "styleUrl", style_url)

            if ts:
                stamp = _sub(pm, "TimeStamp")
                # KML TimeStamp requires an xsd:dateTime value
                when_str = ts if ts.endswith("Z") else ts + "Z"
                _sub(stamp, "when", when_str)

            _sub(pm, "description",
                 f"Alt: {agl:.1f}m  Speed: {speed:.1f}m/s  RSSI: {rssi}dBm")

            pt = _sub(pm, "Point")
            _sub(pt, "altitudeMode", "absolute")
            _sub(pt, "coordinates", _coords(lon, lat, alt))

        # -- Operator position (last detection that has operator coords) --
        op_lat = op_lon = op_alt = 0.0
        for r in reversed(drone_records):
            cand_lat = r.get("operator_lat", 0.0) or 0.0
            cand_lon = r.get("operator_lon", 0.0) or 0.0
            if _has_position(cand_lat, cand_lon):
                op_lat = cand_lat
                op_lon = cand_lon
                # operator_alt is not returned by get_history; default to 0
                op_alt = 0.0
                break

        if _has_position(op_lat, op_lon):
            op_pm = _sub(folder, "Placemark")
            _sub(op_pm, "name", f"Operator: {sn}")
            _sub(op_pm, "styleUrl", "#operator_style")
            op_pt = _sub(op_pm, "Point")
            _sub(op_pt, "coordinates", _coords(op_lon, op_lat, op_alt))

    # ---- Serialise to string --------------------------------------------
    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ")
    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8")
