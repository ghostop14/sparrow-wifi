/* ============================================================
   settings.js — Full-page settings tab with card-based layout
   ============================================================ */

const SettingsManager = (() => {

  let _settings = null;
  let _dataStats = null;
  let _interfaces = [];
  let _certs = [];
  let _vendorCodes = null;
  let _wifiSsidPatterns = [];
  let _takConfig = null;
  let _takCerts = null;
  let _takIfaces = [];
  let _takPollTimer = null;
  let _takPollBusy = false;
  let _takPollGen = 0;

  // ---- Public: called from app.js init ----
  function init() {
    // Wire up Save button
    document.getElementById('btnSaveSettings')?.addEventListener('click', () => save());

    // Load data whenever the settings modal opens
    document.getElementById('settingsModal')?.addEventListener('show.bs.modal', () => {
      // Reset to General tab — content is rebuilt on every open, so the
      // active tab button must match the hardcoded "show active" pane.
      const generalTab = document.getElementById('generalTab-tab');
      if (generalTab) {
        const tab = bootstrap.Tab.getOrCreateInstance(generalTab);
        tab.show();
      }
      _loadAndRender();
    });

    // The modal closing always stops TAK status polling.
    document.getElementById('settingsModal')?.addEventListener('hide.bs.modal', () => _stopTakPolling());

    // Tab buttons are static markup, so these are attached once (not per render).
    const setHint = (html) => {
      const hint = document.getElementById('settingsFooterHint');
      if (hint) hint.innerHTML = html;
    };
    document.getElementById('takTab-tab')?.addEventListener('shown.bs.tab', () => {
      setHint('<i class="bi bi-info-circle me-1"></i>TAK settings apply immediately on Save \u2014 no restart needed');
      _startTakPolling();
    });
    ['generalTab-tab', 'elasticsearchTab-tab'].forEach(id => {
      document.getElementById(id)?.addEventListener('shown.bs.tab', () => _stopTakPolling());
    });
  }

  // ---- Load data and render ----
  async function _loadAndRender() {
    const container = document.getElementById('settingsTabContent');
    if (!container) return;

    _stopTakPolling();

    // Show loading state
    container.innerHTML = `
      <div class="text-center py-5 text-secondary">
        <i class="bi bi-arrow-repeat spin fs-3"></i><br>Loading settings…
      </div>`;

    try {
      [_settings, _dataStats, _interfaces, _certs, _vendorCodes, _wifiSsidPatterns,
       _takConfig, _takCerts, _takIfaces] = await Promise.all([
        Api.getSettings().then(r => r.settings),
        Api.getDataStats().catch(() => null),
        Api.getInterfaces().then(r => r.interfaces || []).catch(() => []),
        Api.getCerts().then(r => r.certs || []).catch(() => []),
        Api.getVendorCodes().catch(() => null),
        Api.getWifiSsidPatterns().then(r => r.patterns || []).catch(() => []),
        Api.getTakConfig().catch(() => null),
        Api.getTakCerts().catch(() => null),
        Api.getTakInterfaces().then(r => r.interfaces || []).catch(() => []),
      ]);

      // GPS error check
      let gpsError = null;
      if (_settings.gps_mode === 'gpsd') {
        gpsError = await Api.getGps().then(r => r.gps_error || null).catch(() => null);
      }

      container.innerHTML = _buildHtml(_settings, _dataStats, _interfaces, _certs, gpsError, _vendorCodes, _wifiSsidPatterns,
                                    _takConfig, _takCerts, _takIfaces);
      _attachListeners();

    } catch (e) {
      container.innerHTML = `<div class="text-danger p-3">Failed to load settings: ${_esc(e.message)}</div>`;
    }
  }

  // ---- HTML builder ----
  function _esc(str) {
    if (str === null || str === undefined || str === false || str === '') return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _checked(val) { return val ? 'checked' : ''; }
  function _sel(a, b)    { return a === b ? 'selected' : ''; }

  function _buildHtml(s, stats, ifaces, certs, gpsError, vendorCodes, wifiSsidPatterns, takConfig, takCerts, takIfaces) {
    // Auto-select the only monitor-capable interface when none is stored.
    // Use base_name (strips 'mon' VIF suffix) so we store the canonical name.
    const monCapable = ifaces.filter(i => i.monitor_capable && i.mode !== 'monitor');
    const _effectiveIface = s.monitor_interface ||
      (monCapable.length === 1 ? monCapable[0].base_name || monCapable[0].name : '');
    return `
      <div class="tab-content">
      <div class="tab-pane fade show active" id="generalTabPane" role="tabpanel" aria-labelledby="generalTab-tab">
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
                    const bname = iface.base_name || iface.name;
                    const label = bname + (iface.monitor_capable ? '' : ' \u2014 no monitor');
                    return `<option value="${_esc(bname)}"
                      ${_sel(_effectiveIface, bname)}
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

          <!-- Network Security -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-shield-lock me-2"></i>Network Security
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

              <div class="mb-3">
                <label class="form-label" for="s_alert_script_path">Script Path</label>
                <input type="text" class="form-control form-control-sm" id="s_alert_script_path"
                  value="${_esc(s.alert_script_path || '')}" placeholder="/path/to/alert.sh">
                <small class="text-muted">Executed when an alert fires</small>
              </div>

              <hr class="my-2">

              <div class="mb-3">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_friendly" ${_checked(s.alert_friendly_enabled !== false)}>
                  <label class="form-check-label" for="s_alert_friendly">Alert on Friendly drones</label>
                </div>
                <small class="text-muted">
                  Disable during ongoing operations (e.g. SAR) to suppress new-drone,
                  altitude, speed, and signal-lost alerts for drones tagged Friendly.
                  Reduces alert fatigue from repeated launches of known assets.
                </small>
              </div>

              <hr class="my-2">

              <div class="mb-3">
                <label class="form-label">Slack</label>
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

              <hr class="my-2">

              <div class="mb-3">
                <label class="form-label">API-Based Alerting</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_api" ${_checked(s.alert_api_enabled)}>
                  <label class="form-check-label" for="s_alert_api">Send drone alerts to an external API</label>
                </div>
                <small class="text-muted">
                  POSTs an ECS-shaped JSON alert to a generic ingest endpoint
                  with a bearer-token Authorization header. See the README for
                  the payload schema and endpoint contract.
                </small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_api_base_url">API Endpoint Root</label>
                <input type="text" class="form-control form-control-sm" id="s_api_base_url"
                  value="${_esc(s.alert_api_base_url || '')}"
                  placeholder="http://MY_API_HOST:PORT/API_ROOT">
                <small class="text-muted">
                  Full root including any path prefix; the bridge appends
                  <code>/v1/alerts</code> and <code>/v1/alerts/verify</code>.
                </small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_api_domain">Domain</label>
                <input type="text" class="form-control form-control-sm" id="s_api_domain"
                  value="${_esc(s.alert_api_domain || '')}"
                  style="max-width:300px;">
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_api_token">Bearer Token</label>
                <div class="input-group input-group-sm" style="max-width:500px;">
                  <input type="password" class="form-control" id="s_api_token"
                    value="${_esc(s.alert_api_token || '')}"
                    placeholder="${s.alert_api_token === '(set)' ? '(set — leave unchanged to keep)' : ''}"
                    autocomplete="off">
                  <button class="btn btn-outline-secondary" type="button" id="btn_api_token_show"
                    title="Show/hide token">
                    <i class="bi bi-eye"></i>
                  </button>
                </div>
              </div>

              <div class="mb-3">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_alert_api_verify_tls" ${_checked(s.alert_api_verify_tls !== false)}>
                  <label class="form-check-label" for="s_alert_api_verify_tls">Verify TLS certificate</label>
                </div>
                <small class="text-muted">
                  Uncheck for self-signed or private-CA endpoints.
                </small>
              </div>

              <div class="mb-0 d-flex gap-2">
                <button class="btn btn-sm btn-outline-secondary" id="btn_api_test_auth" type="button">
                  <i class="bi bi-shield-check me-1"></i>Test Auth
                </button>
                <button class="btn btn-sm btn-outline-secondary" id="btn_api_send_test" type="button">
                  <i class="bi bi-send me-1"></i>Send Test Message
                </button>
              </div>

            </div>
          </div>

          <!-- WiFi SSID Detection -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-wifi me-2"></i>WiFi SSID Detection
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label">Enable</label>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="s_wifi_ssid_enabled" ${_checked(s.wifi_ssid_enabled)}>
                  <label class="form-check-label" for="s_wifi_ssid_enabled">Poll sparrow-wifi agent for drone SSIDs</label>
                </div>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_wifi_ssid_agent_url">Agent URL</label>
                <input type="text" class="form-control form-control-sm" id="s_wifi_ssid_agent_url"
                  value="${_esc(s.wifi_ssid_agent_url || 'http://127.0.0.1:8020')}" placeholder="http://127.0.0.1:8020">
                <small class="text-muted">sparrow-wifi agent base URL (no trailing slash)</small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_wifi_ssid_agent_iface">Agent Interface</label>
                <input type="text" class="form-control form-control-sm" id="s_wifi_ssid_agent_iface"
                  value="${_esc(s.wifi_ssid_agent_interface || '')}" placeholder="(auto-detect)" style="max-width:150px;">
                <small class="text-muted">WiFi interface on the agent (e.g. wlan0); blank = auto</small>
              </div>

              <div class="mb-3">
                <label class="form-label" for="s_wifi_ssid_poll_interval">Poll Interval</label>
                <div class="d-flex align-items-center gap-2">
                  <input type="number" class="form-control form-control-sm" id="s_wifi_ssid_poll_interval"
                    value="${s.wifi_ssid_poll_interval || 20}" min="5" max="300" style="max-width:80px;">
                  <span class="text-secondary small">seconds</span>
                </div>
              </div>

              <hr class="my-2">

              <div class="mb-2 d-flex align-items-center justify-content-between">
                <label class="form-label mb-0">SSID Patterns</label>
                <button class="btn btn-sm btn-outline-secondary" id="btn_ssid_add_pattern">
                  <i class="bi bi-plus-lg me-1"></i>Add
                </button>
              </div>

              <!-- Inline add form (hidden by default) -->
              <div id="ssid_add_form" class="mb-2 p-2 border rounded" style="display:none;background:var(--bg-surface,#1e2130);">
                <div class="mb-2">
                  <label class="form-label form-label-sm mb-1">Pattern (regex)</label>
                  <input type="text" class="form-control form-control-sm" id="ssid_new_pattern" placeholder="^PHANTOM">
                  <div id="ssid_pattern_error" class="text-danger small mt-1" style="display:none;"></div>
                </div>
                <div class="mb-2">
                  <label class="form-label form-label-sm mb-1">Label</label>
                  <input type="text" class="form-control form-control-sm" id="ssid_new_label" placeholder="DJI Phantom">
                </div>
                <div class="d-flex gap-2">
                  <button class="btn btn-sm btn-primary" id="btn_ssid_save_pattern">Save</button>
                  <button class="btn btn-sm btn-outline-secondary" id="btn_ssid_cancel_pattern">Cancel</button>
                </div>
              </div>

              <!-- Pattern list -->
              <div id="ssid_pattern_list" style="max-height:220px;overflow-y:auto;">
                ${_buildSsidPatternList(wifiSsidPatterns || [])}
              </div>

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

              <div class="mb-0">
                <small class="text-muted">Vendor codes are loaded from the bundled seed file on first startup.</small>
              </div>

            </div>
          </div>

          <!-- Data Management -->
          <div class="card settings-card mb-3">
            <div class="card-header">
              <i class="bi bi-database me-2"></i>Data Management
            </div>
            <div class="card-body">

              <div class="mb-3">
                <label class="form-label" for="s_retention">Retention Period</label>
                <div class="d-flex align-items-center gap-2">
                  <input type="number" class="form-control form-control-sm" id="s_retention"
                    value="${s.retention_days || 90}" min="1" max="365" style="max-width:80px;">
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

        </div><!-- /col-2 -->
      </div><!-- /row -->
      </div><!-- /generalTabPane -->

      ${_buildEsTabHtml(s)}

      ${_buildTakTabHtml(s, takConfig, takCerts, takIfaces)}

      </div><!-- /tab-content -->
    `;
  }

  function _buildEsTabHtml(s) {
    // Settings are flat es_* keys, not nested
    const setupDismissed = localStorage.getItem('sparrow_es_setup_dismissed') === '1';
    const backendType = _esc(s.es_backend_type || 'elasticsearch');
    const isOpenSearch = backendType === 'opensearch';
    const dashboardsLabel = isOpenSearch ? 'OpenSearch Dashboards Connection' : 'Dashboards Connection';
    const clusterAuthMethod = _esc(s.es_auth_method || 'none');
    const dashAuthMethod = _esc(s.es_dashboards_auth_method || 'none');

    return `
      <div class="tab-pane fade" id="elasticsearchTabPane" role="tabpanel" aria-labelledby="elasticsearchTab-tab">
        <div class="p-3">
          <div class="row g-3">

            <!-- ===== Card 1: Cluster Connection (includes Enable + Backend Type) ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header">
                  <i class="bi bi-hdd-network me-2"></i>Cluster Connection
                </div>
                <div class="card-body">

                  ${setupDismissed ? '' : `
                  <div class="alert alert-info py-2 px-3 mb-3 d-flex align-items-start gap-2" id="es_setup_banner" style="font-size:12px;">
                    <i class="bi bi-info-circle-fill mt-1 flex-shrink-0"></i>
                    <div class="flex-grow-1">
                      Enter your cluster URL, click <strong>Test Cluster</strong>, then <strong>Save Settings</strong>.
                      Default index settings work for most installs.
                    </div>
                    <button type="button" class="btn-close btn-close-white ms-2 flex-shrink-0" id="btn_es_dismiss_banner" aria-label="Dismiss" style="font-size:10px;"></button>
                  </div>`}

                  <div class="d-flex gap-3 mb-3">
                    <div class="form-check form-switch pt-1">
                      <input class="form-check-input" type="checkbox" id="s_es_enabled" ${_checked(s.es_enabled)}>
                      <label class="form-check-label" for="s_es_enabled">Enable</label>
                    </div>
                    <div>
                      <select class="form-select form-select-sm" id="s_es_backend_type" style="max-width:170px;">
                        <option value="elasticsearch" ${_sel(backendType, 'elasticsearch')}>Elasticsearch</option>
                        <option value="opensearch"    ${_sel(backendType, 'opensearch')}>OpenSearch</option>
                      </select>
                    </div>
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_es_url">URL</label>
                    <input type="url" class="form-control form-control-sm" id="s_es_url"
                      value="${_esc(s.es_url || 'https://localhost:9200')}" placeholder="https://localhost:9200">
                  </div>

                  <fieldset class="mb-3">
                    <legend class="form-label">Authentication</legend>

                    <div class="d-flex flex-column gap-1 mb-2">
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_auth_method" id="s_es_auth_none"
                          value="none" ${clusterAuthMethod === 'none' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_es_auth_none">None</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_auth_method" id="s_es_auth_basic"
                          value="basic" ${clusterAuthMethod === 'basic' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_es_auth_basic">Basic (username / password)</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_auth_method" id="s_es_auth_apikey"
                          value="apikey" ${clusterAuthMethod === 'apikey' ? 'checked' : ''}
                          ${isOpenSearch ? 'disabled' : ''}>
                        <label class="form-check-label ${isOpenSearch ? 'text-muted' : ''}" for="s_es_auth_apikey">
                          API Key
                          ${isOpenSearch ? '<span class="ms-1 text-muted" style="font-size:11px;">(Elasticsearch only)</span>' : ''}
                        </label>
                      </div>
                    </div>

                    <div id="es_basic_fields" style="display:${clusterAuthMethod === 'basic' ? '' : 'none'}">
                      <div class="mb-2">
                        <label class="form-label" for="s_es_username">Username</label>
                        <input type="text" class="form-control form-control-sm" id="s_es_username"
                          value="${_esc(s.es_username || '')}" placeholder="elastic" style="max-width:200px;">
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_es_password">Password</label>
                        <input type="password" class="form-control form-control-sm" id="s_es_password"
                          value="" placeholder="${s.es_password === '(set)' ? '(set — enter new to change)' : '(not set)'}"
                          autocomplete="new-password" style="max-width:200px;">
                      </div>
                    </div>

                    <div id="es_apikey_field" style="display:${clusterAuthMethod === 'apikey' ? '' : 'none'}">
                      <div class="mb-2">
                        <label class="form-label" for="s_es_api_key">API Key</label>
                        <input type="password" class="form-control form-control-sm" id="s_es_api_key"
                          value="" placeholder="${s.es_api_key === '(set)' ? '(set — enter new to change)' : '(not set)'}"
                          autocomplete="off" style="max-width:280px;">
                        <small class="text-muted">Base64-encoded id:api_key pair</small>
                      </div>
                    </div>

                  </fieldset>

                  <div class="mb-3">
                    <label class="form-label">TLS</label>
                    <div class="form-check form-switch">
                      <input class="form-check-input" type="checkbox" id="s_es_verify_tls" ${_checked(s.es_verify_tls !== false)}>
                      <label class="form-check-label" for="s_es_verify_tls">Verify TLS certificate</label>
                    </div>
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_es_agent_name">Agent Name</label>
                    <input type="text" class="form-control form-control-sm" id="s_es_agent_name"
                      value="${_esc(s.es_agent_name || '')}" placeholder="" style="max-width:220px;">
                    <small class="text-muted">Tags records by source host (hostname used if blank)</small>
                  </div>

                  <div class="mb-0">
                    <button class="btn btn-sm btn-outline-secondary" id="btn_es_test_cluster" type="button">
                      <i class="bi bi-plug me-1"></i>Test Cluster
                    </button>
                    <span id="es_cluster_test_status" class="ms-2 small text-muted"></span>
                  </div>

                </div>
              </div>
            </div><!-- /col -->

            <!-- ===== Card 2: Dashboards Connection ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header">
                  <i class="bi bi-grid me-2"></i><span id="es_dashboards_card_title">${_esc(dashboardsLabel)}</span>
                </div>
                <div class="card-body">

                  <div class="mb-3">
                    <label class="form-label" for="s_es_dashboards_url">URL</label>
                    <input type="url" class="form-control form-control-sm" id="s_es_dashboards_url"
                      value="${_esc(s.es_dashboards_url || 'http://localhost:5601')}" placeholder="http://localhost:5601">
                    <small class="text-muted">Kibana or OpenSearch Dashboards URL</small>
                  </div>

                  <fieldset class="mb-3">
                    <legend class="form-label">Authentication</legend>

                    <div class="d-flex flex-column gap-1 mb-2">
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_dashboards_auth_method" id="s_es_dash_auth_none"
                          value="none" ${dashAuthMethod === 'none' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_es_dash_auth_none">None</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_dashboards_auth_method" id="s_es_dash_auth_basic"
                          value="basic" ${dashAuthMethod === 'basic' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_es_dash_auth_basic">Basic (username / password)</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="es_dashboards_auth_method" id="s_es_dash_auth_apikey"
                          value="apikey" ${dashAuthMethod === 'apikey' ? 'checked' : ''}
                          ${isOpenSearch ? 'disabled' : ''}>
                        <label class="form-check-label ${isOpenSearch ? 'text-muted' : ''}" for="s_es_dash_auth_apikey">
                          API Key
                          ${isOpenSearch ? '<span class="ms-1 text-muted" style="font-size:11px;">(Elasticsearch only)</span>' : ''}
                        </label>
                      </div>
                    </div>

                    <div id="es_dash_basic_fields" style="display:${dashAuthMethod === 'basic' ? '' : 'none'}">
                      <div class="mb-2">
                        <label class="form-label" for="s_es_dashboards_username">Username</label>
                        <input type="text" class="form-control form-control-sm" id="s_es_dashboards_username"
                          value="${_esc(s.es_dashboards_username || '')}" placeholder="elastic" style="max-width:200px;">
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_es_dashboards_password">Password</label>
                        <input type="password" class="form-control form-control-sm" id="s_es_dashboards_password"
                          value="" placeholder="${s.es_dashboards_password === '(set)' ? '(set — enter new to change)' : '(not set)'}"
                          autocomplete="new-password" style="max-width:200px;">
                      </div>
                    </div>

                    <div id="es_dash_apikey_field" style="display:${dashAuthMethod === 'apikey' ? '' : 'none'}">
                      <div class="mb-2">
                        <label class="form-label" for="s_es_dashboards_api_key">API Key</label>
                        <input type="password" class="form-control form-control-sm" id="s_es_dashboards_api_key"
                          value="" placeholder="${s.es_dashboards_api_key === '(set)' ? '(set — enter new to change)' : '(not set)'}"
                          autocomplete="off" style="max-width:280px;">
                        <small class="text-muted">Base64-encoded id:api_key pair</small>
                      </div>
                    </div>

                  </fieldset>

                  <div class="mb-3">
                    <label class="form-label">TLS</label>
                    <div class="form-check form-switch">
                      <input class="form-check-input" type="checkbox" id="s_es_dashboards_verify_tls" ${_checked(s.es_dashboards_verify_tls !== false)}>
                      <label class="form-check-label" for="s_es_dashboards_verify_tls">Verify TLS certificate</label>
                    </div>
                  </div>

                  <div class="mb-0">
                    <button class="btn btn-sm btn-outline-secondary" id="btn_es_test_dashboards" type="button">
                      <i class="bi bi-plug me-1"></i>Test Dashboards
                    </button>
                    <span id="es_dash_test_status" class="ms-2 small text-muted"></span>
                  </div>

                </div>
              </div>
            </div><!-- /col -->

            <!-- ===== Card 3: Index Configuration ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header">
                  <i class="bi bi-table me-2"></i>Index Configuration
                </div>
                <div class="card-body">

                  <div class="mb-3">
                    <label class="form-label" for="s_es_index_prefix">Index Prefix</label>
                    <input type="text" class="form-control form-control-sm" id="s_es_index_prefix"
                      value="${_esc(s.es_index_prefix || 'sparrow-droneid')}" placeholder="sparrow-droneid" style="max-width:220px;">
                    <small class="text-muted">Prefix for all created indices (e.g. sparrow-droneid-000001)</small>
                  </div>

                  <div class="d-flex gap-3 mb-3">
                    <div>
                      <label class="form-label" for="s_es_shards">Shards</label>
                      <input type="number" class="form-control form-control-sm" id="s_es_shards"
                        value="${s.es_shards || 2}" min="1" step="1" style="max-width:80px;">
                    </div>
                    <div>
                      <label class="form-label" for="s_es_replicas">Replicas</label>
                      <input type="number" class="form-control form-control-sm" id="s_es_replicas"
                        value="${s.es_replicas !== undefined ? s.es_replicas : 0}" min="0" step="1" style="max-width:80px;">
                    </div>
                  </div>
                  <small class="text-muted d-block mb-3" style="margin-top:-0.5rem;">Applied on index creation. 0 replicas for single-node clusters.</small>

                  <div class="mb-3">
                    <label class="form-label" for="s_es_ilm_policy">ILM / ISM Policy</label>
                    <div class="d-flex align-items-center gap-2">
                      <select class="form-select form-select-sm" id="s_es_ilm_policy" style="max-width:200px;">
                        <option value="">(none)</option>
                        ${s.es_ilm_policy ? `<option value="${_esc(s.es_ilm_policy)}" selected>${_esc(s.es_ilm_policy)}</option>` : ''}
                        <option value="__create_new__">+ Create new…</option>
                      </select>
                      <button class="btn btn-sm btn-outline-secondary" id="btn_es_refresh_ilm" type="button" title="Fetch policies from cluster">
                        <i class="bi bi-arrow-clockwise"></i>
                      </button>
                      <span id="es_ilm_status" class="small text-muted"></span>
                    </div>
                    <div id="es_ilm_create_form" style="display:none;" class="mt-2 p-2 rounded border border-secondary">
                      <div class="mb-2">
                        <label class="form-label small mb-1" for="s_es_new_ilm_name">Policy Name</label>
                        <input type="text" class="form-control form-control-sm" id="s_es_new_ilm_name"
                          value="7d-hot-30d-warm-90d-delete" placeholder="7d-hot-30d-warm-90d-delete" style="max-width:220px;">
                      </div>
                      <div class="d-flex gap-3 mb-2">
                        <div>
                          <label class="form-label small mb-1" for="s_es_ilm_hot_days">Hot</label>
                          <div class="d-flex align-items-center gap-1">
                            <input type="number" class="form-control form-control-sm" id="s_es_ilm_hot_days"
                              value="7" min="1" max="365" step="1" style="max-width:60px;">
                            <span class="text-muted small">days</span>
                          </div>
                        </div>
                        <div>
                          <label class="form-label small mb-1" for="s_es_ilm_warm_days">Warm</label>
                          <div class="d-flex align-items-center gap-1">
                            <input type="number" class="form-control form-control-sm" id="s_es_ilm_warm_days"
                              value="30" min="1" max="3650" step="1" style="max-width:60px;">
                            <span class="text-muted small">days</span>
                          </div>
                        </div>
                        <div>
                          <label class="form-label small mb-1" for="s_es_ilm_delete_days">Delete</label>
                          <div class="d-flex align-items-center gap-1">
                            <input type="number" class="form-control form-control-sm" id="s_es_ilm_delete_days"
                              value="90" min="1" max="3650" step="1" style="max-width:60px;">
                            <span class="text-muted small">days</span>
                          </div>
                        </div>
                      </div>
                      <div class="d-flex align-items-center gap-2">
                        <button class="btn btn-sm btn-outline-primary" id="btn_es_create_ilm" type="button">Create</button>
                        <button class="btn btn-sm btn-outline-secondary" id="btn_es_cancel_ilm" type="button">Cancel</button>
                      </div>
                      <small class="text-muted">Hot: active writes + rollover. Warm: read-only + force-merge. Delete: purge.</small>
                    </div>
                    <small class="text-muted">Lifecycle policy for rollover and retention</small>
                  </div>

                  <div class="mb-0">
                    <a href="#" id="es_manage_policies_link" class="small text-muted"
                      target="_blank" rel="noopener noreferrer"
                      style="display:${s.es_dashboards_url ? '' : 'none'};">
                      <i class="bi bi-box-arrow-up-right me-1"></i>Manage policies in Kibana / OSD
                    </a>
                  </div>

                </div>
              </div>
            </div><!-- /col -->

            <!-- ===== Card 4: Dashboard Management ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header" style="background:var(--bg-elevated);">
                  <i class="bi bi-layout-wtf me-2"></i>Dashboard Management
                </div>
                <div class="card-body">

                  <p class="text-secondary small mb-3">
                    Push pre-built drone detection dashboards to your Kibana / OpenSearch Dashboards instance.
                  </p>

                  <div class="mb-3">
                    <div class="form-check form-switch">
                      <input class="form-check-input" type="checkbox" id="s_es_overwrite_dashboards">
                      <label class="form-check-label" for="s_es_overwrite_dashboards">
                        Overwrite existing dashboards
                      </label>
                    </div>
                    <small class="text-danger">Warning: overwrites any customisations you've made</small>
                  </div>

                  <div class="mb-3">
                    <button class="btn btn-sm btn-outline-primary" id="btn_es_push_dashboards" type="button">
                      <i class="bi bi-cloud-upload me-1"></i>Create Dashboards
                    </button>
                  </div>

                  <div id="es_dashboard_status" class="small text-muted">Not installed</div>

                </div>
              </div>
            </div><!-- /col -->

            <!-- ===== Card 5: Ingest ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header">
                  <i class="bi bi-arrow-right-circle me-2"></i>Ingest
                </div>
                <div class="card-body">

                  <div class="mb-3">
                    <label class="form-label" for="s_es_bulk_size">Bulk Batch Size</label>
                    <div class="d-flex align-items-center gap-2">
                      <input type="number" class="form-control form-control-sm" id="s_es_bulk_size"
                        value="${s.es_bulk_size || 100}" min="1" max="10000" step="1" style="max-width:100px;">
                      <span class="text-secondary small">records</span>
                    </div>
                    <small class="text-muted">Max detections per bulk request</small>
                  </div>

                  <div class="mb-0">
                    <label class="form-label" for="s_es_flush_interval">Flush Interval</label>
                    <div class="d-flex align-items-center gap-2">
                      <input type="number" class="form-control form-control-sm" id="s_es_flush_interval"
                        value="${s.es_flush_interval || 5}" min="1" max="60" step="1" style="max-width:80px;">
                      <span class="text-secondary small">seconds</span>
                    </div>
                    <small class="text-muted">Maximum time between flushes to the cluster</small>
                  </div>

                </div>
              </div>
            </div><!-- /col -->

          </div><!-- /row -->
        </div><!-- /p-3 -->
      </div><!-- /elasticsearchTabPane -->
    `;
  }

  function _buildSsidPatternList(patterns) {
    if (!patterns || patterns.length === 0) {
      return '<p class="text-secondary small mb-0">No patterns configured.</p>';
    }
    const rows = patterns.map((p, i) => `
      <div class="d-flex align-items-center gap-2 py-1 border-bottom ssid-pattern-row" data-index="${i}">
        <code class="flex-grow-1 small text-truncate" style="max-width:160px;" title="${_esc(p.pattern)}">${_esc(p.pattern)}</code>
        <span class="text-secondary small text-truncate" style="max-width:120px;">${_esc(p.label || '')}</span>
        <button class="btn btn-xs btn-outline-danger btn-ssid-del-pattern ms-auto flex-shrink-0" data-index="${i}" type="button">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>`).join('');
    return rows;
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

    // API-based alerting: bearer token show/hide.  We swap input type, not the
    // value, so the field is always submittable and Save Settings still gates
    // on the '(set)' placeholder via the _collect token guard.
    document.getElementById('btn_api_token_show')?.addEventListener('click', () => {
      const input = document.getElementById('s_api_token');
      const icon = document.querySelector('#btn_api_token_show i');
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        if (icon) { icon.classList.remove('bi-eye'); icon.classList.add('bi-eye-slash'); }
      } else {
        input.type = 'password';
        if (icon) { icon.classList.remove('bi-eye-slash'); icon.classList.add('bi-eye'); }
      }
    });

    // Helper: gather the API form values for either test endpoint.
    // Token retains the '(set)' placeholder when the user didn't retype —
    // the backend resolves it to the stored value.
    const _collectApiTestBody = () => {
      const baseUrl = document.getElementById('s_api_base_url')?.value?.trim() || '';
      const domain  = document.getElementById('s_api_domain')?.value?.trim() || '';
      const token   = document.getElementById('s_api_token')?.value || '';
      const verifyTls = !!(document.getElementById('s_alert_api_verify_tls')?.checked);
      return { base_url: baseUrl, domain, token, verify_tls: verifyTls };
    };

    // API-based alerting: Test Auth — calls /alerts/api-test (verify endpoint).
    document.getElementById('btn_api_test_auth')?.addEventListener('click', async () => {
      const body = _collectApiTestBody();
      if (!body.base_url) { Utils.toast('Enter the API endpoint root first', 'warning'); return; }
      if (!body.domain)   { Utils.toast('Enter the API domain first',         'warning'); return; }
      const btn = document.getElementById('btn_api_test_auth');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Testing…';
      try {
        const resp = await Api.post('/alerts/api-test', body);
        if (resp.success) {
          Utils.toast(resp.message || 'Authentication verified', 'success');
        } else {
          Utils.toast('Auth test failed: ' + (resp.error || 'Unknown error'), 'danger');
        }
      } catch (e) {
        Utils.toast('Auth test error: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-shield-check me-1"></i>Test Auth';
    });

    // API-based alerting: Send Test Message — POSTs a synthetic alert.
    document.getElementById('btn_api_send_test')?.addEventListener('click', async () => {
      const body = _collectApiTestBody();
      if (!body.base_url) { Utils.toast('Enter the API endpoint root first', 'warning'); return; }
      if (!body.domain)   { Utils.toast('Enter the API domain first',         'warning'); return; }
      const btn = document.getElementById('btn_api_send_test');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Sending…';
      try {
        const resp = await Api.post('/alerts/api-send-test', body);
        if (resp.success) {
          Utils.toast(resp.message || 'Test alert sent', 'success');
        } else {
          Utils.toast('Send test failed: ' + (resp.error || 'Unknown error'), 'danger');
        }
      } catch (e) {
        Utils.toast('Send test error: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-send me-1"></i>Send Test Message';
    });

    // Purge data
    document.getElementById('btnPurgeData')?.addEventListener('click', async () => {
      if (!confirm('Purge data older than the retention period?')) return;
      const days = parseInt(document.getElementById('s_retention')?.value || '90');
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

    // WiFi SSID pattern management
    document.getElementById('btn_ssid_add_pattern')?.addEventListener('click', () => {
      const form = document.getElementById('ssid_add_form');
      if (form) {
        form.style.display = form.style.display === 'none' ? '' : 'none';
        if (form.style.display !== 'none') {
          document.getElementById('ssid_new_pattern')?.focus();
        }
      }
    });

    document.getElementById('btn_ssid_cancel_pattern')?.addEventListener('click', () => {
      const form = document.getElementById('ssid_add_form');
      if (form) form.style.display = 'none';
      const errEl = document.getElementById('ssid_pattern_error');
      if (errEl) errEl.style.display = 'none';
    });

    document.getElementById('btn_ssid_save_pattern')?.addEventListener('click', async () => {
      const patternInput = document.getElementById('ssid_new_pattern');
      const labelInput   = document.getElementById('ssid_new_label');
      const errEl        = document.getElementById('ssid_pattern_error');
      const pattern = patternInput?.value.trim() || '';
      const label   = labelInput?.value.trim() || '';

      if (!pattern) {
        if (errEl) { errEl.textContent = 'Pattern is required'; errEl.style.display = ''; }
        return;
      }

      // Validate regex client-side before sending
      try { new RegExp(pattern); } catch (e) {
        if (errEl) { errEl.textContent = `Invalid regex: ${e.message}`; errEl.style.display = ''; }
        return;
      }
      if (errEl) errEl.style.display = 'none';

      const newPatterns = [..._wifiSsidPatterns, { pattern, label }];
      try {
        const result = await Api.putWifiSsidPatterns(newPatterns);
        _wifiSsidPatterns = result.patterns || newPatterns;
        const listEl = document.getElementById('ssid_pattern_list');
        if (listEl) listEl.innerHTML = _buildSsidPatternList(_wifiSsidPatterns);
        _reattachSsidDeleteListeners();
        const form = document.getElementById('ssid_add_form');
        if (form) form.style.display = 'none';
        if (patternInput) patternInput.value = '';
        if (labelInput)   labelInput.value   = '';
        Utils.toast('Pattern added', 'success');
      } catch (e) {
        Utils.toast('Failed to save pattern: ' + e.message, 'danger');
      }
    });

    _reattachSsidDeleteListeners();

    // ---- TAK listeners ----
    _attachTakListeners();

    // ---- Elasticsearch listeners ----

    // Quick setup banner dismiss
    document.getElementById('btn_es_dismiss_banner')?.addEventListener('click', () => {
      localStorage.setItem('sparrow_es_setup_dismissed', '1');
      const banner = document.getElementById('es_setup_banner');
      if (banner) banner.style.display = 'none';
    });

    // Backend type change: disable API Key for OpenSearch, update dashboards card title
    document.getElementById('s_es_backend_type')?.addEventListener('change', e => {
      const isOpenSearch = e.target.value === 'opensearch';
      _applyBackendTypeUi(isOpenSearch);
    });

    // Cluster auth method toggle
    document.querySelectorAll('input[name="es_auth_method"]').forEach(radio => {
      radio.addEventListener('change', () => _updateEsAuthFields());
    });

    // Dashboards auth method toggle
    document.querySelectorAll('input[name="es_dashboards_auth_method"]').forEach(radio => {
      radio.addEventListener('change', () => _updateEsDashAuthFields());
    });

    // Test Cluster
    document.getElementById('btn_es_test_cluster')?.addEventListener('click', async () => {
      const btn = document.getElementById('btn_es_test_cluster');
      const statusEl = document.getElementById('es_cluster_test_status');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Saving…';
      if (statusEl) { statusEl.textContent = ''; statusEl.className = 'ms-2 small text-muted'; }
      await _saveEsConnectionSettings();
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Testing…';
      try {
        const resp = await Api.testEsCluster();
        if (statusEl) {
          const detail = resp.cluster_name && resp.version
            ? `${resp.cluster_name} v${resp.version}`
            : 'Connected';
          statusEl.textContent = detail;
          statusEl.className = 'ms-2 small text-success';
        }
        Utils.toast('Cluster connection successful', 'success');
        // Auto-refresh ILM policies on successful connection test
        _loadIlmPolicies();
      } catch (e) {
        if (statusEl) {
          statusEl.textContent = e.message || 'Connection failed';
          statusEl.className = 'ms-2 small text-danger';
        }
        Utils.toast('Cluster test failed: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-plug me-1"></i>Test Cluster';
    });

    // Test Dashboards
    document.getElementById('btn_es_test_dashboards')?.addEventListener('click', async () => {
      const btn = document.getElementById('btn_es_test_dashboards');
      const statusEl = document.getElementById('es_dash_test_status');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Saving…';
      if (statusEl) { statusEl.textContent = ''; statusEl.className = 'ms-2 small text-muted'; }
      await _saveEsConnectionSettings();
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Testing…';
      try {
        const resp = await Api.testEsDashboards();
        if (resp.ok && statusEl) {
          const detail = resp.version ? `v${resp.version}` : 'Connected';
          statusEl.textContent = detail;
          statusEl.className = 'ms-2 small text-success';
        } else if (statusEl) {
          statusEl.textContent = resp.error || 'Connection failed';
          statusEl.className = 'ms-2 small text-danger';
        }
        if (resp.ok) Utils.toast('Dashboards connection successful', 'success');
        else Utils.toast('Dashboards test failed: ' + (resp.error || 'Unknown error'), 'danger');
      } catch (e) {
        if (statusEl) {
          statusEl.textContent = e.message || 'Connection failed';
          statusEl.className = 'ms-2 small text-danger';
        }
        Utils.toast('Dashboards test failed: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-plug me-1"></i>Test Dashboards';
    });

    // Create Dashboards
    document.getElementById('btn_es_push_dashboards')?.addEventListener('click', async () => {
      const overwrite = document.getElementById('s_es_overwrite_dashboards')?.checked || false;
      const msg = overwrite
        ? 'Push dashboards and overwrite any existing customisations?'
        : 'Push pre-built dashboards? Existing dashboards will not be overwritten.';
      if (!confirm(msg)) return;
      const btn = document.getElementById('btn_es_push_dashboards');
      const statusEl = document.getElementById('es_dashboard_status');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Pushing…';
      if (statusEl) { statusEl.textContent = 'Installing…'; statusEl.className = 'small text-muted'; }
      try {
        const resp = await Api.pushEsDashboards(overwrite);
        if (statusEl) {
          statusEl.textContent = resp.message || 'Dashboards installed';
          statusEl.className = 'small text-success';
        }
        Utils.toast('Dashboards pushed successfully', 'success');
      } catch (e) {
        if (statusEl) {
          statusEl.textContent = 'Failed: ' + e.message;
          statusEl.className = 'small text-danger';
        }
        Utils.toast('Dashboard push failed: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-cloud-upload me-1"></i>Create Dashboards';
    });

    // ILM policies: load when ES tab is shown; update footer hint
    document.getElementById('elasticsearchTab-tab')?.addEventListener('shown.bs.tab', () => {
      _loadIlmPolicies();
      const hint = document.getElementById('settingsFooterHint');
      if (hint) hint.innerHTML = '<i class="bi bi-info-circle me-1"></i>Elasticsearch settings apply immediately — no restart needed';
    });

    // Restore footer hint when General tab is shown
    document.getElementById('generalTab-tab')?.addEventListener('shown.bs.tab', () => {
      const hint = document.getElementById('settingsFooterHint');
      if (hint) hint.innerHTML = '<i class="bi bi-info-circle me-1"></i>Some changes (port, HTTPS) require a restart';
    });

    // ILM policies: manual refresh button
    document.getElementById('btn_es_refresh_ilm')?.addEventListener('click', async () => {
      const btn = document.getElementById('btn_es_refresh_ilm');
      const statusEl = document.getElementById('es_ilm_status');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split"></i>';
      if (statusEl) { statusEl.textContent = ''; statusEl.className = 'small text-muted'; }
      await _saveEsConnectionSettings();
      try {
        await _loadIlmPolicies();
        const select = document.getElementById('s_es_ilm_policy');
        // Subtract 2 for the fixed options: (none) and + Create new…
        const count = select ? Math.max(0, select.options.length - 2) : 0;
        if (statusEl) {
          statusEl.textContent = count > 0 ? `${count} found` : 'No policies found';
          statusEl.className = count > 0 ? 'small text-success' : 'small text-warning';
        }
      } catch (_) {
        if (statusEl) {
          statusEl.textContent = 'Could not reach cluster';
          statusEl.className = 'small text-danger';
        }
      }
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
    });

    // ILM: show create form when "Create new" is selected
    document.getElementById('s_es_ilm_policy')?.addEventListener('change', (e) => {
      const createForm = document.getElementById('es_ilm_create_form');
      if (e.target.value === '__create_new__') {
        if (createForm) createForm.style.display = '';
        document.getElementById('s_es_new_ilm_name')?.focus();
      } else {
        if (createForm) createForm.style.display = 'none';
      }
    });

    // ILM: cancel create
    document.getElementById('btn_es_cancel_ilm')?.addEventListener('click', () => {
      const select = document.getElementById('s_es_ilm_policy');
      const createForm = document.getElementById('es_ilm_create_form');
      if (createForm) createForm.style.display = 'none';
      if (select) select.value = '';  // Reset to (none)
    });

    // ILM: create policy
    document.getElementById('btn_es_create_ilm')?.addEventListener('click', async () => {
      const nameInput = document.getElementById('s_es_new_ilm_name');
      const name = (nameInput?.value || '').trim();
      if (!name) {
        Utils.toast('Policy name is required', 'warning');
        nameInput?.focus();
        return;
      }
      const hotDays = parseInt(document.getElementById('s_es_ilm_hot_days')?.value || '7', 10);
      const warmDays = parseInt(document.getElementById('s_es_ilm_warm_days')?.value || '30', 10);
      const deleteDays = parseInt(document.getElementById('s_es_ilm_delete_days')?.value || '90', 10);
      if (warmDays <= hotDays) {
        Utils.toast('Warm phase must be greater than hot phase', 'warning');
        return;
      }
      if (deleteDays <= warmDays) {
        Utils.toast('Delete threshold must be greater than warm phase', 'warning');
        return;
      }
      const btn = document.getElementById('btn_es_create_ilm');
      btn.disabled = true;
      btn.textContent = 'Saving…';
      await _saveEsConnectionSettings();
      btn.textContent = 'Creating…';
      try {
        const resp = await Api.createEsIlmPolicy(name, hotDays, warmDays, deleteDays);
        if (resp.ok) {
          Utils.toast(`Policy "${name}" created`, 'success');
          // Refresh the policy list and select the new one
          await _loadIlmPolicies();
          const select = document.getElementById('s_es_ilm_policy');
          if (select) select.value = name;
          const createForm = document.getElementById('es_ilm_create_form');
          if (createForm) createForm.style.display = 'none';
        } else {
          Utils.toast('Failed to create policy: ' + (resp.error || 'Unknown error'), 'danger');
        }
      } catch (e) {
        Utils.toast('Failed to create policy: ' + e.message, 'danger');
      }
      btn.disabled = false;
      btn.textContent = 'Create';
    });

  }

  function _applyBackendTypeUi(isOpenSearch) {
    // Update dashboards card title
    const titleEl = document.getElementById('es_dashboards_card_title');
    if (titleEl) titleEl.textContent = isOpenSearch ? 'OpenSearch Dashboards Connection' : 'Dashboards Connection';

    // Disable API Key radio for both cluster and dashboards
    const clusterApiKeyRadio = document.getElementById('s_es_auth_apikey');
    const dashApiKeyRadio    = document.getElementById('s_es_dash_auth_apikey');

    if (clusterApiKeyRadio) {
      clusterApiKeyRadio.disabled = isOpenSearch;
      const lbl = clusterApiKeyRadio.nextElementSibling;
      if (lbl) lbl.className = isOpenSearch ? 'form-check-label text-muted' : 'form-check-label';
      // If currently selected and now disabled, fall back to none
      if (isOpenSearch && clusterApiKeyRadio.checked) {
        const noneRadio = document.getElementById('s_es_auth_none');
        if (noneRadio) noneRadio.checked = true;
        _updateEsAuthFields();
      }
    }

    if (dashApiKeyRadio) {
      dashApiKeyRadio.disabled = isOpenSearch;
      const lbl = dashApiKeyRadio.nextElementSibling;
      if (lbl) lbl.className = isOpenSearch ? 'form-check-label text-muted' : 'form-check-label';
      if (isOpenSearch && dashApiKeyRadio.checked) {
        const noneRadio = document.getElementById('s_es_dash_auth_none');
        if (noneRadio) noneRadio.checked = true;
        _updateEsDashAuthFields();
      }
    }
  }

  function _updateEsAuthFields() {
    const method = document.querySelector('input[name="es_auth_method"]:checked')?.value || 'none';
    const basicDiv  = document.getElementById('es_basic_fields');
    const apikeyDiv = document.getElementById('es_apikey_field');
    if (basicDiv)  basicDiv.style.display  = method === 'basic'  ? '' : 'none';
    if (apikeyDiv) apikeyDiv.style.display = method === 'apikey' ? '' : 'none';
  }

  function _updateEsDashAuthFields() {
    const method = document.querySelector('input[name="es_dashboards_auth_method"]:checked')?.value || 'none';
    const basicDiv  = document.getElementById('es_dash_basic_fields');
    const apikeyDiv = document.getElementById('es_dash_apikey_field');
    if (basicDiv)  basicDiv.style.display  = method === 'basic'  ? '' : 'none';
    if (apikeyDiv) apikeyDiv.style.display = method === 'apikey' ? '' : 'none';
  }

  async function _saveEsConnectionSettings() {
    // Persist current ES connection fields (cluster + dashboards) to the
    // backend so that ad-hoc operations (test, ILM query, policy create,
    // dashboard push) can use them before the user clicks Save Settings.
    const conn = {};
    // Cluster connection
    conn.es_backend_type = document.getElementById('s_es_backend_type')?.value || 'elasticsearch';
    conn.es_url = document.getElementById('s_es_url')?.value?.trim() || '';
    conn.es_auth_method = document.querySelector('input[name="es_auth_method"]:checked')?.value || 'none';
    conn.es_username = document.getElementById('s_es_username')?.value?.trim() || '';
    conn.es_verify_tls = !!(document.getElementById('s_es_verify_tls')?.checked);
    conn.es_index_prefix = document.getElementById('s_es_index_prefix')?.value?.trim() || 'sparrow-droneid';
    const pw = document.getElementById('s_es_password')?.value || '';
    if (pw && pw !== '(set)') conn.es_password = pw;
    const ak = document.getElementById('s_es_api_key')?.value || '';
    if (ak && ak !== '(set)') conn.es_api_key = ak;
    // Dashboards connection
    conn.es_dashboards_url = document.getElementById('s_es_dashboards_url')?.value?.trim() || '';
    conn.es_dashboards_auth_method = document.querySelector('input[name="es_dashboards_auth_method"]:checked')?.value || 'none';
    conn.es_dashboards_username = document.getElementById('s_es_dashboards_username')?.value?.trim() || '';
    conn.es_dashboards_verify_tls = !!(document.getElementById('s_es_dashboards_verify_tls')?.checked);
    const dpw = document.getElementById('s_es_dashboards_password')?.value || '';
    if (dpw && dpw !== '(set)') conn.es_dashboards_password = dpw;
    const dak = document.getElementById('s_es_dashboards_api_key')?.value || '';
    if (dak && dak !== '(set)') conn.es_dashboards_api_key = dak;
    try {
      await Api.putSettings(conn);
    } catch (_) {
      // Best-effort — don't block the operation
    }
  }

  async function _loadIlmPolicies() {
    const select = document.getElementById('s_es_ilm_policy');
    if (!select) return;
    const currentVal = select.value === '__create_new__' ? '' : select.value;
    const createNewOpt = '<option value="__create_new__">+ Create new…</option>';
    try {
      const resp = await Api.getEsIlmPolicies();
      // resp.policies is { name: body, ... } — extract names
      const policyObj = resp.policies || {};
      const names = Object.keys(policyObj).sort();
      select.innerHTML = '<option value="">(none)</option>' +
        names.map(p => `<option value="${_esc(p)}" ${p === currentVal ? 'selected' : ''}>${_esc(p)}</option>`).join('') +
        createNewOpt;
      if (currentVal && names.includes(currentVal)) {
        select.value = currentVal;
      }
    } catch (_) {
      // Cluster unreachable — keep existing options
      if (select.options.length <= 1 && currentVal) {
        select.innerHTML = `<option value="">(none)</option><option value="${_esc(currentVal)}" selected>${_esc(currentVal)}</option>` + createNewOpt;
      }
    }
  }

  // ---- WiFi SSID pattern delete ----
  function _reattachSsidDeleteListeners() {
    document.querySelectorAll('.btn-ssid-del-pattern').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.index, 10);
        if (isNaN(idx) || idx < 0 || idx >= _wifiSsidPatterns.length) return;
        const newPatterns = _wifiSsidPatterns.filter((_, i) => i !== idx);
        try {
          const result = await Api.putWifiSsidPatterns(newPatterns);
          _wifiSsidPatterns = result.patterns || newPatterns;
          const listEl = document.getElementById('ssid_pattern_list');
          if (listEl) listEl.innerHTML = _buildSsidPatternList(_wifiSsidPatterns);
          _reattachSsidDeleteListeners();
          Utils.toast('Pattern removed', 'success');
        } catch (e) {
          Utils.toast('Failed to remove pattern: ' + e.message, 'danger');
        }
      });
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

  // =====================================================================
  // TAK tab
  // =====================================================================

  const _TAK_POLL_MS = 2000;
  const _TAK_CERT_MAX = 256 * 1024;
  const _TAK_ZIP_MAX = 2 * 1024 * 1024;

  // [setting key, element id, kind]. kind: bool | int | float | str | select |
  // radio (id = group wrapper, radios share a name) | secret.
  const _TAK_FIELDS = [
    ['cot_enabled',                 's_tak_enabled',            'bool'],
    ['tak_transport',               's_tak_transport',          'select'],
    ['cot_address',                 's_tak_host',               'str'],
    ['cot_port',                    's_tak_port',               'int'],
    ['tak_multicast_interface',     's_tak_mcast_iface',        'select'],
    ['tak_multicast_ttl',           's_tak_mcast_ttl',          'int'],
    ['tak_auth_username',           's_tak_auth_user',          'str'],
    ['tak_auth_password',           's_tak_auth_pass',          'secret'],
    ['tak_client_uid',              's_tak_client_uid',         'str'],
    ['tak_station_name',            's_tak_station',            'str'],
    ['tak_uid_prefix',              's_tak_uid_prefix',         'str'],
    ['tak_tls_client_mode',         'tak_client_mode_group',    'radio'],
    ['tak_tls_client_cert_path',    's_tak_client_cert_path',   'str'],
    ['tak_tls_client_key_path',     's_tak_client_key_path',    'str'],
    ['tak_tls_client_password',     's_tak_client_pass',        'secret'],
    ['tak_tls_ca_mode',             'tak_ca_mode_group',        'radio'],
    ['tak_tls_ca_path',             's_tak_ca_path',            'str'],
    ['tak_tls_ca_password',         's_tak_ca_pass',            'secret'],
    ['tak_tls_verify',              's_tak_tls_verify',         'bool'],
    ['tak_tls_check_hostname',      's_tak_tls_check_hostname', 'bool'],
    ['tak_enroll_port',             's_tak_enroll_port',        'int'],
    ['tak_enroll_username',         's_tak_enroll_user',        'str'],
    ['tak_enroll_password',         's_tak_enroll_pass',        'secret'],
    ['tak_send_interval_s',         's_tak_send_interval',      'float'],
    ['tak_refresh_interval_s',      's_tak_refresh_interval',   'float'],
    ['tak_stale_after_s',           's_tak_stale_after',        'float'],
    ['tak_delete_after_s',          's_tak_delete_after',       'float'],
    ['tak_send_deletes',            's_tak_send_deletes',       'bool'],
    ['tak_unknown_affiliation',     's_tak_unknown_aff',        'select'],
    ['tak_drone_type_family',       's_tak_type_family',        'select'],
    ['tak_operator_enabled',        's_tak_operator',           'bool'],
    ['tak_line_enabled',            's_tak_line',               'bool'],
    ['tak_include_ssid_detections', 's_tak_ssid',               'bool'],
    ['tak_sensor_enabled',          's_tak_sensor',             'bool'],
    ['tak_sensor_heartbeat_s',      's_tak_sensor_hb',          'float'],
    ['tak_sensor_stale_s',          's_tak_sensor_stale',       'str'],
  ];

  // setting key -> element id (used to attach server/client validation errors)
  const _TAK_FIELD_IDS = {};
  _TAK_FIELDS.forEach(([key, id]) => { _TAK_FIELD_IDS[key] = id; });

  const _TAK_TIMING_KEYS = ['tak_send_interval_s', 'tak_refresh_interval_s',
                            'tak_stale_after_s', 'tak_delete_after_s'];

  const _TAK_HOST_UI = {
    udp_multicast: { label: 'Multicast group',  placeholder: '239.2.3.1' },
    udp_broadcast: { label: 'Broadcast address', placeholder: '192.168.1.255' },
    udp_unicast:   { label: 'Host',             placeholder: '192.168.1.50' },
    tcp:           { label: 'Host',             placeholder: 'takserver.local' },
    tls:           { label: 'TAK Server host',  placeholder: 'takserver.local' },
  };
  const _TAK_DEFAULT_PORTS = { udp_multicast: 6969, udp_broadcast: 6969, udp_unicast: 4242, tcp: 8087, tls: 8089 };

  function _takDefaultPorts() {
    return (_takConfig && _takConfig.default_ports) || _TAK_DEFAULT_PORTS;
  }

  function _el(id) { return document.getElementById(id); }

  function _fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
  }

  function _takSecretInput(id, key, label, t, extraAttrs) {
    const isSet = t[key] === '(set)';
    return `
      <label class="form-label" for="${id}">${label}</label>
      <input type="password" class="form-control form-control-sm" id="${id}"
        value="" placeholder="${isSet ? '(set — enter new to change)' : '(not set)'}"
        autocomplete="new-password" ${extraAttrs || ''}>
      ${isSet ? `<small class="text-muted"><button type="button" class="btn btn-link btn-sm p-0 align-baseline"
        data-clear-for="${id}" style="font-size:11px;">Clear</button>
        <span class="tak-clear-note" id="${id}_clear_note" style="display:none;">will be cleared on Save</span></small>` : ''}`;
  }

  function _takSwitch(id, label, checked, extra) {
    return `
      <div class="form-check form-switch mb-2">
        <input class="form-check-input" type="checkbox" id="${id}" ${_checked(checked)} ${extra || ''}>
        <label class="form-check-label" for="${id}">${label}</label>
      </div>`;
  }

  function _takCertSummaryHtml(info, emptyText) {
    if (!info) return `<div class="text-muted">${_esc(emptyText)}</div>`;
    let days = '';
    if (typeof info.days_left === 'number') {
      const cls = (info.expired || info.days_left < 14) ? 'tak-days-bad' : (info.days_left < 30 ? 'tak-days-warn' : '');
      days = info.expired
        ? ` <span class="tak-days-bad">(expired)</span>`
        : ` <span class="${cls}">(${_esc(info.days_left)} days left)</span>`;
    }
    const src = [info.source, info.filename].filter(Boolean).map(_esc).join(' / ');
    return `
      <div><strong>${_esc(info.subject || '')}</strong></div>
      ${info.issuer ? `<div class="text-muted">Issuer: ${_esc(info.issuer)}</div>` : ''}
      ${info.not_after ? `<div>Expires: ${_esc(_fmtTime(info.not_after))}${days}</div>` : ''}
      ${src ? `<div class="text-muted">Source: ${src}</div>` : ''}`;
  }

  function _takCaSummaryHtml(info) {
    if (!info) return '<div class="text-muted">No CA truststore uploaded</div>';
    const subjects = (info.subjects || []).map(s => `<li>${_esc(s)}</li>`).join('');
    const src = [info.source, info.filename].filter(Boolean).map(_esc).join(' / ');
    return `
      <div><strong>${_esc(info.count)}</strong> CA certificate${info.count === 1 ? '' : 's'}</div>
      ${subjects ? `<ul class="mb-1 ps-3">${subjects}</ul>` : ''}
      ${info.earliest_not_after ? `<div>Earliest expiry: ${_esc(_fmtTime(info.earliest_not_after))}</div>` : ''}
      ${src ? `<div class="text-muted">Source: ${src}</div>` : ''}`;
  }

  function _buildTakTabHtml(s, takConfig, takCerts, takIfaces) {
    if (!takConfig || !takConfig.settings) {
      return `
        <div class="tab-pane fade" id="takTabPane" role="tabpanel" aria-labelledby="takTab-tab">
          <div class="p-3 text-danger">TAK settings are unavailable (the server did not return them).</div>
        </div>`;
    }
    const t = takConfig.settings;
    const eff = takConfig.effective || {};
    const certs = takCerts || {};
    const transport = _esc(t.tak_transport || 'udp_multicast');
    const hostUi = _TAK_HOST_UI[t.tak_transport] || _TAK_HOST_UI.udp_multicast;
    const clientMode = t.tak_tls_client_mode || 'upload';
    const caMode = t.tak_tls_ca_mode || 'none';
    let bannerDismissed = false;
    try { bannerDismissed = localStorage.getItem('sparrow_tak_setup_dismissed') === '1'; } catch (_) { /* ignore */ }
    const ifaces = takIfaces || [];
    const storedIface = t.tak_multicast_interface || '';
    const ifaceKnown = !storedIface || ifaces.some(i => i.name === storedIface);
    const staleAutoPh = eff.sensor_stale_s != null ? `auto (${eff.sensor_stale_s}s)` : 'auto';
    const staleAfter = t.tak_stale_after_s, deleteAfter = t.tak_delete_after_s;

    return `
      <div class="tab-pane fade" id="takTabPane" role="tabpanel" aria-labelledby="takTab-tab">
        <div class="p-3">
          <div class="row g-3">

            <!-- ===== Card 1: TAK Output ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-broadcast me-2"></i>TAK Output</div>
                <div class="card-body">

                  ${bannerDismissed ? '' : `
                  <div class="alert alert-info py-2 px-3 mb-3 d-flex align-items-start gap-2" id="tak_setup_banner" style="font-size:12px;">
                    <i class="bi bi-info-circle-fill mt-1 flex-shrink-0"></i>
                    <div class="flex-grow-1">
                      Have a TAK data package (.zip)? Import it in <strong>Data Package</strong> below,
                      then <strong>Test Connection</strong> and <strong>Save</strong>.
                    </div>
                    <button type="button" class="btn-close btn-close-white ms-2 flex-shrink-0" id="btn_tak_dismiss_banner" aria-label="Dismiss" style="font-size:10px;"></button>
                  </div>`}

                  ${_takSwitch('s_tak_enabled', 'Enable TAK output', t.cot_enabled)}

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_transport">Transport</label>
                    <select class="form-select form-select-sm" id="s_tak_transport" style="max-width:260px;" data-prev="${transport}">
                      <option value="udp_multicast" ${_sel(t.tak_transport, 'udp_multicast')}>UDP multicast</option>
                      <option value="udp_broadcast" ${_sel(t.tak_transport, 'udp_broadcast')}>UDP broadcast</option>
                      <option value="udp_unicast"   ${_sel(t.tak_transport, 'udp_unicast')}>UDP unicast</option>
                      <option value="tcp"           ${_sel(t.tak_transport, 'tcp')}>TCP</option>
                      <option value="tls"           ${_sel(t.tak_transport, 'tls')}>TLS (TAK Server)</option>
                    </select>
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_host" id="tak_host_label">${_esc(hostUi.label)}</label>
                    <input type="text" class="form-control form-control-sm" id="s_tak_host"
                      value="${_esc(t.cot_address || '')}" placeholder="${_esc(hostUi.placeholder)}" style="max-width:300px;">
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_port">Port</label>
                    <input type="number" class="form-control form-control-sm" id="s_tak_port"
                      value="${_esc(t.cot_port)}" min="1" max="65535" style="max-width:110px;">
                  </div>

                  <div id="tak_mcast_block">
                    <div class="mb-3">
                      <label class="form-label" for="s_tak_mcast_iface">Multicast interface</label>
                      <select class="form-select form-select-sm" id="s_tak_mcast_iface" style="max-width:260px;">
                        <option value="">(OS default)</option>
                        ${ifaces.map(i => `<option value="${_esc(i.name)}" ${_sel(storedIface, i.name)}>${_esc(i.name)} (${_esc(i.ipv4)})</option>`).join('')}
                        ${ifaceKnown ? '' : `<option value="${_esc(storedIface)}" selected>${_esc(storedIface)} (not found)</option>`}
                      </select>
                    </div>
                    <div class="mb-3">
                      <label class="form-label" for="s_tak_mcast_ttl">Multicast TTL</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_mcast_ttl"
                        value="${_esc(t.tak_multicast_ttl)}" min="1" max="255" style="max-width:110px;">
                      <small class="text-muted">1 keeps traffic on this LAN segment</small>
                    </div>
                  </div>

                  <fieldset class="mb-3" id="tak_auth_block">
                    <legend class="form-label">Input authentication</legend>
                    <div class="mb-2">
                      <label class="form-label" for="s_tak_auth_user">Username</label>
                      <input type="text" class="form-control form-control-sm" id="s_tak_auth_user"
                        value="${_esc(t.tak_auth_username || '')}" autocomplete="off" style="max-width:220px;">
                    </div>
                    <div class="mb-1">
                      ${_takSecretInput('s_tak_auth_pass', 'tak_auth_password', 'Password', t, 'style="max-width:220px;"')}
                    </div>
                    <small class="text-muted">Optional. Sent once after connecting, before any CoT.</small>
                  </fieldset>

                  <div class="d-flex align-items-center flex-wrap gap-2">
                    <button class="btn btn-sm btn-outline-secondary" id="btn_tak_test" type="button">
                      <i class="bi bi-plug me-1"></i>Test Connection
                    </button>
                    <span class="small text-muted" id="tak_test_status" role="status" aria-live="polite"></span>
                  </div>
                  <div class="small mt-1" id="tak_test_details"></div>

                </div>
              </div>
            </div>

            <!-- ===== Card 2: Live Status ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-activity me-2"></i>Live Status</div>
                <div class="card-body">
                  <div id="tak_status_warnings"></div>
                  <div id="tak_status_body" aria-live="off"><span class="text-muted small">Loading status…</span></div>
                </div>
              </div>
            </div>

            <!-- ===== Card 3: TLS & Certificates ===== -->
            <div class="col-lg-6" id="tak_tls_card_col">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-shield-lock me-2"></i>TLS &amp; Certificates</div>
                <div class="card-body">

                  <fieldset class="mb-3">
                    <legend class="form-label">Client certificate</legend>
                    <div class="d-flex flex-column gap-1 mb-2" id="tak_client_mode_group">
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_client_mode" id="s_tak_client_mode_upload"
                          value="upload" ${clientMode === 'upload' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_client_mode_upload">Upload certificate</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_client_mode" id="s_tak_client_mode_path"
                          value="path" ${clientMode === 'path' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_client_mode_path">File on this host</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_client_mode" id="s_tak_client_mode_enroll"
                          value="enroll" ${clientMode === 'enroll' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_client_mode_enroll">Enroll with TAK Server</label>
                      </div>
                    </div>

                    <div id="tak_client_upload_block">
                      <div class="tak-cert-summary mb-2 small" id="tak_client_summary">${_takCertSummaryHtml(certs.client, 'No client certificate uploaded')}</div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_client_file">Certificate file</label>
                        <input type="file" class="form-control form-control-sm" id="s_tak_client_file" accept=".p12,.pfx,.pem,.crt,.cer,.key">
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_client_key_file">Private key file (optional)</label>
                        <input type="file" class="form-control form-control-sm" id="s_tak_client_key_file" accept=".pem,.key">
                        <small class="text-muted">Not needed for .p12 or a combined PEM</small>
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_client_upload_pass">Password</label>
                        <input type="password" class="form-control form-control-sm" id="s_tak_client_upload_pass"
                          autocomplete="new-password" placeholder="(if the file is protected)" style="max-width:220px;">
                        <small class="text-muted">Used for this upload only; never saved</small>
                      </div>
                      <div class="d-flex gap-2 flex-wrap">
                        <button class="btn btn-sm btn-outline-secondary" id="btn_tak_client_upload" type="button">
                          <i class="bi bi-upload me-1"></i>Upload
                        </button>
                        <button class="btn btn-sm btn-outline-danger" id="btn_tak_client_remove" type="button" ${certs.client ? '' : 'disabled'}>
                          <i class="bi bi-trash me-1"></i>Remove
                        </button>
                      </div>
                    </div>

                    <div id="tak_client_path_block">
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_client_cert_path">Certificate path</label>
                        <input type="text" class="form-control form-control-sm" id="s_tak_client_cert_path"
                          value="${_esc(t.tak_tls_client_cert_path || '')}" placeholder="/path/to/client.p12">
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_client_key_path">Key path (optional)</label>
                        <input type="text" class="form-control form-control-sm" id="s_tak_client_key_path"
                          value="${_esc(t.tak_tls_client_key_path || '')}" placeholder="/path/to/client.key">
                        <small class="text-muted">Not needed for .p12 or a combined PEM</small>
                      </div>
                      <div class="mb-1">
                        ${_takSecretInput('s_tak_client_pass', 'tak_tls_client_password', 'Password', t, 'style="max-width:220px;"')}
                      </div>
                    </div>

                    <div id="tak_client_enroll_block">
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_enroll_user">Enrollment username</label>
                        <input type="text" class="form-control form-control-sm" id="s_tak_enroll_user"
                          value="${_esc(t.tak_enroll_username || '')}" autocomplete="off" style="max-width:220px;">
                      </div>
                      <div class="mb-2">
                        ${_takSecretInput('s_tak_enroll_pass', 'tak_enroll_password', 'Enrollment password', t, 'style="max-width:220px;"')}
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_enroll_port">Enrollment port</label>
                        <input type="number" class="form-control form-control-sm" id="s_tak_enroll_port"
                          value="${_esc(t.tak_enroll_port)}" min="1" max="65535" style="max-width:110px;">
                      </div>
                      <div class="tak-cert-summary mb-2 small" id="tak_enrolled_summary">${_takCertSummaryHtml(certs.enrolled, 'No enrolled certificate yet')}</div>
                      <div class="d-flex align-items-center flex-wrap gap-2">
                        <button class="btn btn-sm btn-outline-secondary" id="btn_tak_enroll_now" type="button">
                          <i class="bi bi-person-check me-1"></i>Enroll now
                        </button>
                        <span class="small text-muted" id="tak_enroll_status" role="status" aria-live="polite"></span>
                      </div>
                      <small class="text-muted">The certificate is requested from
                        https://&lt;host&gt;:&lt;port&gt; and renewed automatically 14 days before it expires.
                        After a failed attempt the next one waits 5 min, doubling to 1 h, to avoid locking the account.</small>
                    </div>
                  </fieldset>

                  <fieldset class="mb-3">
                    <legend class="form-label">CA truststore</legend>
                    <div class="d-flex flex-column gap-1 mb-2" id="tak_ca_mode_group">
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_ca_mode" id="s_tak_ca_mode_none"
                          value="none" ${caMode === 'none' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_ca_mode_none">System trust store</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_ca_mode" id="s_tak_ca_mode_upload"
                          value="upload" ${caMode === 'upload' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_ca_mode_upload">Upload truststore</label>
                      </div>
                      <div class="form-check">
                        <input class="form-check-input" type="radio" name="tak_ca_mode" id="s_tak_ca_mode_path"
                          value="path" ${caMode === 'path' ? 'checked' : ''}>
                        <label class="form-check-label" for="s_tak_ca_mode_path">File on this host</label>
                      </div>
                    </div>

                    <div id="tak_ca_upload_block">
                      <div class="tak-cert-summary mb-2 small" id="tak_ca_summary">${_takCaSummaryHtml(certs.ca)}</div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_ca_file">Truststore file</label>
                        <input type="file" class="form-control form-control-sm" id="s_tak_ca_file" accept=".p12,.pfx,.pem,.crt,.cer">
                      </div>
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_ca_upload_pass">Truststore password (often atakatak)</label>
                        <input type="password" class="form-control form-control-sm" id="s_tak_ca_upload_pass"
                          autocomplete="new-password" placeholder="(if the file is protected)" style="max-width:220px;">
                        <small class="text-muted">Used for this upload only; never saved</small>
                      </div>
                      <div class="d-flex gap-2 flex-wrap">
                        <button class="btn btn-sm btn-outline-secondary" id="btn_tak_ca_upload" type="button">
                          <i class="bi bi-upload me-1"></i>Upload
                        </button>
                        <button class="btn btn-sm btn-outline-danger" id="btn_tak_ca_remove" type="button" ${certs.ca ? '' : 'disabled'}>
                          <i class="bi bi-trash me-1"></i>Remove
                        </button>
                      </div>
                    </div>

                    <div id="tak_ca_path_block">
                      <div class="mb-2">
                        <label class="form-label" for="s_tak_ca_path">Truststore path</label>
                        <input type="text" class="form-control form-control-sm" id="s_tak_ca_path"
                          value="${_esc(t.tak_tls_ca_path || '')}" placeholder="/path/to/truststore.p12">
                      </div>
                      <div class="mb-1">
                        ${_takSecretInput('s_tak_ca_pass', 'tak_tls_ca_password', 'Password', t, 'style="max-width:220px;"')}
                        <small class="text-muted">Separate from the client certificate password</small>
                      </div>
                    </div>
                  </fieldset>

                  ${_takSwitch('s_tak_tls_verify', 'Verify server certificate', t.tak_tls_verify !== false)}
                  ${_takSwitch('s_tak_tls_check_hostname', 'Check server hostname', t.tak_tls_check_hostname !== false)}

                </div>
              </div>
            </div>

            <!-- ===== Card 4: Data Package ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-file-earmark-zip me-2"></i>Data Package</div>
                <div class="card-body">
                  <div class="mb-2">
                    <label class="form-label" for="s_tak_pkg_file">TAK data package (.zip)</label>
                    <input type="file" class="form-control form-control-sm" id="s_tak_pkg_file" accept=".zip">
                  </div>
                  <button class="btn btn-sm btn-outline-secondary" id="btn_tak_pkg_import" type="button">
                    <i class="bi bi-box-arrow-in-down me-1"></i>Import
                  </button>
                  <small class="text-muted">Imports an ATAK enrollment package, a certificate package, or an iTAK
                    package. Settings are applied immediately; usernames and passwords are never in packages.</small>
                  <div class="tak-pkg-result small mt-2" id="tak_pkg_result" role="status" aria-live="polite"></div>
                </div>
              </div>
            </div>

            <!-- ===== Card 5: Map Content ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-pin-map me-2"></i>Map Content</div>
                <div class="card-body">

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_unknown_aff">Unknown drones shown as</label>
                    <select class="form-select form-select-sm" id="s_tak_unknown_aff" style="max-width:220px;">
                      <option value="u" ${_sel(t.tak_unknown_affiliation, 'u')}>Unknown (yellow)</option>
                      <option value="n" ${_sel(t.tak_unknown_affiliation, 'n')}>Neutral (green)</option>
                    </select>
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_type_family">Drone symbol</label>
                    <select class="form-select form-select-sm" id="s_tak_type_family">
                      <option value="uav"   ${_sel(t.tak_drone_type_family, 'uav')}>UAV symbol, coloured by disposition (recommended)</option>
                      <option value="civil" ${_sel(t.tak_drone_type_family, 'civil')}>Civil aircraft symbol (always purple)</option>
                    </select>
                    <small class="text-muted">TAK only colours military-family symbols by affiliation; civil aircraft symbols are always purple, so a threat would not show red.</small>
                  </div>

                  ${_takSwitch('s_tak_operator', 'Operator / takeoff point', t.tak_operator_enabled)}
                  ${_takSwitch('s_tak_line', 'Line from drone to operator', t.tak_line_enabled)}
                  ${_takSwitch('s_tak_ssid', 'Include Wi-Fi SSID-only detections (plotted at this receiver)', t.tak_include_ssid_detections)}
                  ${_takSwitch('s_tak_sensor', 'Publish this receiver as a sensor point (needs a GPS fix)', t.tak_sensor_enabled)}

                  <div class="row g-2 mb-3">
                    <div class="col-6">
                      <label class="form-label" for="s_tak_sensor_hb">Sensor heartbeat (s)</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_sensor_hb"
                        value="${_esc(t.tak_sensor_heartbeat_s)}" min="1" max="300" step="1">
                    </div>
                    <div class="col-6">
                      <label class="form-label" for="s_tak_sensor_stale">Sensor stale (s)</label>
                      <input type="text" class="form-control form-control-sm" id="s_tak_sensor_stale" inputmode="decimal"
                        value="${_esc(t.tak_sensor_stale_s || '')}" placeholder="${_esc(staleAutoPh)}">
                      <small class="text-muted">Blank = auto</small>
                    </div>
                  </div>

                  <div class="mb-3">
                    <label class="form-label" for="s_tak_station">Station name</label>
                    <input type="text" class="form-control form-control-sm" id="s_tak_station"
                      value="${_esc(t.tak_station_name || '')}" placeholder="${_esc(eff.station_name || '')}" style="max-width:260px;">
                    <small class="text-muted">Sensor callsign; also part of every drone's UID on TAK</small>
                  </div>
                  <div class="mb-3">
                    <label class="form-label" for="s_tak_uid_prefix">UID prefix</label>
                    <input type="text" class="form-control form-control-sm" id="s_tak_uid_prefix"
                      value="${_esc(t.tak_uid_prefix || '')}" style="max-width:260px;">
                  </div>
                  <div class="mb-0">
                    <label class="form-label" for="s_tak_client_uid">Client UID</label>
                    <input type="text" class="form-control form-control-sm" id="s_tak_client_uid"
                      value="${_esc(t.tak_client_uid || '')}" placeholder="${_esc(eff.client_uid || '')}" style="max-width:300px;">
                    <small class="text-muted">Identifies this receiver to TAK Server; must be unique per receiver</small>
                  </div>

                </div>
              </div>
            </div>

            <!-- ===== Card 6: Timing ===== -->
            <div class="col-lg-6">
              <div class="card settings-card mb-3">
                <div class="card-header"><i class="bi bi-stopwatch me-2"></i>Timing</div>
                <div class="card-body">
                  <div class="row g-2 mb-2">
                    <div class="col-6">
                      <label class="form-label" for="s_tak_send_interval">Send interval (s)</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_send_interval"
                        value="${_esc(t.tak_send_interval_s)}" min="0.5" max="60" step="0.5">
                      <small class="text-muted">Minimum spacing between updates of one drone</small>
                    </div>
                    <div class="col-6">
                      <label class="form-label" for="s_tak_refresh_interval">Refresh interval (s)</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_refresh_interval"
                        value="${_esc(t.tak_refresh_interval_s)}" min="1" max="300" step="1">
                      <small class="text-muted">Re-send of an unchanged drone</small>
                    </div>
                    <div class="col-6">
                      <label class="form-label" for="s_tak_stale_after">Stale after (s)</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_stale_after"
                        value="${_esc(staleAfter)}" min="1" max="300" step="1">
                    </div>
                    <div class="col-6">
                      <label class="form-label" for="s_tak_delete_after">Delete after (s)</label>
                      <input type="number" class="form-control form-control-sm" id="s_tak_delete_after"
                        value="${_esc(deleteAfter)}" min="1" max="300" step="1">
                    </div>
                  </div>
                  ${_takSwitch('s_tak_send_deletes', 'Send delete messages', t.tak_send_deletes !== false)}
                  <div class="small text-muted" id="tak_timing_hint"></div>
                </div>
              </div>
            </div>

          </div><!-- /row -->
        </div><!-- /p-3 -->
      </div><!-- /takTabPane -->
    `;
  }

  // ---- TAK: reading, validating and collecting fields ----

  // Raw values straight from the DOM (numbers may be NaN). Secrets: cleared ->
  // '', typed -> the typed value, otherwise omitted (D11).
  function _readTakValues() {
    const out = {};
    for (const [key, id, kind] of _TAK_FIELDS) {
      const el = _el(id);
      if (!el) continue;
      if (kind === 'bool') {
        if (key === 'tak_tls_check_hostname' && el.disabled && el.dataset.prev !== undefined) {
          out[key] = el.dataset.prev === '1';   // verify is off: keep the stored choice
        } else {
          out[key] = el.checked;
        }
      } else if (kind === 'int') {
        out[key] = el.value.trim() === '' ? NaN : parseInt(el.value, 10);
      } else if (kind === 'float') {
        out[key] = el.value.trim() === '' ? NaN : parseFloat(el.value);
      } else if (kind === 'radio') {
        const r = el.querySelector('input[type="radio"]:checked');
        if (r) out[key] = r.value;
      } else if (kind === 'secret') {
        if (el.dataset.clear === '1') out[key] = '';
        else if (el.value) out[key] = el.value;
      } else {
        out[key] = el.value.trim();
      }
    }
    return out;
  }

  function _collectTakFields() {
    return _readTakValues();
  }

  const _IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
  const _HOST_RE = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;

  function _ipv4(s) {
    const m = _IPV4_RE.exec(s);
    if (!m) return null;
    const o = m.slice(1).map(Number);
    return o.every(n => n >= 0 && n <= 255) ? o : null;
  }

  // Mirrors the backend rules for the fields that are cheap to check; the
  // backend stays authoritative.
  function _validateTakClient(v) {
    const errs = [];
    const bad = (field, message) => errs.push({ field, message });
    const inRange = (key, lo, hi, label, isInt) => {
      const x = v[key];
      if (x === undefined) return true;
      if (typeof x !== 'number' || isNaN(x)) { bad(key, `${label} must be a number`); return false; }
      if (x < lo || x > hi) { bad(key, `${label} must be between ${lo} and ${hi}`); return false; }
      return true;
    };

    inRange('cot_port', 1, 65535, 'Port');
    inRange('tak_multicast_ttl', 1, 255, 'Multicast TTL');
    inRange('tak_enroll_port', 1, 65535, 'Enrollment port');
    inRange('tak_sensor_heartbeat_s', 1, 300, 'Sensor heartbeat');

    const sendOk = inRange('tak_send_interval_s', 0.5, 60, 'Send interval');
    const refOk = inRange('tak_refresh_interval_s', 1, 300, 'Refresh interval');
    const staleOk = inRange('tak_stale_after_s', 1, 300, 'Stale after');
    const delOk = inRange('tak_delete_after_s', 1, 300, 'Delete after');
    if (sendOk && refOk && v.tak_send_interval_s !== undefined && v.tak_refresh_interval_s !== undefined &&
        v.tak_refresh_interval_s < v.tak_send_interval_s) {
      bad('tak_refresh_interval_s', 'Refresh interval must be at least the send interval');
    }
    if (sendOk && refOk && staleOk && v.tak_stale_after_s !== undefined) {
      const need = 3 * Math.max(v.tak_send_interval_s, v.tak_refresh_interval_s);
      if (v.tak_stale_after_s < need) {
        bad('tak_stale_after_s', `Stale after must be at least 3 x max(send interval, refresh interval) = ${need} s`);
      }
    }
    if (staleOk && delOk && v.tak_delete_after_s < v.tak_stale_after_s) {
      bad('tak_delete_after_s', 'Delete after must be at least the stale time');
    }

    if (v.tak_sensor_stale_s) {
      const x = Number(v.tak_sensor_stale_s);
      const hb = v.tak_sensor_heartbeat_s;
      const lo = (typeof hb === 'number' && !isNaN(hb)) ? 3 * Math.ceil(hb) : 3;
      if (isNaN(x)) bad('tak_sensor_stale_s', 'Sensor stale must be blank (auto) or a number of seconds');
      else if (x < lo || x > 86400) bad('tak_sensor_stale_s', `Sensor stale must be between ${lo} and 86400 s`);
    }

    if (v.tak_uid_prefix !== undefined && !/^[A-Za-z0-9._-]{1,48}$/.test(v.tak_uid_prefix)) {
      bad('tak_uid_prefix', 'UID prefix must be 1-48 letters, digits, dot, underscore or dash');
    }
    if (v.tak_client_uid && !/^[A-Za-z0-9._:-]{1,128}$/.test(v.tak_client_uid)) {
      bad('tak_client_uid', 'Client UID may only contain letters, digits and . _ : -');
    }
    if (v.tak_station_name && v.tak_station_name.length > 64) {
      bad('tak_station_name', 'Station name must be at most 64 characters');
    }

    const addr = v.cot_address;
    if (addr !== undefined) {
      const tr = v.tak_transport;
      const o = _ipv4(addr);
      if (tr === 'udp_multicast') {
        if (!o || o[0] < 224 || o[0] > 239) bad('cot_address', 'Multicast group must be an IPv4 address in 224.0.0.0/4');
      } else if (tr === 'udp_broadcast') {
        if (!o) bad('cot_address', 'Broadcast address must be an IPv4 address');
      } else if (!o && !_HOST_RE.test(addr) && !(addr.includes(':') && /^[0-9A-Fa-f:.]+$/.test(addr))) {
        bad('cot_address', 'Enter a valid host name or IP address');
      }
    }
    return errs;
  }

  function _clearTakErrors() {
    const pane = _el('takTabPane');
    if (!pane) return;
    pane.querySelectorAll('.is-invalid').forEach(n => n.classList.remove('is-invalid'));
    pane.querySelectorAll('.tak-field-error').forEach(n => n.remove());
  }

  // Marks fields with is-invalid + feedback. Returns the first message shown.
  function _showTakErrors(errors) {
    let first = null;
    for (const e of errors || []) {
      const id = _TAK_FIELD_IDS[e.field];
      const el = id && _el(id);
      if (!el) continue;
      if (first === null) first = e.message;
      el.classList.add('is-invalid');
      const fb = document.createElement('div');
      fb.className = 'invalid-feedback d-block tak-field-error';
      fb.textContent = e.message;       // textContent: server strings stay inert
      el.insertAdjacentElement('afterend', fb);
    }
    return first;
  }

  function _focusTakTab() {
    const tab = _el('takTab-tab');
    if (tab) bootstrap.Tab.getOrCreateInstance(tab).show();
  }

  // ---- TAK: UI state ----

  function _applyTakTransportUi(transport) {
    const ui = _TAK_HOST_UI[transport] || _TAK_HOST_UI.udp_multicast;
    const label = _el('tak_host_label');
    if (label) label.textContent = ui.label;
    const host = _el('s_tak_host');
    if (host) host.placeholder = ui.placeholder;
    const show = (id, on) => { const n = _el(id); if (n) n.style.display = on ? '' : 'none'; };
    show('tak_mcast_block', transport === 'udp_multicast');
    show('tak_auth_block', transport === 'tcp' || transport === 'tls');
    show('tak_tls_card_col', transport === 'tls');
  }

  function _applyTakModeUi() {
    const cm = _el('tak_client_mode_group')?.querySelector('input:checked')?.value || 'upload';
    const am = _el('tak_ca_mode_group')?.querySelector('input:checked')?.value || 'none';
    const show = (id, on) => { const n = _el(id); if (n) n.style.display = on ? '' : 'none'; };
    show('tak_client_upload_block', cm === 'upload');
    show('tak_client_path_block', cm === 'path');
    show('tak_client_enroll_block', cm === 'enroll');
    show('tak_ca_upload_block', am === 'upload');
    show('tak_ca_path_block', am === 'path');
    _applyTakVerifyUi();
    _updateTakEnrollButton();
  }

  // Hostname check is meaningless while verification is off: show it
  // unchecked + disabled but remember the choice so it is not lost.
  function _applyTakVerifyUi() {
    const verify = _el('s_tak_tls_verify');
    const host = _el('s_tak_tls_check_hostname');
    if (!verify || !host) return;
    if (!verify.checked) {
      if (!host.disabled) { host.dataset.prev = host.checked ? '1' : '0'; }
      host.checked = false;
      host.disabled = true;
    } else if (host.disabled) {
      host.disabled = false;
      host.checked = host.dataset.prev !== '0';
      delete host.dataset.prev;
    }
  }

  function _applyTakLineUi() {
    const op = _el('s_tak_operator'), line = _el('s_tak_line');
    if (op && line) line.disabled = !op.checked;
  }

  function _updateTakTimingHint() {
    const hint = _el('tak_timing_hint');
    if (!hint) return;
    const st = parseFloat(_el('s_tak_stale_after')?.value);
    const del = parseFloat(_el('s_tak_delete_after')?.value);
    hint.textContent = (isNaN(st) || isNaN(del)) ? '' :
      `Drones go stale on TAK ${st} s after their last Remote ID frame and are deleted after ${del} s ` +
      `(the app shows them as aging after 30 s and drops them from the list after 180 s).`;
  }

  // Enroll now works on the SAVED config (the server reads it), so it is
  // enabled only when the saved settings are tls + enroll with credentials.
  function _updateTakEnrollButton() {
    const btn = _el('btn_tak_enroll_now');
    if (!btn) return;
    const t = (_takConfig && _takConfig.settings) || {};
    const ok = t.tak_transport === 'tls' && t.tak_tls_client_mode === 'enroll' &&
      !!t.tak_enroll_username && t.tak_enroll_password === '(set)';
    btn.disabled = !ok;
    btn.title = ok ? '' : 'Save TLS transport, enrollment mode, username and password first';
  }

  function _fileToB64(file, maxBytes, label) {
    return new Promise((resolve) => {
      if (!file) { resolve(null); return; }
      if (file.size > maxBytes) {
        Utils.toast(`${_esc(label)} too large (max ${Math.round(maxBytes / 1024)} KiB)`, 'warning');
        resolve(null);
        return;
      }
      const fr = new FileReader();
      fr.onload = () => {
        const s = String(fr.result || '');
        const i = s.indexOf('base64,');
        resolve(i >= 0 ? s.slice(i + 7) : '');
      };
      fr.onerror = () => {
        Utils.toast(`Could not read ${_esc(file.name)}`, 'danger');
        resolve(null);
      };
      fr.readAsDataURL(file);
    });
  }

  function _setBusy(btn, busyHtml) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = busyHtml;
    return () => { btn.innerHTML = orig; btn.disabled = false; };
  }

  function _setInline(el, text, cls) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'small ' + (cls || 'text-muted');
  }

  // ---- TAK: live status ----

  function _renderTakStatus(st) {
    const badge = (cls, text) => `<span class="badge ${cls}">${_esc(text)}</span>`;
    const stateMap = {
      disabled:   badge('bg-secondary', 'Disabled'),
      connecting: badge('bg-warning text-dark', 'Connecting'),
      connected:  badge('bg-success', 'Connected'),
      ready:      badge('bg-success', 'Ready'),
      error:      badge('bg-danger', 'Error'),
    };
    const rows = [];
    const row = (k, vHtml) => rows.push(`<dt>${_esc(k)}</dt><dd>${vHtml}</dd>`);
    const dash = '<span class="text-muted">—</span>';
    const val = (v) => (v === null || v === undefined || v === '') ? dash : _esc(v);

    row('State', stateMap[st.state] || badge('bg-secondary', st.state || 'unknown'));
    row('Destination', val(st.destination));
    row('Connected since', st.connected_since ? _esc(_fmtTime(st.connected_since)) : dash);
    row('Sent', val(st.sent));
    row('Send errors', val(st.send_errors));
    row('Reconnects', val(st.reconnects));
    row('Queue', `${_esc(st.queue_depth ?? 0)} queued / ${_esc(st.queue_drops ?? 0)} dropped`);
    const it = st.items || {};
    row('Items', `${_esc(it.drones ?? 0)} drones / ${_esc(it.operators ?? 0)} operators / ${_esc(it.lines ?? 0)} lines / ${_esc(it.sensor ?? 0)} sensor`);
    row('Deletes', `${_esc(st.deletes_sent ?? 0)} sent / ${_esc(st.deletes_on_stop ?? 0)} on stop`);
    const sp = st.sensor_point;
    row('Sensor point', sp ? val(sp.reason) : dash);
    row('Last error', st.last_error
      ? `<span class="tak-err">${_esc(st.last_error)}</span>${st.last_error_at ? ` <span class="text-muted">(${_esc(_fmtTime(st.last_error_at))})</span>` : ''}`
      : dash);

    if (st.tls) {
      let cc = dash;
      if (st.tls.client_subject) {
        const dl = st.tls.client_days_left;
        let tag = '';
        if (typeof dl === 'number') {
          tag = dl < 0 ? ' <span class="tak-days-bad">(expired)</span>'
            : ` <span class="${dl < 14 ? 'tak-days-bad' : (dl < 30 ? 'tak-days-warn' : '')}">(${_esc(dl)} days left)</span>`;
        }
        cc = `${_esc(st.tls.client_subject)}` +
          (st.tls.client_not_after ? `<br><span class="text-muted">expires ${_esc(_fmtTime(st.tls.client_not_after))}</span>${tag}` : '');
      }
      row('Client cert', cc);
      if (st.tls.version) row('TLS', `${_esc(st.tls.version)}${st.tls.cipher ? ' / ' + _esc(st.tls.cipher) : ''}`);
    }
    if (st.enrollment) {
      const e = st.enrollment;
      row('Enrollment', `${_esc(e.status || '')}` +
        (e.last_success_at ? `<br><span class="text-muted">last success ${_esc(_fmtTime(e.last_success_at))}</span>` : '') +
        (e.next_attempt_at ? `<br><span class="text-muted">next attempt ${_esc(_fmtTime(e.next_attempt_at))}</span>` : '') +
        (e.last_error ? `<br><span class="tak-err">${_esc(e.last_error)}</span>` : ''));
    }
    return `<dl class="tak-status-grid mb-0">${rows.join('')}</dl>`;
  }

  function _renderTakWarnings(st) {
    const w = (st && st.config_warnings) || [];
    if (!w.length) return '';
    return `<div class="alert alert-warning py-2 px-3 mb-2 small"><ul class="mb-0 ps-3">${w.map(x => `<li>${_esc(x)}</li>`).join('')}</ul></div>`;
  }

  async function _pollTakStatus() {
    if (_takPollBusy || document.hidden) return;
    const body = _el('tak_status_body');
    if (!body) { _stopTakPolling(); return; }
    _takPollBusy = true;
    const gen = _takPollGen;
    try {
      const st = await Api.getTakStatus();
      if (gen !== _takPollGen || !_el('tak_status_body')) return;   // stopped meanwhile
      _el('tak_status_body').innerHTML = _renderTakStatus(st);
      const w = _el('tak_status_warnings');
      if (w) w.innerHTML = _renderTakWarnings(st);
    } catch (_) {
      if (gen === _takPollGen) {
        const b = _el('tak_status_body');
        if (b) b.innerHTML = '<span class="text-muted small">Status unavailable</span>';
      }
    } finally {
      _takPollBusy = false;
    }
  }

  function _startTakPolling() {
    _stopTakPolling();            // never more than one interval
    _takPollTimer = setInterval(_pollTakStatus, _TAK_POLL_MS);
    _pollTakStatus();
  }

  function _stopTakPolling() {
    if (_takPollTimer !== null) { clearInterval(_takPollTimer); _takPollTimer = null; }
    _takPollGen++;                // discard any in-flight response
    _takPollBusy = false;
  }

  // ---- TAK: listeners ----

  function _takTestDetailsHtml(r) {
    const d = r.details || {};
    const parts = [];
    if (d.transport) parts.push(`${_esc(d.transport)}${d.destination ? ' ' + _esc(d.destination) : ''}`);
    if (d.tls_version) parts.push(`${_esc(d.tls_version)}${d.cipher ? ' / ' + _esc(d.cipher) : ''}`);
    if (d.server_subject) parts.push(`server ${_esc(d.server_subject)}`);
    if (d.client_subject) parts.push(`client cert ${_esc(d.client_subject)}${d.client_not_after ? ' (expires ' + _esc(_fmtTime(d.client_not_after)) + ')' : ''}`);
    if (d.enrollment) parts.push(`enrollment: ${_esc(d.enrollment)}`);
    if (r.duration_ms != null) parts.push(`${_esc(r.duration_ms)} ms`);
    return parts.length ? `<span class="text-muted">${parts.join(' &middot; ')}</span>` : '';
  }

  function _takPkgResultHtml(r) {
    const imp = r.imported || {};
    const items = [];
    if (imp.ca) items.push(`CA truststore: ${_esc(imp.ca.count)} certificate${imp.ca.count === 1 ? '' : 's'}`);
    if (imp.client) items.push('Client certificate imported');
    if (r.enroll_required) items.push('Enrollment required');
    const warns = (r.warnings || []).map(w => `<li class="tak-days-warn">${_esc(w)}</li>`).join('');
    return `
      <div><strong>${_esc(r.kind || '')}</strong> package: ${_esc(r.host || '')}:${_esc(r.port || '')} (${_esc(r.transport || '')})</div>
      ${r.description ? `<div class="text-muted">${_esc(r.description)}</div>` : ''}
      ${items.length ? `<ul class="mb-1 ps-3">${items.map(i => `<li>${i}</li>`).join('')}</ul>` : ''}
      ${warns ? `<ul class="mb-1 ps-3">${warns}</ul>` : ''}
      ${r.next_step ? `<div><strong>${_esc(r.next_step)}</strong></div>` : ''}`;
  }

  async function _refreshTakCerts() {
    const c = await Api.getTakCerts().catch(() => null);
    if (!c) return;
    _takCerts = c;
    const set = (id, html) => { const n = _el(id); if (n) n.innerHTML = html; };
    set('tak_client_summary', _takCertSummaryHtml(c.client, 'No client certificate uploaded'));
    set('tak_ca_summary', _takCaSummaryHtml(c.ca));
    set('tak_enrolled_summary', _takCertSummaryHtml(c.enrolled, 'No enrolled certificate yet'));
    const rc = _el('btn_tak_client_remove'); if (rc) rc.disabled = !c.client;
    const rca = _el('btn_tak_ca_remove'); if (rca) rca.disabled = !c.ca;
  }

  function _setRadio(groupId, value) {
    const r = _el(groupId)?.querySelector(`input[value="${value}"]`);
    if (r) r.checked = true;
  }

  // Re-render the TAK pane from fresh settings (after a package import).
  function _rerenderTakPane() {
    const old = _el('takTabPane');
    if (!old) return;
    const wasActive = old.classList.contains('active');
    const wrap = document.createElement('div');
    wrap.innerHTML = _buildTakTabHtml(_settings, _takConfig, _takCerts, _takIfaces).trim();
    const fresh = wrap.firstElementChild;
    if (wasActive) fresh.classList.add('show', 'active');
    old.replaceWith(fresh);
    _attachTakListeners();
    _pollTakStatus();
  }

  function _attachTakListeners() {
    if (!_el('s_tak_enabled')) return;     // error pane: nothing to wire

    // Setup banner
    _el('btn_tak_dismiss_banner')?.addEventListener('click', () => {
      try { localStorage.setItem('sparrow_tak_setup_dismissed', '1'); } catch (_) { /* ignore */ }
      const b = _el('tak_setup_banner'); if (b) b.style.display = 'none';
    });

    // Transport change: swap the port when it was still the old default
    const trSel = _el('s_tak_transport');
    trSel?.addEventListener('change', () => {
      const prev = trSel.dataset.prev;
      const next = trSel.value;
      const ports = _takDefaultPorts();
      const portEl = _el('s_tak_port');
      if (portEl && ports[next] !== undefined && parseInt(portEl.value, 10) === ports[prev]) {
        portEl.value = ports[next];
      }
      trSel.dataset.prev = next;
      _applyTakTransportUi(next);
    });

    _el('tak_client_mode_group')?.addEventListener('change', _applyTakModeUi);
    _el('tak_ca_mode_group')?.addEventListener('change', _applyTakModeUi);
    _el('s_tak_tls_verify')?.addEventListener('change', _applyTakVerifyUi);
    _el('s_tak_operator')?.addEventListener('change', _applyTakLineUi);

    // Secret "Clear" links
    document.querySelectorAll('#takTabPane [data-clear-for]').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = _el(btn.dataset.clearFor);
        if (!input) return;
        input.value = '';
        input.dataset.clear = '1';
        const note = _el(btn.dataset.clearFor + '_clear_note');
        if (note) note.style.display = '';
      });
    });
    // Typing a new secret after Clear replaces the clear request
    ['s_tak_auth_pass', 's_tak_client_pass', 's_tak_ca_pass', 's_tak_enroll_pass'].forEach(id => {
      _el(id)?.addEventListener('input', e => {
        if (e.target.value) {
          delete e.target.dataset.clear;
          const note = _el(id + '_clear_note'); if (note) note.style.display = 'none';
        }
      });
    });

    // Timing: live hint + live client-side checks on the timing fields only
    ['s_tak_send_interval', 's_tak_refresh_interval', 's_tak_stale_after', 's_tak_delete_after'].forEach(id => {
      _el(id)?.addEventListener('input', () => {
        _updateTakTimingHint();
        _clearTakErrors();
        _showTakErrors(_validateTakClient(_readTakValues()).filter(e => _TAK_TIMING_KEYS.includes(e.field)));
      });
    });

    // Test Connection (unsaved values)
    _el('btn_tak_test')?.addEventListener('click', async () => {
      const btn = _el('btn_tak_test');
      const statusEl = _el('tak_test_status');
      const detailsEl = _el('tak_test_details');
      _clearTakErrors();
      if (detailsEl) detailsEl.innerHTML = '';
      const body = _collectTakFields();
      const errs = _validateTakClient(body);
      if (errs.length) {
        const first = _showTakErrors(errs);
        _setInline(statusEl, first, 'text-danger');
        return;
      }
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Testing…');
      _setInline(statusEl, 'Testing…', 'text-muted');
      try {
        const r = await Api.testTak(body);
        if (r.ok) {
          _setInline(statusEl, 'OK: ' + (r.message || 'Connection successful'), 'text-success');
        } else {
          _setInline(statusEl, r.error || 'Connection failed', 'text-danger');
        }
        if (detailsEl) detailsEl.innerHTML = _takTestDetailsHtml(r);
        if (r.details && r.details.enrollment === 'enrolled') _refreshTakCerts();
      } catch (e) {
        if (e.detail && e.detail.errors) _showTakErrors(e.detail.errors);
        _setInline(statusEl, e.message || 'Test failed', 'text-danger');
      }
      done();
    });

    // Client certificate upload / remove
    _el('btn_tak_client_upload')?.addEventListener('click', async () => {
      const btn = _el('btn_tak_client_upload');
      const certFile = _el('s_tak_client_file')?.files?.[0];
      const keyFile = _el('s_tak_client_key_file')?.files?.[0];
      if (!certFile) { Utils.toast('Choose a certificate file first', 'warning'); return; }
      const cert_b64 = await _fileToB64(certFile, _TAK_CERT_MAX, 'Certificate file');
      if (cert_b64 === null) return;
      const body = { cert_b64, filename: certFile.name };
      if (keyFile) {
        const key_b64 = await _fileToB64(keyFile, _TAK_CERT_MAX, 'Key file');
        if (key_b64 === null) return;
        body.key_b64 = key_b64;
      }
      const pw = _el('s_tak_client_upload_pass')?.value || '';
      if (pw) body.password = pw;
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Uploading…');
      try {
        await Api.uploadTakClientCert(body);
        Utils.toast('Client certificate uploaded', 'success');
        ['s_tak_client_file', 's_tak_client_key_file', 's_tak_client_upload_pass'].forEach(id => { const n = _el(id); if (n) n.value = ''; });
        _setRadio('tak_client_mode_group', 'upload');     // the server switched the mode
        if (_takConfig && _takConfig.settings) _takConfig.settings.tak_tls_client_mode = 'upload';
        _applyTakModeUi();
        await _refreshTakCerts();
        _pollTakStatus();
      } catch (e) {
        Utils.toast('Certificate upload failed: ' + _esc(e.message), 'danger');
      }
      done();
      const rm = _el('btn_tak_client_remove'); if (rm) rm.disabled = !(_takCerts && _takCerts.client);
    });

    _el('btn_tak_client_remove')?.addEventListener('click', async () => {
      if (!confirm('Remove the uploaded client certificate?')) return;
      const btn = _el('btn_tak_client_remove');
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Removing…');
      try {
        await Api.deleteTakClientCert();
        Utils.toast('Client certificate removed', 'success');
        await _refreshTakCerts();
        _pollTakStatus();
      } catch (e) {
        Utils.toast('Remove failed: ' + _esc(e.message), 'danger');
      }
      done();
      btn.disabled = !(_takCerts && _takCerts.client);
    });

    // CA upload / remove
    _el('btn_tak_ca_upload')?.addEventListener('click', async () => {
      const btn = _el('btn_tak_ca_upload');
      const file = _el('s_tak_ca_file')?.files?.[0];
      if (!file) { Utils.toast('Choose a truststore file first', 'warning'); return; }
      const data_b64 = await _fileToB64(file, _TAK_CERT_MAX, 'Truststore file');
      if (data_b64 === null) return;
      const body = { data_b64, filename: file.name };
      const pw = _el('s_tak_ca_upload_pass')?.value || '';
      if (pw) body.password = pw;
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Uploading…');
      try {
        await Api.uploadTakCa(body);
        Utils.toast('CA truststore uploaded', 'success');
        ['s_tak_ca_file', 's_tak_ca_upload_pass'].forEach(id => { const n = _el(id); if (n) n.value = ''; });
        _setRadio('tak_ca_mode_group', 'upload');
        if (_takConfig && _takConfig.settings) _takConfig.settings.tak_tls_ca_mode = 'upload';
        _applyTakModeUi();
        await _refreshTakCerts();
        _pollTakStatus();
      } catch (e) {
        Utils.toast('Truststore upload failed: ' + _esc(e.message), 'danger');
      }
      done();
      const rm = _el('btn_tak_ca_remove'); if (rm) rm.disabled = !(_takCerts && _takCerts.ca);
    });

    _el('btn_tak_ca_remove')?.addEventListener('click', async () => {
      if (!confirm('Remove the uploaded CA truststore?')) return;
      const btn = _el('btn_tak_ca_remove');
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Removing…');
      try {
        await Api.deleteTakCa();
        Utils.toast('CA truststore removed', 'success');
        await _refreshTakCerts();
        _pollTakStatus();
      } catch (e) {
        Utils.toast('Remove failed: ' + _esc(e.message), 'danger');
      }
      done();
      btn.disabled = !(_takCerts && _takCerts.ca);
    });

    // Enroll now (acts on the saved configuration)
    _el('btn_tak_enroll_now')?.addEventListener('click', async () => {
      const btn = _el('btn_tak_enroll_now');
      const statusEl = _el('tak_enroll_status');
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Enrolling…');
      _setInline(statusEl, 'Waiting for TAK Server…', 'text-muted');
      try {
        const r = await Api.takEnrollNow();
        const ok = r.status === 'enrolled' || r.status === 'cached';
        const msg = ok ? `Enrollment ${r.status}`
          : `Enrollment ${r.status || 'failed'}${r.last_error ? ': ' + r.last_error : ''}` +
            (r.next_attempt_at ? ` (next attempt ${_fmtTime(r.next_attempt_at)})` : '');
        _setInline(statusEl, msg, ok ? 'text-success' : 'text-danger');
        Utils.toast(_esc(msg), ok ? 'success' : 'danger');
        await _refreshTakCerts();
        _pollTakStatus();
      } catch (e) {
        _setInline(statusEl, e.message || 'Enrollment failed', 'text-danger');
        Utils.toast('Enrollment failed: ' + _esc(e.message), 'danger');
      }
      done();
      _updateTakEnrollButton();
    });

    // Data package import
    _el('btn_tak_pkg_import')?.addEventListener('click', async () => {
      const btn = _el('btn_tak_pkg_import');
      const resEl = _el('tak_pkg_result');
      const file = _el('s_tak_pkg_file')?.files?.[0];
      if (!file) { Utils.toast('Choose a .zip package first', 'warning'); return; }
      const data_b64 = await _fileToB64(file, _TAK_ZIP_MAX, 'Package');
      if (data_b64 === null) return;
      const done = _setBusy(btn, '<i class="bi bi-hourglass-split me-1"></i>Importing…');
      try {
        const r = await Api.importTakPackage({ data_b64, filename: file.name });
        if (r.settings) {
          _takConfig = Object.assign({}, _takConfig, { settings: r.settings });
          if (_settings) {
            ['cot_enabled', 'cot_address', 'cot_port'].forEach(k => { if (k in r.settings) _settings[k] = r.settings[k]; });
          }
        }
        await _refreshTakCerts();
        Utils.toast('Package imported', 'success');
        done();
        _rerenderTakPane();
        const res2 = _el('tak_pkg_result');
        if (res2) res2.innerHTML = _takPkgResultHtml(r);
        return;
      } catch (e) {
        if (resEl) { resEl.className = 'tak-pkg-result small mt-2 text-danger'; resEl.textContent = e.message || 'Import failed'; }
        Utils.toast('Package import failed: ' + _esc(e.message), 'danger');
      }
      done();
    });

    _applyTakTransportUi(_el('s_tak_transport')?.value || 'udp_multicast');
    _applyTakModeUi();
    _applyTakLineUi();
    _updateTakTimingHint();
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
    boolField ('s_alert_audio',      'alert_audio_enabled');
    boolField ('s_alert_visual',     'alert_visual_enabled');
    boolField ('s_alert_script',     'alert_script_enabled');
    strField  ('s_alert_script_path','alert_script_path');
    boolField ('s_alert_friendly',   'alert_friendly_enabled');
    boolField ('s_alert_slack',      'alert_slack_enabled');
    strField  ('s_slack_webhook',    'alert_slack_webhook_url');
    strField  ('s_slack_name',       'alert_slack_display_name');

    // API-based alerting — bearer token uses the '(set)' sensitive-field
    // pattern: only send the token to the backend when the user actually
    // typed a new value, otherwise the stored one is preserved.
    boolField ('s_alert_api',           'alert_api_enabled');
    strField  ('s_api_base_url',        'alert_api_base_url');
    strField  ('s_api_domain',          'alert_api_domain');
    boolField ('s_alert_api_verify_tls','alert_api_verify_tls');
    const apiTok = document.getElementById('s_api_token')?.value || '';
    if (apiTok && apiTok !== '(set)') changes.alert_api_token = apiTok;

    // Enable-without-creds guard, matching the Slack pattern.
    if (changes.alert_api_enabled &&
        (!changes.alert_api_base_url || !changes.alert_api_domain)) {
      changes.alert_api_enabled = false;
      const apiToggle = document.getElementById('s_alert_api');
      if (apiToggle) apiToggle.checked = false;
      Utils.toast('API-based alerting requires endpoint root and domain — disabled.', 'warning');
    }
    boolField ('s_wifi_ssid_enabled',       'wifi_ssid_enabled');
    strField  ('s_wifi_ssid_agent_url',     'wifi_ssid_agent_url');
    strField  ('s_wifi_ssid_agent_iface',   'wifi_ssid_agent_interface');
    intField  ('s_wifi_ssid_poll_interval', 'wifi_ssid_poll_interval');

    // Slack guard: can't enable notifications without a webhook URL
    if (changes.alert_slack_enabled && !changes.alert_slack_webhook_url) {
      changes.alert_slack_enabled = false;
      const slackToggle = document.getElementById('s_alert_slack');
      if (slackToggle) slackToggle.checked = false;
      Utils.toast('Slack notifications require a webhook URL — disabled.', 'warning');
    }
    intField  ('s_retention',        'retention_days');

    // Elasticsearch settings — flat es_* keys matching backend _SETTINGS_WRITABLE
    const esEnabled = document.getElementById('s_es_enabled');
    if (esEnabled) {
      changes.es_enabled       = esEnabled.checked;
      changes.es_backend_type  = document.getElementById('s_es_backend_type')?.value || 'elasticsearch';
      changes.es_url           = document.getElementById('s_es_url')?.value?.trim() || '';
      changes.es_auth_method   = document.querySelector('input[name="es_auth_method"]:checked')?.value || 'none';
      changes.es_username      = document.getElementById('s_es_username')?.value?.trim() || '';
      changes.es_verify_tls    = !!(document.getElementById('s_es_verify_tls')?.checked);
      changes.es_agent_name    = document.getElementById('s_es_agent_name')?.value?.trim() || '';
      changes.es_dashboards_url        = document.getElementById('s_es_dashboards_url')?.value?.trim() || '';
      changes.es_dashboards_auth_method = document.querySelector('input[name="es_dashboards_auth_method"]:checked')?.value || 'none';
      changes.es_dashboards_username   = document.getElementById('s_es_dashboards_username')?.value?.trim() || '';
      changes.es_dashboards_verify_tls = !!(document.getElementById('s_es_dashboards_verify_tls')?.checked);
      changes.es_index_prefix  = document.getElementById('s_es_index_prefix')?.value?.trim() || 'sparrow-droneid';
      changes.es_shards        = parseInt(document.getElementById('s_es_shards')?.value || '2', 10);
      changes.es_replicas      = parseInt(document.getElementById('s_es_replicas')?.value || '0', 10);
      const ilmVal = document.getElementById('s_es_ilm_policy')?.value || '';
      changes.es_ilm_policy    = ilmVal === '__create_new__' ? '' : ilmVal;
      changes.es_bulk_size     = parseInt(document.getElementById('s_es_bulk_size')?.value || '100', 10);
      changes.es_flush_interval = parseInt(document.getElementById('s_es_flush_interval')?.value || '5', 10);

      // Only include sensitive fields if not blank and not the placeholder mask
      const esPasswordVal = document.getElementById('s_es_password')?.value || '';
      if (esPasswordVal && esPasswordVal !== '(set)') changes.es_password = esPasswordVal;

      const esApiKeyVal = document.getElementById('s_es_api_key')?.value || '';
      if (esApiKeyVal && esApiKeyVal !== '(set)') changes.es_api_key = esApiKeyVal;

      const esDashPasswordVal = document.getElementById('s_es_dashboards_password')?.value || '';
      if (esDashPasswordVal && esDashPasswordVal !== '(set)') changes.es_dashboards_password = esDashPasswordVal;

      const esDashApiKeyVal = document.getElementById('s_es_dashboards_api_key')?.value || '';
      if (esDashApiKeyVal && esDashApiKeyVal !== '(set)') changes.es_dashboards_api_key = esDashApiKeyVal;
    }

    // TAK settings (cot_* + tak_*): secrets follow the '(set)' convention in _readTakValues
    if (document.getElementById('s_tak_enabled')) {
      _clearTakErrors();
      const takBody = _collectTakFields();
      const takErrs = _validateTakClient(takBody);
      if (takErrs.length) {
        _focusTakTab();
        const first = _showTakErrors(takErrs);
        Utils.toast(_esc(first || 'Invalid TAK settings'), 'danger');
        return;
      }
      Object.assign(changes, takBody);
    }

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
      const takErrs = (e && e.detail && Array.isArray(e.detail.errors))
        ? e.detail.errors.filter(x => x && _TAK_FIELD_IDS[x.field]) : [];
      if (e && e.code === 'VALIDATION_ERROR' && takErrs.length) {
        // Keep the modal open on the TAK tab with the offending fields marked
        _focusTakTab();
        _clearTakErrors();
        const first = _showTakErrors(takErrs);
        Utils.toast(_esc(first || e.message), 'danger');
      } else {
        Utils.toast('Failed to save settings: ' + _esc(e.message), 'danger');
      }
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
