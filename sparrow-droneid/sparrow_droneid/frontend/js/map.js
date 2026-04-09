/* ============================================================
   map.js — Leaflet map management
   Drone markers, operator markers, receiver, range rings,
   track trails, popups, and layer switching
   ============================================================ */

const MapManager = (() => {

  let _map = null;
  let _receiverMarker = null;
  let _rangeRings = [];
  let _rangeRingsVisible = true;
  let _droneMarkers = {};       // serial -> { marker, operatorMarker, line, track }
  let _selectedSerial = null;
  let _trackPolyline = null;
  let _onDroneClick = null;     // callback(serial)
  let _allTracksVisible = false;
  let _allTrackPolylines = {};  // serial -> L.polyline
  let _osmLightLayer = null;
  let _osmDarkLayer = null;
  let _osmLayer = null;       // currently active OSM layer (light or dark)
  let _satelliteLayer = null;
  let _layerControl = null;
  let _currentTheme = 'dark';

  // Default center (Washington DC area) — will update from receiver position
  const DEFAULT_CENTER = [38.8977, -77.0365];
  const DEFAULT_ZOOM   = 14;
  let _hasAutocentered = false;  // only auto-center once on first receiver position

  // ---- Tile URL builder (proxied through backend) ----
  function tileUrl(source) {
    return `/api/v1/tiles/${source}/{z}/{x}/{y}.png`;
  }

  // ---- Init ----
  function init(onDroneClickCb) {
    _onDroneClick = onDroneClickCb;

    _map = L.map('map', {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      zoomControl: true,
      attributionControl: true,
    });

    // Tile layers — light and dark OSM variants
    // Dark mode uses the same OSM tiles with a CSS invert filter applied via
    // className — this preserves all road detail and labels unlike CartoDB Dark Matter.
    _osmLightLayer = L.tileLayer(tileUrl('osm'), {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    });

    _osmDarkLayer = L.tileLayer(tileUrl('osm'), {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
      className: 'dark-tiles',
    });

    // Satellite view: imagery + transparent street/place labels overlay
    const satImagery = L.tileLayer(tileUrl('esri_satellite'), {
      attribution: '&copy; Esri &mdash; Source: Esri, USGS',
      maxZoom: 19,
    });
    const satLabels = L.tileLayer(tileUrl('esri_labels'), {
      maxZoom: 19,
      pane: 'overlayPane',   // render above imagery
    });
    _satelliteLayer = L.layerGroup([satImagery, satLabels]);

    // Pick initial OSM layer based on current theme
    _currentTheme = document.documentElement.getAttribute('data-bs-theme') || 'dark';
    _osmLayer = _currentTheme === 'dark' ? _osmDarkLayer : _osmLightLayer;
    _osmLayer.addTo(_map);

    // Layer control
    _layerControl = L.control.layers(
      { 'Map': _osmLayer, 'Satellite': _satelliteLayer },
      {},
      { position: 'topright', collapsed: true }
    ).addTo(_map);

    // Refresh range ring colors when switching between Map and Satellite
    _map.on('baselayerchange', () => _refreshRangeRings());

    // Ruler control (lower-right)
    const MeasureControl = L.Control.extend({
      options: { position: 'bottomright' },
      onAdd() {
        const btn = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-measure');
        btn.innerHTML = '<a href="#" title="Measure distance" aria-label="Measure distance" aria-pressed="false" role="button"><i class="bi bi-rulers"></i></a>';
        btn.querySelector('a').style.cssText = 'display:flex;align-items:center;justify-content:center;width:30px;height:30px;text-decoration:none;';
        L.DomEvent.disableClickPropagation(btn);
        btn.addEventListener('click', (e) => { e.preventDefault(); toggleMeasure(); });
        return btn;
      },
    });
    new MeasureControl().addTo(_map);

    // Compass toggle control (lower-right, above ruler)
    const CompassControl = L.Control.extend({
      options: { position: 'bottomright' },
      onAdd() {
        const btn = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-compass-toggle');
        btn.innerHTML = '<a href="#" title="Toggle compass" aria-label="Toggle compass" aria-pressed="false" role="button"><i class="bi bi-compass"></i></a>';
        btn.querySelector('a').style.cssText = 'display:flex;align-items:center;justify-content:center;width:30px;height:30px;text-decoration:none;';
        L.DomEvent.disableClickPropagation(btn);
        btn.addEventListener('click', (e) => { e.preventDefault(); toggleCompass(); });
        return btn;
      },
    });
    new CompassControl().addTo(_map);

    // Flight tracks toggle control (lower-right, above compass)
    const TracksControl = L.Control.extend({
      options: { position: 'bottomright' },
      onAdd() {
        const btn = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-tracks-toggle');
        btn.innerHTML = '<a href="#" title="Toggle all flight tracks" aria-label="Toggle all flight tracks" aria-pressed="false" role="button"><i class="bi bi-bezier2"></i></a>';
        btn.querySelector('a').style.cssText = 'display:flex;align-items:center;justify-content:center;width:30px;height:30px;text-decoration:none;';
        L.DomEvent.disableClickPropagation(btn);
        btn.addEventListener('click', (e) => { e.preventDefault(); toggleAllTracks(); });
        return btn;
      },
    });
    new TracksControl().addTo(_map);

    // Go-to input + cursor position (single row, lower-left)
    let _cursorValueEl = null;
    const CoordBarControl = L.Control.extend({
      options: { position: 'bottomleft' },
      onAdd() {
        const bar = L.DomUtil.create('div', 'leaflet-control map-coordbar');
        bar.innerHTML =
          '<input type="text" class="goto-input" placeholder="33.12345, -117.56789" title="Enter lat,lon and press Enter to snap map" />' +
          '<span class="cursorpos-label">Cursor</span> <span class="cursorpos-value">off map</span>';
        L.DomEvent.disableClickPropagation(bar);
        const input = bar.querySelector('.goto-input');
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            _handleGoTo(input.value.trim());
            input.value = '';
            input.blur();
          }
        });
        _cursorValueEl = bar.querySelector('.cursorpos-value');
        return bar;
      },
    });
    new CoordBarControl().addTo(_map);

    _map.on('mousemove', (e) => {
      if (_cursorValueEl) _cursorValueEl.textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    });
    _map.on('mouseout', () => {
      if (_cursorValueEl) _cursorValueEl.textContent = 'off map';
    });

    // Click on map deselects (skip when ruler is active)
    _map.on('click', () => {
      if (_measureActive) return;
      if (_selectedSerial) {
        clearTrack();
        _selectedSerial = null;
        if (_onDroneClick) _onDroneClick(null);
      }
    });

    return _map;
  }

  // ---- Receiver position ----
  function setReceiverPosition(lat, lon, gpsFix, receiver) {
    if (!_map) return;
    if (!lat || !lon) return;

    const latLng = [lat, lon];
    const popupHtml = _buildReceiverPopup(lat, lon, gpsFix, receiver);

    if (_receiverMarker) {
      _receiverMarker.setLatLng(latLng);
      _receiverMarker.getPopup()?.setContent(popupHtml);
    } else {
      const icon = L.divIcon({
        className: '',
        html: `<div style="
          width:16px;height:16px;
          background:${gpsFix ? '#2563EB' : '#94A3B8'};
          border:2px solid #fff;
          border-radius:50%;
          box-shadow:0 0 0 3px rgba(37,99,235,0.4);
        "></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      _receiverMarker = L.marker(latLng, { icon, zIndexOffset: 500, title: 'Receiver' }).addTo(_map);
      _receiverMarker.bindPopup(popupHtml, { maxWidth: 280, minWidth: 180 });
    }

    // Auto-center map on first valid receiver position (GPS fix or static coords)
    if (!_hasAutocentered) {
      _map.setView(latLng, DEFAULT_ZOOM);
      _hasAutocentered = true;
    }

    updateRangeRings(lat, lon);
  }

  function _buildReceiverPopup(lat, lon, gpsFix, receiver) {
    const name = localStorage.getItem('sparrow_operator_name') || 'Receiver';
    const source = receiver?.source || 'unknown';
    const fixStr = gpsFix ? 'Yes' : 'No';
    const rows = [
      _popupRow('GPS fix', fixStr),
      _popupRow('Source', source),
      _popupRow('Latitude', lat.toFixed(5)),
      _popupRow('Longitude', lon.toFixed(5)),
      _popupRow('Alt MSL', Utils.formatAlt(receiver?.alt)),
      _popupRow('Speed', receiver?.speed ? Utils.formatSpeed(receiver.speed) : null),
    ].filter(Boolean).join('');

    return `
      <div class="drone-popup">
        <div class="drone-popup-title" style="color:#2563EB;"><i class="bi bi-broadcast me-1"></i>${name}</div>
        <div class="drone-popup-grid">${rows}</div>
      </div>`;
  }

  // ---- Range rings ----

  // Ring color palettes per visual context
  // Cyan on dark/satellite matches aviation convention (VFR charts, TCAS displays)
  const _ringStyles = {
    light:     { color: '#2563EB', opacity: 0.60, labelColor: '#2563EB', labelShadow: '0 0 3px #fff, 0 0 3px #fff' },
    dark:      { color: '#22D3EE', opacity: 0.70, labelColor: '#22D3EE', labelShadow: '0 0 4px #0d1117' },
    satellite: { color: '#22D3EE', opacity: 0.75, labelColor: '#22D3EE', labelShadow: '0 1px 3px rgba(0,0,0,0.8)' },
  };

  function _getRingStyle() {
    if (_map && _map.hasLayer(_satelliteLayer)) return _ringStyles.satellite;
    return _currentTheme === 'dark' ? _ringStyles.dark : _ringStyles.light;
  }

  function updateRangeRings(lat, lon) {
    _rangeRings.forEach(r => _map.removeLayer(r));
    _rangeRings = [];

    if (!_rangeRingsVisible || !lat || !lon) return;

    const imperial = Utils.getUnits && Utils.getUnits() === 'imperial';
    const radii  = imperial
      ? [402.3, 804.7, 1609.3, 3218.7]   // 0.25mi, 0.5mi, 1mi, 2mi
      : [500, 1000, 2000, 3000];           // 0.5km, 1km, 2km, 3km
    const labels = imperial
      ? ['0.25 mi', '0.5 mi', '1 mi', '2 mi']
      : ['500 m', '1 km', '2 km', '3 km'];

    const style = _getRingStyle();
    // Opacity gradient: inner rings full strength, outer rings slightly lighter
    const opacityScale = [1.0, 0.9, 0.8, 0.7];

    radii.forEach((r, i) => {
      const circle = L.circle([lat, lon], {
        radius: r,
        color: style.color,
        opacity: style.opacity * opacityScale[i],
        weight: 2.5,
        dashArray: '6 8',
        fillOpacity: 0,
        interactive: false,
      }).addTo(_map);
      _rangeRings.push(circle);

      // Label
      const labelIcon = L.divIcon({
        className: '',
        html: `<span style="font-size:10px;font-weight:600;color:${style.labelColor};opacity:0.9;text-shadow:${style.labelShadow};">${labels[i]}</span>`,
        iconAnchor: [0, 0],
      });
      const bearing = 45; // NE label position
      const labelLatLng = destinationPoint(lat, lon, r, bearing);
      const labelMarker = L.marker(labelLatLng, { icon: labelIcon, interactive: false }).addTo(_map);
      _rangeRings.push(labelMarker);
    });
  }

  function _refreshRangeRings() {
    if (_receiverMarker && _rangeRingsVisible) {
      const ll = _receiverMarker.getLatLng();
      updateRangeRings(ll.lat, ll.lng);
    }
  }

  function toggleRangeRings() {
    _rangeRingsVisible = !_rangeRingsVisible;
    if (_receiverMarker) {
      const latLng = _receiverMarker.getLatLng();
      updateRangeRings(latLng.lat, latLng.lng);
    }
    return _rangeRingsVisible;
  }

  // Great-circle destination point
  function destinationPoint(lat, lon, distM, bearingDeg) {
    const R = 6371000;
    const d = distM / R;
    const b = bearingDeg * Math.PI / 180;
    const lat1 = lat * Math.PI / 180;
    const lon1 = lon * Math.PI / 180;
    const lat2 = Math.asin(Math.sin(lat1)*Math.cos(d) + Math.cos(lat1)*Math.sin(d)*Math.cos(b));
    const lon2 = lon1 + Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(lat1), Math.cos(d)-Math.sin(lat1)*Math.sin(lat2));
    return [lat2 * 180/Math.PI, lon2 * 180/Math.PI];
  }

  // ---- Ruler measurement tool ----

  let _measureActive = false;
  let _measureStart = null;    // L.marker
  let _measureEnd = null;      // L.marker
  let _measureLine = null;     // L.polyline
  let _measureBar = null;      // DOM element

  function _formatMeasurement(meters) {
    const imperial = Utils.getUnits && Utils.getUnits() === 'imperial';
    if (imperial) {
      const feet = meters * 3.28084;
      if (feet < 1320) return `${Math.round(feet)} ft`;          // < 0.25 mi
      return `${(meters / 1609.34).toFixed(2)} mi`;
    }
    if (meters < 400) return `${Math.round(meters)} m`;
    return `${(meters / 1000).toFixed(2)} km`;
  }

  function _bearingBetween(lat1, lon1, lat2, lon2) {
    const toRad = d => d * Math.PI / 180;
    const toDeg = r => r * 180 / Math.PI;
    const dLon = toRad(lon2 - lon1);
    const y = Math.sin(dLon) * Math.cos(toRad(lat2));
    const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
              Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function _bearingCardinal(deg) {
    const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                  'S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return dirs[Math.round(deg / 22.5) % 16];
  }

  function _haversineDist(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function _updateMeasureDisplay() {
    if (!_measureStart || !_measureEnd || !_measureBar) return;
    const s = _measureStart.getLatLng();
    const e = _measureEnd.getLatLng();
    const dist = _haversineDist(s.lat, s.lng, e.lat, e.lng);
    const brg = _bearingBetween(s.lat, s.lng, e.lat, e.lng);
    const card = _bearingCardinal(brg);

    _measureBar.innerHTML =
      `<span class="measure-label">Distance:</span> <span class="measure-value">${_formatMeasurement(dist)}</span>` +
      `<span class="measure-label">Bearing:</span> <span class="measure-value">${Math.round(brg)}&deg; ${card}</span>` +
      `<span class="measure-hint">Left-click: start &nbsp; Right-click: end</span>`;

    if (_measureLine) _map.removeLayer(_measureLine);
    _measureLine = L.polyline([s, e], {
      color: '#3B82F6', weight: 2, dashArray: '6 4', opacity: 0.9, interactive: false,
    }).addTo(_map);
  }

  function _measurePin(color) {
    return L.divIcon({
      className: '',
      html: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><polygon points="8,1 15,15 1,15" fill="${color}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
  }

  function _onMeasureLeftClick(e) {
    if (!_measureActive) return;
    if (_measureStart) _map.removeLayer(_measureStart);
    _measureStart = L.marker(e.latlng, { icon: _measurePin('#22C55E'), interactive: false }).addTo(_map);
    if (_measureEnd) _updateMeasureDisplay();
    else {
      _measureBar.innerHTML =
        `<span class="measure-label">Start placed.</span>` +
        `<span class="measure-hint">Right-click to place end marker</span>`;
    }
  }

  function _onMeasureRightClick(e) {
    if (!_measureActive) return;
    e.originalEvent.preventDefault();
    if (_measureEnd) _map.removeLayer(_measureEnd);
    _measureEnd = L.marker(e.latlng, { icon: _measurePin('#EF4444'), interactive: false }).addTo(_map);
    if (_measureStart) _updateMeasureDisplay();
    else {
      _measureBar.innerHTML =
        `<span class="measure-label">End placed.</span>` +
        `<span class="measure-hint">Left-click to place start marker</span>`;
    }
  }

  function _clearMeasure() {
    if (_measureStart) { _map.removeLayer(_measureStart); _measureStart = null; }
    if (_measureEnd)   { _map.removeLayer(_measureEnd);   _measureEnd = null; }
    if (_measureLine)  { _map.removeLayer(_measureLine);  _measureLine = null; }
    if (_measureBar)   { _measureBar.remove(); _measureBar = null; }
  }

  function _onMeasureKey(e) {
    if (e.key === 'Escape' && _measureActive) toggleMeasure();
  }

  /** Re-render the measurement readout with current unit setting. */
  function refreshMeasureUnits() {
    if (_measureActive && _measureStart && _measureEnd) _updateMeasureDisplay();
  }

  function toggleMeasure() {
    _measureActive = !_measureActive;
    const btn = document.querySelector('.leaflet-control-measure');
    if (btn) {
      btn.classList.toggle('active', _measureActive);
      const a = btn.querySelector('a');
      if (a) a.setAttribute('aria-pressed', _measureActive);
    }

    if (_measureActive) {
      _map.getContainer().style.cursor = 'crosshair';
      // Create the readout bar inside the map container
      _measureBar = document.createElement('div');
      _measureBar.className = 'measure-bar';
      _measureBar.innerHTML =
        `<span class="measure-label">Measure mode</span>` +
        `<span class="measure-hint">Left-click: start &nbsp; Right-click: end &nbsp; Esc: cancel</span>`;
      _map.getContainer().appendChild(_measureBar);
      _map.on('click', _onMeasureLeftClick);
      _map.on('contextmenu', _onMeasureRightClick);
      document.addEventListener('keydown', _onMeasureKey);
    } else {
      _map.getContainer().style.cursor = '';
      _map.off('click', _onMeasureLeftClick);
      _map.off('contextmenu', _onMeasureRightClick);
      document.removeEventListener('keydown', _onMeasureKey);
      _clearMeasure();
    }
    return _measureActive;
  }

  // ---- Compass rose ----

  let _compassVisible = false;
  let _compassEl = null;

  const _compassSVG = `<svg class="compass-rose" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
    <!-- Outer ring -->
    <circle cx="60" cy="60" r="54" fill="rgba(15,23,42,0.75)" stroke="rgba(148,163,184,0.5)" stroke-width="1"/>
    <!-- Tick marks: 16 divisions (every 22.5 deg) -->
    ${[...Array(16)].map((_, i) => {
      const angle = i * 22.5;
      const isCardinal = i % 4 === 0;
      const r1 = isCardinal ? 38 : 43;
      const r2 = 50;
      const rad = (angle - 90) * Math.PI / 180;
      const x1 = 60 + r1 * Math.cos(rad), y1 = 60 + r1 * Math.sin(rad);
      const x2 = 60 + r2 * Math.cos(rad), y2 = 60 + r2 * Math.sin(rad);
      const w = isCardinal ? 1.8 : 0.8;
      const col = isCardinal ? 'rgba(241,245,249,0.95)' : 'rgba(148,163,184,0.6)';
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="${w}"/>`;
    }).join('')}
    <!-- Cardinal labels -->
    <text x="60" y="24" text-anchor="middle" fill="#EF4444" font-size="13">N</text>
    <text x="60" y="103" text-anchor="middle" fill="rgba(241,245,249,0.85)" font-size="11">S</text>
    <text x="99" y="64" text-anchor="middle" fill="rgba(241,245,249,0.85)" font-size="11">E</text>
    <text x="21" y="64" text-anchor="middle" fill="rgba(241,245,249,0.85)" font-size="11">W</text>
    <!-- Center dot -->
    <circle cx="60" cy="60" r="2.5" fill="rgba(241,245,249,0.6)"/>
    <!-- North pointer -->
    <polygon points="60,16 56,36 64,36" fill="#EF4444"/>
  </svg>`;

  // ---- Go-to-coordinates ----
  function _handleGoTo(text) {
    if (!_map || !text) return;
    // Accept "lat, lon" or "lat lon"
    const parts = text.split(/[,\s]+/).map(Number);
    if (parts.length >= 2 && isFinite(parts[0]) && isFinite(parts[1])) {
      _map.setView([parts[0], parts[1]], _map.getZoom());
    }
  }

  function toggleCompass() {
    _compassVisible = !_compassVisible;
    const btn = document.querySelector('.leaflet-control-compass-toggle');
    if (btn) {
      btn.classList.toggle('active', _compassVisible);
      const a = btn.querySelector('a');
      if (a) a.setAttribute('aria-pressed', _compassVisible);
    }

    if (_compassVisible) {
      _compassEl = document.createElement('div');
      _compassEl.className = 'compass-container';
      _compassEl.innerHTML = _compassSVG;
      _map.getContainer().appendChild(_compassEl);
    } else {
      if (_compassEl) { _compassEl.remove(); _compassEl = null; }
    }
    return _compassVisible;
  }

  // ---- Drone marker ----
  function makeDroneIcon(drone) {
    const state = drone.derived?.state || 'active';
    const color = state === 'active' ? '#F59E0B' : state === 'aging' ? '#78909C' : '#455A64';
    const dir = drone.direction || 0;
    const opacity = state === 'active' ? 1.0 : state === 'aging' ? 0.6 : 0.35;

    // At-a-glance label: operator ID or serial (truncated), plus alt AGL
    const label = drone.operator_id || Utils.shortSerial(drone.serial_number) || '';
    const altText = (drone.drone_height_agl != null && drone.drone_height_agl !== 0)
      ? Utils.formatAlt(drone.drone_height_agl) + ' AGL'
      : '';
    const labelHtml = (label || altText) ? `<div style="
      position:absolute;top:100%;left:50%;transform:translateX(-50%);
      white-space:nowrap;font-size:10px;font-weight:600;line-height:1.2;
      color:#fff;text-shadow:0 0 3px #000,0 0 3px #000;text-align:center;
      pointer-events:none;padding-top:1px;
    ">${label}${label && altText ? '<br>' : ''}${altText}</div>` : '';

    // Quadcopter SVG rotated to heading
    const html = `
      <div style="position:relative;width:36px;height:36px;opacity:${opacity};">
        <div style="transform:rotate(${dir}deg);position:absolute;top:0;left:0;width:36px;height:36px;">
          <svg viewBox="0 0 36 36" width="36" height="36">
            <!-- Arms -->
            <line x1="18" y1="18" x2="7"  y2="7"  stroke="${color}" stroke-width="2.2" stroke-linecap="round"/>
            <line x1="18" y1="18" x2="29" y2="7"  stroke="${color}" stroke-width="2.2" stroke-linecap="round"/>
            <line x1="18" y1="18" x2="7"  y2="29" stroke="${color}" stroke-width="2.2" stroke-linecap="round"/>
            <line x1="18" y1="18" x2="29" y2="29" stroke="${color}" stroke-width="2.2" stroke-linecap="round"/>
            <!-- Rotors -->
            <circle cx="7"  cy="7"  r="5" fill="${color}" opacity="0.25" stroke="${color}" stroke-width="1"/>
            <circle cx="29" cy="7"  r="5" fill="${color}" opacity="0.25" stroke="${color}" stroke-width="1"/>
            <circle cx="7"  cy="29" r="5" fill="${color}" opacity="0.25" stroke="${color}" stroke-width="1"/>
            <circle cx="29" cy="29" r="5" fill="${color}" opacity="0.25" stroke="${color}" stroke-width="1"/>
            <!-- Body -->
            <circle cx="18" cy="18" r="4.5" fill="${color}" stroke="rgba(0,0,0,0.5)" stroke-width="1"/>
            <!-- Heading tick (front) -->
            <line x1="18" y1="13" x2="18" y2="7" stroke="#fff" stroke-width="1.5" stroke-linecap="round" opacity="0.9"/>
          </svg>
        </div>
        ${labelHtml}
      </div>`;

    return L.divIcon({
      className: '',
      html,
      iconSize: [36, 36],
      iconAnchor: [18, 18],
    });
  }

  function makeWifiDroneIcon(drone) {
    const state = drone.derived?.state || 'active';
    const opacity = state === 'active' ? 1.0 : state === 'aging' ? 0.6 : 0.35;
    const label = Utils.shortSerial(drone.mac_address) || '';
    const ssidText = drone.self_id_text ? drone.self_id_text.replace(/\[.*\]$/, '').trim() : '';
    const labelHtml = (label || ssidText) ? `<div style="
      position:absolute;top:100%;left:50%;transform:translateX(-50%);
      white-space:nowrap;font-size:10px;font-weight:600;line-height:1.2;
      color:#fff;text-shadow:0 0 3px #000,0 0 3px #000;text-align:center;
      pointer-events:none;padding-top:1px;
    ">${ssidText || label}</div>` : '';

    const html = `
      <div style="position:relative;width:36px;height:36px;opacity:${opacity};">
        <div style="
          width:36px;height:36px;
          background:#16a34a;
          border-radius:50%;
          border:2px solid rgba(255,255,255,0.8);
          box-shadow:0 0 6px rgba(22,163,74,0.6);
          display:flex;align-items:center;justify-content:center;
        ">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="white" viewBox="0 0 16 16">
            <path d="M15.384 6.115a.485.485 0 0 0-.047-.736A12.44 12.44 0 0 0 8 3C5.259 3 2.723 3.882.663 5.379a.485.485 0 0 0-.048.736.518.518 0 0 0 .668.05A11.45 11.45 0 0 1 8 4c2.507 0 4.827.802 6.716 2.166.205.148.49.13.668-.051z"/>
            <path d="M13.229 8.271a.482.482 0 0 0-.063-.745A9.455 9.455 0 0 0 8 6c-1.905 0-3.68.56-5.166 1.526a.48.48 0 0 0-.063.745.525.525 0 0 0 .652.065A8.46 8.46 0 0 1 8 7a8.46 8.46 0 0 1 4.576 1.336c.206.132.48.108.653-.065z"/>
            <path d="M10.793 11.0a.438.438 0 0 0-.093-.652A6.466 6.466 0 0 0 8 9.5a6.466 6.466 0 0 0-2.7.848.438.438 0 0 0-.092.652.52.52 0 0 0 .65.123A5.47 5.47 0 0 1 8 10.5c.955 0 1.851.25 2.142.473a.52.52 0 0 0 .651-.123z"/>
            <circle cx="8" cy="13.5" r="1"/>
          </svg>
        </div>
        ${labelHtml}
      </div>`;

    return L.divIcon({
      className: '',
      html,
      iconSize: [36, 36],
      iconAnchor: [18, 18],
    });
  }

  function makeOperatorIcon() {
    const html = `<div style="
      display:flex;align-items:center;justify-content:center;
      width:22px;height:22px;
      background:#14B8A6;
      border-radius:50%;
      border:2px solid rgba(0,0,0,0.4);
      box-shadow:0 0 6px #14B8A688;
      color:#fff;font-size:12px;
    "><i class="bi bi-controller"></i></div>`;
    return L.divIcon({ className: '', html, iconSize: [22,22], iconAnchor: [11,11] });
  }

  function buildOperatorPopup(drone) {
    const opLat = drone.operator_lat?.toFixed(5) ?? '—';
    const opLon = drone.operator_lon?.toFixed(5) ?? '—';
    const opId = drone.operator_id || '—';
    const serial = drone.serial_number || drone.mac_address || '?';

    const rows = [
      _popupRow('Drone', serial),
      _popupRow('Operator ID', opId),
      _popupRow('Position', `${opLat}, ${opLon}`),
      _popupRow('Alt', Utils.formatAlt(drone.operator_alt)),
      _popupRow('Pilot dist', _bvlosStr(drone)),
    ].filter(Boolean).join('');

    return `
      <div class="drone-popup">
        <div class="drone-popup-title" style="color:#14B8A6;"><i class="bi bi-controller me-1"></i>Operator</div>
        <div class="drone-popup-grid">${rows}</div>
        <button class="btn-popup-detail" onclick="MapManager._operatorDetailClick('${drone.serial_number}')">
          <i class="bi bi-bezier2 me-1"></i>Show Flight Track
        </button>
      </div>`;
  }

  function _operatorDetailClick(serial) {
    // Select the associated drone — shows track + detail sidebar
    const entry = _droneMarkers[serial];
    if (entry) {
      selectDrone(serial, null);
      if (_onDroneClick) _onDroneClick(serial);
    }
  }

  function _popupRow(label, value) {
    if (!value && value !== 0) return '';
    return `<span class="lbl">${label}</span><span class="val">${value}</span>`;
  }

  function _headingStr(drone) {
    const spd = Utils.formatSpeed(drone.speed);
    const dir = drone.direction != null ? ` HDG ${Math.round(drone.direction)}°` : '';
    return spd + dir;
  }

  function _vspdStr(drone) {
    if (!drone.vertical_speed) return null;
    const arrow = drone.vertical_speed > 0 ? '▲' : '▼';
    // vertical_speed is m/s, formatSpeed handles unit conversion
    return `${arrow} ${Utils.formatSpeed(Math.abs(drone.vertical_speed))}`;
  }

  function _bvlosStr(drone) {
    if (!drone.operator_lat || !drone.operator_lon || !drone.drone_lat || !drone.drone_lon) return null;
    const d = _haversineM(drone.operator_lat, drone.operator_lon, drone.drone_lat, drone.drone_lon);
    const dist = Utils.formatRange(d);
    const bvlos = d > 400 ? ' <span style="color:#EF4444;font-weight:700;">BVLOS</span>' : '';
    return dist + bvlos;
  }

  function _haversineM(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = Math.PI / 180;
    const dLat = (lat2 - lat1) * toRad;
    const dLon = (lon2 - lon1) * toRad;
    const a = Math.sin(dLat/2)**2 + Math.cos(lat1*toRad)*Math.cos(lat2*toRad)*Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  }

  function buildPopupContent(drone) {
    const d = drone.derived || {};

    // Identity block
    const uaType = (drone.ua_type_name && drone.ua_type_name !== 'None / Not Declared') ? drone.ua_type_name : '';
    const typeStr = [drone.vendor, uaType].filter(Boolean).join(' ') || (drone.protocol === 'wifi_ssid' ? 'WiFi SSID Detection' : '—');
    const idRows = [
      _popupRow('Serial', drone.serial_number || '—'),
      _popupRow('Type', typeStr),
      _popupRow('Operator', drone.operator_id),
    ].filter(Boolean).join('');

    // Kinematics block
    const kinRows = [
      _popupRow('Alt AGL', Utils.formatAlt(drone.drone_height_agl)),
      _popupRow('Alt MSL', Utils.formatAlt(drone.drone_alt_geo)),
      _popupRow('Speed', _headingStr(drone)),
      _popupRow('V/S', _vspdStr(drone)),
      _popupRow('From Rx', Utils.formatBearing(d.bearing_deg, d.bearing_cardinal)
        + (d.range_m != null ? ' @ ' + Utils.formatRange(d.range_m) : '')),
    ].filter(Boolean).join('');

    // Operator block
    const opDist = _bvlosStr(drone);
    const opRows = [
      _popupRow('Pilot dist', opDist),
    ].filter(Boolean).join('');

    // Self-ID
    const selfId = drone.self_id_text
      ? `<div style="font-size:11px;color:var(--text-secondary);border-top:1px solid var(--border-color);padding-top:4px;margin-top:4px;">"${drone.self_id_text}"</div>`
      : '';

    // Signal line
    const sigRow = _popupRow('Signal', `${Utils.formatRssi(drone.rssi)} · ${drone.protocol || '?'} · ${Utils.relativeTime(drone.last_seen)}`);

    return `
      <div class="drone-popup">
        <div class="drone-popup-title">${drone.serial_number || drone.mac_address || '?'}</div>
        <div class="drone-popup-grid">${idRows}</div>
        <div class="drone-popup-grid" style="border-top:1px solid var(--border-color);padding-top:3px;margin-top:3px;">${kinRows}</div>
        ${opRows ? `<div class="drone-popup-grid" style="border-top:1px solid var(--border-color);padding-top:3px;margin-top:3px;">${opRows}</div>` : ''}
        ${selfId}
        <div class="drone-popup-grid" style="border-top:1px solid var(--border-color);padding-top:3px;margin-top:3px;opacity:0.7;font-size:10px;">${sigRow}</div>
        <button class="btn-popup-detail" onclick="MapManager._popupDetailClick('${drone.serial_number}')">
          <i class="bi bi-info-circle me-1"></i>Full Details
        </button>
      </div>`;
  }

  // ---- Public: detail button click from popup ----
  function _popupDetailClick(serial) {
    if (_onDroneClick) _onDroneClick(serial);
  }

  // ---- Update all drone markers from live data ----
  function updateDrones(drones, receiver) {
    if (!_map) return;

    // Update receiver
    if (receiver && receiver.lat && receiver.lon) {
      setReceiverPosition(receiver.lat, receiver.lon, receiver.gps_fix, receiver);
    }

    const activeSerialsSet = new Set(drones.map(d => d.serial_number));

    // Remove markers for drones no longer in list
    Object.keys(_droneMarkers).forEach(serial => {
      if (!activeSerialsSet.has(serial)) {
        removeDroneMarker(serial);
      }
    });

    // Add / update markers
    drones.forEach(drone => {
      if (!drone.drone_lat || !drone.drone_lon) return;
      const serial = drone.serial_number;
      const latLng = [drone.drone_lat, drone.drone_lon];

      if (_droneMarkers[serial]) {
        // Update existing
        const entry = _droneMarkers[serial];
        entry.marker.setLatLng(latLng);
        const icon = drone.protocol === 'wifi_ssid' ? makeWifiDroneIcon(drone) : makeDroneIcon(drone);
        entry.marker.setIcon(icon);
        entry.marker.getPopup()?.setContent(buildPopupContent(drone));

        // Operator
        if (drone.operator_lat && drone.operator_lon) {
          const opLatLng = [drone.operator_lat, drone.operator_lon];
          if (entry.operatorMarker) {
            entry.operatorMarker.setLatLng(opLatLng);
            entry.operatorMarker.getPopup()?.setContent(buildOperatorPopup(drone));
          } else {
            entry.operatorMarker = L.marker(opLatLng, { icon: makeOperatorIcon(), zIndexOffset: 100 }).addTo(_map);
            entry.operatorMarker.bindPopup(buildOperatorPopup(drone), { maxWidth: 260, minWidth: 180 });
            entry.operatorMarker.on('click', (e) => {
              L.DomEvent.stopPropagation(e);
              if (_measureActive) { _onMeasureLeftClick(e); return; }
            });
          }
          if (entry.line) {
            entry.line.setLatLngs([latLng, opLatLng]);
          } else {
            entry.line = L.polyline([latLng, opLatLng], {
              color: '#14B8A6', weight: 1.5, dashArray: '5 4', opacity: 0.7
            }).addTo(_map);
          }
        }
      } else {
        // Create new
        const newIcon = drone.protocol === 'wifi_ssid' ? makeWifiDroneIcon(drone) : makeDroneIcon(drone);
        const marker = L.marker(latLng, {
          icon: newIcon,
          zIndexOffset: 200,
          title: serial,
        }).addTo(_map);

        marker.bindPopup(buildPopupContent(drone), { maxWidth: 300, minWidth: 220 });
        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          // In measure mode, treat drone clicks as pin placement
          if (_measureActive) {
            _onMeasureLeftClick(e);
            return;
          }
          selectDrone(serial, drone);
          // Notify app (outside selectDrone to avoid circular callbacks)
          if (_onDroneClick) _onDroneClick(serial);
        });

        let operatorMarker = null;
        let line = null;

        if (drone.operator_lat && drone.operator_lon) {
          const opLatLng = [drone.operator_lat, drone.operator_lon];
          operatorMarker = L.marker(opLatLng, { icon: makeOperatorIcon(), zIndexOffset: 100 }).addTo(_map);
          operatorMarker.bindPopup(buildOperatorPopup(drone), { maxWidth: 260, minWidth: 180 });
          operatorMarker.on('click', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_measureActive) { _onMeasureLeftClick(e); return; }
          });
          line = L.polyline([latLng, opLatLng], {
            color: '#14B8A6', weight: 1.5, dashArray: '5 4', opacity: 0.7
          }).addTo(_map);
        }

        _droneMarkers[serial] = { marker, operatorMarker, line, track: null };
      }
    });

    // Keep selected drone's popup content fresh (setContent updates in-place
    // without reopening a closed popup — don't call openPopup here or it
    // forces the popup open every poll cycle even after the user closes it).
    if (_selectedSerial && !activeSerialsSet.has(_selectedSerial)) {
      // Selected drone disappeared from list — deselect
      _selectedSerial = null;
      if (_onDroneClick) _onDroneClick(null);
    }
  }

  function removeDroneMarker(serial) {
    const entry = _droneMarkers[serial];
    if (!entry) return;
    entry.marker.remove();
    if (entry.operatorMarker) entry.operatorMarker.remove();
    if (entry.line) entry.line.remove();
    if (entry.track) entry.track.remove();
    delete _droneMarkers[serial];
  }

  // ---- Selection & track ----
  /** Visual-only: pan to drone, open popup. Does NOT fire _onDroneClick
   *  to avoid a circular loop between map.js ↔ app.js ↔ table.js. */
  function selectDrone(serial, drone) {
    _selectedSerial = serial;

    // Center map on drone
    if (drone && drone.drone_lat && drone.drone_lon) {
      _map.panTo([drone.drone_lat, drone.drone_lon], { animate: true });
    }

    // Open popup
    const entry = _droneMarkers[serial];
    if (entry) {
      entry.marker.openPopup();
    }
  }

  function showTrack(trackPoints) {
    clearTrack();
    if (!trackPoints || trackPoints.length < 2) return;

    const latlngs = trackPoints
      .filter(p => p.drone_lat && p.drone_lon)
      .map(p => [p.drone_lat, p.drone_lon]);

    if (latlngs.length < 2) return;

    _trackPolyline = L.polyline(latlngs, {
      color: '#F59E0B',
      weight: 2,
      opacity: 0.8,
      dashArray: null,
    }).addTo(_map);
  }

  function clearTrack() {
    if (_trackPolyline) {
      _trackPolyline.remove();
      _trackPolyline = null;
    }
  }

  // ---- All-tracks toggle ----
  let _onAllTracksToggle = null;  // callback(visible) set by app.js

  function toggleAllTracks() {
    _allTracksVisible = !_allTracksVisible;
    const btn = document.querySelector('.leaflet-control-tracks-toggle');
    if (btn) {
      btn.classList.toggle('active', _allTracksVisible);
      const a = btn.querySelector('a');
      if (a) a.setAttribute('aria-pressed', _allTracksVisible);
    }
    if (_allTracksVisible) {
      if (_onAllTracksToggle) _onAllTracksToggle(true);
    } else {
      clearAllTracks();
      if (_onAllTracksToggle) _onAllTracksToggle(false);
    }
    return _allTracksVisible;
  }

  function showAllTracks(tracksBySerial) {
    if (!_map) return;
    // Remove polylines for serials no longer present
    Object.keys(_allTrackPolylines).forEach(serial => {
      if (!tracksBySerial[serial]) {
        _allTrackPolylines[serial].remove();
        delete _allTrackPolylines[serial];
      }
    });
    // Add/update polylines per serial
    Object.entries(tracksBySerial).forEach(([serial, trackPoints]) => {
      const latlngs = trackPoints
        .filter(p => p.drone_lat && p.drone_lon)
        .map(p => [p.drone_lat, p.drone_lon]);
      if (latlngs.length < 2) {
        if (_allTrackPolylines[serial]) {
          _allTrackPolylines[serial].remove();
          delete _allTrackPolylines[serial];
        }
        return;
      }
      if (_allTrackPolylines[serial]) {
        _allTrackPolylines[serial].setLatLngs(latlngs);
      } else {
        _allTrackPolylines[serial] = L.polyline(latlngs, {
          color: '#F59E0B',
          weight: 2,
          opacity: 0.7,
          dashArray: null,
        }).addTo(_map);
      }
    });
  }

  function clearAllTracks() {
    Object.values(_allTrackPolylines).forEach(p => p.remove());
    _allTrackPolylines = {};
  }

  // ---- Replay mode: render snapshot ----
  // Fix #10: filter records to only those at or before currentTimeMs for tracks
  function renderReplaySnapshot(records, receiverLat, receiverLon, currentTimeMs) {
    if (!_map) return;

    // Exit measure mode if active — replay is a different operational context
    if (_measureActive) toggleMeasure();

    // Clear all existing drone markers
    Object.keys(_droneMarkers).forEach(s => removeDroneMarker(s));

    // If currentTimeMs provided, only consider records up to that time
    const relevantRecords = (currentTimeMs != null)
      ? records.filter(r => new Date(r.timestamp).getTime() <= currentTimeMs)
      : records;

    // Group by serial, take most recent per serial
    const bySerial = {};
    relevantRecords.forEach(rec => {
      if (!bySerial[rec.serial_number] || rec.timestamp > bySerial[rec.serial_number].timestamp) {
        bySerial[rec.serial_number] = rec;
      }
    });

    // Show receiver
    if (receiverLat && receiverLon) {
      setReceiverPosition(receiverLat, receiverLon, true);
    }

    // Draw markers
    Object.values(bySerial).forEach(rec => {
      if (!rec.drone_lat || !rec.drone_lon) return;
      const fakeDrone = {
        serial_number: rec.serial_number,
        ua_type_name: '',
        drone_lat: rec.drone_lat,
        drone_lon: rec.drone_lon,
        drone_height_agl: rec.drone_height_agl,
        speed: rec.speed,
        direction: rec.direction,
        operator_lat: rec.operator_lat,
        operator_lon: rec.operator_lon,
        rssi: rec.rssi,
        last_seen: rec.timestamp,
        derived: { state: 'active' },
      };

      const latLng = [rec.drone_lat, rec.drone_lon];
      const marker = L.marker(latLng, { icon: makeDroneIcon(fakeDrone), zIndexOffset: 200 }).addTo(_map);
      let operatorMarker = null;
      let line = null;

      if (rec.operator_lat && rec.operator_lon) {
        const opLatLng = [rec.operator_lat, rec.operator_lon];
        operatorMarker = L.marker(opLatLng, { icon: makeOperatorIcon(), zIndexOffset: 100 }).addTo(_map);
        operatorMarker.bindPopup(buildOperatorPopup(fakeDrone), { maxWidth: 260, minWidth: 180 });
        line = L.polyline([latLng, opLatLng], { color: '#14B8A6', weight: 1.5, dashArray: '5 4', opacity: 0.7 }).addTo(_map);
      }

      _droneMarkers[rec.serial_number] = { marker, operatorMarker, line, track: null };
    });

    // Draw tracks per serial — only up to currentTimeMs
    const tracksBySerial = {};
    relevantRecords.forEach(rec => {
      if (!tracksBySerial[rec.serial_number]) tracksBySerial[rec.serial_number] = [];
      if (rec.drone_lat && rec.drone_lon) {
        tracksBySerial[rec.serial_number].push([rec.drone_lat, rec.drone_lon]);
      }
    });

    Object.entries(tracksBySerial).forEach(([serial, latlngs]) => {
      if (latlngs.length < 2) return;
      const poly = L.polyline(latlngs, { color: '#F59E0B', weight: 1.5, opacity: 0.6, dashArray: null }).addTo(_map);
      if (_droneMarkers[serial]) _droneMarkers[serial].track = poly;
    });
  }

  // ---- Clear all ----
  function clearAll() {
    Object.keys(_droneMarkers).forEach(s => removeDroneMarker(s));
    clearTrack();
    clearAllTracks();
  }

  // ---- Fit bounds to drones ----
  function fitToDrones() {
    const latlngs = Object.values(_droneMarkers).map(e => e.marker.getLatLng());
    if (latlngs.length === 0) return;
    const bounds = L.latLngBounds(latlngs);
    _map.fitBounds(bounds.pad(0.3));
  }

  // Fix #24: invalidate map size after sidebar opens so Leaflet recalculates viewport
  function setTheme(theme) {
    if (!_map) return;
    _currentTheme = theme;
    const newOsm = theme === 'dark' ? _osmDarkLayer : _osmLightLayer;
    if (newOsm === _osmLayer) return;

    // Swap OSM layers on the map (if OSM is currently showing)
    const osmWasActive = _map.hasLayer(_osmLayer);
    if (osmWasActive) {
      _map.removeLayer(_osmLayer);
    }
    _osmLayer = newOsm;
    if (osmWasActive) {
      _osmLayer.addTo(_map);
    }

    // Rebuild layer control so the correct OSM variant is wired up.
    // Leaflet needs the currently-visible base layer added first so its
    // radio button is checked.
    if (_layerControl) _map.removeControl(_layerControl);
    const bases = osmWasActive
      ? { 'Map': _osmLayer, 'Satellite': _satelliteLayer }
      : { 'Satellite': _satelliteLayer, 'Map': _osmLayer };
    _layerControl = L.control.layers(
      bases, {},
      { position: 'topright', collapsed: true }
    ).addTo(_map);

    // Refresh range ring colors for the new theme
    _refreshRangeRings();
  }

  function invalidateSizeDelayed(ms) {
    setTimeout(() => {
      if (_map) _map.invalidateSize();
    }, ms || 100);
  }

  return {
    init,
    setReceiverPosition,
    toggleRangeRings,
    toggleMeasure,
    toggleCompass,
    refreshMeasureUnits,
    updateDrones,
    selectDrone,
    showTrack,
    clearTrack,
    toggleAllTracks,
    showAllTracks,
    clearAllTracks,
    get allTracksVisible() { return _allTracksVisible; },
    set onAllTracksToggle(fn) { _onAllTracksToggle = fn; },
    clearAll,
    fitToDrones,
    renderReplaySnapshot,
    setTheme,
    invalidateSizeDelayed,
    _popupDetailClick,
    _operatorDetailClick,
    getMap: () => _map,
  };
})();
