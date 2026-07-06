import { header } from '../components.js';
import { toast } from '../components.js';
import { mic as micIcon, volume, ring, CIRC } from '../icons.js';
import { esc } from '../ui.js';
import { api } from '../api.js';
import { TurnRecorder, warmMic, releaseMic } from '../recorder.js';
import { createLiveLink } from '../rtc.js';

const sideColor = (s) => (s === 'a' ? 'var(--teal)' : 'var(--coral)');
const sideHex = (s) => (s === 'a' ? '#3FB8AF' : '#F2735F');

// سجال — the optional open-mic closing round. Two phases the poll drives:
//   sijal_offer -> the «⚡ وقت السجال» announcement + accept/skip
//   sijal       -> both mics live (P2P) + each records its own 60s stream
// The recording is what reaches the judge (isolated, per device); the live
// link only lets the two hear each other during the spar. Fully additive:
// nothing here can change a score (the backend keeps سجال out of scoring).
export function mountSijal(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  const mine = ctx.creds.side;
  const opp = mine === 'a' ? 'b' : 'a';
  let phase = null;          // 'offer' | 'round'
  let lastState = null;
  let responded = false;     // I tapped accept/skip
  let recorder = null;
  let live = null;
  let uploaded = false;
  let ticker = null;

  const nameOf = (st, s) =>
    (st && st.debaters[s] && st.debaters[s].name)
      || (s === 'a' ? 'المتناظر الأول' : 'المتناظر الثاني');

  function teardown() {
    if (ticker) { clearInterval(ticker); ticker = null; }
    if (recorder) { try { recorder.cancel(); } catch { /* noop */ } recorder = null; }
    if (live) { live.destroy(); live = null; }
  }

  // ---- offer phase --------------------------------------------------------
  function renderOffer(st) {
    const sj = st.sijal || {};
    const mineAcc = sj[`${mine}_accepted`];
    const oppAcc = sj[`${opp}_accepted`];
    root.innerHTML = header('سجال') + `
      <div class="screen-body sijal-offer">
        <div class="sijal-blast" data-blast>
          <div class="sijal-bolt">⚡</div>
          <div class="sijal-clash">
            <span class="sijal-side a" style="--c:var(--teal)"></span>
            <span class="sijal-vs">×</span>
            <span class="sijal-side b" style="--c:var(--coral)"></span>
          </div>
          <h1 class="sijal-title">وقتُ السِّجال</h1>
          <p class="sijal-sub">جولة ختامية بمِيكروفون مفتوح — ${sj.seconds || 60} ثانية
            تقولان فيها ما شئتما. تُعرض للحَكَم، ولا تُغيّر الدرجة.</p>
        </div>

        <div class="sijal-status">
          <span class="sijal-chip ${mineAcc === true ? 'yes' : mineAcc === false ? 'no' : ''}">
            أنت: ${mineAcc === true ? 'مستعدّ' : mineAcc === false ? 'تخطّيت' : 'بانتظار قرارك'}</span>
          <span class="sijal-chip ${oppAcc === true ? 'yes' : oppAcc === false ? 'no' : ''}" style="--c:${sideColor(opp)}">
            ${esc(nameOf(st, opp))}: ${oppAcc === true ? 'مستعدّ' : oppAcc === false ? 'تخطّى' : 'لم يقرّر بعد'}</span>
        </div>

        ${responded ? `
          <p class="sijal-wait">${mineAcc
    ? 'بانتظار خصمك… يبدأ السجال حين يستعدّ الطرفان.'
    : 'ينتقل الحَكَم إلى إصدار الحُكم…'}</p>`
    : `
          <div class="sijal-actions">
            <button class="btn btn-primary sijal-go" data-accept type="button">
              <span class="sijal-go-ico">⚡</span> ابدأ السجال</button>
            <button class="btn btn-ghost" data-skip type="button">تخطّي — أصدر الحُكم</button>
          </div>`}
      </div>`;

    if (!responded) {
      root.querySelector('[data-accept]').addEventListener('click', () => respond(true));
      root.querySelector('[data-skip]').addEventListener('click', () => respond(false));
    }
    // Re-trigger the entrance animation each mount.
    requestAnimationFrame(() => {
      const b = root.querySelector('[data-blast]');
      if (b) { b.classList.remove('in'); void b.offsetWidth; b.classList.add('in'); }
    });
  }

  async function respond(accept) {
    responded = true;
    try {
      const st = await api.sijalRespond(code, token, accept);
      apply(st);
    } catch (e) {
      responded = false;
      toast('تعذّر الإرسال — حاول مجددًا');
    }
  }

  // ---- round phase --------------------------------------------------------
  function renderRoundShell(st) {
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
          <span style="color:${sideColor(mine)}">أنت — ميكروفونك مفتوح</span>
          <span style="color:${sideColor(opp)}">${esc(nameOf(st, opp))}</span>
        </div>
        <p class="sijal-hint" data-hint>تحدّث بحُرّية حتى ينتهي العدّاد — يُسجَّل صوتك ويصل للحَكَم.</p>
      </div>`;
  }

  function startRound(st) {
    renderRoundShell(st);
    const sj = st.sijal || {};
    const total = (sj.seconds || 60) * 1000;
    const deadline = sj.deadline_at ? Date.parse(sj.deadline_at) : Date.now() + total;

    // Live link: hear each other during the spar (best-effort; the recording
    // is what matters and never depends on it).
    if (typeof RTCPeerConnection !== 'undefined') {
      live = createLiveLink({ code, token, side: mine, onStatus: renderLive });
      renderLive({ state: 'connecting', needsGesture: false });
      root.querySelector('[data-live-enable]').addEventListener('click', () => live && live.resumeAudio());
      if (sj && st.rtc) live.onSignal(st.rtc);
    }

    // Record MY mic for the whole round; the same track feeds the live link.
    recorder = new TurnRecorder({
      maxMs: Math.max(1000, deadline - Date.now()),
      onStart: () => {
        if (recorder && recorder.stream && live) {
          const t = recorder.stream.getAudioTracks()[0];
          if (t) live.attachMic(t);
        }
      },
      onStop: onRoundRecorded,
      onDiscard: () => finishWaiting('لم يُلتقط صوت — انتقل الحَكَم إلى الحُكم'),
      onLevel: drawLevel,
      onError: () => finishWaiting('تعذّر فتح الميكروفون — انتقل الحَكَم إلى الحُكم'),
    });
    recorder.start();

    const clockEl = root.querySelector('[data-clock]');
    const ringEl = root.querySelector('.ring-progress');
    ticker = setInterval(() => {
      const rem = Math.max(0, deadline - Date.now());
      const secs = Math.ceil(rem / 1000);
      clockEl.textContent = `0:${String(secs).padStart(2, '0')}`;
      if (ringEl) ringEl.setAttribute('stroke-dashoffset',
        (CIRC * (1 - rem / total)).toFixed(1));
      if (rem <= 0) { clearInterval(ticker); ticker = null; }
    }, 200);
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
          : s === 'unreachable' ? 'تعذّر الصوت المباشر — يُسجَّل كلامك على أي حال' : '';
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
    finishWaiting('انتهى سِجالك — بانتظار خصمك ثم يبدأ الحُكم…');
    for (let attempt = 1; attempt <= 3; attempt++) {
      try { await api.sijalStream(code, token, blob); return; }
      catch (e) {
        if (e.status && e.status < 500) return;   // 4xx: nothing to retry
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
    lastState = st;
    const want = st.state === 'sijal' ? 'round' : 'offer';
    if (want !== phase) {
      phase = want;
      responded = false;
      if (want === 'offer') { teardown(); renderOffer(st); }
      else startRound(st);
      return;
    }
    if (phase === 'offer') renderOffer(st);
    else if (phase === 'round' && live && st.rtc) live.onSignal(st.rtc);
  }

  warmMic();
  return {
    update: apply,
    unmount() { teardown(); releaseMic(); },
  };
}
