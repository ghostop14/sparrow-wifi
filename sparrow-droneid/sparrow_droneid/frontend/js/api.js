/* ============================================================
   api.js — HTTP client with bearer token management
   All endpoints from the Sparrow DroneID API v1.0.0
   ============================================================ */

const Api = (() => {

  const BASE = '/api/v1';
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
        // OpenAPI error format: { error: { code, message } }
        const err = data.error || {};
        throw new ApiError(err.code || `HTTP_${resp.status}`, err.message || `HTTP ${resp.status}`);
      }

      return data;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError('NETWORK_ERROR', err.message || 'Network error');
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

  function acknowledgeAlert(id, operator = '') {
    return put(`/alerts/${id}/acknowledge`, { operator });
  }

  function acknowledgeAllAlerts(operator = '') {
    return put('/alerts/acknowledge', { operator });
  }

  // ---- Geozones ----

  function getGeozoneAirports(lat, lon, radiusMi = 50) {
    return get('/geozones/airports', { lat, lon, radius_mi: radiusMi });
  }

  function getGeozoneNofly(lat, lon) {
    return get('/geozones/nofly', { lat, lon });
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

  // ---- Vendor Codes ----

  function getVendorCodes() {
    return get('/vendor-codes');
  }

  function putVendorCodes(data) {
    return put('/vendor-codes', data);
  }

  function updateVendorCodes() {
    return post('/vendor-codes/update');
  }

  // ---- WiFi SSID Detection ----

  function getWifiSsidPatterns() {
    return get('/wifi-ssid/patterns');
  }

  function putWifiSsidPatterns(patterns) {
    return put('/wifi-ssid/patterns', { patterns });
  }

  function getWifiSsidStatus() {
    return get('/wifi-ssid/status');
  }

  // ---- Certificates ----

  function getCerts() {
    return get('/certs');
  }

  function getCertDetail(name) {
    return get(`/certs/${encodeURIComponent(name)}`);
  }

  function generateSelfSigned(data) {
    return post('/certs/self-signed', data);
  }

  function generateCSR(data) {
    return post('/certs/csr', data);
  }

  function importCert(data) {
    return post('/certs/import', data);
  }

  function deleteCert(name) {
    return request('DELETE', `/certs/${encodeURIComponent(name)}`);
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
    acknowledgeAlert,
    acknowledgeAllAlerts,
    getGeozoneAirports,
    getGeozoneNofly,
    getGps,
    getCotStatus,
    putCotConfig,
    getDataStats,
    purgeData,
    purgeTiles,
    getSettings,
    putSettings,
    getVendorCodes,
    putVendorCodes,
    updateVendorCodes,
    getWifiSsidPatterns,
    putWifiSsidPatterns,
    getWifiSsidStatus,
    getCerts,
    getCertDetail,
    generateSelfSigned,
    generateCSR,
    importCert,
    deleteCert,
    post,
    ApiError,
  };
})();
