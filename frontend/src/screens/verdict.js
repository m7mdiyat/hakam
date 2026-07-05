// Verdict screen (Phase 2): renders the judge's stored verdict JSON verbatim —
// the server merged/validated everything; the client only displays.
// While state == deliberating with no verdict yet, shows «الحَكَم يراجع الحجج»
// and retriggers judging when the server reports null/failed (lease-guarded
// server-side, so firing is always safe).
import { header, toast, spectatorsHtml, wireSpectatorShare } from '../components.js';
import { logo, play as playIcon, stop as stopIcon } from '../icons.js';
import { esc, fmtClock } from '../ui.js';
import { api } from '../api.js';
import { creds, specCreds } from '../store.js';
import { createProofPlayer } from '../audioproof.js';

const AXES = ['logic', 'relevance', 'rebuttal', 'clarity', 'composure'];
const AXIS_AR = {
  logic: 'الاتساق المنطقي', relevance: 'الالتزام بالموضوع',
  rebuttal: 'الرد على النقاط', clarity: 'الوضوح', composure: 'الهدوء والعقلانية',
};
const SEV_AR = { low: 'منخفضة', medium: 'متوسطة', high: 'عالية' };
const BAND_AR = { decisive: 'فوز حاسم', clear: 'فوز واضح', narrow: 'فوز بفارق ضئيل' };
// Verdict v2 — everyday-Arabic renderings of the logic vocabulary.
const CLS_AR = { deductive: 'استدلال قطعي', inductive: 'استدلال ترجيحي' };
const CLS_HINT = { deductive: 'إن صحّت مقدماته لزمت نتيجته',
                   inductive: 'مقدماته ترجّح نتيجته' };
const AV_AR = { valid: 'سليم البناء', invalid: 'مختل البناء',
                strong: 'حجة قوية', weak: 'حجة ضعيفة', contested: 'تقييم متقارب' };
const AV_KIND = { valid: 'good', strong: 'good', invalid: 'bad', weak: 'bad',
                  contested: 'mid' };
const EFFECT_AR = { defeated: 'أسقطتها', weakened: 'أضعفتها', unaffected: 'لم تؤثر فيها' };
const SND_AR = { self_contradiction: 'تناقض ذاتي',
                 unsupported_load_bearing: 'ادعاء مفصلي بلا سند',
                 premise_conclusion_drift: 'انزياح عن المقدمات',
                 claim_abandonment: 'التخلي عن الدعوى' };
const isV2 = (v) => (v.schema_version || 1) >= 2;

const sideColor = (s) => (s === 'a' ? 'var(--teal)' : 'var(--coral)');
const roundOf = (tk) => parseInt(tk.slice(-1), 10);
// Debaters are identified by name + color; turns get ordinal round labels.
const ROUND_AR = ['الأولى', 'الثانية', 'الثالثة'];
const turnLabel = (tk) => `الجولة ${ROUND_AR[roundOf(tk) - 1] || roundOf(tk)}`;
const nameOf = (state, side) =>
  state.debaters[side].name || (side === 'a' ? 'الطرف الأول' : 'الطرف الثاني');
const meanScore = (scores) => {
  const vals = AXES.map((ax) => scores[ax]).filter((v) => v != null);
  return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
};

// --- pure builders (also used by the static preview harness) ----------------

