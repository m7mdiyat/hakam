// Verdict screen (Phase 2): renders the judge's stored verdict JSON verbatim —
// the server merged/validated everything; the client only displays.
// While state == deliberating with no verdict yet, shows «الحَكَم يراجع الحجج»
// and retriggers judging when the server reports null/failed (lease-guarded
// server-side, so firing is always safe).
import { header, toast } from '../components.js';
import { logo, play as playIcon, stop as stopIcon } from '../icons.js';
import { esc } from '../ui.js';
import { api } from '../api.js';
import { creds } from '../store.js';
import { createProofPlayer } from '../audioproof.js';

const AXES = ['logic', 'relevance', 'rebuttal', 'clarity', 'composure'];
const AXIS_AR = {
  logic: 'الاتساق المنطقي', relevance: 'الالتزام بالموضوع',
  rebuttal: 'الرد على النقاط', clarity: 'الوضوح', composure: 'الهدوء والعقلانية',
};
const SEV_AR = { low: 'منخفضة', medium: 'متوسطة', high: 'عالية' };
const BAND_AR = { decisive: 'فوز حاسم', clear: 'فوز واضح', narrow: 'فوز بفارق ضئيل' };

const sideColor = (s) => (s === 'a' ? 'var(--teal)' : 'var(--coral)');
const roundOf = (tk) => parseInt(tk.slice(-1), 10);
const turnLabel = (tk) => {
  const side = tk.split('_')[1][0] === 'a' ? 'أ' : 'ب';
  return `${roundOf(tk) === 1 ? 'افتتاح' : 'رد'} ${side}`;
};
const nameOf = (state, side) =>
  state.debaters[side].name || (side === 'a' ? 'الطرف الأول' : 'الطرف الثاني');
const meanScore = (scores) => {
  const vals = AXES.map((ax) => scores[ax]).filter((v) => v != null);
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
};

// --- pure builders (also used by the static preview harness) ----------------

function heroHtml(state, v) {
  const chips = ['a', 'b'].map((s) => `
    <span class="v-chip" style="--c:${sideColor(s)}">
      ${esc(nameOf(state, s))} <b>${meanScore(v.scores[s])}</b></span>`).join('');
  let core;
  if (v.winner == null) {
    core = `
      <div class="verdict-title">النتيجة متقاربة</div>
      <div class="verdict-sub">لم يثبت متفوّق بعد مراجعة الحجج بترتيبات مختلفة</div>`;
  } else {
    const band = BAND_AR[v.margin.band] || 'فوز';
    const qualifier = v.tier === 'medium' ? ' بعد مداولة متقاربة' : '';
    core = `
      <div class="verdict-sub">الفائز</div>
      <div class="v-winner" style="color:${sideColor(v.winner)}">${esc(nameOf(state, v.winner))}</div>
      <div class="v-band">${band}${qualifier}</div>`;
  }
  return `
    <div class="verdict-hero">
      <div class="verdict-mark">${logo(30, 'var(--gold)', 1.3)}</div>
      <div class="v-label">الحُكْم</div>
      ${core}
      <div class="verdict-topic">${esc(state.topic)}</div>
      ${v.reasoning_ar ? `<p class="v-reason">${esc(v.reasoning_ar)}</p>` : ''}
      <div class="v-chips">${chips}</div>
    </div>`;
}

