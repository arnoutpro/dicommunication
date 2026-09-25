---
name: arnout.pro
description: Calm, dark-first editorial shell and working tool console for a PACS / radiology IT practitioner's tools and profile.
colors:
  reading-room-navy: "#0a1330"
  paper-white: "#ffffff"
  ink-dark: "#eef2ff"
  ink-light: "#0f172a"
  muted-dark: "#94a3b8"
  muted-light: "#475569"
  faint: "#64748b"
  signal-cyan: "#22d3ee"
  signal-cyan-strong: "#06b6d4"
  signal-cyan-deep: "#0e7490"
  button-ink-dark: "#020617"
  button-ink-light: "#f8fafc"
  caution-amber-dark: "#fbbf24"
  caution-amber-light: "#b45309"
  lane-hl7: "#22d3ee"
  lane-modalities: "#fb7185"
  lane-dicom: "#2dd4bf"
  lane-viewing: "#fbbf24"
  card-tools: "#fbbf24"
  card-simulators: "#818cf8"
  card-about: "#fb7185"
  tool-surface-dark: "#0e1a3f"
  tool-subtle-dark: "#cbd5e1"
  tool-subtle-light: "#334155"
  signal-cyan-bright: "#a5f3fc"
  signal-cyan-ink: "#164e63"
  signal-cyan-hover-light: "#155e75"
  signal-cyan-tint: "#ecfeff"
  tool-hairline-dark: "rgb(255 255 255 / 0.08)"
  tool-hairline-light: "rgb(15 23 42 / 0.1)"
  tool-tint-dark: "rgb(255 255 255 / 0.04)"
  tool-tint-light: "rgb(15 23 42 / 0.03)"
  status-error-dark: "#fecdd3"
  status-error-light: "#9f1239"
  status-warning-dark: "#fde68a"
  status-warning-light: "#92400e"
  status-added-dark: "#34d399"
  status-added-light: "#047857"
typography:
  display:
    fontFamily: "Sansation, sans-serif"
    fontSize: "clamp(2.35rem, 8vw, 4.35rem)"
    fontWeight: 700
    lineHeight: 0.92
    letterSpacing: "-0.055em"
  display-line:
    fontFamily: "Sansation, sans-serif"
    fontSize: "clamp(1.35rem, 3.4vw, 2.05rem)"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "clamp(1.55rem, 2.8vw, 2.15rem)"
    fontWeight: 800
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1.05rem"
    fontWeight: 700
    letterSpacing: "-0.01em"
  lede:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1.02rem"
    fontWeight: 500
    lineHeight: 1.6
  body:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.5
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
  tool-panel-title:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    letterSpacing: "-0.01em"
  tool-ui:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 700
  tool-label:
    fontFamily: "Plus Jakarta Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    letterSpacing: "0.01em"
rounded:
  xs: "0.3rem"
  sm: "0.35rem"
  md: "0.5rem"
  lg: "0.75rem"
  xl: "1.1rem"
  tool-control: "0.55rem"
  tool-dialog: "1rem"
spacing:
  gutter: "1.5rem"
  card-gap: "1.25rem"
  card-pad: "1rem 1.15rem"
  section-gap: "clamp(2.75rem, 6vw, 4rem)"
  container: "72rem"
  tool-inset: "1rem"
  tool-item-gap: "0.5rem"
  tool-tight-gap: "0.75rem"
  tool-group-gap: "1.25rem"
