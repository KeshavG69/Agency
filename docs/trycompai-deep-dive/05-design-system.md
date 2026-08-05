# 05 — Design System & UI Engineering Playbooks

**Source repo:** `/private/tmp/.../scratchpad/crm` (an open-source govcon-adjacent CRM, a Turborepo monorepo: `apps/app` = Next.js frontend, `apps/api`, `apps/agent`; `packages/ui` = the shared shadcn-based design system).

**What this file is:** the *written recipes* that produce this repo's professional, un-templated look — captured VERBATIM where they are rules/guidelines, so they can be adopted into **Collecct** (our existing custom Next.js + Tailwind CRM frontend with its own emerging design system).

**How the professional look is actually produced (the one-paragraph mental model):**
1. A **single source-of-truth UI package** (`packages/ui`) — nothing styled at the call site.
2. A **deliberate, opinionated token layer** (flat white + one brand green, untinted greys, 5px radius identical in both themes, only-two-things-filled) that replaces "default shadcn neutral" — this is the single biggest lever.
3. A stack of **enforced engineering playbooks** (shadcn composition rules, no-`useEffect`, server-first React, nuqs URL-state, composition-over-boolean-props) that keep the UI *consistent* as it scales, which is what actually reads as "professional" vs "templated."

The rest of this doc reproduces each layer. Jump to **§10 (House-style checklist)** and **§11 (Collecct adoption)** for the actionable distillation.

---

## Table of Contents

1. The color palette & tokens (VERBATIM) — the single biggest lever
2. Design principles (`docs/design.md`, VERBATIM)
3. The palette ADR — the *why* (`adrs/comp-palette.md`, VERBATIM)
4. Radius, spacing, typography, density, shadows, motion (concrete values)
5. shadcn composition & styling rules (VERBATIM)
6. `no-useEffect` + server-first React (VERBATIM + why)
7. The nuqs URL-state reusable pattern (VERBATIM)
8. AI Elements (chat/stream UI)
9. Composition patterns & TypeScript conventions
10. Repo-wide conventions (AGENTS / CONTRIBUTING)
11. **Distilled house-style checklist**
12. **Collecct adoption notes — copy directly vs adapt**

---

## 1. The color palette & tokens (VERBATIM) — the single biggest lever

This is the crown jewel. The entire palette + token system lives in ONE file. Everything else references these CSS variables via semantic Tailwind tokens (`bg-primary`, `text-muted-foreground`, etc.). Reproduced verbatim.

**File: `packages/ui/src/styles/globals.css`** (the `:root` light theme, `.dark` theme, and `@theme inline` token map):

```css
/* packages/ui/src/styles/globals.css */
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";

@source "../../**/*.{ts,tsx}";

@custom-variant dark (&:is(.dark *));

:root {
	--background: #ffffff;
	--foreground: #171717;
	--card: #ffffff;
	--card-foreground: #171717;
	--popover: #ffffff;
	--popover-foreground: #171717;
	--primary: #006b4f;
	--primary-foreground: #ffffff;
	--secondary: #f4f4f4;
	--secondary-foreground: #171717;
	--muted: #f4f4f4;
	--muted-foreground: #6b6b6b;
	--accent: #f4f4f4;
	--accent-foreground: #171717;
	--destructive: #ae2e24;
	--destructive-foreground: #ffffff;
	--success: oklch(0.55 0.13 150);
	--success-foreground: #ffffff;
	--warning: oklch(0.62 0.14 65);
	--warning-foreground: #ffffff;
	--info: oklch(0.55 0.16 255);
	--info-foreground: #ffffff;
	--border: #e2e2e2;
	--input: #e2e2e2;
	--overlay: rgb(0 0 0 / 0.18);
	--ring: #006b4f;
	--chart-1: #00915f;
	--chart-2: #2563eb;
	--chart-3: #b45309;
	--chart-4: #7c3aed;
	--chart-5: #0891b2;
	--severity-critical: var(--destructive);
	--severity-high: oklch(0.646 0.222 41.116);
	--severity-medium: oklch(0.681 0.162 75.834);
	--severity-low: var(--muted-foreground);
	--severity-info: oklch(0.71 0.01 286);
	--severity-unknown: oklch(0.75 0 0);
	--sidebar: #fafafa;
	--sidebar-foreground: #171717;
	--sidebar-primary: #006b4f;
	--sidebar-primary-foreground: #ffffff;
	--sidebar-accent: #f4f4f4;
	--sidebar-accent-foreground: #171717;
	--sidebar-border: #e2e2e2;
	--sidebar-ring: #006b4f;
	--font-sans: var(--font-geist-sans, "Geist", sans-serif);
	--font-mono: var(--font-geist-mono, "Geist Mono", monospace);
	--radius: 5px;
	--shadow-x: 0px;
	--shadow-y: 1px;
	--shadow-blur: 2px;
	--shadow-spread: 0px;
	--shadow-opacity: 0.08;
	--shadow-color: hsl(0 0% 0%);
	--shadow-2xs: 0px 1px 2px 0px hsl(0 0% 0% / 0.04);
	--shadow-xs: 0px 1px 2px 0px hsl(0 0% 0% / 0.04);
	--shadow-sm: 0px 1px 2px 0px hsl(0 0% 0% / 0.06);
	--shadow: 0px 1px 2px 0px hsl(0 0% 0% / 0.06);
	--shadow-md: 0px 2px 4px -1px hsl(0 0% 0% / 0.08);
	--shadow-lg: 0px 4px 8px -2px hsl(0 0% 0% / 0.08);
	--shadow-xl: 0px 8px 16px -4px hsl(0 0% 0% / 0.08);
	--shadow-2xl: 0px 16px 32px -8px hsl(0 0% 0% / 0.12);
	--tracking-normal: 0em;
	--spacing: 0.25rem;
}

.dark {
	--background: #0f0f0f;
	--foreground: #f5f5f5;
	--card: #171717;
	--card-foreground: #f5f5f5;
	--popover: #171717;
	--popover-foreground: #f5f5f5;
	--primary: #006b4f;
	--primary-foreground: #ffffff;
	--secondary: #1f1f1f;
	--secondary-foreground: #f5f5f5;
	--muted: #1f1f1f;
	--muted-foreground: #a0a0a0;
	--accent: #292929;
	--accent-foreground: #f5f5f5;
	--destructive: #ae2e24;
	--destructive-foreground: #ffffff;
	--success: oklch(0.72 0.16 155);
	--success-foreground: oklch(0.205 0 0);
	--warning: oklch(0.8 0.15 75);
	--warning-foreground: oklch(0.205 0 0);
	--info: oklch(0.7 0.15 250);
	--info-foreground: oklch(0.205 0 0);
	--border: #2a2a2a;
	--input: #2a2a2a;
	--overlay: rgb(0 0 0 / 0.55);
	--ring: #40be96;
	--chart-1: #0fa871;
	--chart-2: #4b87f0;
	--chart-3: #c07e22;
	--chart-4: #9b7bf0;
	--chart-5: #1e93b8;
	--severity-critical: oklch(0.637 0.237 25.331);
	--severity-high: oklch(0.705 0.213 47.604);
	--severity-medium: var(--chart-2);
	--severity-low: var(--muted-foreground);
	--severity-info: oklch(0.71 0.01 286);
	--severity-unknown: oklch(0.5 0 0);
	--sidebar: #171717;
	--sidebar-foreground: #f5f5f5;
	--sidebar-primary: #006b4f;
	--sidebar-primary-foreground: #ffffff;
	--sidebar-accent: #292929;
	--sidebar-accent-foreground: #f5f5f5;
	--sidebar-border: #2a2a2a;
	--sidebar-ring: #40be96;
	--font-sans: var(--font-geist-sans, "Geist", sans-serif);
	--font-mono: var(--font-geist-mono, "Geist Mono", monospace);
	--radius: 5px;
	/* shadow tokens identical to :root (omitted here for brevity, but present verbatim) */
}

@theme inline {
	--color-background: var(--background);
	--color-foreground: var(--foreground);
	--color-card: var(--card);
	--color-card-foreground: var(--card-foreground);
	--color-popover: var(--popover);
	--color-popover-foreground: var(--popover-foreground);
	--color-primary: var(--primary);
	--color-primary-foreground: var(--primary-foreground);
	--color-secondary: var(--secondary);
	--color-secondary-foreground: var(--secondary-foreground);
	--color-muted: var(--muted);
	--color-muted-foreground: var(--muted-foreground);
	--color-accent: var(--accent);
	--color-accent-foreground: var(--accent-foreground);
	--color-destructive: var(--destructive);
	--color-destructive-foreground: var(--destructive-foreground);
	--color-success: var(--success);
	--color-success-foreground: var(--success-foreground);
	--color-warning: var(--warning);
	--color-warning-foreground: var(--warning-foreground);
	--color-info: var(--info);
	--color-info-foreground: var(--info-foreground);
	--color-border: var(--border);
	--color-input: var(--input);
	--color-ring: var(--ring);
	--color-overlay: var(--overlay);
	--color-chart-1: var(--chart-1);
	/* ...chart-2..5, severity-*, sidebar-* all mapped the same way... */

	--font-sans: var(--font-sans);
	--font-mono: var(--font-mono);
	--radius-sm: 4px;
	--radius-md: var(--radius);   /* 5px */
	--radius-lg: 8px;
	--radius-xl: 12px;

	--shadow-2xs: var(--shadow-2xs);
	/* ...shadow-xs..2xl mapped... */
	--font-heading: var(--font-sans);
	--radius-2xl: calc(var(--radius) * 1.8);
	--radius-3xl: calc(var(--radius) * 2.2);
	--radius-4xl: calc(var(--radius) * 2.6);
}

@layer base {
	* {
		@apply border-border outline-ring/50;
	}
	body {
		@apply bg-background text-foreground font-sans;
	}

	/* Thin, neutral custom scrollbars — a small but real "polish" signal */
	* {
		scrollbar-width: thin;
		scrollbar-color: var(--border) transparent;
	}
	*::-webkit-scrollbar { width: 10px; height: 10px; }
	*::-webkit-scrollbar-track { background: transparent; }
	*::-webkit-scrollbar-thumb {
		background-color: var(--border);
		border: 2px solid transparent;
		border-radius: 9999px;
		background-clip: padding-box;
	}
	*::-webkit-scrollbar-thumb:hover { background-color: var(--muted-foreground); }
	*::-webkit-scrollbar-corner { background: transparent; }
}
```

