/* ============================================================
   settings.js — Full-page settings tab with card-based layout
   ============================================================ */

const SettingsManager = (() => {

  let _settings = null;
  let _dataStats = null;
  let _interfaces = [];
  let _certs = [];
  let _vendorCodes = null;

  // ---- Public: called from app.js init ----
  function init() {
    // Wire up Save button
    document.getElementById('btnSaveSettings')?.addEventListener('click', () => save());

    // Load data whenever the settings modal opens
    document.getElementById('settingsModal')?.addEventListener('show.bs.modal', () => {
      _loadAndRender();
    });
  }

  // ---- Load data and render ----
  async function _loadAndRender() {
    const container = document.getElementById('settingsTabContent');
    if (!container) return;

    // Show loading state
    container.innerHTML = `
      <div class="text-center py-5 text-secondary">
        <i class="bi bi-arrow-repeat spin fs-3"></i><br>Loading settings…
      </div>`;

    try {
      [_settings, _dataStats, _interfaces, _certs, _vendorCodes] = await Promise.all([
        Api.getSettings().then(r => r.settings),
        Api.getDataStats().catch(() => null),
        Api.getInterfaces().then(r => r.interfaces || []).catch(() => []),
        Api.getCerts().then(r => r.certs || []).catch(() => []),
        Api.getVendorCodes().catch(() => null),
      ]);

      // GPS error check
      let gpsError = null;
      if (_settings.gps_mode === 'gpsd') {
        gpsError = await Api.getGps().then(r => r.gps_error || null).catch(() => null);
      }

      container.innerHTML = _buildHtml(_settings, _dataStats, _interfaces, _certs, gpsError, _vendorCodes);
      _attachListeners();

    } catch (e) {
      container.innerHTML = `<div class="text-danger p-3">Failed to load settings: ${_esc(e.message)}</div>`;
    }
  }

  // ---- HTML builder ----
  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _checked(val) { return val ? 'checked' : ''; }
  function _sel(a, b)    { return a === b ? 'selected' : ''; }

  function _buildHtml(s, stats, ifaces, certs, gpsError, vendorCodes) {
    return `
      <div class="row g-3">

        <!-- ===== Col 1 ===== -->
        <div class="col-lg-6">

          <!-- Operator Identity -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-person-badge me-2"></i>Operator Identity
            </div>
            <div class="card-body">
              <div class="mb-0">
                <label class="form-label" for="s_operator_name">Operator Name / Callsign</label>
                <input type="text" class="form-control form-control-sm" id="s_operator_name"
                  value="${_esc(localStorage.getItem('sparrow_operator_name') || '')}" placeholder="e.g. KILO-1"
                  style="max-width:220px;">
                <small class="text-muted">Stored on this device only — each operator sets their own</small>
              </div>
            </div>
          </div>

          <!-- Network & Security -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-shield-lock me-2"></i>Network &amp; Security
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label" for="s_port">Port</label>
                <input type="number" class="form-control form-control-sm" id="s_port"
                  value="${s.port || 8097}" min="1" max="65535" style="max-width:110px;">
                <small class="text-muted">HTTP listen port (restart required)</small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_bind">Bind Address</label>
                <input type="text" class="form-control form-control-sm" id="s_bind"
                  value="${_esc(s.bind_address || '0.0.0.0')}" placeholder="0.0.0.0" style="max-width:170px;">
                <small class="text-muted">IP to listen on (restart required)</small>
              </div>

              <div class="mb-3">
                <label class="form-label">HTTPS</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_https" ${_checked(s.https_enabled)}>
                  <label class="form-check-label" for="s_https">Enable HTTPS (restart required)</label>
                </div>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_https_cert_name">TLS Certificate</label>
                <select class="form-select form-select-sm" id="s_https_cert_name" style="max-width:220px;">
                  <option value="">(none)</option>
                  ${certs.map(c => `<option value="${_esc(c.name)}" ${_sel(s.https_cert_name, c.name)}>${_esc(c.name)}</option>`).join('')}
                </select>
                <small class="text-muted">Certificate to use for HTTPS</small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_allowed_ips">Allowed IPs</label>
                <input type="text" class="form-control form-control-sm" id="s_allowed_ips"
                  value="${_esc(s.allowed_ips || '')}" placeholder="192.168.1.0/24" style="max-width:220px;">
                <small class="text-muted">Comma-separated IPs/CIDRs; empty = allow all</small>
              </div>

              <div class="mb-0">
                <label class="form-label" for="s_token">Auth Token</label>
                <input type="password" class="form-control form-control-sm" id="s_token"
                  value="" placeholder="${s.auth_token === '(set)' ? '(set — enter new to change)' : '(not set)'}"
                  autocomplete="new-password" style="max-width:220px;">
                <small class="text-muted">Bearer token; leave blank to keep current</small>
              </div>

            </div>
          </div>

          <!-- Monitoring -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-wifi me-2"></i>Monitoring
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label" for="s_iface">Default Interface</label>
                <select class="form-select form-select-sm" id="s_iface" style="max-width:200px;">
                  <option value="">(none)</option>
                  ${ifaces.map(iface => {
                    const label = iface.name + (iface.monitor_capable ? '' : ' \u2014 no monitor');
                    return `<option value="${_esc(iface.name)}"
                      ${_sel(s.monitor_interface, iface.name)}
                      ${iface.monitor_capable ? '' : 'disabled'}
                    >${_esc(label)}</option>`;
                  }).join('')}
                </select>
                <small class="text-muted">Interface to use for drone detection</small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_display_units">Display Units</label>
                <select class="form-select form-select-sm" id="s_display_units" style="max-width:150px;">
                  <option value="metric"   ${_sel(s.display_units || 'metric', 'metric')}>Metric (m, m/s, km)</option>
                  <option value="imperial" ${_sel(s.display_units || 'metric', 'imperial')}>Imperial (ft, mph, mi)</option>
                </select>
                <small class="text-muted">Units for display and Slack notifications</small>
              </div>

              <div class="mb-3">
                <label class="form-label">Tile Cache</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_tile_cache" ${_checked(s.tile_cache_enabled)}>
                  <label class="form-check-label" for="s_tile_cache">Cache map tiles locally for offline use</label>
                </div>
              </div>

              <div class="mb-0">
                <label class="form-label" for="s_airport_radius">Airport Geozone Radius</label>
                <div class="d-flex align-items-center gap-2">
                  <input type="number" class="form-control form-control-sm" id="s_airport_radius"
                    value="${s.airport_geozone_radius_mi || 2}" min="0.5" max="10" step="0.5" style="max-width:80px;">
                  <span class="text-secondary small">miles</span>
                </div>
                <small class="text-muted">Radius of airport zone circles on map</small>
              </div>

            </div>
          </div>

          <!-- Alerts -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-bell me-2"></i>Alerts
            </div>
            <div class="card-body">

              <div class="mb-2 d-flex align-items-center gap-2">
                <div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" id="s_alert_audio" ${_checked(s.alert_audio_enabled)}>
                  <label class="form-check-label" for="s_alert_audio">Audio notifications</label>
                </div>
                <button class="btn btn-sm btn-outline-secondary" id="btn_audio_test" type="button">
                  <i class="bi bi-volume-up me-1"></i>Test
                </button>
              </div>

              <div class="mb-2">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_visual" ${_checked(s.alert_visual_enabled)}>
                  <label class="form-check-label" for="s_alert_visual">Visual notifications</label>
                </div>
              </div>

              <div class="mb-2">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_script" ${_checked(s.alert_script_enabled)}>
                  <label class="form-check-label" for="s_alert_script">Script notifications</label>
                </div>
              </div>

              <div class="mb-0">
                <label class="form-label" for="s_alert_script_path">Script Path</label>
                <input type="text" class="form-control form-control-sm" id="s_alert_script_path"
                  value="${_esc(s.alert_script_path || '')}" placeholder="/path/to/alert.sh">
                <small class="text-muted">Executed when an alert fires</small>
              </div>

            </div>
          </div>

          <!-- Certificates -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-file-earmark-lock me-2"></i>Certificates
            </div>
            <div class="card-body">

              <div id="certList" class="mb-3">
                ${_buildCertTable(certs, s.https_cert_name)}
              </div>

              <div class="d-flex flex-wrap gap-2">
                <button class="btn btn-sm btn-outline-secondary" id="btnGenSelfSigned">
                  <i class="bi bi-key me-1"></i>Generate Self-Signed
                </button>
                <button class="btn btn-sm btn-outline-secondary" id="btnGenCSR">
                  <i class="bi bi-file-earmark-text me-1"></i>Generate CSR
                </button>
                <button class="btn btn-sm btn-outline-secondary" id="btnImportCert">
                  <i class="bi bi-upload me-1"></i>Import Certificate
                </button>
              </div>

              <!-- Inline forms, hidden by default -->
              <div id="certFormArea" class="mt-3"></div>

            </div>
          </div>

        </div><!-- /col-1 -->

        <!-- ===== Col 2 ===== -->
        <div class="col-lg-6">

          <!-- GPS Configuration -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-geo-alt me-2"></i>GPS Configuration
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label" for="s_gps_mode">Mode</label>
                <select class="form-select form-select-sm" id="s_gps_mode" style="max-width:150px;">
                  <option value="none"   ${_sel(s.gps_mode, 'none')}>None</option>
                  <option value="gpsd"   ${_sel(s.gps_mode, 'gpsd')}>gpsd</option>
                  <option value="static" ${_sel(s.gps_mode, 'static')}>Static</option>
                </select>
                <small class="text-muted">How receiver position is determined</small>
              </div>

              ${gpsError ? `
              <div class="alert alert-warning mt-2 py-1 px-2" id="gpsErrorAlert" style="font-size:12px;">
                <i class="bi bi-exclamation-triangle me-1"></i>${_esc(gpsError)}
              </div>` : `<div id="gpsErrorAlert" style="display:none;"></div>`}

              <div id="s_static_coords" style="display:${s.gps_mode === 'static' ? '' : 'none'}">

                <div class="mb-3">
                  <label class="form-label" for="s_lat">Static Latitude</label>
                  <input type="number" class="form-control form-control-sm" id="s_lat"
                    value="${s.gps_static_lat || 0}" step="0.000001" min="-90" max="90" style="max-width:160px;">
                </div>

                <div class="mb-3">
                  <label class="form-label" for="s_lon">Static Longitude</label>
                  <input type="number" class="form-control form-control-sm" id="s_lon"
                    value="${s.gps_static_lon || 0}" step="0.000001" min="-180" max="180" style="max-width:160px;">
                </div>

                <div class="mb-0">
                  <label class="form-label" for="s_alt">Static Altitude (m)</label>
                  <input type="number" class="form-control form-control-sm" id="s_alt"
                    value="${s.gps_static_alt || 0}" step="0.1" style="max-width:120px;">
                </div>

              </div>

            </div>
          </div>

          <!-- Cursor on Target -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-crosshair me-2"></i>Cursor on Target
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label">CoT Output</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_cot_enabled" ${_checked(s.cot_enabled)}>
                  <label class="form-check-label" for="s_cot_enabled">Multicast drone positions to TAK / ATAK</label>
                </div>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_cot_addr">Multicast Address</label>
                <input type="text" class="form-control form-control-sm" id="s_cot_addr"
                  value="${_esc(s.cot_address || '239.2.3.1')}" placeholder="239.2.3.1" style="max-width:180px;">
              </div>

              <div class="mb-0">
                <label class="form-label" for="s_cot_port">Port</label>
                <input type="number" class="form-control form-control-sm" id="s_cot_port"
                  value="${s.cot_port || 6969}" min="1" max="65535" style="max-width:110px;">
              </div>

            </div>
          </div>

          <!-- Slack Notifications -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-chat-dots me-2"></i>Slack Notifications
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label">Enable Slack</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_slack" ${_checked(s.alert_slack_enabled)}>
                  <label class="form-check-label" for="s_alert_slack">Send drone alerts to Slack</label>
                </div>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_slack_webhook">Webhook URL</label>
                <input type="text" class="form-control form-control-sm" id="s_slack_webhook"
                  value="${_esc(s.alert_slack_webhook_url || '')}" placeholder="https://hooks.slack.com/services/...">
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_slack_name">Display Name</label>
                <input type="text" class="form-control form-control-sm" id="s_slack_name"
                  value="${_esc(s.alert_slack_display_name || 'Sparrow DroneID')}" style="max-width:200px;">
              </div>

              <div class="mb-0">
                <button class="btn btn-sm btn-outline-secondary" id="btn_slack_test">
                  <i class="bi bi-send me-1"></i>Test Slack
                </button>
              </div>

            </div>
          </div>

          <!-- Data & Retention -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-database me-2"></i>Data &amp; Retention
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label" for="s_retention">Retention Period</label>
                <div class="d-flex align-items-center gap-2">
                  <input type="number" class="form-control form-control-sm" id="s_retention"
                    value="${s.retention_days || 14}" min="1" max="365" style="max-width:80px;">
                  <span class="text-secondary small">days</span>
                </div>
                <small class="text-muted">Auto-purge data older than this</small>
              </div>

              ${stats ? `
              <div class="settings-stats-grid mb-3">
                <div><span class="text-secondary">DB Size</span><span>${Utils.formatBytes(stats.db_size_bytes)}</span></div>
                <div><span class="text-secondary">Tile Cache</span><span>${Utils.formatBytes(stats.tile_cache_size_bytes)}</span></div>
                <div><span class="text-secondary">Detections</span><span>${(stats.detection_count || 0).toLocaleString()}</span></div>
                <div><span class="text-secondary">Unique Drones</span><span>${stats.unique_serials || 0}</span></div>
                <div><span class="text-secondary">Alerts Logged</span><span>${stats.alert_count || 0}</span></div>
                <div><span class="text-secondary">Oldest Record</span><span>${Utils.formatDateTime(stats.oldest_record)}</span></div>
              </div>
              <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-danger" id="btnPurgeData">
                  <i class="bi bi-trash me-1"></i>Purge Old Data
                </button>
                <button class="btn btn-sm btn-outline-secondary" id="btnPurgeTiles">
                  <i class="bi bi-map me-1"></i>Purge Tile Cache
                </button>
              </div>` : '<p class="text-secondary small mb-0">Stats unavailable.</p>'}

            </div>
          </div>

          <!-- Vendor Codes -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-upc-scan me-2"></i>Vendor Codes
            </div>
            <div class="card-body">

              <div class="settings-stats-grid mb-3">
                <div>
                  <span class="text-secondary">Serial Prefixes</span>
                  <span id="vc_serial_count">${vendorCodes ? vendorCodes.serial_prefix_count : '—'}</span>
                </div>
                <div>
                  <span class="text-secondary">MAC OUIs</span>
                  <span id="vc_oui_count">${vendorCodes ? vendorCodes.mac_oui_count : '—'}</span>
                </div>
              </div>

              ${s.vendor_codes_url ? '' : `
              <div class="mb-2">
                <small class="text-muted">Set <strong>vendor_codes_url</strong> in settings to enable remote updates.</small>
              </div>`}

              <div class="mb-0 d-flex align-items-center gap-2">
                <button class="btn btn-sm btn-outline-secondary" id="btn_vendor_update"
                  ${s.vendor_codes_url ? '' : 'disabled'}>
                  <i class="bi bi-arrow-repeat me-1"></i>Update Vendor Codes
                </button>
                <span id="vc_status" class="small text-muted"></span>
              </div>

            </div>
          </div>

        </div><!-- /col-2 -->
      </div><!-- /row -->
    `;
  }

  function _buildCertTable(certs, activeName) {
    if (!certs || certs.length === 0) {
      return '<p class="text-secondary small mb-0">No certificates installed.</p>';
    }
    const rows = certs.map(c => {
      const isActive = c.name === activeName;
      return `
        <tr>
          <td class="text-nowrap">
            ${isActive ? '<i class="bi bi-check-circle-fill text-success me-1" title="Active"></i>' : ''}
            <span class="text-truncate" style="max-width:120px;display:inline-block;vertical-align:middle;">${_esc(c.name)}</span>
          </td>
          <td class="text-secondary small">${_esc(c.common_name || '—')}</td>
          <td class="text-secondary small text-nowrap">${_esc(c.expires || '—')}</td>
          <td>
            ${c.self_signed ? '<span class="badge bg-secondary" style="font-size:10px;">self-signed</span>' : ''}
            ${c.has_key ? '<span class="badge bg-secondary ms-1" style="font-size:10px;">has key</span>' : ''}
          </td>
          <td class="text-nowrap">
            <button class="btn btn-xs btn-outline-secondary me-1" onclick="SettingsManager.showCertDetail('${_esc(c.name)}')">
              <i class="bi bi-eye"></i>
            </button>
            <button class="btn btn-xs btn-outline-danger" onclick="SettingsManager.deleteCert('${_esc(c.name)}')">
              <i class="bi bi-trash"></i>
            </button>
          </td>
        </tr>`;
    }).join('');

    return `
      <div class="table-responsive">
        <table class="table table-sm settings-cert-table mb-0">
          <thead>
            <tr>
              <th>Name</th><th>CN</th><th>Expires</th><th>Flags</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ---- Listeners ----
  function _attachListeners() {
    // GPS mode toggle
    document.getElementById('s_gps_mode')?.addEventListener('change', e => {
      const isStatic = e.target.value === 'static';
      const div = document.getElementById('s_static_coords');
      if (div) div.style.display = isStatic ? '' : 'none';

      // Show/hide GPS error for gpsd mode
      if (e.target.value === 'gpsd') {
        Api.getGps().then(r => {
          const err = r.gps_error || null;
          const el = document.getElementById('gpsErrorAlert');
          if (el) {
            if (err) {
              el.innerHTML = `<i class="bi bi-exclamation-triangle me-1"></i>${_esc(err)}`;
              el.style.display = '';
            } else {
              el.style.display = 'none';
            }
          }
        }).catch(() => {});
      } else {
        const el = document.getElementById('gpsErrorAlert');
        if (el) el.style.display = 'none';
      }
    });

    // Operator name — mirror to localStorage so alerts module can read it synchronously
    document.getElementById('s_operator_name')?.addEventListener('change', e => {
      const val = e.target.value.trim();
      if (val) {
        localStorage.setItem('sparrow_operator_name', val);
      } else {
        localStorage.removeItem('sparrow_operator_name');
      }
    });

    // Auth token — store in localStorage for API usage
    document.getElementById('s_token')?.addEventListener('change', e => {
      const val = e.target.value.trim();
      if (val) Api.setToken(val);
    });

    // Audio test
    document.getElementById('btn_audio_test')?.addEventListener('click', () => {
      if (typeof AlertsManager !== 'undefined' && AlertsManager._testAudio) {
        AlertsManager._testAudio();
      } else {
        Utils.toast('Audio system not available', 'warning');
      }
    });

    // Slack test
    document.getElementById('btn_slack_test')?.addEventListener('click', async () => {
      const url = document.getElementById('s_slack_webhook')?.value?.trim();
      const name = document.getElementById('s_slack_name')?.value?.trim() || 'Sparrow DroneID';
      if (!url) { Utils.toast('Enter a webhook URL first', 'warning'); return; }
      const btn = document.getElementById('btn_slack_test');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Sending…';
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
      btn.innerHTML = '<i class="bi bi-send me-1"></i>Test Slack';
    });

    // Purge data
    document.getElementById('btnPurgeData')?.addEventListener('click', async () => {
      if (!confirm('Purge data older than the retention period?')) return;
      const days = parseInt(document.getElementById('s_retention')?.value || '14');
      const before = new Date(Date.now() - days * 86400000).toISOString();
      try {
        const result = await Api.purgeData(before);
        Utils.toast(`Purged ${result.detections_deleted} detections, ${result.alerts_deleted} alerts.`, 'success');
        _dataStats = await Api.getDataStats().catch(() => null);
      } catch (e) {
        Utils.toast('Purge failed: ' + e.message, 'danger');
      }
    });

    // Purge tiles
    document.getElementById('btnPurgeTiles')?.addEventListener('click', async () => {
      if (!confirm('Delete all cached map tiles?')) return;
      try {
        const result = await Api.purgeTiles();
        Utils.toast(`Deleted ${result.tiles_deleted} tiles (${Utils.formatBytes(result.bytes_freed)} freed).`, 'success');
      } catch (e) {
        Utils.toast('Tile purge failed: ' + e.message, 'danger');
      }
    });

    // Certificate management buttons
    document.getElementById('btnGenSelfSigned')?.addEventListener('click', () => _showCertForm('self-signed'));
    document.getElementById('btnGenCSR')?.addEventListener('click', () => _showCertForm('csr'));
    document.getElementById('btnImportCert')?.addEventListener('click', () => _showCertForm('import'));

    // Vendor codes update
    document.getElementById('btn_vendor_update')?.addEventListener('click', async () => {
      const btn    = document.getElementById('btn_vendor_update');
      const status = document.getElementById('vc_status');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Updating…';
      if (status) status.textContent = '';
      try {
        const result = await Api.updateVendorCodes();
        const added = (result.added_serial_prefixes || 0) + (result.added_mac_ouis || 0);
        Utils.toast(
          `Vendor codes updated — ${result.serial_prefix_count} serial prefixes, ${result.mac_oui_count} MAC OUIs` +
          (added > 0 ? ` (+${added} new)` : ''),
          'success'
        );
        // Refresh displayed counts
        const sc = document.getElementById('vc_serial_count');
        const oc = document.getElementById('vc_oui_count');
        if (sc) sc.textContent = result.serial_prefix_count;
        if (oc) oc.textContent = result.mac_oui_count;
        if (status) status.textContent = 'Updated';
      } catch (e) {
        Utils.toast('Vendor code update failed: ' + e.message, 'danger');
        if (status) status.textContent = 'Failed';
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Update Vendor Codes';
      }
    });
  }

  // ---- Cert forms ----
  function _showCertForm(type) {
    const area = document.getElementById('certFormArea');
    if (!area) return;

    if (type === 'self-signed') {
      area.innerHTML = `
        <div class="card settings-inline-form p-3">
          <div class="fw-600 mb-2 small">Generate Self-Signed Certificate</div>
          <div class="mb-2">
            <label class="form-label" for="cf_cn">Common Name (CN)</label>
            <input type="text" class="form-control form-control-sm" id="cf_cn" placeholder="sparrow.local" style="max-width:220px;">
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_days">Valid Days</label>
            <input type="number" class="form-control form-control-sm" id="cf_days" value="365" min="1" max="3650" style="max-width:110px;">
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_name">Certificate Name</label>
            <input type="text" class="form-control form-control-sm" id="cf_name" placeholder="my-cert" style="max-width:180px;">
            <small class="text-muted">Identifier stored in the cert store</small>
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-sm btn-primary" id="btnCfSubmit">
              <i class="bi bi-key me-1"></i>Generate
            </button>
            <button class="btn btn-sm btn-outline-secondary" id="btnCfCancel">Cancel</button>
          </div>
        </div>`;

      document.getElementById('btnCfSubmit')?.addEventListener('click', async () => {
        const cn   = document.getElementById('cf_cn')?.value?.trim();
        const days = parseInt(document.getElementById('cf_days')?.value || '365');
        const name = document.getElementById('cf_name')?.value?.trim();
        if (!cn || !name) { Utils.toast('CN and name are required', 'warning'); return; }
        try {
          await Api.generateSelfSigned({ common_name: cn, days, name });
          Utils.toast('Self-signed certificate generated', 'success');
          _reloadCerts();
        } catch (e) {
          Utils.toast('Generate failed: ' + e.message, 'danger');
        }
      });

    } else if (type === 'csr') {
      area.innerHTML = `
        <div class="card settings-inline-form p-3">
          <div class="fw-600 mb-2 small">Generate Certificate Signing Request</div>
          <div class="mb-2">
            <label class="form-label" for="cf_cn">Common Name (CN)</label>
            <input type="text" class="form-control form-control-sm" id="cf_cn" placeholder="sparrow.local" style="max-width:220px;">
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_org">Organization</label>
            <input type="text" class="form-control form-control-sm" id="cf_org" placeholder="My Org" style="max-width:220px;">
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_country">Country (2-letter)</label>
            <input type="text" class="form-control form-control-sm" id="cf_country" placeholder="US" maxlength="2" style="max-width:80px;">
          </div>
          <div class="d-flex gap-2 mb-2">
            <button class="btn btn-sm btn-primary" id="btnCfSubmit">
              <i class="bi bi-file-earmark-text me-1"></i>Generate CSR
            </button>
            <button class="btn btn-sm btn-outline-secondary" id="btnCfCancel">Cancel</button>
          </div>
          <div id="csrOutput" style="display:none;">
            <label class="form-label">CSR PEM (copy this)</label>
            <textarea class="form-control form-control-sm font-monospace" id="csrPemText" rows="8" readonly style="font-size:11px;"></textarea>
          </div>
        </div>`;

      document.getElementById('btnCfSubmit')?.addEventListener('click', async () => {
        const cn      = document.getElementById('cf_cn')?.value?.trim();
        const org     = document.getElementById('cf_org')?.value?.trim();
        const country = document.getElementById('cf_country')?.value?.trim();
        if (!cn) { Utils.toast('Common Name is required', 'warning'); return; }
        try {
          const resp = await Api.generateCSR({ common_name: cn, organization: org, country });
          const out = document.getElementById('csrOutput');
          const txt = document.getElementById('csrPemText');
          if (out && txt) {
            txt.value = resp.csr_pem || '';
            out.style.display = '';
          }
          Utils.toast('CSR generated', 'success');
        } catch (e) {
          Utils.toast('CSR generation failed: ' + e.message, 'danger');
        }
      });

    } else if (type === 'import') {
      area.innerHTML = `
        <div class="card settings-inline-form p-3">
          <div class="fw-600 mb-2 small">Import Certificate</div>
          <div class="mb-2">
            <label class="form-label" for="cf_name">Certificate Name</label>
            <input type="text" class="form-control form-control-sm" id="cf_name" placeholder="my-cert" style="max-width:180px;">
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_cert_pem">Certificate PEM</label>
            <textarea class="form-control form-control-sm font-monospace" id="cf_cert_pem"
              rows="5" placeholder="-----BEGIN CERTIFICATE-----" style="font-size:11px;"></textarea>
          </div>
          <div class="mb-2">
            <label class="form-label" for="cf_key_pem">Private Key PEM (optional)</label>
            <textarea class="form-control form-control-sm font-monospace" id="cf_key_pem"
              rows="5" placeholder="-----BEGIN PRIVATE KEY-----" style="font-size:11px;"></textarea>
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-sm btn-primary" id="btnCfSubmit">
              <i class="bi bi-upload me-1"></i>Import
            </button>
            <button class="btn btn-sm btn-outline-secondary" id="btnCfCancel">Cancel</button>
          </div>
        </div>`;

      document.getElementById('btnCfSubmit')?.addEventListener('click', async () => {
        const name     = document.getElementById('cf_name')?.value?.trim();
        const certPem  = document.getElementById('cf_cert_pem')?.value?.trim();
        const keyPem   = document.getElementById('cf_key_pem')?.value?.trim();
        if (!name || !certPem) { Utils.toast('Name and certificate PEM are required', 'warning'); return; }
        try {
          const body = { name, cert_pem: certPem };
          if (keyPem) body.key_pem = keyPem;
          await Api.importCert(body);
          Utils.toast('Certificate imported', 'success');
          _reloadCerts();
        } catch (e) {
          Utils.toast('Import failed: ' + e.message, 'danger');
        }
      });
    }

    // Wire up cancel button for all form types
    document.getElementById('btnCfCancel')?.addEventListener('click', () => {
      area.innerHTML = '';
    });
  }

  async function _reloadCerts() {
    _certs = await Api.getCerts().then(r => r.certs || []).catch(() => []);
    const certList = document.getElementById('certList');
    if (certList) {
      certList.innerHTML = _buildCertTable(_certs, _settings?.https_cert_name);
    }
    // Also refresh the HTTPS cert dropdown
    const certSelect = document.getElementById('s_https_cert_name');
    if (certSelect) {
      const currentVal = certSelect.value;
      certSelect.innerHTML = `<option value="">(none)</option>` +
        _certs.map(c => `<option value="${_esc(c.name)}" ${c.name === currentVal ? 'selected' : ''}>${_esc(c.name)}</option>`).join('');
    }
    // Clear form area
    const area = document.getElementById('certFormArea');
    if (area) area.innerHTML = '';
  }

  // ---- Public: show cert detail ----
  async function showCertDetail(name) {
    try {
      const resp = await Api.getCertDetail(name);
      const cert = resp.cert || resp;
      const lines = [
        ['Name',        cert.name],
        ['Common Name', cert.common_name],
        ['Expires',     cert.expires],
        ['Self-Signed', cert.self_signed ? 'Yes' : 'No'],
        ['Has Key',     cert.has_key     ? 'Yes' : 'No'],
      ].map(([k, v]) => `<tr><td class="text-secondary pe-3">${_esc(k)}</td><td>${_esc(v || '—')}</td></tr>`).join('');

      // Use a simple bootstrap modal
      let modal = document.getElementById('certDetailModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = 'certDetailModal';
        modal.tabIndex = -1;
        modal.innerHTML = `
          <div class="modal-dialog">
            <div class="modal-content">
              <div class="modal-header">
                <h6 class="modal-title"><i class="bi bi-file-earmark-lock me-2"></i>Certificate Details</h6>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
              </div>
              <div class="modal-body" id="certDetailBody"></div>
              <div class="modal-footer">
                <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
              </div>
            </div>
          </div>`;
        document.body.appendChild(modal);
      }
      document.getElementById('certDetailBody').innerHTML =
        `<table class="table table-sm mb-0"><tbody>${lines}</tbody></table>`;
      bootstrap.Modal.getOrCreateInstance(modal).show();
    } catch (e) {
      Utils.toast('Failed to load cert details: ' + e.message, 'danger');
    }
  }

  // ---- Public: delete cert ----
  async function deleteCert(name) {
    if (!confirm(`Delete certificate "${name}"?`)) return;
    try {
      await Api.deleteCert(name);
      Utils.toast(`Certificate "${name}" deleted`, 'success');
      _reloadCerts();
    } catch (e) {
      Utils.toast('Delete failed: ' + e.message, 'danger');
    }
  }

  // ---- Save ----
  async function save() {
    if (!_settings) return;

    const changes = {};

    const intField   = (id, key) => { const el = document.getElementById(id); if (el) changes[key] = parseInt(el.value); };
    const floatField = (id, key) => { const el = document.getElementById(id); if (el) changes[key] = parseFloat(el.value); };
    const strField   = (id, key) => { const el = document.getElementById(id); if (el) changes[key] = el.value.trim(); };
    const boolField  = (id, key) => { const el = document.getElementById(id); if (el) changes[key] = el.checked; };

    // operator_name is localStorage-only (per device), not saved to DB
    intField  ('s_port',             'port');
    strField  ('s_bind',             'bind_address');
    boolField ('s_https',            'https_enabled');
    strField  ('s_https_cert_name',  'https_cert_name');
    strField  ('s_allowed_ips',      'allowed_ips');
    strField  ('s_gps_mode',         'gps_mode');
    floatField('s_lat',              'gps_static_lat');
    floatField('s_lon',              'gps_static_lon');
    floatField('s_alt',              'gps_static_alt');
    strField  ('s_iface',            'monitor_interface');
    strField  ('s_display_units',    'display_units');
    boolField ('s_tile_cache',       'tile_cache_enabled');
    floatField('s_airport_radius',   'airport_geozone_radius_mi');
    boolField ('s_cot_enabled',      'cot_enabled');
    strField  ('s_cot_addr',         'cot_address');
    intField  ('s_cot_port',         'cot_port');
    boolField ('s_alert_audio',      'alert_audio_enabled');
    boolField ('s_alert_visual',     'alert_visual_enabled');
    boolField ('s_alert_script',     'alert_script_enabled');
    strField  ('s_alert_script_path','alert_script_path');
    boolField ('s_alert_slack',      'alert_slack_enabled');
    strField  ('s_slack_webhook',    'alert_slack_webhook_url');
    strField  ('s_slack_name',       'alert_slack_display_name');

    // Slack guard: can't enable notifications without a webhook URL
    if (changes.alert_slack_enabled && !changes.alert_slack_webhook_url) {
      changes.alert_slack_enabled = false;
      const slackToggle = document.getElementById('s_alert_slack');
      if (slackToggle) slackToggle.checked = false;
      Utils.toast('Slack notifications require a webhook URL — disabled.', 'warning');
    }
    intField  ('s_retention',        'retention_days');

    // Token only if entered
    const tokenVal = document.getElementById('s_token')?.value?.trim();
    if (tokenVal) {
      changes.auth_token = tokenVal;
      Api.setToken(tokenVal);
    }

    const btn = document.getElementById('btnSaveSettings');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Saving…'; }

    try {
      const result = await Api.putSettings(changes);
      _settings = result.settings;

      // Sync unit toggle with the saved server value
      if (_settings.display_units) {
        Utils.syncUnitsFromSettings(_settings.display_units);
        const unitBtn = document.getElementById('btnUnitToggle');
        if (unitBtn) unitBtn.textContent = _settings.display_units === 'imperial' ? 'ft' : 'm';
        TableManager.refreshUnits();
      }

      // operator_name is managed in localStorage only (per-device), not from DB

      if (result.restart_required) {
        Utils.toast('Settings saved. Restart required for some changes.', 'warning');
      } else {
        Utils.toast('Settings saved.', 'success');
      }

      // Always close the modal — the toast communicates any restart message
      setTimeout(() => {
        const modalEl = document.getElementById('settingsModal');
        if (modalEl) {
          const modal = bootstrap.Modal.getInstance(modalEl);
          if (modal) modal.hide();
        }
      }, 500);
    } catch (e) {
      Utils.toast('Failed to save settings: ' + e.message, 'danger');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-check-lg me-1"></i> Save Settings'; }
    }
  }

  return {
    init,
    save,
    showCertDetail,
    deleteCert,
  };
})();
