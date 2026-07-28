---
name: Media Manager
description: Ice-mono control surface for triaging and re-encoding a self-hosted media library
colors:
  page-void: "#0b0d12"
  panel-raised: "#0e1218"
  panel-row: "#10141c"
  panel-card: "#12161f"
  inset-well: "#0b0e14"
  inset-disc: "#0d1015"
  hairline: "#161b25"
  border-panel: "#1c2230"
  border-control: "#232a38"
  border-secondary: "#2c3648"
  track-empty: "#1a202c"
  disc-idle: "#2a2e37"
  text-primary: "#dfe5ee"
  text-body: "#c9d2e0"
  text-muted: "#8a94a6"
  text-faint: "#7a8497"
  glacier-accent: "#8fb8d8"
  amber-attention: "#e8c268"
  jade-resolved: "#7fc9a8"
  orchid-duplicate: "#b48ead"
  signal-error: "#ff5d5d"
typography:
  # The enumerated ramp. The named roles below describe intent; this is the
  # complete set of sizes the interface is allowed to use, and it is what the
  # detector checks against. Without it, every 11px and 12px in a deliberately
  # dense instrument reads as drift.
  scale:
    micro: "9px"
    label: "10px"
    dataSm: "11px"
    data: "12px"
    body: "13px"
    subtitle: "14px"
    headline: "15px"
    title: "19px"
  title:
    fontFamily: "-apple-system, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 700
    lineHeight: 1.2
  headline:
    fontFamily: "-apple-system, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    letterSpacing: "0.02em"
  body:
    fontFamily: "-apple-system, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  readout:
    fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace"
    fontSize: "19px"
    fontWeight: 600
  label:
    fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "0.12em"
rounded:
  # Bar caps are proportional to the bar, not part of the panel ramp: a 3px
  # progress bar takes a 2px cap, a 5px axis takes 3px. Enumerated so they
  # read as the rule they are.
  barCapSm: "2px"
  barCap: "3px"
  chip: "4px"
  control: "6px"
  button: "7px"
  field: "8px"
  card: "10px"
  panel: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "18px"
  xxl: "22px"
components:
  button-primary:
    backgroundColor: "{colors.glacier-accent}"
    textColor: "{colors.page-void}"
    rounded: "{rounded.button}"
    padding: "7px 13px"
  button-commit:
    backgroundColor: "{colors.amber-attention}"
    textColor: "{colors.page-void}"
    rounded: "{rounded.button}"
    padding: "9px 16px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.button}"
    padding: "7px 13px"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.signal-error}"
    rounded: "{rounded.button}"
    padding: "7px 13px"
  chip-keep:
    backgroundColor: "rgba(127,201,168,.1)"
    textColor: "{colors.jade-resolved}"
    rounded: "{rounded.chip}"
    padding: "4px 8px"
  chip-drop:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.chip}"
    padding: "4px 8px"
  card-poster:
    backgroundColor: "{colors.panel-card}"
    rounded: "{rounded.card}"
  input-field:
    backgroundColor: "{colors.panel-card}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
---

# Design System: Media Manager

## Overview

**Creative North Star: "The Broadcast Control Room"**

This is an instrument panel, not a media browser. The operator is watching
machine work that runs for hours and committing to decisions that cannot be
taken back, so the interface behaves like broadcast equipment: a dark chassis
that never competes with the content, numeric readouts in monospace, semantic
colors that mean one thing each, and light that appears only where something
is genuinely live.

The density is deliberate and high. Posters carry the recognition load; every
other surface is compressed data — bitrates, sizes, ETAs, track layouts, codec
strings. Text is small (10–13px) because the operator is scanning many items
to find the few that need action, and an airier layout would mean less on
screen and more scrolling for the same decision. Where the design does spend
space, it spends it on the two moments that deserve deliberation: choosing an
encode quality, and destroying an original.

The palette is called **Ice mono** and was owner-selected. It is cool,
desaturated, and almost monochrome until a status needs to speak. Rejected by
the incumbent system: light mode (the tool runs in a dim room against dark
poster art), decorative gradients on text, and any icon font — the handful of
glyphs are literal characters (⌕ ⟳ ▲ ▼ ▶ ✓ ✕ ▾).

**Key Characteristics:**
- Near-monochrome cool dark, with color reserved for status meaning
- Monospace for every number, label, and machine string
- Glow strictly as a liveness signal, behind one toggle class
- High information density; small type; tight rows
- Flat, tonal depth — layered surfaces rather than drop shadows
- Progressive disclosure through numbered, collapsing step cards

