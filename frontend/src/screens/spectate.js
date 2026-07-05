// Spectator entry (/s/CODE): a name gate, then the read-only room view.
// Spectators poll with their own token (presence powers the «يشاهد الآن»
// strip) and can play turn audio, but never see a mic, a finish button, or
// the rematch actions.
import { header, mountMessage } from '../components.js';
import { api } from '../api.js';
import { creds, specCreds } from '../store.js';
import { esc } from '../ui.js';
import { startPolling } from '../poll.js';
import { createRoomView } from './room.js';

export function mountSpectate(root, ctx) {
  const code = ctx.code;

  // Debaters follow their own seat, not the spectator view.
  if (creds.get(code)) { ctx.navigate(`/r/${code}`, { replace: true }); return { unmount() {} }; }

  let inner = null;
  let alive = true;

  function mountRoom(token) {
    const view = createRoomView(root, {
      ...ctx, creds: { token, side: null }, role: 'spectator',
    });
    const stop = startPolling(code, token, (s) => view.update(s), (e) => view.onError(e));
    inner = { unmount() { stop(); view.unmount(); } };
  }

  const saved = specCreds.get(code);
  if (saved) {
    mountRoom(saved.token);
    return { unmount() { if (inner) inner.unmount(); } };
  }

  root.innerHTML = header('مشاهدة') + '<div class="screen-body screen-center"><div class="loading">جارٍ الفتح…</div></div>';

  (async () => {
    let room;
    try {
      room = await api.getRoom(code);
    } catch (err) {
      if (!alive) return;
      mountMessage(root, {
        label: 'مشاهدة',
        title: err.status === 410 ? 'انتهت صلاحية الجلسة' : 'الجلسة غير موجودة',
        body: 'تأكّد من رابط المشاهدة.',
        cta: 'العودة للرئيسية', onCta: () => ctx.navigate('/'),
      });
      return;
    }
    if (!alive) return;

    root.innerHTML = header('مشاهدة') + `
      <div class="screen-body join">
        <div class="topic-pill">${esc(room.topic)}</div>
        <form class="card form-card" data-spectate novalidate>
          <div class="micro">تابع المناظرة كمشاهد — اسمك يظهر للحاضرين</div>
          <div class="field">
            <label class="micro" for="s-name">اسمك</label>
            <input class="input" id="s-name" data-name maxlength="40" placeholder="اسمك الأول" autocomplete="off" />
          </div>
          <div class="form-error" data-error hidden></div>
          <button class="btn btn-gold" type="submit" data-submit>ادخل كمشاهد</button>
        </form>
      </div>`;

    const errEl = root.querySelector('[data-error]');
    const showError = (m) => { errEl.textContent = m; errEl.hidden = !m; };

    root.querySelector('[data-spectate]').addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = root.querySelector('[data-name]').value.trim();
      if (!name) return showError('اكتب اسمك.');
      showError('');
      const btn = root.querySelector('[data-submit]');
      btn.disabled = true; btn.textContent = 'جارٍ الدخول…';
      try {
        const res = await api.spectate(code, name);
        specCreds.set(code, res.token, name);
        mountRoom(res.token);
      } catch (err) {
        showError(err.message || 'تعذّر الدخول.');
        btn.disabled = false; btn.textContent = 'ادخل كمشاهد';
      }
    });
  })();

  return { unmount() { alive = false; if (inner) inner.unmount(); } };
}
