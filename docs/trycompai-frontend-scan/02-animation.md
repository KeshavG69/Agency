# CRM Frontend Study — 02: The Animation & Motion System

Source repo root (all paths below are relative to it unless absolute):
`/private/tmp/claude-501/-Users-keshav-Developer-Others-AI-Agency/ad094025-ce2f-4d46-9f0e-7189842d9f45/scratchpad/crm`

Target: **Collecct** — Next.js 15.5.19 + React 19.0.0 + Tailwind, single `frontend/app/globals.css` (3029 lines).

---

## 0. TL;DR — the shape of the system

There is **no motion library at all**. Verified: grepping for `framer-motion`, `motion/react`, `react-spring`, `gsap`, `autoAnimate` across every `*.ts`, `*.tsx`, `*.json` in the repo returns **zero** hits. The entire motion system is:

1. **CSS custom properties** for three durations (`--duration-exit/enter/move`).
2. **Native View Transitions**, driven by React 19's `<ViewTransition>` + Next.js `experimental.viewTransition`, with a **global `animation: none` reset** so animation is strictly opt-in.
3. **`tw-animate-css`** (the Tailwind v4 successor to `tailwindcss-animate`) providing the shadcn/Radix `animate-in` / `animate-out` enter-exit vocabulary.
4. A hand-rolled **`.cds-icon[data-motion=…]`** hover-motion system with a spring curve and 16 named motions, auto-assigned per icon.
5. A large **`.link-hover--*`** underline-effect library (present in CSS, currently **unused** by app code).
6. Two `prefers-reduced-motion` postures — `no-preference` gates (opt-in) and `reduce` overrides (opt-out).

The philosophy, stated as a rule: **chrome persists, content transitions, everything is opt-in, exits are faster than enters.**

---

## 1. Duration & easing tokens

### 1.1 The tokens — VERBATIM

`packages/ui/src/styles/globals.css:399-403`

```css
:root {
	--duration-exit: 150ms;
	--duration-enter: 210ms;
	--duration-move: 400ms;
}
```

Note this is a **second `:root` block**, declared ~400 lines after the color `:root` at line 9. It sits immediately above the View-Transition keyframes it feeds — the timing tokens are physically colocated with the only thing that consumes them. They are *not* registered in `@theme inline` (lines 141-207), so they are **not** exposed as Tailwind utilities. They are pure CSS vars for the `::view-transition-*` rules only.

### 1.2 Why exit (150ms) < enter (210ms) < move (400ms)

This is the classic asymmetric-motion rule, and the CSS makes the reasoning legible:

- **Exit = 150ms.** The outgoing page is already stale — the user has decided to leave. Lingering on it costs perceived latency with zero information gain. Fast exit = the app "gets out of the way."
- **Enter = 210ms.** The incoming page is the payload. It gets 40% more time so the user's eye can land on new content without it snapping into place. Slower enter reads as "arriving," faster enter reads as "flickering."
- **Move = 400ms.** The *positional* slide is nearly 2× the enter fade. Position is the continuity signal ("this came from over there"); it must be slow enough to be tracked by the eye, whereas opacity is just a veil. Splitting duration by *property* rather than using one duration everywhere is the single most transferable idea here.
- **Ratio ≈ 1 : 1.4 : 2.67.**

### 1.3 The deliberate sequencing (enter delayed by exit duration)

`packages/ui/src/styles/globals.css:462-467` — VERBATIM:

```css
	::view-transition-new(.nav-forward) {
		--slide-offset: 60px;
		animation:
			var(--duration-enter) ease-out var(--duration-exit) both vt-fade,
			var(--duration-move) ease-in-out both vt-slide;
	}
```

The third value in the `animation` shorthand — `var(--duration-exit)` — is the **animation-delay**. The new view's *fade* does not begin until the old view's fade has fully finished (150ms). So the fade is a **cross-dissolve avoided**: old fades out 0→150ms, new fades in 150→360ms. There is never a moment where two semi-transparent copies of the page are stacked, which is what makes naive view transitions look muddy.

Meanwhile the **slide has no delay** — `var(--duration-move) ease-in-out both vt-slide` starts at t=0 on both old and new. So the two layers *move together* for the whole 400ms while only one of them is visible at a time. That's the trick: **shared positional continuity, sequenced opacity.**

Timeline for a `nav-forward`:

```
t=0                150               360        400
|---- old fade out ----|
|---- old slide (reverse, -60px → 0 reversed) --------|
|---- new slide (60px → 0) ---------------------------|
                   |---- new fade in ----|
```

Easing split is also deliberate:
- fades use **`ease-out` on enter, `ease-in` on exit** (accelerate away, decelerate in — standard Material-ish asymmetry),
- the slide uses **`ease-in-out`** because it is a single continuous positional gesture that both starts and ends at rest.

### 1.4 The `.cds-icon` spring curve

`packages/ui/src/styles/globals.css:262-264` — VERBATIM:

```css
		.cds-icon {
			transition: transform 240ms cubic-bezier(0.34, 1.56, 0.64, 1);
		}
```

`cubic-bezier(0.34, 1.56, 0.64, 1)` — the y2 control point of **1.56 > 1** means the curve **overshoots** past the target and settles back. This is the "ease-out-back" spring. 240ms is long for a hover, but overshoot needs the extra time to read as bounce rather than jitter. It applies to `transform` only, so hover color changes (handled elsewhere) do not inherit the bounce.

### 1.5 Other easings in the codebase (component level)

`packages/ui/src/components/message-scroller.tsx:102`:
- `data-[active=false]:ease-[cubic-bezier(0.7,0,0.84,0)]` — **ease-in-quint**, used for the *hide* direction.
- `data-[active=true]:ease-[cubic-bezier(0.23,1,0.32,1)]` — **ease-out-quint**, used for the *show* direction.
- and asymmetric durations on the same element: `duration-200` base, `data-[active=false]:duration-400`.

Interesting: here the *exit* is the **slower** one (400 vs 200). That's the opposite of the page rule — and correct, because this is a floating "scroll to bottom" button that should slip away unobtrusively rather than pop out. The rule is not "exit is always faster," it is "exit should not compete for attention."

Full tally of duration/ease/animate utility usage across `apps/` + `packages/`:

```
  13 animate-in            8 duration-100        2 ease-out
  12 animate-out           2 duration-300        1 ease-[cubic-bezier(0.7,0,0.84,0)]
   2 animate-spin          1 duration-400        1 ease-[cubic-bezier(0.23,1,0.32,1)]
   2 animate-pulse         1 duration-200
   1 animate-accordion-up  1 animate-none
   1 animate-accordion-down
```

That is the **whole** motion surface of a full CRM app. ~35 motion class-uses total.

---

## 2. The View Transition setup — "animation is opt-IN"

### 2.1 The global reset — VERBATIM

`packages/ui/src/styles/globals.css:434-442`:

```css
::view-transition-group(*),
::view-transition-new(*) {
	animation: none;
}

::view-transition-old(*) {
	animation: none;
	opacity: 0;
}
```

This is the keystone of the whole system. By default the browser gives **every** `::view-transition-old/new` pseudo-element a built-in cross-fade, and every `::view-transition-group` a built-in morph. That default is what makes View Transitions look janky in real apps: unrelated elements smear across the screen because the UA decided to animate them.

This block **kills all of it**. `animation: none` on the group means captured elements **snap** to their new position with zero morph. `animation: none; opacity: 0` on `old` means the outgoing snapshot is **instantly invisible** rather than fading — so nothing is ever double-drawn.

Consequence: **out of the box, this app's view transitions are visually identical to no view transitions at all.** Every visible transition in the app exists because someone wrote a rule that re-enables it for a *specific named type*. That is the opt-in discipline.

