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
  let _osmLayer = null;
  let _satelliteLayer = null;
  let _layerControl = null;

  // Default center (Washington DC area) — will update from receiver position
  const DEFAULT_CENTER = [38.8977, -77.0365];
  const DEFAULT_ZOOM   = 14;
  let _hasAutocentered = false;  // only auto-center once on first receiver position

  // ---- Tile URL builder (proxied through backend) ----
  function tileUrl(source) {
    return `/api/tiles/${source}/{z}/{x}/{y}.png`;
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

    // Tile layers
    _osmLayer = L.tileLayer(tileUrl('osm'), {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(_map);

    _satelliteLayer = L.tileLayer(tileUrl('esri_satellite'), {
      attribution: '&copy; Esri &mdash; Source: Esri, USGS',
      maxZoom: 19,
    });

    // Layer control
    _layerControl = L.control.layers(
      { 'OSM': _osmLayer, 'Satellite': _satelliteLayer },
      {},
      { position: 'topright', collapsed: true }
    ).addTo(_map);

    // Click on map deselects
    _map.on('click', () => {
      if (_selectedSerial) {
        clearTrack();
        _selectedSerial = null;
        if (_onDroneClick) _onDroneClick(null);
      }
    });

    return _map;
  }

  // ---- Receiver position ----
  function setReceiverPosition(lat, lon, gpsFix) {
    if (!_map) return;
    if (!lat || !lon) return;

    const latLng = [lat, lon];

    if (_receiverMarker) {
      _receiverMarker.setLatLng(latLng);
    } else {
      const icon = L.divIcon({
        className: '',
        html: `<div style="
          width:16px;height:16px;
          background:${gpsFix ? '#BFDBFE' : '#94A3B8'};
          border:2px solid #fff;
          border-radius:50%;
          box-shadow:0 0 0 3px rgba(191,219,254,0.35);
        "></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      _receiverMarker = L.marker(latLng, { icon, zIndexOffset: 500, title: 'Receiver' }).addTo(_map);
      _receiverMarker.bindPopup('<b>Receiver</b><br>GPS fix: ' + (gpsFix ? 'Yes' : 'No'));
    }

    // Auto-center map on first valid receiver position (GPS fix or static coords)
    if (!_hasAutocentered) {
      _map.setView(latLng, DEFAULT_ZOOM);
      _hasAutocentered = true;
    }

    updateRangeRings(lat, lon);
  }

  // ---- Range rings ----
  function updateRangeRings(lat, lon) {
    _rangeRings.forEach(r => _map.removeLayer(r));
    _rangeRings = [];

    if (!_rangeRingsVisible || !lat || !lon) return;

    const radii = [250, 500, 1000]; // meters
    const colors = ['rgba(191,219,254,0.5)', 'rgba(191,219,254,0.35)', 'rgba(191,219,254,0.2)'];
    const labels = ['250 m', '500 m', '1 km'];

    radii.forEach((r, i) => {
      const circle = L.circle([lat, lon], {
        radius: r,
        color: colors[i],
        weight: 1,
        dashArray: '4 4',
        fillOpacity: 0.03,
        interactive: false,
      }).addTo(_map);
      _rangeRings.push(circle);

      // Label
      const labelIcon = L.divIcon({
        className: '',
        html: `<span style="font-size:10px;color:rgba(191,219,254,0.6);text-shadow:0 0 3px #000;">${labels[i]}</span>`,
        iconAnchor: [0, 0],
      });
      const bearing = 45; // NE label position
      const labelLatLng = destinationPoint(lat, lon, r, bearing);
      const labelMarker = L.marker(labelLatLng, { icon: labelIcon, interactive: false }).addTo(_map);
      _rangeRings.push(labelMarker);
    });
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

  // ---- Drone marker ----
  function makeDroneIcon(drone) {
    const state = drone.derived?.state || 'active';
    const color = state === 'active' ? '#F59E0B' : state === 'aging' ? '#78909C' : '#455A64';
    const dir = drone.direction || 0;
    const size = 28;

    // Arrow pointing in heading direction
    const arrowHtml = `
      <div style="transform:rotate(${dir}deg);position:absolute;top:0;left:0;width:${size}px;height:${size}px;display:flex;align-items:center;justify-content:center;">
        <svg width="${size}" height="${size}" viewBox="0 0 28 28">
          <polygon points="14,3 18,20 14,17 10,20" fill="${color}" opacity="0.85"/>
        </svg>
      </div>`;

    const html = `
      <div style="position:relative;width:${size}px;height:${size}px;">
        <div style="
          position:absolute;top:50%;left:50%;
          transform:translate(-50%,-50%);
          width:18px;height:18px;
          background:${color};
          border-radius:50%;
          border:2px solid rgba(0,0,0,0.4);
          box-shadow:0 0 6px ${color}88;
        "></div>
        ${arrowHtml}
      </div>`;

    return L.divIcon({
      className: '',
      html,
      iconSize: [size, size],
      iconAnchor: [size/2, size/2],
    });
  }

  function makeOperatorIcon() {
    const html = `<div style="
      width:14px;height:14px;
      background:#14B8A6;
      border-radius:50%;
      border:2px solid rgba(0,0,0,0.4);
      box-shadow:0 0 4px #14B8A688;
    "></div>`;
    return L.divIcon({ className: '', html, iconSize: [14,14], iconAnchor: [7,7] });
  }

  // Fix #14: replace hardcoded hex colors with CSS custom properties
  function buildPopupContent(drone) {
    const d = drone.derived || {};
    return `
      <div class="drone-popup">
        <div class="drone-popup-title">${Utils.shortSerial(drone.serial_number)}</div>
        <div class="drone-popup-grid">
          <span class="lbl">Type</span><span class="val">${drone.ua_type_name || '—'}</span>
          <span class="lbl">Alt AGL</span><span class="val">${Utils.formatAlt(drone.drone_height_agl)}</span>
          <span class="lbl">Speed</span><span class="val">${Utils.formatSpeed(drone.speed)}</span>
          <span class="lbl">Bearing</span><span class="val">${Utils.formatBearing(d.bearing_deg, d.bearing_cardinal)}</span>
          <span class="lbl">Range</span><span class="val">${Utils.formatRange(d.range_m)}</span>
          <span class="lbl">RSSI</span><span class="val">${Utils.formatRssi(drone.rssi)}</span>
          <span class="lbl">Last seen</span><span class="val">${Utils.relativeTime(drone.last_seen)}</span>
        </div>
        ${drone.self_id_text ? `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">ID: ${drone.self_id_text}</div>` : ''}
        <button class="btn-popup-detail" onclick="MapManager._popupDetailClick('${drone.serial_number}')">
          <i class="bi bi-info-circle me-1"></i>Details
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
      setReceiverPosition(receiver.lat, receiver.lon, receiver.gps_fix);
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
        entry.marker.setIcon(makeDroneIcon(drone));
        entry.marker.getPopup()?.setContent(buildPopupContent(drone));

        // Operator
        if (drone.operator_lat && drone.operator_lon) {
          const opLatLng = [drone.operator_lat, drone.operator_lon];
          if (entry.operatorMarker) {
            entry.operatorMarker.setLatLng(opLatLng);
          } else {
            entry.operatorMarker = L.marker(opLatLng, { icon: makeOperatorIcon(), zIndexOffset: 100 }).addTo(_map);
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
        const marker = L.marker(latLng, {
          icon: makeDroneIcon(drone),
          zIndexOffset: 200,
          title: serial,
        }).addTo(_map);

        marker.bindPopup(buildPopupContent(drone), { maxWidth: 260, minWidth: 200 });
        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          selectDrone(serial, drone);
        });

        let operatorMarker = null;
        let line = null;

        if (drone.operator_lat && drone.operator_lon) {
          const opLatLng = [drone.operator_lat, drone.operator_lon];
          operatorMarker = L.marker(opLatLng, { icon: makeOperatorIcon(), zIndexOffset: 100 }).addTo(_map);
          line = L.polyline([latLng, opLatLng], {
            color: '#14B8A6', weight: 1.5, dashArray: '5 4', opacity: 0.7
          }).addTo(_map);
        }

        _droneMarkers[serial] = { marker, operatorMarker, line, track: null };
      }
    });

    // Refresh selected drone track if still active
    if (_selectedSerial && activeSerialsSet.has(_selectedSerial)) {
      const drone = drones.find(d => d.serial_number === _selectedSerial);
      if (drone) {
        _droneMarkers[_selectedSerial]?.marker.openPopup();
      }
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
  function selectDrone(serial, drone) {
    _selectedSerial = serial;
    if (_onDroneClick) _onDroneClick(serial);

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

  // ---- Replay mode: render snapshot ----
  // Fix #10: filter records to only those at or before currentTimeMs for tracks
  function renderReplaySnapshot(records, receiverLat, receiverLon, currentTimeMs) {
    if (!_map) return;

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
  }

  // ---- Fit bounds to drones ----
  function fitToDrones() {
    const latlngs = Object.values(_droneMarkers).map(e => e.marker.getLatLng());
    if (latlngs.length === 0) return;
    const bounds = L.latLngBounds(latlngs);
    _map.fitBounds(bounds.pad(0.3));
  }

  // Fix #24: invalidate map size after sidebar opens so Leaflet recalculates viewport
  function invalidateSizeDelayed(ms) {
    setTimeout(() => {
      if (_map) _map.invalidateSize();
    }, ms || 100);
  }

  return {
    init,
    setReceiverPosition,
    toggleRangeRings,
    updateDrones,
    selectDrone,
    showTrack,
    clearTrack,
    clearAll,
    fitToDrones,
    renderReplaySnapshot,
    invalidateSizeDelayed,
    _popupDetailClick,
    getMap: () => _map,
  };
})();
