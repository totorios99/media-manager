# Redesign brief — Media Manager

Prompt for a design agent (Claude Design). Goal: redesign the UI/UX of this app while keeping every existing capability and API contract.

---

## The prompt

You are redesigning **Media Manager**, a self-hosted movie-library cleanup tool used by a single admin on desktop and on iPhone (installed as a home-screen PWA in Safari). It is one static HTML page (vanilla JS, no framework, no build step) talking to a small JSON API. Redesign the interface and interaction flow; do not change the backend.

### What the app does

The user has ~300 movies (~4.8 TB). The mission is triage: find the ~70 oversized BDRemux files worth shrinking, configure and run long encode jobs on them, verify results, delete originals — and quickly dismiss the ~230 files that are already fine. It is a working tool, not a media browser: every screen exists to answer "what do I do with this file next?"

### Current structure

- **Header**: search box, status filter, duplicates checkbox, advice filter, "Scan library" and "Purge junk" buttons, scan status text, and a power switch (CPU for jobs: ⚡ auto / ⚡ full / 🐢 throttle, plus a work-hours field in auto mode).
- **Jobs strip**: thin bar under the header, one row per active job — movie title, job type (Quick Remux / Heavy Encode), progress bar, percent. Hidden when idle.
- **Library grid**: TMDB poster cards with chips — status (unprocessed/ready/cleaning/encoding/working/clean/stub/error), DUP, SHRINK/FINE advice, resolution, codec, HDR type, file size.
- **Movie detail**: numbered 4-step wizard, steps collapse when done —
  1. *Identify*: TMDB match/re-match with candidate search.
  2. *Tracks*: table of audio/subtitle tracks — keep, drag order, language, default/forced flags, name stripping; "suggest" button pre-fills sane choices.
  3. *Process*: advice hint ("45 Mbps is above the 25 Mbps bar for this resolution"), buttons for Quick Remux / Heavy Encode / Accept as-is, RF quality input, 30-second sample encodes with projected full size, raw command previews, live job status.
  4. *Finalize*: delete original + rename everything to "Title (Year)".
- **Theme**: dark only (#14161a background, #2f6fed accent, small chips, 13px system font).

### Known UX pain points

- Header is a flat pile of unrelated controls (search, maintenance, power) that wraps badly on mobile.
- Grid chips are noisy; the one decision-driving signal (SHRINK/FINE) doesn't stand out.
- No library-level progress sense: how many TB reclaimed, how many movies triaged vs left.
- Track table is dense and awkward on a phone (drag-to-reorder especially).
- Long-running encode (10+ h) feedback lives in a tiny strip; no ETA surfaced, though the log contains one.
- Errors surface as `alert()`.
- Sample-encode comparison (RF vs size) is a flat list; deciding an RF value is the highest-judgment moment in the app and gets the least support.

### Constraints

- Keep it one self-contained HTML file: inline CSS/JS, vanilla JS, no external fonts/CDNs (TMDB poster images are the only remote assets).
- Dark theme first; must stay readable on a 6-inch phone and fine on desktop.
- All data comes from the existing endpoints: `GET /api/movies` (list + filters), `GET /api/movies/{id}` (detail + tracks + duplicates), `POST .../jobs`, `GET /api/jobs`, `POST .../accept`, `GET/PUT /api/power`, `POST /api/scan`, junk preview/apply, samples list/fetch/delete, TMDB search/set, config save, rename, delete-original. Payload shapes are in `app.py`; do not invent fields.
- Jobs poll every 3 s; scans report done/total/current-file.
- Destructive actions (delete original, accept as-is, purge junk) need explicit confirmation, but better than `confirm()`.

### Deliverables

1. Redesigned `static/index.html` (drop-in replacement).
2. Short rationale: information hierarchy, mobile layout strategy, and how each pain point above was addressed.

Prioritize: triage speed (grid → decision → job in as few taps as possible), glanceable long-job progress, and confidence at the two irreversible moments (starting a 10-hour encode with the right settings; deleting an original).
