# Handoff: Media Manager UI Redesign

## Overview
Redesign of **Media Manager**, a self-hosted single-admin movie-library cleanup tool (desktop + iPhone PWA). Mission: triage ~300 movies (~4.8 TB), shrink the ~70 oversized BDRemux files, verify, delete originals, dismiss the rest fast. This redesign covers the two core screens: the **Library home** (dashboard + triage grid) and the **Movie detail wizard** (4 steps, with an RF decision chart). The design was iterated and approved by the owner.

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, not production code to copy directly. The task is to **recreate these designs inside the existing app environment**: one self-contained `static/index.html`, inline CSS/JS, **vanilla JS, no framework, no build step, no external fonts/CDNs** (TMDB poster images are the only remote assets). Do not change the backend; all payload shapes come from the existing endpoints (see `current_index.html` and the API list in `DESIGN_PROMPT.md`).

- `Media Manager Explorations.dc.html` — the approved design (top section = detail wizard "3a"; second section = library home "2a"). Ignore the light-gray review chrome (badges/labels around the dark cards); only the dark cards are the design. Ignore its template/logic plumbing — it is a design-tool artifact.
- `current_index.html` — the current production page: **the source of truth for API calls, polling, and behavior**. All existing capabilities must survive the redesign.
- `DESIGN_PROMPT.md` — the original brief with constraints and endpoint list.

## Fidelity
**High-fidelity.** Colors, typography, spacing, and copy in the dark mock cards are final. Recreate pixel-perfectly (within CSS-implementation reason). The mock uses fake data; wire everything to the real API.

## Design Tokens (final, owner-selected "Ice mono" palette)
Base:
- Page background `#0b0d12`; panel backgrounds `#0e1218`, `#10141c`, `#12161f`; inset/darkest `#0b0e14`, `#0d1015`
- Hairlines/borders: `#161b25` (section dividers), `#1c2230` (panel borders), `#232a38` (control borders), `#2c3648` (secondary button border)
- Text: primary `#dfe5ee`, body `#c9d2e0`, muted `#8a94a6`, faint `#5b6577`, ghost `#4a5568`

Semantic accents (each also used as `rgba(color, α)` tints — border α .35–.45, background α .08–.15, glow/box-shadow α .3–.5):
- **Accent / activity (jobs, encoding, primary buttons, power):** `#8fb8d8`
- **Shrink / attention (SHRINK badge, queue counts, advice banner, Heavy Encode button):** `#e8c268`
- **Fine / success (FINE badge, reclaimed stats, done ticks, ring, recommended RF):** `#7fc9a8`
- **Duplicate:** `#b48ead`
- **Error/destructive:** `#ff5d5d`
- Neon glow: colored `box-shadow`/`text-shadow` (e.g. `0 0 8px rgba(accent,.5)`) on live/emphasized elements. Implement behind one CSS class so it can be disabled easily.

Typography (system stack only):
- UI text: `-apple-system, system-ui, sans-serif`; titles 13–19px/600–700, body 12–13px, small 11px
- Data/labels: `ui-monospace, 'SF Mono', Menlo, monospace`; stat numbers 19–20px/600, chips & labels 9–11px, section labels 10px with `letter-spacing:.12em`, uppercase
- Radii: cards/panels 10–12px, controls 6–8px, chips 4px. Base spacing unit ~4px (paddings 8/10/14/18/22).

## Screens / Views

