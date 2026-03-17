/* ============================================================
   app.js — Main controller: polling, view routing, startup
   Coordinates all modules: Map, Table, Alerts, Replay, Settings
   ============================================================ */

const App = (() => {

  // ---- State ----
  let _monitoring = false;
  let _inReplay = false;
  let _interfaces = [];
  let _pollTimer = null;
  let _alertPollTimer = null;
  let _statusPollTimer = null;
  let _selectedSerial = null;
  let _selectedTrack = null;

  const POLL_INTERVAL_MS  = 2000;
  const ALERT_POLL_MS     = 5000;
  const STATUS_POLL_MS    = 5000;

  // ---- Init ----
  async function init() {
    // Theme
    _initTheme();

    // Init sub-modules
    MapManager.init(_onDroneMapClick);

    TableManager.init((serial, drone) => {
      _selectedSerial = serial;
      if (serial && drone) {
        MapManager.selectDrone(serial, drone);
        _fetchTrackAndShowDetail(serial);
      } else {
        MapManager.clearTrack();
        TableManager.hideDetailSidebar();
      }
    });

    AlertsManager.init();

    ReplayManager.init((records, timeMs) => {
      // Replay time update — render snapshot on map and table
      _renderReplaySnapshot(records, timeMs);
    });

    SettingsManager.init();

    // Close detail sidebar button
    document.getElementById('btnCloseDetail')?.addEventListener('click', () => {
      _selectedSerial = null;
      TableManager.clearSelection();
      MapManager.clearTrack();
      TableManager.hideDetailSidebar();
    });

    // Range rings toggle
    document.getElementById('btnRangeRings')?.addEventListener('click', () => {
      const on = MapManager.toggleRangeRings();
      const btn = document.getElementById('btnRangeRings');
      btn.classList.toggle('text-info', on);
    });

    // Panel collapse
    document.getElementById('btnPanelCollapse')?.addEventListener('click', togglePanelCollapse);

    // Panel resize handle
    _initResizeHandle();

    // Monitor button
    document.getElementById('btnMonitor')?.addEventListener('click', _toggleMonitoring);

    // Replay tab — pause live polling when switched to
    document.getElementById('tab-replay')?.addEventListener('shown.bs.tab', () => {
      _inReplay = true;
      _stopPolling();
      document.getElementById('replayIndicator').style.display = '';
    });

    document.getElementById('tab-drones')?.addEventListener('shown.bs.tab', () => {
      if (_inReplay) {
        _inReplay = false;
        ReplayManager.stop();
        MapManager.clearAll();
        document.getElementById('replayIndicator').style.display = 'none';
        _startPolling();
      }
    });

    document.getElementById('tab-alerts')?.addEventListener('shown.bs.tab', () => {
      if (_inReplay) {
        _inReplay = false;
        document.getElementById('replayIndicator').style.display = 'none';
        _startPolling();
      }
    });

    // Listen for replay play state
    document.addEventListener('replayPlayStateChanged', e => {
      const label = document.getElementById('replayTimeLabel');
      if (e.detail.playing) {
        if (label) label.textContent = 'Replaying…';
      }
    });

    // Load interfaces
    await _loadInterfaces();

    // Initial status poll
    await _pollStatus();

    // Start polling
    _startPolling();
  }

  // ---- Theme ----
  function _initTheme() {
    const stored = localStorage.getItem('sparrow_theme') || 'dark';
    _applyTheme(stored);

    document.getElementById('btnTheme')?.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      _applyTheme(next);
      localStorage.setItem('sparrow_theme', next);
    });
  }

  function _applyTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    const icon = document.getElementById('themeIcon');
    if (icon) icon.className = theme === 'dark' ? 'bi bi-moon-fill' : 'bi bi-sun-fill';
  }

  // ---- Interfaces ----
  async function _loadInterfaces() {
    try {
      const resp = await Api.getInterfaces();
      _interfaces = resp.interfaces || [];
      const select = document.getElementById('ifaceSelect');
      if (!select) return;

      select.innerHTML = '<option value="">-- interface --</option>';
      _interfaces.forEach(iface => {
        const opt = document.createElement('option');
        opt.value = iface.name;
        opt.textContent = iface.name + (iface.monitor_capable ? '' : ' (no mon)');
        if (!iface.monitor_capable) opt.disabled = true;
        select.appendChild(opt);
      });

      // Auto-select if only one capable interface
      const capable = _interfaces.filter(i => i.monitor_capable);
      if (capable.length === 1) select.value = capable[0].name;
    } catch (e) {
      // Server may not be up yet — silently ignore
    }
  }

  // ---- Monitoring ----
  async function _toggleMonitoring() {
    const btn = document.getElementById('btnMonitor');
    const label = document.getElementById('btnMonitorLabel');
    if (!btn) return;
    btn.disabled = true;

    try {
      if (_monitoring) {
        await Api.stopMonitor();
        _monitoring = false;
        Utils.toast('Monitoring stopped.', 'info');
      } else {
        const iface = document.getElementById('ifaceSelect')?.value;
        if (!iface) {
          Utils.toast('Select an interface first.', 'alert');
          btn.disabled = false;
          return;
        }
        await Api.startMonitor(iface);
        _monitoring = true;
        Utils.toast(`Monitoring started on ${iface}.`, 'success');
      }
      _updateMonitorUi();
    } catch (e) {
      Utils.toast('Monitor error: ' + e.message, 'alert');
    } finally {
      btn.disabled = false;
    }
  }

  function _updateMonitorUi() {
    const btn = document.getElementById('btnMonitor');
    const label = document.getElementById('btnMonitorLabel');
    const dot = document.getElementById('monitorDot');
    const monLabel = document.getElementById('monitorLabel');

    if (_monitoring) {
      btn?.classList.add('monitoring');
      if (label) label.textContent = 'Stop';
      dot?.classList.add('active');
      if (monLabel) monLabel.textContent = 'Monitoring';
    } else {
      btn?.classList.remove('monitoring');
      if (label) label.textContent = 'Start';
      dot?.classList.remove('active');
      if (monLabel) monLabel.textContent = 'Idle';
    }
  }

  // ---- Status polling ----
  async function _pollStatus() {
    try {
      const status = await Api.getStatus();
      _monitoring = status.monitoring;
      _updateMonitorUi();
      _updateGpsUi(status.gps_fix, null);
    } catch (e) { /* ignore */ }
  }

  function _updateGpsUi(fix, mode) {
    const el = document.getElementById('gpsLabel');
    if (!el) return;
    if (mode === 'static') { el.textContent = 'GPS: static'; return; }
    if (mode === 'none')   { el.textContent = 'GPS: off'; return; }
    el.textContent = fix ? 'GPS: fix' : 'GPS: no fix';
    el.style.color = fix ? 'var(--success-color)' : 'var(--text-secondary)';
  }

  // ---- Live drone polling ----
  async function _pollDrones() {
    if (_inReplay) return;
    try {
      const resp = await Api.getDrones();
      const drones = resp.drones || [];
      const receiver = resp.receiver;

      MapManager.updateDrones(drones, receiver);
      TableManager.update(drones);

      if (receiver) _updateGpsUi(receiver.gps_fix, receiver.source);

      // Re-fetch track for selected drone if still visible
      if (_selectedSerial) {
        const still = drones.find(d => d.serial_number === _selectedSerial);
        if (still && !_selectedTrack) {
          _fetchTrackAndShowDetail(_selectedSerial);
        }
      }
    } catch (e) { /* polling — ignore transient errors */ }
  }

  async function _pollAlerts() {
    if (_inReplay) return;
    try {
      const resp = await Api.getAlertLog({ limit: 50 });
      AlertsManager.update(resp.alerts || []);
    } catch (e) { /* ignore */ }
  }

  function _startPolling() {
    _stopPolling();
    _pollDrones();
    _pollAlerts();
    _pollStatus();

    _pollTimer       = setInterval(_pollDrones,  POLL_INTERVAL_MS);
    _alertPollTimer  = setInterval(_pollAlerts,  ALERT_POLL_MS);
    _statusPollTimer = setInterval(_pollStatus,  STATUS_POLL_MS);
  }

  function _stopPolling() {
    if (_pollTimer)       { clearInterval(_pollTimer);       _pollTimer = null; }
    if (_alertPollTimer)  { clearInterval(_alertPollTimer);  _alertPollTimer = null; }
    if (_statusPollTimer) { clearInterval(_statusPollTimer); _statusPollTimer = null; }
  }

  // ---- Map drone click callback ----
  async function _onDroneMapClick(serial) {
    if (!serial) {
      _selectedSerial = null;
      TableManager.clearSelection();
      MapManager.clearTrack();
      TableManager.hideDetailSidebar();
      return;
    }
    _selectedSerial = serial;
    TableManager.selectDrone(serial);
    _fetchTrackAndShowDetail(serial);

    // Switch to drones tab if on another tab
    const dronesTab = document.getElementById('tab-drones');
    if (dronesTab && !dronesTab.classList.contains('active')) {
      bootstrap.Tab.getOrCreateInstance(dronesTab).show();
    }
  }

  async function _fetchTrackAndShowDetail(serial) {
    try {
      const resp = await Api.getDroneDetail(serial, 10);
      _selectedTrack = resp.track || [];
      TableManager.showDetailSidebar(resp.drone, _selectedTrack);
      if (_selectedTrack.length > 1) {
        MapManager.showTrack(_selectedTrack);
      }
    } catch (e) {
      // Use cached drone data if detail endpoint fails
      _selectedTrack = null;
    }
  }

  // ---- Replay snapshot rendering ----
  function _renderReplaySnapshot(records, timeMs) {
    const receiverLat = null; // will use existing receiver marker
    const receiverLon = null;
    MapManager.renderReplaySnapshot(records, receiverLat, receiverLon);

    const timeLabel = document.getElementById('replayTimeLabel');
    if (timeLabel) timeLabel.textContent = Utils.formatDateTime(new Date(timeMs).toISOString());
  }

  // ---- Panel collapse ----
  function togglePanelCollapse() {
    const panel = document.getElementById('bottomPanel');
    const icon  = document.getElementById('panelCollapseIcon');
    if (!panel) return;
    const collapsed = panel.classList.toggle('collapsed');
    if (icon) icon.className = collapsed ? 'bi bi-chevron-up' : 'bi bi-chevron-down';
  }

  // ---- Panel resize handle ----
  function _initResizeHandle() {
    const handle = document.getElementById('panelResizeHandle');
    const layout = document.getElementById('appLayout');
    const panel  = document.getElementById('bottomPanel');
    if (!handle || !layout || !panel) return;

    let dragging = false;
    let startY = 0;
    let startH = 0;

    handle.addEventListener('mousedown', e => {
      dragging = true;
      startY = e.clientY;
      startH = panel.getBoundingClientRect().height;
      document.body.style.cursor = 'ns-resize';
      document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const delta = startY - e.clientY; // dragging up increases panel height
      const newH = Math.max(160, Math.min(window.innerHeight * 0.75, startH + delta));
      panel.style.flex = `0 0 ${newH}px`;
    });

    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
  }

  // ---- Document ready ----
  document.addEventListener('DOMContentLoaded', () => {
    init().catch(e => console.error('App init error:', e));
  });

  return {
    init,
    startPolling: _startPolling,
    stopPolling: _stopPolling,
  };
})();
