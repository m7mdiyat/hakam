import { header, avatar, toast } from '../components.js';
import { check } from '../icons.js';
import { copy as copyIcon } from '../icons.js';
import { api } from '../api.js';
import { esc } from '../ui.js';

const LABEL = { a: 'الطرف الأول', b: 'الطرف الثاني' };

export function mountLobby(root, ctx) {
  const code = ctx.code;
  const token = ctx.creds.token;
  const mine = ctx.creds.side;          // 'a' | 'b'
  const other = mine === 'a' ? 'b' : 'a';
  const inviteLink = `${location.origin}/j/${code}`;

  root.innerHTML = header('الجلسة') + `
    <div class="screen-body lobby">
      <div class="topic-pill" data-topic></div>

      <button class="card invite" data-copy>
        <div class="invite-text">
          <div class="micro">شارك الرابط مع خصمك</div>
          <div class="invite-link" data-invite></div>
        </div>
        <span class="invite-btn">${copyIcon(18)}</span>
      </button>

      <div class="cards">
        <div data-slot-a></div>
        <div data-slot-b></div>
      </div>

      <div class="format-row" data-format></div>
      <button class="btn" data-primary></button>
    </div>`;

  root.querySelector('[data-topic]').textContent = '';
  root.querySelector('[data-invite]').textContent = inviteLink.replace(/^https?:\/\//, '');

  root.querySelector('[data-copy]').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(inviteLink); toast('نُسخ الرابط'); }
    catch { toast('انسخ الرابط يدويًا'); }
  });

  const slot = { a: root.querySelector('[data-slot-a]'), b: root.querySelector('[data-slot-b]') };
  const primaryBtn = root.querySelector('[data-primary]');
  let myFormShown = false;

  function statusHTML(side, d) {
    if (!d.joined) {
      return side === 'b'
        ? `<span class="pending"><i class="dot dot-${side}"></i>ينضم الآن…</span>`
        : '';
    }
    if (d.ready) return `<span class="ready ready-${side}">${check(14, side === 'a' ? 'var(--teal)' : 'var(--coral)')}جاهز</span>`;
    return '<span class="micro-2">لم يجهز بعد</span>';
  }

  function populatedCard(side, d) {
    return `
      <div class="dcard dcard-${side}">
        <div class="dcard-head">
          <div class="dcard-id">
            ${avatar(side, d.name)}
            <div class="dcard-meta">
              <div class="micro">${LABEL[side]}</div>
              <div class="dcard-name">${esc(d.name || '…')}</div>
            </div>
          </div>
          <div class="dcard-status">${statusHTML(side, d)}</div>
        </div>
        ${d.claim ? `<div class="claim-quote claim-${side}">«${esc(d.claim)}»</div>` : ''}
      </div>`;
  }

  function myFormCard(side) {
    return `
      <div class="dcard dcard-${side}">
        <div class="dcard-head">
          <div class="dcard-id">
            ${avatar(side, '')}
            <div class="dcard-meta">
              <div class="micro">${LABEL[side]}</div>
              <div class="dcard-name">أنت</div>
            </div>
          </div>
        </div>
        <div class="field"><input class="input" data-my-name maxlength="40" placeholder="اسمك الأول" autocomplete="off" /></div>
        <div class="field"><textarea class="input textarea" data-my-claim rows="2" maxlength="400" placeholder="اكتب دعواك في جملة"></textarea></div>
        <div class="form-error" data-my-error hidden></div>
        <button class="btn btn-ghost btn-sm" data-save-claim>حفظ الدعوى</button>
      </div>`;
  }

  function mountMyForm(side) {
    slot[side].innerHTML = myFormCard(side);
    myFormShown = true;
    slot[side].querySelector('[data-save-claim]').addEventListener('click', async () => {
      const name = slot[side].querySelector('[data-my-name]').value.trim();
      const claim = slot[side].querySelector('[data-my-claim]').value.trim();
      const errEl = slot[side].querySelector('[data-my-error]');
      const show = (m) => { errEl.textContent = m; errEl.hidden = !m; };
      if (!name) return show('اكتب اسمك.');
      if (!claim) return show('اكتب دعواك.');
      show('');
      try { apply(await api.setClaim(code, token, { name, claim })); }
      catch (e) { show(e.message || 'تعذّر الحفظ.'); }
    });
  }

  // --- format row: creator picks the round count; the other side sees it ----
  const ROUNDS_TEXT = { 1: 'جولة واحدة لكل طرف', 2: 'جولتان لكل طرف', 3: '٣ جولات لكل طرف' };
  const formatEl = root.querySelector('[data-format]');
  let formatBusy = false;

  function turnLenText(secs) {
    return secs === 120 ? 'دقيقتان للجولة' : secs === 60 ? 'دقيقة للجولة' : `${secs} ثانية للجولة`;
  }

  function renderFormat(state) {
    const rounds = state.format.rounds_per_side;
    const len = turnLenText(state.format.turn_seconds);
    if (mine !== 'a') {
      formatEl.innerHTML = `${esc(ROUNDS_TEXT[rounds] || `${rounds} جولات`)} · ${len}`;
      return;
    }
    formatEl.innerHTML = `
      <div class="format-pick">
        <span class="micro">عدد الجولات</span>
        <div class="seg" role="group">
          ${[1, 2, 3].map((n) => `
            <button type="button" class="seg-btn ${n === rounds ? 'seg-on' : ''}"
              data-rounds="${n}" ${formatBusy ? 'disabled' : ''}>${n === 1 ? 'واحدة' : n === 2 ? 'جولتان' : 'ثلاث'}</button>`).join('')}
        </div>
        <span class="micro-2">${len}</span>
      </div>`;
  }

  formatEl.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-rounds]');
    if (!b || formatBusy || mine !== 'a') return;
    const n = parseInt(b.getAttribute('data-rounds'), 10);
    formatBusy = true;
    try { apply(await api.setFormat(code, token, n)); }
    catch (err) { toast(err.message || 'تعذّر تعديل الصيغة'); }
    finally { formatBusy = false; if (lastState) renderFormat(lastState); }
  });

  let lastState = null;

  function primaryFor(state) {
    const me = state.debaters[mine];
    if (!me.claim) return { label: 'اكتب دعواك للاستعداد', disabled: true, cls: 'btn-disabled' };
    if (!me.ready) return { label: 'أنا جاهز', disabled: false, cls: 'btn-gold', act: 'ready' };
    if (state.both_ready) return { label: 'ابدأ المناظرة', disabled: true, cls: 'btn-gold' };
    return { label: 'بانتظار الطرف الآخر…', disabled: true, cls: 'btn-disabled' };
  }

  primaryBtn.addEventListener('click', async () => {
    if (primaryBtn._act !== 'ready') return;
    primaryBtn.disabled = true;
    try { apply(await api.ready(code, token, true)); }
    catch { primaryBtn.disabled = false; }
  });

  function apply(state) {
    lastState = state;
    root.querySelector('[data-topic]').textContent = state.topic;
    renderFormat(state);

    // Opponent card: always refresh (no inputs there).
    slot[other].innerHTML = populatedCard(other, state.debaters[other]);

    // My card: form until my claim is set, then a populated card (safe to refresh).
    if (state.debaters[mine].claim) {
      myFormShown = false;
      slot[mine].innerHTML = populatedCard(mine, state.debaters[mine]);
    } else if (!myFormShown) {
      mountMyForm(mine);
    }

    const p = primaryFor(state);
    primaryBtn.textContent = p.label;
    primaryBtn.disabled = p.disabled;
    primaryBtn.className = `btn ${p.cls}`;
    primaryBtn._act = p.act || null;
  }

  return { update: apply, unmount() {} };
}
