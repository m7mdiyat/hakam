import { header, spectatorsHtml, wireSpectatorShare } from '../components.js';
import { mic as micIcon, micOff, volume, volumeOff, ring, CIRC, play as playIcon, stop as stopIcon } from '../icons.js';
import { esc, fmtClock } from '../ui.js';
import { api } from '../api.js';
import { toast } from '../components.js';
import { TurnRecorder, isSupported, warmMic, releaseMic } from '../recorder.js';
import { createLiveLink } from '../rtc.js';

const TEAL = 'var(--teal)';
const CORAL = 'var(--coral)';
const sideColor = (s) => (s === 'a' ? TEAL : CORAL);
const sideHex = (s) => (s === 'a' ? '#3FB8AF' : '#F2735F');   // canvas needs concrete colors
const sideInk = (s) => (s === 'a' ? '#062B29' : '#3d120c');
const sideOf = (turnKey) => turnKey.split('_')[1][0];
const roundOf = (turnKey) => parseInt(turnKey.slice(-1), 10);
// Debaters are identified by name + color everywhere; rounds get ordinals.
const ROUND_AR = ['الأولى', 'الثانية', 'الثالثة'];
const roundLabel = (r) => `الجولة ${ROUND_AR[r - 1] || r}`;

export function mountDebate(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  const mine = ctx.creds.side;                 // null for spectators
  const spectator = ctx.role === 'spectator';  // read-only: no mic, no finish

  root.innerHTML = header(spectator ? 'مشاهدة' : 'المناظرة') + `
    <div class="screen-body debate">
      <div class="debate-topic" data-topic></div>
      <div class="chips" data-chips></div>

      ${spectator ? '' : `
      <div class="live-row" data-live hidden>
        <span class="live-dot" data-live-dot></span>
        <span class="live-status" data-live-status></span>
        <button class="live-pill" data-live-self type="button"
          title="بث صوتك المباشر للخصم — كتمه لا يؤثر على تسجيل جولتك">
          <span class="live-ico" data-live-self-ico></span><span data-live-self-txt></span>
        </button>
        <button class="live-pill" data-live-peer type="button"
          title="سماع صوت الخصم المباشر عندك">
          <span class="live-ico" data-live-peer-ico></span><span data-live-peer-txt></span>
        </button>
        <button class="live-pill live-enable" data-live-enable type="button" hidden>
          <span class="live-ico">${volume(15)}</span>تفعيل الصوت المباشر</button>
      </div>`}

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

      ${spectator ? '' : `
      <div class="mic-wrap">
        <button class="mic" data-mic type="button"><span class="mic-glyph" data-mic-glyph></span></button>
        <canvas class="wave" data-wave width="224" height="36" hidden></canvas>
        <div class="mic-label" data-mic-label></div>
      </div>`}

      <div class="turns-panel">
        <div class="panel-head"><span>الجولات المسجّلة</span></div>
        <div class="turns-list" data-turns></div>
      </div>

      <div class="dots" data-dots></div>
      <div data-spectators></div>

      ${spectator ? '' : `
      <div class="finish">
        <button class="btn btn-ghost btn-sm" data-finish type="button">طلب إنهاء المناظرة</button>
        <div class="micro-2" data-finish-hint>يتطلب موافقة الطرفين</div>
      </div>`}
    </div>`;
  wireSpectatorShare(root, code);

  const $ = (s) => root.querySelector(s);
  const ringWrap = root.querySelector('.ring-wrap');
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
  let anchor = null;      // speaking clock { deadline, serverNow, perf, total }
  let prep = null;        // prep clock { deadline, serverNow, perf, label }
  let raf = null;

  // --- live walkie-talkie link (participants only; strictly additive) -----
  // The controls live in their own container rendered ONCE at mount — the
  // poll re-renders everything around them, and the mute states must survive.
  // Two distinct pills: mic = MY voice on their speaker, speaker = THEIR
  // voice on mine. Green = flowing, red = muted.
  // Turn-aware: the SPEAKER only ever sees «صوتي …» (mute yourself), the
  // LISTENER only «أسمع الخصم / الخصم مكتوم» — one relevant control at a time.
  let lastLiveStatus = { state: 'idle', peerMuted: false, selfMuted: false, needsGesture: false };
  function renderLive(status) {
    if (status) lastLiveStatus = status;
    const row = root.querySelector('[data-live]');
    if (!row) return;
    const { state, peerMuted, selfMuted, needsGesture } = lastLiveStatus;
    row.hidden = state === 'idle';
    const dot = root.querySelector('[data-live-dot]');
    dot.className = `live-dot${state === 'connected' ? ' on' : state === 'failed' ? ' err' : ' busy'}`;
    root.querySelector('[data-live-status]').textContent =
      state === 'connecting' ? 'جارٍ الاتصال…'
        : state === 'failed' ? 'تعذّر الاتصال المباشر' : '';
    const ct = lastState && lastState.current_turn;
    const turnSide = ct ? sideOf(ct) : null;
    const selfPill = root.querySelector('[data-live-self]');
    selfPill.hidden = turnSide !== mine;               // my turn only
    selfPill.classList.toggle('is-off', selfMuted);
    root.querySelector('[data-live-self-ico]').innerHTML = selfMuted ? micOff(15) : micIcon(15, 'currentColor', 1.8);
    root.querySelector('[data-live-self-txt]').textContent = selfMuted ? 'صوتي مكتوم' : 'صوتي مسموع';
    const peerPill = root.querySelector('[data-live-peer]');
    peerPill.hidden = !turnSide || turnSide === mine;  // opponent's turn only
    peerPill.classList.toggle('is-off', peerMuted);
    root.querySelector('[data-live-peer-ico]').innerHTML = peerMuted ? volumeOff(15) : volume(15);
    root.querySelector('[data-live-peer-txt]').textContent = peerMuted ? 'الخصم مكتوم' : 'أسمع الخصم';
    root.querySelector('[data-live-enable]').hidden = !needsGesture;
  }
  const live = (!spectator && typeof RTCPeerConnection !== 'undefined')
    ? createLiveLink({ code, token, side: mine, onStatus: renderLive })
    : null;
  if (live) {
    renderLive(lastLiveStatus);
    root.querySelector('[data-live-self]').addEventListener('click', () => {
      live.setSelfMuted(!live.selfMuted);
    });
    root.querySelector('[data-live-peer]').addEventListener('click', () => {
      live.setPeerMuted(!live.peerMuted);
    });
    root.querySelector('[data-live-enable]').addEventListener('click', () => {
      live.resumeAudio();
    });
  }

  // --- countdown (server-anchored, ticked locally) -----------------------
  const anchorRemaining = (a, cap) => {
    const rem = a.deadline - (a.serverNow + (performance.now() - a.perf));
    return Math.max(0, cap != null ? Math.min(cap, rem) : rem);
  };
  function remainingMs() {
    return anchor ? anchorRemaining(anchor, anchor.total) : 0;
  }
  function paintTimer() {
    if (anchor) {
      // Speaking: the ring drains with the debater's clock.
      const rem = remainingMs();
      clockEl.textContent = fmtClock(rem);
      const frac = anchor.total ? rem / anchor.total : 0;
      if (ringEl) ringEl.setAttribute('stroke-dashoffset', (CIRC * (1 - frac)).toFixed(1));
      raf = rem > 0 ? requestAnimationFrame(paintTimer) : null;
    } else if (prep) {
      // Prep: ring stays full; the label counts down the start-your-mic window.
      const rem = anchorRemaining(prep);
      turnLabel.textContent = `${prep.label} — ${fmtClock(rem)}`;
      raf = rem > 0 ? requestAnimationFrame(paintTimer) : null;
    } else {
      raf = null;
    }
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
    return !!ct && sideOf(ct) === mine && !uploading && !lastState.processing;
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
    if (kind !== 'recording') { stopRecTick(); waveEl.hidden = true; resetWave(); }
    else waveEl.hidden = false;
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
    } else if (kind === 'hold') {
      micGlyph.innerHTML = micIcon(34, 'var(--muted-2)', 1.8);
      micLabel.textContent = 'يدوّن الحَكَم الجولة السابقة…';
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
    if (spectator || recording || uploading) return;
    if (!isSupported()) {
      micBtn.disabled = true;
      micGlyph.innerHTML = micIcon(34, 'var(--muted-2)', 1.8);
      micLabel.textContent = 'التسجيل غير مدعوم في هذا المتصفح';
      micLabel.style.color = 'var(--muted)';
      return;
    }
    if (lastState && lastState.processing) { micBtn.disabled = true; setMic('hold'); }
    else if (myTurn()) { micBtn.disabled = false; setMic('idle'); }
    else { micBtn.disabled = true; setMic('waiting'); }
  }

  // Start the server speaking clock, once. Idempotent server-side, so the
  // poll-driven self-heal in apply() can re-fire it if the first attempt was
  // lost — otherwise the server stays in the prep window and forfeits the turn
  // WHILE the debater is happily recording.
  let startReqInFlight = false;
  function ensureTurnStarted() {
    if (startReqInFlight) return;
    startReqInFlight = true;
    api.startTurn(code, token).then(apply)
      .catch(() => { /* apply() retries on the next poll */ })
      .finally(() => { startReqInFlight = false; });
  }

  function startRec() {
    if (recording || uploading || !myTurn()) return;
    // The speaking clock starts at the first mic tap (server-stamped). A later
    // tap on the same turn (e.g. after a too-short discard) resumes the clock.
    const started = lastState && lastState.turn_started;
    const rem = started ? remainingMs() : lastState.format.turn_seconds * 1000;
    if (rem <= 400) { toast('انتهى وقت الجولة'); return; }
    recording = true;
    setMic('recording');
    if (!started) ensureTurnStarted();
    rec = new TurnRecorder({
      maxMs: rem,
      onStart: () => {
        if (recording) startRecTick();
        // The live link transmits the SAME track the recorder just enabled:
        // the opponent hears exactly what is being recorded, nothing else.
        if (live && rec.stream) {
          const t = rec.stream.getAudioTracks()[0];
          if (t) live.attachMic(t);
        }
      },
      onStop: onRecorded,
      onDiscard: onDiscarded,
      onLevel: drawLevel,
      onError: (e) => {
        recording = false;
        refreshMic();
        toast(e && e.name === 'NotAllowedError'
          ? 'الوصول للميكروفون مرفوض — فعّله من إعدادات المتصفح لهذا الموقع'
          : 'تعذّر الوصول للميكروفون');
      },
    });
    rec.start();
  }

  // --- live mic level (waveform bars + orb glow) ---------------------------
  const waveEl = $('[data-wave]');
  const wctx = waveEl ? waveEl.getContext('2d') : null;  // no canvas for spectators
  const LEVELS = new Array(28).fill(0);
  function drawLevel(rms) {
    LEVELS.unshift(rms);
    LEVELS.pop();
    micBtn.style.setProperty('--level', rms.toFixed(2));
    const w = waveEl.width, h = waveEl.height, gap = w / LEVELS.length;
    wctx.clearRect(0, 0, w, h);
    wctx.fillStyle = sideHex(mine);
    for (let i = 0; i < LEVELS.length; i++) {   // newest bar enters from the right (RTL)
      const bh = Math.max(3, LEVELS[i] * h);
      wctx.fillRect(w - (i + 1) * gap, (h - bh) / 2, gap - 3, bh);
    }
  }
  function resetWave() {
    LEVELS.fill(0);
    wctx.clearRect(0, 0, waveEl.width, waveEl.height);
    micBtn.style.removeProperty('--level');
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

  // A transient network blip must not destroy a whole spoken turn: retry the
  // upload. 4xx responses are verdicts (expired / rejected audio), not blips —
  // those surface immediately. The server's submit grace absorbs the backoff.
  async function submitWithRetry(blob, durationMs, tries = 3) {
    for (let attempt = 1; ; attempt++) {
      try {
        return await api.submitTurn(code, token, blob, durationMs);
      } catch (e) {
        const transient = !e.status || e.status >= 500;
        if (!transient || attempt >= tries) throw e;
        await new Promise((r) => setTimeout(r, 1500 * attempt));
      }
    }
  }

  // The take is on its way: stop the countdown NOW rather than at the next
  // poll — the debater already spoke; watching the ring keep draining during
  // the upload reads as time being stolen. If the submit fails, the next
  // poll's apply() restores the real clock.
  function showSubmitHold() {
    anchor = null;
    prep = null;
    clockEl.textContent = '—';
    if (ringEl) ringEl.setAttribute('stroke-dashoffset', '0');
    ringWrap.classList.add('ring-hold');
    turnLabel.textContent = 'جارٍ إرسال التسجيل…';
    turnLabel.style.color = 'var(--muted)';
  }

  async function onRecorded(blob, durationMs) {
    recording = false;
    uploading = true;
    setMic('uploading');
    micBtn.disabled = true;
    showSubmitHold();
    try {
      apply(await submitWithRetry(blob, durationMs));
    } catch (e) {
      toast(e.message || 'تعذّر إرسال التسجيل');
    } finally {
      uploading = false;
      refreshMic();
    }
  }

  // Tap to toggle: one press starts recording, the next press stops and sends.
  // (The server-side deadline still auto-stops via TurnRecorder's maxMs timer.)
  if (!spectator) {
    micBtn.addEventListener('click', () => {
      if (micBtn.disabled || uploading) return;
      if (recording) stopRec();
      else startRec();
    });
    micBtn.addEventListener('contextmenu', (e) => e.preventDefault());
  }

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
  if (!spectator) {
    finishBtn.addEventListener('click', async () => {
      finishBtn.disabled = true;
      try { apply(await api.finish(code, token)); }
      catch (e) { finishBtn.disabled = false; toast(e.message || 'تعذّر الطلب'); }
    });
  }

  // --- render ------------------------------------------------------------
  // Re-rendered every poll: presence («غير متصل») changes over time.
  function renderChips(state) {
    const chip = (s) => {
      const d = state.debaters[s];
      const offline = d.online === false;   // null = unknown -> assume online
      return `<div class="chip chip-${s}${offline ? ' chip-away' : ''}">
        <b>${esc(d.name || (s === 'a' ? 'الطرف الأول' : 'الطرف الثاني'))}
          ${offline ? '<span class="chip-offline">غير متصل</span>' : ''}</b>
        <span>${esc(d.claim || '')}</span></div>`;
    };
    root.querySelector('[data-chips]').innerHTML = chip('a') + chip('b');
  }

  function renderDots(state) {
    dotsEl.innerHTML = state.turn_order.map((tk, i) => {
      const s = sideOf(tk);
      const st = i < state.turn_index ? 'done' : i === state.turn_index ? 'current' : 'future';
      return `<div class="dot-item">
        <i class="tdot tdot-${st} tdot-${s}"></i>
        <span class="tdot-label ${st === 'future' ? 'is-future' : ''}">${ROUND_AR[roundOf(tk) - 1] || roundOf(tk)}</span>
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
            <span class="micro-2">${roundLabel(roundOf(t.turn))}</span></div>
          ${right}</div>
        ${transcriptHtml(t)}</div>`;
    }).join('');
    markPlaying();
  }

  function apply(state) {
    lastState = state;
    root.querySelector('[data-topic]').textContent = state.topic;
    renderChips(state);
    if (live && state.rtc) live.onSignal(state.rtc);

    // timer anchors: speaking clock (deadline set) vs prep window (not started)
    const secs = state.format.turn_seconds;
    totalEl.textContent = `من ${fmtClock(secs * 1000)}`;
    if (state.current_turn) {
      const side = sideOf(state.current_turn);
      const col = sideColor(side);
      const nm = state.debaters[side].name || '';
      if (ringEl) ringEl.setAttribute('stroke', col);
      turnLabel.style.color = col;
      if (state.processing || uploading) {
        // Between turns (transcript being produced) — or our OWN submit still
        // in flight: the server doesn't know about the upload yet and reports
        // the turn as live, so without the `uploading` guard the 2s poll
        // repaints a draining ring right over showSubmitHold(). The debater
        // already spoke; the clock must read as stopped.
        anchor = null;
        prep = null;
        clockEl.textContent = '—';
        if (ringEl) ringEl.setAttribute('stroke-dashoffset', '0');
        ringWrap.classList.add('ring-hold');
        turnLabel.textContent = uploading ? 'جارٍ إرسال التسجيل…' : 'يدوّن الحَكَم الجولة…';
        turnLabel.style.color = 'var(--muted)';
      } else if (state.turn_deadline_at) {
        ringWrap.classList.remove('ring-hold');
        anchor = {
          deadline: Date.parse(state.turn_deadline_at),
          serverNow: Date.parse(state.server_now),
          perf: performance.now(),
          total: secs * 1000,
        };
        prep = null;
        turnLabel.textContent = `دور ${nm} — ${roundLabel(roundOf(state.current_turn))}`;
      } else {
        // Turn not started: full ring, full clock, prep countdown in the label.
        ringWrap.classList.remove('ring-hold');
        anchor = null;
        clockEl.textContent = fmtClock(secs * 1000);
        if (ringEl) ringEl.setAttribute('stroke-dashoffset', '0');
        const base = side === mine ? 'دورك — اضغط للتحدث' : `بانتظار ${nm}`;
        prep = state.turn_prep_deadline_at ? {
          deadline: Date.parse(state.turn_prep_deadline_at),
          serverNow: Date.parse(state.server_now),
          perf: performance.now(),
          label: base,
        } : null;
        turnLabel.textContent = base;
      }
      restartTimer();
    }

    // Keep a live recording glued to the SERVER clock (anchor was just updated):
    // - clock running -> re-arm the auto-stop from the authoritative remaining
    //   time (the local timer started late by the getUserMedia delay);
    // - clock NOT running -> our turns/start was lost; re-fire it (idempotent)
    //   before the prep window forfeits a turn someone is actually speaking;
    // - turn no longer ours -> it forfeited/advanced; stop recording into it.
    if (recording && rec) {
      const ct = state.current_turn;
      if (ct && sideOf(ct) === mine) {
        if (state.turn_started) rec.syncStop(remainingMs());
        else ensureTurnStarted();
      } else {
        recording = false;   // now, not in onDiscard: the next poll must not re-cancel/re-toast
        rec.cancel();
        toast('انتهى وقت الجولة — لم يُرسل التسجيل');
      }
    }

    renderDots(state);
    renderTurns(state);
    refreshMic();
    if (live) renderLive();   // pill visibility follows whose turn it is
    root.querySelector('[data-spectators]').innerHTML =
      spectatorsHtml(state, { shareCode: code });

    // finish state (participants only)
    if (!spectator) {
      const iAsked = state.finish_requested[mine];
      const otherAsked = state.finish_requested[mine === 'a' ? 'b' : 'a'];
      finishBtn.disabled = iAsked;
      finishBtn.textContent = iAsked ? 'طلبت الإنهاء' : 'طلب إنهاء المناظرة';
      finishHint.textContent = otherAsked && !iAsked
        ? 'الطرف الآخر طلب الإنهاء — وافق لإنهاء المناظرة'
        : 'يتطلب موافقة الطرفين';
      finishHint.classList.toggle('hint-active', otherAsked && !iAsked);
    }
  }

  // If the mic permission is already granted, open the shared stream now so the
  // first tap records instantly (and the debate never re-prompts). Never prompts.
  // Spectators never touch the mic at all — not even a permission query.
  if (!spectator) warmMic();

  return {
    update: apply,
    unmount() {
      if (raf) cancelAnimationFrame(raf);
      stopRecTick();
      if (recording && rec) rec.cancel();
      if (live) live.destroy();
      if (!spectator) releaseMic();   // debate over (or navigated away): let go of the device
      try { audioEl.pause(); } catch { /* ignore */ }
      Object.values(urlCache).forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* ignore */ } });
    },
  };
}