### Palette at a glance (the numbers to copy)

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--background` | `#ffffff` | `#0f0f0f` | flat white / near-black |
| `--foreground` | `#171717` | `#f5f5f5` | ink |
| `--card` / `--popover` | `#ffffff` | `#171717` | surfaces sit *above* bg in dark |
| `--primary` (brand green) | `#006b4f` | `#006b4f` | **same in both themes** |
| `--primary-foreground` | `#ffffff` | `#ffffff` | |
| `--secondary` / `--muted` / `--accent` | `#f4f4f4` | `#1f1f1f` / `#1f1f1f` / `#292929` | untinted greys |
| `--muted-foreground` | `#6b6b6b` | `#a0a0a0` | secondary text |
| `--destructive` | `#ae2e24` | `#ae2e24` | **same in both themes** (muted brick red, not alarm red) |
| `--border` / `--input` | `#e2e2e2` | `#2a2a2a` | |
| `--overlay` (modal scrim) | `rgb(0 0 0 / 0.18)` | `rgb(0 0 0 / 0.55)` | dark scrim is *heavier* so dialogs separate from near-black page |
| `--ring` (focus) | `#006b4f` | `#40be96` | **only** token that intentionally differs per theme — a ring only has to be seen |
| charts 1–5 | `#00915f`, `#2563eb`, `#b45309`, `#7c3aed`, `#0891b2` | lighter variants | categorical, not a single-hue ramp |

**Fonts:** Geist Sans (`--font-sans`) and Geist Mono (`--font-mono`); `--font-heading` = `--font-sans` (no separate display face).

---

## 2. Design principles (`docs/design.md`, VERBATIM)

These are the rules an agent must follow. Reproduced in full.

```md
<!-- docs/design.md -->
# Design — Rules for AI Agents

- /packages/ui is the single source of truth for all UI.
- Always use shared shadcn components from /packages/ui.
- Do not override component styles with className.
- Do not introduce custom border radii, spacing, colours, shadows, or other visual deviations.
- Corners are rounded, from the scale only: `rounded-sm` (4px) for the smallest
  controls, `rounded-md` (5px) for buttons, inputs and segments, `rounded-lg`
  (8px) for surfaces that contain controls — popovers, dialogs, menus, table
  shells. Never a literal radius at the call site.
- `rounded-none` is still correct in one case: an element that must join its
  neighbour edge to edge. The input inside an input group, the middle cells of
  a selected date range, and the drawer handle are the existing examples.
- If a component needs a new variant or style, implement it in /packages/ui so the entire application stays consistent.

## Colour

Flat white, neutral greys, and one brand green (`#006B4F`). The greys are
untinted on purpose: there is no scene to tint them toward, and a tinted grey
without a reason reads as indecision.

**Only two things are filled**: `primary` for the action you want, `destructive`
for the one you cannot undo. Everything else — secondary, outline, ghost — is a
white chip in light and a dark chip in dark. That is what keeps a rep's eye
landing on *go* or *stop* and skimming past the rest.

`--primary` and `--destructive` hold the **same value in both themes**. A brand
colour that changes per theme is not one colour, it is two, and both then need
maintaining. The single exception is `--ring`, which lightens in dark: a fill
carries the brand, but a ring only has to be seen, and `#006B4F` is too close to
the dark background to register.
```

---

## 3. The palette ADR — the *why* (`adrs/comp-palette.md`, VERBATIM)

This is the decision record that explains the reasoning (and the exact bugs that motivated it). It is the best single explanation of the aesthetic philosophy — worth internalizing.

```md
<!-- adrs/comp-palette.md -->
# Put the CRM on Comp's colours

The CRM currently looks like unstyled shadcn: pure-neutral greys, near-black on
every primary action, square corners everywhere. That's a fine default and it is
nobody's brand. Comp's is flat white and `#006B4F`, and the CRM is the product
people see, so it may as well look like it came from the same company as the
site.

The thing that made me notice was the settings page. A failed Gmail sync renders
a red alert with a near-black "Resolve" button, and it reads as far more alarming
than "an API needs enabling in your Google Cloud project". Once I started pulling
on that I found a handful of things that were wrong regardless of palette:
`--radius` was `0.625rem` in `:root` and `0.75rem` in `.dark`, so a control
quietly changed shape when you switched theme. Focus rings were 1px, 2px and 3px
depending on which component you were looking at. The modal scrim is `bg-black/10`,
which over a near-black page is invisible, so dialogs in dark mode have nothing
separating them from the page underneath. And the deal-stage chart ramp is an
amber-to-orange sequence that has no relationship to anything else in the product.

What I'd do: repoint both themes onto flat white, untinted neutrals and the brand
green, with `--primary` and `--destructive` holding the same value in both themes
so the brand colour isn't secretly two colours. Radius down to 5px and identical
across themes. Fills reserved for exactly two things — the action you want and the
one you can't undo — so everything else is a chip and a rep's eye lands on *go* or
*stop*. Keep the greys genuinely neutral rather than tinted: there's no scene to
tint them toward, and a tinted grey without a reason reads as indecision.

What it breaks: anything that hardcodes a colour or reaches for `--primary`
meaning "the dark ink colour" rather than "the accent". I hit three of those —
the logo in the header, the active state in the icon rail, and the Resolve button
— all of which turned green the moment the token changed, without opting in. It's
also a wide diff by nature, since it's a token change plus every component that
was pinned to `rounded-none`. Happy to split the two genuine bug fixes out of it
(the theme radius mismatch and the invisible scrim) if the palette itself is not
something you want.
```

**Design lessons distilled from this ADR (directly applicable to Collecct):**
- **Radius must be identical across light/dark.** A control that changes shape on theme switch is a bug.
- **Focus rings must be one consistent width** (this repo standardized on a 2px ring: `focus-visible:ring-2`). Not 1/2/3px depending on component.
- **The modal scrim must be visible in dark** — a `black/10` scrim over a near-black page is invisible. Use a heavier dark scrim (`rgb(0 0 0 / 0.55)` here) via a dedicated `--overlay` token.
- **Chart colors should relate to the system**, not be a random amber→orange ramp.
- **`--primary` means "the accent," never "the dark ink color."** If components reach for `--primary` to get near-black, they break when you introduce a real brand color. Audit for this.

**On the ADR process itself** (`adrs/README.md`, VERBATIM) — the repo prefers short, human-written prose decisions over generated proposals:

```md
<!-- adrs/README.md -->
# adrs