function heroHtml(state, v) {
  const val = (s) => (isV2(v) ? Math.round(v.score[s]) : meanScore(v.scores[s]));
  const chips = ['a', 'b'].map((s) => `
    <span class="v-chip" style="--c:${sideColor(s)}">
      ${esc(nameOf(state, s))} <b>${val(s)}</b></span>`).join('');
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
function radarInnerSvg(v) {
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
  return `${ring(100)}${ring(66)}${ring(33)}${spokes}
    ${poly('a', '#3FB8AF')}${poly('b', '#F2735F')}${labels}`;
}

function radarHtml(v) {
  return `
    <div class="v-panel">
      <div class="panel-head"><span>مقارنة الأداء</span></div>
      <svg class="radar" viewBox="-52 -16 384 312" role="img" aria-label="مقارنة الأداء">
        ${radarInnerSvg(v)}
      </svg>
    </div>`;
}

function axesInnerHtml(state, v) {
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
  return `<div class="strip-sub"><span>معايير التقييم</span><span class="v-legends">${legend}</span></div>${rows}`;
}

function axesHtml(state, v) {
  return `
    <div class="v-panel">
      ${axesInnerHtml(state, v)}
    </div>`;
}

function emotionalityInnerHtml(state, v) {
  const rows = ['a', 'b'].map((s) => {
    const val = v.emotionality[s];
    const word = val < 35 ? 'هادئ' : val <= 65 ? 'متوازن' : 'منفعل';
    return `<div class="meter-row">
      <span class="meter-name" style="color:${sideColor(s)}">${esc(nameOf(state, s))}</span>
      <div class="meter"><i style="width:${val}%;background:${sideColor(s)}"></i></div>
      <span class="meter-val">${val} · ${word}</span>
    </div>`;
  }).join('');
  return `<div class="strip-sub"><span>المؤشر العاطفي</span></div>${rows}`;
}

function emotionalityHtml(state, v) {
  return `
    <div class="v-panel">
      ${emotionalityInnerHtml(state, v)}
    </div>`;
}

// ---- Verdict v2: Section 1 (تحليل الحجج) + Section 2 (صحة القول) -----------

const playChip = (key, audio) => (audio ? `
  <button class="proof-btn proof-sm" type="button" data-proof="${key}"
    data-turn="${audio.turn}" data-start="${audio.start_s}" data-end="${audio.end_s}">
    <span class="proof-icon">${playIcon(11, 'currentColor')}</span></button>` : '');

function argCardHtml(state, side, arg, rebuttedBy) {
  const cls = arg.classification;
  const chips = `
    <span class="arg-tag">${arg.weight === 'primary' ? 'الحجة الرئيسية' : 'حجة فرعية'}</span>
    <span class="arg-tag arg-cls" title="${CLS_HINT[cls.type]}">${CLS_AR[cls.type]}${cls.tentative ? ' · تصنيف تقريبي' : ''}</span>
    <span class="arg-verdict av-${AV_KIND[arg.verdict]}">${AV_AR[arg.verdict]}</span>`;
  const premises = arg.premises.map((p, i) => `
    <div class="arg-line">
      <span class="arg-line-label">مقدمة</span>
      <span class="arg-quote">«${esc(p.quote)}»</span>
      ${p.external ? '<span class="arg-ext">واقعة خارجية</span>' : ''}
      ${playChip(`pr-${arg.id}-${i}`, p.audio)}
    </div>`).join('');
  const implicit = arg.implicit_premises.map((ip) => `
    <div class="arg-line arg-implicit">
      <span class="arg-line-label">مقدمة غير منطوقة</span>
      <span>${esc(ip.text_ar)}</span>
      <span class="arg-ext arg-ext-ghost">استنتجها الحَكَم</span>
    </div>`).join('');
  const links = [];
  if (arg.rebuts) {
    links.push(`ترد على حجة ${esc(nameOf(state, side === 'a' ? 'b' : 'a'))} — ${EFFECT_AR[arg.rebuts.effect] || ''}`);
  }
  if (rebuttedBy) {
    links.push(`ردّ عليها ${esc(nameOf(state, rebuttedBy.side))} — ${EFFECT_AR[rebuttedBy.effect] || ''}`);
  }
  return `
    <div class="arg-card" id="arg-${arg.id}" style="--c:${sideColor(side)}">
      <div class="arg-chips">${chips}${arg.unanswered ? '<span class="arg-badge">بقيت بلا ردّ</span>' : ''}</div>
      <div class="arg-line arg-concl">
        <span class="arg-line-label">النتيجة</span>
        <span class="arg-quote">«${esc(arg.conclusion.quote)}»</span>
        ${playChip(`co-${arg.id}`, arg.conclusion.audio)}
      </div>
      ${premises}${implicit}
      ${arg.failure_point_ar ? `<div class="arg-fail">موضع الخلل: ${esc(arg.failure_point_ar)}</div>` : ''}
      ${links.length ? `<div class="arg-links">${links.join(' · ')}</div>` : ''}
    </div>`;
}

function analysisHtml(state, v) {
  // Cross-links: who rebutted whom (drawn from the opponent's rebuts fields).
  const rebuttedBy = {};
  ['a', 'b'].forEach((s) => v.analysis[s].arguments.forEach((arg) => {
    if (arg.rebuts) rebuttedBy[arg.rebuts.target_id] = { side: s, effect: arg.rebuts.effect };
  }));
  const blocks = ['a', 'b'].map((s) => {
    const m = v.analysis[s];
    const assertions = m.unsupported_assertions.length ? `
      <div class="arg-empty" style="--c:${sideColor(s)}">
        <div class="arg-empty-title">قدّم رأيًا بلا مقدمات تدعمه</div>
        ${m.unsupported_assertions.map((u, i) => `
          <div class="arg-line"><span class="arg-quote">«${esc(u.quote)}»</span>
          ${playChip(`ua-${s}-${i}`, u.audio)}</div>`).join('')}
      </div>` : '';
    const cards = m.arguments.map((arg) => argCardHtml(state, s, arg, rebuttedBy[arg.id])).join('');
    return `
      <div class="arg-block">
        <div class="arg-owner" style="color:${sideColor(s)}">${esc(nameOf(state, s))}</div>
        ${assertions}${cards || '<div class="v-empty">لم تُستخرج حجج بنيوية</div>'}
      </div>`;
  }).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>تحليل الحجج</span></div>
      ${blocks}
    </div>`;
}

function soundnessHtml(state, v) {
  if (!v.soundness.length) return '';
  const cards = v.soundness.map((s, i) => `
    <div class="fal-card" style="--c:${sideColor(s.speaker)}">
      <div class="fal-head"><span class="fal-name">${esc(s.name_ar || SND_AR[s.type] || '')}</span></div>
      <div class="fal-meta">${esc(nameOf(state, s.speaker))}</div>
      ${s.quotes.map((q, j) => `
        <blockquote class="fal-quote">«${esc(q.quote)}»
          ${playChip(`sn-${i}-${j}`, q.audio)}</blockquote>`).join('')}
      <div class="fal-why">${esc(s.explanation_ar)}</div>
    </div>`).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>تماسك الموقف</span></div>
      <div class="fal-list">${cards}</div></div>`;
}

function externalHtml(state, v) {
  if (!v.external_claims.length) return '';
  const rows = v.external_claims.map((e, i) => `
    <div class="ext-row">
      <span class="turn-name turn-${e.speaker}">${esc(nameOf(state, e.speaker))}</span>
      <span class="ext-claim">${esc(e.claim_ar)}</span>
    </div>`).join('');
  return `
    <div class="v-panel">
      <div class="panel-head"><span>وقائع استند إليها القول</span></div>
      ${rows}
      <div class="ext-note">لا يفصل الحَكَم في صحة هذه الوقائع أو خطئها.</div>
    </div>`;
}

// Demoted general assessment: axes + emotionality + radar, collapsed.
function assessmentStripHtml(state, v) {
  return `
    <div class="v-panel">
      <button class="v-collapse-head" type="button" data-collapse>
        <span>التقييم العام</span><span class="chev">▾</span>
      </button>
      <div class="v-collapse-body" hidden>
        ${axesInnerHtml(state, v)}
        ${emotionalityInnerHtml(state, v)}
        <svg class="radar" viewBox="-52 -16 384 312" role="img" aria-label="مقارنة الأداء">
          ${radarInnerSvg(v)}
        </svg>
      </div>
    </div>`;
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
      <div class="fal-meta">${esc(nameOf(state, f.speaker))} · ${turnLabel(f.turn)}${
        f.argument_id ? ` · <button class="linklike fal-link" type="button" data-goto="arg-${f.argument_id}">ضمن حجته — اعرضها</button>` : ''}</div>
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

// Full transcript, collapsed by default: every turn in order with continuous
// playback (shared proof player + blob cache) and the joined transcript text.
function transcriptPanelHtml(state) {
  const rows = state.turns.map((t) => {
    const name = esc(nameOf(state, t.debater));
    const label = turnLabel(t.turn);
    if (t.forfeited) {
      return `<div class="tr-turn">
        <div class="tr-head"><span class="turn-name turn-${t.debater}">${name}</span>
          <span class="micro-2">${label}</span><span class="turn-forfeit">لم يُسجَّل</span></div>
      </div>`;
    }
    const tr = t.transcript;
    const text = tr && tr.status === 'ok'
      ? tr.segments.map((s) => esc(s.text)).join(' ')
      : `<span class="micro-2">${tr && tr.reason === 'no_speech'
        ? 'لم يلتقط الميكروفون كلامًا في هذه المداخلة'
        : 'تعذّر نسخ هذه المداخلة'}</span>`;
    const dur = t.duration_s ? fmtClock(t.duration_s * 1000) : 'تشغيل';
    return `<div class="tr-turn">
      <div class="tr-head">
        <span class="turn-name turn-${t.debater}">${name}</span>
        <span class="micro-2">${label}</span>
        <button class="proof-btn" type="button" data-proof="tr-${t.turn}" data-turn="${t.turn}"
          data-start="0"${t.duration_s ? ` data-end="${t.duration_s}"` : ''}>
          <span class="proof-icon">${playIcon(13, 'currentColor')}</span> ${dur}
        </button>
      </div>
      <div class="tr-text">${text}</div>
    </div>`;
  }).join('');
  return `
    <div class="v-panel">
      <button class="v-collapse-head" type="button" data-collapse>
        <span>النص الكامل للمناظرة</span><span class="chev">▾</span>
      </button>
      <div class="v-collapse-body" hidden>${rows}</div>
    </div>`;
}

export function verdictHtml(state, spectator = false) {
  const v = state.verdict;
  // v2: the argument analysis is the main event; axes/radar demote to a
  // collapsed strip. v1 docs (≤24h old) render the classic layout.
  const middle = isV2(v) ? `
      ${analysisHtml(state, v)}
      ${fallaciesHtml(state, v)}
      ${soundnessHtml(state, v)}
      ${externalHtml(state, v)}
      ${assessmentStripHtml(state, v)}` : `
      ${radarHtml(v)}
      ${axesHtml(state, v)}
      ${emotionalityHtml(state, v)}
      ${fallaciesHtml(state, v)}
      ${droppedHtml(state, v)}`;
  return `
    <div class="screen-body verdict">
      ${heroHtml(state, v)}
      ${middle}
      ${momentHtml(v)}
      ${tipsHtml(state, v)}
      ${transcriptPanelHtml(state)}
      <div data-spectators></div>
      <div class="verdict-actions">
        <button class="btn btn-gold" data-share type="button">شارك الحُكْم</button>
        ${spectator ? '' : `
        <button class="btn btn-ghost" data-rematch type="button">أعد المناظرة مع نفس الخصم</button>
        <button class="linklike" data-new type="button">موضوع جديد</button>`}
      </div>
    </div>`;
}

// Deliberation steps, cycled while judging runs (~10-23s in production).
// 4 steps x 4.5s = one 18s sweep — lands mid-cycle for most verdicts, loops
// gracefully for slow ones, and the verdict interrupts instantly either way.
export const DELIB_STEPS = [
  'الحَكَم يراجع الحجج',
  'يبحث عن مغالطات منطقية',
  'يقيّم قوة كل حجة',
  'يحسب النتيجة النهائية',
];
export const DELIB_STEP_MS = 4500;

export function deliberatingHtml(state, failed) {
  return `
    <div class="screen-body screen-center verdict">
      <div class="verdict-hero delib-hero">
        <div class="delib-scale">${logo(46, 'var(--gold)', 1.2)}</div>
        <div class="verdict-title delib-msg" data-delib-msg>${failed
          ? 'تعثّرت المراجعة، تجري إعادة المحاولة…' : DELIB_STEPS[0]}</div>
        ${failed ? '' : `<div class="delib-dots" data-delib-dots>${
          DELIB_STEPS.map((_, i) => `<i class="${i === 0 ? 'on' : ''}"></i>`).join('')}</div>`}
        <div class="verdict-topic">${esc(state.topic)}</div>
      </div>
    </div>`;
}

function shareText(state, v) {
  const lines = [`الحُكْم في مناظرة: ${state.topic}`];
  lines.push(v.winner == null
    ? 'النتيجة: متقاربة — لا فائز محسوم'
    : `الفائز: ${nameOf(state, v.winner)} (${BAND_AR[v.margin.band] || ''})`);
  ['a', 'b'].forEach((s) => lines.push(
    `${nameOf(state, s)}: ${isV2(v) ? Math.round(v.score[s]) : meanScore(v.scores[s])}/100`));
  if (v.reasoning_ar) lines.push(v.reasoning_ar);
  lines.push('thehakam.com');
  return lines.join('\n');
}

// --- mount -------------------------------------------------------------------

export function mountVerdict(root, ctx) {
  const { code } = ctx;
  const token = ctx.creds.token;
  const spectator = ctx.role === 'spectator';
  let mode = null;              // 'wait' | 'verdict'
  let lastRetrigger = 0;
  let player = null;
  let delibTimer = null;
  let delibIdx = 0;
  let followedRematch = false;
  wireSpectatorShare(root, code);

  // Move to the rematch room. Debater tokens carry over server-side, so each
  // client keeps its own seat: same token, same side, new code. A spectator
  // has no seat there — they re-join the new room with their saved name.
  function goRematch(newCode) {
    if (followedRematch) return;
    followedRematch = true;
    if (spectator) {
      const name = (specCreds.get(code) || {}).name || 'مشاهد';
      api.spectate(newCode, name)
        .then((r) => {
          specCreds.set(newCode, r.token, name);
          ctx.navigate(`/s/${newCode}`);
        })
        .catch(() => { followedRematch = false; /* next poll retries */ });
      return;
    }
    creds.set(newCode, token, ctx.creds.side);
    ctx.navigate(`/r/${newCode}`);
  }

  function stopDelibCycle() {
    if (delibTimer) { clearInterval(delibTimer); delibTimer = null; }
  }
  function startDelibCycle() {
    stopDelibCycle();
    delibIdx = 0;
    delibTimer = setInterval(() => {
      const msg = root.querySelector('[data-delib-msg]');
      if (!msg) { stopDelibCycle(); return; }
      delibIdx = (delibIdx + 1) % DELIB_STEPS.length;
      msg.classList.add('delib-fade');
      setTimeout(() => {
        msg.textContent = DELIB_STEPS[delibIdx];
        msg.classList.remove('delib-fade');
        root.querySelectorAll('[data-delib-dots] i').forEach(
          (d, i) => d.classList.toggle('on', i === delibIdx));
      }, 250);
    }, DELIB_STEP_MS);
  }

  function markProofs() {
    const active = player ? player.active() : null;
    root.querySelectorAll('[data-proof]').forEach((b) => {
      const on = b.getAttribute('data-proof') === active;
      b.classList.toggle('playing', on);
      b.querySelector('.proof-icon').innerHTML = on ? stopIcon(13, 'currentColor') : playIcon(13, 'currentColor');
    });
  }

  function renderVerdict(state) {
    root.innerHTML = header('الحُكْم') + verdictHtml(state, spectator);
    player = createProofPlayer(code, token, markProofs);

    root.addEventListener('click', (e) => {
      const g = e.target.closest('[data-goto]');
      if (g) {
        const el = root.querySelector(`#${g.getAttribute('data-goto')}`);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('arg-flash');
          setTimeout(() => el.classList.remove('arg-flash'), 1600);
        }
        return;
      }
      const c = e.target.closest('[data-collapse]');
      if (c) {
        const body = c.parentElement.querySelector('.v-collapse-body');
        body.hidden = !body.hidden;
        c.classList.toggle('open', !body.hidden);
        return;
      }
      const b = e.target.closest('[data-proof]');
      if (b) {
        const end = parseFloat(b.getAttribute('data-end'));
        player.toggle(
          b.getAttribute('data-proof'), b.getAttribute('data-turn'),
          parseFloat(b.getAttribute('data-start')) || 0, Number.isNaN(end) ? null : end,
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
    const rematchBtn = root.querySelector('[data-rematch]');
    if (rematchBtn) {
      rematchBtn.addEventListener('click', async (e) => {
        // Creator only: the server links a fresh room (same seats, same
        // tokens) and the opponent's poll follows rematch_code automatically.
        if (ctx.creds.side !== 'a') {
          toast('منشئ الجلسة فقط يبدأ الإعادة — ستنتقل تلقائيًا حين يبدأها');
          return;
        }
        e.target.disabled = true;
        try {
          const { code: newCode } = await api.rematch(code, token);
          goRematch(newCode);
        } catch (err) { e.target.disabled = false; toast(err.message || 'تعذّر بدء الإعادة'); }
      });
    }
    const newBtn = root.querySelector('[data-new]');
    if (newBtn) {
      newBtn.addEventListener('click', () => {
        creds.clear(code);
        ctx.navigate('/');
      });
    }
  }

  function maybeRetrigger(state) {
    // Server lease makes this idempotent; the client just nudges when judging
    // is missing (forfeit entry path) or failed, with a 10s cooldown.
    // Spectator tokens can't fire /judge — the debaters' clients handle it.
    if (spectator) return;
    const st = state.judging_status;
    if ((st == null || st === 'failed') && Date.now() - lastRetrigger > 10000) {
      lastRetrigger = Date.now();
      api.judge(code, token).catch(() => { /* next poll retries */ });
    }
  }

  return {
    update(state) {
      // The creator started a rematch: both clients follow it (the creator
      // already navigated from the click; this catches the opponent's poll).
      if (state.rematch_code && !followedRematch) {
        toast(spectator ? 'بدأت مناظرة جديدة — جارٍ الانتقال…'
          : 'مناظرة جديدة مع نفس الخصم — جارٍ الانتقال…');
        goRematch(state.rematch_code);
        return;
      }
      if (state.verdict) {
        // The verdict interrupts the deliberation loop immediately.
        if (mode !== 'verdict') { mode = 'verdict'; stopDelibCycle(); renderVerdict(state); }
        // The strip stays live while everyone lingers on the verdict.
        const specEl = root.querySelector('[data-spectators]');
        if (specEl) specEl.innerHTML = spectatorsHtml(state, { shareCode: code });
        return;
      }
      const failed = state.judging_status === 'failed';
      const key = failed ? 'wait-failed' : 'wait';
      if (mode !== key) {
        mode = key;
        root.innerHTML = header('الحُكْم') + deliberatingHtml(state, failed);
        if (failed) stopDelibCycle();
        else startDelibCycle();
      }
      maybeRetrigger(state);
    },
    unmount() { stopDelibCycle(); if (player) player.destroy(); },
  };
}
