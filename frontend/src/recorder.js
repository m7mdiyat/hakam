// Hold-to-record wrapper around MediaRecorder.
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
  constructor({ maxMs = 120000, onStart, onStop, onError } = {}) {
    this.maxMs = maxMs;
    this.onStart = onStart;
    this.onStop = onStop;
    this.onError = onError;
    this.recording = false;
    this._canceled = false;
  }

  async start() {
    if (this.recording) return;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      if (this.onError) this.onError(e);
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
    this._timer = setTimeout(() => this.stop(), this.maxMs);
  }

  stop() { this._end(false); }
  cancel() { this._end(true); }

  _end(canceled) {
    if (!this.recording) return;
    this.recording = false;
    this._canceled = canceled;
    clearTimeout(this._timer);
    try { if (this.rec && this.rec.state !== 'inactive') this.rec.stop(); }
    catch { this._cleanup(); }
  }

  _finish() {
    const durationMs = Math.round(performance.now() - this.startedAt);
    const type = (this.chunks[0] && this.chunks[0].type) || pickMime() || 'audio/webm';
    const blob = new Blob(this.chunks, { type });
    this._cleanup();
    if (this._canceled || durationMs < MIN_MS || blob.size === 0) return;
    if (this.onStop) this.onStop(blob, durationMs);
  }

  _cleanup() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
