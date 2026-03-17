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

    // Unit toggle
    _initUnitToggle();

    // Init sub-modules
    MapManager.init(_onDroneMapClick);

    TableManager.init((serial, drone) => {
      _selectedSerial = serial;
      if (serial && drone) {
        MapManager.selectDrone(serial, drone);
        _fetchTrackAndShowDetail(serial);
      } else {
        // Fix #18: also clear _selectedTrack on deselect
        _selectedTrack = null;
        MapManager.clearTrack();
        TableManager.hideDetailSidebar();
      }
    });

    AlertsManager.init();

    ReplayManager.init((records, timeMs) => {
      // Replay time update — render snapshot on map and table
      // Pass currentTimeMs so map filters tracks correctly (Fix #10)
      _renderReplaySnapshot(records, timeMs);
    });

    SettingsManager.init();

    // Close detail sidebar button
    document.getElementById('btnCloseDetail')?.addEventListener('click', () => {
      _selectedSerial = null;
      // Fix #18: reset _selectedTrack when closing detail sidebar
      _selectedTrack = null;
      TableManager.clearSelection();
      MapManager.clearTrack();
      TableManager.hideDetailSidebar();
    });

    // Geozones
    if (typeof GeozoneManager !== 'undefined') {
      GeozoneManager.init(MapManager.getMap());
      document.getElementById('btnGeozones')?.addEventListener('click', () => {
        const on = GeozoneManager.toggle();
        document.getElementById('btnGeozones')?.classList.toggle('text-info', on);
      });
      // Set initial button state
      document.getElementById('btnGeozones')?.classList.toggle('text-info', true);
    }

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

  // ---- Unit toggle (metric / imperial) ----
  function _initUnitToggle() {
    const btn = document.getElementById('btnUnitToggle');
    if (!btn) return;

    // Set initial label
    btn.textContent = Utils.getUnits() === 'imperial' ? 'ft' : 'm';

    btn.addEventListener('click', () => {
      const next = Utils.toggleUnits();
      btn.textContent = next === 'imperial' ? 'ft' : 'm';
      // Refresh all unit-aware displays
      TableManager.refreshUnits();
      // Force a drone re-render if we have data (popups will update on next poll)
    });
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
        // Use interface from settings
        const cfg = await Api.getSettings();
        const iface = cfg.monitor_interface || '';
        if (!iface) {
          Utils.toast('Set a monitor interface in Settings first.', 'alert');
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
      // GPS UI is updated exclusively from the drones poll (has receiver.source)

      // Show monitor health warning if the adapter isn't delivering frames
      if (status.monitor_warning) {
        _showMonitorWarning(status.monitor_warning);
      } else {
        _clearMonitorWarning();
      }
    } catch (e) { /* ignore */ }
  }

  let _monitorWarningShown = false;
  function _showMonitorWarning(msg) {
    if (_monitorWarningShown) return;
    _monitorWarningShown = true;
    Utils.toast(msg, 'warning');
    // Also show a persistent banner below the navbar
    let banner = document.getElementById('monitorWarningBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'monitorWarningBanner';
      banner.className = 'monitor-warning-banner';
      banner.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> ' + msg;
      const main = document.querySelector('.main-content') || document.body;
      main.insertBefore(banner, main.firstChild);
    }
  }
  function _clearMonitorWarning() {
    if (!_monitorWarningShown) return;
    _monitorWarningShown = false;
    const banner = document.getElementById('monitorWarningBanner');
    if (banner) banner.remove();
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

      // Fix #15: update GPS UI with mode from receiver.source (e.g. 'gpsd', 'static', 'none')
      if (receiver) _updateGpsUi(receiver.gps_fix, receiver.source);

      // Load geozones when receiver position is known
      if (receiver && receiver.lat && receiver.lon && typeof GeozoneManager !== 'undefined') {
        GeozoneManager.loadData(receiver.lat, receiver.lon);
      }

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
      // Fix #18: reset _selectedTrack on map deselect
      _selectedTrack = null;
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
      // Fix #24: invalidate map size after sidebar opens (sidebar transition = 250ms, add a bit more)
      MapManager.invalidateSizeDelayed(300);
    } catch (e) {
      // Use cached drone data if detail endpoint fails
      _selectedTrack = null;
    }
  }

  // ---- Replay snapshot rendering ----
  function _renderReplaySnapshot(records, timeMs) {
    const receiverLat = null; // will use existing receiver marker
    const receiverLon = null;
    // Fix #10: pass currentTimeMs so renderReplaySnapshot filters tracks correctly
    MapManager.renderReplaySnapshot(records, receiverLat, receiverLon, timeMs);

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
    const handle       = document.getElementById('panelResizeHandle');
    const mapContainer = document.getElementById('mapContainer');
    const bottomPanel  = document.getElementById('bottomPanel');
    if (!handle || !mapContainer || !bottomPanel) return;

    let startY, startMapH, startPanelH;

    handle.addEventListener('mousedown', e => {
      e.preventDefault();
      startY      = e.clientY;
      startMapH   = mapContainer.offsetHeight;
      startPanelH = bottomPanel.offsetHeight;
      document.body.style.cursor    = 'ns-resize';
      document.body.style.userSelect = 'none';
      handle.classList.add('active');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup',   onUp);
    });

    function onMove(e) {
      const dy      = e.clientY - startY;
      const totalH  = startMapH + startPanelH;
      const newMapH = Math.max(100, Math.min(totalH - 100, startMapH + dy));
      mapContainer.style.flex = `0 0 ${newMapH}px`;
      bottomPanel.style.flex  = `0 0 ${totalH - newMapH}px`;
      // Tell Leaflet the map size changed
      if (typeof MapManager !== 'undefined' && MapManager.invalidateSizeDelayed) {
        MapManager.invalidateSizeDelayed(0);
      }
    }

    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
      document.body.style.cursor    = '';
      document.body.style.userSelect = '';
      handle.classList.remove('active');
      // Final Leaflet resize
      if (typeof MapManager !== 'undefined' && MapManager.invalidateSizeDelayed) {
        MapManager.invalidateSizeDelayed(100);
      }
    }

    // Keyboard support: ArrowUp/ArrowDown adjust by 20px steps
    handle.addEventListener('keydown', e => {
      const step = 20;
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      const dy      = e.key === 'ArrowUp' ? -step : step;
      const mapH    = mapContainer.offsetHeight;
      const panelH  = bottomPanel.offsetHeight;
      const totalH  = mapH + panelH;
      const newMapH = Math.max(100, Math.min(totalH - 100, mapH + dy));
      mapContainer.style.flex = `0 0 ${newMapH}px`;
      bottomPanel.style.flex  = `0 0 ${totalH - newMapH}px`;
      if (typeof MapManager !== 'undefined' && MapManager.invalidateSizeDelayed) {
        MapManager.invalidateSizeDelayed(50);
      }
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