components:
  button-primary:
    backgroundColor: "{colors.signal-cyan-strong}"
    textColor: "{colors.button-ink-dark}"
    rounded: "{rounded.sm}"
    padding: "0.78rem 1.15rem"
  button-primary-light:
    backgroundColor: "{colors.signal-cyan-deep}"
    textColor: "{colors.button-ink-light}"
    rounded: "{rounded.sm}"
    padding: "0.78rem 1.15rem"
  button-ghost:
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.sm}"
    padding: "0.78rem 1.15rem"
  delivery-label:
    textColor: "{colors.ink-dark}"
    typography: "{typography.label}"
    rounded: "{rounded.xs}"
    padding: "0.05rem 0.45rem"
  catalog-card:
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.lg}"
    padding: "{spacing.card-pad}"
  profile-card:
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.md}"
    padding: "1.05rem 1.15rem"
  tool-button:
    backgroundColor: "{colors.tool-tint-dark}"
    textColor: "{colors.tool-subtle-dark}"
    typography: "{typography.tool-ui}"
    rounded: "{rounded.tool-control}"
    padding: "0.4rem 0.7rem"
  tool-button-light:
    backgroundColor: "{colors.tool-tint-light}"
    textColor: "{colors.tool-subtle-light}"
    typography: "{typography.tool-ui}"
    rounded: "{rounded.tool-control}"
    padding: "0.4rem 0.7rem"
  tool-button-primary:
    backgroundColor: "{colors.signal-cyan-strong}"
    textColor: "{colors.button-ink-dark}"
    typography: "{typography.tool-ui}"
    rounded: "{rounded.tool-control}"
    padding: "0.4rem 0.7rem"
  tool-button-primary-light:
    backgroundColor: "{colors.signal-cyan-deep}"
    textColor: "{colors.button-ink-light}"
    typography: "{typography.tool-ui}"
    rounded: "{rounded.tool-control}"
    padding: "0.4rem 0.7rem"
  tool-panel:
    textColor: "{colors.ink-dark}"
    padding: "{spacing.tool-inset}"
  tool-drawer:
    backgroundColor: "{colors.tool-surface-dark}"
    textColor: "{colors.ink-dark}"
  tool-dialog:
    backgroundColor: "{colors.tool-surface-dark}"
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.tool-dialog}"
---

<!--
  Copied from github.com/arnoutpro/pacsadministration (DESIGN.md at ee41670,
  2026-09-24). Everything above "In Dicommunication" is the shared system and
  should stay identical to the upstream copy; update it there first, then copy
  it here. Dicommunication-specific notes live in the last section only.
-->

# Design System: arnout.pro

## Overview

**Creative North Star: "The Reading Room"**

A radiology reading room is dim on purpose: the surroundings step back so the image is the brightest, sharpest thing in view. This system works the same way. The chrome is a deep navy (or plain white in light mode), quiet and low-glare; the content — a headline, a tool, a screenshot of real output, the imaging-chain map — is what gets lit. One cool signal colour (cyan) marks what you can act on. Everything else is ink, muted ink and thin tinted hairlines.

It is an editorial shell rather than an app skin: generous air, left-aligned text on a 72rem track, plain words in the audience's own vocabulary, and very little ornament. Depth comes from hairlines and faint tints, not shadows. Motion is a single short settle, never a bounce. Colour carries meaning where it appears — the About map's four lanes, the amber pilot warning — and is otherwise held back.

Dark mode is the reference theme; light mode is a full equal (white paper, slate ink, deeper -700 accents that keep AA contrast). A third "professional" theme remaps accents to an amber family.

**Key Characteristics:**
- Dark-first, low-glare chrome; content is the brightest thing on screen.
- One signal accent (cyan) for actions and links.
- Flat: hairlines and faint tints instead of shadows; the header's frosted glass bar and the homepage cards' soft glow are the only exceptions.
- Sansation for the name-level display, Plus Jakarta Sans for everything else.
- Mixed-case labels at readable sizes; nothing functional under 12px.
- One short entrance settle, gated on reduced-motion preference.

### The tools: "The Reading Room console"

The five in-browser tools (HL7 and FHIR Message Analyzers, DICOM Tag Compare, the Worklist and PACS Admin simulators) are the same room with the lights on the worklist. The chrome recedes even further than on the editorial pages; the pasted message, the verdict and the reply are what's lit. Where the editorial shell has generous air, the console runs at working density: full-viewport panels, 12–13px interface text, one filled action per column. It should feel clinical and calm: low glare, no ornament, colour only where it means something, and the same behaviour in every tool.