## Colors

A cool near-monochrome chassis in which each accent carries exactly one
meaning, so a screen reads as "what needs me" before any text is parsed.

### Primary
- **Glacier Accent** (`#8fb8d8`): activity and the system's own voice. Running
  jobs, progress fills, primary buttons, the power pill, active step borders
  and discs, selected chart columns, focus rings. If something is happening
  now, or is the main thing to press, it is this blue.

### Secondary
- **Amber Attention** (`#e8c268`): a decision the operator has not made yet.
  The SHRINK badge, the shrink-queue count, the advice banner, the Heavy
  Encode button, oversized file sizes, forced-subtitle chips. Amber is the
  color of "this file is costing you space."

### Tertiary
- **Jade Resolved** (`#7fc9a8`): settled, verified, reclaimed. The FINE badge,
  reclaimed-space stat, triage ring fill, completed step ticks, the KEEP pill,
  and the recommended RF column.
- **Orchid Duplicate** (`#b48ead`): the DUP badge and duplicate cross-links —
  a distinct category of problem, never mixed with status.
- **Signal Error** (`#ff5d5d`): failures and destructive controls only.

### Neutral
- **Page Void** (`#0b0d12`): the application background.
- **Panel Raised / Row / Card** (`#0e1218`, `#10141c`, `#12161f`): the three
  stacked surfaces — step cards, track rows, poster cards and fields.
- **Inset Well / Disc** (`#0b0e14`, `#0d1015`): recessed areas — command
  previews, the RF chart panel, the ring's inner disc.
- **Hairline / Panel / Control / Secondary borders** (`#161b25`, `#1c2230`,
  `#232a38`, `#2c3648`): a four-step border ramp that carries nearly all the
  structure in place of shadow.
- **Text Primary / Body / Muted / Faint** (`#dfe5ee`, `#c9d2e0`, `#8a94a6`,
  `#7a8497`): four text weights, all cool-tinted, never pure white or gray.

### Named Rules

**The One Meaning Rule.** Each accent maps to exactly one state: blue = live,
amber = undecided, jade = resolved, orchid = duplicate, red = failed or
destructive. A color never borrows another's job for visual variety.

**The 4.5 Floor.** Every text token clears 4.5:1 on every surface it can land
on. `text-faint` sits at 4.80:1 and is the darkest text permitted; anything
dimmer is a border or a fill, never type. Verify against `panel-card`
(`#12161f`), the lightest surface, not against the page background.

**The Poster Supremacy Rule.** Artwork is the only saturated thing on screen.
Chrome stays desaturated so 300 posters can be scanned without fatigue.

## Typography

**Body Font:** system stack (`-apple-system, system-ui, sans-serif`)
**Label/Mono Font:** `ui-monospace, 'SF Mono', Menlo, monospace`

**Character:** Two faces with a strict division of labor. The system sans
handles anything a human wrote — titles, prose, button labels, explanations.
The monospace handles anything a machine produced or measured — sizes,
bitrates, percentages, ETAs, codecs, filenames, status words, section labels.
The pairing reads as equipment because the numbers line up in columns and
never reflow as values change.

**The ramp is deliberately shallow — 10px to 19px, a 1.9:1 range.** A mechanical
detector reads that as a flat hierarchy, and on a marketing page it would be
one. Here the largest thing on screen is a 19px title because the surface is an
instrument: a dense grid of measurements where scale contrast would cost rows.
Hierarchy is carried by weight, case, colour and the mono/sans split instead.
Do not "fix" this by scaling titles up.

### Hierarchy
- **Title** (700, 19px): movie and show names on detail pages.
- **Headline** (700, 15px, .02em): the application name in the header.
- **Subtitle** (600, 13–14px): step card titles, dashboard summary lines.
- **Body** (400, 12–13px, 1.5): explanatory copy, advice banners, modal text.
- **Readout** (600 mono, 19–20px): dashboard stat numbers — the largest type
  in the product after page titles.
- **Data** (mono, 10–12px): track metadata, spec chips, sizes, filenames.
- **Label** (600 mono, 10px, .12em, uppercase): section labels above grouped
  content.

### Named Rules

**The Machine Voice Rule.** If a value came from a file, a probe, an encoder,
or a clock, it is monospace. If a person wrote it, it is the system sans.
Monospace is a signal of provenance, never a costume for looking technical.

