/* ============================================================
   geozones.js — Leaflet layer manager for airport zones and
   FAA no-fly zones (restricted / prohibited airspace).
   ============================================================ */

const GeozoneManager = (() => {

  let _map            = null;
  let _airportLayer   = null;    // L.layerGroup — airport radius circles
  let _noflyLayer     = null;    // L.layerGroup — no-fly polygons (GeoJSON)
  let _visible        = true;
  let _airportRadiusMi = 2;
  let _lastReceiverLat = null;
  let _lastReceiverLon = null;
  let _loaded          = false;
  let _cachedAirports  = null;   // last airport array, kept for radius re-renders

  const LS_KEY = 'sparrow_geozones_visible';

  // ---- Airport type config ----
  const AIRPORT_STYLES = {
    large_airport:  { color: '#EF4444', fillOpacity: 0.08 },
    medium_airport: { color: '#F97316', fillOpacity: 0.06 },
    small_airport:  { color: '#EAB308', fillOpacity: 0.05 },
  };

  function _typeLabel(type) {
    if (type === 'large_airport')  return 'Large Airport';
    if (type === 'medium_airport') return 'Medium Airport';
    if (type === 'small_airport')  return 'Small Airport';
    return type || 'Airport';
  }

  // ---- Init ----
  function init(map) {
    _map = map;

    _airportLayer = L.layerGroup().addTo(_map);
    _noflyLayer   = L.layerGroup().addTo(_map);

    const stored = localStorage.getItem(LS_KEY);
    _visible = stored === null ? true : stored === 'true';

    if (!_visible) {
      _airportLayer.remove();
      _noflyLayer.remove();
    }
  }

  // ---- Load data from API ----
  async function loadData(receiverLat, receiverLon) {
    if (!_map) return;

    // Skip re-fetch if receiver hasn't moved significantly and data already loaded
    if (_loaded &&
        _lastReceiverLat !== null &&
        Math.abs(receiverLat - _lastReceiverLat) < 0.01 &&
        Math.abs(receiverLon - _lastReceiverLon) < 0.01) {
      return;
    }

    try {
      const [airports, nofly] = await Promise.all([
        Api.getGeozoneAirports(receiverLat, receiverLon),
        Api.getGeozoneNofly(receiverLat, receiverLon),
      ]);

      _cachedAirports = airports;
      _renderAirports(airports);
      _renderNofly(nofly);

      _lastReceiverLat = receiverLat;
      _lastReceiverLon = receiverLon;
      _loaded = true;
    } catch (_err) {
      // Non-fatal — geozones are informational only
    }
  }

  // ---- Render airport circles ----
  function _renderAirports(airports) {
    _airportLayer.clearLayers();
    if (!airports || !airports.length) return;

    const radiusM = _airportRadiusMi * 1609.34;

    airports.forEach(ap => {
      if (!ap.lat || !ap.lon) return;

      const style = AIRPORT_STYLES[ap.type] || { color: '#94A3B8', fillOpacity: 0.05 };

      const circle = L.circle([ap.lat, ap.lon], {
        radius:       radiusM,
        color:        style.color,
        weight:       1.5,
        dashArray:    '6 4',
        opacity:      0.6,
        fillColor:    style.color,
        fillOpacity:  style.fillOpacity,
        interactive:  false,
      });

      const tooltipContent =
        `<b>${ap.ident || ap.icao || ''}</b><br>` +
        `${ap.name || ''}<br>` +
        `<span style="font-size:11px">${_typeLabel(ap.type)}</span>`;

      circle.bindTooltip(tooltipContent, { sticky: true });

      _airportLayer.addLayer(circle);
    });
  }

  // ---- Render no-fly / restricted polygons ----
  function _renderNofly(geojson) {
    _noflyLayer.clearLayers();
    if (!geojson) return;

    const layer = L.geoJSON(geojson, {
      style: feature => {
        const code = feature.properties && feature.properties.TYPE_CODE;
        if (code === 'P') {
          // Prohibited
          return {
            color:       '#EF4444',
            fillColor:   '#EF4444',
            fillOpacity: 0.12,
            weight:      1.5,
            dashArray:   '4 3',
            interactive: false,
          };
        }
        // Restricted (R) and any other type
        return {
          color:       '#F97316',
          fillColor:   '#F97316',
          fillOpacity: 0.10,
          weight:      1.5,
          dashArray:   '4 3',
          interactive: false,
        };
      },
      onEachFeature: (feature, lyr) => {
        const props = feature.properties || {};
        const code  = props.TYPE_CODE === 'P' ? 'Prohibited' : 'Restricted';
        const name  = props.NAME || props.name || '';
        lyr.bindTooltip(`${name} \u2014 ${code}`, { sticky: true });
      },
    });

    _noflyLayer.addLayer(layer);
  }

  // ---- Toggle visibility ----
  function toggle() {
    _visible = !_visible;
    localStorage.setItem(LS_KEY, String(_visible));

    if (_visible) {
      _airportLayer.addTo(_map);
      _noflyLayer.addTo(_map);
    } else {
      _airportLayer.remove();
      _noflyLayer.remove();
    }

    return _visible;
  }

  // ---- Update airport radius and re-render if data is loaded ----
  function setAirportRadius(radiusMi) {
    _airportRadiusMi = radiusMi;
    if (_loaded && _cachedAirports) {
      _renderAirports(_cachedAirports);
    }
  }

  return {
    init,
    loadData,
    toggle,
    setAirportRadius,
  };

})();
