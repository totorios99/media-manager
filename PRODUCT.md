# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A single administrator — the owner of a self-hosted media server — working from
a desktop browser and from an iPhone where the app is installed as a
home-screen PWA. There are no other users, no accounts, no roles, and no
sharing. This is confirmed and durable: future work must not introduce auth,
permissions, multi-user state, or concurrent-edit handling.

The job is triage under uncertainty. The owner is deciding, file by file,
"what do I do with this next?" — and some of those decisions commit a machine
to ten or more hours of encoding, or permanently delete an original.

## Product Purpose

Reclaim disk space from an oversized media library without degrading what is
worth keeping, and leave the library correctly named and tagged for playback.

The library is large enough that per-file attention does not scale: ~300
movies (~5 TB) plus TV shows running to hundreds of episodes. Most files are
already fine and need to be dismissed quickly. A minority — oversized BDRemux
rips — are worth a slow, careful re-encode. Success is: the wasteful files
found and shrunk, the acceptable ones cleared out of the queue fast, originals
deleted only after their replacements are verified, and everything named so
the playback server reads it correctly.

## Positioning

Not a media browser and not a batch transcoder. It is a decision tool for
irreversible, expensive operations: it estimates the outcome before committing
(30-second sample encodes projected to full size), states its recommendation
and the reasoning behind it, and requires deliberate confirmation at the two
moments that cannot be undone — starting a long encode at a chosen quality,
and deleting an original.

The mechanism a neighboring tool would not copy: sample-driven RF selection
with projected full-file size, presented as a comparison at the moment of
choosing, rather than a quality number entered blind.

## Operating Context

- Runs on the owner's own hardware alongside the media itself; started via
  `run.sh`, which carries the TMDB API key. A bare `uvicorn` start silently
  loses TMDB matching.
- Two library roots, scanned separately: movies (`MEDIA_ROOT`) and TV
  (`MM_SHOWS_ROOT`).
- **Jellyfin is the consuming server.** Directory layout, filenames, and track
  language tags exist to satisfy it. TV uses `Show (Year)/Season NN/Show (Year)
  - SxxEyy.ext`. Season and episode numbering follows TMDB, which requires the
  Jellyfin library to use TMDB as its metadata provider — on TVDB, shows whose
  arcs are split differently land wrong.
- Encode jobs are long (hours to days), run one at a time in detached tmux
  sessions under systemd scopes, and must survive the browser closing, the
  session ending, and power loss.
- The machine is shared with its owner's normal use, so encoding CPU is
  throttled on a schedule (work hours) or manually.
- Source files come from mixed release groups. Track layouts, language tags,
  and naming conventions are inconsistent between files of the same show, and
  some are simply wrong in the container.

## Capabilities and Constraints

Confirmed capabilities:

- Scan both roots; match against TMDB; per-title and per-episode track
  configuration (keep/drop, order, language, default/forced, output name).
- Show-level configuration: promote one episode's track setup to an entire
  show, applied only to episodes whose track inventory matches, with the
  divergent ones reported for individual handling rather than partially
  applied.
- Quick remux (mkvmerge, minutes) and heavy encode (x265 10-bit via HandBrake,
  hours); 30-second sample encodes with projected full size.
- Junk sweeping, duplicate detection, TMDB season validation with missing-
  episode reporting, Jellyfin-shaped rename, verified delete-original.
- CPU throttling applied to live jobs, including the Flatpak HandBrake scope
  that escapes the job's own cgroup.

Constraints that future work must preserve:

- **One self-contained `static/index.html`** — inline CSS and JS, vanilla, no
  framework, no build step, no external fonts or CDNs. TMDB poster images are
  the only remote asset.
- Must remain usable on a 6-inch phone and correct on desktop.
- Destructive actions require explicit in-page confirmation. Never `alert()`
  or `confirm()`.
- Long-job progress must be visible from every view.
- Data comes from the existing JSON API; payload shapes are defined in
  `app.py`. Do not invent fields.

Terminology used consistently in the product and its code: *advice*
(SHRINK / FINE), *triage*, *RF* (encoder quality), *remux* vs *encode*,
*forced* subtitles, *original* (the pre-encode source file).

## Brand Commitments

Name: **Media Manager**. No logo, no external brand identity — it is a private
tool.

Binding visual constraint carried from the owner-approved redesign: the
"Ice mono" palette and the colored-glow treatment on live and emphasized
elements, implemented behind a single toggle class. Recorded in DESIGN.md.

## Evidence on Hand

- A real library to work against: ~301 movies and 10 TV shows on disk.
- `media.db` — real scan data, track inventories, and job history.
- The owner-approved UI design, now recorded in `DESIGN.md`.

There are no users beyond the owner, no testimonials, no benchmarks, no
pricing, and no deployment story. Future work must not fabricate any.

## Product Principles

1. **Decisions before commitments.** Anything expensive or irreversible is
   preceded by an estimate, a recommendation, and the reasoning — then a
   deliberate confirmation.
2. **Dismissing is as important as acting.** Most files need a fast "this is
   fine." Triage speed matters as much as the depth of the encode flow.
3. **Long work must stay visible.** A job running for ten hours has to be
   glanceable from anywhere, survive restarts, and report honest progress.
4. **The library on disk is the product.** Names, folder shapes, and language
   tags are output, not metadata — they must satisfy Jellyfin exactly.
5. **Sources are inconsistent; the tool absorbs that.** Divergent track layouts
   are reported clearly and handled individually, never silently averaged over.

## Accessibility & Inclusion

**WCAG 2.1 AA is the floor**, confirmed as a durable standard. Established and
verified: every interactive control has an accessible name and a keyboard path,
all text tokens clear 4.5:1 on every surface they appear on, images carry alt
text, modals have dialog semantics with focus trap and restore, destructive
confirmations are operable by keyboard, status changes reach a live region, and
motion respects `prefers-reduced-motion`. Future UI work must hold this floor.
