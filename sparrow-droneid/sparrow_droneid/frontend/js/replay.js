/* ============================================================
   replay.js — Time range picker, playback controls, history
   ============================================================ */

const ReplayManager = (() => {

  let _records = [];        // flat array of all history records for range
  let _serials = [];        // summary list from /api/history/serials
  let _buckets = [];        // timeline buckets
  let _playTimer = null;
  let _playing = false;
  let _speed = 1;
  let _sliderPos = 0;       // 0..100
  let _rangeFromMs = 0;
  let _rangeToMs = 0;
  let _currentTimeMs = 0;
  let _onTimeUpdate = null; // callback(records_at_time, timeMs)
  let _isLoaded = false;

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
      // Load serials summary
      const serialsResp = await Api.getHistorySerials(fromIso, toIso);
      _serials = serialsResp.serials || [];

      // Load all records for the range
      const histResp = await Api.getHistory(fromIso, toIso);
      _records = (histResp.records || []).sort((a, b) =>
        new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      );

      _isLoaded = true;
      _sliderPos = 0;
      _currentTimeMs = _rangeFromMs;

      _renderSerials();
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

    const html = _serials.map(s => `
      <span class="replay-serial-tag" title="${s.ua_type_name} | ${s.detection_count} records | RSSI peak ${s.max_rssi} dBm">
        <i class="bi bi-aircraft-horizontal"></i>
        ${Utils.shortSerial(s.serial_number)}
        <span style="opacity:0.6;font-size:10px;">${s.detection_count}</span>
      </span>`).join('');

    container.innerHTML = html;
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
    const visible = _records.filter(r => {
      const t = new Date(r.timestamp).getTime();
      return t <= _currentTimeMs && t >= windowStart;
    });

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
    _isLoaded = false;
    _sliderPos = 0;
    _enableControls(false);

    const container = document.getElementById('replaySerials');
    if (container) container.innerHTML = '<span class="text-secondary small">Load a time range to see detected drones.</span>';

    const timeLabel = document.getElementById('replayCurrentTime');
    if (timeLabel) timeLabel.textContent = '—';
  }

  function isPlaying() { return _playing; }
  function isLoaded()  { return _isLoaded; }

  return {
    init,
    loadRange,
    stop,
    reset,
    isPlaying,
    isLoaded,
  };
})();
