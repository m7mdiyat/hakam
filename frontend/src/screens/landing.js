import { header } from '../components.js';
import { logo } from '../icons.js';
import { api } from '../api.js';
import { creds } from '../store.js';
import { normalizeCode } from '../codeutil.js';

export function mountLanding(root, ctx) {
  root.innerHTML = header('') + `
    <div class="screen-body landing">
      <div class="hero">
        <div class="hero-mark">${logo(56, 'var(--gold)', 1.2)}</div>
        <h1 class="wordmark">حَكَم</h1>
        <div class="tagline">لتكن الحُجّة هي الفيصل</div>
      </div>

      <form class="stack" data-create novalidate>
        <input class="input" data-topic type="text" maxlength="300"
               placeholder="ما موضوع المناظرة؟" autocomplete="off" />
        <button class="btn btn-gold" type="submit" data-create-btn>أنشئ جلسة</button>
        <div class="sub-link">
          لديك رمز؟ <button type="button" class="linklike" data-join-toggle>انضم لجلسة</button>
        </div>
        <div class="join-row" data-join-row hidden>
          <input class="input input-code" data-code type="text" inputmode="text"
                 maxlength="6" placeholder="رمز الجلسة" autocomplete="off" />
          <button class="btn btn-ghost" type="button" data-join-go>دخول</button>
        </div>
        <div class="form-error" data-error hidden></div>
      </form>

      <div class="features">مناظرة صوتية · حَكَم محايد · بدون تسجيل حساب</div>
    </div>`;

  const topic = root.querySelector('[data-topic]');
  const createBtn = root.querySelector('[data-create-btn]');
  const errEl = root.querySelector('[data-error]');
  const joinRow = root.querySelector('[data-join-row]');
  const codeInput = root.querySelector('[data-code]');

  const showError = (msg) => { errEl.textContent = msg; errEl.hidden = !msg; };

  root.querySelector('[data-join-toggle]').addEventListener('click', () => {
    joinRow.hidden = !joinRow.hidden;
    if (!joinRow.hidden) codeInput.focus();
  });

  codeInput.addEventListener('input', () => {
    codeInput.value = normalizeCode(codeInput.value);
  });

  const goJoin = () => {
    const code = normalizeCode(codeInput.value);
    if (code.length !== 6) { showError('أدخل رمزًا صحيحًا من 6 خانات.'); return; }
    ctx.navigate(`/j/${code}`);
  };
  root.querySelector('[data-join-go]').addEventListener('click', goJoin);
  codeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); goJoin(); } });

  root.querySelector('[data-create]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const t = topic.value.trim();
    if (!t) { showError('اكتب موضوع المناظرة أولًا.'); topic.focus(); return; }
    showError('');
    createBtn.disabled = true;
    createBtn.textContent = 'جارٍ الإنشاء…';
    try {
      const { code, token, side } = await api.createRoom(t);
      creds.set(code, token, side);
      ctx.navigate(`/r/${code}`);
    } catch (err) {
      showError(err.message || 'تعذّر إنشاء الجلسة.');
      createBtn.disabled = false;
      createBtn.textContent = 'أنشئ جلسة';
    }
  });

  return { unmount() {} };
}