### 2.2 `default: "none"` on `<ViewTransition>` — VERBATIM

`apps/app/components/page-transition.tsx` (whole file, 29 lines):

```tsx
import type * as React from "react";
import { ViewTransition } from "react";

const directional = {
	"nav-forward": "nav-forward",
	"nav-back": "nav-back",
	"nav-lateral": "nav-lateral",
	default: "none",
} as const;

const enter = {
	"nav-forward": "nav-forward",
	"nav-back": "nav-back",
	"nav-lateral": "nav-lateral",
	default: "none",
} as const;

export function PageTransition({ children }: { children: React.ReactNode }) {
	return (
		<ViewTransition
			enter={enter}
			exit={directional}
			update={directional}
			default="none"
		>
			{children}
		</ViewTransition>
	);
}
```

This is the **same discipline enforced a second time, in React**. React's `<ViewTransition>` accepts either a class-name string or a map from *transition type* → class name. Here every phase (`enter`, `exit`, `update`) gets a map, and every map ends in `default: "none"`.

So the opt-in is **belt and braces**:
- CSS side: `::view-transition-*(*) { animation: none }` — nothing animates unless a rule names it.
- React side: `default: "none"` + top-level `default="none"` — no class is applied to the pseudo-element unless the navigation **declared a transition type**.

A navigation that forgets to pass `transitionTypes` produces `class="none"`, matches no `::view-transition-new(.nav-*)` selector, and therefore renders instantly. **Silent no-op is the failure mode, not a wrong animation.** That is exactly the right default for a data-dense CRM.

Note also that `enter` and `directional` are **structurally identical objects** — duplicated deliberately (not aliased) so that the enter phase can diverge from exit/update later without a refactor. Small thing, but it signals the maps are considered a policy surface.

### 2.3 Enabling the feature

`apps/app/next.config.ts`:

```ts
	experimental: {
		viewTransition: true,
	},
```

Requires Next 16 / React 19.2 in this repo (`packages/ui/package.json`: `"next": "16.2.12"`, `"react": "19.2.4"`; peerDeps `next: ^16.0.0`, `react: ^19.2.0`).

### 2.4 The three named nav types

`packages/ui/src/styles/globals.css:449-481` — VERBATIM (the entire opt-in block):

```css
@media (prefers-reduced-motion: no-preference) {
	::view-transition-new(.nav-lateral) {
		animation:
			var(--duration-enter) ease-out both vt-fade,
			var(--duration-enter) ease-out both vt-rise;
	}

	::view-transition-old(.nav-forward) {
		--slide-offset: -60px;
		animation:
			var(--duration-exit) ease-in both vt-fade reverse,
			var(--duration-move) ease-in-out both vt-slide reverse;
	}
	::view-transition-new(.nav-forward) {
		--slide-offset: 60px;
		animation:
			var(--duration-enter) ease-out var(--duration-exit) both vt-fade,
			var(--duration-move) ease-in-out both vt-slide;
	}

	::view-transition-old(.nav-back) {
		--slide-offset: 60px;
		animation:
			var(--duration-exit) ease-in both vt-fade reverse,
			var(--duration-move) ease-in-out both vt-slide reverse;
	}
	::view-transition-new(.nav-back) {
		--slide-offset: -60px;
		animation:
			var(--duration-enter) ease-out var(--duration-exit) both vt-fade,
			var(--duration-move) ease-in-out both vt-slide;
	}
}
```

The three keyframes it uses — `packages/ui/src/styles/globals.css:405-432`, VERBATIM:

```css
@keyframes vt-fade {
	from {
		filter: blur(3px);
		opacity: 0;
	}
	to {
		filter: blur(0);
		opacity: 1;
	}
}

@keyframes vt-slide {
	from {
		translate: var(--slide-offset);
	}
	to {
		translate: 0;
	}
}

@keyframes vt-rise {
	from {
		transform: translateY(12px);
	}
	to {
		transform: translateY(0);
	}
}
```

Design notes on the keyframes:

- **`vt-fade` bundles a 3px blur with the opacity ramp.** A pure opacity fade reads as "a ghost"; adding a small blur reads as "out of focus, coming into focus" — it hides sub-pixel text shimmer during the fade and makes 210ms feel like more than it is. 3px is very restrained.
- **Every keyframe is written in the "enter" direction only**, then the exits are produced by appending `reverse` to the shorthand (`… both vt-fade reverse`). One keyframe, two directions, guaranteed symmetry. This is why there is no `vt-fade-out`.
- **`vt-slide` is parameterised by `--slide-offset`**, set per-selector. `nav-forward` = old slides to -60px, new comes from +60px (content moves right-to-left, "going deeper"). `nav-back` = mirrored. The *same two rules* express both directions purely via the custom property. 60px is a small "shift," not a full-width page slide — this is a CRM, not a phone.
- **`vt-slide` uses the `translate` property, `vt-rise` uses `transform`.** Deliberate: they must be able to compose without one clobbering the other, and `translate` is an independent transform property in modern CSS.
- **`vt-rise` (12px, no delay) is the whole of `nav-lateral`.** Lateral navigation (sibling → sibling, e.g. Companies → Contacts) gets *no horizontal slide at all* — just fade + a 12px lift, both at `--duration-enter`, and **no old-side rule whatsoever** (the old view is already invisible from the global `opacity: 0`). Sideways movement would imply hierarchy that doesn't exist between siblings.

### 2.5 What is actually wired up — and what isn't

`transitionTypes` appears in exactly **two** places, and both pass `nav-lateral`:

- `apps/app/components/app-icon-rail.tsx:71` — the primary rail links:
  ```tsx
  				<Link
  					href={item.href}
  					aria-current={active ? "page" : undefined}
  					transitionTypes={["nav-lateral"]}
  				>
  ```
- `apps/app/app/(app)/settings/settings-sidebar.tsx:49` — the settings sub-nav, identical prop.

`nav-forward` and `nav-back` are **defined in CSS and in the React type maps but never triggered by any call site.** Grepping `nav-forward|nav-back` across the repo returns only the CSS rules and the two map literals in `page-transition.tsx`.

Read this as: the *forward/back* vocabulary is a **reserved, pre-built slot** for drill-down navigation (list → record detail) that the app currently satisfies with a Sheet instead (`RecordSheetHost` in `apps/app/app/(app)/layout.tsx`). The grammar was designed whole; only the lateral case is currently used. Worth copying the *grammar*, not just the used branch.

Also note: the **mobile** rail links (`MobileRailLink`, `apps/app/components/app-icon-rail.tsx:78-105`) deliberately **omit** `transitionTypes` — mobile nav closes a Sheet and navigates, and stacking a view transition on top of a sheet-close animation would collide. Restraint, not oversight.

### 2.6 Where the boundary sits

`PageTransition` wraps only `PageShell` — `apps/app/components/page-shell.tsx:5-23`:

```tsx
function PageShell({ className, ...props }: React.ComponentProps<"div">) {
	return (
		<PageTransition>
			<main
				data-slot="page-shell-scroll"
				className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 pt-4 pb-4 md:px-6 md:pt-6 md:pb-6"
			>
```

`PageShell` is used by **9 page files**. The header, rail and layout chrome live *outside* it (`apps/app/app/(app)/layout.tsx`). So the transition boundary is exactly the scrollable content column — which is the next section's point.

---

## 3. `view-transition-name`: chrome, not content

Complete inventory — every occurrence in the repo:

