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

// --- shared mic stream -------------------------------------------------------
// Acquired once and reused across every take of the debate, so the permission
// prompt shows AT MOST ONCE: browsers with one-time grants (Chrome's «Allow
// this time», Safari's per-session grant) re-prompt on the next getUserMedia
// after the previous stream is fully stopped. Reuse also removes the
// acquisition latency from every mic tap. Holding the stream open does NOT
// lock the device — plain {audio:true} capture is OS-mixed, other applications
// can use the mic at the same time. Tracks are disabled between takes, and the
// debate screen calls releaseMic() on unmount so nothing lingers afterwards.
let micStream = null;
let micPending = null;   // single-flight: a double-tap must not double-prompt

async function acquireMic() {
  if (micStream && micStream.getTracks().some((t) => t.readyState === 'live')) {
    return micStream;
  }
  micStream = null;
  if (!micPending) {
    micPending = navigator.mediaDevices.getUserMedia({ audio: true })
      .then((stream) => {
        micStream = stream;
        // The browser/OS can end the track behind our back (device unplugged,
        // permission revoked, tab suspended): drop the cache so the next take
        // re-acquires instead of recording a dead stream.
        stream.getTracks().forEach((t) => {
          t.addEventListener('ended', () => { if (micStream === stream) micStream = null; });
        });
        return stream;
      })
      .finally(() => { micPending = null; });
  }
  return micPending;
}

export function releaseMic() {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;
}

// Pre-warm on debate mount WITHOUT ever prompting: only when the permission is
// already granted (Permissions API), so the first tap starts instantly. Safari
// doesn't support querying 'microphone' — it just skips the warm-up.
export function warmMic() {
  if (!isSupported() || !(navigator.permissions && navigator.permissions.query)) return;
  try {
    navigator.permissions.query({ name: 'microphone' })
      .then((st) => { if (st.state === 'granted') acquireMic().catch(() => {}); })
      .catch(() => { /* unsupported query name — never prompt from here */ });
  } catch { /* ignore */ }
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
      this.stream = await acquireMic();
    } catch (e) {
      this._pending = false;
      if (this.onError) this.onError(e);
      return;
    }
    this._pending = false;
    this.stream.getAudioTracks().forEach((t) => { t.enabled = true; });
    if (this._abort) {
      // Stopped before the mic was even acquired: never start an orphaned
      // recording — re-disable the shared stream and report a discard.
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
    // Timesliced so chunks flush as we go: if the recorder dies mid-turn
    // (phone call, revoked mic, suspended page) the take survives up to the
    // interruption instead of collapsing to an empty blob.
    this.rec.start(1000);
    this.recording = true;
    if (this.onStart) this.onStart();
    this._startMeter();
    this._keepAwake();
    this._timer = setTimeout(() => this.stop(), this.maxMs);
  }

  // Re-arm the auto-stop against the SERVER clock. The local timer starts when
  // getUserMedia resolves — after the server already stamped the deadline (and
  // a first-time permission prompt can sit for many seconds) — so without a
  // resync the recorder always overshoots the turn.
  syncStop(msFromNow) {
    if (!this.recording) return;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.stop(), Math.max(0, msFromNow));
  }

  // A phone that auto-locks mid-turn suspends the page and kills the take.
  // Screen wake lock while recording where supported; silently cosmetic elsewhere.
  _keepAwake() {
    if (!(navigator.wakeLock && navigator.wakeLock.request)) return;
    const acquire = () => {
      if (!this.recording || document.visibilityState !== 'visible') return;
      navigator.wakeLock.request('screen')
        .then((lock) => { this._wakeLock = lock; })
        .catch(() => { /* denied/low battery — recording continues */ });
    };
    this._onVisible = acquire;   // the lock auto-releases on tab switch; re-acquire on return
    document.addEventListener('visibilitychange', this._onVisible);
    acquire();
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
    // The recorder can stop ITSELF (track died: phone call, OS interruption,
    // device switch) — then onstop lands here without _end() having run. Kill
    // the auto-stop timer and the recording flag NOW, or the timer later fires
    // on this dead recorder and reports a phantom 'error' discard («تعذّر
    // التسجيل») after the take was already delivered.
    this.recording = false;
    clearTimeout(this._timer);
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
    if (this._onVisible) { document.removeEventListener('visibilitychange', this._onVisible); this._onVisible = null; }
    if (this._wakeLock) { try { this._wakeLock.release(); } catch { /* ignore */ } this._wakeLock = null; }
    // The stream is the debate-wide shared one (see acquireMic): disable its
    // tracks between takes, never stop them — stopping would force the next
    // take back through getUserMedia and, on one-time grants, a fresh prompt.
    if (this.stream) this.stream.getAudioTracks().forEach((t) => { t.enabled = false; });
    this.stream = null;
  }
}
