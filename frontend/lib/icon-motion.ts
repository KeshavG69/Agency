/**
 * Icon hover motion, assigned by MEANING rather than by decoration.
 *
 * The motions themselves live in `app/globals.css` (§4.1) as
 * `.cds-icon[data-motion="…"]` rules. This file is the other half of that contract:
 * a value here with no matching rule there is a silent no-op, so the union below is
 * the single source of truth for what is spellable.
 */

/** Every motion defined in globals.css. Keep in sync — order matches the stylesheet. */
export const ICON_MOTIONS = [
  "pop",
  "scale",
  "lift",
  "turn",
  "rotate",
  "flip",
  "nudge-right",
  "nudge-left",
  "nudge-up",
  "nudge-down",
  "launch",
  "shrink",
  "wiggle",
  "shake",
  "pulse",
  "spin",
] as const;

export type IconMotion = (typeof ICON_MOTIONS)[number];

/**
 * What an unmapped icon gets. `pop` is the neutral one: it acknowledges the hover
 * without claiming a direction or an outcome the icon may not have.
 */
export const DEFAULT_ICON_MOTION: IconMotion = "pop";

/**
 * Keyed by what the icon MEANS, using lucide's names as the canonical spelling since
 * that is the naming every icon set is measured against. Aliases for one meaning share
 * a motion — `Trash` and `Trash2` are the same idea and must not animate differently.
 *
 * The rule for adding entries: the motion should restate the icon's verb. An arrow
 * travels the way it points, a gear turns, a delete recoils, a warning refuses to
 * settle. Anything that is merely pretty belongs on `pop`, which is the default and
 * therefore costs nothing to leave out.
 */
export const MOTION_BY_ICON: Record<string, IconMotion> = {
  // Direction — the glyph moves the way it points.
  ArrowRight: "nudge-right",
  ArrowLeft: "nudge-left",
  ArrowUp: "nudge-up",
  ArrowDown: "nudge-down",
  ChevronRight: "nudge-right",
  ChevronLeft: "nudge-left",
  ChevronUp: "nudge-up",
  ChevronDown: "nudge-down",
  LogOut: "nudge-right",

  // Transfer — direction is the payload's direction, not the cursor's.
  Download: "nudge-down",
  Upload: "nudge-up",
  Save: "nudge-down",
  Send: "launch",
  ExternalLink: "launch",

  // Mechanism — things with moving parts move.
  Settings: "turn",
  Refresh: "turn",
  Sync: "spin",
  RotateCw: "spin",
  Loader: "spin",
  Repeat: "flip",
  ArrowLeftRight: "flip",

  // Tilt — a small off-axis lean, for icons that hang or get marked.
  Bell: "rotate",
  Bookmark: "rotate",
  Pencil: "rotate",
  Edit: "rotate",

  // Recoil and alarm — motion that reads as reluctance, reserved for the two places
  // where a hover should feel slightly uncomfortable.
  Trash: "wiggle",
  Trash2: "wiggle",
  AlertTriangle: "shake",
  AlertCircle: "shake",

  // Emphasis — a beat, for state that is meant to be noticed.
  Star: "pulse",
  Sparkles: "pulse",
  Heart: "pulse",

  // Size — the glyph does to itself what the control does to the pane.
  Maximize: "scale",
  ZoomIn: "scale",
  Expand: "scale",
  Minimize: "shrink",
  ZoomOut: "shrink",
  Minimize2: "shrink",

  // Surfaces that come toward you.
  Mail: "lift",
  Calendar: "lift",
  FileText: "lift",
  Folder: "lift",
  Copy: "lift",
  Link: "lift",
};

/**
 * Resolve an icon name to a motion. Unknown and missing names both fall back rather
 * than throwing: a wrong hover animation is not worth a broken render.
 */
export function motionFor(name: string | undefined): IconMotion {
  if (!name) return DEFAULT_ICON_MOTION;
  return MOTION_BY_ICON[name] ?? DEFAULT_ICON_MOTION;
}