| File:line | Element | Name |
|---|---|---|
| `apps/app/components/app-header.tsx:62` | `<header>` (top bar, h-12) | `app-header` |
| `apps/app/components/app-icon-rail.tsx:122` | `<nav aria-label="Primary">` (w-14 icon rail) | `app-rail` |
| `apps/app/app/(app)/settings/settings-sidebar.tsx:62` | `<aside>` (desktop w-56 settings nav) | `settings-sidebar` |
| `apps/app/app/(app)/settings/settings-sidebar.tsx:80` | `<nav>` (mobile scroll-strip settings nav) | `settings-sidebar` |
| `apps/app/components/page-shell.tsx:34` | `<header data-slot="page-shell-header">` | `page-header` |

VERBATIM samples:

`apps/app/components/app-header.tsx:62`
```tsx
		<header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 [view-transition-name:app-header]">
```

`apps/app/components/app-icon-rail.tsx:120-123`
```tsx
			<nav
				aria-label="Primary"
				className="hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-3 md:flex [view-transition-name:app-rail]"
			>
```

`apps/app/components/page-shell.tsx:31-37`
```tsx
		<header
			data-slot="page-shell-header"
			className={cn(
				"flex flex-col gap-3 [view-transition-name:page-header]",
				className,
			)}
```

### Why these five and nothing else

**Zero rows, cards, avatars, or list items carry a `view-transition-name`.** No `view-transition-name: card-${id}` shared-element choreography anywhere. That is a decision, not an omission.

The named elements are **the four pieces of persistent chrome**: global header, global rail, the settings sub-nav, and the page title block. Naming an element does **two** things here:

1. It **lifts the element out of the root snapshot** into its own `::view-transition-group`. Because the global reset sets `animation: none` on `::view-transition-group(*)` and `opacity: 0` on `::view-transition-old(*)`, a named element is one that **does not participate in the page fade/slide at all** — it just snaps to its new geometry. The header and rail are *identical* across navigations, so "snap" means "visually motionless."
2. It **guarantees the content transition can't paint over it**, via the z-index rule below.

So the naming convention here is inverted from the usual tutorial framing. Elsewhere `view-transition-name` means "animate this specially." Here it means: **"exclude this from the transition; it is furniture."** The user's mental anchor — the rail they just clicked, the header showing the workspace — must not move, blur, or slide while the content column does. That is what makes a 400ms page slide feel snappy rather than sluggish: the frame is rock-steady, only the picture changes.

`page-header` is the interesting middle case: it *is* content (its text changes per page) but it's named anyway, so the title snaps to the new string while the body beneath it fades+rises. That keeps the "you are here" label instantaneous.

Note also `settings-sidebar` is applied to **two mutually-exclusive elements** (desktop `md:block` aside at :62, mobile `md:hidden` nav at :80). Because only one is ever rendered-visible at a time this is safe — but it is a duplicate-name hazard worth knowing about: two simultaneously-visible elements sharing a `view-transition-name` abort the whole transition.

### The z-index rule — VERBATIM

`packages/ui/src/styles/globals.css:444-447`:

```css
::view-transition-group(app-header),
::view-transition-group(app-rail) {
	z-index: 100;
}
```

The view-transition pseudo-element tree is a **separate stacking context** from the page — normal `z-index` on your DOM has no effect there. Groups are stacked in DOM order by default, which means the (large) root snapshot, containing the sliding content, can render **above** the header/rail snapshots. The result is content visibly sliding *over* the header for the duration of the transition.

`z-index: 100` on just those two groups forces the chrome to the top of the transition layer. `settings-sidebar` and `page-header` are deliberately **not** raised — they live inside the content column and should stack normally with it.

This is a small rule that fixes the single most common "why does my view transition look broken" bug. Copy it.

---

## 4. The `.cds-icon` hover motion system

This is the most distinctive piece of the whole design system: **every icon in the app has an opinion about how it moves on hover, derived from what the icon means.**

### 4.1 The CSS — VERBATIM

`packages/ui/src/styles/globals.css:256-334` (entire block):

```css
@layer utilities {
	.cds-icon {
		transform-origin: center;
	}

	@media (prefers-reduced-motion: no-preference) {
		.cds-icon {
			transition: transform 240ms cubic-bezier(0.34, 1.56, 0.64, 1);
		}

		.cds-icon[data-motion="pop"] {
			--cds-hover: scale(1.14);
		}
		.cds-icon[data-motion="scale"] {
			--cds-hover: scale(1.1);
		}
		.cds-icon[data-motion="lift"] {
			--cds-hover: translateY(-2px) scale(1.06);
		}
		.cds-icon[data-motion="turn"] {
			--cds-hover: rotate(90deg);
		}
		.cds-icon[data-motion="rotate"] {
			--cds-hover: rotate(-12deg);
		}
		.cds-icon[data-motion="flip"] {
			--cds-hover: rotateY(180deg);
		}
		.cds-icon[data-motion="nudge-right"] {
			--cds-hover: translateX(3px);
		}
		.cds-icon[data-motion="nudge-left"] {
			--cds-hover: translateX(-3px);
		}
		.cds-icon[data-motion="nudge-up"] {
			--cds-hover: translateY(-3px);
		}
		.cds-icon[data-motion="nudge-down"] {
			--cds-hover: translateY(3px);
		}
		.cds-icon[data-motion="launch"] {
			--cds-hover: translate(2px, -2px);
		}

		:where(
				.cds-icon:hover,
				:where(
						button,
						a,
						[role="button"],
						[role="menuitem"],
						[data-slot="dropdown-menu-item"],
						[data-slot="command-item"],
						[data-slot="sidebar-menu-button"],
						.icon-hover
					):hover
					.cds-icon
			) {
			transform: var(--cds-hover, none);

			&[data-motion="spin"] {
				animation: cds-spin 1.1s linear infinite;
			}
			&[data-motion="wiggle"] {
				animation: cds-wiggle 0.5s ease-in-out;
			}
			&[data-motion="swing"] {
				animation: cds-swing 0.6s ease-in-out;
				transform-origin: top center;
			}
			&[data-motion="bounce"] {
				animation: cds-bounce 0.6s ease;
			}
			&[data-motion="pulse"] {
				animation: cds-pulse 0.9s ease-in-out infinite;
			}
		}
	}
}
```

And the keyframes — `packages/ui/src/styles/globals.css:336-397`, VERBATIM:

```css
@keyframes cds-spin {
	to {
		transform: rotate(360deg);
	}
}
@keyframes cds-wiggle {
	0%,
	100% {
		transform: rotate(0);
	}
	20% {
		transform: rotate(-9deg);
	}
	40% {
		transform: rotate(7deg);
	}
	60% {
		transform: rotate(-5deg);
	}
	80% {
		transform: rotate(3deg);
	}
}
@keyframes cds-swing {
	0%,
	100% {
		transform: rotate(0);
	}
	25% {
		transform: rotate(12deg);
	}
	50% {
		transform: rotate(-8deg);
	}
	75% {
		transform: rotate(4deg);
	}
}
@keyframes cds-bounce {
	0%,
	100% {
		transform: translateY(0);
	}
	30% {
		transform: translateY(-3px);
	}
	55% {
		transform: translateY(1px);
	}
	75% {
		transform: translateY(-1px);
	}
}
@keyframes cds-pulse {
	0%,
	100% {
		transform: scale(1);
	}
	50% {
		transform: scale(1.12);
	}
}
```

### 4.2 The two-tier architecture

Two mechanically different tiers, both keyed off the same `data-motion` attribute:

**Tier A — transform motions (11 of them), driven by a custom property.**
`pop`, `scale`, `lift`, `turn`, `rotate`, `flip`, `nudge-right/left/up/down`, `launch`.
Each rule sets **only** `--cds-hover`; it never sets `transform`. A single rule at the bottom does `transform: var(--cds-hover, none)`. This is a **CSS-variable dispatch table**: adding a 12th transform motion is one 3-line rule, no new selector in the hover block. The `none` fallback means an icon with an unrecognised `data-motion` silently doesn't move.