### 1. Library home (mock "2a", 1120px card; grid = 6 columns desktop)
Top→bottom:
- **Header** (14px 22px padding, bottom hairline): app name (15px/700) · search field (max 340px, `#12161f` bg, `#232a38` border, radius 8) · right group: "⟳ Scan", "Maintenance ▾" (junk purge lives here), power pill (accent border/text, glowing 7px dot, "Auto power · 09–18"). Power pill opens mode switcher (auto/full/throttle + work-hours field in auto).
- **Dashboard panel** (margin 16px 22px, gradient `#10141c→#0d1017`, border `#1c2230`, radius 12) — one row, two zones:
  - Left (border-right divider): **triage ring** 72px, `conic-gradient` fine-green for % triaged, inner 56px disc `#0d1015` with mono % label; beside it "227 of 300 triaged" (14px/600) and "~0.9 TB still on the table" (shrink-gold).
  - Right: 4 equal mono stat cells divided by hairlines — RECLAIMED (fine-green, 19px mono), LIBRARY (4.8 TB · 300), SHRINK QUEUE (shrink-gold), ACTIVE JOB (accent "AKIRA · 64%" + faint "ETA 4H 12M" + 3px progress bar with glow). Labels: 10px mono, faint, .12em tracking.
- **Filter tabs** (mono 11px): NEEDS DECISION n (shrink tint bg + border, active) · IN FLIGHT n · DONE n · ALL n; right-aligned "SORT: SIZE ▾ · DUP ☐". These map to the old status/advice/dup filters: Needs decision = advice-shrink & not clean/working; In flight = cleaning/encoding/working (+queued jobs); Done = clean/accepted.
- **Poster grid**: 6 columns, 14px gap. Card: radius 10, bg `#12161f`, border `#1c2230` (shrink cards: `rgba(shrink,.35)` border); TMDB poster 2/3 aspect; **advice badge** top-left (mono 10px/700, dark `rgba(10,11,15,.9)` bg; SHRINK = gold text + gold glow ring, FINE = green text + soft ring; hide on clean); DUP badge top-right (dup color); encoding cards get a 3px accent progress bar over the footer; footer = title (12px/600, ellipsis), row of res+HDR vs size (11px mono; size gold when shrink), spec line `4K · HEVC · DV · 52Mbps` (10px mono faint).
- Card click → detail. Scan progress replaces the ACTIVE JOB cell content while running (`done/total: current`).

### 2. Movie detail (mock "3a", 960px card)
- **Top bar**: "← Library" (accent) left; status pill right (mono, colored dot + glow; READY=green, ENCODING=accent, ERROR=red, NEW=gray).
- **Identity header**: poster 110px radius 8 · title 19px/700 + faint year · filename (11px mono faint) · spec chips (mono 10px, bordered 4px radius): neutral `3840×2160`, `HEVC`, `orig: eng`; accent `DV`; gold `52 Mbps`, `78.4 G` when above bar · duplicates line (dup-colored links).
- **Wizard steps** as stacked cards (radius 10, bg `#0e1218`, 10px gap):
  - Done: green ✓ disc (22px), title 13px/600, gray summary, right ghost action ("Re-match" / "▾ reopened"); collapsed by default, click header to expand.
  - Active: accent border + faint outer glow, accent numbered disc.
  - Pending: opacity .55, gray disc, right-side disabled action.
- **Step 1 Match on TMDB** (done): summary "Title (Year) · tmdb id". Expanded: search field + candidate rows (poster 40px, title, year, lang).
- **Step 2 Configure tracks**: AUDIO ("TOP = PRIMARY") and SUBTITLES groups. Each track = a **card row** (bg `#10141c`, border `#1c2230`, radius 8, dropped rows at 45% opacity): **▲▼ reorder buttons** (24×17px bordered — replaces drag; keep drag as desktop enhancement only) · KEEP/DROP pill (9px mono/700; keep = green tint, drop = gray border) toggles keep · codec 12px/600 + meta 10px mono (`7.1 · lossless`) · lang select chip · DEFAULT chip (accent, one per group) · FORCED chip (gold, subs only) · name field (mono 11px, dark inset, flex-grows). Footer buttons: Auto-suggest, Strip junk names (ghost) · **Save configuration** (accent bg, dark text, glow).
  - Mobile: rows wrap to two lines (controls line + name line); 44px min hit targets.
