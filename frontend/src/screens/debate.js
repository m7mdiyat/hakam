import { header } from '../components.js';
import { mic as micIcon, ring, CIRC, play as playIcon, stop as stopIcon } from '../icons.js';
import { esc, fmtClock } from '../ui.js';
import { api } from '../api.js';
import { toast } from '../components.js';
import { TurnRecorder, isSupported } from '../recorder.js';

const TEAL = 'var(--teal)';
const CORAL = 'var(--coral)';
const sideColor = (s) => (s === 'a' ? TEAL : CORAL);
const sideInk = (s) => (s === 'a' ? '#062B29' : '#3d120c');
const sideOf = (turnKey) => turnKey.split('_')[1][0];
const roundOf = (turnKey) => parseInt(turnKey.slice(-1), 10);
const roundName = (r) => (r === 1 ? 'الافتتاح' : r === 2 ? 'الرد' : `الجولة ${r}`);
const roundShort = (r) => (r === 1 ? 'افتتاح' : r === 2 ? 'رد' : `ج${r}`);
const sideLetter = (s) => (s === 'a' ? 'أ' : 'ب');

export function mountDebate(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  const mine = ctx.creds.side;

  root.innerHTML = header('المناظرة') + `
    <div class="screen-body debate">
      <div class="chips" data-chips></div>

      <div class="timer">
        <div class="ring-wrap">
          ${ring(196, 1, TEAL)}
          <div class="ring-center">
            <div class="clock" data-clock>--:--</div>
            <div class="micro" data-total></div>
          </div>
        </div>
        <div class="turn-label" data-turnlabel></div>
      </div>

      <div class="mic-wrap">
        <button class="mic" data-mic type="button"><span class="mic-glyph" data-mic-glyph></span></button>
        <div class="mic-label" data-mic-label></div>
      </div>

      <div class="turns-panel">
        <div class="panel-head"><span>الجولات المسجّلة</span></div>
        <div class="turns-list" data-turns></div>
      </div>

      <div class="dots" data-dots></div>

      <div class="finish">
        <button class="btn btn-ghost btn-sm" data-finish type="button">طلب إنهاء المناظرة</button>
        <div class="micro-2" data-finish-hint>يتطلب موافقة الطرفين</div>
      </div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const clockEl = $('[data-clock]');
  const totalEl = $('[data-total]');
  const ringEl = root.querySelector('.ring-progress');
  const turnLabel = $('[data-turnlabel]');
  const micBtn = $('[data-mic]');
  const micGlyph = $('[data-mic-glyph]');
  const micLabel = $('[data-mic-label]');
  const dotsEl = $('[data-dots]');
  const turnsEl = $('[data-turns]');
  const finishBtn = $('[data-finish]');
  const finishHint = $('[data-finish-hint]');

  let lastState = null;
  let chipsBuilt = false;
  let anchor = null;      // { deadline, serverNow, perf, total }
  let raf = null;

  // --- countdown (server-anchored, ticked locally) -----------------------
  function remainingMs() {
    if (!anchor) return 0;
    const rem = anchor.deadline - (anchor.serverNow + (performance.now() - anchor.perf));
    return Math.max(0, Math.min(anchor.total, rem));
  }
  function paintTimer() {
    const rem = remainingMs();
    clockEl.textContent = fmtClock(rem);
    const frac = anchor && anchor.total ? rem / anchor.total : 0;
    if (ringEl) ringEl.setAttribute('stroke-dashoffset', (CIRC * (1 - frac)).toFixed(1));
    if (rem > 0) raf = requestAnimationFrame(paintTimer);
    else raf = null;
  }
  function restartTimer() {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(paintTimer);
  }

  // --- recording ---------------------------------------------------------
  let rec = null;
  let recording = false;
  let uploading = false;

  function myTurn() {
    const ct = lastState && lastState.current_turn;
    return !!ct && sideOf(ct) === mine && !uploading;
  }

  // Live elapsed counter while recording (started by TurnRecorder.onStart, i.e.
  // when the mic is actually capturing — not while the permission prompt is up).
  let recTick = null;
  let recStartedAt = 0;
  function startRecTick() {
    recStartedAt = performance.now();
    const paint = () => {
      micLabel.textContent = `جارٍ التسجيل ${fmtClock(performance.now() - recStartedAt)} — اضغط للإرسال`;
    };
    paint();
    recTick = setInterval(paint, 500);
  }
  function stopRecTick() {
    if (recTick) { clearInterval(recTick); recTick = null; }
  }

  function setMic(kind) {
    // kind: 'idle' | 'recording' | 'uploading' | 'waiting'
    if (kind !== 'recording') stopRecTick();
    micBtn.className = `mic mic-${kind} mic-${mine}`;
    micBtn.style.setProperty('--mic-color', sideColor(mine));
    if (kind === 'recording') {
      // Stop-square glyph on the solid fill: the button now reads "tap to stop".
      micGlyph.innerHTML = stopIcon(30, sideInk(mine));
      micLabel.textContent = 'جارٍ التسجيل…';
      micLabel.style.color = sideColor(mine);
    } else if (kind === 'uploading') {
      micGlyph.innerHTML = micIcon(34, sideColor(mine), 1.8);
      micLabel.textContent = 'جارٍ الإرسال…';
      micLabel.style.color = 'var(--muted)';
    } else if (kind === 'waiting') {
      const other = mine === 'a' ? 'b' : 'a';
      const name = lastState ? lastState.debaters[activeSide()].name : '';
      micGlyph.innerHTML = micIcon(34, 'var(--muted-2)', 1.8);
      micLabel.textContent = name ? `دور ${name}…` : 'بانتظار الطرف الآخر…';
      micLabel.style.color = 'var(--muted)';
    } else {
      micGlyph.innerHTML = micIcon(34, sideColor(mine), 1.8);
      micLabel.textContent = 'اضغط للتحدث';
      micLabel.style.color = 'var(--muted)';
    }
  }

  function activeSide() {
    return lastState && lastState.current_turn ? sideOf(lastState.current_turn) : mine;
  }

  function refreshMic() {
    if (recording || uploading) return;
    if (!isSupported()) {
      micBtn.disabled = true;
      micGlyph.innerHTML = micIcon(34, 'var(--muted-2)', 1.8);
      micLabel.textContent = 'التسجيل غير مدعوم في هذا المتصفح';
      micLabel.style.color = 'var(--muted)';
      return;
    }
    if (myTurn()) { micBtn.disabled = false; setMic('idle'); }
    else { micBtn.disabled = true; setMic('waiting'); }
  }

  function startRec() {
    if (recording || uploading || !myTurn()) return;
    const rem = remainingMs();
    if (rem <= 400) { toast('انتهى وقت الجولة'); return; }
    recording = true;
    setMic('recording');
    rec = new TurnRecorder({
      maxMs: rem,
      onStart: () => { if (recording) startRecTick(); },
      onStop: onRecorded,
      onDiscard: onDiscarded,
      onError: () => { recording = false; refreshMic(); toast('تعذّر الوصول للميكروفون'); },
    });
    rec.start();
  }

  // Terminal no-upload outcomes (too short / canceled / empty / error). Without
  // this reset a discarded take would wedge `recording=true` forever — the orb
  // stuck on «جارٍ التسجيل» with every tap ignored.
  function onDiscarded(reason) {
    recording = false;
    refreshMic();
    if (reason === 'too_short') toast('التسجيل قصير جدًا — حاول مجددًا');
    else if (reason === 'empty' || reason === 'error') toast('تعذّر التسجيل — حاول مجددًا');
  }
  function stopRec() { if (recording && rec) rec.stop(); }

  async function onRecorded(blob, durationMs) {
    recording = false;
    uploading = true;
    setMic('uploading');
    micBtn.disabled = true;
    try {
      apply(await api.submitTurn(code, token, blob, durationMs));
    } catch (e) {
      toast(e.message || 'تعذّر إرسال التسجيل');
    } finally {
      uploading = false;
      refreshMic();
    }
  }

  // Tap to toggle: one press starts recording, the next press stops and sends.
  // (The server-side deadline still auto-stops via TurnRecorder's maxMs timer.)
  micBtn.addEventListener('click', () => {
    if (micBtn.disabled || uploading) return;
    if (recording) stopRec();
    else startRec();
  });
  micBtn.addEventListener('contextmenu', (e) => e.preventDefault());

  // --- recorded-turn playback -------------------------------------------
  const audioEl = new Audio();
  let playingTurn = null;
  const urlCache = {};
  audioEl.onended = audioEl.onpause = () => { playingTurn = null; markPlaying(); };

  function markPlaying() {
    turnsEl.querySelectorAll('[data-play]').forEach((b) => {
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
      playingTurn = turn;
      markPlaying();
    } catch { toast('تعذّر تشغيل التسجيل'); }
  }
  turnsEl.addEventListener('click', (e) => {
    const b = e.target.closest('[data-play]');
    if (b) togglePlay(b.getAttribute('data-play'));
  });

  // --- finish ------------------------------------------------------------
  finishBtn.addEventListener('click', async () => {
    finishBtn.disabled = true;
    try { apply(await api.finish(code, token)); }
    catch (e) { finishBtn.disabled = false; toast(e.message || 'تعذّر الطلب'); }
  });

  // --- render ------------------------------------------------------------
  function renderChips(state) {
    const a = state.debaters.a, b = state.debaters.b;
    root.querySelector('[data-chips]').innerHTML = `
      <div class="chip chip-a"><b>${esc(a.name || 'الطرف الأول')}</b><span>${esc(a.claim || '')}</span></div>
      <div class="chip chip-b"><b>${esc(b.name || 'الطرف الثاني')}</b><span>${esc(b.claim || '')}</span></div>`;
  }

  function renderDots(state) {
    dotsEl.innerHTML = state.turn_order.map((tk, i) => {
      const s = sideOf(tk);
      const st = i < state.turn_index ? 'done' : i === state.turn_index ? 'current' : 'future';
      return `<div class="dot-item">
        <i class="tdot tdot-${st} tdot-${s}"></i>
        <span class="tdot-label ${st === 'future' ? 'is-future' : ''}">${roundShort(roundOf(tk))} ${sideLetter(s)}</span>
      </div>`;
    }).join('');
  }

  // Live transcript: the poll re-renders this panel, so text appears as the
  // background transcription of each turn completes.
  function transcriptHtml(t) {
    const tr = t.transcript;
    if (!tr || t.forfeited) return '';
    if (tr.status === 'pending') return '<div class="turn-text turn-text-dim">جارٍ نسخ التسجيل…</div>';
    if (tr.status === 'failed') return '<div class="turn-text turn-text-dim">تعذّر نسخ هذه الجولة</div>';
    const text = (tr.segments || []).map((s) => esc(s.text)).join(' ');
    return text ? `<div class="turn-text">${text}</div>` : '';
  }

  function renderTurns(state) {
    if (!state.turns.length) {
      turnsEl.innerHTML = '<div class="turns-empty">لم تُسجَّل أي جولة بعد.</div>';
      return;
    }
    turnsEl.innerHTML = state.turns.map((t) => {
      const name = state.debaters[t.debater].name || (t.debater === 'a' ? 'الطرف الأول' : 'الطرف الثاني');
      const right = t.forfeited
        ? '<span class="turn-forfeit">لم تُسجَّل</span>'
        : `<button class="turn-play" data-play="${t.turn}" aria-label="تشغيل">${playIcon(15)}</button>`;
      return `<div class="turn-bubble turn-bubble-col">
        <div class="turn-row">
          <div class="turn-meta"><span class="turn-name turn-${t.debater}">${esc(name)}</span>
            <span class="micro-2">${roundShort(roundOf(t.turn))} ${sideLetter(t.debater)}</span></div>
          ${right}</div>
        ${transcriptHtml(t)}</div>`;
    }).join('');
    markPlaying();
  }

  function apply(state) {
    lastState = state;
    if (!chipsBuilt) { renderChips(state); chipsBuilt = true; }

    // timer anchor
    const secs = state.format.turn_seconds;
    totalEl.textContent = `من ${fmtClock(secs * 1000)}`;
    if (state.turn_deadline_at && state.current_turn) {
      anchor = {
        deadline: Date.parse(state.turn_deadline_at),
        serverNow: Date.parse(state.server_now),
        perf: performance.now(),
        total: secs * 1000,
      };
      const col = sideColor(sideOf(state.current_turn));
      if (ringEl) ringEl.setAttribute('stroke', col);
      const r = roundOf(state.current_turn);
      const nm = state.debaters[sideOf(state.current_turn)].name || '';
      turnLabel.textContent = `دور ${nm} — جولة ${roundName(r)}`;
      turnLabel.style.color = col;
      restartTimer();
    }

    renderDots(state);
    renderTurns(state);
    refreshMic();

    // finish state
    const iAsked = state.finish_requested[mine];
    const otherAsked = state.finish_requested[mine === 'a' ? 'b' : 'a'];
    finishBtn.disabled = iAsked;
    finishBtn.textContent = iAsked ? 'طلبت الإنهاء' : 'طلب إنهاء المناظرة';
    finishHint.textContent = otherAsked && !iAsked
      ? 'الطرف الآخر طلب الإنهاء — وافق لإنهاء المناظرة'
      : 'يتطلب موافقة الطرفين';
    finishHint.classList.toggle('hint-active', otherAsked && !iAsked);
  }

  return {
    update: apply,
    unmount() {
      if (raf) cancelAnimationFrame(raf);
      stopRecTick();
      if (recording && rec) rec.cancel();
      try { audioEl.pause(); } catch { /* ignore */ }
      Object.values(urlCache).forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* ignore */ } });
    },
  };
}