**Tier B — keyframe motions (5 of them), nested inside the hover selector.**
`spin`, `wiggle`, `swing`, `bounce`, `pulse`. These need timing curves that a single `transition` can't express (multi-stop, or infinite). They're nested with `&[data-motion="…"]` inside the same `:where()` block.

Note `swing` also **re-declares `transform-origin: top center`** inside the hover rule, overriding the base `transform-origin: center` at line 258 — a pendulum must pivot from its hanger, not its middle. Nice detail.

**Damped oscillation is the shared grammar of the keyframes.** `cds-wiggle` goes -9° → +7° → -5° → +3° → 0. `cds-swing` goes +12° → -8° → +4° → 0. Each successive swing is ~70-75% of the previous. That is what makes them read as physical rather than as a stutter. The amplitudes are also tiny — max 12° — because these are 16px icons.

Two motions are **infinite** (`cds-spin` at 1.1s linear, `cds-pulse` at 0.9s) and stop when hover ends; the other three are one-shot and complete even if the pointer leaves.

### 4.3 The decisive selector

```css
		:where(
				.cds-icon:hover,
				:where(button, a, [role="button"], [role="menuitem"],
						[data-slot="dropdown-menu-item"], [data-slot="command-item"],
						[data-slot="sidebar-menu-button"], .icon-hover):hover
					.cds-icon
			)
```

Three things going on:

1. **The whole selector is wrapped in `:where()`, so its specificity is 0-0-0.** That means any `className` a consumer passes — a Tailwind `rotate-90`, a `transform-none` — wins without `!important`. The motion system is **overridable by default**. This is the single cleverest line in the file.
2. **The icon animates when its *ancestor control* is hovered, not just itself.** Hovering anywhere on a `<Button>` fires the icon's motion. Without this, users would have to hover the 16px glyph precisely, and the effect would essentially never be seen. The ancestor list is enumerated explicitly (button, a, role=button, role=menuitem, and three shadcn `data-slot` values) rather than using something broad, so the motion doesn't fire on incidental containers.
3. **`.icon-hover` is the manual escape hatch** — put that class on any wrapper to make it an icon-motion trigger.

Everything is inside `@media (prefers-reduced-motion: no-preference)`, so under `reduce` the icons have **no `transition` at all** — not "0.01ms transition", genuinely no declaration. See §6.

### 4.4 How a component opts in — the `Icon` wrapper

`packages/ui/src/components/icon.tsx` — the motion registry, VERBATIM:

```tsx
export const ICON_MOTIONS = [
	"pop", "scale", "lift", "turn", "rotate", "flip", "spin",
	"wiggle", "swing", "bounce", "pulse", "nudge-right", "nudge-left",
	"nudge-up", "nudge-down", "launch", "none",
] as const;

export type IconMotion = (typeof ICON_MOTIONS)[number];
```
(reformatted onto fewer lines; original is one entry per line, `icon.tsx:5-20`)

`packages/ui/src/components/icon.tsx:38-70` — VERBATIM:

```tsx
const MOTION_BY_ICON: Record<string, IconMotion> = {
	ArrowRight: "nudge-right",
	ChevronRight: "nudge-right",
	Play: "nudge-right",
	SendAlt: "nudge-right",
	Logout: "nudge-right",
	ArrowLeft: "nudge-left",
	ChevronLeft: "nudge-left",
	ArrowUp: "nudge-up",
	ArrowDown: "nudge-down",
	ChevronDown: "nudge-down",
	Download: "bounce",
	Launch: "launch",
	Settings: "spin",
	Renew: "spin",
	Restart: "spin",
	RecentlyViewed: "spin",
	Earth: "spin",
	Add: "turn",
	Close: "turn",
	TrashCan: "wiggle",
	Tools: "wiggle",
	Locked: "wiggle",
	Password: "wiggle",
	MagicWand: "wiggle",
	Search: "wiggle",
	WarningAlt: "wiggle",
	Misuse: "pulse",
	Information: "pulse",
	Security: "pulse",
	Light: "pulse",
	Ai: "pulse",
	Chip: "pulse",
	Asleep: "swing",
};
```

`packages/ui/src/components/icon.tsx:72-95` — VERBATIM:

```tsx
function iconName(icon: CarbonIcon): string | undefined {
	return icon.displayName ?? icon.render?.displayName ?? icon.render?.name;
}

export function iconMotionFor(icon: CarbonIcon): IconMotion {
	const name = iconName(icon);
	return (name && MOTION_BY_ICON[name]) || "pop";
}

export type IconProps = CarbonIconProps & {
	icon: CarbonIcon;
	motion?: IconMotion;
};

export function Icon({ icon: Glyph, motion, className, ...props }: IconProps) {
	const resolved = motion ?? iconMotionFor(Glyph);
	return (
		<Glyph
			aria-hidden
			focusable={false}
			{...props}
			data-motion={resolved}
			className={cn("cds-icon", className)}
		/>
	);
}
```

**The opt-in mechanism is: use `<Icon icon={X} />` instead of `<X />`.** That's it. No motion prop needed at the call site.

The resolution chain is `explicit motion prop` → `MOTION_BY_ICON[displayName]` → **`"pop"` as the universal fallback.** Every icon in the app moves; the registry just upgrades 33 of them from generic pop to something semantically apt.

The **semantics** of the mapping are the point, and they're internally consistent:
- **Directional glyphs nudge in their own direction.** ArrowRight/ChevronRight → `nudge-right`. `Play` and `SendAlt` are also right-nudges — they *mean* "forward/away." `Logout` is grouped with them, which is a genuine insight: logout is a departure.
- **Rotational objects spin.** Settings (a gear literally rotates), Renew/Restart (circular arrows), RecentlyViewed (clock), Earth (it spins).
- **Toggles turn 90°.** `Add` (+) and `Close` (×) → `turn`: a plus rotated 90° *is* a plus, and it's the classic +/× morph. `turn` = `rotate(90deg)`.
- **Destructive & effortful things wiggle.** TrashCan, Tools, Locked, Password, MagicWand, Search, WarningAlt — "this requires work / be careful."
- **Status & attention things pulse.** Misuse, Information, Security, Light, Ai, Chip — a heartbeat, i.e. "live."
- **Download bounces** (falls and settles). **Launch** translates up-and-right (`translate(2px, -2px)`) — literally the direction of the "open in new window" arrow. **Asleep** (moon) swings like a hanging pendant.

`iconName()` handling three shapes (`displayName`, `render.displayName`, `render.name`) is defensive plumbing for Carbon icons across `forwardRef`/`memo` wrappers.

Also worth copying: `aria-hidden` and `focusable={false}` are applied to **every** icon, before the spread — decorative-by-default, with `{...props}` able to override.

### 4.5 The unused sibling: `.link-hover--*`

`packages/ui/src/styles/globals.css:483-847` — ~365 lines implementing 9 named link-underline effects (`--slide`, `--double`, `--grow`, `--strike`, `--fade`, `--pulse`, `--swap`, `--sweep`, `--bounce`) plus SVG stroke variants (`--arc`, `--scribble`) using `stroke-dasharray/dashoffset`.

**Not referenced anywhere in `apps/` or `packages/` `.tsx`.** It is dead weight in the shipped CSS. Interesting as a catalogue of underline techniques but **do not port it** — it's ~12% of the stylesheet for zero rendered pixels. Mentioned here so you don't mistake its size for importance.

It does contain one transferable pattern — the springy `link-hover--bounce` timing at `globals.css:715-745`, which uses different curves for enter (`cubic-bezier(0.2, 0.57, 0.67, 1.53)` — another >1 overshoot) and exit (`cubic-bezier(0.8, 0, 0.1, 1)`) and different durations (0.2s / 0.4s). Same asymmetry principle as everywhere else.

