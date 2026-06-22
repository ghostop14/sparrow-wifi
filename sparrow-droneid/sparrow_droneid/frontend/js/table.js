/* ============================================================
   table.js — Drone table rendering, sorting, and selection
   ============================================================ */

const TableManager = (() => {

  let _drones = [];
  let _sortCol = 'last_seen';
  // Fix #12: default descending so newest drones appear at top
  let _sortAsc = false;
  let _selectedSerial = null;
  let _onSelect = null;    // callback(serial, drone)

  // Column widths (px, roughly)
  const COL_WIDTHS = {
    serial_number: '14%',
    ua_type:       '9%',
    drone_height_agl: '8%',
    speed:         '8%',
    range_m:       '8%',
    bearing_deg:   '9%',
    rssi:          '11%',
    protocol:      '8%',
    last_seen:     '8%',
    state:         '7%',
  };

  function init(onSelectCb) {
    _onSelect = onSelectCb;

    // Apply column widths
    const headers = document.querySelectorAll('#droneTable thead th');
    headers.forEach(th => {
      const col = th.dataset.col;
      if (COL_WIDTHS[col]) th.style.width = COL_WIDTHS[col];
    });

    // Sort click handlers
    document.querySelectorAll('#droneTable thead th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.dataset.col;
        if (_sortCol === col) {
          _sortAsc = !_sortAsc;
        } else {
          _sortCol = col;
          _sortAsc = true;
        }
        _updateSortIndicators();
        _render();
      });
    });

    _updateSortIndicators();
    _updateColumnHeaders();
  }

  // Update column header text to reflect current unit system
  function _updateColumnHeaders() {
    const altHeader = document.querySelector('#droneTable thead th[data-col="drone_height_agl"]');
    const speedHeader = document.querySelector('#droneTable thead th[data-col="speed"]');
    if (altHeader) {
      altHeader.innerHTML = `Alt AGL (${Utils.formatAltUnit()}) <i class="bi bi-arrow-down-up sort-icon"></i>`;
    }
    if (speedHeader) {
      speedHeader.innerHTML = `Speed (${Utils.formatSpeedUnit()}) <i class="bi bi-arrow-down-up sort-icon"></i>`;
    }
    _updateSortIndicators();
  }

  function _updateSortIndicators() {
    document.querySelectorAll('#droneTable thead th').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (th.dataset.col === _sortCol) {
        th.classList.add(_sortAsc ? 'sort-asc' : 'sort-desc');
      }
    });
  }

  function update(drones) {
    _drones = drones || [];
    _render();

    // Update count badge
    const count = _drones.length;
    document.getElementById('droneTabCount').textContent = count;
    document.getElementById('droneCountLabel').textContent = count;
    const badge = document.getElementById('droneCountBadge');
    if (count > 0) {
      badge.classList.add('has-drones');
    } else {
      badge.classList.remove('has-drones');
    }
  }

  function _sortedDrones() {
    return [..._drones].sort((a, b) => {
      let va = _getVal(a, _sortCol);
      let vb = _getVal(b, _sortCol);
      if (va === null || va === undefined) va = _sortAsc ? Infinity : -Infinity;
      if (vb === null || vb === undefined) vb = _sortAsc ? Infinity : -Infinity;
      if (typeof va === 'string') {
        return _sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return _sortAsc ? va - vb : vb - va;
    });
  }

  function _getVal(drone, col) {
    switch (col) {
      case 'serial_number':     return drone.serial_number;
      case 'ua_type':           return drone.ua_type;
      case 'drone_height_agl':  return drone.drone_height_agl;
      case 'speed':             return drone.speed;
      case 'range_m':           return drone.derived?.range_m ?? null;
      case 'bearing_deg':       return drone.derived?.bearing_deg ?? null;
      case 'rssi':              return drone.rssi;
      case 'protocol':          return drone.protocol;
      case 'last_seen':         return drone.last_seen ? new Date(drone.last_seen).getTime() : 0;
      case 'state': {
        const order = { active: 0, aging: 1, stale: 2 };
        return order[drone.derived?.state] ?? 3;
      }
      default: return null;
    }
  }

  function _render() {
    const tbody = document.getElementById('droneTableBody');
    if (!tbody) return;

    if (_drones.length === 0) {
      tbody.innerHTML = `
        <tr class="table-empty-row">
          <td colspan="10" class="text-center text-secondary py-4">
            <i class="bi bi-radar fs-3 d-block mb-2 opacity-25"></i>
            No drones detected
          </td>
        </tr>`;
      return;
    }

    const sorted = _sortedDrones();
    const rows = sorted.map(drone => _buildRow(drone)).join('');
    tbody.innerHTML = rows;

    // Re-attach click and context-menu listeners
    tbody.querySelectorAll('tr[data-serial]').forEach(tr => {
      tr.addEventListener('click', () => {
        const serial = tr.dataset.serial;
        selectDrone(serial);
        // Notify app (outside selectDrone to avoid circular callbacks)
        const drone = _drones.find(d => d.serial_number === serial);
        if (drone && _onSelect) _onSelect(serial, drone);
      });

      tr.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const serial = tr.dataset.serial;
        const drone = _drones.find(d => d.serial_number === serial);
        if (!drone) return;
        ContextMenu.show(e.clientX, e.clientY, [
          ...buildDispositionMenu(drone, _tagDrone),
          ...buildFlagsMenu(drone, _toggleFlag),
        ]);
      });
    });

    // Re-highlight selection
    if (_selectedSerial) {
      const row = tbody.querySelector(`tr[data-serial="${CSS.escape(_selectedSerial)}"]`);
      if (row) row.classList.add('selected');
    }
  }

  function _buildRow(drone) {
    const state = drone.derived?.state || 'active';
    const d = drone.derived || {};

    const stateHtml = `<span class="state-dot ${state}" title="${state}"></span>`;

    const selected = drone.serial_number === _selectedSerial ? 'selected' : '';
    const stateClass = `state-${state}`;

    const typeCell = drone.protocol === 'wifi_ssid'
      ? '<i class="bi bi-wifi ua-icon me-1" title="WiFi SSID"></i>SSID'
      : Utils.uaTypeHtml(drone.ua_type);

    const disp = drone.disposition || 'unknown';
    const dispDot = disp !== 'unknown'
      ? `<span class="disposition-dot disposition-${disp}" title="${disp}">&#9679;</span>`
      : '';
    const flagChips = (drone.military ? '<span class="flag-chip flag-military" title="Military">MIL</span>' : '')
      + (drone.law_enforcement ? '<span class="flag-chip flag-le" title="Law Enforcement">LE</span>' : '');

    return `<tr data-serial="${_esc(drone.serial_number)}" class="${stateClass} ${selected}">
      <td title="${_esc(drone.serial_number)}">${dispDot}${flagChips}${_esc(Utils.shortSerial(drone.serial_number))}</td>
      <td>${typeCell}</td>
      <td>${Utils.formatAlt(drone.drone_height_agl)}</td>
      <td>${Utils.formatSpeed(drone.speed)}</td>
      <td>${Utils.formatRange(d.range_m)}</td>
      <td>${Utils.formatBearing(d.bearing_deg, d.bearing_cardinal)}</td>
      <td>${Utils.rssiBarHtml(drone.rssi)}</td>
      <td>${Utils.protocolLabel(drone.protocol)}</td>
      <td>${Utils.relativeTime(drone.last_seen)}</td>
      <td>${stateHtml}</td>
    </tr>`;
  }

  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Visual-only: highlight the row. Does NOT fire the _onSelect callback
   *  to avoid a circular loop between map.js ↔ app.js ↔ table.js. */
  function selectDrone(serial) {
    const prev = document.querySelector('#droneTableBody tr.selected');
    if (prev) prev.classList.remove('selected');

    _selectedSerial = serial;

    if (serial) {
      const row = document.querySelector(`#droneTableBody tr[data-serial="${CSS.escape(serial)}"]`);
      if (row) {
        row.classList.add('selected');
        row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }
  }

  function clearSelection() {
    selectDrone(null);
  }

  // ---- Detail sidebar content builder ----
  /**
   * Build the detail HTML for a drone + track.
   *
   * @param {Object} drone   Full drone object from the API
   * @param {Array}  track   Array of track-point objects
   * @param {Object} [opts]  Optional rendering options:
   *   opts.whereToLookFirst {boolean} — when true, render "From Receiver" and
   *     "Operator" sections ABOVE Identity/Position/Motion (alert panel only).
   *     When absent/false, uses the standard sidebar order.
   * @returns {string}  HTML string
   */
  function buildDetailHtml(drone, track, opts) {
    if (!drone) return '<div class="text-secondary text-center py-4">No drone selected</div>';

    const d = drone.derived || {};
    const state = d.state || 'active';
    const whereFirst = opts && opts.whereToLookFirst;

    const section = (title, rows) => `
      <div class="detail-section">
        <div class="detail-section-title">${title}</div>
        ${rows.map(([label, value]) => `
          <div class="detail-row">
            <span class="detail-label">${label}</span>
            <span class="detail-value">${value}</span>
          </div>`).join('')}
      </div>`;

    const disp = drone.disposition || 'unknown';
    const dispBadge = `<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;">
      <span style="font-size:11px;color:var(--text-secondary);">Disposition:</span>
      <span class="disposition-${disp}" style="font-size:12px;font-weight:600;">${disp.charAt(0).toUpperCase() + disp.slice(1)}</span>
    </div>`;

    const flagBadges = (drone.military || drone.law_enforcement) ? `
      <div style="display:flex;align-items:center;gap:4px;margin-bottom:8px;">
        ${drone.military ? '<span class="flag-chip flag-military" title="Military">MIL</span>' : ''}
        ${drone.law_enforcement ? '<span class="flag-chip flag-le" title="Law Enforcement">LE</span>' : ''}
      </div>` : '';

    // Quick-look summary (alert panel only): the most actionable info first —
    // what kind of drone (friend/foe) and where to look — before the full
    // reference detail below.
    const alertView = opts && opts.alertSummary;
    function _quickLook() {
      const disp = drone.disposition || 'unknown';
      const dispChip = `<span class="disposition-${disp}" style="font-size:11px;font-weight:600;">${disp.charAt(0).toUpperCase() + disp.slice(1)}</span>`;
      const flags = `${drone.military ? '<span class="flag-chip flag-military" title="Military">MIL</span>' : ''}${drone.law_enforcement ? '<span class="flag-chip flag-le" title="Law Enforcement">LE</span>' : ''}`;
      const vendorStr = drone.vendor ? _esc(drone.vendor) + ' ' : '';
      const uaType = (drone.ua_type_name && drone.ua_type_name !== 'None / Not Declared') ? _esc(drone.ua_type_name) : 'Drone';
      let where = '';
      if (drone.drone_height_agl != null) {
        where += `<div>&#9650; ${_esc(Utils.formatAltDual(drone.drone_height_agl, 'AGL'))}</div>`;
      }
      if (d.range_m != null) {
        const card = d.bearing_cardinal || Utils.bearingCardinal(d.bearing_deg);
        where += `<div>&#9992; ${_esc(Utils.formatRangeDual(d.range_m))} &middot; brg ${Math.round(d.bearing_deg)}&deg; (${_esc(card)})</div>`;
      }
      if (d.operator_range_m != null) {
        const opCard = d.operator_bearing_cardinal || Utils.bearingCardinal(d.operator_bearing_deg);
        where += `<div>&#128100; Pilot ${_esc(Utils.formatRangeDual(d.operator_range_m))} &middot; brg ${Math.round(d.operator_bearing_deg)}&deg; (${_esc(opCard)})</div>`;
      }
      const selfId = drone.self_id_text
        ? `<div class="ql-selfid">"${_esc(drone.self_id_text)}"</div>` : '';
      return `<div class="alert-quicklook">
        <div class="ql-kind">${vendorStr}${uaType} ${dispChip} ${flags}</div>
        ${where ? `<div class="ql-where">${where}</div>` : ''}
        ${selfId}
      </div>`;
    }

    // All RemoteID-sourced string fields pass through _esc() — a hostile
    // drone can embed HTML/JS in serial_number, self_id_text, operator_id,
    // ua_type_name, id_type_name, or protocol and the detail sidebar
    // interpolates into innerHTML.
    let html = '';
    if (alertView) {
      // Labeled ID + quick-look. No green state bar (it read as a dead
      // divider/timeline) and no oversized unlabeled serial.
      html += `<div class="detail-id-line"><span class="detail-id-label">ID:</span> <span class="detail-id-value">${_esc(drone.serial_number) || '—'}</span></div>`;
      html += _quickLook();
    } else {
      html += `
        <div class="detail-state-bar ${state}"></div>
        <div class="detail-serial">${_esc(drone.serial_number) || '—'}</div>
        ${dispBadge}${flagBadges}`;
      if (drone.self_id_text) {
        html += `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:10px;font-style:italic;">"${_esc(drone.self_id_text)}"</div>`;
      }
    }

    const idRows = [];
    if (drone.vendor) idRows.push(['Manufacturer', _esc(drone.vendor)]);
    const uaType = (drone.ua_type_name && drone.ua_type_name !== 'None / Not Declared') ? drone.ua_type_name : '';
    if (uaType) idRows.push(['UA Type', _esc(uaType)]);
    if (drone.id_type_name && drone.id_type_name !== 'None / Not Declared') {
      idRows.push(['ID Type', _esc(drone.id_type_name)]);
    }
    const protoNames = { astm_nan: 'WiFi NAN', astm_beacon: 'WiFi Beacon', astm_ble: 'Bluetooth', dji_proprietary: 'WiFi (DJI)', french: 'French RemoteID', wifi_ssid: 'WiFi SSID Detection' };
    idRows.push(['Protocol', _esc(protoNames[drone.protocol] || drone.protocol || '—')]);
    idRows.push(['MAC', _esc(drone.mac_address || '—')]);
    if (drone.operator_id) idRows.push(['Operator ID', _esc(drone.operator_id)]);
    html += section('Identity', idRows);

    // Helper: build a lat/lon coordinate block with MGRS sibling
    function _coordRows(lat, lon) {
      // Treat null/undefined and the 0,0 SQLite default as "absent" — the
      // convention used throughout the codebase. mgrs.forward([0,0]) otherwise
      // succeeds and emits a bogus Gulf-of-Guinea grid square.
      const present = lat != null && lon != null && (lat !== 0 || lon !== 0);
      if (!present) {
        return [['Lat', '—'], ['Lon', '—']];
      }
      const mgrsStr = Utils.toMGRS(lat, lon);
      const rows = [
        ['Lat', lat.toFixed(6)],
        ['Lon', lon.toFixed(6)],
      ];
      if (mgrsStr) {
        rows.push(['MGRS',
          `<span style="font-family:monospace;">${_esc(mgrsStr)}</span>` +
          ` <button class="btn-copy-inline" title="Copy MGRS" ` +
          `onclick="Utils.copyToClipboard('${_esc(mgrsStr)}', 'MGRS copied')">` +
          `<i class="bi bi-clipboard" style="font-size:10px;"></i></button>`
        ]);
      }
      return rows;
    }

    // ---- "From Receiver" and "Operator" sections (reusable) ----
    const fromReceiverHtml = d.range_m != null ? section('From Receiver', [
      ['Range', Utils.formatRange(d.range_m)],
      ['Bearing', Utils.formatBearing(d.bearing_deg, d.bearing_cardinal)],
    ]) : '';

    let operatorHtml = '';
    if (drone.operator_lat && drone.operator_lon) {
      operatorHtml = section('Operator', [
        ..._coordRows(drone.operator_lat, drone.operator_lon),
        ['Range', Utils.formatRange(d.operator_range_m)],
        ['Bearing', Utils.formatBearing(d.operator_bearing_deg, d.operator_bearing_cardinal)],
      ]);
    } else if (drone.takeoff_lat && drone.takeoff_lon) {
      // French RemoteID: no live operator position, only launch point.
      operatorHtml = section('Takeoff Point', [
        ['Lat', drone.takeoff_lat.toFixed(6)],
        ['Lon', drone.takeoff_lon.toFixed(6)],
        ['Note', '<span style="color:#9CA3AF;font-style:italic;">Launch location, not pilot position</span>'],
      ]);
    }

    // When whereToLookFirst: render Receiver + Operator sections ABOVE Identity
    if (whereFirst) {
      html += fromReceiverHtml;
      html += operatorHtml;
    }

    html += section('Position', [
      ..._coordRows(drone.drone_lat, drone.drone_lon),
      ['AGL (height above takeoff)', Utils.formatAlt(drone.drone_height_agl)],
      ['Alt Geo', Utils.formatAltOrAbsent(drone.drone_alt_geo)],
      ['Alt Baro', Utils.formatAltOrAbsent(drone.drone_alt_baro)],
      ['Altitude Class', Utils.altBadge(d.altitude_class)],
    ]);

    // Fix #14: use CSS var for V-Speed unit label
    html += section('Motion', [
      ['Speed', Utils.formatSpeed(drone.speed)],
      ['V-Speed', drone.vertical_speed != null ? `${drone.vertical_speed.toFixed(1)} m/s` : '—'],
      ['Heading', drone.direction != null ? `${Math.round(drone.direction)}°` : '—'],
    ]);

    // Standard order: Receiver + Operator appear after Motion (unless whereFirst overrode)
    if (!whereFirst) {
      html += fromReceiverHtml;
      html += operatorHtml;
    }

    html += section('Signal', [
      ['RSSI', Utils.rssiBarHtml(drone.rssi)],
      ['Trend', drone.rssi_trend || '—'],
      ['State', `<span class="state-dot ${state}" style="margin-right:4px"></span>${state}`],
    ]);

    html += section('Session', [
      ['First Seen', Utils.formatDateTime(drone.first_seen)],
      ['Last Seen', Utils.formatDateTime(drone.last_seen)],
      ['In Area', Utils.formatDuration(drone.time_in_area_seconds)],
    ]);

    if (track && track.length > 0) {
      html += `<button class="btn-detail-track" id="btnDetailShowTrack">
        <i class="bi bi-geo-alt-fill me-1"></i>Show Track (${track.length} pts)
      </button>`;
    }

    return html;
  }

  let _detailShownSerial = null;

  function showDetailSidebar(drone, track) {
    const sidebar = document.getElementById('detailSidebar');
    const body = document.getElementById('detailBody');
    const title = document.getElementById('detailTitle');

    if (!drone) {
      sidebar.classList.remove('open');
      _detailShownSerial = null;
      return;
    }

    const isSameDrone = _detailShownSerial === drone.serial_number;
    const prevScroll = isSameDrone ? body.scrollTop : 0;

    title.textContent = Utils.shortSerial(drone.serial_number);
    body.innerHTML = buildDetailHtml(drone, track);
    body.scrollTop = prevScroll;
    sidebar.classList.add('open');
    _detailShownSerial = drone.serial_number;

    // Attach track button handler
    const trackBtn = document.getElementById('btnDetailShowTrack');
    if (trackBtn && track) {
      trackBtn.addEventListener('click', () => {
        MapManager.showTrack(track);
      });
    }
  }

  function hideDetailSidebar() {
    document.getElementById('detailSidebar')?.classList.remove('open');
  }

  // Refresh table and column headers after unit toggle
  function refreshUnits() {
    _updateColumnHeaders();
    _render();
  }

  // ---- Disposition tagging ----
  function _tagDrone(drone, disposition) {
    const key = drone.drone_key || drone.serial_number || drone.registration_id || drone.mac_address;
    if (!key) return;
    Api.putDisposition(key, disposition).catch(() => {});
    // Optimistically update local state so the UI refreshes immediately
    drone.disposition = disposition;
    _render();
    if (typeof App !== 'undefined' && App.pollDronesNow) {
      App.pollDronesNow();
    }
  }

  // ---- Flag toggling ----
  function _toggleFlag(drone, name, value) {
    const key = drone.drone_key || drone.serial_number || drone.registration_id || drone.mac_address;
    if (!key) return;
    Api.putFlags(key, { [name]: value }).catch(() => {});
    // Optimistically update local state so the UI refreshes immediately
    drone[name] = value;
    _render();
    if (typeof App !== 'undefined' && App.pollDronesNow) {
      App.pollDronesNow();
    }
  }

  /** Return the detail HTML string for a drone + track without touching the
   *  sidebar DOM.  Used by AlertsManager to render context into inline panels.
   *  @param {Object} drone
   *  @param {Array}  track
   *  @param {Object} [opts]  Passed through to buildDetailHtml (e.g. {whereToLookFirst:true})
   */
  function renderDetailHtml(drone, track, opts) {
    return buildDetailHtml(drone, track, opts);
  }

  return {
    init,
    update,
    selectDrone,
    clearSelection,
    showDetailSidebar,
    hideDetailSidebar,
    refreshUnits,
    renderDetailHtml,
  };
})();