// Pentagon radar — same geometry as design/hakam-design.html:
// center (140,140), vertex k at angle k·72° from top: x=140+R·sin, y=140−R·cos.
// Data radius == axis score. Inapplicable axes are skipped (shorter polygon).
function radarHtml(v) {
  const pt = (k, r) => {
    const th = (k * 72 * Math.PI) / 180;
    return `${(140 + r * Math.sin(th)).toFixed(1)},${(140 - r * Math.cos(th)).toFixed(1)}`;
  };
  const ring = (r) => `<polygon points="${AXES.map((_, k) => pt(k, r)).join(' ')}"
    fill="none" stroke="rgba(255,255,255,0.09)" stroke-width="1"/>`;
  const spokes = AXES.map((_, k) => {
    const [x, y] = pt(k, 100).split(',');
    return `<line x1="140" y1="140" x2="${x}" y2="${y}" stroke="rgba(255,255,255,0.07)"/>`;
  }).join('');
  const poly = (side, color) => {
    const pts = AXES.map((ax, k) => (v.scores[side][ax] == null ? null : pt(k, v.scores[side][ax])))
      .filter(Boolean).join(' ');
    return `<polygon points="${pts}" fill="${color}" fill-opacity="0.16"
      stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>`;
  };
  const labels = AXES.map((ax, k) => {
    const th = (k * 72 * Math.PI) / 180;
    const x = 140 + 122 * Math.sin(th);
    const y = 140 - 122 * Math.cos(th) + (k === 0 ? -4 : 5);
    const na = v.scores.a[ax] == null && v.scores.b[ax] == null;
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle"
      font-size="11" fill="${na ? 'var(--muted-2)' : 'var(--muted)'}">${AXIS_AR[ax]}</text>`;
  }).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>مقارنة الأداء</span></div>
      <svg class="radar" viewBox="-52 -16 384 312" role="img" aria-label="مقارنة الأداء">
        ${ring(100)}${ring(66)}${ring(33)}${spokes}
        ${poly('a', '#3FB8AF')}${poly('b', '#F2735F')}
        ${labels}
      </svg>
    </div>`;
}

function axesHtml(state, v) {
  const rows = AXES.map((ax) => {
    const a = v.scores.a[ax], b = v.scores.b[ax];
    const bars = (a == null && b == null)
      ? '<div class="axis-na">غير منطبق — لم تتح فرصة للرد</div>'
      : ['a', 'b'].map((s) => {
        const val = v.scores[s][ax];
        return val == null
          ? `<div class="axis-bar-row"><span class="axis-val">—</span>
               <div class="axis-bar"><i style="width:0"></i></div></div>`
          : `<div class="axis-bar-row"><span class="axis-val">${val}</span>
               <div class="axis-bar"><i style="width:${val}%;background:${sideColor(s)}"></i></div></div>`;
      }).join('');
    return `<div class="axis-row"><div class="axis-name">${AXIS_AR[ax]}</div>${bars}</div>`;
  }).join('');
  const legend = ['a', 'b'].map((s) =>
    `<span class="v-legend"><i style="background:${sideColor(s)}"></i>${esc(nameOf(state, s))}</span>`).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>معايير التقييم</span><span class="v-legends">${legend}</span></div>
      ${rows}
    </div>`;
}

function emotionalityHtml(state, v) {
  const rows = ['a', 'b'].map((s) => {
    const val = v.emotionality[s];
    const word = val < 35 ? 'هادئ' : val <= 65 ? 'متوازن' : 'منفعل';
    return `<div class="meter-row">
      <span class="meter-name" style="color:${sideColor(s)}">${esc(nameOf(state, s))}</span>
      <div class="meter"><i style="width:${val}%;background:${sideColor(s)}"></i></div>
      <span class="meter-val">${val} · ${word}</span>
    </div>`;
  }).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>المؤشر العاطفي</span></div>
      ${rows}</div>`;
}

const proofBtn = (key, audio) => (audio ? `
  <button class="proof-btn" type="button" data-proof="${key}"
    data-turn="${audio.turn}" data-start="${audio.start_s}" data-end="${audio.end_s}">
    <span class="proof-icon">${playIcon(13, 'currentColor')}</span> استمع للحظتها
  </button>` : '');

function fallaciesHtml(state, v) {
  const cards = v.fallacies.length ? v.fallacies.map((f, i) => `
    <div class="fal-card" style="--c:${sideColor(f.speaker)}">
      <div class="fal-head">
        <span class="fal-name">${esc(f.name_ar)}</span>
        <span class="fal-en">${esc(f.name_en)}</span>
        <span class="fal-sev fal-sev-${f.severity}">${SEV_AR[f.severity] || ''}</span>
      </div>
      <div class="fal-meta">${esc(nameOf(state, f.speaker))} · ${turnLabel(f.turn)}</div>
      <blockquote class="fal-quote">«${esc(f.quote)}»</blockquote>
      <div class="fal-why">${esc(f.explanation_ar)}</div>
      ${proofBtn(`fal-${i}`, f.audio)}
    </div>`).join('')
    : '<div class="v-empty">لم تُرصد مغالطات — مناظرة نظيفة 👏</div>';
  return `
    <div class="v-panel">
      <div class="panel-head"><span>سجل المغالطات</span></div>
      <div class="fal-list">${cards}</div></div>`;
}

function droppedHtml(state, v) {
  if (!v.dropped_points.length) return '';
  const rows = v.dropped_points.map((d) => `
    <div class="drop-card">
      <div class="drop-point">${esc(d.point_ar)}</div>
      <div class="drop-meta">أُثيرت في ${turnLabel(d.raised_turn)} وتُركت بلا رد من
        <b style="color:${sideColor(d.speaker)}">${esc(nameOf(state, d.speaker))}</b></div>
    </div>`).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>نقاط بلا رد</span></div>
      ${rows}</div>`;
}

function momentHtml(v) {
  if (!v.key_moment) return '';
  return `
    <div class="v-panel moment-card">
      <div class="panel-head"><span>اللحظة الفاصلة</span></div>
      <div class="moment-text">${esc(v.key_moment.description_ar)}</div>
      ${proofBtn('moment', v.key_moment.audio)}
    </div>`;
}

function tipsHtml(state, v) {
  if (!v.profiles) return '';
  const cards = ['a', 'b'].map((s) => {
    const p = v.profiles[s];
    return `
    <div class="tip-card" style="--c:${sideColor(s)}">
      <div class="tip-name">${esc(nameOf(state, s))}</div>
      <div class="tip-line"><b>الأقوى:</b> ${esc(p.strongest_ar)}</div>
      <div class="tip-line"><b>الأضعف:</b> ${esc(p.weakest_ar)}</div>
      <div class="tip-line tip-advice"><b>نصيحة الحَكَم:</b> ${esc(p.tip_ar)}</div>
    </div>`;
  }).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>نصيحة الحَكَم</span></div>
      <div class="tips-grid">${cards}</div></div>`;
}

export function verdictHtml(state) {
  const v = state.verdict;
  return `
    <div class="screen-body verdict">
      ${heroHtml(state, v)}
      ${radarHtml(v)}
      ${axesHtml(state, v)}
      ${emotionalityHtml(state, v)}
      ${fallaciesHtml(state, v)}
      ${droppedHtml(state, v)}
      ${momentHtml(v)}
      ${tipsHtml(state, v)}
      <div class="verdict-actions">
        <button class="btn btn-gold" data-share type="button">شارك الحُكْم</button>
        <button class="btn btn-ghost" data-rematch type="button">مناظرة جديدة بنفس الموضوع</button>
        <button class="linklike" data-new type="button">موضوع جديد</button>
      </div>
    </div>`;
}

export function deliberatingHtml(state, failed) {
  return `
    <div class="screen-body screen-center verdict">
      <div class="verdict-hero">
        <div class="verdict-mark v-pulse">${logo(30, 'var(--gold)', 1.3)}</div>
        <div class="verdict-title">الحَكَم يراجع الحجج</div>
        <div class="verdict-sub">${failed
          ? 'تعثّرت المراجعة، تجري إعادة المحاولة…'
          : 'يستمع للجولات، يقارن الحجج، ويتحقق من المغالطات…'}</div>
        <div class="verdict-topic">${esc(state.topic)}</div>
      </div>
    </div>`;
}

function shareText(state, v) {
  const lines = [`الحُكْم في مناظرة: ${state.topic}`];
  lines.push(v.winner == null
    ? 'النتيجة: متقاربة — لا فائز محسوم'
    : `الفائز: ${nameOf(state, v.winner)} (${BAND_AR[v.margin.band] || ''})`);
  ['a', 'b'].forEach((s) => lines.push(`${nameOf(state, s)}: ${meanScore(v.scores[s])}/100`));
  if (v.reasoning_ar) lines.push(v.reasoning_ar);
  lines.push('thehakam.com');
  return lines.join('\n');
}

// --- mount -------------------------------------------------------------------

export function mountVerdict(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  let mode = null;              // 'wait' | 'verdict'
  let lastRetrigger = 0;
  let player = null;

  function markProofs() {
    const active = player ? player.active() : null;
    root.querySelectorAll('[data-proof]').forEach((b) => {
      const on = b.getAttribute('data-proof') === active;
      b.classList.toggle('playing', on);
      b.querySelector('.proof-icon').innerHTML = on ? stopIcon(13, 'currentColor') : playIcon(13, 'currentColor');
    });
  }

  function renderVerdict(state) {
    root.innerHTML = header('الحُكْم') + verdictHtml(state);
    player = createProofPlayer(code, token, markProofs);

    root.addEventListener('click', (e) => {
      const b = e.target.closest('[data-proof]');
      if (b) {
        player.toggle(
          b.getAttribute('data-proof'), b.getAttribute('data-turn'),
          parseFloat(b.getAttribute('data-start')), parseFloat(b.getAttribute('data-end')),
        ).catch(() => toast('تعذّر تشغيل التسجيل'));
      }
    });
    root.querySelector('[data-share]').addEventListener('click', async () => {
      const text = shareText(state, state.verdict);
      try {
        if (navigator.share) await navigator.share({ text });
        else { await navigator.clipboard.writeText(text); toast('نُسخ الحُكْم'); }
      } catch { /* share sheet dismissed */ }
    });
    root.querySelector('[data-rematch]').addEventListener('click', async (e) => {
      e.target.disabled = true;
      try {
        const { code: newCode, token: t, side } = await api.createRoom(state.topic);
        creds.set(newCode, t, side);
        ctx.navigate(`/r/${newCode}`);
      } catch (err) { e.target.disabled = false; toast(err.message || 'تعذّر إنشاء الجلسة'); }
    });
    root.querySelector('[data-new]').addEventListener('click', () => {
      creds.clear(code);
      ctx.navigate('/');
    });
  }

  function maybeRetrigger(state) {
    // Server lease makes this idempotent; the client just nudges when judging
    // is missing (forfeit entry path) or failed, with a 10s cooldown.
    const st = state.judging_status;
    if ((st == null || st === 'failed') && Date.now() - lastRetrigger > 10000) {
      lastRetrigger = Date.now();
      api.judge(code, token).catch(() => { /* next poll retries */ });
    }
  }

  return {
    update(state) {
      if (state.verdict) {
        if (mode !== 'verdict') { mode = 'verdict'; renderVerdict(state); }
        return;
      }
      const failed = state.judging_status === 'failed';
      const key = failed ? 'wait-failed' : 'wait';
      if (mode !== key) { mode = key; root.innerHTML = header('الحُكْم') + deliberatingHtml(state, failed); }
      maybeRetrigger(state);
    },
    unmount() { if (player) player.destroy(); },
  };
}