### 4.6 `alert-attention` — the one imperative motion

`packages/ui/src/styles/globals.css:849-887` — VERBATIM:

```css
@keyframes alert-attention-nudge {
	0%,
	100% {
		transform: translateX(0);
	}
	15% {
		transform: translateX(-3px);
	}
	35% {
		transform: translateX(3px);
	}
	55% {
		transform: translateX(-2px);
	}
	75% {
		transform: translateX(2px);
	}
}

@keyframes alert-attention-ring {
	from {
		box-shadow: 0 0 0 3px var(--ring);
	}
	to {
		box-shadow: 0 0 0 0 transparent;
	}
}

@utility alert-attention {
	animation:
		alert-attention-nudge 0.5s ease-in-out,
		alert-attention-ring 1.4s ease-out;
}
```

Two channels, **very different durations on purpose**: a fast 0.5s shake (the "hey!") layered under a slow 1.4s ring bloom (the "…this thing here"). Same damped-oscillation grammar as `cds-wiggle`, -3 → +3 → -2 → +2 px.

Triggered from `packages/ui/src/components/alert.tsx:29-42`:

```tsx
		<div
			key={attention}
			data-slot="alert"
			role="alert"
			className={cn(
				alertVariants({ variant }),
				attention > 0 && "alert-attention",
				className,
			)}
```

**`key={attention}` is the whole trick.** CSS animations only run on mount; re-adding a class to an existing element does nothing. Bumping a numeric `attention` counter changes the React `key`, which **remounts the div**, which re-fires the animation. This is the standard React way to replay a CSS animation and it means the API is just `attention={n}` — increment to re-shake. No refs, no `requestAnimationFrame`, no `animation-play-state` juggling.

Note also `@utility alert-attention` — Tailwind v4's `@utility` at-rule, so this is a real utility class participating in Tailwind's cascade layers, not a bare `.class`. Same for `.bloom-low/high/aura` at `globals.css:242-254` (those are `@layer utilities`).

---

## 5. Component-level motion: kept vs overridden

### 5.1 `tw-animate-css` — what it provides

`packages/ui/src/styles/globals.css:2`:
```css
@import "tw-animate-css";
```
`packages/ui/package.json` deps: `"tw-animate-css": "^1.4.0"`.

It is the Tailwind v4 replacement for `tailwindcss-animate` (which was Tailwind v3-only). Inspected the published `dist/tw-animate.css` for 1.4.0. It provides:

- **Two generic keyframes, `enter` and `exit`**, both parameterised by `@property`-registered custom props: `--tw-enter-opacity`, `--tw-enter-scale`, `--tw-enter-rotate`, `--tw-enter-translate-x/y`, `--tw-enter-blur` (and `--tw-exit-*` mirrors).
- **`--animate-in` / `--animate-out`** theme entries that bind those keyframes with `var(--tw-animation-duration, var(--tw-duration, .15s))` — i.e. **default duration 150ms**, overridable by any Tailwind `duration-*` class on the same element.
- **The modifier utilities that set those props**: `fade-in-0`, `fade-out-0`, `zoom-in-95`, `zoom-out-95`, `spin-in-*`, `blur-in-*`, `slide-in-from-top-2` / `-bottom-full` / `-left-full` / `-right-full` (plus logical `-from-start`/`-from-end` RTL variants).
- **Ready-made component keyframes**: `accordion-down` / `accordion-up` (height 0 ↔ `--radix-accordion-content-height`, default `.2s ease-out`), `collapsible-down` / `collapsible-up`, and `caret-blink` (1.25s).
- **Animation-control utilities**: `delay-*`, `repeat-*`, `direction-*`, `fill-mode-*`, `running`, `paused`, `animation-duration-*`.

Crucially: **`tw-animate-css` ships NO `prefers-reduced-motion` guard of its own** (grep for `prefers-reduced-motion` in its dist returns 0). Anything you build on it is unguarded unless you guard it yourself. This CRM does *not* guard it either — see §6.4.

Because `accordion-down`/`accordion-up` come from the package, `globals.css` contains **no accordion keyframes** — confirmed by grep. The accordion in `packages/ui/src/components/accordion.tsx:86` just uses them:

```tsx
			className="overflow-hidden text-xs data-closed:animate-accordion-up data-open:animate-accordion-down"
```

### 5.2 What they KEEP (unmodified shadcn/Radix vocabulary)

The standard four-part shadcn enter/exit recipe — `data-open:animate-in fade-in-0 zoom-in-95` / `data-closed:animate-out fade-out-0 zoom-out-95` — is kept essentially verbatim across every overlay:

- **Dialog overlay** `packages/ui/src/components/dialog.tsx:41`:
  ```
  "fixed inset-0 isolate z-50 bg-overlay duration-100 supports-backdrop-filter:backdrop-blur-xs data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0"
  ```
- **Dialog content** `packages/ui/src/components/dialog.tsx:63` — same plus `data-open:zoom-in-95 data-closed:zoom-out-95`, `duration-100`.
- **AlertDialog** `alert-dialog.tsx:39` and `:57` — byte-for-byte the same recipe as Dialog.
- **DropdownMenu content** `dropdown-menu.tsx:46`, **sub-content** `:248`, **Select content** `select.tsx:89`, **Popover** `popover.tsx:22`, **Tooltip** `tooltip.tsx:44` — all use the same fade+zoom pair plus the **side-aware slide**:
  ```
  data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2
  ```
  (2 = `0.5rem`. The popup always slides *from the direction of its trigger*.)
- **`origin-(--radix-*-content-transform-origin)`** on dropdown/select/popover/tooltip so the `zoom-in-95` scales *out of the trigger*, not out of the popup's own centre. This is kept, not overridden, and it's what makes the zoom read as "emerging from the button."
- **`animate-spin`** on `spinner.tsx:10` (`"size-4 animate-spin"`) and on the sonner loading icon (`sonner.tsx:26`).
- **`animate-pulse`** on `skeleton.tsx:7` (`"animate-pulse rounded-sm bg-muted"`) and `status-indicator.tsx:45`.
- **Vaul drawer** `drawer.tsx:52` overlay uses `animate-in/out` fade; the content has **no animation classes at all** — vaul drives that transform itself in JS.

### 5.3 What they OVERRIDE

**(a) Duration: 150ms default → 100ms for overlays.**
`duration-100` appears on dialog overlay+content, alert-dialog overlay+content, dropdown content, dropdown sub-content, select content, popover content — **8 uses**. That is a deliberate, uniform tightening from `tw-animate-css`'s 150ms default. Menus and dialogs are *responses to a click*; anything over ~100ms there reads as lag. Compare: the page transitions get 210–400ms because they're spatial, and the sheet gets 300ms because it's a large physical object.

**(b) Sheet: 300ms + `ease-out` + full-distance slide, no zoom.**
`packages/ui/src/components/sheet.tsx:11` (content) and `:62` (overlay) both carry `duration-300 ease-out`. The content slides `slide-in-from-{side}-full` (100%, not 8px) and **has no `fade-in-0`/`zoom-in-95`** — it's opaque and simply arrives. A big panel should behave like a physical drawer: full travel, slower, decelerating, no fading. Only the overlay fades.

**(c) Tooltip: an extra state.**
`tooltip.tsx:44` adds `data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=delayed-open]:zoom-in-95` alongside the normal `data-open:` set — Radix uses a distinct `delayed-open` state after the hover delay, and without this branch a delayed tooltip appears with no animation.

**(d) Select: `animate-none` escape hatch.**
`select.tsx:89` contains `data-[align-trigger=true]:animate-none`. When the select popup is aligned so the selected item sits directly over the trigger (native-select behaviour), animating it would visibly shift the text the user is reading. **Explicitly opting an instance OUT of motion.** Same philosophy as the view-transition reset.

