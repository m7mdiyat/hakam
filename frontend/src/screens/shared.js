// Shared verdict (/v/{id}): the public 7-day snapshot behind «شارك الحكم».
// Read-only — no polling, no tokens; the verdict renderer is reused verbatim
// (the server shapes the snapshot to the room public_view keys it reads) and
// the proof players ride the public shared-audio endpoints.
import { header, toast } from '../components.js';
import { play as playIcon, stop as stopIcon } from '../icons.js';
import { api } from '../api.js';
import { createProofPlayer } from '../audioproof.js';
import { verdictHtml } from './verdict.js';

function expiredHtml() {
  return `
    <div class="screen-body screen-center verdict">
      <div class="verdict-hero delib-hero">
        <div class="verdict-title">انتهت صلاحية هذا الحُكْم</div>
        <p class="share-expired-note">روابط الأحكام تبقى ٧ أيام من مشاركتها ثم تُحذف مع تسجيلاتها.</p>
        <a class="btn btn-gold" href="/" data-nav>جرّب الحَكَم بنفسك</a>
      </div>
    </div>`;
}

export function mountShared(root, ctx) {
  const { id } = ctx;
  let player = null;

  root.innerHTML = header('الحُكْم') + `
    <div class="screen-body screen-center verdict">
      <div class="verdict-hero delib-hero"><div class="verdict-title">جارٍ فتح الحُكْم…</div></div>
    </div>`;

  function markProofs() {
    const active = player ? player.active() : null;
    root.querySelectorAll('[data-proof]').forEach((b) => {
      const on = b.getAttribute('data-proof') === active;
      b.classList.toggle('playing', on);
      b.querySelector('.proof-icon').innerHTML = on ? stopIcon(13, 'currentColor') : playIcon(13, 'currentColor');
    });
  }

  api.getShared(id).then((state) => {
    root.innerHTML = header('الحُكْم') + verdictHtml(state, true);
    // The snapshot page owns its actions: re-copy the link + a visitor CTA.
    const actions = root.querySelector('.verdict-actions');
    if (actions) {
      actions.innerHTML = `
        <button class="btn btn-gold" data-copylink type="button">انسخ رابط الحُكْم</button>
        <a class="btn btn-ghost" href="/" data-nav>جرّب الحَكَم بنفسك</a>`;
      actions.querySelector('[data-copylink]').addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(location.href);
          toast('نُسخ الرابط');
        } catch { /* clipboard denied */ }
      });
    }
    player = createProofPlayer(null, null, markProofs,
      (turn) => api.fetchSharedAudioUrl(id, turn));
    root.addEventListener('click', (e) => {
      const g = e.target.closest('[data-goto]');
      if (g) {
        const el = root.querySelector(`#${g.getAttribute('data-goto')}`);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('arg-flash');
          setTimeout(() => el.classList.remove('arg-flash'), 1600);
        }
        return;
      }
      const c = e.target.closest('[data-collapse]');
      if (c) {
        const body = c.parentElement.querySelector('.v-collapse-body');
        body.hidden = !body.hidden;
        c.classList.toggle('open', !body.hidden);
        return;
      }
      const b = e.target.closest('[data-proof]');
      if (b) {
        const end = parseFloat(b.getAttribute('data-end'));
        player.toggle(
          b.getAttribute('data-proof'), b.getAttribute('data-turn'),
          parseFloat(b.getAttribute('data-start')) || 0, Number.isNaN(end) ? null : end,
        ).catch(() => toast('انتهت صلاحية هذا التسجيل أو تعذّر تشغيله'));
      }
    });
  }).catch(() => {
    root.innerHTML = header('الحُكْم') + expiredHtml();
  });

  return { unmount() { if (player) player.destroy(); } };
}