All five tools load one shared shell (`src/styles/tool-shell.css`, tokens `--az-*`, both themes) before their own stylesheet. A tool may add layout and components; it does not redefine the shell's colours, type sizes or chrome.

**Key Characteristics (tools):**
- One shared token set for every tool; dark and light are both full themes.
- A thin top bar with the tool name as the page's h1, and Help and Config on the right.
- One filled Signal Cyan button per column, for that column's job (Parse, Copy the reply). Everything else is a ghost button.
- Severity and state are always said in words, and colour only reinforces them.
- Flat surfaces for panels, drawers and dialogs. The top bar's glass is the only lift.

## Colors

A near-monochrome reading-room palette with one cool signal colour and a small set of colours that each mean one thing.

### Primary
- **Signal Cyan** (dark `signal-cyan`, fill `signal-cyan-strong`; light `signal-cyan-deep`): links, the headline's second line, focus rings, hover borders, and solid button fills. In light mode every accent drops to its -700 shade so white button labels clear 4.5:1.

### Secondary
- **Caution Amber** (dark `caution-amber-dark`, light `caution-amber-light`, one step darker on the amber homepage card): reserved for warnings that change a decision — "Lab / pilot build — not for clinical use." Never decorative.
- **Imaging-chain lanes** (`lane-hl7` cyan, `lane-modalities` rose, `lane-dicom` teal, `lane-viewing` amber; each has a -700 light-mode counterpart): used only on the About flow map and its key, where they encode HL7 orders, modalities, DICOM images and viewing.

### Tertiary
- **Homepage card tints** (`card-tools` amber, `card-simulators` indigo, `card-about` rose; light mode `#b45309` / `#4338ca` / `#be123c`): the three homepage cards' gradient tint, icon tile and link colour. Scoped to the homepage grid.

### Neutral
- **Reading-Room Navy** (`reading-room-navy`): dark page background.
- **Paper White** (`paper-white`): light page background.
- **Ink** (`ink-dark` / `ink-light`): headings, names, labels on cards.
- **Muted Ink** (`muted-dark` / `muted-light`): body copy, blurbs, captions, ledes (7:1+ in both themes).
- **Faint** (`faint`): de-emphasised meta only; never for text that must be read on the dark background (fails there).
- **Hairline**: the accent at 22% (dark) or 28% (light) opacity, via `color-mix`, for borders and dividers.

### In the tools
The shell remaps the same palette onto roles, per theme:
- **Canvas** (`reading-room-navy` / `paper-white`) and **raised surface** (`tool-surface-dark` / `paper-white`) for drawers, dialogs and menus.
- **Ink**, **Subtle ink** (`tool-subtle-dark` / `tool-subtle-light`) for secondary text and ghost button labels, and **Muted ink** for labels and hints.
- **Signal Cyan** as fill (`signal-cyan-strong` / `signal-cyan-deep`, hover `signal-cyan` / `signal-cyan-hover-light`) for the one primary button. It's also text (`signal-cyan` / `signal-cyan-deep`) for links, the selected tab and focus rings, and a 12% soft fill for hover and selection.
- **Hairlines and tints** (`tool-hairline-*`, `tool-tint-*`): neutral white or slate at 3–10%, not tinted by the accent.
- **Status**: error (`status-error-*` on a rose tint), warning (`status-warning-*` on an amber tint), info in plain subtle ink on a neutral tint, and added lines in Compare (`status-added-*`). Status colour always travels with a word: "error", "warning", "info", "matches", "mismatch", "only in B".

### Named Rules
**The One Signal Rule.** Cyan means "you can act on this". Don't use it for decoration, headings that aren't links, or backgrounds.

**The Lanes Stay on the Map Rule.** The four imaging-chain colours encode the flow on the About map. Don't reuse them to tint cards, tags or section labels.

**The Amber Means Caution Rule.** Amber text is for warnings that change what someone does (pilot builds, not-for-clinical-use). Don't use it for emphasis.