**The Steady Column Rule.** Live-updating numbers (progress, ETA, sizes) are
monospace and fixed-width so a changing value never shifts the layout around
it.

## Layout

A single centered column with two densities. The library home runs full-bleed
with 22px side padding; detail pages constrain to a 960px reading column
(`.dwrap`) so track rows and step cards never stretch past comfortable scanning
width.

Spacing rides a ~4px unit: 4 / 8 / 10 / 14 / 18 / 22. Panels sit 16–22px from
the viewport edge, step cards stack with a 10px gap, and track rows separate by
6px — tight groups, generous separation between sections.

The poster grid steps down by viewport rather than holding one column count:
6 columns above 1320px, 5 from 1101–1320px, 4 from 701–1100px, 3 below 700px.
The 4-column step exists because six columns on a small laptop compress cards
to roughly 110px, at which point the footer text stops being readable.

At the 700px mobile breakpoint the header wraps with search taking a full row,
the dashboard stacks the triage ring above a 2×2 stat grid, filter tabs scroll
horizontally, track rows wrap to two lines (controls, then the name field), and
modals become bottom sheets. Interactive targets reach 44px there.

Long lists are bounded rather than rendered whole: episodes cap at 24 and
divergent-track rows at 10, each with an explicit expander. An unbounded list
pushed the page's own actions thousands of pixels below the fold.

### Named Rules

**The Reachable Action Rule.** A list never grows so long that the controls
belonging to its container leave the screen. Cap it and offer an expander.

## Elevation & Depth

**Tonal, not shadowed.** Depth comes from a four-step surface ramp
(`page-void` → `panel-raised` → `panel-row` → `panel-card`) paired with a
four-step border ramp. Drop shadows appear in exactly two places, both of
which genuinely float above the page: the modal panel and the header popover.

Everything else is flat. A card is distinguished from its background by being
one step lighter and having a `1px` border, never by a shadow.

### Shadow Vocabulary
- **Overlay lift** (`box-shadow: 0 12px 50px rgba(0,0,0,.6)`): the modal panel.
- **Popover lift** (`box-shadow: 0 8px 30px rgba(0,0,0,.5)`): header dropdowns.
- **Liveness glow** (`box-shadow: 0 0 8px rgba(accent,.5)`, and larger variants
  at 14px/16px/20px): zero-offset colored halos. See the rule below.

### Named Rules

**The Glow Means Live Rule.** A colored glow marks something that is happening
now or is the single action being urged: a running progress bar, the active
status dot, the primary or commit button, the active step panel, the SHRINK
badge, the recommended RF column. It is never applied for emphasis, polish, or
because an element looks flat. Every glow lives behind the `body.glow` class so
the entire treatment can be disabled in one place — keep it that way.

**The Flat Chassis Rule.** Surfaces at rest have no shadow. If something needs
to feel raised, move it one step up the surface ramp and give it a border.

## Shapes

Softly rectangular and consistently stepped: chips 4px, controls and selects
6px, buttons 7px, fields and small cards 8px, cards 10px, panels and modals
12px. Larger surfaces get larger radii, so nesting reads correctly.

Borders do the structural work — `1px` in all cases, stepping through the
border ramp by importance. A row that is dropped or excluded is dimmed to
45% opacity rather than restyled, keeping the layout stable as state changes.

Two circular forms break the rectangle language deliberately: the 72px triage
ring (a `conic-gradient` progress dial with a 56px inner disc) and the 22px
numbered step discs. Both are status instruments, which is exactly why they are
allowed to be round.

### Named Rules

**The Dim-Don't-Move Rule.** Deselected, dropped, and excluded items lose
opacity, never size or position. The operator's eye should not have to
re-find a row because its state changed.

## Components

### Buttons
- **Shape:** gently rounded (7px), horizontally compact (7px 13px).
- **Primary:** glacier accent fill, void-dark text, 600 weight, with liveness
  glow. The system's own recommended action.
- **Commit:** amber fill, void-dark text, 700 weight, larger padding
  (9px 16px). Reserved for starting a heavy encode — the expensive commitment.
- **Ghost:** transparent with a control-border stroke and muted text; the
  default for everything routine.
- **Danger:** transparent with a red stroke and red text. Outline only, never
  filled — a destructive button should not be the brightest thing on screen.
