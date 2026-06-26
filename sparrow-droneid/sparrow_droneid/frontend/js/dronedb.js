/* ============================================================
   dronedb.js — Drone Database tab
   Lists every serial in the 90-day retention window, one row per
   serial, with disposition/flags/vendor enrichment, last-known
   drone + controller positions as Google Maps links, client-side
   column sorting, right-click disposition/flag editing, and
   inline row expansion reusing TableManager.renderDetailHtml.
   ============================================================ */

const DroneDbManager = (() => {

  // ---- State ----
  let _rows = [];
  let _sortCol = 'last_seen';
  let _sortAsc = false;           // default: newest first
  let _expandedSerial = null;

  // ---- Init ----
  function init() {
    // Wire sortable column headers
    document.querySelectorAll('#droneDbTable thead th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.dataset.col;
        if (_sortCol === col) {
          _sortAsc = !_sortAsc;
        } else {
          _sortCol = col;
          // last_seen defaults desc; everything else defaults asc on first click
          _sortAsc = (col !== 'last_seen');
        }
        _updateSortIndicators();
        _render();
      });
    });
    _updateSortIndicators();
  }

  // ---- Load from API ----
  async function load() {
    try {
      const resp = await Api.getDroneDatabase();
      _rows = resp.drones || [];
      _render();
    } catch (e) {
      Utils.toast('Drone Database: ' + (e.message || 'load failed'), 'alert');
    }
  }

  // ---- Sort helpers ----
  function _sortedRows() {
    return [..._rows].sort((a, b) => {
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

  function _getVal(row, col) {
    switch (col) {
      case 'last_seen':        return row.last_seen ? new Date(row.last_seen).getTime() : 0;
      case 'ua_type':          return row.ua_type != null ? Number(row.ua_type) : null;
      case 'serial_number':    return row.serial_number || '';
      case 'detection_count':  return row.detection_count != null ? Number(row.detection_count) : 0;
      default: return null;
    }
  }

  function _updateSortIndicators() {
    document.querySelectorAll('#droneDbTable thead th').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (th.dataset.col === _sortCol) {
        th.classList.add(_sortAsc ? 'sort-asc' : 'sort-desc');
      }
    });
  }

  // ---- Render ----
  function _render() {
    const tbody = document.getElementById('droneDbTableBody');
    if (!tbody) return;

    if (_rows.length === 0) {
      tbody.innerHTML = `
        <tr class="table-empty-row">
          <td colspan="6" class="text-center text-secondary py-4">
            <i class="bi bi-database fs-3 d-block mb-2 opacity-25"></i>
            No drones in database
          </td>
        </tr>`;
      return;
    }

    const sorted = _sortedRows();
    let html = '';
    sorted.forEach(row => {
      const sn = row.serial_number || '';
      const isExpanded = sn === _expandedSerial;
      html += _buildRow(row, isExpanded);
      // Adjacent expand row — only emitted when expanded, so collapsed
      // entries don't leave a phantom empty striped row between them.
      if (isExpanded) {
        html += `<tr class="dronedb-detail-row expanded" data-serial="${_esc(sn)}">`;
        html += `<td colspan="6" class="dronedb-detail-cell">`;
        html += TableManager.renderDetailHtml(row, []);
        html += `</td></tr>`;
      }
    });
    tbody.innerHTML = html;

    // Wire row interactions
    tbody.querySelectorAll('tr[data-serial]:not(.dronedb-detail-row)').forEach(tr => {
      tr.addEventListener('click', () => {
        _toggleExpand(tr.dataset.serial);
      });

      tr.addEventListener('contextmenu', e => {
        e.preventDefault();
        const sn = tr.dataset.serial;
        const row = _rows.find(r => r.serial_number === sn);
        if (!row) return;
        ContextMenu.show(e.clientX, e.clientY, [
          ...buildDispositionMenu(row, _tagDrone),
          ...buildFlagsMenu(row, _toggleFlag),
        ]);
      });
    });
  }

  // ---- Build a single data row ----
  function _buildRow(row, isExpanded) {
    const sn = row.serial_number || '';

    // Last Seen
    const lastSeenHtml = Utils.formatDateTime(row.last_seen);

    // Type: vendor + UA type
    const vendor = row.vendor ? _esc(row.vendor) + ' ' : '';
    const uaName = (row.ua_type_name && row.ua_type_name !== 'None / Not Declared')
      ? _esc(row.ua_type_name) : '';
    const typeHtml = vendor || uaName ? `${vendor}${uaName}` : '—';

    // ID: disposition dot + flag chips + full serial (the ID column flexes to
    // fill the table's free width, so there's room for the untruncated serial).
    const disp = row.disposition || 'unknown';
    const dispDot = disp !== 'unknown'
      ? `<span class="disposition-dot disposition-${disp}" title="${disp}">&#9679;</span>`
      : '';
    const flagChips =
      (row.military ? '<span class="flag-chip flag-military" title="Military">MIL</span>' : '') +
      (row.law_enforcement ? '<span class="flag-chip flag-le" title="Law Enforcement">LE</span>' : '');
    const idHtml = `${dispDot}${flagChips}${_esc(sn)}`;

    // Position cells — map link or dash
    const dronePos = row.drone_maps_url
      ? `<a href="${_esc(row.drone_maps_url)}" target="_blank" rel="noopener" title="Open in Google Maps"><i class="bi bi-geo-alt-fill"></i></a>`
      : '—';
    const ctrlPos = row.controller_maps_url
      ? `<a href="${_esc(row.controller_maps_url)}" target="_blank" rel="noopener" title="Controller position in Google Maps"><i class="bi bi-geo-alt"></i></a>`
      : '—';

    // Detection count
    const cnt = row.detection_count != null ? row.detection_count : '—';

    const expandedClass = isExpanded ? ' expanded' : '';

    return `<tr data-serial="${_esc(sn)}" class="dronedb-row${expandedClass}" title="${_esc(sn)}">
      <td>${lastSeenHtml}</td>
      <td>${typeHtml}</td>
      <td>${idHtml}</td>
      <td class="text-center">${dronePos}</td>
      <td class="text-center">${ctrlPos}</td>
      <td class="text-end">${cnt}</td>
    </tr>`;
  }

  // ---- Inline expand ----
  function _toggleExpand(serial) {
    if (_expandedSerial === serial) {
      _expandedSerial = null;
    } else {
      _expandedSerial = serial;
    }
    _render();
  }

  // ---- Disposition tagging ----
  function _tagDrone(row, disposition) {
    const key = row.drone_key || row.serial_number;
    if (!key) return;
    Api.putDisposition(key, disposition).catch(() => {});
    // Optimistic update
    row.disposition = disposition;
    _render();
  }

  // ---- Flag toggling ----
  function _toggleFlag(row, name, value) {
    const key = row.drone_key || row.serial_number;
    if (!key) return;
    Api.putFlags(key, { [name]: value }).catch(() => {});
    // Optimistic update
    row[name] = value;
    _render();
  }

  // ---- Unit refresh ----
  // Called by app.js _initUnitToggle so expanded detail rows reflect the new units.
  function refreshUnits() {
    if (_rows.length > 0) _render();
  }

  // ---- XSS guard ----
  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  return {
    init,
    load,
    refreshUnits,
  };
})();