**The Words Before Colour Rule (tools).** Every coloured state in a tool also says what it is in text: a severity chip, "matches" / "mismatch", "only in A". Role pills (reply, next) and informational banners stay neutral.

## Typography

**Display Font:** Sansation (with sans-serif), weight 700 — used only for h1 page titles and the wordmark.
**Body Font:** Plus Jakarta Sans (with system-ui, sans-serif), weights 400–800.
**Label/Mono Font:** the system monospace stack (`mono` token) only for real data: message text in the tools, inline `code`, and protocol names on the About map (HL7, C-STORE). Never as a "technical" costume; the 01 / 02 / 03 ordinals on the /apps/ list predate this file and are the one legacy exception. Numbered steps are fine where the order is real (the DICOM Camera capture flow).

**Character:** Sansation gives each page one distinctive, slightly geometric name; Plus Jakarta Sans does all the reading work with warm, even texture.

### Hierarchy
- **Display** (700, `clamp(2.35rem, 8vw, 4.35rem)`, 0.92): the page's name — "Tools", "Arnout van der Elst". On /about/ it scales down to `clamp(2.2rem, 6vw, 3.6rem)` for a longer name.
- **Display line** (700, `clamp(1.35rem, 3.4vw, 2.05rem)`, 1.15, accent colour): the second line under the display, saying what the page offers.
- **Headline** (800, `clamp(1.55rem, 2.8vw, 2.15rem)`): section headings ("In your browser", "What I do").
- **Title** (700, 1.05rem): tool and card names.
- **Lede** (500, 1.02rem, 1.6, max 38rem): the sentence under a headline.
- **Body** (400, 0.9rem, 1.5, muted): blurbs and card text; prose capped at 34–38rem (≈80 characters). Use rem caps, not `ch` — Plus Jakarta Sans's `ch` runs wide and let lines reach ~100 characters.
- **Label** (600, 0.75rem, mixed case): delivery labels ("Browser · HL7 v2"), map keys. Small uppercase section labels (0.78rem, 0.18em tracking) are the only all-caps text.

### Named Rules
**The 12px Floor Rule.** No functional text below 12px (0.75rem): labels, links, captions and tags included.

**The Name Is the Headline Rule.** A page's h1 names the thing (the tools, the person). No kicker or eyebrow above it; no gradient text anywhere.

### In the tools
Plus Jakarta Sans only; Sansation does not appear inside a tool. A fixed working scale:
- **Panel title** (`tool-panel-title`, 1rem / 700): Message, Inspect, Respond and their equivalents.
- **Interface** (`tool-ui`, 0.8rem / 700 on controls): buttons, inputs, table cells, issue text.
- **Label** (`tool-label`, 0.75rem): section labels, meta labels, the tool name in the top bar (the page h1, muted), severity chips. Mixed case.
- **Mono** for message data only: the paste box, generated replies, HL7 tags and FHIR paths in tables.

## Layout

Single column of editorial content on a centred **72rem** track (homepage 76rem) with a **1.5rem** side gutter. Heroes are single-column and share the grid's left edge. Sections are separated by `clamp(2.75rem, 6vw, 4rem)`; headings sit closer to their content than to the section above.

Card grids use CSS grid with a **1.25rem** gap and cap at three columns (`minmax(max(17rem, calc((100% - 2.5rem) / 3)), 1fr)`), so five items read 3 + 2, never 4 + 1. Groups of cards are introduced by a headline and a one-line lede.

Responsive changes: the homepage grid stacks at 900px; 2-column card grids start at 768px; catalog thumbnails switch to a shallower 2:1 crop under 640px; the About flow map becomes a stacked list under 600px. Pages clear the fixed header with top padding (`clamp(6.5rem, 12vw, 9rem)` on the homepage).

