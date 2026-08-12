---
name: Collecct
description: Government-document discipline, warmed by a single green — a capture-operations console for govcon business development.
colors:
  paper: "#ffffff"
  ink: "#171717"
  surface: "#ffffff"
  surface-2: "#f4f4f4"
  muted-foreground: "#6b6b6b"
  line: "#e2e2e2"
  go-green: "#006b4f"
  go-green-ink: "#40be96"
  stop-red: "#ae2e24"
  watch-amber: "#b45309"
  paper-dark: "#0f0f0f"
  ink-dark: "#f5f5f5"
  surface-dark: "#1f1f1f"
  surface-2-dark: "#292929"
  line-dark: "#2a2a2a"
  ring-dark: "#40be96"
  deadline-week: "#006b4f"
  deadline-month: "#2f9e78"
  deadline-later: "#86cfb5"
  deadline-none: "#647587"
  chart-1: "#00915f"
  chart-2: "#2563eb"
  chart-3: "#b45309"
  chart-4: "#7c3aed"
  chart-5: "#0891b2"
typography:
  display:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "27px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "22px"
    fontWeight: 500
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "17px"
    fontWeight: 500
    letterSpacing: "-0.01em"
  brand-lockup:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "34px"
    fontWeight: 500
    letterSpacing: "-0.025em"
    lineHeight: 1
  page-title:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "26px"
    fontWeight: 500
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  body-sm:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  caption:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "12px"
    fontWeight: 400
  figure:
    fontFamily: "Geist Mono, ui-monospace, SFMono-Regular, monospace"
    fontSize: "21px"
    fontWeight: 500
    letterSpacing: "-0.01em"
  label:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.03em"
  micro-label:
    fontFamily: "Geist Sans, system-ui, -apple-system, sans-serif"
    fontSize: "10px"
    fontWeight: 700
    letterSpacing: "0.07em"
  numeric:
    fontFamily: "Geist Mono, ui-monospace, SFMono-Regular, monospace"
    fontSize: "11px"
    fontWeight: 500
rounded:
  sm: "4px"
  md: "5px"
  lg: "8px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "22px"
components:
  button-primary:
    backgroundColor: "{colors.go-green}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "10px 18px"
    typography: "{typography.body}"
  button-primary-disabled:
    backgroundColor: "{colors.go-green}"
    textColor: "#ffffff"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 18px"
  button-small:
    rounded: "{rounded.md}"
    padding: "6px 12px"
  input-search:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    typography: "{typography.body}"
  badge-bid:
    textColor: "{colors.go-green}"
    rounded: "{rounded.full}"
    padding: "3px 8px"
    typography: "{typography.micro-label}"
  badge-watch:
    textColor: "{colors.watch-amber}"
    rounded: "{rounded.full}"
    padding: "3px 8px"
    typography: "{typography.micro-label}"
  badge-nobid:
    textColor: "{colors.muted-foreground}"
    rounded: "{rounded.full}"
    padding: "3px 8px"
    typography: "{typography.micro-label}"
  pill:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.muted-foreground}"
    rounded: "{rounded.full}"
    padding: "3px 9px"
    typography: "{typography.micro-label}"
  nav-item-top:
    textColor: "{colors.muted-foreground}"
    rounded: "{rounded.md}"
    padding: "7px 14px"
  nav-item-top-active:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
  table-header-cell:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.muted-foreground}"
    padding: "10px 14px"
    typography: "{typography.label}"
---

# Design System: Collecct

## Overview

**Creative North Star: "The Contracting Officer's Desk"**

Collecct looks like government-contracting paperwork that someone finally made
comfortable to work in. It borrows the discipline of the source material — ruled tables,
uppercase micro-labels, tabular figures that line up down a column, official restraint in
color — and then removes the parts that make federal documents miserable: the cramped
margins, the undifferentiated wall of text, the sense that nothing on the page is more
important than anything else.

