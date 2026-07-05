import { header, avatar, toast, spectatorsHtml, wireSpectatorShare } from '../components.js';
import { check, eye } from '../icons.js';
import { copy as copyIcon } from '../icons.js';
import { api } from '../api.js';
import { esc } from '../ui.js';

const LABEL = { a: 'الطرف الأول', b: 'الطرف الثاني' };
const ROUNDS_TEXT = { 1: 'جولة واحدة لكل طرف', 2: 'جولتان لكل طرف', 3: '٣ جولات لكل طرف' };

function turnLenText(secs) {
  return secs === 120 ? 'دقيقتان للجولة' : secs === 60 ? 'دقيقة للجولة' : `${secs} ثانية للجولة`;
}

function statusHTML(side, d) {
  if (!d.joined) {
    return side === 'b'
      ? `<span class="pending"><i class="dot dot-${side}"></i>ينضم الآن…</span>`
      : '';
  }
  if (d.ready) return `<span class="ready ready-${side}">${check(14, side === 'a' ? 'var(--teal)' : 'var(--coral)')}جاهز</span>`;
  return '<span class="micro-2">لم يجهز بعد</span>';
}

// `mine` = viewer's side ('a'/'b') for the edit affordance; null = read-only
// (spectator lobby).
function debaterCardHtml(side, d, mine) {
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
      ${d.claim && side === mine
        ? '<button type="button" class="edit-link" data-edit-claim>تعديل الدعوى</button>' : ''}
    </div>`;
}

// Read-only lobby for spectators: both cards, format, the strip — no forms.
export function mountSpectatorLobby(root, ctx) {
  root.innerHTML = header('مشاهدة') + `
    <div class="screen-body lobby">
      <div class="topic-pill" data-topic></div>
      <div class="cards">
        <div data-slot-a></div>
        <div data-slot-b></div>
      </div>
      <div class="format-row" data-format></div>
      <div data-spectators></div>
      <div class="micro-2 spec-note">أنت تشاهد — تبدأ المناظرة حين يجهز الطرفان</div>
    </div>`;
  wireSpectatorShare(root, ctx.code);

  function apply(state) {
    root.querySelector('[data-topic]').textContent = state.topic;
    root.querySelector('[data-slot-a]').innerHTML = debaterCardHtml('a', state.debaters.a, null);
    root.querySelector('[data-slot-b]').innerHTML = debaterCardHtml('b', state.debaters.b, null);
    root.querySelector('[data-format]').innerHTML =
      `${esc(ROUNDS_TEXT[state.format.rounds_per_side] || `${state.format.rounds_per_side} جولات`)} · ${turnLenText(state.format.turn_seconds)}`;
    root.querySelector('[data-spectators]').innerHTML =
      spectatorsHtml(state, { shareCode: ctx.code });
  }
  return { update: apply, unmount() {} };
}

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
      <button type="button" class="spec-share-row" data-spec-share>${eye(14)} انسخ رابط المشاهدة للجمهور</button>
      <div data-spectators></div>
      <button class="btn" data-primary></button>
    </div>`;

  root.querySelector('[data-invite]').textContent = inviteLink.replace(/^https?:\/\//, '');
  wireSpectatorShare(root, code);

  root.querySelector('[data-copy]').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(inviteLink); toast('نُسخ الرابط'); }
    catch { toast('انسخ الرابط يدويًا'); }
  });

  // --- topic row: creator can reword it pre-debate (resets both ready flags) --
  const topicEl = root.querySelector('[data-topic]');
  let editingTopic = false;

  function renderTopicRow(state) {
    if (editingTopic) return;   // an open editor is never stomped by the poll
    topicEl.classList.remove('topic-editing');
    topicEl.innerHTML = `<span class="topic-text"></span>${mine === 'a'
      ? '<button type="button" class="edit-link" data-edit-topic>تعديل</button>' : ''}`;
    topicEl.querySelector('.topic-text').textContent = state.topic;
  }

  function openTopicEditor() {
    editingTopic = true;
    topicEl.classList.add('topic-editing');
    topicEl.innerHTML = `
      <div class="topic-editor">
        <textarea class="input textarea" data-topic-input rows="2" maxlength="300"></textarea>
        <div class="form-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-topic-save>حفظ الموضوع</button>
          <button type="button" class="btn btn-ghost btn-sm" data-topic-cancel>إلغاء</button>
        </div>
      </div>`;
    const input = topicEl.querySelector('[data-topic-input]');
    input.value = lastState ? lastState.topic : '';
    input.focus();
    topicEl.querySelector('[data-topic-cancel]').addEventListener('click', () => {
      editingTopic = false;
      if (lastState) renderTopicRow(lastState);
    });
    topicEl.querySelector('[data-topic-save]').addEventListener('click', async () => {
      const t = input.value.trim();
      if (!t) { toast('اكتب موضوع المناظرة.'); return; }
      try {
        const view = await api.setTopic(code, token, t);
        editingTopic = false;
        apply(view);
      } catch (e) { toast(e.message || 'تعذّر تعديل الموضوع'); }
    });
  }

  topicEl.addEventListener('click', (e) => {
    if (mine === 'a' && e.target.closest('[data-edit-topic]')) openTopicEditor();
  });

  const slot = { a: root.querySelector('[data-slot-a]'), b: root.querySelector('[data-slot-b]') };
  const primaryBtn = root.querySelector('[data-primary]');
  let myFormShown = false;

  function myFormCard(side, withCancel) {
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
        <div class="form-actions">
          <button class="btn btn-ghost btn-sm" data-save-claim>حفظ الدعوى</button>
          ${withCancel ? '<button class="btn btn-ghost btn-sm" data-cancel-claim>إلغاء</button>' : ''}
        </div>
      </div>`;
  }

  let editingClaim = false;

  function mountMyForm(side, prefill) {
    slot[side].innerHTML = myFormCard(side, !!prefill);
    myFormShown = true;
    if (prefill) {
      slot[side].querySelector('[data-my-name]').value = prefill.name || '';
      slot[side].querySelector('[data-my-claim]').value = prefill.claim || '';
    }
    const cancel = slot[side].querySelector('[data-cancel-claim]');
    if (cancel) {
      cancel.addEventListener('click', () => {
        editingClaim = false;
        myFormShown = false;
        if (lastState) apply(lastState);
      });
    }
    slot[side].querySelector('[data-save-claim]').addEventListener('click', async () => {
      const name = slot[side].querySelector('[data-my-name]').value.trim();
      const claim = slot[side].querySelector('[data-my-claim]').value.trim();
      const errEl = slot[side].querySelector('[data-my-error]');
      const show = (m) => { errEl.textContent = m; errEl.hidden = !m; };
      if (!name) return show('اكتب اسمك.');
      if (!claim) return show('اكتب دعواك.');
      show('');
      try {
        const view = await api.setClaim(code, token, { name, claim });
        editingClaim = false;
        apply(view);
      } catch (e) { show(e.message || 'تعذّر الحفظ.'); }
    });
  }

  // Re-open the form prefilled to edit an existing claim (server un-readies
  // the editor automatically — «re-confirm after editing»).
  slot[mine].addEventListener('click', (e) => {
    if (!e.target.closest('[data-edit-claim]') || !lastState) return;
    editingClaim = true;
    const me = lastState.debaters[mine];
    mountMyForm(mine, { name: me.name, claim: me.claim });
  });

  // --- format row: creator picks the round count; the other side sees it ----
  const formatEl = root.querySelector('[data-format]');
  let formatBusy = false;

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
    renderTopicRow(state);
    renderFormat(state);
    root.querySelector('[data-spectators]').innerHTML = spectatorsHtml(state);

    // Opponent card: always refresh (no inputs there).
    slot[other].innerHTML = debaterCardHtml(other, state.debaters[other], mine);

    // My card: form until my claim is set, then a populated card (safe to
    // refresh). An open edit form is never stomped by the poll.
    if (editingClaim) {
      /* keep the form as-is */
    } else if (state.debaters[mine].claim) {
      myFormShown = false;
      slot[mine].innerHTML = debaterCardHtml(mine, state.debaters[mine], mine);
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