Proposals live here. One file per idea, `.md` or `.txt`, named after the thing you want to change.

Write it yourself, in your own words, at whatever length the idea actually needs — a paragraph is a
perfectly good ADR. See [CONTRIBUTING.md](../CONTRIBUTING.md) for why we'd rather have that than a
pull request full of generated code.

Roughly:

- what you want to change
- why the current behaviour is a problem, ideally with the case that made you notice
- what you'd do instead, and what it would break

Nothing to record after the fact. These are for deciding, and they stay as the record of what was
decided.
```

---

## 4. Radius, spacing, typography, density, shadows, motion (concrete values)

### Radius scale (from the scale only — never a literal radius at a call site)
- `rounded-sm` = **4px** — smallest controls (xs buttons, tiny chips)
- `rounded-md` = **5px** (`--radius`) — buttons, inputs, segments (the default)
- `rounded-lg` = **8px** — surfaces that contain controls: popovers, dialogs, menus, table shells, cards
- `rounded-xl` = **12px**; `2xl/3xl/4xl` = `calc(--radius * 1.8 / 2.2 / 2.6)`
- `rounded-none` — **only** when an element must join its neighbour edge-to-edge (input inside input-group; middle cells of a selected date range; drawer handle).

### Spacing
- Base spacing unit `--spacing: 0.25rem` (Tailwind default 4px grid).
- **Always `gap-*` with flex/grid; never `space-x-*` / `space-y-*`.** Vertical stacks = `flex flex-col gap-*`.

### Typography & density (read off the actual components — this is a DENSE, data-app scale)
The components encode a deliberately **small, tight** type scale — appropriate for a data-heavy CRM.

- **Buttons** (`packages/ui/src/components/button.tsx`): base text is `text-xs`, `font-medium`. Default height **`h-8`** (32px), `px-2.5`, `gap-1.5`. Sizes: `xs` = `h-6`, `sm` = `h-7`, `lg` = `h-9`; icon buttons `size-6/7/8/9`. Icons inside buttons default to `size-4` (`size-3`/`size-3.5` at xs/sm). Focus: `focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-1`.
- **Inputs** (`input.tsx`): `h-8`, `text-xs`, `px-2.5 py-1`, `rounded-md`; hover `border-ring/40`, focus `border-ring` + `ring-2 ring-ring/40`; invalid `border-destructive` + `ring-2 ring-destructive/25`. Dark inputs sit on `--muted` with an inset shadow.
- **Card** (`card.tsx`): `CardTitle` = `text-sm font-medium`; `CardDescription` = `text-xs/relaxed text-muted-foreground` (and `hidden … sm:block` — descriptions are progressively disclosed); `CardContent` = `rounded-lg border bg-card p-4 md:p-6`; `CardFooter` = `border-t pt-4`. Cards use `flex flex-col gap-3`.

**Net density signal:** default control height 32px, body/control text `text-xs`, titles `text-sm`, generous but not airy gaps (`gap-3`/`gap-4`), 4–8px radii. Copy this if you want the same "serious tool, not a marketing site" feel.

### Shadows (very restrained — near-flat)
A single soft shadow family, low opacity (0.04–0.12). Buttons use only `shadow-2xs`. This is deliberately *flat* — depth comes from borders + the `bg-card` vs `bg-background` contrast, not drop shadows.

### Button variants — how "only two things are filled" is actually implemented
`packages/ui/src/components/button.tsx` (the variant list is the enforcement of the color philosophy):

```tsx
// packages/ui/src/components/button.tsx  (variants excerpt)
variant: {
  default:     "bg-primary text-primary-foreground shadow-2xs hover:bg-[color-mix(in_oklch,var(--primary),black_12%)] active:bg-[color-mix(in_oklch,var(--primary),black_22%)] active:shadow-none",
  outline:     "border-border bg-background shadow-2xs hover:bg-muted hover:text-foreground aria-expanded:bg-muted ... dark:bg-muted dark:hover:bg-accent",
  secondary:   "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)] ...",
  ghost:       "hover:bg-muted hover:text-foreground aria-expanded:bg-muted ... dark:hover:bg-muted/50",
  destructive: "bg-destructive text-destructive-foreground shadow-2xs hover:bg-[color-mix(in_oklch,var(--destructive),black_12%)] ... focus-visible:ring-destructive/50",
  contrast:    "bg-foreground text-background shadow-2xs hover:bg-foreground/90 ...",
  link:        "text-primary underline-offset-4 hover:underline",
}
```

Note the technique: **hover/active states are derived from the base token with `color-mix(in oklch, …, black 12%/22%)`** rather than being separate hardcoded colors. One token drives the whole interaction ramp. `default` (green) and `destructive` (red) are the only filled variants; `outline`/`secondary`/`ghost` are chips.

### Motion system (distinctive, "un-templated") — the CDS icon-hover choreography
A genuinely differentiating detail: icons animate on hover of their containing button/link/menu-item, with **per-icon default motions**. Defined verbatim in `globals.css` (`@layer utilities .cds-icon` + keyframes) and wired in `packages/ui/src/components/icon.tsx`.

Motion tokens (from `globals.css`): `pop` (scale 1.14), `scale` (1.1), `lift` (translateY(-2px) scale(1.06)), `turn` (rotate 90deg), `rotate` (-12deg), `flip` (rotateY 180deg), `nudge-right/left/up/down` (translate 3px), `launch` (translate 2px,-2px), plus keyframe animations `spin`, `wiggle`, `swing`, `bounce`, `pulse`. Timing: `transition: transform 240ms cubic-bezier(0.34, 1.56, 0.64, 1)` (a spring-ish overshoot), all gated behind `@media (prefers-reduced-motion: no-preference)`.

Per-icon defaults (`icon.tsx`, `MOTION_BY_ICON`): `ArrowRight`/`ChevronRight`/`Play`/`SendAlt`/`Logout` → `nudge-right`; `Download` → `bounce`; `Settings`/`Renew`/`Restart`/`Earth` → `spin`; `Add`/`Close` → `turn`; `TrashCan`/`Tools`/`Search`/`MagicWand` → `wiggle`; `Information`/`Security`/`Ai` → `pulse`; `Asleep` → `swing`; default fallback → `pop`. Icons are **IBM Carbon** icons (`cds-` = Carbon Design System), passed as component objects.

Additional motion niceties in `globals.css`:
- **View Transitions API** choreography for nav (`::view-transition-*` with `nav-forward`/`nav-back`/`nav-lateral` classes; durations `--duration-exit: 150ms`, `--duration-enter: 210ms`, `--duration-move: 400ms`).
- A rich **`.link-hover--*` underline family** (slide, double, grow, strike, fade, pulse, sweep, bounce, arc, scribble) — animated link underlines.
- `alert-attention` utility (nudge + expanding ring) to draw the eye to an alert.
- `bloom-low/high/aura` glow utilities.
- All motion respects `prefers-reduced-motion: reduce` (transitions collapse to `0.01ms`).

> For Collecct: the motion layer is optional polish. The *tokens + palette + radius + density + composition rules* are the load-bearing parts of the "professional look." Add motion last.

---

## 5. shadcn composition & styling rules (VERBATIM)

These are the rules that keep the UI consistent and un-templated. The repo runs the **shadcn** skill; its critical rules are the enforced house style for component usage.

### 5a. The critical-rules summary (`.agents/skills/shadcn/SKILL.md`, VERBATIM excerpt)

```md
## Principles

