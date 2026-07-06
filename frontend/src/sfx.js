// Synthesized sound effects (WebAudio only — no asset files, CSP-safe).
// One shared AudioContext, created lazily and resumed on the first gesture.
// IMPORTANT: this synthesizes tones; it never routes a *remote* MediaStream
// through WebAudio (that hits the Safari silent-remote-stream bug — see
// CLAUDE.md). Local oscillators are fine. All calls are best-effort: if the
// context can't resume (no gesture yet) the sound is simply skipped.
let ctx = null;
let master = null;
let muted = false;

function ac() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try {
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0.5;
    master.connect(ctx.destination);
  } catch { ctx = null; }
  return ctx;
}

export function primeSfx() {
  const c = ac();
  if (c && c.state === 'suspended') c.resume().catch(() => {});
}
export function setSfxMuted(m) { muted = !!m; }

function env(node, t0, { attack = 0.005, decay = 0.12, peak = 1, sustain = 0 }) {
  const g = node.gain;
  g.cancelScheduledValues(t0);
  g.setValueAtTime(0.0001, t0);
  g.exponentialRampToValueAtTime(Math.max(0.0001, peak), t0 + attack);
  g.exponentialRampToValueAtTime(Math.max(0.0001, sustain || 0.0001), t0 + attack + decay);
}

function tone(freq, t0, dur, { type = 'sine', gain = 0.6, glideTo = null,
  attack = 0.005, decay } = {}) {
  const c = ac(); if (!c) return;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (glideTo) osc.frequency.exponentialRampToValueAtTime(glideTo, t0 + dur);
  env(g, t0, { attack, decay: decay || dur, peak: gain });
  osc.connect(g).connect(master);
  osc.start(t0);
  osc.stop(t0 + dur + 0.05);
}

function noiseBurst(t0, dur, { gain = 0.5, from = 6000, to = 400, q = 0.7 } = {}) {
  const c = ac(); if (!c) return;
  const n = Math.floor(c.sampleRate * dur);
  const buf = c.createBuffer(1, n, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
  const src = c.createBufferSource();
  src.buffer = buf;
  const bp = c.createBiquadFilter();
  bp.type = 'bandpass';
  bp.frequency.setValueAtTime(from, t0);
  bp.frequency.exponentialRampToValueAtTime(to, t0 + dur);
  bp.Q.value = q;
  const g = c.createGain();
  env(g, t0, { attack: 0.008, decay: dur, peak: gain });
  src.connect(bp).connect(g).connect(master);
  src.start(t0);
  src.stop(t0 + dur + 0.05);
}

function guard() {
  if (muted) return null;
  const c = ac();
  if (!c) return null;
  if (c.state === 'suspended') c.resume().catch(() => {});
  return c.state === 'running' ? c : (c.currentTime >= 0 ? c : null);
}

// ⚡ reveal: a rising whoosh into a punchy low impact + a bright spark.
export function sfxReveal() {
  const c = guard(); if (!c) return;
  const t = c.currentTime;
  noiseBurst(t, 0.32, { gain: 0.32, from: 800, to: 5200, q: 0.6 });  // whoosh up
  tone(140, t + 0.26, 0.4, { type: 'sine', gain: 0.9, glideTo: 55, decay: 0.4 }); // impact
  tone(880, t + 0.28, 0.16, { type: 'triangle', gain: 0.28, glideTo: 1760 });     // spark
}

// Accept / start: an energetic ascending two-note "go".
export function sfxGo() {
  const c = guard(); if (!c) return;
  const t = c.currentTime;
  tone(523, t, 0.12, { type: 'triangle', gain: 0.5 });
  tone(784, t + 0.1, 0.22, { type: 'triangle', gain: 0.55, glideTo: 988 });
  noiseBurst(t, 0.14, { gain: 0.12, from: 3000, to: 8000, q: 1.2 });
}

// Countdown tick (last few seconds).
export function sfxTick(last = false) {
  const c = guard(); if (!c) return;
  const t = c.currentTime;
  tone(last ? 1320 : 990, t, last ? 0.16 : 0.06,
    { type: 'square', gain: last ? 0.4 : 0.22 });
}

// Round end: a soft descending two-tone.
export function sfxTimeUp() {
  const c = guard(); if (!c) return;
  const t = c.currentTime;
  tone(660, t, 0.18, { type: 'sine', gain: 0.4 });
  tone(440, t + 0.16, 0.5, { type: 'sine', gain: 0.45, decay: 0.5 });
}
