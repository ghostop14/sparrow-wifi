/* ============================================================
   alerts.js — Alert display, Web Audio tones, and config modal
   ============================================================ */

const AlertsManager = (() => {

  let _alerts = [];
  let _seenIds = new Set();
  let _audioCtx = null;
  let _audioEnabled = true;
  let _visualEnabled = true;
  let _alertConfig = null;

  // ---- Web Audio ----

  function _getAudioCtx() {
    if (!_audioCtx) {
      _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    // Resume if suspended (browsers require user gesture)
    if (_audioCtx.state === 'suspended') {
      _audioCtx.resume().catch(() => {});
    }
    return _audioCtx;
  }

  // Short ascending two-note chime for "new drone" / info events
  function playChime() {
    if (!_audioEnabled) return;
    try {
      const ctx = _getAudioCtx();
      const now = ctx.currentTime;

      const notes = [659.25, 880.0]; // E5, A5
      notes.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now + i * 0.12);

        gain.gain.setValueAtTime(0, now + i * 0.12);
        gain.gain.linearRampToValueAtTime(0.18, now + i * 0.12 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 0.18);

        osc.start(now + i * 0.12);
        osc.stop(now + i * 0.12 + 0.2);
      });
    } catch (e) { /* audio unavailable */ }
  }

  // Warble/pulse tone for violation alerts
  function playAlertTone() {
    if (!_audioEnabled) return;
    try {
      const ctx = _getAudioCtx();
      const now = ctx.currentTime;

      for (let i = 0; i < 3; i++) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(440, now + i * 0.22);
        osc.frequency.linearRampToValueAtTime(330, now + i * 0.22 + 0.1);
        osc.frequency.linearRampToValueAtTime(440, now + i * 0.22 + 0.2);

        gain.gain.setValueAtTime(0, now + i * 0.22);
        gain.gain.linearRampToValueAtTime(0.14, now + i * 0.22 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.22 + 0.2);

        osc.start(now + i * 0.22);
        osc.stop(now + i * 0.22 + 0.22);
      }
    } catch (e) { /* audio unavailable */ }
  }

  function playSound(soundType) {
    if (soundType === 'alert') {
      playAlertTone();
    } else {
      playChime();
    }
  }

  // ---- Alert type helpers ----

  function _alertIcon(type) {
    switch (type) {
      case 'new_drone':    return 'bi-aircraft-horizontal new-drone';
      case 'altitude_max': return 'bi-arrow-up-circle altitude';
      case 'speed_max':    return 'bi-speedometer2 speed';
      case 'signal_lost':  return 'bi-wifi-off signal-lost';
      default:             return 'bi-bell';
    }
  }

  function _alertTitle(type) {
    switch (type) {
      case 'new_drone':    return 'New Drone';
      case 'altitude_max': return 'Altitude Violation';
      case 'speed_max':    return 'Speed Violation';
      case 'signal_lost':  return 'Signal Lost';
      default:             return 'Alert';
    }
  }

  function _alertToastType(type) {
    switch (type) {
      case 'new_drone':   return 'drone';
      case 'altitude_max':
      case 'speed_max':   return 'alert';
      default:            return 'info';
    }
  }

  // ---- Unit-aware detail formatting ----
  function _localizeDetail(detail) {
    if (Utils.getUnits && Utils.getUnits() === 'imperial') {
      // Convert "X m/s" → "X mph" and "X m" → "X ft"
      return detail
        .replace(/(\d+\.?\d*)\s*m\/s/g, (_, v) => (parseFloat(v) * 2.23694).toFixed(1) + ' mph')
        .replace(/(\d+\.?\d*)\s*m\b/g, (_, v) => Math.round(parseFloat(v) * 3.28084) + ' ft');
    }
    return detail;
  }

  // ---- Filter state ----
  // 'active' shows only ACTIVE alerts; 'all' shows everything
  let _filterMode = 'active';

  // ---- Render alert list ----

  function _renderAlertList() {
    const list = document.getElementById('alertsList');
    if (!list) return;

    // Apply filter
    const visible = _filterMode === 'all'
      ? _alerts
      : _alerts.filter(a => (a.state || 'ACTIVE') === 'ACTIVE');

    if (visible.length === 0) {
      const emptyMsg = _filterMode === 'active' && _alerts.length > 0
        ? 'All alerts acknowledged'
        : 'No alerts';
      list.innerHTML = `<div class="alerts-empty text-center text-secondary py-4">
        <i class="bi bi-bell-slash fs-3 d-block mb-2 opacity-25"></i>${_esc(emptyMsg)}</div>`;
      _updateTabBadge(_alerts.filter(a => (a.state || 'ACTIVE') === 'ACTIVE').length);
      return;
    }

    const html = visible.map(a => {
      const state = a.state || 'ACTIVE';
      const icon = _alertIcon(a.alert_type);

      let stateClass = '';
      let stateExtra = '';
      if (state === 'ACKNOWLEDGED') {
        stateClass = 'acked';
        stateExtra = `<span style="font-size:10px;color:var(--text-muted);margin-left:4px;" title="Acknowledged${a.acknowledged_by ? ' by ' + _esc(a.acknowledged_by) : ''}">&#10003; acked</span>`;
      } else if (state === 'RESOLVED') {
        stateClass = 'resolved';
        stateExtra = `<span style="font-size:10px;color:var(--text-muted);margin-left:4px;">&#10003; resolved</span>`;
      }

      const ackBtn = state === 'ACTIVE'
        ? `<button class="btn-ack" title="Acknowledge" onclick="AlertsManager._ackOne(${a.id})">
             <i class="bi bi-check-lg"></i>
           </button>`
        : '';

      return `
        <div class="alert-item ${stateClass}">
          <i class="bi ${icon} alert-icon"></i>
          <div class="alert-content">
            <div class="alert-title">
              ${_esc(_alertTitle(a.alert_type))}${stateExtra}
            </div>
            <div class="alert-detail">${_esc(_localizeDetail(a.detail || ''))}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">
              ${_esc(Utils.shortSerial(a.serial_number))}
              ${a.drone_height_agl ? ` &mdash; ${Utils.formatAlt(a.drone_height_agl)} AGL` : ''}
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
            <span class="alert-time">${Utils.relativeTime(a.timestamp)}</span>
            ${ackBtn}
          </div>
        </div>`;
    }).join('');

    list.innerHTML = html;
    // Badge shows count of ACTIVE (unacknowledged) alerts regardless of filter
    _updateTabBadge(_alerts.filter(a => (a.state || 'ACTIVE') === 'ACTIVE').length);
  }

  function _updateTabBadge(count) {
    const badge = document.getElementById('alertTabCount');
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count;
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  }

  // ---- Poll / Update ----

  let _initialLoadDone = false;

  function update(newAlerts) {
    if (!newAlerts || !newAlerts.length) return;

    // On first load, seed _seenIds from DB history — no toasts/sounds
    if (!_initialLoadDone) {
      _initialLoadDone = true;
      newAlerts.forEach(a => _seenIds.add(a.id));
      _alerts = [...newAlerts.slice(0, 100)];
      _renderAlertList();
      return;
    }

    // Detect new ACTIVE alerts not yet seen (acknowledged/resolved alerts never get toasts)
    const freshAlerts = newAlerts.filter(
      a => !_seenIds.has(a.id) && (a.state || 'ACTIVE') === 'ACTIVE'
    );

    // Update display list (cap at 100)
    _alerts = [...newAlerts.slice(0, 100)];
    newAlerts.forEach(a => _seenIds.add(a.id));

    // Notify for fresh ACTIVE alerts only
    freshAlerts.forEach(a => {
      // Visual toast
      if (_visualEnabled) {
        Utils.toast(
          a.detail || _alertTitle(a.alert_type),
          _alertToastType(a.alert_type),
          _alertTitle(a.alert_type),
          5000
        );
      }

      // Audio
      const ruleSound = _alertConfig?.rules?.find(r => r.type === a.alert_type)?.audio_sound || 'chime';
      playSound(ruleSound);
    });

    _renderAlertList();
  }

  function clearAlerts() {
    // Switch to 'active' filter which hides acknowledged/resolved alerts.
    // Don't clear _seenIds — acknowledged alerts must not re-trigger toasts.
    _filterMode = 'active';
    _updateFilterButtons();
    _renderAlertList();
  }

  function _updateFilterButtons() {
    const btnActive = document.getElementById('btnFilterActive');
    const btnAll    = document.getElementById('btnFilterAll');
    if (btnActive) btnActive.classList.toggle('active', _filterMode === 'active');
    if (btnAll)    btnAll.classList.toggle('active', _filterMode === 'all');
  }

  function _requireOperatorName() {
    let name = localStorage.getItem('sparrow_operator_name') || '';
    if (name) return name;
    name = (prompt('Enter your operator name / callsign:') || '').trim();
    if (!name) return null;  // user cancelled
    localStorage.setItem('sparrow_operator_name', name);
    // Sync to settings field if it's open
    const el = document.getElementById('s_operator_name');
    if (el) el.value = name;
    return name;
  }

  async function _ackOne(alertId) {
    const operator = _requireOperatorName();
    if (operator === null) return;
    try {
      await Api.acknowledgeAlert(alertId, operator);
      await _repoll();
    } catch (e) {
      Utils.toast('Acknowledge failed: ' + e.message, 'alert');
    }
  }

  async function _ackAll() {
    const operator = _requireOperatorName();
    if (operator === null) return;
    try {
      const result = await Api.acknowledgeAllAlerts(operator);
      await _repoll();
      if (result.count > 0) {
        Utils.toast(`${result.count} alert${result.count !== 1 ? 's' : ''} acknowledged.`, 'success');
      }
    } catch (e) {
      Utils.toast('Acknowledge all failed: ' + e.message, 'alert');
    }
  }

  async function _repoll() {
    try {
      const result = await Api.getAlertLog({ limit: 100 });
      if (result && result.alerts) {
        _alerts = result.alerts.slice(0, 100);
        _renderAlertList();
      }
    } catch (e) {
      // Non-fatal — stale display is acceptable
    }
  }

  function _esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ---- Alert config modal ----

  async function openConfigModal() {
    try {
      _alertConfig = await Api.getAlertConfig();
    } catch (e) {
      Utils.toast('Failed to load alert config: ' + e.message, 'alert');
      return;
    }

    const body = document.getElementById('alertConfigBody');
    if (!body) return;

    const cfg = _alertConfig;

    body.innerHTML = `
      <div class="mb-3">
        <div class="detail-section-title mb-2">Global Settings</div>
        <div class="settings-row">
          <div>
            <span class="settings-label">Audio Notifications</span>
            <span class="settings-hint">Play tone when alert fires</span>
          </div>
          <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" id="cfgAudioEnabled" ${cfg.audio_enabled ? 'checked' : ''}>
          </div>
        </div>
        <div class="settings-row">
          <div>
            <span class="settings-label">Visual Notifications</span>
            <span class="settings-hint">Show toast banner when alert fires</span>
          </div>
          <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" id="cfgVisualEnabled" ${cfg.visual_enabled ? 'checked' : ''}>
          </div>
        </div>
        <div class="settings-row">
          <div>
            <span class="settings-label">Script Notifications</span>
            <span class="settings-hint">Run external script on alert</span>
          </div>
          <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" id="cfgScriptEnabled" ${cfg.script_enabled ? 'checked' : ''}>
          </div>
        </div>
        <div class="settings-row">
          <span class="settings-label">Script Path</span>
          <input type="text" class="form-control form-control-sm" id="cfgScriptPath"
            style="max-width:240px;" value="${_esc(cfg.script_path || '')}" placeholder="/path/to/script.sh">
        </div>
      </div>

      <div class="detail-section-title mb-2">Alert Rules</div>
      <div id="cfgRulesContainer">
        ${(cfg.rules || []).map(rule => _buildRuleRow(rule)).join('')}
      </div>

      <div class="mt-3">
        <button class="btn btn-sm btn-outline-secondary" onclick="AlertsManager._testAudio()">
          <i class="bi bi-volume-up me-1"></i>Test Audio
        </button>
      </div>`;

    const modal = new bootstrap.Modal(document.getElementById('alertConfigModal'));
    modal.show();
  }

  function _buildRuleRow(rule) {
    const hasMax = rule.params && (rule.params.max_altitude_m !== undefined || rule.params.max_speed_mps !== undefined);
    const hasTimeout = rule.params && rule.params.timeout_seconds !== undefined;

    const imperial = Utils.getUnits && Utils.getUnits() === 'imperial';
    let paramHtml = '';
    if (hasMax && rule.params.max_altitude_m !== undefined) {
      const val = imperial ? Math.round(rule.params.max_altitude_m * 3.28084) : rule.params.max_altitude_m;
      const unit = imperial ? 'ft' : 'm';
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" data-param="altitude" value="${val}" title="Max altitude (${unit})"> ${unit}`;
    } else if (hasMax && rule.params.max_speed_mps !== undefined) {
      const val = imperial ? +(rule.params.max_speed_mps * 2.23694).toFixed(1) : rule.params.max_speed_mps;
      const unit = imperial ? 'mph' : 'm/s';
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" data-param="speed" value="${val}" title="Max speed (${unit})"> ${unit}`;
    } else if (hasTimeout) {
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" data-param="timeout" value="${rule.params.timeout_seconds}" title="Timeout (s)"> s`;
    }

    return `
      <div class="settings-row" id="cfgRule_${rule.id}">
        <div class="d-flex align-items-center gap-2 flex-grow-1">
          <div class="form-check form-switch mb-0">
            <input class="form-check-input" type="checkbox" id="rule_enabled_${rule.id}" ${rule.enabled ? 'checked' : ''}>
          </div>
          <span class="settings-label">${_esc(rule.name)}</span>
        </div>
        <div class="d-flex align-items-center">
          ${paramHtml}
        </div>
      </div>`;
  }

  async function saveAlertConfig() {
    if (!_alertConfig) return;

    const cfg = { ..._alertConfig };
    cfg.audio_enabled = document.getElementById('cfgAudioEnabled')?.checked ?? cfg.audio_enabled;
    cfg.visual_enabled = document.getElementById('cfgVisualEnabled')?.checked ?? cfg.visual_enabled;
    cfg.script_enabled = document.getElementById('cfgScriptEnabled')?.checked ?? cfg.script_enabled;
    cfg.script_path = document.getElementById('cfgScriptPath')?.value ?? cfg.script_path;

    cfg.rules = (cfg.rules || []).map(rule => {
      const enabled = document.getElementById(`rule_enabled_${rule.id}`)?.checked ?? rule.enabled;
      const paramEl = document.getElementById(`rule_param_${rule.id}`);
      const newRule = { ...rule, enabled };

      if (paramEl) {
        let val = parseFloat(paramEl.value);
        if (!isNaN(val)) {
          const imperial = Utils.getUnits && Utils.getUnits() === 'imperial';
          newRule.params = { ...rule.params };
          if (rule.params.max_altitude_m !== undefined) {
            newRule.params.max_altitude_m = imperial ? val / 3.28084 : val;
          } else if (rule.params.max_speed_mps !== undefined) {
            newRule.params.max_speed_mps = imperial ? val / 2.23694 : val;
          } else if (rule.params.timeout_seconds !== undefined) {
            newRule.params.timeout_seconds = val;
          }
        }
      }
      return newRule;
    });

    try {
      await Api.putAlertConfig(cfg);
      _alertConfig = cfg;
      _audioEnabled = cfg.audio_enabled;
      _visualEnabled = cfg.visual_enabled;

      bootstrap.Modal.getInstance(document.getElementById('alertConfigModal'))?.hide();
      Utils.toast('Alert configuration saved.', 'success');
    } catch (e) {
      Utils.toast('Failed to save: ' + e.message, 'alert');
    }
  }

  function _testAudio() {
    playChime();
    setTimeout(() => playAlertTone(), 600);
  }

  // ---- Init ----
  function init() {
    document.getElementById('btnAlertConfig')?.addEventListener('click', () => openConfigModal());
    document.getElementById('btnSaveAlertConfig')?.addEventListener('click', () => saveAlertConfig());

    // Repurpose the existing "Clear" button as "Acknowledge All"
    const btnClear = document.getElementById('btnClearAlerts');
    if (btnClear) {
      btnClear.innerHTML = '<i class="bi bi-check-all me-1"></i>Ack All';
      btnClear.title = 'Acknowledge all active alerts';
      btnClear.addEventListener('click', () => _ackAll());
    }

    // Inject filter toggle buttons into the alerts toolbar
    const toolbar = document.querySelector('#pane-alerts .alerts-toolbar');
    if (toolbar) {
      const filterGroup = document.createElement('div');
      filterGroup.className = 'd-flex gap-1';
      filterGroup.innerHTML = `
        <button class="btn btn-xs btn-outline-secondary active" id="btnFilterActive" title="Show active alerts only">Active</button>
        <button class="btn btn-xs btn-outline-secondary" id="btnFilterAll" title="Show all alerts">All</button>`;
      // Insert before the Configure button (the ms-auto element)
      const cfgBtn = document.getElementById('btnAlertConfig');
      if (cfgBtn) {
        toolbar.insertBefore(filterGroup, cfgBtn);
      } else {
        toolbar.appendChild(filterGroup);
      }

      document.getElementById('btnFilterActive')?.addEventListener('click', () => {
        _filterMode = 'active';
        _updateFilterButtons();
        _renderAlertList();
      });
      document.getElementById('btnFilterAll')?.addEventListener('click', () => {
        _filterMode = 'all';
        _updateFilterButtons();
        _renderAlertList();
      });
    }

    // Unlock audio on first user interaction
    document.addEventListener('click', () => {
      if (!_audioCtx) _getAudioCtx();
    }, { once: true });
  }

  return {
    init,
    update,
    clearAlerts,
    openConfigModal,
    saveAlertConfig,
    playChime,
    playAlertTone,
    _testAudio,
    _ackOne,
    _ackAll,
  };
})();
