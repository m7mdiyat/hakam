// Tiny DOM helpers (no framework).

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Build a single element from an HTML string.
export function node(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// mm:ss with Western tabular numerals (design uses Western digits for timers).
export function fmtClock(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

// Debater display initial (first letter of the name).
export function initial(name) {
  return (name || '').trim().charAt(0) || '؟';
}
