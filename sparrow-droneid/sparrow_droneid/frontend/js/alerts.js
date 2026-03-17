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

  // ---- Render alert list ----

  function _renderAlertList() {
    const list = document.getElementById('alertsList');
    if (!list) return;

    if (_alerts.length === 0) {
      list.innerHTML = `<div class="alerts-empty text-center text-secondary py-4">
        <i class="bi bi-bell-slash fs-3 d-block mb-2 opacity-25"></i>No alerts</div>`;
      _updateTabBadge(0);
      return;
    }

    const html = _alerts.map(a => {
      const icon = _alertIcon(a.alert_type);
      return `
        <div class="alert-item">
          <i class="bi ${icon} alert-icon"></i>
          <div class="alert-content">
            <div class="alert-title">${_esc(_alertTitle(a.alert_type))}</div>
            <div class="alert-detail">${_esc(a.detail || '')}</div>
            <div style="font-size:11px;color:#64748B;margin-top:2px;">
              ${_esc(Utils.shortSerial(a.serial_number))}
              ${a.drone_height_agl ? ` &mdash; ${Math.round(a.drone_height_agl)} m AGL` : ''}
            </div>
          </div>
          <span class="alert-time">${Utils.relativeTime(a.timestamp)}</span>
        </div>`;
    }).join('');

    list.innerHTML = html;
    _updateTabBadge(_alerts.length);
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

  function update(newAlerts) {
    if (!newAlerts || !newAlerts.length) return;

    // Detect new alerts (not yet seen)
    const freshAlerts = newAlerts.filter(a => !_seenIds.has(a.id));

    // Prepend new alerts to display list
    _alerts = [...newAlerts.slice(0, 100)]; // cap display at 100
    newAlerts.forEach(a => _seenIds.add(a.id));

    // Notify for fresh alerts
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
    _alerts = [];
    _seenIds.clear();
    _renderAlertList();
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

    let paramHtml = '';
    if (hasMax && rule.params.max_altitude_m !== undefined) {
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" value="${rule.params.max_altitude_m}" title="Max altitude (m)"> m`;
    } else if (hasMax && rule.params.max_speed_mps !== undefined) {
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" value="${rule.params.max_speed_mps}" title="Max speed (m/s)"> m/s`;
    } else if (hasTimeout) {
      paramHtml = `<input type="number" class="form-control form-control-sm ms-2" style="width:80px"
        id="rule_param_${rule.id}" value="${rule.params.timeout_seconds}" title="Timeout (s)"> s`;
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
        const val = parseFloat(paramEl.value);
        if (!isNaN(val)) {
          newRule.params = { ...rule.params };
          if (rule.params.max_altitude_m !== undefined) newRule.params.max_altitude_m = val;
          else if (rule.params.max_speed_mps !== undefined) newRule.params.max_speed_mps = val;
          else if (rule.params.timeout_seconds !== undefined) newRule.params.timeout_seconds = val;
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
    document.getElementById('btnClearAlerts')?.addEventListener('click', () => clearAlerts());
    document.getElementById('btnSaveAlertConfig')?.addEventListener('click', () => saveAlertConfig());

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
  };
})();