The result is a quiet, dense, high-information surface. Body text sits at 13px and micro
labels at 10–11px, because a capture rep is reading a hundred opportunities, not an
article. Nearly everything is neutral: white or near-black paper, one hairline rule, one
tonal step. Into that neutrality, exactly one warm color enters — a deep forest green
(#006b4f) that means *go*. When you see green, a decision has been made or an action is
available. Nothing else in the system is allowed to be green.

Depth is soft and layered rather than flat: cards, panels, and popovers sit on the paper
as real objects with a resting shadow, and the shadow scale grows with how far off the
page a thing is meant to be. Motion is fast and restrained — a 150ms exit, a 210ms enter,
a 400ms move, because position is what carries continuity and opacity is only a veil.

**Key Characteristics:**
- Two typefaces, both Geist: sans for everything readable, mono for every number.
- Neutral-dominant palette; one accent, one alarm, one caution.
- Tabular figures globally — every count, currency, and score aligns.
- Uppercase, wide-tracked micro-labels as the organizing device.
- 5px corners as the default; pills only for status, never for containers.
- Light and dark are peers, not a theme and its afterthought.

## Colors

A near-monochrome working surface with one deep green accent, one oxblood alarm, and one
amber caution — every hue in the system is either neutral or load-bearing.

### Primary
- **Go Green** (`#006b4f`): the accent as a **fill** — primary buttons, checked boxes,
  selected pills, the active nav underline. It reads as an *institutional* green: deep,
  desaturated, closer to a seal than to a success toast. It carries white text at 6.5:1
  and holds its value unchanged in both themes.
- **Go Green Ink** (`#006b4f` light / `#40be96` dark): the accent as **text**. Bid
  verdicts, links, the brand's terminal dot, sort carets, agent status labels. In dark
  mode the deep green measures **2.5:1** on the card surface — below even the 3:1
  large-text floor — so green *lettering* takes the brighter value while green *fills*
  keep the deep one. Same reasoning as `--ring`, which has always been a per-theme token.

### Secondary
- **Watch Amber** (`#b45309` light / `#c07e22` dark): the Watch verdict and medium risk.
  Caution, not danger — the state where a human still has to decide.
- **Stop Red** (`#ae2e24`): destructive actions and hard blockers only. Never a chart
  series, never a decorative accent.

### Tertiary
- **Deadline Ramp** (`#006b4f` → `#2f9e78` → `#86cfb5` → `#647587`): time-to-deadline
  encoded as one hue losing saturation as urgency falls away, ending in a cool slate for
  "no deadline." This is the only place a *tint* of the accent is permitted.
- **Chart Series** (`#00915f`, `#2563eb`, `#b45309`, `#7c3aed`, `#0891b2`): categorical
  data only. The decision colors (bid / nobid / watch) are reserved and must never be
  reused as a series color, or a chart legend starts lying about verdicts.

### Neutral
- **Paper** (`#ffffff` light / `#0f0f0f` dark): the page.
- **Surface** (`#ffffff` / `#1f1f1f`): cards, panels, popovers.
- **Surface 2** (`#f4f4f4` / `#292929`): the one tonal step — table headers, inset wells,
  hover fills, the risk block.
- **Ink** (`#171717` / `#f5f5f5`): primary text.
- **Muted** (`#6b6b6b` / `#a0a0a0`): secondary text, column headers, inactive nav. This
  is a *text* color, not a surface — the two meanings are deliberately separate tokens.
- **Line** (`#e2e2e2` / `#2a2a2a`): every hairline rule and border, and the default
  border color globally, so a bare `border` utility is correct on its own.

### Named Rules

**The One Green Rule.** The accent covers ≤10% of any screen. It marks decisions and
actions — nothing decorative, no accent-tinted surfaces, no green section headers. A Bid
badge is only legible as a signal because the surrounding page has no other green in it.

**The Reserved Signal Rule.** `bid`, `nobid`, `watch`, and the deadline ramp are semantic
tokens. Never borrow one for a chart series, an illustration, or a mood.

**The Ink-Is-Not-Fill Rule.** A color that works as a background does not automatically
work as lettering. Green text uses `--bid-ink`, green fills use `--primary`; `border-color`
on a focus state uses `--ring`. Writing `color: var(--accent)` is the bug this rule exists
to prevent — it renders at 2.5:1 in dark mode.

**The Soft-Fill Rule.** Status backgrounds are the signal color mixed 8% into the page
(`color-mix(in oklab, var(--bid) 8%, var(--background))`) — never a raw tint constant, so
they stay correct in both modes automatically.

## Typography

**Display Font:** Geist Sans (with system-ui, -apple-system, sans-serif)
**Body Font:** Geist Sans — the same face
**Label/Mono Font:** Geist Mono (with ui-monospace, SFMono-Regular, monospace)

**Character:** There is no display face. Headings are the body sans set larger with
negative tracking, which keeps the whole product sounding like one voice — plain,
technical, unornamented. The personality lives in the *extremes* of the scale: 27px
headings with −0.02em tracking at the top, and 10px uppercase labels tracked out to
0.07em at the bottom. Numbers always defect to the mono.

### Hierarchy
- **Brand lockup** (500, 34px, 1.0, −0.025em): the wordmark on entry surfaces only — sign
  in, sign up, invite. The one place the mark is the largest thing on screen.
- **Display** (500, 27px, 1.0, −0.02em): the wordmark in the app shell.
- **Page title** (500, 26px, −0.015em): the `h1` of a section — Dashboard, Today, Pipeline.
- **Headline** (500, 22px, −0.015em): section and modal titles.
- **Title** (500, 17px, −0.01em): card and record titles.
- **Body** (400, 13px, 1.5): the global default. Set on `html/body`, so everything
  inherits it.
- **Body small** (400, 12.5px): dense label/value rows and secondary list text.
- **Caption** (400, 12px): the explanatory line under a label; never the only copy.
- **Figure** (Geist Mono, 500, 21px, −0.01em): a headline count that carries a whole
  population — the Dashboard's blocker figures. The one place mono is set large.
- **Label** (600, 11px, 0.03em, uppercase): table column headers, field labels.
- **Micro-label** (700, 10px, 0.07em, uppercase): badges, pills, status chips.
- **Numeric** (Geist Mono, 500, 11px): priority scores, IDs, document types, any figure
  read against another figure.

### Named Rules

**The Tabular Rule.** `font-variant-numeric: tabular-nums` is set once on `body`, not
per-cell. Almost nothing here is running prose, and every count, dollar column, and score
needs figures that line up. Do not override it.

**The Mono-for-Meaning Rule.** Geist Mono is not a style choice; it marks a value as
machine-derived — a score, an ID, a code. Never set prose in the mono, and never set a
score in the sans.

**The Two-Case Rule.** Text is sentence case or uppercase-tracked, never Title Case. Title
Case in a govcon UI reads as a document heading and fights the real hierarchy.

## Layout

The application shell is a fixed, non-scrolling frame — `html, body { overflow: hidden }`
— and scrolling happens *inside* panes. This is deliberate: a capture rep should never
lose the top bar or the pursuit rail while reading a long table.

- **Shell:** a 56px top bar (brand, section nav, primary action, user menu) over a
  two-column body of `272px 1fr` — the Bid pursuit rail and the main pane. Below 1100px
  the grid collapses to `1fr` and the rail is dismissed.
- **Density:** compact by intent. 13px body, 10–14px control padding, table rows on a
  ~10px vertical rhythm. Spacing steps run 4 / 8 / 12 / 18 / 22px; 22px is the shell
  gutter, 12–14px the internal card padding.
- **Tables are the primary layout.** Pipeline, Contacts, Call Plan and Library are all
  ruled tables with sticky headers. The sticky header draws its rule as
  `box-shadow: inset 0 -1px 0` rather than `border-bottom`, because a border on a sticky
  element does not paint reliably while rows composite over it.
- **Breakpoints:** 1100px (shell collapses, side panes stack) and 900px (toolbars wrap).
  There is no mobile-first tier; this is a desktop working tool that stays usable on a
  tablet.

### Named Rules

**The Frame Rule.** The page never scrolls. New surfaces get their own scroll container
inside the frame; anything that makes `body` scroll is a bug.

## Elevation & Depth

Softly layered. Surfaces are real objects sitting on paper, and the shadow scale encodes
how far off the page a thing is meant to be — a resting card lifts barely perceptibly, a
modal lifts unmistakably. Hairline rules and the single tonal step (`surface-2`) do the
rest of the separation work; shadow and rule are partners, not alternatives.

Dark mode does not reuse the light shadows. Each step roughly quadruples in opacity
(0.04 → 0.20 at the smallest step) because a shadow that reads on white is invisible on
`#0f0f0f`.

### Shadow Vocabulary
- **`--shadow-2xs`** (`0 1px 2px rgb(0 0 0 / 0.04)` · dark `/ 0.20`): the resting lift on
  cards and inline panels. Barely there; you notice its absence, not its presence.
- **`--shadow-sm`** (`0 1px 3px rgb(0 0 0 / 0.06)` · dark `/ 0.28`): raised containers and
  hover response on interactive cards.
- **`--shadow-md`** (`0 8px 24px rgb(0 0 0 / 0.08)` · dark `/ 0.36`): dropdowns,
  popovers, menus — things that overlay siblings.
- **`--shadow-lg`** (`0 16px 40px rgb(0 0 0 / 0.12)` · dark `/ 0.44`): modals and dialogs,
  always over the `--overlay` scrim.

The scrim itself is a token, not a hardcoded rgba: `rgb(0 0 0 / 0.18)` light,
`rgb(0 0 0 / 0.55)` dark — it must be heavier in dark mode or it disappears.

### Named Rules

**The Distance Rule.** Pick a shadow by how far the element is from the page, never by how
important it is. Importance is expressed in type and color; distance is expressed in
shadow.

**The Paired-Depth Rule.** A shadow never replaces the hairline rule. Layered surfaces
carry both — the rule defines the edge, the shadow defines the gap.

> Note: the incumbent implementation is flatter than this — most cards currently rest on
> `1px solid var(--line)` with no shadow. Layered depth is the committed direction; apply
> it as surfaces are touched rather than in one sweep.

## Shapes

Rectilinear and quiet. Three radii and one pill, nothing else.

- **5px (`--radius-md`)** is the default and by far the most common: buttons, inputs, nav
  items, cards, menu items. Enough to look intentional, not enough to look playful.
- **4px (`--radius-sm`)** for small chrome inside an already-rounded container, where 5px
  would visually collide with the parent's corner.
- **8px (`--radius-lg`)** for large surfaces — modals, the risk block (6px in place today),
  full-width panels.
- **999px (pill)** is reserved for *status*: badges, pills, risk bars, avatars. A pill
  container is off-brand; a pill is a label about state.

Borders are always exactly 1px and always `var(--border)`. There are no 2px borders, no
double rules, no dashed dividers. Emphasis comes from an inset shadow instead — the active
nav item is marked with `inset 0 -2px 0 var(--accent)`, not a thicker border.

### Named Rules

**The Hairline Rule.** Every border is 1px in the line token. If a divider needs more
weight, the answer is a tonal surface change or an inset shadow, never a thicker stroke.

## Components

### Buttons
- **Shape:** softly rounded (5px), never pill.
- **Primary:** Go Green fill, white text, 600 weight at 13px, `10px 18px` padding.
  Its meaning is "the action of this surface" — one per region.
- **Hover / Focus:** background shift over 180ms; `:active` scales to `0.98` over 120ms,
  a small physical acknowledgement. Disabled drops to 0.55 opacity with
  `cursor: not-allowed`.
- **Ghost:** 1px line border, ink text, transparent fill; hovers to `surface`. The
  default for secondary and cancel actions.
- **Small:** `6px 12px` at 12px, for toolbars and inline table actions.
- **Busy state:** a 12px inline spinner (2px ring, 0.7s linear) replaces nothing — it sits
  before the label, so the button never changes width mid-flight.

### Badges (signature component)
The verdict badge is the most important 20 pixels in the product. Uppercase 10px/700
tracked 0.07em, pill-shaped, `3px 8px`, with an 8% soft fill of its own signal color:
**Bid** green, **Watch** amber, **No-Bid** muted. In-flight states (`ingesting`,
`processing`) reuse the same shape and pulse opacity 1 → 0.45 on a 1.6s loop, so "working"
is legible without a separate spinner vocabulary.

### Pills / Chips
Neutral metadata, not status: 10px/700 uppercase, muted text on `surface-2` with a 1px
line border, fully rounded. Use for NAICS codes, set-asides, agencies — anything
categorical that carries no judgement. Document-type chips defect to Geist Mono at 10.5px
in accent color.

### Risk Meter (signature component)
Three 20×5px rounded bars in a 3px row, colored by level — green / amber / red — with the
count of lit bars *being* the level, so it reads pre-attentively without the word. It sits
in a `surface-2` well with a 1px border and a 10px/700 uppercase caption. Below it, the
factor list carries a severity tag per row, and blocker rows are visually distinguished.
This component is the physical form of the product's "show your evidence" rule; never ship
a risk level without its factors.

### Cards / Containers
- **Corner Style:** 5px, or 8px for large panels.
- **Background:** `surface` on `paper`; inset wells step to `surface-2`.
- **Shadow Strategy:** resting `--shadow-2xs`; see Elevation.
- **Border:** 1px `--border`, always.
- **Internal Padding:** 12–14px.

### Inputs / Fields
- **Style:** `paper` fill, 1px line border, 5px radius, 13px ink text; placeholder in the
  faint token.
- **Focus:** `outline: none` plus `border-color: var(--accent)` over 180ms. The green
  border *is* the focus indicator; it must always be accompanied by sufficient contrast
  since no ring is drawn.

### Navigation
Top bar, 56px, on `paper` with a bottom hairline. Items are 13.5px/500 in muted text with
5px corners; hover fills to `surface-2` and lifts the text to ink. The **active** item
takes the same fill plus `inset 0 -2px 0 var(--accent)` — a green underline drawn inside
the item, not a border. The wordmark is 21px/500 tracked −0.02em with the terminal dot in
accent. The left rail is *not* navigation; it lists active Bid pursuits.

### Iconography
Icons are 12–16px glyphs carrying a `data-motion` attribute and a shared overshoot spring
(`240ms cubic-bezier(0.34, 1.56, 0.64, 1)`). The motion fires on **ancestor** hover, so
the whole button is the target and a 12px glyph never needs precise aiming. Sixteen named
motions exist (`pop`, `lift`, `turn`, `nudge-right`, `launch`, `shrink`, `wiggle`, …); pick
the one that describes what the action does. All of it is gated behind
`prefers-reduced-motion: no-preference`.

### Named Rules

**The Verdict-With-Evidence Rule.** A `bid_decision`, `priority_score`, or `risk_level`
never appears alone. Wherever one is shown, its factors or rationale must be one
interaction away at most — hover, adjacent, or expandable in place.

## Do's and Don'ts

### Do:
- **Do** keep the accent scarce — buttons, active nav, focus, and Bid verdicts. Everything
  else is neutral.
- **Do** set every number in Geist Mono and leave `tabular-nums` alone.
- **Do** use uppercase, 0.03–0.07em tracked micro-labels as the organizing device for
  dense regions.
- **Do** build status fills with `color-mix(in oklab, var(--signal) 8%, var(--background))`
  so both themes resolve automatically.
- **Do** give every new surface its own scroll container; the frame never scrolls.
- **Do** author against the semantic tokens (`--ink`, `--line`, `--surface-2`), never raw
  hex — the light/dark pairing lives in the token, not in your rule.
- **Do** pick shadows by distance from the page, and pair them with the hairline rule.
- **Do** draw emphasis with an inset shadow (`inset 0 -2px 0`) rather than a heavier
  border.

### Don't:
- **Don't** reuse `bid`, `nobid`, `watch`, or the deadline ramp as a chart series color.
- **Don't** introduce a third typeface, or a display face. Headings are the sans, larger,
  with negative tracking.
- **Don't** use pill radius on containers — pills mean status.
- **Don't** use borders thicker than 1px, dashed dividers, or double rules.
- **Don't** write Title Case in UI copy.
- **Don't** tint a surface with the accent, or use green as a section-header color.
- **Don't** style scrollbars per-pane; they are defined once globally.
- **Don't** add a `border-bottom` to a sticky table header — use
  `box-shadow: inset 0 -1px 0`.
- **Don't** ship a verdict, score, or risk level without the evidence behind it.
