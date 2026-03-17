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

    // Re-attach click listeners
    tbody.querySelectorAll('tr[data-serial]').forEach(tr => {
      tr.addEventListener('click', () => {
        const serial = tr.dataset.serial;
        selectDrone(serial);
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

    return `<tr data-serial="${_esc(drone.serial_number)}" class="${stateClass} ${selected}">
      <td title="${_esc(drone.serial_number)}">${_esc(Utils.shortSerial(drone.serial_number))}</td>
      <td>${Utils.uaTypeHtml(drone.ua_type)}</td>
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

      const drone = _drones.find(d => d.serial_number === serial);
      if (drone && _onSelect) _onSelect(serial, drone);
    } else {
      if (_onSelect) _onSelect(null, null);
    }
  }

  function clearSelection() {
    selectDrone(null);
  }

  // ---- Detail sidebar content builder ----
  function buildDetailHtml(drone, track) {
    if (!drone) return '<div class="text-secondary text-center py-4">No drone selected</div>';

    const d = drone.derived || {};
    const state = d.state || 'active';

    const section = (title, rows) => `
      <div class="detail-section">
        <div class="detail-section-title">${title}</div>
        ${rows.map(([label, value]) => `
          <div class="detail-row">
            <span class="detail-label">${label}</span>
            <span class="detail-value">${value}</span>
          </div>`).join('')}
      </div>`;

    let html = `
      <div class="detail-state-bar ${state}"></div>
      <div class="detail-serial">${drone.serial_number || '—'}</div>`;

    // Fix #14: use CSS var instead of hardcoded #94A3B8
    if (drone.self_id_text) {
      html += `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:10px;font-style:italic;">"${drone.self_id_text}"</div>`;
    }

    html += section('Identity', [
      ['ID Type', drone.id_type_name || '—'],
      ['UA Type', drone.ua_type_name || '—'],
      ['Protocol', drone.protocol || '—'],
      ['MAC', drone.mac_address || '—'],
      ['Operator ID', drone.operator_id || '—'],
    ]);

    html += section('Position', [
      ['Lat', drone.drone_lat != null ? drone.drone_lat.toFixed(6) : '—'],
      ['Lon', drone.drone_lon != null ? drone.drone_lon.toFixed(6) : '—'],
      ['Alt AGL', Utils.formatAlt(drone.drone_height_agl)],
      ['Alt Geo', Utils.formatAlt(drone.drone_alt_geo)],
      ['Alt Baro', Utils.formatAlt(drone.drone_alt_baro)],
      ['Altitude Class', Utils.altBadge(d.altitude_class)],
    ]);

    // Fix #14: use CSS var for V-Speed unit label
    html += section('Motion', [
      ['Speed', Utils.formatSpeed(drone.speed)],
      ['V-Speed', drone.vertical_speed != null ? `${drone.vertical_speed.toFixed(1)} m/s` : '—'],
      ['Heading', drone.direction != null ? `${Math.round(drone.direction)}°` : '—'],
    ]);

    if (d.range_m != null) {
      html += section('From Receiver', [
        ['Range', Utils.formatRange(d.range_m)],
        ['Bearing', Utils.formatBearing(d.bearing_deg, d.bearing_cardinal)],
      ]);
    }

    if (drone.operator_lat && drone.operator_lon) {
      html += section('Operator', [
        ['Lat', drone.operator_lat.toFixed(6)],
        ['Lon', drone.operator_lon.toFixed(6)],
        ['Range', Utils.formatRange(d.operator_range_m)],
        ['Bearing', Utils.formatBearing(d.operator_bearing_deg, d.operator_bearing_cardinal)],
      ]);
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

  function showDetailSidebar(drone, track) {
    const sidebar = document.getElementById('detailSidebar');
    const body = document.getElementById('detailBody');
    const title = document.getElementById('detailTitle');

    if (!drone) {
      sidebar.classList.remove('open');
      return;
    }

    title.textContent = Utils.shortSerial(drone.serial_number);
    body.innerHTML = buildDetailHtml(drone, track);
    sidebar.classList.add('open');

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

  return {
    init,
    update,
    selectDrone,
    clearSelection,
    showDetailSidebar,
    hideDetailSidebar,
    refreshUnits,
  };
})();
