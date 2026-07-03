// Turn-recording wrapper around MediaRecorder (gesture-agnostic: the debate
// screen drives it tap-to-toggle — one tap starts, the next stops and sends).
// Feature-detects mimeType: audio/webm (Chrome/Android) or audio/mp4 (iOS Safari).

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/aac',
  'audio/ogg;codecs=opus',
];

export function isSupported() {
  return typeof MediaRecorder !== 'undefined'
    && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

export function pickMime() {
  if (typeof MediaRecorder === 'undefined') return '';
  for (const m of MIME_CANDIDATES) {
    try { if (MediaRecorder.isTypeSupported(m)) return m; } catch { /* ignore */ }
  }
  return '';
}

// Ignore accidental taps shorter than this.
const MIN_MS = 400;

export class TurnRecorder {
  // Every started recording ends in EXACTLY ONE of:
  //   onStop(blob, ms) — a usable take, or
  //   onDiscard(reason) — 'too_short' | 'canceled' | 'empty' | 'error'.
  // Callers key their UI state off these; a recorder that could end silently
  // (the old <MIN_MS path) wedges the caller's "recording" flag forever.
  constructor({ maxMs = 120000, onStart, onStop, onDiscard, onError, onLevel } = {}) {
    this.maxMs = maxMs;
    this.onStart = onStart;
    this.onStop = onStop;
    this.onDiscard = onDiscard;
    this.onError = onError;
    this.onLevel = onLevel;   // ~30fps mic RMS in [0,1] while recording
    this.recording = false;
    this._pending = false;   // getUserMedia in flight
    this._abort = false;     // stop()/cancel() arrived while pending
    this._canceled = false;
  }

  async start() {
    if (this.recording || this._pending) return;
    this._pending = true;
    this._abort = false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      this._pending = false;
      if (this.onError) this.onError(e);
      return;
    }
    this._pending = false;
    if (this._abort) {
      // Released before the mic was even acquired: never start an orphaned
      // recording — release the stream and report a discard.
      this._cleanup();
      this._discard(this._abortCanceled ? 'canceled' : 'too_short');
      return;
    }
    this._canceled = false;
    this.chunks = [];
    const mime = pickMime();
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.rec.ondataavailable = (e) => { if (e.data && e.data.size) this.chunks.push(e.data); };
    this.rec.onstop = () => this._finish();
    this.startedAt = performance.now();
    this.rec.start();
    this.recording = true;
    if (this.onStart) this.onStart();
    this._startMeter();
    this._timer = setTimeout(() => this.stop(), this.maxMs);
  }

  // Live level meter on the SAME stream (no extra permission): AnalyserNode
  // time-domain RMS. Drives the waveform bars so a dead mic is visibly flat.
  _startMeter() {
    if (!this.onLevel) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this._audioCtx = new Ctx();
      const analyser = this._audioCtx.createAnalyser();
      analyser.fftSize = 512;
      this._audioCtx.createMediaStreamSource(this.stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      this._meter = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        this.onLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3));
      }, 33);
    } catch { /* metering is cosmetic — recording continues without it */ }
  }

  stop() { this._end(false); }
  cancel() { this._end(true); }

  _end(canceled) {
    if (!this.recording) {
      if (this._pending) { this._abort = true; this._abortCanceled = canceled; }
      return;
    }
    this.recording = false;
    this._canceled = canceled;
    clearTimeout(this._timer);
    let stopping = false;
    try {
      if (this.rec && this.rec.state !== 'inactive') { this.rec.stop(); stopping = true; }
    } catch { /* fall through to the discard below */ }
    if (!stopping) {  // no onstop will ever fire — report terminally now
      this._cleanup();
      this._discard('error');
    }
  }

  _finish() {
    const durationMs = Math.round(performance.now() - this.startedAt);
    const type = (this.chunks[0] && this.chunks[0].type) || pickMime() || 'audio/webm';
    const blob = new Blob(this.chunks, { type });
    this._cleanup();
    if (this._canceled) return this._discard('canceled');
    if (durationMs < MIN_MS) return this._discard('too_short');
    if (blob.size === 0) return this._discard('empty');
    if (this.onStop) this.onStop(blob, durationMs);
  }

  _discard(reason) {
    if (this.onDiscard) this.onDiscard(reason);
  }

  _cleanup() {
    if (this._meter) { clearInterval(this._meter); this._meter = null; }
    if (this._audioCtx) { try { this._audioCtx.close(); } catch { /* ignore */ } this._audioCtx = null; }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