1. **Use existing components first.** Use `npx shadcn@latest search` to check registries before writing custom UI. Check community registries too.
2. **Compose, don't reinvent.** Settings page = Tabs + Card + form controls. Dashboard = Sidebar + Card + Chart + Table.
3. **Use built-in variants before custom styles.** `variant="outline"`, `size="sm"`, etc.
4. **Use semantic colors.** `bg-primary`, `text-muted-foreground` — never raw values like `bg-blue-500`.

## Critical Rules

### Styling & Tailwind → [styling.md](./rules/styling.md)

- **`className` for layout, not styling.** Never override component colors or typography.
- **No `space-x-*` or `space-y-*`.** Use `flex` with `gap-*`. For vertical stacks, `flex flex-col gap-*`.
- **Use `size-*` when width and height are equal.** `size-10` not `w-10 h-10`.
- **Use `truncate` shorthand.** Not `overflow-hidden text-ellipsis whitespace-nowrap`.
- **No manual `dark:` color overrides.** Use semantic tokens (`bg-background`, `text-muted-foreground`).
- **Use `cn()` for conditional classes.** Don't write manual template literal ternaries.
- **No manual `z-index` on overlay components.** Dialog, Sheet, Popover, etc. handle their own stacking.

### Forms & Inputs → [forms.md](./rules/forms.md)

- **Forms use `FieldGroup` + `Field`.** Never use raw `div` with `space-y-*` or `grid gap-*` for form layout.
- **`InputGroup` uses `InputGroupInput`/`InputGroupTextarea`.** Never raw `Input`/`Textarea` inside `InputGroup`.
- **Buttons inside inputs use `InputGroup` + `InputGroupAddon`.**
- **Option sets (2–7 choices) use `ToggleGroup`.** Don't loop `Button` with manual active state.
- **`FieldSet` + `FieldLegend` for grouping related checkboxes/radios.** Don't use a `div` with a heading.
- **Field validation uses `data-invalid` + `aria-invalid`.** `data-invalid` on `Field`, `aria-invalid` on the control. For disabled: `data-disabled` on `Field`, `disabled` on the control.

### Component Structure → [composition.md](./rules/composition.md)

- **Items always inside their Group.** `SelectItem` → `SelectGroup`. `DropdownMenuItem` → `DropdownMenuGroup`. `CommandItem` → `CommandGroup`.
- **Use `asChild` (radix) or `render` (base) for custom triggers.**
- **Dialog, Sheet, and Drawer always need a Title.** `DialogTitle`, `SheetTitle`, `DrawerTitle` required for accessibility. Use `className="sr-only"` if visually hidden.
- **Use full Card composition.** `CardHeader`/`CardTitle`/`CardDescription`/`CardContent`/`CardFooter`. Don't dump everything in `CardContent`.
- **Button has no `isPending`/`isLoading`.** Compose with `Spinner` + `data-icon` + `disabled`.
- **`TabsTrigger` must be inside `TabsList`.**
- **`Avatar` always needs `AvatarFallback`.**

### Use Components, Not Custom Markup → [composition.md](./rules/composition.md)

- **Use existing components before custom markup.** Check if a component exists before writing a styled `div`.
- **Callouts use `Alert`.** Don't build custom styled divs.
- **Empty states use `Empty`.** Don't build custom empty state markup.
- **Toast follows the project base.** `toast` (Base UI) / `sonner` (Radix, React Aria).
- **Use `Separator`** instead of `<hr>` or `<div className="border-t">`.
- **Use `Skeleton`** for loading placeholders. No custom `animate-pulse` divs.
- **Use `Badge`** instead of custom styled spans.

### Icons → [icons.md](./rules/icons.md)

- **Icons in `Button` use `data-icon`.** `data-icon="inline-start"` or `data-icon="inline-end"` on the icon.
- **No sizing classes on icons inside components.** Components handle icon sizing via CSS. No `size-4` or `w-4 h-4`.
- **Pass icons as objects, not string keys.** `icon={CheckIcon}`, not a string lookup.
```

Key patterns block (VERBATIM) — the tightest "do/don't" summary:

```tsx
// Form layout: FieldGroup + Field, not div + Label.
<FieldGroup>
  <Field>
    <FieldLabel htmlFor="email">Email</FieldLabel>
    <Input id="email" />
  </Field>
</FieldGroup>

// Validation: data-invalid on Field, aria-invalid on the control.
<Field data-invalid>
  <FieldLabel>Email</FieldLabel>
  <Input aria-invalid />
  <FieldDescription>Invalid email.</FieldDescription>
</Field>

// Icons in buttons: data-icon, no sizing classes.
<Button>
  <SearchIcon data-icon="inline-start" />
  Search
</Button>

// Spacing: gap-*, not space-y-*.
<div className="flex flex-col gap-4">  // correct
<div className="space-y-4">           // wrong

// Equal dimensions: size-*, not w-* h-*.
<Avatar className="size-10">   // correct
<Avatar className="w-10 h-10"> // wrong

// Status colors: Badge variants or semantic tokens, not raw colors.
<Badge variant="secondary">+20.1%</Badge>    // correct
<span className="text-emerald-600">+20.1%</span> // wrong
```

### 5b. Styling rules (`.agents/skills/shadcn/rules/styling.md`, VERBATIM — the load-bearing "un-templated" rules)

```md
## Semantic colors
Incorrect: <div className="bg-blue-500 text-white"><p className="text-gray-600">…</p></div>
Correct:   <div className="bg-primary text-primary-foreground"><p className="text-muted-foreground">…</p></div>

## No raw color values for status/state indicators
For positive, negative, or status indicators, use Badge variants, semantic tokens like `text-destructive`,
or define custom CSS variables — don't reach for raw Tailwind colors.
Incorrect: <span className="text-emerald-600">+20.1%</span>
Correct:   <Badge variant="secondary">+20.1%</Badge>  /  <span className="text-destructive">-3.2%</span>

## Built-in variants first
Incorrect: <Button className="border border-input bg-transparent hover:bg-accent">Click me</Button>
Correct:   <Button variant="outline">Click me</Button>

## className for layout only
Use `className` for layout (max-w-md, mx-auto, mt-4), NOT for overriding component colors or typography.
To customize appearance, prefer in order: 1. Built-in variants  2. Semantic color tokens  3. CSS variables.

## No space-x-* / space-y-*
Use `gap-*`. `space-y-4` → `flex flex-col gap-4`. `space-x-2` → `flex gap-2`.

## Prefer size-* over w-* h-* when equal      (size-10, not w-10 h-10)
## Prefer truncate shorthand                  (truncate, not overflow-hidden text-ellipsis whitespace-nowrap)
## No manual dark: color overrides            (bg-background text-foreground, not bg-white dark:bg-gray-950)
## Use cn() for conditional classes           (not manual template-literal ternaries)
## No manual z-index on overlay components     (Dialog/Sheet/Drawer/Popover/Tooltip handle their own stacking)
## Use shimmer / scroll-fade utilities, not custom animations
   Loading text: <span className="shimmer">Thinking…</span>  (not a hand-rolled @keyframes / bg-clip-text sweep)
```

### 5c. Forms rules (`.agents/skills/shadcn/rules/forms.md`, VERBATIM control-choice guide)

```md
## Forms use FieldGroup + Field  — never raw div with space-y-*
Use `Field orientation="horizontal"` for settings pages. Use `FieldLabel className="sr-only"` for hidden labels.

Choosing form controls:
- Simple text input → Input
- Dropdown with predefined options → Select
- Searchable dropdown → Combobox
- Native HTML select (no JS) → native-select
- Boolean toggle → Switch (for settings) or Checkbox (for forms)
- Single choice from few options → RadioGroup
- Toggle between 2–5 options → ToggleGroup + ToggleGroupItem
- OTP/verification code → InputOTP
- Multi-line text → Textarea

