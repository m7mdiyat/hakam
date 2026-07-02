# Hakam — Design tokens (extracted from `hakam-design.html`)

The design export is a self-unpacking bundle (JSON-escaped HTML + gzipped variable
Readex Pro woff2). These are the exact values mined from it — the single source of
truth for `frontend/src/styles/tokens.css`. Two reference frames exist in the export:

- **سطح المكتب — التصميم الأساسي** (Desktop base, 1440×900)
- **الجوّال — المرجع المتجاوب** (Mobile responsive reference, 390×~800)

The product is used on **two phones**, so we build the **mobile reference** as the
responsive app (centered, phone-width, presented as a device frame on wide screens).

## Palette

| Token | Hex / value | Used for |
|---|---|---|
| `--bg` | `#0B0B0E` | screen/app background (frame interior) |
| `--bg-page` | `#131318` | canvas behind the device frame (desktop) |
| `--surface` | `#16161B` | cards, inputs, chips |
| `--surface-2` | `#101015` | transcript panel, fallacy cards |
| `--surface-3` | `#1B1B21` | disabled button fill |
| `--ink` | `#F2EFE9` | headings / primary text |
| `--ink-2` | `#C9C5BC` | body text |
| `--ink-3` | `#E6E2D9` | quoted fallacy text |
| `--muted` | `#8A867E` | secondary labels |
| `--muted-2` | `#6E6A63` | faint / breadcrumb |
| `--muted-3` | `#A9A49A` | tagline, ghost buttons |
| **Gold** (verdict + primary CTA **only**) | | |
| `--gold` | `#C9A45C` | primary CTA fill, accents |
| `--gold-light` | `#D4B36A` | gold gradient top |
| `--gold-ink` | `#17110A` | text on gold |
| **Teal — Debater A** | | |
| `--teal` | `#3FB8AF` | A accent, A ring/mic |
| `--teal-light` | `#A9DDD9` | A claim chip text |
| `--teal-ink` | `#062B29` | mic glyph on filled teal |
| A tints | `rgba(63,184,175,0.14 / .12 / .10 / .07 / .06)` | avatar bg, chip bg, claim quote bg |
| A borders | `rgba(63,184,175,0.4 / .35 / .32 / .25)` | avatar/chip borders |
| **Coral — Debater B** | | |
| `--coral` | `#F2735F` | B accent, B ring/mic |
| `--coral-light` | `#F5B7AC` | B claim chip text |
| B tints / borders | same alphas as A with `242,115,95` | |
| **Hairlines** | `rgba(255,255,255, 0.09 / .10 / .12 / .14 / .08 / .07 / .06)` | frame, cards, inputs, pills, dividers |

## Radii

`--r-pill: 999px` · `--r-frame: 20px` (desktop) / `28px` (mobile device frame) ·
`--r-card: 16px` · `--r-input: 14px` / `--r-btn: 14px` · `--r-sm: 12px` · `--r-xs: 10px`

## Type

- Family: **Readex Pro** (self-hosted variable woff2, weight axis 300–700), fallback `'Segoe UI', sans-serif`.
- Subsets self-hosted at `frontend/public/fonts/` by unicode-range: `arabic`, `latin`, `latin-ext`, `vietnamese`.
- Weights in use: 300 (tagline), 500, 600 (labels/CTA), 700 (wordmark, scores, verdict).
- **Wordmark** حَكَم: 118px/700 desktop, **72px/700 mobile**, line-height 1.5.
- **Tagline** «لتكن الحُجّة هي الفيصل»: 23px/300 desktop, 17px/300 mobile, color `--muted-3`.
- Timers/scores use **tabular (Western) numerals**: `font-feature-settings:'tnum'; font-variant-numeric: tabular-nums`.

## Key component specs (mobile reference)

- **Header bar**: padding `18px 22px`; gold scale-logo (18px) + «حَكَم» 15px/600 `--ink`; right breadcrumb 12px `--muted-2`.
- **Primary CTA**: bg `--gold`, text `--gold-ink`, radius 14px, height 58px, 16px/600.
- **Input**: bg `--surface`, text `--ink`, border `rgba(255,255,255,.12)`, radius 14px, height 58px, 15px.
- **Topic pill** (lobby): bg `--surface`, radius 999px, border `rgba(255,255,255,.10)`, 13.5px/500.
- **Invite row**: card `--surface` radius 16px; link `hakam.app/j/CODE` 15px/500 letter-spacing .02em; copy btn 44×44 bordered, gold copy icon.
- **Debater card** (lobby): card `--surface` radius 16px; avatar 42×42 circle, tinted bg + colored initial (ع/س); "الطرف الأول/الثاني" 11.5px `--muted` + name 16px/600; ready = colored check + «جاهز»; pending B = pulsing dot + «ينضم الآن…»; claim quote in tinted `.06` bg radius 10px 13.5px/1.8.
- **Format row**: «جولتان لكل طرف · دقيقتان للجولة» 13px `--muted`.
- **Start button**: gated → disabled fill `--surface-3` text `--muted-2` «بانتظار جاهزية الطرفين»; ready → gold «ابدأ المناظرة», height 58px.
- **Claim chips** (debate): A bg `rgba(63,184,175,.10)` text `--teal-light` border `.32`; B coral; radius 10px, 11.5px/1.6.
- **Countdown ring**: `svg viewBox 0 0 200 200`, two `circle r=88 stroke-width=8`; track `rgba(255,255,255,.08)`; progress current side color, `stroke-dasharray≈552.9`, `stroke-dashoffset = 552.9 * (1 - remaining/total)`, `transform rotate(-90 100 100)`, `stroke-linecap round`. Center: mm:ss 46px/600 `--ink` + «من 02:00» 11.5px `--muted`. Container 196px mobile / 264px desktop.
- **Turn label**: «دور {name} — جولة الافتتاح/الرد» 14px/500 current side color.
- **Turn dots**: 4 dots (افتتاح أ/ب, رد أ/ب); done/current = filled side color 9px; future = 1.5px ring in side color `.55`; label `--ink-2`/`--muted-2` 11px.
- **Mic (hold-to-record)**: idle = 92px circle bg `--surface` + 2px border side-color `.5`, mic glyph side color, label «اضغط مطوّلاً للتحدث» `--muted`; recording = 92px filled side color, mic glyph `--teal-ink`(dark), label «جارٍ التسجيل… ارفع إصبعك للإرسال» side color.
- **Finish request**: ghost button `--muted-3` border `rgba(255,255,255,.14)` radius 12px height 44px «طلب إنهاء المناظرة» + hint «يتطلب موافقة الطرفين» 11px `--muted-2`.
- **Transcript/turns panel** (Phase 1 = recorded-turns playback list): card `--surface-2` radius 16px; each turn bubble bg `--surface` radius 12px, speaker name 11.5px/600 side color + play control.

## SVGs (viewBox `0 0 24 24` unless noted)

- **Logo (scale)** stroke `--gold`: `M12 4v15  M5 6h14  M5 6l-2.5 5  M5 6l2.5 5  M2.5 11a2.5 2.5 0 0 0 5 0  M19 6l-2.5 5  M19 6l2.5 5  M16.5 11a2.5 2.5 0 0 0 5 0  M8.5 19.5h7`
- **Mic**: `M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z  M19 11a7 7 0 0 1-14 0  M12 18v4`
- **Copy** stroke `--gold`: `rect x=9 y=9 w=12 h=12 rx=2.5` + `M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1`
- **Check** stroke side-color w=2.4: `polyline 20 6 9 17 4 12`
- **Ring**: see Countdown ring above.
