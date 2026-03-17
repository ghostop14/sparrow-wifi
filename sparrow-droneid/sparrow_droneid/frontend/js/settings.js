/* ============================================================
   settings.js — Settings modal with all configuration sections
   ============================================================ */

const SettingsManager = (() => {

  let _settings = null;
  let _dataStats = null;

  // ---- Render modal ----
  async function open() {
    const body = document.getElementById('settingsBody');
    if (!body) return;

    body.innerHTML = `<div class="text-center py-4 text-secondary">
      <i class="bi bi-arrow-repeat spin fs-3"></i><br>Loading settings…</div>`;

    // Hide restart warning initially
    const warn = document.getElementById('restartWarning');
    if (warn) warn.style.setProperty('display', 'none', 'important');

    try {
      [_settings, _dataStats] = await Promise.all([
        Api.getSettings().then(r => r.settings),
        Api.getDataStats().catch(() => null),
      ]);
    } catch (e) {
      body.innerHTML = `<div class="text-danger p-3">Failed to load settings: ${e.message}</div>`;
      return;
    }

    body.innerHTML = _buildSettingsHtml(_settings, _dataStats);
    _attachSettingsListeners();
  }

  function _esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _buildSettingsHtml(s, stats) {
    return `
      <!-- Network -->
      <div class="settings-section-title">Network</div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Port</span>
          <span class="settings-hint">HTTP listen port (restart required)</span>
        </div>
        <input type="number" class="form-control form-control-sm" id="s_port" value="${s.port || 8097}" style="width:90px;" min="1" max="65535">
      </div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Bind Address</span>
          <span class="settings-hint">IP to bind to (restart required)</span>
        </div>
        <input type="text" class="form-control form-control-sm" id="s_bind" value="${_esc(s.bind_address || '0.0.0.0')}" style="width:140px;" placeholder="0.0.0.0">
      </div>
      <div class="settings-row">
        <div>
          <span class="settings-label">HTTPS</span>
          <span class="settings-hint">Enable HTTPS (restart required)</span>
        </div>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_https" ${s.https_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">TLS Cert Path</span>
        <input type="text" class="form-control form-control-sm" id="s_cert" value="${_esc(s.https_cert_path || '')}" style="width:200px;" placeholder="/etc/ssl/cert.pem">
      </div>
      <div class="settings-row">
        <span class="settings-label">TLS Key Path</span>
        <input type="text" class="form-control form-control-sm" id="s_key" value="${_esc(s.https_key_path || '')}" style="width:200px;" placeholder="/etc/ssl/key.pem">
      </div>

      <!-- Authentication -->
      <div class="settings-section-title">Authentication</div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Bearer Token</span>
          <span class="settings-hint">API token — write-only, leave blank to keep current</span>
        </div>
        <input type="password" class="form-control form-control-sm" id="s_token"
          value="" placeholder="${s.auth_token === '(set)' ? '(set — enter new to change)' : '(not set)'}" style="width:200px;" autocomplete="new-password">
      </div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Allowed IPs</span>
          <span class="settings-hint">Comma-separated IPs/CIDRs; empty = allow all</span>
        </div>
        <input type="text" class="form-control form-control-sm" id="s_allowed_ips"
          value="${_esc(s.allowed_ips || '')}" style="width:200px;" placeholder="192.168.1.0/24">
      </div>

      <!-- GPS -->
      <div class="settings-section-title">GPS / Receiver Position</div>
      <div class="settings-row">
        <span class="settings-label">GPS Mode</span>
        <select class="form-select form-select-sm" id="s_gps_mode" style="width:130px;">
          <option value="none"   ${s.gps_mode === 'none'   ? 'selected' : ''}>None</option>
          <option value="gpsd"   ${s.gps_mode === 'gpsd'   ? 'selected' : ''}>gpsd</option>
          <option value="static" ${s.gps_mode === 'static' ? 'selected' : ''}>Static</option>
        </select>
      </div>
      <div id="s_static_coords" style="display:${s.gps_mode === 'static' ? '' : 'none'}">
        <div class="settings-row">
          <span class="settings-label">Static Latitude</span>
          <input type="number" class="form-control form-control-sm" id="s_lat" value="${s.gps_static_lat || 0}" style="width:130px;" step="0.000001" min="-90" max="90">
        </div>
        <div class="settings-row">
          <span class="settings-label">Static Longitude</span>
          <input type="number" class="form-control form-control-sm" id="s_lon" value="${s.gps_static_lon || 0}" style="width:130px;" step="0.000001" min="-180" max="180">
        </div>
        <div class="settings-row">
          <span class="settings-label">Static Altitude (m)</span>
          <input type="number" class="form-control form-control-sm" id="s_alt" value="${s.gps_static_alt || 0}" style="width:100px;" step="0.1">
        </div>
      </div>

      <!-- Monitoring -->
      <div class="settings-section-title">Monitoring</div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Default Interface</span>
          <span class="settings-hint">Interface to start monitoring on launch</span>
        </div>
        <input type="text" class="form-control form-control-sm" id="s_iface"
          value="${_esc(s.monitor_interface || '')}" style="width:130px;" placeholder="wlan0">
      </div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Tile Cache</span>
          <span class="settings-hint">Cache map tiles locally for offline use</span>
        </div>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_tile_cache" ${s.tile_cache_enabled ? 'checked' : ''}>
        </div>
      </div>

      <!-- CoT -->
      <div class="settings-section-title">Cursor on Target (CoT)</div>
      <div class="settings-row">
        <div>
          <span class="settings-label">CoT Output</span>
          <span class="settings-hint">Multicast drone positions to TAK / ATAK</span>
        </div>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_cot_enabled" ${s.cot_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">CoT Address</span>
        <input type="text" class="form-control form-control-sm" id="s_cot_addr"
          value="${_esc(s.cot_address || '239.2.3.1')}" style="width:150px;" placeholder="239.2.3.1">
      </div>
      <div class="settings-row">
        <span class="settings-label">CoT Port</span>
        <input type="number" class="form-control form-control-sm" id="s_cot_port"
          value="${s.cot_port || 6969}" style="width:90px;" min="1" max="65535">
      </div>

      <!-- Alerts -->
      <div class="settings-section-title">Alerts</div>
      <div class="settings-row">
        <span class="settings-label">Audio Notifications</span>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_alert_audio" ${s.alert_audio_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">Visual Notifications</span>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_alert_visual" ${s.alert_visual_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">Script Notifications</span>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_alert_script" ${s.alert_script_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">Script Path</span>
        <input type="text" class="form-control form-control-sm" id="s_alert_script_path"
          value="${_esc(s.alert_script_path || '')}" style="width:220px;" placeholder="/path/to/alert.sh">
      </div>

      <!-- Slack -->
      <div class="settings-section-title">Slack Notifications</div>
      <div class="settings-row">
        <span class="settings-label">Enable Slack</span>
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="s_alert_slack" ${s.alert_slack_enabled ? 'checked' : ''}>
        </div>
      </div>
      <div class="settings-row">
        <span class="settings-label">Webhook URL</span>
        <input type="text" class="form-control form-control-sm" id="s_slack_webhook"
          value="${_esc(s.alert_slack_webhook_url || '')}" style="width:280px;" placeholder="https://hooks.slack.com/services/...">
      </div>
      <div class="settings-row">
        <span class="settings-label">Display Name</span>
        <input type="text" class="form-control form-control-sm" id="s_slack_name"
          value="${_esc(s.alert_slack_display_name || 'Sparrow DroneID')}" style="width:180px;">
      </div>
      <div class="settings-row">
        <span class="settings-label"></span>
        <button class="btn btn-sm btn-outline-secondary" id="btn_slack_test" title="Send a test message to Slack">
          Test Slack
        </button>
      </div>

      <!-- Data -->
      <div class="settings-section-title">Data &amp; Retention</div>
      <div class="settings-row">
        <div>
          <span class="settings-label">Retention Period</span>
          <span class="settings-hint">Auto-purge data older than this</span>
        </div>
        <div class="d-flex align-items-center gap-1">
          <input type="number" class="form-control form-control-sm" id="s_retention"
            value="${s.retention_days || 14}" style="width:70px;" min="1" max="365"> days
        </div>
      </div>

      ${stats ? `
      <div class="mt-2 p-3 rounded" style="background:var(--bg-tertiary);font-size:12px;">
        <div class="row g-2">
          <div class="col-6"><span class="text-secondary">DB Size:</span> ${Utils.formatBytes(stats.db_size_bytes)}</div>
          <div class="col-6"><span class="text-secondary">Tile Cache:</span> ${Utils.formatBytes(stats.tile_cache_size_bytes)}</div>
          <div class="col-6"><span class="text-secondary">Detections:</span> ${(stats.detection_count || 0).toLocaleString()}</div>
          <div class="col-6"><span class="text-secondary">Unique Drones:</span> ${stats.unique_serials || 0}</div>
          <div class="col-6"><span class="text-secondary">Alerts Logged:</span> ${stats.alert_count || 0}</div>
          <div class="col-6"><span class="text-secondary">Oldest Record:</span> ${Utils.formatDateTime(stats.oldest_record)}</div>
        </div>
        <div class="d-flex gap-2 mt-2">
          <button class="btn btn-xs btn-outline-danger" id="btnPurgeData">
            <i class="bi bi-trash me-1"></i>Purge Old Data
          </button>
          <button class="btn btn-xs btn-outline-secondary" id="btnPurgeTiles">
            <i class="bi bi-map me-1"></i>Purge Tile Cache
          </button>
        </div>
      </div>` : ''}
    `;
  }

  function _attachSettingsListeners() {
    // GPS mode toggle
    document.getElementById('s_gps_mode')?.addEventListener('change', e => {
      const isStatic = e.target.value === 'static';
      const div = document.getElementById('s_static_coords');
      if (div) div.style.display = isStatic ? '' : 'none';
    });

    // Purge actions
    document.getElementById('btnPurgeData')?.addEventListener('click', async () => {
      if (!confirm('Purge data older than the retention period?')) return;
      const days = parseInt(document.getElementById('s_retention')?.value || '14');
      const before = new Date(Date.now() - days * 86400000).toISOString();
      try {
        const result = await Api.purgeData(before);
        Utils.toast(`Purged ${result.detections_deleted} detections, ${result.alerts_deleted} alerts.`, 'success');
        // Refresh stats
        _dataStats = await Api.getDataStats().catch(() => null);
      } catch (e) {
        Utils.toast('Purge failed: ' + e.message, 'alert');
      }
    });

    document.getElementById('btnPurgeTiles')?.addEventListener('click', async () => {
      if (!confirm('Delete all cached map tiles?')) return;
      try {
        const result = await Api.purgeTiles();
        Utils.toast(`Deleted ${result.tiles_deleted} tiles (${Utils.formatBytes(result.bytes_freed)} freed).`, 'success');
      } catch (e) {
        Utils.toast('Tile purge failed: ' + e.message, 'alert');
      }
    });

    // Auth token — also store in localStorage for API usage
    document.getElementById('s_token')?.addEventListener('change', e => {
      const val = e.target.value.trim();
      if (val) Api.setToken(val);
    });

    // Slack test button
    document.getElementById('btn_slack_test')?.addEventListener('click', async () => {
      const url = document.getElementById('s_slack_webhook')?.value?.trim();
      const name = document.getElementById('s_slack_name')?.value?.trim() || 'Sparrow DroneID';
      if (!url) { Utils.toast('Enter a webhook URL first', 'warning'); return; }
      const btn = document.getElementById('btn_slack_test');
      btn.disabled = true;
      btn.textContent = 'Sending...';
      try {
        const resp = await Api.post('/alerts/slack-test', { webhook_url: url, display_name: name });
        if (resp.success) {
          Utils.toast('Slack test message sent', 'success');
        } else {
          Utils.toast('Slack test failed: ' + (resp.error || 'Unknown error'), 'danger');
        }
      } catch (e) {
        Utils.toast('Slack test error: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.textContent = 'Test Slack';
    });
  }

  // ---- Save ----
  async function save() {
    if (!_settings) return;

    const changes = {};

    const intField = (id, key) => {
      const el = document.getElementById(id);
      if (el) changes[key] = parseInt(el.value);
    };
    const floatField = (id, key) => {
      const el = document.getElementById(id);
      if (el) changes[key] = parseFloat(el.value);
    };
    const strField = (id, key) => {
      const el = document.getElementById(id);
      if (el) changes[key] = el.value.trim();
    };
    const boolField = (id, key) => {
      const el = document.getElementById(id);
      if (el) changes[key] = el.checked;
    };

    intField('s_port', 'port');
    strField('s_bind', 'bind_address');
    boolField('s_https', 'https_enabled');
    strField('s_cert', 'https_cert_path');
    strField('s_key', 'https_key_path');
    strField('s_allowed_ips', 'allowed_ips');
    strField('s_gps_mode', 'gps_mode');
    floatField('s_lat', 'gps_static_lat');
    floatField('s_lon', 'gps_static_lon');
    floatField('s_alt', 'gps_static_alt');
    strField('s_iface', 'monitor_interface');
    boolField('s_tile_cache', 'tile_cache_enabled');
    boolField('s_cot_enabled', 'cot_enabled');
    strField('s_cot_addr', 'cot_address');
    intField('s_cot_port', 'cot_port');
    boolField('s_alert_audio', 'alert_audio_enabled');
    boolField('s_alert_visual', 'alert_visual_enabled');
    boolField('s_alert_script', 'alert_script_enabled');
    strField('s_alert_script_path', 'alert_script_path');
    boolField('s_alert_slack', 'alert_slack_enabled');
    strField('s_slack_webhook', 'alert_slack_webhook_url');
    strField('s_slack_name', 'alert_slack_display_name');
    intField('s_retention', 'retention_days');

    // Token only if non-empty
    const tokenVal = document.getElementById('s_token')?.value?.trim();
    if (tokenVal) {
      changes.auth_token = tokenVal;
      Api.setToken(tokenVal);
    }

    try {
      const result = await Api.putSettings(changes);
      _settings = result.settings;

      if (result.restart_required) {
        const warn = document.getElementById('restartWarning');
        if (warn) warn.style.removeProperty('display');
      } else {
        bootstrap.Modal.getInstance(document.getElementById('settingsModal'))?.hide();
        Utils.toast('Settings saved successfully.', 'success');
      }
    } catch (e) {
      Utils.toast('Failed to save settings: ' + e.message, 'alert');
    }
  }

  // ---- Init ----
  function init() {
    document.getElementById('settingsModal')?.addEventListener('show.bs.modal', () => open());
    document.getElementById('btnSaveSettings')?.addEventListener('click', () => save());
  }

  return {
    init,
    open,
    save,
  };
})();
