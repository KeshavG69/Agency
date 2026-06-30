import type { Metadata } from "next";
import Script from "next/script";
import { Fraunces, Hanken_Grotesk, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import AuthProvider from "@/components/AuthProvider";

const display = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
});

const sans = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600"],
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Collecct — Capture Operations",
  description: "Govcon business-development pipeline.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body>
        {/* Runtime config — beforeInteractive hoists this into <head> so window.__ENV is set
            before the app bundle reads the backend URL. */}
        <Script src="/__env.js" strategy="beforeInteractive" />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
