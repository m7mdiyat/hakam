// SVG icons — paths lifted verbatim from design/hakam-design.html.

const svg = (size, stroke, sw, inner) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${stroke}" ` +
  `stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

// Balance scale — the الحَكَم mark.
export const logo = (size = 18, stroke = 'var(--gold)', sw = 1.5) => svg(size, stroke, sw,
  '<path d="M12 4v15"/><path d="M5 6h14"/><path d="M5 6l-2.5 5"/><path d="M5 6l2.5 5"/>' +
  '<path d="M2.5 11a2.5 2.5 0 0 0 5 0"/><path d="M19 6l-2.5 5"/><path d="M19 6l2.5 5"/>' +
  '<path d="M16.5 11a2.5 2.5 0 0 0 5 0"/><path d="M8.5 19.5h7"/>');

export const mic = (size = 34, stroke = 'var(--teal)', sw = 1.8) => svg(size, stroke, sw,
  '<path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>' +
  '<path d="M19 11a7 7 0 0 1-14 0"/><path d="M12 18v4"/>');

export const micOff = (size = 15, stroke = 'currentColor', sw = 1.8) => svg(size, stroke, sw,
  '<path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>' +
  '<path d="M19 11a7 7 0 0 1-14 0"/><path d="M12 18v4"/>' +
  '<line x1="4" y1="4" x2="20" y2="20"/>');

export const volume = (size = 15, stroke = 'currentColor', sw = 1.8) => svg(size, stroke, sw,
  '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>' +
  '<path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 6a9 9 0 0 1 0 12"/>');

export const volumeOff = (size = 15, stroke = 'currentColor', sw = 1.8) => svg(size, stroke, sw,
  '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>' +
  '<line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/>');

export const copy = (size = 18, stroke = 'var(--gold)', sw = 1.6) => svg(size, stroke, sw,
  '<rect x="9" y="9" width="12" height="12" rx="2.5"/>' +
  '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>');

export const check = (size = 14, stroke = 'var(--teal)', sw = 2.4) => svg(size, stroke, sw,
  '<polyline points="20 6 9 17 4 12"/>');

export const play = (size = 16, stroke = 'var(--ink-2)', sw = 1.8) => svg(size, stroke, sw,
  '<polygon points="7 5 19 12 7 19 7 5" fill="' + stroke + '" stroke="' + stroke + '"/>');

export const stop = (size = 16, stroke = 'var(--ink-2)', sw = 1.8) => svg(size, stroke, sw,
  '<rect x="6" y="6" width="12" height="12" rx="2" fill="' + stroke + '"/>');

// Eye — spectator strip («يشاهد الآن»).
export const eye = (size = 15, stroke = 'var(--muted)', sw = 1.6) => svg(size, stroke, sw,
  '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>' +
  '<circle cx="12" cy="12" r="3"/>');

// Shield — «بدون تسجيل حساب» feature icon.
export const shield = (size = 18, stroke = 'var(--muted)', sw = 1.5) => svg(size, stroke, sw,
  '<path d="M12 3l7 3v5c0 4.6-3 7.7-7 9.2-4-1.5-7-4.6-7-9.2V6z"/>');

// Countdown ring. `frac` = remaining/total in [0,1]; full ring = full time.
// circumference = 2·π·88 ≈ 552.9 ; offset grows as time drains (matches the design).
export const CIRC = 552.9;
export function ring(size, frac, color) {
  const off = (CIRC * (1 - Math.max(0, Math.min(1, frac)))).toFixed(1);
  return `<svg width="${size}" height="${size}" viewBox="0 0 200 200" class="ring">
    <circle cx="100" cy="100" r="88" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="8"/>
    <circle class="ring-progress" cx="100" cy="100" r="88" fill="none" stroke="${color}"
      stroke-width="8" stroke-linecap="round" stroke-dasharray="${CIRC}" stroke-dashoffset="${off}"
      transform="rotate(-90 100 100)"/>
  </svg>`;
}
