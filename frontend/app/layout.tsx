import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import AuthProvider from "@/components/AuthProvider";
import QueryProvider from "@/components/QueryProvider";
import ThemeProvider from "@/components/ThemeProvider";
import { NuqsAdapter } from "nuqs/adapters/next/app";

export const metadata: Metadata = {
  title: "Collecct — Capture Operations",
  description: "Govcon business-development pipeline.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The two Geist classes set --font-geist-sans / --font-geist-mono on the same
    // element as :root, which is where legacy.css builds --font-sans, --font-mono and
    // --font-display out of them. There is no display face any more: headings are the
    // sans at a larger size, so --font-display is an alias rather than a third family.
    // suppressHydrationWarning is required by next-themes: it writes the theme class onto
    // <html> before React hydrates, so server and client markup legitimately differ here.
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body>
        {/* QueryProvider OUTSIDE AuthProvider: AuthProvider's one job is to kick off
            initializeAuth(), and anything that later wants to prefetch or invalidate from
            that path needs the client to already exist. Nothing here depends on the
            reverse order. */}
        {/* NuqsAdapter must sit above anything that reads URL state. It is the App Router
            adapter specifically — the pages-router one silently no-ops here. */}
        <NuqsAdapter>
          <ThemeProvider>
            <QueryProvider>
              <AuthProvider>{children}</AuthProvider>
            </QueryProvider>
          </ThemeProvider>
        </NuqsAdapter>
      </body>
    </html>
  );
}
