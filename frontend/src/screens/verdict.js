import { header, toast } from '../components.js';
import { logo, play as playIcon, stop as stopIcon } from '../icons.js';
import { esc } from '../ui.js';
import { api } from '../api.js';
import { creds } from '../store.js';

const sideLetter = (s) => (s === 'a' ? 'أ' : 'ب');
const roundShort = (tk) => (parseInt(tk.slice(-1), 10) === 1 ? 'افتتاح' : 'رد');

// Phase 1: the debate is complete ("deliberating"); the AI verdict arrives in Phase 2.
// We show a completion card + playback of every recorded round.
export function mountVerdict(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  let built = false;

  const audioEl = new Audio();
  let playingTurn = null;
  const urlCache = {};
  audioEl.onended = audioEl.onpause = () => { playingTurn = null; mark(); };

  function mark() {
    root.querySelectorAll('[data-play]').forEach((b) => {
      const on = b.getAttribute('data-play') === playingTurn;
      b.classList.toggle('playing', on);
      b.innerHTML = on ? stopIcon(15) : playIcon(15);
    });
  }
  async function togglePlay(turn) {
    if (playingTurn === turn) { audioEl.pause(); return; }
    try {
      if (!urlCache[turn]) urlCache[turn] = await api.fetchAudioUrl(code, token, turn);
      audioEl.src = urlCache[turn];
      await audioEl.play();
      playingTurn = turn; mark();
    } catch { toast('تعذّر تشغيل التسجيل'); }
  }

  function render(state) {
    const turns = state.turns.filter((t) => t.has_audio);
    root.innerHTML = header('الحُكْم') + `
      <div class="screen-body verdict">
        <div class="verdict-hero">
          <div class="verdict-mark">${logo(30, 'var(--gold)', 1.3)}</div>
          <div class="verdict-title">اكتملت المناظرة</div>
          <div class="verdict-sub">الحُكْم بالذكاء الاصطناعي قادم في المرحلة الثانية</div>
          <div class="verdict-topic">${esc(state.topic)}</div>
        </div>

        <div class="turns-panel">
          <div class="panel-head"><span>الجولات المسجّلة</span></div>
          <div class="turns-list" data-turns></div>
        </div>

        <div class="verdict-actions">
          <button class="btn btn-gold" data-new type="button">مناظرة جديدة</button>
        </div>
      </div>`;

    const list = root.querySelector('[data-turns]');
    list.innerHTML = turns.length
      ? turns.map((t) => {
          const name = state.debaters[t.debater].name || (t.debater === 'a' ? 'الطرف الأول' : 'الطرف الثاني');
          return `<div class="turn-bubble">
            <div class="turn-meta"><span class="turn-name turn-${t.debater}">${esc(name)}</span>
              <span class="micro-2">${roundShort(t.turn)} ${sideLetter(t.debater)}</span></div>
            <button class="turn-play" data-play="${t.turn}" aria-label="تشغيل">${playIcon(15)}</button>
          </div>`;
        }).join('')
      : '<div class="turns-empty">لم تُسجَّل جولات صوتية.</div>';

    list.addEventListener('click', (e) => {
      const b = e.target.closest('[data-play]');
      if (b) togglePlay(b.getAttribute('data-play'));
    });
    root.querySelector('[data-new]').addEventListener('click', () => {
      creds.clear(code);
      ctx.navigate('/');
    });
  }

  return {
    update(state) { if (!built) { render(state); built = true; } },
    unmount() {
      try { audioEl.pause(); } catch { /* ignore */ }
      Object.values(urlCache).forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* ignore */ } });
    },
  };
}
