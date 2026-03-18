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
      case 'dji_proprietary':return '<span class="badge bg-warning text-dark">DJI</span>';
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

  // ---- Expose public API ----
  return {
    relativeTime,
    formatTime,
    formatDateTime,
    toLocalDatetimeInput,
    fromLocalDatetimeInput,
    getUnits,
    setUnits,
    syncUnitsFromSettings,
    toggleUnits,
    formatAltUnit,
    formatSpeedUnit,
    formatAlt,
    formatSpeed,
    formatDistance,
    formatRange,
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
  };
})();