**(e) Checkbox indicator: `transition-none`.**
`checkbox.tsx:23`: `"grid place-content-center text-current transition-none [&>svg]:size-3.5"`. The checkmark must be **instant** — a checkbox is a state readout, and any easing on it makes the control feel unresponsive. Deliberate non-motion.

**(f) Chevron rotation instead of a keyframe.**
`data-table.tsx:237-240`:
```tsx
						<ChevronDown
							className={cn(
								"shrink-0 opacity-60 transition-transform",
								filtersOpen && "rotate-180",
							)}
						/>
```
and `data-table.tsx:528-535` the row-expander chevron with `"inline-block transition-transform"` + `isOpen && "rotate-90"`. A `transition-transform` plus a conditional rotate class — the state drives the transform, CSS interpolates. No keyframes, no library, reversible for free.

**(g) `transition-all` on the interactive primitives.**
`button.tsx:7`, `toggle.tsx:9`, `switch.tsx:20`, `tabs.tsx:65` use `transition-all`; `input.tsx:10`, `textarea.tsx:6`, `select.tsx:35`, `checkbox.tsx:16`, `attachment.tsx:9`, `input-group.tsx:16`, `table.tsx:63` use the narrower `transition-colors`. Switch thumb uses `transition-transform` (`switch.tsx:27`), tabs' active underline uses `transition-opacity` (`tabs.tsx:68` — the `::after` bar fades rather than slides, so there's no layout-linked animation to go wrong). **No duration is specified on any of these** → they all inherit Tailwind's 150ms default. So the app has exactly one hover/focus speed, unspecified and uniform.

**(h) Sonner is left almost entirely alone.**
`sonner.tsx` sets `position`, `theme`, icons and CSS vars but **no animation config** — sonner's built-in spring is accepted as-is.

### 5.4 The one place with bespoke asymmetric easing

`packages/ui/src/components/message-scroller.tsx:102` — the floating scroll-to-end button:
```
"absolute inset-s-1/2 -translate-x-1/2 border-border bg-background text-foreground transition-[translate,scale,opacity] duration-200 hover:bg-muted hover:text-foreground data-[active=false]:pointer-events-none data-[active=false]:scale-95 data-[active=false]:opacity-0 data-[active=false]:duration-400 data-[active=false]:ease-[cubic-bezier(0.7,0,0.84,0)] data-[active=true]:translate-y-0 data-[active=true]:scale-100 data-[active=true]:opacity-100 data-[active=true]:ease-[cubic-bezier(0.23,1,0.32,1)] data-[direction=end]:bottom-4 …"
```
Three things to steal: (1) `transition-[translate,scale,opacity]` — an **explicit property list**, never `transition-all`, on something that animates every frame; (2) different easing *and* different duration per direction, both expressed as data-attribute variants; (3) `pointer-events-none` bundled with the hidden state so an invisible button is never clickable.

---

## 6. Every `prefers-reduced-motion` guard

There are exactly **four** blocks. Two use `no-preference` (opt-in) and two use `reduce` (opt-out). The split is the interesting part.

### 6.1 `no-preference` #1 — icon hover motion
`packages/ui/src/styles/globals.css:261`
```css
	@media (prefers-reduced-motion: no-preference) {
```
Wraps `globals.css:262-333`. **Disables:** the `transition: transform 240ms …` spring, all 11 `--cds-hover` transform declarations, and all 5 keyframe motions (spin/wiggle/swing/bounce/pulse). Under `reduce`, `.cds-icon` retains **only** `transform-origin: center` from line 258. Icons are completely static.

### 6.2 `no-preference` #2 — view transitions
`packages/ui/src/styles/globals.css:449`
```css
@media (prefers-reduced-motion: no-preference) {
```
Wraps `globals.css:450-480`. **Disables:** all `nav-lateral`/`nav-forward`/`nav-back` rules. What survives is the **global reset at 434-442**, which is outside the query — so under `reduce` you get `animation: none` on groups and new, and `animation: none; opacity: 0` on old. Result: **navigations are instantaneous, hard cuts.** The reduced-motion path is not a degraded animation; it's the *absence* of one, and it's guaranteed correct because it's the same code path as an un-typed navigation.

This is the strongest structural idea in the file: **the reduced-motion state is the system's own default state.** Nothing extra had to be written to make it work.

