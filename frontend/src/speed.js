// Global playback-speed preference for every recorded-audio player on the site.
// ONE persisted rate (×1 / ×1.5 / ×2), applied to every <audio> element that
// opts in and reflected by every speed pill on screen — set it once and it
// sticks across clips and across sessions. Pitch is preserved, so ×2 is faster
// *speech*, never chipmunk voices.
//
// Scope: recorded playback only (the proof player + the debate turn-replay
// element). It NEVER touches live WebRTC/SFU audio — remote MediaStreams must
// not be re-rated or routed through anything exotic (Safari silent-remote-stream
// trap, see CLAUDE.md).

const RATES = [1, 1.5, 2];
const KEY = 'hakam:rate';

let rate = load();
const els = new Set();    // bound <audio> elements to keep in sync
const subs = new Set();   // pill re-render callbacks

function load() {
  try {
    const v = parseFloat(localStorage.getItem(KEY));
    return RATES.includes(v) ? v : 1;
  } catch { return 1; }
}

// Western numerals (the RTL rule for timers/scores): "×1", "×1.5", "×2".
export function rateLabel(r = rate) { return `×${r}`; }
export function getRate() { return rate; }

function applyEl(el) {
  try {
    el.preservesPitch = true;
    el.mozPreservesPitch = true;
    el.webkitPreservesPitch = true;
    el.playbackRate = rate;
  } catch { /* element gone or rate unsupported: ignore */ }
}

// Register an <audio> element so it always tracks the current rate. Returns an
// unbind fn to call when the element is destroyed.
export function bindAudio(el) {
  els.add(el);
  applyEl(el);
  return () => els.delete(el);
}

export function setRate(r) {
  rate = RATES.includes(r) ? r : 1;
  try { localStorage.setItem(KEY, String(rate)); } catch { /* private mode: ignore */ }
  els.forEach(applyEl);
  subs.forEach((f) => { try { f(); } catch { /* ignore */ } });
}

export function cycleRate() {
  const i = RATES.indexOf(rate);
  setRate(RATES[(i + 1) % RATES.length]);
}

function onRateChange(fn) { subs.add(fn); return () => subs.delete(fn); }

// Fast-forward glyph (two triangles) — direction-neutral for RTL; the ×N label
// carries the meaning.
const FF_ICON = `<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
  <path fill="currentColor" d="M4 5l8 7-8 7V5zm9 0l8 7-8 7V5z"/></svg>`;

// A compact floating speed control, docked to the frame's bottom inline-start
// corner (clear of the centered mic orb). Appended to `root` (the .app frame,
// position:relative + overflow:hidden), so it floats over the scrolling body
// and stays contained inside the desktop device frame.
export function createSpeedPill(root, { visible = true } = {}) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'speed-pill';
  el.setAttribute('aria-label', 'سرعة تشغيل التسجيلات');
  el.hidden = !visible;

  function render() {
    el.innerHTML = `${FF_ICON}<span class="speed-rate">${rateLabel()}</span>`;
    el.classList.toggle('speed-fast', rate !== 1);
  }
  render();

  el.addEventListener('click', () => {
    cycleRate();
    // A tiny bump on each tap makes the change feel responsive.
    el.classList.remove('speed-bump');
    void el.offsetWidth;
    el.classList.add('speed-bump');
  });
  const off = onRateChange(render);
  root.appendChild(el);

  return {
    el,
    // gold-accent while audio is actually playing (driven by each player's
    // onChange/markPlaying callback).
    setActive(on) { el.classList.toggle('speed-active', !!on); },
    setVisible(on) { el.hidden = !on; },
    destroy() { off(); try { el.remove(); } catch { /* ignore */ } },
  };
}
