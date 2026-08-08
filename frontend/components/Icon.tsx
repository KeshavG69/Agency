import type { ComponentType, SVGProps } from "react";
import { cn } from "@/lib/cn";
import { motionFor, type IconMotion } from "@/lib/icon-motion";

/** `data-motion` is not part of SVGProps, so name it rather than widening to `any`. */
type GlyphProps = SVGProps<SVGSVGElement> & { "data-motion"?: IconMotion };

type IconProps = SVGProps<SVGSVGElement> & {
  /**
   * The glyph component. Any icon set works — every one of them types its exports as
   * a component taking SVG props. Omit it to draw the SVG inline via `children`,
   * which is what the existing hand-written icons in `app/` do today.
   */
  as?: ComponentType<SVGProps<SVGSVGElement>>;
  /** Meaning key, e.g. "ArrowRight". Looked up in MOTION_BY_ICON; unknown names pop. */
  name?: string;
  /**
   * Override the lookup. Pass `"none"` for an icon that already transforms itself —
   * a disclosure chevron toggling `rotate-90`, say — since the hover rule would
   * otherwise win over that class and flatten it mid-interaction.
   */
  motion?: IconMotion | "none";
};

/**
 * Wraps a glyph in the `.cds-icon` hover system: no listeners, no state, no client
 * boundary — an ancestor `:hover` rule in globals.css does the work, so this stays a
 * server component and adds nothing to the bundle.
 */
export function Icon({ as: Glyph, name, motion, className, ...rest }: IconProps) {
  const resolved = motion ?? motionFor(name);
  const labelled =
    rest["aria-label"] !== undefined || rest["aria-labelledby"] !== undefined;

  const glyphProps: GlyphProps = {
    // Decorative by default — label the control, not the glyph, or a screen reader
    // reads the button twice. A caller that supplies its own label is declaring the
    // icon to be content, so the hide is dropped.
    "aria-hidden": labelled ? undefined : true,
    // IE-era SVGs still land in focus order in some engines; this is cheap insurance.
    focusable: "false",
    ...rest,
    className: cn("cds-icon", className),
    // Omitted rather than written as "none", so the attribute selectors simply miss
    // and the element keeps `--cds-hover` unset.
    "data-motion": resolved === "none" ? undefined : resolved,
  };

  // Two call shapes, one set of props: `children` rides along inside `rest` for the
  // inline case, so neither branch needs to know about it.
  return Glyph ? <Glyph {...glyphProps} /> : <svg {...glyphProps} />;
}