### 6.3 `reduce` #1 — link-hover effects
`packages/ui/src/styles/globals.css:839-847` — VERBATIM:
```css
@media (prefers-reduced-motion: reduce) {
	.link-hover,
	.link-hover *,
	.link-hover::before,
	.link-hover::after {
		animation: none !important;
		transition-duration: 0.01ms !important;
	}
}
```
**Disables:** every underline effect, including pseudo-elements and descendants. Uses `transition-duration: 0.01ms` rather than `none` so the **end state still applies instantly** (the underline still appears — it just doesn't draw). Preserving the final visual state while removing the tweening is the correct reduced-motion behaviour; `transition: none` would work too but `0.01ms` also defeats any later `transition-duration` override.

### 6.4 `reduce` #2 — alert attention
`packages/ui/src/styles/globals.css:883-887` — VERBATIM:
```css
@media (prefers-reduced-motion: reduce) {
	.alert-attention {
		animation: alert-attention-ring 1.4s ease-out;
	}
}
```
The most nuanced guard in the file. It does **not** disable the animation — it **drops the `alert-attention-nudge` channel and keeps `alert-attention-ring`.** Positional shaking is what triggers vestibular discomfort; a box-shadow bloom does not move anything. The *communicative purpose* (draw the eye to this alert) is preserved at full strength while the harmful component is removed.

**Selective degradation, not blanket disabling.** This is the single most sophisticated reduced-motion pattern in the codebase and the one most worth internalising: ask "which channel of this animation is the harmful one," not "should this animate."

### 6.5 The gap

**The entire `tw-animate-css` overlay layer is NOT guarded.** Dialogs, sheets, dropdowns, tooltips, popovers, selects, accordions, `animate-spin`, `animate-pulse` all continue to animate under `prefers-reduced-motion: reduce`. `tw-animate-css` ships no guard, and this repo adds none.

Defensible in part (fades and 100ms zooms are low-risk; a spinner is a status indicator), but `sheet.tsx` slides a full-height panel 100% of the viewport width in 300ms, and `animate-pulse` on skeletons is an infinite oscillation — those are genuine reduced-motion offenders. If porting, this is the hole to plug:

```css
@media (prefers-reduced-motion: reduce) {
  [data-slot="sheet-content"],
  [data-slot="drawer-content"] { animation-duration: 0.01ms !important; }
}
```

---

## 7. The distilled rules

Twenty rules, portable to any app.

**Timing**
1. Define exactly three duration tokens, not a scale: `--duration-exit: 150ms`, `--duration-enter: 210ms`, `--duration-move: 400ms`. More tokens means more decisions and less consistency.
2. **Exit faster than enter** (150 vs 210). The user has already left the old thing; don't make them wait for it.
3. **Positional motion slower than opacity motion** (400 vs 210). Position carries continuity information the eye must track; opacity is just a veil.
4. **Delay the enter by the exit duration** (`animation: 210ms ease-out 150ms both vt-fade`). Sequence, don't cross-dissolve — two semi-transparent copies of the same page look like a bug.
5. Fades: `ease-out` in, `ease-in` out. Continuous positional gestures: `ease-in-out`.
6. **Interactive feedback (menus, dialogs) is 100ms.** Spatial/page-level is 200–400ms. Large physical panels are 300ms. One speed per *category* of motion, not per component.
7. Springs = a cubic-bezier with y2 > 1: `cubic-bezier(0.34, 1.56, 0.64, 1)`. Give overshoot ~240ms; less and it reads as jitter.
8. Multi-stop keyframes should be **damped**: each swing ~70-75% of the last (-9° → +7° → -5° → +3° → 0). Undamped oscillation reads as a glitch.

**Opt-in discipline**
9. **Reset the platform's default animations to `none`, then re-enable by name.** `::view-transition-group(*), ::view-transition-new(*) { animation: none }` and `::view-transition-old(*) { animation: none; opacity: 0 }`.
10. **Make "no animation" the failure mode.** `default: "none"` in the React transition-type map means a forgotten `transitionTypes` renders instantly rather than wrongly.
11. Enforce the same policy twice — once in CSS, once in the component API. Cheap, and it makes the intent unmissable to the next reader.
12. Give yourself an **explicit opt-out** for instances: `data-[align-trigger=true]:animate-none`, `transition-none` on a checkbox indicator. Some controls must be instant.

**View transitions specifically**
13. **`view-transition-name` marks persistent CHROME, not content.** Naming an element under an `animation: none` reset *excludes* it from the transition. Header, rail, sub-nav, page title: named. Rows, cards, avatars: never.
14. **`z-index` your named chrome groups** — `::view-transition-group(app-header) { z-index: 100 }`. The pseudo-element tree is its own stacking context; without this, sliding content paints over your header.
15. Write each keyframe **once, in the enter direction**, and produce exits by appending `reverse` to the `animation` shorthand. Symmetry for free.
16. **Parameterise direction with a custom property** (`--slide-offset: 60px` / `-60px`) so forward and back are the same two rules.
17. Build the **full vocabulary** (forward / back / lateral) even if you only wire up one today. The grammar is the deliverable.
18. Distinguish hierarchy in the motion: forward/back slide horizontally; **lateral (sibling→sibling) gets no horizontal slide at all** — just a fade and a 12px rise. Sideways motion implies depth that isn't there.
19. Add a **~3px blur to page fades**. It masks sub-pixel text shimmer and makes 210ms feel substantial.

**Reduced motion**
20. Prefer **`@media (prefers-reduced-motion: no-preference)` as a gate** over `reduce` as an override — then the reduced-motion path is your system's default path and is correct by construction. Where you must use `reduce`, **degrade selectively**: keep the channel that communicates (`alert-attention-ring`) and drop only the channel that moves things (`alert-attention-nudge`). Use `transition-duration: 0.01ms` rather than `none` so end states still apply.

**Bonus, from the icon system**
21. **Derive motion from meaning, with a table.** `Record<IconName, Motion>` + a sane universal fallback (`"pop"`). Arrows nudge in their direction; gears spin; trash wiggles; status icons pulse. The mapping is authored once and every call site gets it free.
22. **Trigger child motion from ancestor hover** (`button:hover .icon`), never from the 16px glyph itself.
23. **Wrap motion selectors in `:where()`** so specificity is 0-0-0 and any consumer class wins without `!important`.
24. **Dispatch transforms through one custom property** (`--cds-hover`) and apply it in a single rule. Adding a motion is 3 lines, not a new selector.
25. **Replay a CSS animation by bumping a React `key`** (`key={attention}`). No refs, no rAF.

---

## 8. Applying this to Collecct

Target: `/Users/keshav/Developer/Others/AI-Agency/frontend/app/globals.css` — 3029 lines, Next 15.5.19, React 19.0.0, no motion library, no `tw-animate-css`.

### 8.1 What Collecct already has (and gets right)

Collecct already independently arrived at several of the CRM's rules:

- **Named easing tokens** — `frontend/app/globals.css:32-33`:
  ```css
  --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
  --ease-spring: cubic-bezier(0.32, 0.72, 0, 1);
  ```
  `--ease-out` is **byte-identical** to the CRM's `message-scroller` show curve (ease-out-quint). Good instinct, already shared.
- **Consistent short transition durations** — the `0.12s`–`0.18s` band is used near-universally across ~40 `transition:` declarations, with `var(--ease-out)` applied on the newer ones (lines 1661, 1678, 1710, 1736).
- **Enter animations that combine opacity + a small translate** — `rowIn` (opacity + `translateY(7px)`, line 409), `fade` (opacity + `translateY(5px)`, line 1185), `toastIn` (opacity + `translate(-50%, 10px)`, line 1451). Same idea as `vt-rise`'s 12px.
- **`animation: rowIn 0.5s var(--ease-out) both`** (line 407) already uses `both` fill-mode correctly.
- **A `prefers-reduced-motion: reduce` block** — `frontend/app/globals.css:1489-1494`:
  ```css
  @media (prefers-reduced-motion: reduce) {
    * {
      animation-duration: 0.001ms !important;
      transition-duration: 0.001ms !important;
    }
  }
  ```
  A blanket nuke. Blunt but **strictly safer than the CRM's**, which leaves the whole overlay layer unguarded. Keep it.
- **`--ease-spring` reserved for the toast** (line 1449) — one spring, used once, for the one element that should feel bouncy. Restraint.

### 8.2 Adopt — in priority order

**1. The duration tokens (high value, zero risk).** Add next to the existing ease tokens at `frontend/app/globals.css:32`:
```css
--duration-exit: 150ms;
--duration-enter: 210ms;
--duration-move: 400ms;
```
Collecct's existing durations are all hardcoded literals (`0.14s`, `0.16s`, `0.5s`, `0.32s`, `0.3s`) with no rationale. Introduce the tokens, then migrate the *entrance* animations (`rowIn` 0.5s, `fade` 0.32s, `fade-in` 0.15s, `toastIn` 0.3s) onto `--duration-enter` over time. `rowIn` at 500ms in particular is ~2.4× the CRM's enter duration — for a list row that's noticeably slow, especially if many rows stagger.

**2. The `.cds-icon` system (highest distinctiveness-per-line).** This is self-contained, ~140 lines of CSS + ~95 lines of TSX, and depends on **nothing** — no Tailwind, no View Transitions, no library. It works in plain CSS with a `data-motion` attribute. Port `globals.css:256-397` essentially verbatim plus an `<Icon>` wrapper. If Collecct uses lucide rather than Carbon, the `MOTION_BY_ICON` keys change but the **semantic categories transfer directly** (arrows nudge, gears spin, trash wiggles, status pulses).

Two caveats: (a) the selector references shadcn `data-slot` values (`dropdown-menu-item`, `command-item`, `sidebar-menu-button`) — swap for Collecct's own class/attribute names; (b) the `:where()` wrapper matters for a hand-written stylesheet where specificity conflicts are likelier than in a Tailwind app — keep it.

**3. The `alert-attention` pattern + selective reduced-motion degradation.** `globals.css:849-887` plus the `key={attention}` remount trick. Directly useful for Collecct's bid/no-bid verdicts and pipeline alerts. And the "keep the ring, drop the nudge" idea is the best reduced-motion thinking in the CRM.

**4. The three-token / asymmetric philosophy applied to what's already there.** Even without View Transitions: give Collecct's modal/panel/toast enter and exit different durations, with exit faster.

**5. Later, if it's ever worth it: View Transitions.** Requires the Next 16 / React 19.2 upgrade (Collecct is on Next 15.5.19 / React 19.0.0 — `ViewTransition` is not exported from React 19.0). If done, port the reset + z-index + `nav-*` rules as a unit; they are meaningless apart. Realistically: **defer.** The value/cost ratio is far worse than items 1-4.

### 8.3 What would CONFLICT — read before pasting

**(a) `@keyframes spin` — name collision.**
Collecct `frontend/app/globals.css:289`:
```css
@keyframes spin {
  to { transform: rotate(360deg); }
}
```
CRM `globals.css:336`:
```css
@keyframes cds-spin {
  to { transform: rotate(360deg); }
}
```
Same body, different names. **No breakage if you port `cds-spin` as-is** — the `cds-` prefix is exactly the collision-avoidance the CRM built in. Keep the prefix on all five (`cds-spin`, `cds-wiggle`, `cds-swing`, `cds-bounce`, `cds-pulse`). Do **not** "tidy" them into unprefixed names.

**(b) `badgePulse` vs `cds-pulse` — semantic collision.**
Collecct `globals.css:1035`:
```css
@keyframes badgePulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
```
CRM `cds-pulse` scales (1 → 1.12 → 1). Different property, different feel, no name clash — but if both run on the same element you'd get a scale+opacity double-pulse. Pick one per element.

**(c) `fade-in` is defined TWICE in Collecct — pre-existing bug.**
- `frontend/app/globals.css:762`: `@keyframes fade-in { from { opacity: 0; } }`
- `frontend/app/globals.css:2238`: `@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }`

Later wins, and they're semantically equivalent here so nothing is visibly broken — but it's a live footgun. Worth deduping while you're in the file. Also note Collecct has **three near-identical fade keyframes** (`fade-in` ×2 at :762/:2238, `fade` at :1185) — consolidating to one `--duration-enter`-driven fade would shrink the surface.

**(d) The blanket `*` reduced-motion rule will neutralise anything you port.**
`frontend/app/globals.css:1489-1494` applies `animation-duration: 0.001ms !important` to `*`. This is **stronger** than the CRM's approach and means:
- The `.cds-icon` motions will be correctly disabled under `reduce` — good, and you don't need the `no-preference` wrapper for correctness.
- **But** the CRM's selective `alert-attention` degradation (keep the ring, drop the nudge) **will not work** — the `*` rule kills the ring too. If you want that behaviour you must add an explicit exception *after* line 1494, e.g.:
  ```css
  @media (prefers-reduced-motion: reduce) {
    .alert-attention {
      animation: alert-attention-ring 1.4s ease-out !important;
      animation-duration: 1.4s !important;
    }
  }
  ```
  Decide consciously: blanket-safe, or selectively expressive. Don't paste the CRM's guard on top of the `*` rule and assume it wins.

- Corollary: if you port the `.cds-icon` block, you can **drop the `@media (prefers-reduced-motion: no-preference)` wrapper** since the `*` rule already covers you — but keeping it is harmless and self-documenting. Recommend keeping it.

**(e) `--ease-out` names the same curve in both, but the CRM's `ease-out` keyword ≠ Collecct's `--ease-out` var.**
CRM's view-transition rules use the *CSS keyword* `ease-out` (`cubic-bezier(0.25, 0.1, 0.25, 1)`-ish default), not a token. Collecct's `--ease-out` is the much snappier `cubic-bezier(0.23, 1, 0.32, 1)`. If you port CRM rules and mechanically substitute `var(--ease-out)` for `ease-out`, the motion will feel meaningfully punchier than intended. Substitute deliberately, and prefer Collecct's token — it's the better curve.

**(f) No `tw-animate-css`, no Tailwind `animate-in`/`animate-out` in Collecct.**
Any CRM component class string quoted in §5 (`data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 …`) is **inert** in Collecct. Either add the dependency (`bun add tw-animate-css` + `@import "tw-animate-css";`) or hand-write the equivalent keyframes. Given Collecct's CSS is hand-authored rather than utility-driven, hand-writing two keyframes (`overlayIn`, `popIn`) is probably cheaper than adopting the package.

**(g) `@utility` and `@layer utilities` are Tailwind v4 at-rules.**
CRM's `@utility alert-attention { … }` (`globals.css:877`) and `@layer utilities { .cds-icon … }` (`globals.css:256`) require Tailwind v4's processor. If Collecct's Tailwind setup differs, **strip the wrappers** and declare `.alert-attention { … }` / `.cds-icon { … }` as plain rules. The CSS inside is standard and portable; only the at-rules are v4-specific.

**(h) Do NOT port `.link-hover--*`.**
365 lines (`globals.css:483-847`), unused in the source app, ~12% of the stylesheet. Collecct's file is already 3029 lines.

### 8.4 Concrete first patch

Minimal, no-dependency, no-framework-upgrade starting point for Collecct:

1. `frontend/app/globals.css:32-33` — add `--duration-exit/enter/move` beside the existing ease tokens.
2. Retime `rowIn` (0.5s → `--duration-enter`), `fade` (0.32s → `--duration-enter`), `toastIn` (0.3s → `--duration-enter`, keep `--ease-spring`).
3. Dedupe `@keyframes fade-in` (drop line 762's, keep 2238's).
4. Append the `.cds-icon` block (CRM `globals.css:256-397`) with all `cds-` prefixes intact, ancestor list rewritten for Collecct's markup, and an `<Icon>` wrapper carrying a lucide-keyed `MOTION_BY_ICON`.
5. Append `alert-attention` + its two keyframes, as plain CSS (no `@utility`), with the explicit `!important` reduced-motion exception from §8.3(d) placed **after** line 1494.

That lands ~80% of the CRM's motion *character* with zero new dependencies and no framework upgrade.

---

## Appendix — file index

| File | What's in it |
|---|---|
| `packages/ui/src/styles/globals.css` | All 887 lines. Motion lives at 242-254 (bloom), 256-397 (cds-icon + keyframes), 399-403 (duration tokens), 405-432 (vt keyframes), 434-447 (VT reset + z-index), 449-481 (nav types), 483-847 (link-hover, unused), 849-887 (alert-attention) |
| `apps/app/components/page-transition.tsx` | 29 lines. `<ViewTransition>` wrapper, `default: "none"` maps |
| `apps/app/components/page-shell.tsx` | Wraps content in `PageTransition`; `[view-transition-name:page-header]` at :34 |
| `apps/app/components/app-header.tsx` | `[view-transition-name:app-header]` at :62 |
| `apps/app/components/app-icon-rail.tsx` | `[view-transition-name:app-rail]` at :122; `transitionTypes={["nav-lateral"]}` at :71; mobile links deliberately untyped |
| `apps/app/app/(app)/settings/settings-sidebar.tsx` | `[view-transition-name:settings-sidebar]` at :62 and :80; `transitionTypes` at :49 |
| `apps/app/app/(app)/layout.tsx` | Chrome (header, rail) outside the transition boundary |
| `apps/app/next.config.ts` | `experimental: { viewTransition: true }` |
| `packages/ui/src/components/icon.tsx` | `ICON_MOTIONS`, `MOTION_BY_ICON`, `iconMotionFor`, `<Icon>` |
| `packages/ui/src/components/alert.tsx` | `key={attention}` animation-replay trick at :29 |
| `packages/ui/src/components/sheet.tsx` | `duration-300 ease-out`, full-distance slide, no zoom |
| `packages/ui/src/components/dialog.tsx` / `alert-dialog.tsx` | `duration-100` fade+zoom recipe |
| `packages/ui/src/components/dropdown-menu.tsx` / `select.tsx` / `popover.tsx` / `tooltip.tsx` | side-aware slide + radix transform-origin; `animate-none` opt-out in select |
| `packages/ui/src/components/checkbox.tsx` | `transition-none` — deliberate non-motion |
| `packages/ui/src/components/message-scroller.tsx` | Bespoke asymmetric easing + durations per direction |
| `packages/ui/src/components/accordion.tsx` | `animate-accordion-up/down` from tw-animate-css |
| `packages/ui/src/components/spinner.tsx` / `skeleton.tsx` / `sonner.tsx` | `animate-spin` / `animate-pulse`; sonner motion untouched |
| `packages/ui/package.json` | `tw-animate-css ^1.4.0`; no motion library |