- **Hover:** background lightens (`rgba(255,255,255,.04)`), 120ms ease-out.
- **Focus:** 2px glacier outline at 2px offset, via `:focus-visible`.
- **Disabled:** 40% opacity, default cursor.

### Chips
- **Spec chips:** 10px mono, 1px bordered, 4px radius, muted by default;
  accent-bordered for HDR, amber-bordered when a value exceeds its bar.
- **Keep/Drop pills:** 9px mono 700, .08em tracking. Keep is jade text on a
  jade tint with a jade border; Drop is muted text on a bare secondary border.
  Carries `aria-pressed`.
- **Default/Forced chips:** accent and amber respectively, dimmed to 30%
  opacity when inactive rather than hidden.

### Cards / Containers
- **Poster card:** 10px radius, panel-card background, panel border; the border
  shifts to amber for shrink candidates and brightens to its semantic color on
  hover. Poster locks to a 2/3 aspect ratio so the grid never shifts while
  images load. Badges overlay top-left (advice) and top-right (duplicate).
- **Step card:** 10px radius on the raised panel surface. Active gets an accent
  border plus a faint panel glow; done collapses to its header with a jade tick;
  pending drops to 55% opacity. Headers are keyboard-operable with
  `aria-expanded` when collapsible.
- **Track row:** row surface, panel border, 8px radius, 6px vertical rhythm.

### Inputs / Fields
- **Style:** card-surface fill, control-border stroke, 6px radius, 6px 10px
  padding, inheriting the 13px body face.
- **Focus:** border shifts to glacier accent; `:focus-visible` adds the outline
  ring. Placeholder text uses `text-faint`.
- **Track name field:** monospace 11px on the darkest inset well — it holds a
  machine string, so it looks like one.

### Navigation
Hash-routed with no persistent chrome beyond the header. The application name
is the home affordance; detail pages open with a "← Library" accent link.
Filter tabs are mono 11px pills carrying live counts, the active one filled
with an amber tint and `aria-pressed`. On mobile the tab strip scrolls
horizontally rather than wrapping.

### Signature Component: the RF decision chart
The product's highest-judgment moment gets its own instrument. Inside a
recessed well, one column per encoded sample: projected full size (12px mono),
percent of source, and a bar whose height is proportional to projected
gigabytes (tallest = 86px). The recommended column — the lowest RF whose
projection lands under the advice bar — fills with a jade gradient, glows, and
carries a RECOMMENDED overline. Beneath sits a 5px quality axis running
jade → amber → red with "← HIGHER QUALITY · BIGGER FILE" and "SMALLER FILE ·
QUALITY RISK →" at its ends. Columns are keyboard-selectable and update the
encode button's RF and size estimate live.

This is the one place the system spends real space and color on a single
decision, because it is the decision that costs ten hours to get wrong.

## Do's and Don'ts

### Do:
- **Do** keep the entire interface in one self-contained `static/index.html`
  with inline CSS and vanilla JS — no framework, no build step, no external
  fonts or CDNs. TMDB posters are the only remote asset.
- **Do** put every machine-produced value in monospace and every human-written
  string in the system sans.
- **Do** reserve glow for what is live, and keep it behind `body.glow`.
- **Do** define colors as tokens in `:root`. There are currently zero raw hex
  or `hsl()` literals outside that block — keep it that way.
- **Do** give every interactive element a visible `:focus-visible` ring, an
  accessible name, and a keyboard path. Cards use `role="button"` plus
  `tabindex="0"`, driven by the delegated Enter/Space handler.
- **Do** state the consequence and the size before any irreversible action, and
  require typed confirmation for deletion or a held press for the rest.
- **Do** cap long lists and provide an expander.
- **Do** honor `prefers-reduced-motion`.

### Don't:
- **Don't** use `alert()` or `confirm()`. Errors are toasts in a live region;
  confirmations are the in-page modal with dialog semantics, a focus trap,
  Escape, and focus restore.
- **Don't** add a shadow to a resting surface. Move it up the surface ramp
  instead.
- **Don't** let a color take on a second meaning. Amber is undecided, not
  "warning-ish."
- **Don't** use `text-faint` or dimmer for anything below 4.5:1 on the surface
  it lands on.
- **Don't** animate layout properties. Transitions are limited to `background`
  and `border-color` at ~120ms ease-out.
- **Don't** introduce a light theme, an icon font, gradient text, or a
  decorative glass/blur effect.
- **Don't** render an unbounded list of episodes, tracks, or conflicts.