## Field validation and disabled states
data-invalid/data-disabled styles the field (label, description); aria-invalid/disabled styles the control. Both needed.
<Field data-invalid><FieldLabel htmlFor="email">Email</FieldLabel><Input id="email" aria-invalid /><FieldDescription>Invalid email address.</FieldDescription></Field>
```

### 5d. Composition rules (`.agents/skills/shadcn/rules/composition.md`, VERBATIM highlights)

- **Items always inside their Group** — the mapping table (VERBATIM):

```md
| Item | Group |
| SelectItem, SelectLabel | SelectGroup |
| DropdownMenuItem, DropdownMenuLabel, DropdownMenuSub | DropdownMenuGroup |
| MenubarItem | MenubarGroup |
| ContextMenuItem | ContextMenuGroup |
| CommandItem | CommandGroup |
| MessageScrollerItem | MessageScrollerContent |
| Message (consecutive, same sender) | MessageGroup |
| Bubble (stacked) | BubbleGroup |
| Attachment (in a row) | AttachmentGroup |
```

- **Choosing between overlay components** (VERBATIM):

```md
| Focused task that requires input | Dialog |
| Destructive action confirmation  | AlertDialog |
| Side panel with details or filters | Sheet |
| Mobile-first bottom panel | Drawer |
| Quick info on hover | HoverCard |
| Small contextual content on click | Popover |
```

- **Button loading** = compose, no `isLoading` prop: `<Button disabled><Spinner data-icon="inline-start" />Saving...</Button>`
- **Use existing components instead of custom markup** (VERBATIM): `<hr>`/`border-t` → `<Separator />`; `animate-pulse` divs → `<Skeleton className="h-4 w-3/4" />`; styled status spans → `<Badge variant="secondary">`.

### 5e. Icons (`.agents/skills/shadcn/rules/icons.md`, VERBATIM rules)
- Icons in `Button` use `data-icon="inline-start"` / `"inline-end"`; **no sizing classes** on icons inside components (components size them via CSS).
- Pass icons as component objects (`icon={CheckIcon}`), never string keys into a lookup map.
- Always import from the project's configured `iconLibrary` (this repo uses IBM Carbon; do not assume `lucide-react`).

### 5f. base-vs-radix (`.agents/skills/shadcn/rules/base-vs-radix.md`)
The repo's shadcn base is **radix** (see `button.tsx` importing `radix-ui`'s `Slot`). So: use **`asChild`** to swap a trigger's element (`<DialogTrigger asChild><Button/></DialogTrigger>`), **not** `render`. Select/ToggleGroup/Slider/Accordion use radix APIs (`type="single"|"multiple"`, string `defaultValue`, array Slider values). The `render=` / `nativeButton={false}` / `items` prop forms are for the "base" library and are NOT what this repo uses — but Collecct should pick one base and apply the matching column consistently.

---

## 6. `no-useEffect` + server-first React (VERBATIM + why)

This is the biggest *engineering* discipline behind the polish: fewer effects → fewer flicker/race/stale-state bugs → the app feels solid. Enforced by an ESLint `no-restricted-syntax` rule that **bans `useEffect`**.

### 6a. `.agents/skills/no-use-effect/SKILL.md` (VERBATIM)

```md
# No useEffect

Never call `useEffect` directly. Use derived state, event handlers, data-fetching libraries, or `useMountEffect` instead.

## Quick Reference
- Lint rule: `no-restricted-syntax` (configured to ban `useEffect`)
- React docs: You Might Not Need an Effect

| Instead of useEffect for... | Use |
| Deriving state from other state/props | Inline computation (Rule 1) |
| Fetching data | useQuery / data-fetching library (Rule 2) |
| Responding to user actions | Event handlers (Rule 3) |
| One-time external sync on mount | useMountEffect (Rule 4) |
| Resetting state when a prop changes | key prop on parent (Rule 5) |

## The Escape Hatch: useMountEffect
export function useMountEffect(effect: () => void | (() => void)) {
  /* eslint-disable no-restricted-syntax */
  useEffect(effect, []);
}

## Replacement Patterns

### Rule 1: Derive state, do not sync it
// BAD: Two render cycles - first stale, then filtered
const [filteredProducts, setFilteredProducts] = useState([]);
useEffect(() => { setFilteredProducts(products.filter(p => p.inStock)); }, [products]);
// GOOD: Compute inline in one render
const filteredProducts = products.filter((p) => p.inStock);
Smell test: You are about to write useEffect(() => setX(deriveFromY(y)), [y]).

### Rule 2: Use data-fetching libraries
// BAD: Race condition risk
useEffect(() => { fetchProduct(productId).then(setProduct); }, [productId]);
// GOOD: Query library handles cancellation/caching/staleness
const { data: product } = useQuery(['product', productId], () => fetchProduct(productId));
Smell test: Your effect does fetch(...) then setState(...).

### Rule 3: Event handlers, not effects
// BAD: Effect as an action relay (set flag → effect runs → reset flag)
// GOOD: Direct event-driven action
return <button onClick={() => postLike()}>Like</button>;

### Rule 4: useMountEffect for one-time external sync
Good uses: DOM integration (focus, scroll), third-party widget lifecycles, browser API subscriptions.
Mount only when preconditions are met (early-return a Loading screen, then the child uses useMountEffect).
Use useMountEffect for stable deps (singletons, refs, context values that never change).

### Rule 5: Reset with key, not dependency choreography
// GOOD: key forces clean remount
<VideoPlayer key={videoId} videoId={videoId} />   // child does useMountEffect(() => loadVideo(videoId))

## Component Structure Convention
Computed values come after hooks and local state, never via useEffect:
  // Hooks first → Local state → Computed values (NOT useEffect+setState) → Event handlers → Early returns → Render
```

### 6b. Server-first React (Vercel React Best Practices — the highest-impact rules, VERBATIM)

From `.agents/skills/vercel-react-best-practices/`. The CRITICAL sections are "Eliminating Waterfalls" and "Bundle Size"; the app is server-first (RSC).

**Derive during render, not in an effect** (`rules/rerender-derived-state-no-effect.md`, VERBATIM):

```tsx
// If a value can be computed from current props/state, derive it during render.
// Incorrect (redundant state and effect):
const [fullName, setFullName] = useState('')
useEffect(() => { setFullName(firstName + ' ' + lastName) }, [firstName, lastName])
// Correct (derive during render):
const fullName = firstName + ' ' + lastName
```

**Promise.all for independent operations** (`rules/async-parallel.md`, VERBATIM — CRITICAL, "2–10× improvement"):

```typescript
// Incorrect (sequential, 3 round trips):
const user = await fetchUser(); const posts = await fetchPosts(); const comments = await fetchComments()
// Correct (parallel, 1 round trip):
const [user, posts, comments] = await Promise.all([fetchUser(), fetchPosts(), fetchComments()])
```

**Strategic Suspense boundaries** (`rules/async-suspense-boundaries.md`, VERBATIM — stream the shell, don't block it):

```tsx
// Instead of awaiting data before returning JSX, wrap the data-dependent part in <Suspense>:
function Page() {
  return (
    <div>
      <div>Sidebar</div><div>Header</div>
      <div><Suspense fallback={<Skeleton />}><DataDisplay /></Suspense></div>
      <div>Footer</div>
    </div>
  )
}
async function DataDisplay() { const data = await fetchData(); return <div>{data.content}</div> }
// Alternative: start the fetch WITHOUT awaiting, pass the promise, unwrap with use(dataPromise) in children.
```

**Parallel data fetching via composition** (`rules/server-parallel-fetching.md`, VERBATIM — RSCs run sequentially within a tree; restructure to parallelize):

```tsx
// Incorrect: Sidebar waits for Page's await. Correct: siblings each await → both fetch simultaneously.
export default function Page() { return <div><Header /><Sidebar /></div> }
async function Header()  { const data  = await fetchHeader();       return <div>{data}</div> }
async function Sidebar() { const items = await fetchSidebarItems();  return <nav>{items.map(renderItem)}</nav> }
// Alternative: a Layout({children}) that renders <Header/> + {children}, page passes <Sidebar/> as children.
```

**Other enforced rule *names* worth knowing** (full list in the skill's `rules/`): `async-cheap-condition-before-await`, `async-defer-await`, `async-api-routes`, `bundle-barrel-imports` (import directly, avoid barrel files), `bundle-dynamic-imports` (`next/dynamic` for heavy components), `bundle-defer-third-party`, `server-cache-react` (`React.cache()` per-request dedup), `server-hoist-static-io`, `server-serialization` (minimize props to client components), `rerender-no-inline-components` (never define a component inside a component), `rendering-conditional-render` (use ternary, not `&&`), `client-swr-dedup` (SWR for client fetch dedup).

**Why this matters for the "look":** server-first + Suspense = the shell paints instantly and data streams into skeletons; no-`useEffect` = no post-mount flicker or double-render. The UI feels *fast and settled*, which is 80% of "professional."

---

## 7. The nuqs URL-state reusable pattern (VERBATIM)

`nuqs` = type-safe URL query state. This is the reusable pattern for **filters, search, pagination, tabs, sort** — exactly Collecct's Pipeline/Opportunity filters and faceted lists. The skill has 39 rules across 8 categories; the reusable core is below.

### 7a. The reusable pattern, step by step

**Step 1 — One shared parser map (single source of truth), client-safe** (`.agents/skills/nuqs/references/setup-shared-parsers.md`, VERBATIM):

```tsx
// lib/searchParams.ts   (client-safe — no nuqs/server import here)
import { parseAsInteger, parseAsString, parseAsStringLiteral } from 'nuqs'

