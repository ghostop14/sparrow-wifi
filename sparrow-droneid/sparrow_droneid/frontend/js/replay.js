/* ============================================================
   replay.js — Time range picker, playback controls, history
   ============================================================ */

const ReplayManager = (() => {

  let _records = [];        // flat array of all history records for range
  let _serials = [];        // summary list from /api/history/serials
  let _buckets = [];        // timeline buckets from /api/history/timeline
  let _playTimer = null;
  let _playing = false;
  let _speed = 1;
  let _sliderPos = 0;       // 0..100
  let _rangeFromMs = 0;
  let _rangeToMs = 0;
  let _currentTimeMs = 0;
  let _onTimeUpdate = null; // callback(records_at_time, timeMs)
  let _isLoaded = false;
  // Fix #11: track which serial is highlighted (null = show all)
  let _filteredSerial = null;

  const REPLAY_WINDOW_MS = 30000; // show records within last 30s of slider pos

  // ---- Init ----
  function init(onTimeUpdateCb) {
    _onTimeUpdate = onTimeUpdateCb;

    // Set default time range: last 1 hour
    const now = new Date();
    const oneHourAgo = new Date(now.getTime() - 3600000);
    const toEl = document.getElementById('replayTo');
    const fromEl = document.getElementById('replayFrom');
    if (toEl)   toEl.value   = Utils.toLocalDatetimeInput(now.toISOString());
    if (fromEl) fromEl.value = Utils.toLocalDatetimeInput(oneHourAgo.toISOString());

    document.getElementById('btnReplayLoad')?.addEventListener('click', loadRange);
    document.getElementById('btnReplayPlay')?.addEventListener('click', togglePlay);
    document.getElementById('btnReplaySkipBack')?.addEventListener('click', () => skipTo(0));
    document.getElementById('btnReplaySkipFwd')?.addEventListener('click', () => skipTo(100));
    document.getElementById('btnExportKml')?.addEventListener('click', exportKml);

    document.getElementById('replaySlider')?.addEventListener('input', e => {
      const pos = parseInt(e.target.value);
      _seekToPos(pos);
    });

    // Speed buttons
    document.querySelectorAll('.replay-speed').forEach(btn => {
      btn.addEventListener('click', () => {
        _speed = parseInt(btn.dataset.speed);
        document.querySelectorAll('.replay-speed').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });
  }

  // ---- Load range ----
  async function loadRange() {
    const fromVal = document.getElementById('replayFrom')?.value;
    const toVal   = document.getElementById('replayTo')?.value;
    if (!fromVal || !toVal) {
      Utils.toast('Please select a start and end time.', 'alert');
      return;
    }

    const fromIso = Utils.fromLocalDatetimeInput(fromVal);
    const toIso   = Utils.fromLocalDatetimeInput(toVal);
    if (!fromIso || !toIso) return;

    _rangeFromMs = new Date(fromIso).getTime();
    _rangeToMs   = new Date(toIso).getTime();

    if (_rangeToMs <= _rangeFromMs) {
      Utils.toast('End time must be after start time.', 'alert');
      return;
    }

    const btn = document.getElementById('btnReplayLoad');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>Loading...'; }

    try {
      // Load serials summary, all records, and timeline buckets in parallel
      const [serialsResp, histResp, timelineResp] = await Promise.all([
        Api.getHistorySerials(fromIso, toIso),
        Api.getHistory(fromIso, toIso),
        Api.getHistoryTimeline(fromIso, toIso, 60).catch(() => null),
      ]);

      _serials = serialsResp.serials || [];
      _records = (histResp.records || []).sort((a, b) =>
        new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      );

      // Fix #20: warn if results were truncated
      const returnedCount = _records.length;
      const pg = histResp.pagination || {};
      const totalCount = pg.total_count ?? histResp.total_count ?? returnedCount;
      if (totalCount > returnedCount) {
        Utils.toast(
          `Showing ${returnedCount.toLocaleString()} of ${totalCount.toLocaleString()} records. Narrow the time range for complete data.`,
          'warning',
          'Truncated Results',
          6000
        );
      }

      // Fix #19: store timeline buckets for activity bar
      _buckets = (timelineResp && timelineResp.buckets) ? timelineResp.buckets : [];

      _filteredSerial = null;
      _isLoaded = true;
      _sliderPos = 0;
      _currentTimeMs = _rangeFromMs;

      _renderSerials();
      _renderActivityBar();
      _updateSliderLabels();
      _enableControls(true);
      _seekToPos(0);

      Utils.toast(`Loaded ${_records.length} records for ${_serials.length} drone(s).`, 'success');
    } catch (e) {
      Utils.toast('Failed to load replay data: ' + e.message, 'alert');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-download me-1"></i>Load'; }
    }
  }

  // ---- Serial tags ----
  function _renderSerials() {
    const container = document.getElementById('replaySerials');
    if (!container) return;

    if (_serials.length === 0) {
      container.innerHTML = '<span class="text-secondary small">No drones in this time range.</span>';
      return;
    }

    // Fix #11: tags are clickable to filter/highlight a specific serial
    const html = _serials.map(s => {
      const isActive = _filteredSerial === s.serial_number;
      return `
        <span class="replay-serial-tag ${isActive ? 'active' : ''}"
              data-serial="${s.serial_number}"
              title="${s.ua_type_name} | ${s.detection_count} records | RSSI peak ${s.max_rssi} dBm&#10;Click to filter to this drone">
          <i class="bi bi-aircraft-horizontal"></i>
          ${Utils.shortSerial(s.serial_number)}
          <span style="opacity:0.6;font-size:10px;">${s.detection_count}</span>
        </span>`;
    }).join('');

    container.innerHTML = html;

    // Attach click handlers to serial tags
    container.querySelectorAll('.replay-serial-tag').forEach(tag => {
      tag.addEventListener('click', () => {
        const serial = tag.dataset.serial;
        if (_filteredSerial === serial) {
          // Second click deselects (show all)
          _filteredSerial = null;
        } else {
          _filteredSerial = serial;
        }
        // Re-render tags to update active state
        _renderSerials();
        // Re-render detection bands to reflect the new serial filter
        _renderDetectionBands();
        // Re-seek to refresh the map with the filter applied
        _seekToPos(_sliderPos);
      });
    });
  }

  // ---- Detection bands: precise slider-aligned overlay derived from _records ----
  // Replaces the old _renderActivityBar which used the timeline endpoint with
  // mismatched field names (b.count/b.time_bucket vs API's active_drones/timestamp).

  function _renderActivityBar() {
    // Remove old activity bar if it exists (field-name mismatch means it was broken)
    const oldBar = document.getElementById('replayActivityBar');
    if (oldBar) oldBar.remove();

    _renderDetectionBands();
  }

  function _renderDetectionBands() {
    const sliderWrap = document.querySelector('.replay-slider-wrap');
    if (!sliderWrap) return;

    // Remove any previous overlay
    let overlay = document.getElementById('replayDetectionBands');
    if (overlay) overlay.remove();

    if (_records.length === 0 || _rangeToMs <= _rangeFromMs) return;

    const span = _rangeToMs - _rangeFromMs;
    // Bin width: coarser of REPLAY_WINDOW_MS or span/300 (aim for ≤300 bins)
    const binMs = Math.max(REPLAY_WINDOW_MS, span / 300);

    // Determine which records to bin (apply serial filter if active)
    const source = _filteredSerial
      ? _records.filter(r => r.serial_number === _filteredSerial)
      : _records;

    // Count records per bin
    const binCounts = {};
    source.forEach(r => {
      const t = new Date(r.timestamp).getTime();
      if (t < _rangeFromMs || t > _rangeToMs) return;
      const binIdx = Math.floor((t - _rangeFromMs) / binMs);
      binCounts[binIdx] = (binCounts[binIdx] || 0) + 1;
    });

    if (Object.keys(binCounts).length === 0) return;

    // Slider thumb radius ≈ 8px — inset left/right so band percentages align
    // with the thumb's travel range rather than the element's full width.
    overlay = document.createElement('div');
    overlay.id = 'replayDetectionBands';
    overlay.className = 'replay-detection-bands';

    Object.entries(binCounts).forEach(([idxStr, count]) => {
      const idx = parseInt(idxStr, 10);
      const binStart = _rangeFromMs + idx * binMs;
      const binEnd   = Math.min(binStart + binMs, _rangeToMs);
      const leftPct  = ((binStart - _rangeFromMs) / span) * 100;
      const widthPct = ((binEnd - binStart) / span) * 100;
      const binLabel = Utils.formatDateTime(new Date(binStart).toISOString());

      const band = document.createElement('div');
      band.className = 'replay-detection-band';
      band.style.left  = `${leftPct}%`;
      band.style.width = `${widthPct}%`;
      band.title = `${binLabel}: ${count} record${count !== 1 ? 's' : ''}`;

      band.addEventListener('click', e => {
        const rect = sliderWrap.getBoundingClientRect();
        const pos  = Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100));
        skipTo(pos);
      });

      overlay.appendChild(band);
    });

    // Insert before slider's firstChild so it sits above the track visually
    sliderWrap.insertBefore(overlay, sliderWrap.firstChild);
  }

  // ---- Controls enable/disable ----
  function _enableControls(on) {
    const ids = ['btnReplayPlay', 'btnReplaySkipBack', 'btnReplaySkipFwd', 'replaySlider'];
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = !on;
    });
  }

  // ---- Slider labels ----
  function _updateSliderLabels() {
    const start = document.getElementById('replaySliderStart');
    const end   = document.getElementById('replaySliderEnd');
    if (start) start.textContent = Utils.formatDateTime(new Date(_rangeFromMs).toISOString());
    if (end)   end.textContent   = Utils.formatDateTime(new Date(_rangeToMs).toISOString());
  }

  // ---- Seek ----
  function _seekToPos(pos) {
    _sliderPos = pos;
    const slider = document.getElementById('replaySlider');
    if (slider) slider.value = pos;

    if (!_isLoaded) return;

    const span = _rangeToMs - _rangeFromMs;
    _currentTimeMs = _rangeFromMs + (span * pos / 100);

    // Filter records up to current time, within window
    const windowStart = _currentTimeMs - REPLAY_WINDOW_MS;
    let visible = _records.filter(r => {
      const t = new Date(r.timestamp).getTime();
      return t <= _currentTimeMs && t >= windowStart;
    });

    // Fix #11: apply serial filter if active
    if (_filteredSerial) {
      visible = visible.filter(r => r.serial_number === _filteredSerial);
    }

    _updateCurrentTimeLabel();
    if (_onTimeUpdate) _onTimeUpdate(visible, _currentTimeMs);
  }

  function _updateCurrentTimeLabel() {
    const el = document.getElementById('replayCurrentTime');
    if (el) el.textContent = Utils.formatDateTime(new Date(_currentTimeMs).toISOString());
  }

  // ---- Play / Pause ----
  function togglePlay() {
    if (!_isLoaded) return;
    _playing = !_playing;

    const icon = document.getElementById('replayPlayIcon');
    if (icon) {
      icon.className = _playing ? 'bi bi-pause-fill' : 'bi bi-play-fill';
    }

    if (_playing) {
      _tick();
    } else {
      if (_playTimer) { clearTimeout(_playTimer); _playTimer = null; }
    }

    // Notify app.js to pause/resume live polling
    document.dispatchEvent(new CustomEvent('replayPlayStateChanged', { detail: { playing: _playing } }));
  }

  function _tick() {
    if (!_playing) return;

    // Advance slider by one step (~100ms of real time = speed * 100ms of recorded time)
    const rangeMs = _rangeToMs - _rangeFromMs;
    if (rangeMs <= 0) { _playing = false; return; }

    // Each tick advances (speed * tick_interval_ms) of replay time
    const TICK_MS = 200; // real-time tick interval
    const advanceMs = _speed * TICK_MS;
    const advancePct = (advanceMs / rangeMs) * 100;

    _sliderPos = Math.min(100, _sliderPos + advancePct);
    _seekToPos(_sliderPos);

    if (_sliderPos >= 100) {
      _playing = false;
      const icon = document.getElementById('replayPlayIcon');
      if (icon) icon.className = 'bi bi-play-fill';
      return;
    }

    _playTimer = setTimeout(_tick, TICK_MS);
  }

  function skipTo(pos) {
    if (_playing) togglePlay();
    _seekToPos(pos);
  }

  // ---- KML Export ----
  function exportKml() {
    const fromVal = document.getElementById('replayFrom')?.value;
    const toVal   = document.getElementById('replayTo')?.value;
    if (!fromVal || !toVal) {
      Utils.toast('Select a time range first.', 'alert');
      return;
    }
    const fromIso = Utils.fromLocalDatetimeInput(fromVal);
    const toIso   = Utils.fromLocalDatetimeInput(toVal);
    if (!fromIso || !toIso) return;

    const url = Api.exportKml(fromIso, toIso);
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // ---- Stop and reset ----
  function stop() {
    _playing = false;
    if (_playTimer) { clearTimeout(_playTimer); _playTimer = null; }
    const icon = document.getElementById('replayPlayIcon');
    if (icon) icon.className = 'bi bi-play-fill';
  }

  function reset() {
    stop();
    _records = [];
    _serials = [];
    _buckets = [];
    _filteredSerial = null;
    _isLoaded = false;
    _sliderPos = 0;
    _enableControls(false);

    const container = document.getElementById('replaySerials');
    if (container) container.innerHTML = '<span class="text-secondary small">Load a time range to see detected drones.</span>';

    const timeLabel = document.getElementById('replayCurrentTime');
    if (timeLabel) timeLabel.textContent = '—';

    // Clear old activity bar and new detection-band overlay
    const oldBar = document.getElementById('replayActivityBar');
    if (oldBar) oldBar.remove();
    const bandsEl = document.getElementById('replayDetectionBands');
    if (bandsEl) bandsEl.remove();
  }

  /** Navigate to a specific timestamp in replay, optionally filtering to one serial.
   *
   * Called from AlertsManager "View in Replay" button.
   * Sets the time range to isoTs ± 5 minutes, loads data, then seeks to isoTs.
   * If serial is provided it is pre-selected in the serial filter.
   */
  async function loadAndSeekTo(isoTs, serial) {
    if (!isoTs) return;

    const centreMs = new Date(isoTs).getTime();
    const WINDOW_MS = 5 * 60 * 1000; // ±5 minutes
    const fromMs = centreMs - WINDOW_MS;
    const toMs   = centreMs + WINDOW_MS;

    const fromEl = document.getElementById('replayFrom');
    const toEl   = document.getElementById('replayTo');
    if (fromEl) fromEl.value = Utils.toLocalDatetimeInput(new Date(fromMs).toISOString());
    if (toEl)   toEl.value   = Utils.toLocalDatetimeInput(new Date(toMs).toISOString());

    await loadRange();

    // loadRange() resets _filteredSerial to null, so apply the serial filter
    // AFTER the load and re-render the serial tags / bands to isolate the drone.
    if (serial && _isLoaded) {
      _filteredSerial = serial;
      _renderSerials();
      _renderDetectionBands();
    }

    // After load, seek to the alert's exact timestamp
    if (_isLoaded && _rangeToMs > _rangeFromMs) {
      const pos = Math.max(0, Math.min(100,
        ((centreMs - _rangeFromMs) / (_rangeToMs - _rangeFromMs)) * 100
      ));
      skipTo(pos);
    }
  }

  function isPlaying() { return _playing; }
  function isLoaded()  { return _isLoaded; }

  return {
    init,
    loadRange,
    loadAndSeekTo,
    stop,
    reset,
    isPlaying,
    isLoaded,
  };
})();
