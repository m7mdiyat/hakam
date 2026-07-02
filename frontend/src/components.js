// Shared UI pieces used across screens.
import { logo } from './icons.js';
import { esc, initial } from './ui.js';

export function header(label, opts = {}) {
  const crumbCls = opts.crumbDesktopOnly ? 'crumb crumb-desktop' : 'crumb';
  return `<header class="hdr">
    <div class="brand">${logo(18)}<span class="brand-name">حَكَم</span></div>
    ${label ? `<div class="${crumbCls}">${esc(label)}</div>` : ''}
  </header>`;
}

export function avatar(side, name, size = 42) {
  return `<div class="avatar avatar-${side}" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.4)}px">${esc(initial(name))}</div>`;
}

let toastTimer;
export function toast(msg) {
  let t = document.querySelector('.toast');
  if (!t) { t = document.createElement('div'); t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 1600);
}

// A centered single-card message screen (gone / abandoned / errors).
export function mountMessage(root, { title, body, cta, onCta, label = '' }) {
  root.innerHTML = header(label) + `
    <div class="screen-body screen-center">
      <div class="msg-card">
        <div class="msg-title">${esc(title)}</div>
        ${body ? `<div class="msg-body">${esc(body)}</div>` : ''}
        ${cta ? `<button class="btn btn-gold" data-cta>${esc(cta)}</button>` : ''}
      </div>
    </div>`;
  if (cta && onCta) root.querySelector('[data-cta]').addEventListener('click', onCta);
  return { update() {}, unmount() {} };
}
