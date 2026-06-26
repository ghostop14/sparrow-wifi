/* ============================================================
   utils.js — Shared utility functions and toast system
   ============================================================ */

const Utils = (() => {

  // ---- Time / Date Formatters ----

  function relativeTime(isoString) {
    if (!isoString) return '—';
    const now = Date.now();
    const then = new Date(isoString).getTime();
    const secs = Math.round((now - then) / 1000);
    if (secs < 0)   return 'now';
    if (secs < 60)  return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m`;
    return `${Math.floor(secs / 3600)}h`;
  }

  function formatTime(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatDateTime(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    return d.toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' });
  }

  /** Local date+time matching elk-ui's alert format exactly (Date.toLocaleString()
   *  with no options → e.g. "6/21/2026, 6:50:07 PM" in en-US, locale-aware). */
  function formatLocal(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '—';
    try { return d.toLocaleString(); } catch (e) { return isoString; }
  }

  /** Format an ISO timestamp as HH:MM:SSZ using UTC getters (never local time). */
  function formatZulu(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '—';
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
  }

  function toLocalDatetimeInput(isoString) {
    if (!isoString) return '';
    const d = new Date(isoString);
    // Return format: YYYY-MM-DDTHH:mm
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fromLocalDatetimeInput(value) {
    if (!value) return null;
    return new Date(value).toISOString();
  }

  // ---- Unit system preference (server-authoritative, locally cached) ----

  const UNITS_KEY = 'sparrow_units';

  // Local cache — initialised from localStorage for fast first paint,
  // then overwritten by the server value once settings are fetched.
  let _unitsCached = localStorage.getItem(UNITS_KEY) || 'metric';

  function getUnits() {
    return _unitsCached;
  }

  function setUnits(system) {
    _unitsCached = system === 'imperial' ? 'imperial' : 'metric';
    localStorage.setItem(UNITS_KEY, _unitsCached);
    // Persist to server (fire-and-forget)
    if (typeof Api !== 'undefined' && Api.putSettings) {
      Api.putSettings({ display_units: _unitsCached }).catch(() => {});
    }
  }

  /** Called once during init after settings are fetched from the server. */
  function syncUnitsFromSettings(serverValue) {
    if (serverValue === 'imperial' || serverValue === 'metric') {
      _unitsCached = serverValue;
      localStorage.setItem(UNITS_KEY, _unitsCached);
    }
  }

  function toggleUnits() {
    const next = getUnits() === 'metric' ? 'imperial' : 'metric';
    setUnits(next);
    return next;
  }

  // ---- Unit label helpers ----

  function formatAltUnit() {
    return getUnits() === 'imperial' ? 'ft' : 'm';
  }

  function formatSpeedUnit() {
    return getUnits() === 'imperial' ? 'mph' : 'm/s';
  }

  // ---- Number Formatters ----

  // Fix #17: guard only on null, not zero — 0m AGL is a valid altitude
  function formatAlt(m) {
    if (m == null) return '—';
    if (getUnits() === 'imperial') {
      return `${Math.round(m * 3.28084)} ft`;
    }
    return `${Math.round(m)} m`;
  }

  function formatSpeed(mps) {
    if (mps == null) return '—';
    if (getUnits() === 'imperial') {
      return `${(mps * 2.23694).toFixed(1)} mph`;
    }
    return `${mps.toFixed(1)} m/s`;
  }

  // Renamed from formatRange — unit-aware distance formatter
  function formatDistance(m) {
    if (m == null) return '—';
    if (getUnits() === 'imperial') {
      if (m < 1609) return `${Math.round(m * 3.28084)} ft`;
      return `${(m / 1609.34).toFixed(2)} mi`;
    }
    if (m >= 1000) return `${(m / 1000).toFixed(2)} km`;
    return `${Math.round(m)} m`;
  }

  // Keep formatRange as alias for backward compatibility
  function formatRange(m) {
    return formatDistance(m);
  }

  /**
   * Format altitude with "— not broadcast" when the value is null or 0.
   * Used for Alt Geo / Alt Baro where 0 typically means "not transmitted",
   * unlike AGL where 0 is a valid (ground-level) reading.
   */
  function formatAltOrAbsent(m) {
    if (m == null || m === 0) return '— not broadcast';
    return formatAlt(m);
  }

  /**
   * Format a range in both units, primary first per the operator's preference.
   * e.g. metric: "337 m / 1106 ft", imperial: "1106 ft / 337 m"
   */
  function formatRangeDual(m) {
    if (m == null) return '—';
    const meters = Math.round(m);
    const feet   = Math.round(m * 3.28084);
    if (getUnits() === 'imperial') {
      return `${feet} ft / ${meters} m`;
    }
    return `${meters} m / ${feet} ft`;
  }

  /**
   * Format an altitude in both units, primary first per the operator's preference.
   * Suffix is caller-supplied (e.g. 'AGL').
   * e.g. metric: "200 m AGL (656 ft AGL)", imperial: "656 ft AGL (200 m AGL)"
   */
  function formatAltDual(m, suffix) {
    if (m == null) return '—';
    const sfx = suffix ? ` ${suffix}` : '';
    const meters = Math.round(m);
    const feet   = Math.round(m * 3.28084);
    if (getUnits() === 'imperial') {
      return `${feet} ft${sfx} (${meters} m${sfx})`;
    }
    return `${meters} m${sfx} (${feet} ft${sfx})`;
  }

  function formatBearing(deg, cardinal) {
    if (deg == null) return '—';
    const c = cardinal ? ` ${cardinal}` : '';
    return `${Math.round(deg)}°${c}`;
  }

  function formatRssi(rssi) {
    if (rssi == null) return '—';
    return `${rssi} dBm`;
  }

  function formatBytes(bytes) {
    if (bytes == null) return '—';
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(2)} MB`;
    if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(2)} KB`;
    return `${bytes} B`;
  }

  function formatDuration(secs) {
    if (!secs) return '0s';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  // ---- RSSI bar HTML ----

  function rssiBarHtml(rssi) {
    const level = rssiToLevel(rssi); // 0..4
    const cls = rssi > -70 ? 'lit' : rssi > -85 ? 'lit warn' : 'lit weak';
    const heights = [4, 7, 10, 14];
    let bars = '';
    heights.forEach((h, i) => {
      const lit = i < level ? cls : '';
      bars += `<div class="rssi-bar ${lit}" style="height:${h}px"></div>`;
    });
    return `<span class="rssi-bar-wrap" title="${rssi} dBm">${bars}</span>${rssi} dBm`;
  }

  function rssiToLevel(rssi) {
    if (rssi == null) return 0;
    if (rssi >= -60) return 4;
    if (rssi >= -70) return 3;
    if (rssi >= -80) return 2;
    if (rssi >= -90) return 1;
    return 0;
  }

  // ---- UA Type helpers ----

  const UA_ICONS = {
    0: 'bi-question-circle',
    1: 'bi-airplane',
    2: 'bi-aircraft-horizontal',
    3: 'bi-arrow-repeat',
    4: 'bi-aircraft-horizontal',
    5: 'bi-feather',
    6: 'bi-wind',
    7: 'bi-stars',
    8: 'bi-circle',
    9: 'bi-circle-half',
    10: 'bi-blimp',
    11: 'bi-cloud-drizzle',
    12: 'bi-lightning-charge',
    13: 'bi-paperclip',
    14: 'bi-geo',
    15: 'bi-box',
  };

  const UA_ABBREV = {
    0: 'N/A', 1: 'Plane', 2: 'Multi', 3: 'Gyro', 4: 'VTOL',
    5: 'Orn.', 6: 'Glider', 7: 'Kite', 8: 'Balloon', 9: 'Cap.Bal',
    10: 'Airship', 11: 'Chute', 12: 'Rocket', 13: 'Tethered', 14: 'Ground', 15: 'Other',
  };

  function uaTypeHtml(type) {
    const icon = UA_ICONS[type] || 'bi-question-circle';
    const abbrev = UA_ABBREV[type] || '?';
    return `<i class="bi ${icon} ua-icon me-1" title="${abbrev}"></i>${abbrev}`;
  }

  // ---- Protocol label ----

  function protocolLabel(protocol) {
    switch (protocol) {
      case 'astm_nan':       return '<span class="badge bg-info text-dark">NAN</span>';
      case 'astm_beacon':    return '<span class="badge bg-primary">Beacon</span>';
      case 'astm_ble':       return '<span class="badge bg-info text-dark">BLE</span>';
      case 'dji_proprietary':return '<span class="badge bg-warning text-dark">DJI</span>';
      case 'french':         return '<span class="badge" style="background:#1E40AF;color:#fff;">FR-DID</span>';
      case 'wifi_ssid':      return '<span class="badge bg-success">WiFi SSID</span>';
      default: return `<span class="badge bg-secondary">${protocol || '?'}</span>`;
    }
  }

  // ---- Toast system ----

  function toast(message, type = 'info', title = null, duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    // Fix #21: extend icon and title maps to include warning and danger types
    const icons = {
      info:    'bi-info-circle',
      alert:   'bi-exclamation-triangle-fill',
      drone:   'bi-aircraft-horizontal',
      success: 'bi-check-circle-fill',
      warning: 'bi-exclamation-triangle',
      danger:  'bi-x-circle',
    };
    const icon = icons[type] || icons.info;
    const titleText = title || {
      info:    'Info',
      alert:   'Alert',
      drone:   'New Drone',
      success: 'Success',
      warning: 'Warning',
      danger:  'Error',
    }[type] || 'Info';

    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const div = document.createElement('div');
    div.className = `toast sparrow-toast toast-${type}`;
    div.id = id;
    div.setAttribute('role', 'alert');
    div.innerHTML = `
      <div class="toast-header">
        <i class="bi ${icon} me-2"></i>
        <strong class="me-auto">${titleText}</strong>
        <small class="text-muted">${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</small>
        <button type="button" class="btn-close btn-close-white ms-2" data-bs-dismiss="toast"></button>
      </div>
      <div class="toast-body">${message}</div>
    `;
    container.appendChild(div);

    const bsToast = new bootstrap.Toast(div, { delay: duration, autohide: true });
    bsToast.show();
    div.addEventListener('hidden.bs.toast', () => div.remove());
  }

  // ---- Geo helpers (JS mirrors of models.py haversine/bearing/bearing_cardinal) ----
  // These implement the same formulas as the Python backend so derived fields
  // (range, bearing) can be computed client-side for replay records that carry
  // receiver_lat/lon.  Keep in sync if the Python versions change.

  const _EARTH_RADIUS_M = 6371000;
  const _CARDINAL_POINTS = [
    'N','NNE','NE','ENE','E','ESE','SE','SSE',
    'S','SSW','SW','WSW','W','WNW','NW','NNW',
  ];

  function haversine(lat1, lon1, lat2, lon2) {
    const toRad = d => d * Math.PI / 180;
    const dlat = toRad(lat2 - lat1);
    const dlon = toRad(lon2 - lon1);
    const a = Math.sin(dlat / 2) ** 2
            + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dlon / 2) ** 2;
    return _EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function bearing(lat1, lon1, lat2, lon2) {
    const toRad = d => d * Math.PI / 180;
    const rlat1 = toRad(lat1), rlat2 = toRad(lat2);
    const dlon  = toRad(lon2 - lon1);
    const x = Math.sin(dlon) * Math.cos(rlat2);
    const y = Math.cos(rlat1) * Math.sin(rlat2) - Math.sin(rlat1) * Math.cos(rlat2) * Math.cos(dlon);
    return (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
  }

  function bearingCardinal(deg) {
    return _CARDINAL_POINTS[Math.round(deg / 22.5) % 16];
  }

  // ---- HTML / attribute escaping ----
  //
  // Remote-ID string fields (serial, operator_id, self_id_text, MAC, vendor)
  // originate from RF-decoded payloads — any hostile actor can broadcast a
  // crafted message. Treat every such string as untrusted when interpolating
  // into innerHTML or inline event handler attributes.

  const _HTML_ESCAPES = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
    '`': '&#96;',
  };

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"'`]/g, ch => _HTML_ESCAPES[ch]);
  }

  // ---- Short serial display ----

  function shortSerial(serial) {
    if (!serial) return '—';
    if (serial.length <= 12) return serial;
    return serial.slice(0, 8) + '…' + serial.slice(-4);
  }

  // ---- Altitude class badge ----

  function altBadge(cls) {
    if (!cls) return '';
    return `<span class="alt-badge ${cls}">${cls}</span>`;
  }

  // ---- MGRS conversion ----

  /**
   * Convert lat/lon to an MGRS string using the vendored mgrs library.
   * CRITICAL: mgrs.forward takes [longitude, latitude] — NOT lat/lon order.
   * Returns '' on any error or out-of-range input (callers must handle gracefully).
   * @param {number} lat  WGS84 latitude
   * @param {number} lon  WGS84 longitude
   * @param {number} [accuracy=5]  digits of precision (5 = 1 m)
   */
  function toMGRS(lat, lon, accuracy) {
    if (lat == null || lon == null) return '';
    try {
      // mgrs.forward([longitude, latitude], accuracy) — lon FIRST
      return mgrs.forward([lon, lat], accuracy != null ? accuracy : 5);
    } catch (e) {
      return '';
    }
  }

  // ---- Google Maps pushpin link (shareable: one-shot open or copy-to-text) ----

  /**
   * Google Maps "drop a pushpin" URL for a coordinate — the SAME format the
   * Slack/API alerts use (backend alert_engine.maps_pushpin_url), so a link
   * copied from the UI matches what teams receive in alerts.
   */
  function mapsPushpinUrl(lat, lon) {
    return `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lon.toFixed(6)}`;
  }

  /**
   * Inline HTML for a clickable Google Maps pushpin link plus a copy-URL
   * button — lets an operator one-shot open the map or copy the link to text
   * to a field team. Returns '' when the coordinate is absent (null/undefined
   * or the 0,0 default). stopPropagation keeps a click from toggling the
   * surrounding row/panel.
   */
  function mapsLinkHtml(lat, lon) {
    if (lat == null || lon == null || (lat === 0 && lon === 0)) return '';
    const urlAttr = escapeHtml(mapsPushpinUrl(lat, lon));
    return `<a href="${urlAttr}" target="_blank" rel="noopener" class="maps-link" ` +
      `title="Open in Google Maps" onclick="event.stopPropagation();">` +
      `<i class="bi bi-geo-alt-fill"></i> Map</a>` +
      ` <button class="btn-copy-inline" title="Copy Google Maps link" ` +
      `onclick="event.stopPropagation(); Utils.copyToClipboard('${urlAttr}', 'Map link copied');">` +
      `<i class="bi bi-clipboard" style="font-size:10px;"></i></button>`;
  }

  // ---- Clipboard helper (promoted from map.js for use across modules) ----

  /**
   * Copy text to clipboard.  Shows a toast on success/failure.
   * Uses navigator.clipboard when available, falls back to execCommand.
   * @param {string} text     Text to copy
   * @param {string} toastMsg Success message shown in toast (defaults to 'Copied')
   */
  function copyToClipboard(text, toastMsg) {
    const msg = toastMsg || 'Copied to clipboard';
    function fallback() {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      toast(ok ? msg : 'Copy failed', ok ? 'success' : 'danger');
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => toast(msg, 'success'))
        .catch(() => fallback());
    } else {
      fallback();
    }
  }

  // ---- Read-aloud callout builder ----

  /**
   * Build a plain-text operator callout string for a fired alert.
   *
   * Source precedence: if liveCtx (ctx.drone from detection_found branch) is
   * provided, prefer its live fields; otherwise compose from denormalized
   * alert columns (purged-fallback path).
   *
   * @param {Object} alertRow  The alert record from the database (with geo cols)
   * @param {Object|null} liveCtx  ctx.drone when detection was found, else null
   * @returns {string}  Single-line plain-text callout ready for TTS / copy
   */
  function buildCallout(alertRow, liveCtx) {
    const parts = [];

    // Identity: vendor + ua_type_name
    const a = alertRow || {};
    const drone = liveCtx || {};

    const vendor = drone.vendor || a.vendor || '';
    const uaType = drone.ua_type_name || a.ua_type_name || '';
    const typeStr = [vendor, uaType].filter(Boolean).join(' ');
    if (typeStr) parts.push(typeStr);

    // AGL altitude — prefer live drone data, fall back to alert row
    const agl = (liveCtx ? drone.drone_height_agl : null) ?? a.drone_height_agl;
    if (agl != null && agl !== 0) {
      parts.push(`~${formatAltDual(agl, 'AGL')}`);
    }

    // Range & bearing from sensor (drone)
    const rangeMDrone = (liveCtx ? (drone.derived && drone.derived.range_m) : null) ?? a.range_m;
    const brgDeg      = (liveCtx ? (drone.derived && drone.derived.bearing_deg) : null) ?? a.bearing_deg;
    if (rangeMDrone != null && brgDeg != null) {
      const card = bearingCardinal(brgDeg);
      parts.push(`${formatRangeDual(rangeMDrone)} bearing ${Math.round(brgDeg)}° (${card}) from sensor`);
    }

    // Operator range & bearing
    const opRangeM = (liveCtx ? (drone.derived && drone.derived.operator_range_m) : null) ?? a.operator_range_m;
    const opBrgDeg  = (liveCtx ? (drone.derived && drone.derived.operator_bearing_deg) : null) ?? a.operator_bearing_deg;
    if (opRangeM != null && opBrgDeg != null) {
      const opCard = bearingCardinal(opBrgDeg);
      parts.push(`pilot ${formatRangeDual(opRangeM)} bearing ${Math.round(opBrgDeg)}° (${opCard})`);
    }

    // Drone position — prefer live ctx, fall back to alert row
    const dLat = (liveCtx ? drone.drone_lat : null) ?? a.drone_lat;
    const dLon = (liveCtx ? drone.drone_lon : null) ?? a.drone_lon;
    if (dLat && dLon) {
      const mgrsStr = toMGRS(dLat, dLon);
      const coordStr = `${dLat.toFixed(5)},${dLon.toFixed(5)}`;
      parts.push(`drone ${coordStr}` + (mgrsStr ? ` (MGRS ${mgrsStr})` : ''));
    }

    // Operator position — prefer live ctx derived, fall back to alert cols
    const opLat = (liveCtx ? drone.operator_lat : null) ?? a.operator_lat;
    const opLon = (liveCtx ? drone.operator_lon : null) ?? a.operator_lon;
    if (opLat && opLon) {
      const opMgrs = toMGRS(opLat, opLon);
      const opCoord = `${opLat.toFixed(5)},${opLon.toFixed(5)}`;
      parts.push(`pilot ${opCoord}` + (opMgrs ? ` (MGRS ${opMgrs})` : ''));
    }

    // Registration — "none broadcast" when empty
    const reg = (liveCtx ? drone.registration_id : null) ?? a.registration_id ?? '';
    if (reg) parts.push(`reg ${reg}`);

    // Timestamp
    const ts = a.timestamp || (liveCtx ? drone.last_seen : null);
    if (ts) parts.push(`as of ${formatZulu(ts)}`);

    return parts.join(' · ');
  }

  // ---- Expose public API ----
  return {
    relativeTime,
    formatTime,
    formatDateTime,
    formatLocal,
    formatZulu,
    toLocalDatetimeInput,
    fromLocalDatetimeInput,
    getUnits,
    setUnits,
    syncUnitsFromSettings,
    toggleUnits,
    formatAltUnit,
    formatSpeedUnit,
    formatAlt,
    formatAltOrAbsent,
    formatSpeed,
    formatDistance,
    formatRange,
    formatRangeDual,
    formatAltDual,
    formatBearing,
    formatRssi,
    formatBytes,
    formatDuration,
    rssiBarHtml,
    rssiToLevel,
    uaTypeHtml,
    protocolLabel,
    toast,
    shortSerial,
    altBadge,
    escapeHtml,
    haversine,
    bearing,
    bearingCardinal,
    toMGRS,
    mapsPushpinUrl,
    mapsLinkHtml,
    copyToClipboard,
    buildCallout,
  };
})();