export const searchParams = {
  page: parseAsInteger.withDefault(1),
  query: parseAsString.withDefault(''),
  sort: parseAsStringLiteral(['asc', 'desc'] as const).withDefault('asc')
}

// components/Pagination.tsx
'use client'
import { useQueryState } from 'nuqs'
import { searchParams } from '@/lib/searchParams'
export function Pagination() {
  const [page, setPage] = useQueryState('page', searchParams.page)
  return <button onClick={() => setPage(p => p + 1)}>Next</button>
}
```

> "A default that disagrees across the boundary (server `withDefault(1)`, client `withDefault(0)`) is a classic hydration mismatch — the shared map makes it impossible."

**Step 2 — Related params as one atomic object** (`references/state-use-query-states.md`, VERBATIM):

```tsx
'use client'
import { useQueryStates, parseAsFloat, parseAsInteger } from 'nuqs'
const [coords, setCoords] = useQueryStates({
  lat: parseAsFloat.withDefault(0),
  lng: parseAsFloat.withDefault(0),
  zoom: parseAsInteger.withDefault(10)
})
// Partial updates / clearing:
setCoords({ zoom: 15 })                    // update only zoom
setCoords({ lat: 51.5074, lng: -0.1278 })  // keep zoom
setCoords(null)                            // clear every key in the object
```
Use for logically-related sets (a filter set, a date range). One typed object, one combined URL flush.

**Step 3 — Constrained values use enum/literal parsers** (`references/parser-enum-validation.md`, VERBATIM — CRITICAL, prevents invalid state from URL tampering):

```tsx
'use client'
import { useQueryState, parseAsStringLiteral } from 'nuqs'
const sortOrders = ['asc', 'desc'] as const
const [sort, setSort] = useQueryState('sort', parseAsStringLiteral(sortOrders).withDefault('asc'))
// sort is 'asc' | 'desc'; URL ?sort=malicious → falls back to 'asc'
```

**Step 4 — Clear a param with `null`, not `''`** (`references/state-clear-with-null.md`, VERBATIM):

```tsx
const clear = () => setQuery(null)            // URL: /  (param removed entirely)
// Convert empty input to null on change → clean URLs:
<input value={query ?? ''} onChange={e => setQuery(e.target.value || null)} />
```

**Step 5 — Debounce URL-driven server fetches** (`references/perf-debounce-search.md`, VERBATIM — built-in since v2.5, don't hand-roll setTimeout):

```tsx
'use client'
import { useQueryState, parseAsString, debounce } from 'nuqs'
import { useTransition } from 'react'
const [isLoading, startTransition] = useTransition()
const [query, setQuery] = useQueryState('q', parseAsString.withDefault('').withOptions({
  shallow: false,          // triggers server re-render
  startTransition,
  limitUrlUpdates: debounce(300)  // one request 300ms after last keystroke
}))
// (Purely client-side search? shallow stays true — just use React's useDeferredValue instead.)
```

**Step 6 — Read the same params on the server** (`references/server-search-params-cache.md`, VERBATIM):

```tsx
// lib/searchParams.server.ts  (server-only — keep nuqs/server out of client bundles)
import { createSearchParamsCache, parseAsString, parseAsInteger } from 'nuqs/server'
export const searchParamsCache = createSearchParamsCache({
  q: parseAsString.withDefault(''),
  page: parseAsInteger.withDefault(1)
})