### In the tools
A full-viewport app shell: the top bar, then the tool's workspace, which scrolls inside its panels on wide screens and as a page on narrow ones.
- **Three-panel tools** (the analyzers) read left to right as the task: Message, then Inspect, then Respond. From 1280px up, the columns are weighted toward the reading column (0.9 / 1.3 / 1). From 768px to 1279px they form two columns, with Message above Respond and Inspect beside them. Below 768px it's a single column in task order.
- **Inset and rhythm:** panel bodies use the same 1rem inset as their headers, so content lines up with the title. Spacing has two steps: `tool-item-gap` (0.5rem) inside a group, and `tool-group-gap` (1.25rem) between groups, with `tool-tight-gap` (0.75rem) for dense stacks like the Inspect column.
- **Disclosure over density:** less-used controls (snippets, next-message templates, the field-by-field diff) fold into a disclosure instead of adding rows.
- **Phones:** a 16px side gutter, and every tappable control at least 44px tall.

## Elevation & Depth

Flat. Surfaces sit on the page, separated by thin accent-tinted hairlines (1px) and faint ink tints (3–6%), not by shadows. Two exceptions are sanctioned: the site header is a squared frosted-glass bar (backdrop blur over the page), and the three homepage cards carry a soft zero-offset glow in their own hue. Nothing else floats.

Dark-mode screenshots are dimmed (`brightness(0.82)`) so light-theme captures don't outshine the text around them, and return to full brightness on hover or keyboard focus.

### Named Rules
**The Hairline Rule.** Separate with a 1px tinted line or a 3% tint before reaching for anything heavier. No thick one-sided accent borders.

**The Two Exceptions Rule.** Only the header glass and the homepage card glow may lift off the page. New components stay flat.

In the tools the same rule holds. The tool top bar is the tools' header, so its frosted glass is the header exception. The Config drawer, dialogs, menus, overlays and cards are flat surfaces separated by a hairline, with no shadow and no backdrop blur. Modal backdrops dim the page (navy at 50–72%, slate at 25–30% in light mode) and never blur it.

## Shapes

Gently rounded, never pill-shaped except where an existing chip already is. Corner radii step up with the size of the thing: labels 0.3rem, buttons 0.35rem, profile cards and the flow-map list 0.5rem, catalog cards 0.75rem, homepage cards 1.1rem. The homepage's second row joins Simulators and "Who builds this" on a single diagonal seam — the system's one signature cut, not a pattern to repeat. Screenshots are cropped to a 16:9 window (2:1 on phones), anchored top-centre, with a hairline under them; portrait phone captures are shown whole.

In the tools, controls (buttons, inputs, selects, menus) use one gently rounded corner (0.55rem), and dialogs use 1rem. Panels are square-edged columns separated by hairlines.

## Components

### Buttons
Quiet and precise: one solid action per hero, optional ghost beside it.
- **Shape:** gently squared corners (0.35rem).
- **Primary:** Signal Cyan fill with near-black label in dark mode, deep cyan with white label in light mode; 0.82rem, weight 800, 0.04em tracking, trailing arrow icon.
- **Hover / Focus:** brighten in dark mode, darken in light mode (so light labels keep 4.5:1).
- **Ghost:** transparent with a 1.5px hairline border and ink label; hover turns border and label cyan.
- External links (LinkedIn, GitHub) open in a new tab.

### Chips (delivery labels and tags)
- **Delivery label:** 12px, weight 600, ink text, 1px hairline outline, 0.3rem radius; says where a tool runs and what it speaks ("Browser · HL7 v2", "Install · DICOM", "Android · DICOM"). Text carries the meaning, not colour. Under a group heading that already says "In your browser", the label drops the repeated prefix.
- **Tags (About):** 14px mixed case. Linked tags are cyan, underlined, with a trailing "→"; plain tags are ink with no underline.

