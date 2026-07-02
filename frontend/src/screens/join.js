import { header, avatar, mountMessage } from '../components.js';
import { api } from '../api.js';
import { creds } from '../store.js';
import { esc } from '../ui.js';

export function mountJoin(root, ctx) {
  const code = ctx.code;

  // Already a participant? Go straight to the room.
  if (creds.get(code)) { ctx.navigate(`/r/${code}`, { replace: true }); return { unmount() {} }; }

  root.innerHTML = header('انضمام') + '<div class="screen-body screen-center"><div class="loading">جارٍ الفتح…</div></div>';

  let alive = true;

  (async () => {
    let room;
    try {
      room = await api.getRoom(code);
    } catch (err) {
      if (!alive) return;
      mountMessage(root, {
        label: 'انضمام',
        title: err.status === 410 ? 'انتهت صلاحية الجلسة' : 'الجلسة غير موجودة',
        body: 'تأكّد من الرابط أو اطلب رمزًا جديدًا من صاحب الجلسة.',
        cta: 'العودة للرئيسية', onCta: () => ctx.navigate('/'),
      });
      return;
    }
    if (!alive) return;

    if (room.state !== 'lobby' || room.debaters.b.joined) {
      mountMessage(root, {
        label: 'انضمام', title: 'الجلسة مكتملة',
        body: 'انضم طرفان بالفعل إلى هذه المناظرة.',
        cta: 'العودة للرئيسية', onCta: () => ctx.navigate('/'),
      });
      return;
    }

    const a = room.debaters.a;
    root.innerHTML = header('انضمام') + `
      <div class="screen-body join">
        <div class="topic-pill">${esc(room.topic)}</div>

        <div class="opponent">
          <div class="opponent-head">
            ${avatar('a', a.name, 40)}
            <div class="opponent-meta">
              <div class="micro">خصمك</div>
              <div class="opponent-name">${esc(a.name || 'الطرف الأول')}</div>
            </div>
          </div>
          ${a.claim ? `<div class="claim-quote claim-a">«${esc(a.claim)}»</div>` : ''}
        </div>

        <form class="card form-card" data-join novalidate>
          <div class="field">
            <label class="micro" for="j-name">اسمك</label>
            <input class="input" id="j-name" data-name maxlength="40" placeholder="اسمك الأول" autocomplete="off" />
          </div>
          <div class="field">
            <label class="micro" for="j-claim">دعواك</label>
            <textarea class="input textarea" id="j-claim" data-claim maxlength="400" rows="2"
              placeholder="اكتب موقفك في جملة"></textarea>
          </div>
          <label class="consent">
            <input type="checkbox" data-consent />
            <span>أوافق على تسجيل صوتي أثناء المناظرة.</span>
          </label>
          <div class="form-error" data-error hidden></div>
          <button class="btn btn-gold" type="submit" data-submit>انضم للمناظرة</button>
        </form>
      </div>`;

    const errEl = root.querySelector('[data-error]');
    const showError = (m) => { errEl.textContent = m; errEl.hidden = !m; };

    root.querySelector('[data-join]').addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = root.querySelector('[data-name]').value.trim();
      const claim = root.querySelector('[data-claim]').value.trim();
      const consent = root.querySelector('[data-consent]').checked;
      if (!name) return showError('اكتب اسمك.');
      if (!claim) return showError('اكتب دعواك.');
      if (!consent) return showError('يجب الموافقة على التسجيل للانضمام.');
      showError('');
      const btn = root.querySelector('[data-submit]');
      btn.disabled = true; btn.textContent = 'جارٍ الانضمام…';
      try {
        const res = await api.joinRoom(code, { name, claim, consent });
        creds.set(code, res.token, 'b');
        ctx.navigate(`/r/${code}`);
      } catch (err) {
        showError(err.message || 'تعذّر الانضمام.');
        btn.disabled = false; btn.textContent = 'انضم للمناظرة';
      }
    });
  })();

  return { unmount() { alive = false; } };
}