// app/search/page.tsx
export default async function SearchPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const { q, page } = await searchParamsCache.parse(searchParams)   // parse ONCE at page level
  return <Results query={q} page={page} />
}
// Nested Server Components then read without prop-drilling: searchParamsCache.get('q')
// Lighter alternative when not deeply nested: createLoader(...) → const { q, page } = await loadSearchParams(searchParams)
```

**Step 7 — Generate link URLs without hooks** (`references/perf-serialize-utility.md`, VERBATIM — works in Server Components):

```tsx
import { createSerializer, parseAsInteger, parseAsString } from 'nuqs/server'
export const serialize = createSerializer({ q: parseAsString, page: parseAsInteger.withDefault(1) })
// <a href={`?${serialize({ page: i + 1 })}`}>…</a>
// With base URL: serialize('/search', { q: 'react', page: 1 }) → /search?q=react&page=1
```

### 7b. Setup requirements (from `SKILL.md`)
- Wrap the app in `<NuqsAdapter>` (Next.js adapter). Missing adapter = 100% of hook failures.
- `'use client'` on any component using the hooks. Server utilities import from `nuqs/server` only.
- Rule categories by priority (VERBATIM): **1 Parser Configuration (CRITICAL)**, **2 Adapter & Setup (CRITICAL)**, **3 State Management (HIGH)**, **4 Server Integration (HIGH)**, **5 Performance (MEDIUM)**, **6 History & Navigation (MEDIUM)**, **7 Debugging & Testing (LOW-MEDIUM)**, **8 Advanced (LOW)**.
- History: `history: 'push'` for real navigation the back button should undo; `'replace'` (default) for ephemeral state.

**Why this is the right pattern for Collecct:** filters/search/sort/pagination in the URL means shareable/bookmarkable pipeline views, working browser back button, and server components that can read filters directly for SSR — all without a client state store. It replaces `useState` + `useEffect(sync-to-url)` (which the no-effect rule bans anyway).

---

## 8. AI Elements (chat/stream UI)

`.agents/skills/ai-elements/SKILL.md` — AI Elements is a shadcn-registry component library for AI-native UIs (built on shadcn/ui, installed as source into `@/components/ai-elements/`). Relevant if Collecct's agent/chat surfaces (Analyst, Mail agent, CEO orchestrator) get a chat UI.

Key points (VERBATIM/near-verbatim):
- Install via `npx ai-elements@latest` (or the project's runner). Requires a Next.js + AI SDK project with shadcn/ui.
- Recommends the **AI Gateway** (`AI_GATEWAY_API_KEY`) so you don't juggle per-provider keys.
- Components are **source in your repo** (not a black-box dep) — open and edit them. Each extends the underlying primitive's props (e.g. `Message extends HTMLAttributes<HTMLDivElement>`).
- Canonical usage:

```tsx
"use client";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import { useChat } from "@ai-sdk/react";
const Example = () => {
  const { messages } = useChat();
  return <>{messages.map(({ role, parts }, i) => (
    <Message from={role} key={i}>
      <MessageContent>
        {parts.map((part, j) => part.type === "text"
          ? <MessageResponse key={`${role}-${j}`}>{part.text}</MessageResponse> : null)}
      </MessageContent>
    </Message>
  ))}</>;
};
```

- 40+ reference components exist under `references/` (conversation, message, prompt-input, reasoning, tool, task, sources, inline-citation, code-block, web-preview, suggestion, etc.).

**Note:** the repo's *own* `packages/ui` chat primitives (`MessageScroller`, `Message`, `Bubble`, `Attachment`, `Marker` — see shadcn `chat.md`) are the house-style chat components; AI Elements is the broader Vercel library. For chat UIs the repo's rule is: **compose `MessageScroller` + `Message` + `Bubble`; never hand-roll bubble divs, scroll containers (`MessageScroller` owns streaming-follow/anchor/jump-to-latest — no `useStickToBottom`/`ResizeObserver`), or attachment cards.** Loading = `shimmer` utility.

---

## 9. Composition patterns & TypeScript conventions

### 9a. React composition patterns (`.agents/skills/vercel-composition-patterns/`, VERBATIM core)

The four core principles (VERBATIM from README):

```md
1. Composition over configuration — Instead of adding props, let consumers compose
2. Lift your state — State in providers, not trapped in components
3. Compose your internals — Subcomponents access context, not props
4. Explicit variants — Create ThreadComposer, EditComposer, not Composer with isThread
```

The rules (from `AGENTS.md`, compiled):
- **1.1 Avoid Boolean Prop Proliferation (CRITICAL):** don't add `isThread`/`isEditing`/`isDMThread`; each boolean doubles the state space. Build explicit variant components instead.
- **1.2 Use Compound Components (HIGH):** export a namespaced object (`Composer.Provider/Frame/Input/Footer/Submit/...`); subcomponents read a shared context, consumers compose the pieces they need.
- **2.1 Decouple state from UI:** the provider is the only place that knows *how* state is managed (useState vs Zustand vs server sync). UI consumes a context interface.
- **2.2 Generic context interface = `{ state, actions, meta }`** for dependency injection — the same UI works with different providers. "The UI is reusable bits you compose together. The state is dependency-injected by the provider. Swap the provider, keep the UI."
- **2.3 Lift state into providers** so sibling components *outside* the visual box (a preview, a dialog's Forward button) can read/act on it. "Components that need shared state don't have to be visually nested — they just need to be within the same provider." Explicitly calls out that using `useEffect` to sync state up is the wrong solution.
- **3.1 Explicit variants** (`<ThreadComposer channelId="abc" />`) over `<Composer isThread channelId="abc" showAttachments … />`.
- **3.2 Prefer `children` over `renderX` props** (render props only when the parent must pass data back down, e.g. `renderItem`).
- **4.1 React 19 APIs:** `ref` is a plain prop (no `forwardRef`); use `use(Context)` instead of `useContext(Context)` (`use()` can be called conditionally).

This *is* how shadcn components are structured (Card, Select, Dialog, Field, MessageScroller are all compound components). Following it keeps Collecct's own components looking native to the system.

### 9b. TypeScript advanced types (`.agents/skills/typescript-advanced-types/SKILL.md`)

Best-practices list (VERBATIM):

```md
1. Use `unknown` over `any`: Enforce type checking
2. Prefer `interface` for object shapes: Better error messages
3. Use `type` for unions and complex types: More flexible
4. Leverage type inference: Let TypeScript infer when possible
5. Create helper types: Build reusable type utilities
6. Use const assertions: Preserve literal types
7. Avoid type assertions: Use type guards instead
8. Document complex types: Add JSDoc comments
9. Use strict mode: Enable all strict compiler options
10. Test your types: Use type tests to verify type behavior
```

Common pitfalls to avoid (VERBATIM): over-using `any`; ignoring strict null checks; over-complex types (slow compiles); not using discriminated unions (misses narrowing); forgetting `readonly`; circular type refs; unhandled edge cases. The skill covers generics, conditional types (`infer`), mapped types (key remapping, filtering), template-literal types, and built-in utility types (`Partial`/`Pick`/`Omit`/`Record`/`NonNullable`/`Exclude`/`Extract`).

---

## 10. Repo-wide conventions (AGENTS / CONTRIBUTING)

### `AGENTS.md` (VERBATIM highlights)

```md
## Code Comments
Do not add code comments to the code you write, ever.

## Design
Read @docs/design.md
```
(Plus: `CLAUDE.md` is just `@AGENTS.md`; `apps/app/CLAUDE.md` → `@AGENTS.md`.)

### `apps/app/AGENTS.md` (VERBATIM — Next.js version warning)

```md
# This is NOT the Next.js you know
This version has breaking changes — APIs, conventions, and file structure may all differ from your
training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code.
```

### `CONTRIBUTING.md` — the "House style" section (VERBATIM, explains most review comments)

```md
- packages/ui is the only place components come from, and you don't override its styles at the call site.

**Comments say why, not what.** The code already says what it does. A comment earns its place by
recording the thing that isn't in the diff — the bug that made this necessary, the obvious approach
that turned out wrong, the constraint that looks arbitrary until you know.

**Nothing about a person is guessed.** This is a CRM: a confidently wrong fact about a real customer
is worse than a blank field, because nobody can tell it's wrong. Code that fills a gap with a
plausible value is a bug here even when it's convenient.
```

### Tooling conventions (`biome.jsonc`)
- **Biome** (not Prettier/ESLint) for format + lint. **Tab indentation**, **double quotes**, auto-organize-imports.
- `packages/ui/src/components` is **excluded from Biome** (vendored shadcn source is left as-is).
- Per-area lint domains: `apps/app` gets `next` + `react` recommended rules.

---

## 11. Distilled house-style checklist (apply to any Next.js + Tailwind app)

A portable checklist to get the same polished, un-templated feel.

**Tokens & theme (do this first — biggest ROI):**
- [ ] One CSS file owns all tokens (`:root` + `.dark` + a Tailwind `@theme inline` map). Nothing hardcodes a color.
- [ ] Pick **flat white bg + one brand color + untinted neutral greys**. Don't tint greys without a reason.
- [ ] **Only two filled colors:** `--primary` (the action you want) and `--destructive` (the one you can't undo). Everything else is an outline/ghost/secondary chip.
- [ ] `--primary` and `--destructive` are **identical in light and dark**. Only `--ring` may lighten in dark.
- [ ] **One radius token**, identical across themes (`--radius: 5px`). Expose `rounded-sm/md/lg` = 4/5/8px. Never a literal radius at a call site.
- [ ] **One focus-ring width everywhere** (`ring-2`), one focus color (`--ring`).
- [ ] Modal scrim via a dedicated `--overlay` token that is **visible in dark** (~`black/0.55`), not `black/10`.
- [ ] Chart colors are a categorical set related to the brand, not a random single-hue ramp.
- [ ] Restrained shadow family (opacity ≤ 0.12); depth from borders + `bg-card` vs `bg-background`, not big drop shadows.
- [ ] Custom thin neutral scrollbars.

**Density & type (data-app scale):**
- [ ] Default control height ~32px (`h-8`); control/body text `text-xs`; titles `text-sm font-medium`.
- [ ] Geist Sans + Geist Mono; heading font = body font.
- [ ] Derive hover/active states from the base token with `color-mix(… black 12%/22%)` rather than new hardcoded colors.

**Component discipline (keeps it consistent = un-templated):**
- [ ] All UI comes from one shared package; **never override component color/typography with `className`** (layout classes only: `max-w-*`, `mx-auto`, `mt-*`, `gap-*`).
- [ ] Semantic tokens only (`bg-primary`, `text-muted-foreground`) — never raw `bg-blue-500`/`text-emerald-600`. Status → `Badge` variants or `text-destructive`.
- [ ] `gap-*` not `space-x/y-*`; `size-*` not `w-* h-*`; `truncate` shorthand; `cn()` not template-literal ternaries.
- [ ] No manual `dark:` color overrides; no manual `z-index` on overlays.
- [ ] Forms use `FieldGroup`+`Field`; validation via `data-invalid`(field)+`aria-invalid`(control). Use the right control per the picker table.
- [ ] Use real components, never custom markup: `Alert` for callouts, `Empty` for empty states, `Separator` for rules, `Skeleton` for loading, `Badge` for status chips.
- [ ] Items live inside their Group (`SelectItem`→`SelectGroup`, etc.). Overlays picked from the overlay table. Dialog/Sheet/Drawer always have a Title (`sr-only` if hidden). Full Card composition.
- [ ] Icons: pass as objects, `data-icon` for button icons, no sizing classes inside components.
- [ ] Loading text uses a `shimmer` utility, not hand-rolled keyframes.

**React engineering (makes it feel fast/solid):**
- [ ] **Ban `useEffect`** (lint `no-restricted-syntax`); replace with derived-during-render, a query lib, event handlers, `key` resets, or a single `useMountEffect` escape hatch.
- [ ] Server-first: stream the shell with `<Suspense>`, parallelize independent fetches (`Promise.all` / sibling async components), minimize props serialized to client components.
- [ ] Derive state in render; never `useEffect(() => setX(derive(y)), [y])`.
- [ ] Composition over boolean props: explicit variants + compound components + `{state, actions, meta}` context; children over render props. React 19: `ref` as prop, `use()` over `useContext()`.

**URL state (nuqs):**
- [ ] Filters/search/sort/pagination/tab live in the URL via nuqs. One shared parser map (client-safe) + a `nuqs/server` cache/loader/serializer for RSC.
- [ ] Constrained values use `parseAsStringLiteral`/enum parsers; clear with `null`; debounce server-driven search with `limitUrlUpdates: debounce(300)` + `shallow: false` + `startTransition`.

**Motion (last, optional polish):**
- [ ] Icon hover choreography, animated nav via View Transitions, animated link underlines — all gated behind `prefers-reduced-motion`.

---

## 12. Collecct adoption notes — copy directly vs adapt

Collecct already has a **custom Next.js + Tailwind** frontend (top-bar shell, Bid sidebar, 3-pane console, faceted Opportunity filters, Dashboard) and its **own emerging design system**. Frame adoption as merging these rules/tokens into that existing app.

### Copy directly (low risk, high payoff)
1. **The token architecture and philosophy.** Adopt the "one CSS file, `:root`+`.dark`+`@theme inline`" structure and the four rules: only-two-fills, brand color identical across themes, one radius token, visible dark scrim. This is the single biggest "professional look" lever and is framework-agnostic.
   - **Swap the brand hue.** `#006b4f` is *Comp's* green — do **not** ship it in Collecct. Replace `--primary` (and `--ring` light, plus a lightened `--ring` dark) with Collecct's brand color; keep everything else (flat white, untinted greys, `--destructive: #ae2e24`, borders, radius, shadows) verbatim. The govcon audience suits a restrained, serious palette, which this system already is.
