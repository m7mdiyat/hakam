import { header, toast } from '../components.js';
import { mic as micIcon, volume, ring, CIRC } from '../icons.js';
import { esc } from '../ui.js';
import { api } from '../api.js';
import { TurnRecorder, warmMic, releaseMic } from '../recorder.js';
import { createLiveLink } from '../rtc.js';
import { primeSfx, sfxReveal, sfxGo, sfxTick, sfxTimeUp } from '../sfx.js';

const sideColor = (s) => (s === 'a' ? 'var(--teal)' : 'var(--coral)');
const sideHex = (s) => (s === 'a' ? '#3FB8AF' : '#F2735F');
const clock = (secs) => `0:${String(Math.max(0, secs)).padStart(2, '0')}`;

// سجال، the optional open-mic closing round. Two phases the poll drives:
//   sijal_offer -> the «⚡ وقت السجال» announcement + a ticking accept window
//   sijal       -> both mics live (P2P) + each records its own stream
// The recording is what reaches the judge (isolated, per device); the live
// link only lets the two hear each other during the spar. Fully additive:
// nothing here can change a score (the backend keeps سجال out of scoring).
export function mountSijal(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  const mine = ctx.creds.side;
  const opp = mine === 'a' ? 'b' : 'a';
  let phase = null;          // 'offer' | 'round'
  let responded = false;     // I tapped accept/skip (or the window closed on me)
  let recorder = null;
  let live = null;
  let uploaded = false;
  let ticker = null;
  let offerTicker = null;
  let lastTick = -1;
  let lastStateRef = null;

  const nameOf = (st, s) =>
    (st && st.debaters[s] && st.debaters[s].name)
      || (s === 'a' ? 'المتناظر الأول' : 'المتناظر الثاني');

  function clearTickers() {
    if (ticker) { clearInterval(ticker); ticker = null; }
    if (offerTicker) { clearInterval(offerTicker); offerTicker = null; }
  }
  function teardown() {
    clearTickers();
    if (recorder) { try { recorder.cancel(); } catch { /* noop */ } recorder = null; }
    if (live) { live.destroy(); live = null; }
  }

  // ---- offer phase --------------------------------------------------------
  function renderOffer(st) {
    const sj = st.sijal || {};
    root.innerHTML = header('سجال') + `
      <div class="screen-body sijal-offer">
        <div class="sijal-flash" data-flash></div>
        <div class="sijal-blast in" data-blast>
          <div class="sijal-bolt-wrap">
            <span class="sijal-shock"></span>
            <div class="sijal-bolt">⚡</div>
          </div>
          <div class="sijal-clash">
            <span class="sijal-side a" style="--c:var(--teal)"></span>
            <span class="sijal-vs">×</span>
            <span class="sijal-side b" style="--c:var(--coral)"></span>
          </div>
          <h1 class="sijal-title">وقتُ السِّجال</h1>
          <p class="sijal-sub">جولة ختامية بمِيكروفون مفتوح، ${sj.seconds || 60} ثانية
            تقولان فيها ما شئتما. تُعرض للحَكَم، ولا تُغيّر الدرجة.</p>
        </div>

        <div class="sijal-status" data-status></div>
        <div class="sijal-decide" data-decide></div>
      </div>`;

    // The reveal fires ONCE on entering the offer (not on every poll).
    primeSfx();
    sfxReveal();
    requestAnimationFrame(() => {
      const f = root.querySelector('[data-flash]');
      if (f) { f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
    });
    paintOffer(st);
    startOfferCountdown(st);
  }

  function paintOffer(st) {
    const sj = st.sijal || {};
    const mineAcc = sj[`${mine}_accepted`];
    const oppAcc = sj[`${opp}_accepted`];
    const statusEl = root.querySelector('[data-status]');
    if (statusEl) statusEl.innerHTML = `
      <span class="sijal-chip ${mineAcc === true ? 'yes' : mineAcc === false ? 'no' : ''}">
        أنت: ${mineAcc === true ? 'مستعدّ' : mineAcc === false ? 'تخطّيت' : 'بانتظار قرارك'}</span>
      <span class="sijal-chip ${oppAcc === true ? 'yes' : oppAcc === false ? 'no' : ''}" style="--c:${sideColor(opp)}">
        ${esc(nameOf(st, opp))}: ${oppAcc === true ? 'مستعدّ' : oppAcc === false ? 'تخطّى' : 'لم يقرّر بعد'}</span>`;

    const decideEl = root.querySelector('[data-decide]');
    if (!decideEl) return;
    if (responded) {
      decideEl.innerHTML = `<p class="sijal-wait">${mineAcc
        ? 'بانتظار خصمك… يبدأ السِّجال حين يستعدّ الطرفان.'
        : 'ينتقل الحَكَم إلى إصدار الحُكم…'}</p>`;
      return;
    }
    decideEl.innerHTML = `
      <div class="sijal-timer" data-timer></div>
      <div class="sijal-actions">
        <button class="btn btn-primary sijal-go" data-accept type="button">
          <span class="sijal-go-ico">⚡</span> ابدأ السِّجال</button>
        <button class="btn btn-ghost" data-skip type="button">تخطّي، أصدر الحُكم</button>
      </div>`;
    decideEl.querySelector('[data-accept]').addEventListener('click', () => respond(true));
    decideEl.querySelector('[data-skip]').addEventListener('click', () => respond(false));
  }

  function startOfferCountdown(st) {
    const sj = st.sijal || {};
    const dl = sj.offer_deadline_at ? Date.parse(sj.offer_deadline_at) : null;
    if (offerTicker) clearInterval(offerTicker);
    offerTicker = setInterval(() => {
      const timerEl = root.querySelector('[data-timer]');
      if (!dl || !timerEl) return;
      const secs = Math.ceil((dl - Date.now()) / 1000);
      if (secs <= 0) {
        clearInterval(offerTicker); offerTicker = null;
        // Window closed: stop offering, show the transition. The next poll
        // flips the room to deliberating and swaps to the verdict screen.
        responded = true;
        paintOffer(st);
        return;
      }
      timerEl.innerHTML = `تنتهي فرصة السِّجال خلال
        <b class="num ${secs <= 5 ? 'hot' : ''}">${clock(secs)}</b>`;
    }, 200);
  }

  async function respond(accept) {
    responded = true;
    if (accept) { primeSfx(); sfxGo(); }
    // Optimistic: repaint immediately so the tap feels instant.
    if (lastStateRef) paintOffer(lastStateRef);
    try {
      const st = await api.sijalRespond(code, token, accept);
      apply(st);
    } catch (e) {
      // 409 = the offer window already closed server-side. That's not an
      // error to the user — the room is heading to the verdict; just show the
      // transition instead of a scary «تعذّر».
      if (e.status === 409) { if (lastStateRef) paintOffer(lastStateRef); return; }
      responded = false;
      if (lastStateRef) paintOffer(lastStateRef);
      toast('تعذّر الإرسال، حاول مجددًا');
    }
  }

  // ---- round phase --------------------------------------------------------
  function startRound(st) {
    root.innerHTML = header('سجال') + `
      <div class="screen-body sijal-round">
        <div class="sijal-live-row" data-live hidden>
          <span class="live-dot" data-live-dot></span>
          <span class="live-status" data-live-status></span>
          <button class="live-pill live-enable" data-live-enable type="button" hidden>
            <span class="live-ico">${volume(15)}</span>اضغط لتفعيل صوت الخصم</button>
        </div>

        <div class="sijal-ring-wrap" data-ringwrap>
          ${ring(190, 1, sideHex(mine))}
          <div class="sijal-ring-face">
            <div class="sijal-clock num" data-clock>0:00</div>
            <div class="sijal-mic"><span class="sijal-mic-ico">${micIcon(26, sideHex(mine), 2)}</span></div>
          </div>
          <canvas class="sijal-wave" data-wave width="300" height="46"></canvas>
        </div>

        <div class="sijal-names">
          <span style="color:${sideColor(mine)}">أنت، ميكروفونك مفتوح</span>
          <span style="color:${sideColor(opp)}">${esc(nameOf(st, opp))}</span>
        </div>
        <p class="sijal-hint" data-hint>تحدّث بحُرّية حتى ينتهي العدّاد، يُسجَّل صوتك ويصل للحَكَم.</p>
      </div>`;

    const sj = st.sijal || {};
    const total = (sj.seconds || 60) * 1000;
    const deadline = sj.deadline_at ? Date.parse(sj.deadline_at) : Date.now() + total;
    lastTick = -1;
    primeSfx();

    if (typeof RTCPeerConnection !== 'undefined') {
      live = createLiveLink({ code, token, side: mine, onStatus: renderLive });
      renderLive({ state: 'connecting', needsGesture: false });
      root.querySelector('[data-live-enable]').addEventListener('click', () => live && live.resumeAudio());
      if (st.rtc) live.onSignal(st.rtc);
    }

    recorder = new TurnRecorder({
      maxMs: Math.max(1000, deadline - Date.now()),
      onStart: () => {
        if (recorder && recorder.stream && live) {
          const t = recorder.stream.getAudioTracks()[0];
          if (t) live.attachMic(t);
        }
      },
      onStop: onRoundRecorded,
      onDiscard: () => finishWaiting('لم يُلتقط صوت، انتقل الحَكَم إلى الحُكم'),
      onLevel: drawLevel,
      onError: () => finishWaiting('تعذّر فتح الميكروفون، انتقل الحَكَم إلى الحُكم'),
    });
    recorder.start();

    const clockEl = root.querySelector('[data-clock]');
    const ringEl = root.querySelector('.ring-progress');
    ticker = setInterval(() => {
      const rem = Math.max(0, deadline - Date.now());
      const secs = Math.ceil(rem / 1000);
      clockEl.textContent = clock(secs);
      clockEl.classList.toggle('hot', secs <= 5 && secs > 0);
      if (ringEl) ringEl.setAttribute('stroke-dashoffset', (CIRC * (1 - rem / total)).toFixed(1));
      if (secs !== lastTick) {
        if (secs > 0 && secs <= 3) sfxTick(secs === 1);
        else if (secs === 0) sfxTimeUp();
        lastTick = secs;
      }
      if (rem <= 0) { clearInterval(ticker); ticker = null; }
    }, 150);
  }

  function renderLive(status) {
    const row = root.querySelector('[data-live]');
    if (!row) return;
    row.hidden = false;
    const s = status.state;
    row.querySelector('[data-live-dot]').className =
      `live-dot${s === 'connected' ? ' ok' : s === 'failed' || s === 'unreachable' ? ' err' : ' busy'}`;
    row.querySelector('[data-live-status]').textContent =
      s === 'connected' ? 'الصوت المباشر يعمل'
        : s === 'connecting' ? 'جارٍ وصل الصوت المباشر…'
          : s === 'unreachable' ? 'تعذّر الصوت المباشر، يُسجَّل كلامك على أي حال' : '';
    row.querySelector('[data-live-enable]').hidden = !status.needsGesture;
  }

  const LEVELS = new Array(30).fill(0);
  function drawLevel(rms) {
    const wave = root.querySelector('[data-wave]');
    if (!wave) return;
    const wctx = wave.getContext('2d');
    LEVELS.unshift(rms); LEVELS.pop();
    const w = wave.width, h = wave.height, gap = w / LEVELS.length;
    wctx.clearRect(0, 0, w, h);
    wctx.fillStyle = sideHex(mine);
    for (let i = 0; i < LEVELS.length; i++) {
      const bh = Math.max(3, LEVELS[i] * h);
      wctx.fillRect(w - (i + 1) * gap, (h - bh) / 2, gap - 3, bh);
    }
  }

  async function onRoundRecorded(blob) {
    if (uploaded) return;
    uploaded = true;
    finishWaiting('انتهى سِجالك، بانتظار خصمك ثم يبدأ الحُكم…');
    for (let attempt = 1; attempt <= 3; attempt++) {
      try { await api.sijalStream(code, token, blob); return; }
      catch (e) {
        if (e.status && e.status < 500) return;
        await new Promise((r) => setTimeout(r, 1500 * attempt));
      }
    }
  }

  function finishWaiting(msg) {
    if (ticker) { clearInterval(ticker); ticker = null; }
    const hint = root.querySelector('[data-hint]');
    if (hint) hint.textContent = msg;
    const face = root.querySelector('.sijal-mic');
    if (face) face.classList.add('done');
  }

  // ---- phase dispatch -----------------------------------------------------
  function apply(st) {
    lastStateRef = st;
    const want = st.state === 'sijal' ? 'round' : 'offer';
    if (want !== phase) {
      phase = want;
      responded = false;
      clearTickers();
      if (want === 'offer') renderOffer(st);
      else startRound(st);
      return;
    }
    if (phase === 'offer') paintOffer(st);        // poll update: chips only
    else if (phase === 'round' && live && st.rtc) live.onSignal(st.rtc);
  }

  warmMic();
  return {
    update: apply,
    unmount() { teardown(); releaseMic(); },
  };
}