### Cards / Containers
- **Catalog card** (/tools/): one link covering the whole card; 0.75rem radius, 1px ink hairline at 12%, 3% ink tint; thumbnail on top, then name + delivery label, one-line blurb, optional amber warning, and "See how it works →". Hover and focus-visible both tint the border cyan and underline the call to action; focus adds a 2px cyan outline offset 3px.
- **Profile card** (/about/ "What I do"): 0.5rem radius, hairline border, 3% tint, title + body only; no numbers, no coloured edge.
- **Homepage cards:** hue-tinted gradient panels with an icon tile, lede, list of items with delivery labels, and a single text link; the documented glow exception.
- **Internal padding:** about 1rem × 1.15rem.

### Navigation
A full-width squared frosted-glass header pinned to the top: the animated aurora "A" mark and wordmark on the left, then Home, About, Contact and three dropdowns (Tools, Simulators, Apps), a theme menu (Light / Dark / Auto / Professional) and LinkedIn. The active item is cyan with an underline. On phones the links collapse into a drawer with grouped sections; group labels are 12px uppercase, and group labels that are links keep a 44px tap height.

### Trust strip (signature)
Four plain facts under the homepage headline, each hung from a 1px cyan hairline: bold ink label, one muted sentence (max 34ch). Deliberately not cards — it reads as a footnote to the promise.

### Imaging-chain map (signature)
The About page's SVG flow — EHR → Mirth → Philips EIS → modalities → Vue PACS → reading — in the four lane colours on a faint board, with a key and a plain-language caption that also describes it for screen readers. HL7 and DICOM wires carry a slow dashed flow (14s, reduced-motion off). Under 600px it becomes a stacked list of the same four lanes.

### Tool shell (all five tools)
Clinical and calm: every tool should feel like the same instrument.
- **Top bar:** brand mark, then the tool name as a muted 12px h1, then an optional "/ view" crumb (hidden on phones). Help and Config sit on the right; Help hides under 480px and stays in Config.
- **Buttons:** ghost by default (neutral tint, hairline, subtle ink; hover turns the border and label cyan with a 12% cyan fill). **One filled primary per column**, for that column's job. Disabled buttons fade to 45%.
- **Config drawer:** slides in from the right over a dimming backdrop. It's a flat raised surface with a hairline left edge, and holds sections for Display (theme as three buttons: System / Light / Dark), Actions and Links. When closed it's `inert` and hidden. Opening it moves focus to Close, and closing returns focus to Config.
- **Dialogs** (privacy, Compare, Challenge): real modals. The page behind them is inert, and focus returns to whatever opened them.
- **Disclosures:** a muted label with a chevron that turns on open, and a 44px tap height on phones.
- **Menus** (Export): a small raised list under its button. It closes on a choice, on Escape and on an outside click.

### Analyzer patterns (HL7 and FHIR Message Analyzers)
- **Trust line:** a lock icon and "Runs in this browser. Nothing you paste is uploaded." directly under the paste box, with a "How it works" link to the privacy dialog.
- **Issue list:** one row per issue, with a severity chip in words and FHIR paths shown in mono. It shows every error plus three more issues, then a "Show N more" toggle.
- **Reply builder** (ACK / OperationOutcome): first in the Respond column. It carries a stub note ("Generated replies are stubs…"), and a caution-amber guard line replaces the output when a reply would mislead: no MSH-10, AA for a message with errors, or no resourceType. Below the output, an "Answers …" line names the message the reply belongs to. Copy is the column's one filled button.
- **Compare:** when one side answers the other, it leads with the reply check ("matches" / "mismatch" per check) and folds the field-by-field diff.

## Do's and Don'ts

