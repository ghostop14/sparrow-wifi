/* ============================================================
   api.js — HTTP client with bearer token management
   All endpoints from the Sparrow DroneID API v1.0.0
   ============================================================ */

const Api = (() => {

  const BASE = '/api';
  const TOKEN_KEY = 'sparrow_auth_token';

  // ---- Token management ----

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
  }

  function setToken(token) {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  }

  // ---- Core fetch wrapper ----

  async function request(method, path, body = null, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const init = { method, headers };
    if (body !== null) init.body = JSON.stringify(body);

    try {
      const resp = await fetch(`${BASE}${path}`, init);

      // Binary / non-JSON responses (e.g. tile proxy, KML download)
      if (options.raw) return resp;

      const data = await resp.json();

      if (!resp.ok) {
        const msg = data.errmsg || `HTTP ${resp.status}`;
        throw new ApiError(data.errcode ?? resp.status, msg);
      }

      if (data.errcode && data.errcode !== 0) {
        throw new ApiError(data.errcode, data.errmsg || 'Unknown error');
      }

      return data;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError(-1, err.message || 'Network error');
    }
  }

  function get(path, params = {}, options = {}) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    });
    const query = qs.toString() ? `?${qs}` : '';
    return request('GET', `${path}${query}`, null, options);
  }

  function post(path, body = {}) {
    return request('POST', path, body);
  }

  function put(path, body = {}) {
    return request('PUT', path, body);
  }

  // ---- System ----

  function getStatus() {
    return get('/status');
  }

  // ---- Monitoring ----

  function getInterfaces() {
    return get('/interfaces');
  }

  function startMonitor(iface) {
    return post('/monitor/start', { interface: iface });
  }

  function stopMonitor() {
    return post('/monitor/stop');
  }

  function getMonitorStatus() {
    return get('/monitor/status');
  }

  // ---- Detections ----

  function getDrones(maxAge = 180) {
    return get('/drones', { max_age: maxAge });
  }

  function getDroneDetail(serial, trackMinutes = 5) {
    return get(`/drones/${encodeURIComponent(serial)}`, { track_minutes: trackMinutes });
  }

  // ---- History / Replay ----

  function getHistory(from, to, serial = null, limit = 10000, offset = 0) {
    const params = { from, to, limit, offset };
    if (serial) params.serial = serial;
    return get('/history', params);
  }

  function getHistorySerials(from, to) {
    return get('/history/serials', { from, to });
  }

  function getHistoryTimeline(from, to, bucketSeconds = 10) {
    return get('/history/timeline', { from, to, bucket_seconds: bucketSeconds });
  }

  // ---- Export ----

  function exportKml(from, to, serial = null) {
    const params = { from, to };
    if (serial) params.serial = serial;
    const qs = new URLSearchParams(params).toString();
    const token = getToken();
    // Build URL for direct download trigger
    let url = `${BASE}/export/kml?${qs}`;
    if (token) url += `&_token=${encodeURIComponent(token)}`;
    return url;
  }

  // ---- Alerts ----

  function getAlertConfig() {
    return get('/alerts/config');
  }

  function putAlertConfig(config) {
    return put('/alerts/config', config);
  }

  function getAlertLog(params = {}) {
    return get('/alerts/log', params);
  }

  // ---- GPS ----

  function getGps() {
    return get('/gps');
  }

  // ---- CoT ----

  function getCotStatus() {
    return get('/cot/status');
  }

  function putCotConfig(config) {
    return put('/cot/config', config);
  }

  // ---- Data maintenance ----

  function getDataStats() {
    return get('/data/stats');
  }

  function purgeData(before) {
    return post('/data/purge', { before });
  }

  function purgeTiles(source = null) {
    const body = source ? { source } : {};
    return post('/data/purge-tiles', body);
  }

  // ---- Settings ----

  function getSettings() {
    return get('/settings');
  }

  function putSettings(changes) {
    return put('/settings', changes);
  }

  // ---- Error class ----

  class ApiError extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
      this.name = 'ApiError';
    }
  }

  return {
    getToken,
    setToken,
    getStatus,
    getInterfaces,
    startMonitor,
    stopMonitor,
    getMonitorStatus,
    getDrones,
    getDroneDetail,
    getHistory,
    getHistorySerials,
    getHistoryTimeline,
    exportKml,
    getAlertConfig,
    putAlertConfig,
    getAlertLog,
    getGps,
    getCotStatus,
    putCotConfig,
    getDataStats,
    purgeData,
    purgeTiles,
    getSettings,
    putSettings,
    ApiError,
  };
})();
