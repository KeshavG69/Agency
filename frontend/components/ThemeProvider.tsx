"use client";

import { ThemeProvider as NextThemes } from "next-themes";

/**
 * Applies the `.dark` class to <html> so the dark palette in legacy.css can actually match.
 *
 * WHY THIS EXISTS: globals.css rebinds Tailwind's dark variant to a CLASS
 * (`@custom-variant dark (&:where(.dark, .dark *))`) rather than the default
 * `prefers-color-scheme` media query. That is the right call — it is what lets a user
 * override their OS setting — but it means dark styles are unreachable until something puts
 * the class on. Without this provider the entire `.dark` block ships to every user and can
 * never apply.
 *
 * `defaultTheme="system"` keeps OS preference as the default, so behaviour matches what a
 * media query would have done, while leaving room for an explicit toggle later.
 *
 * `disableTransitionOnChange` suppresses the colour-transition flash when the theme flips —
 * without it every element with a `transition` animates its colour change at once.
 */
export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemes
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemes>
  );
}