### Do:
- **Do** keep the page dim and let one thing per view be the brightest: the headline, a tool, a screenshot.
- **Do** use Signal Cyan only for actions, links and focus, and deepen it to the -700 shade in light mode.
- **Do** separate with 1px tinted hairlines and 3–6% tints.
- **Do** keep functional text at 12px or above and prose capped in rem (34–38rem), not `ch`.
- **Do** say where a tool runs with a text label, and show pilot / not-for-clinical-use warnings in amber right on the item.
- **Do** use one entrance at most: a 12px fade-up over ~0.45s with `cubic-bezier(0.25, 1, 0.5, 1)`, only under `prefers-reduced-motion: no-preference`, with content visible by default.
- **Do** crop screenshots to the tool's output, not its window chrome.
- **Do** build every tool on `tool-shell.css` and its `--az-*` tokens; add a tool's own styles on top, never a parallel palette.
- **Do** give each tool column one filled button for its main job, and make the rest ghost buttons.
- **Do** say severity and state in words (error / warning / info, matches / mismatch) and let colour reinforce them.
- **Do** make tool dialogs and drawers real modals: inert background, focus moved in and returned on close.
- **Do** keep tappable tool controls 44px tall on phones, and fold rarely used controls into a disclosure.

### Don't:
- **Don't** use bounce, overshoot or drop-in motion.
- **Don't** use gradient text, a kicker/eyebrow above the headline, or section ordinals (01 / 02 / 03).
- **Don't** add thick one-sided accent borders or coloured left rails to cards.
- **Don't** reuse the imaging-chain lane colours outside the About map.
- **Don't** add shadows or glows beyond the header glass and the homepage card glow.
- **Don't** set functional text in uppercase at small sizes, or below 12px.
- **Don't** let two equal-weight actions compete in one hero.
- **Don't** give a tool its own accent colour (the analyzers' old teal and rose); tools are told apart by name, not hue.
- **Don't** blur or shadow tool overlays, drawers, menus or cards; only the tool top bar keeps the header glass.
- **Don't** colour role pills or informational banners amber or cyan; they are neutral.

## In Dicommunication

Dicommunication is the installed member of the family (Docker, Windows MSI, macOS DMG), a FastAPI + HTMX app that runs on the admin's own network. It follows "The Reading Room console" like the browser tools, with a few differences in how it's built.

- **One stylesheet, same roles.** `app/static/css/app.css` doesn't load `tool-shell.css`; it carries its own custom properties, and those map onto the shell's `--az-*` roles in all three themes (`html` for dark, `html.light-mode`, `html.professional-mode`):

  | Dicommunication | Tool shell | Role |
  | --- | --- | --- |
  | `--bg`, `--bg-elev` | `--az-canvas` | Page and chrome (top bar, tab row, sidebar) |
  | `--content-bg` | — | The work area: canvas with a 2–3% ink tint, so it reads as the lit part |
  | `--bg-panel` | `--az-tint` | Panels, cards and results on the work area |
  | `--surface-raised` | `--az-surface-raised` | Menus, dropdowns, dialogs |
  | `--text` / `--subtle` / `--muted` | `--az-ink` / `--az-subtle` / `--az-muted` | Ink, secondary text and ghost labels, labels and hints |
  | `--accent` | `--az-accent-text` | Links, the selected tab or nav item, focus rings |
  | `--accent-fill` / `--accent-fill-hover` | `--az-accent-fill` / `--az-accent-fill-hover` | The one filled button per column |
  | `--accent-dim` | `--az-accent-soft` | Hover and selected fills |
  | `--line` / `--line-strong` | `--az-hairline` / `--az-hairline-strong` | Borders and dividers |
  | `--glass` / `--glass-strong` | `--az-tint` / `--az-tint-strong` | Ghost buttons, inactive tabs, hover tints |
  | `--ok` / `--fail` / `--amber` | status colours | Always paired with a word (OK, Failed, Paused) |

- **Themes.** Light (the default for new installs), Dark, Auto and Professional, as in the site's theme menu. Professional remaps the accent to the amber family on black; in that theme only, amber is the action colour.
- **Offline.** The app often runs on a locked-down hospital network, so nothing may load from a CDN: fonts, scripts and icons ship inside the app.
- **Shape.** Controls (buttons, inputs, selects, menu items) use `--radius-sm` (0.55rem), panels and cards `--radius` (0.75rem), dialogs and menus `--radius-lg` (1rem), and chips and badges `--radius-xs` (0.3rem).