- **Step 3 Process** (active):
  - **Advice banner** (gold tint bg/border, radius 8): "SHRINK ADVISED — 52 Mbps is above the 25 Mbps bar for 4K — a heavy encode should reclaim ~60 GB with no visible loss." (Green variant for keep advice.)
  - **Action row**: primary gold "Heavy Encode @ RF 22 · est ~17.9 G" (RF + est update live from chart selection) · "Quick Remux (minutes)" ghost · "Accept as-is" green ghost · "view command ›" text link (expands `pre` with the raw command).
  - **RF decision chart** (inset panel `#0b0e14`): header "RF DECISION — PROJECTED FULL SIZE FROM 30s SAMPLES" + right "source 78.4 G", RF stepper, "+ Encode sample" (accent). One column per sample: projected size (12px mono), "% of source" (10px), **bar with height proportional to projected GB** (tallest sample = 86px; recommended = green gradient fill + glow + RECOMMENDED overline; others = `rgba(accent,.28)`), RF label, judgment tag (NEAR-LOSSLESS / EXCELLENT / SWEET SPOT / QUALITY RISK), "▶ play · ✕" links (play opens sample file; ✕ deletes). Below: 5px quality-axis gradient bar (green→gold→red) with "← HIGHER QUALITY · BIGGER FILE" / "SMALLER FILE · QUALITY RISK →" labels. Clicking a column selects that RF for the encode button. Tag thresholds: derive from RF (≤18 near-lossless, 19–21 excellent, 22–23 sweet spot, ≥24 risk) — the recommended highlight goes to the lowest RF whose projection is under the advice bar.
  - While a job runs: replace action row with progress (%, **ETA parsed from the job log**), keep polling every 3 s.
- **Step 4 Finalize** (pending until output verified): "delete original + rename to 'Title (Year)'" summary, red-outline "Delete original…" button.

## Interactions & Behavior
- Routing: hash (`#/movie/{id}`) as today. Jobs poll `GET /api/jobs` every 3 s; scan status every 1 s while running.
- **Destructive confirmations — replace all `alert()`/`confirm()`** with an in-page modal (dark panel, red-tinted header): shows exactly what will be deleted/kept + sizes; confirm requires **typing the movie title** (finalize/delete original) or a hold-to-confirm button (accept as-is, purge junk). Purge junk modal lists the preview files (scrollable). Errors render as a dismissible toast/banner (red border, mono message), never `alert()`.
- Long-running jobs: any active job is always visible in the dashboard ACTIVE JOB cell from every view; ETA parsed from job log where available.
- Hover: cards brighten border to their semantic color; buttons lighten bg ~8%. Transitions ~120ms ease-out. No other animation except glow pulses on live progress (subtle, optional).
- Loading: skeleton gray blocks in panel shapes; poster `loading="lazy"`.

## State Management
Same as current app (module-level state + re-render functions is fine): filters (tab, search, sort, dup), movies list, current movie detail (movie/tracks/duplicates), samples list, jobs list, scan status, power config, selected RF, modal state. Dashboard numbers: derive reclaimed/triaged/queue counts client-side from `GET /api/movies` (status + advice + size fields); no new endpoints.

## Responsive (iPhone PWA)
Single breakpoint ~700px: header collapses to name + search + "⋯" menu (scan/maintenance/power inside); dashboard stacks ring zone above a 2×2 stat grid; tabs horizontally scrollable; grid 3 columns (badge + title + size only); track rows wrap to 2 lines; RF chart columns compress (min 64px, horizontal scroll if needed); modals full-width bottom sheets. Keep 44px hit targets.

## Assets
- TMDB posters: `https://image.tmdb.org/t/p/w342{poster_path}` (only remote asset). Mock uses striped gradient placeholders — replace with real posters, keep the gradient as the no-poster fallback.
- No icon font; the few glyphs are text (⌕ ⟳ ▲ ▼ ▶ ✓ ✕ ▾).

## Files
- `Media Manager Explorations.dc.html` — approved hi-fi design (open in a browser; top card = detail wizard, bottom card = library home)
- `current_index.html` — current production page (API contract + behavior reference)
- `DESIGN_PROMPT.md` — original brief, constraints, endpoint list