2. **The density/type scale** (`h-8` controls, `text-xs` body, `text-sm` titles, Geist). A CRM pipeline is data-dense; this scale reads as a serious tool. Align Collecct's controls to it.
3. **The engineering playbooks wholesale:** no-`useEffect` (add the `no-restricted-syntax` lint + `useMountEffect`), server-first Suspense/parallel-fetch rules, composition-over-boolean-props, TS best-practices. These are pure discipline, no visual coupling — adopt as-is.
4. **The nuqs URL-state pattern** for Collecct's Opportunity/Pipeline filters, search, sort, pagination, and active-Bid selection. Note our memory says filters are currently *client-side + localStorage-persisted*; **migrating them to nuqs** gives shareable/bookmarkable filtered pipeline views, a working back button, and lets server components read filters for SSR. High-value, directly on-point.
5. **The shadcn styling/forms/composition/icons rule set** as Collecct's lint-in-review checklist — especially "className for layout only," "semantic tokens only," "no `space-*`," "status via Badge/`text-destructive`," "real components not custom divs." This is what stops a growing app from drifting into templated inconsistency.
6. **The two CRM-specific correctness rules** from CONTRIBUTING (they belong in Collecct verbatim): *comments say why not what*, and **"nothing about a person is guessed"** — a confidently wrong fact about a real contact/agency is worse than a blank field. Directly relevant to our contact-enrichment/company-enrich pipelines.
7. **Consistency fixes to audit for in Collecct now** (the ADR's incidental bug list): radius that differs between themes; focus rings of inconsistent width; an invisible dark modal scrim; any component using the brand/primary token to mean "dark ink"; chart ramps unrelated to the palette.

### Adapt (needs a Collecct-specific decision)
1. **shadcn base (radix vs base).** This repo uses **radix** (`asChild`, radix Select/Slider/Accordion APIs). Collecct must pick one base and apply the matching column of `base-vs-radix.md` consistently. If Collecct's UI isn't shadcn-based at all, adopt the *rules* (semantic tokens, composition, forms) against whatever primitives it uses.
2. **Single-UI-package rule.** The repo's "`packages/ui` is the only source of UI" assumes a monorepo. Collecct may be a single app — adapt to "one `components/ui` directory is the only source; app code never restyles it." Same intent, simpler structure.
3. **Motion system.** The CDS icon-hover choreography is keyed to **IBM Carbon** icon names (`MOTION_BY_ICON`). If Collecct uses Lucide/another set, either re-map motions to your icon names or skip it. The *generic* motion utilities (link-underline family, View-Transition nav, `alert-attention`, `prefers-reduced-motion` gating) port directly. Treat motion as final polish, not foundational.
4. **AI Elements vs house chat primitives.** If/when Collecct's agent surfaces get a chat UI, decide between Vercel **AI Elements** (broad, AI-SDK-oriented) and building house `MessageScroller`/`Message`/`Bubble` primitives. Either way, follow the rule: never hand-roll bubbles/scroll containers/attachment cards; let the scroller own streaming-follow/anchor/jump-to-latest; loading = `shimmer`.
5. **Next.js version caveat.** `apps/app/AGENTS.md` warns this is a bleeding-edge Next.js with breaking changes — its exact file conventions (and possibly React 19 `use()`/ref-as-prop) may differ from Collecct's Next version. Adopt the *patterns*; verify APIs against Collecct's installed Next/React.
6. **Biome vs Collecct's formatter.** The repo standardizes on Biome (tabs, double quotes) and excludes vendored `ui/components` from linting. Adopt the *convention of excluding vendored component source*; keep Collecct's existing formatter unless a switch is separately warranted.

### Suggested adoption order for Collecct
1. Re-point the token layer (swap in Collecct's brand color; adopt only-two-fills, one-radius, visible-dark-scrim, one-ring). Ship the two "genuine bug fixes" (theme-radius parity, scrim) even if the palette rollout is staged — exactly as the ADR offers.
2. Align density/type scale + button/input/card to the shared components; enforce "className for layout only" + semantic tokens in review.
3. Add the no-`useEffect` lint + `useMountEffect`; sweep existing effects into derived state / query / handlers.
4. Migrate Opportunity/Pipeline filters + search to nuqs (shared parser map + server cache).
5. Layer motion polish last.

---

### File map (where each rule lives, for re-reading)
- Tokens/palette/motion: `packages/ui/src/styles/globals.css`
- Design principles: `docs/design.md`
- Palette decision (the *why*): `adrs/comp-palette.md`; ADR process: `adrs/README.md`
- shadcn rules: `.agents/skills/shadcn/SKILL.md` + `rules/{styling,forms,composition,icons,chat,base-vs-radix}.md`
- No-effect: `.agents/skills/no-use-effect/SKILL.md`
- Server-first React: `.agents/skills/vercel-react-best-practices/` (SKILL.md + `rules/*`)
- Composition: `.agents/skills/vercel-composition-patterns/` (SKILL.md + AGENTS.md + `rules/*`)
- URL state: `.agents/skills/nuqs/` (SKILL.md, README.md, AGENTS.md + `references/*`)
- AI chat UI: `.agents/skills/ai-elements/SKILL.md` (+ `references/*`, `scripts/*`)
- TS types: `.agents/skills/typescript-advanced-types/SKILL.md` (+ `references/details.md`)
- Web-interface audit skill: `.agents/skills/web-design-guidelines/SKILL.md` (fetches Vercel's web-interface-guidelines `command.md` live)
- Repo conventions: `AGENTS.md`, `CLAUDE.md`, `apps/app/AGENTS.md`, `apps/app/CLAUDE.md`, `CONTRIBUTING.md`, `biome.jsonc`
- Grounding components: `packages/ui/src/components/{button,card,input,icon}.tsx`
```

Note on `web-design-guidelines`: its `SKILL.md` is a thin wrapper — it fetches Vercel's live "Web Interface Guidelines" from `https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md` and audits files against them in `file:line` format. There is no static rule text in the repo to reproduce; the rules live at that URL.
