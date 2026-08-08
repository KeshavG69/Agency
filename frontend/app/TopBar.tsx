"use client";

import { useEffect, useRef, useState } from "react";
import type { User } from "@/lib/types";
import type { ViewKey } from "@/lib/stores/uiStore";

const NAV: { key: ViewKey; label: string; admin?: boolean }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "pipeline", label: "Pipeline" },
  { key: "callplan", label: "Call Plan" },
  { key: "contacts", label: "Contacts" },
  { key: "documents", label: "Library", admin: true },
  { key: "org", label: "Organisation", admin: true },
];

/**
 * Primary navigation bar (PriceIQ-style): logo, the section nav across the top,
 * the SAM.gov pull action, and a user menu. Replaces the old left command rail as
 * the main way to move between sections; the left sidebar is now just Bid pursuits.
 */
export default function TopBar({
  user,
  isAdmin,
  view,
  onNavigate,
  onPull,
  pulling,
  onSignOut,
}: {
  user: User;
  isAdmin: boolean;
  view: ViewKey;
  onNavigate: (v: ViewKey) => void;
  onPull: () => void;
  pulling: boolean;
  onSignOut: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const initials =
    `${(user.firstName?.[0] ?? "").toUpperCase()}${(user.lastName?.[0] ?? "").toUpperCase()}` || "?";

  return (
    <header className="topbar">
      <div className="tb-brand">
        Collecct<span className="dot">.</span>
      </div>

      <nav className="tb-nav">
        {NAV.filter((i) => !i.admin || isAdmin).map((i) => (
          <button
            key={i.key}
            className={`tb-item ${view === i.key ? "on" : ""}`}
            onClick={() => onNavigate(i.key)}
          >
            {i.label}
          </button>
        ))}
      </nav>

      <div className="tb-right">
        <div className="tb-user" ref={ref}>
          <button className="tb-userbtn" onClick={() => setMenuOpen((v) => !v)}>
            <span className="tb-avatar">{initials}</span>
            <span className="tb-uinfo">
              <span className="tb-uname">
                {user.firstName} {user.lastName}
              </span>
              <span className="tb-urole">{isAdmin ? "Admin" : "Member"}</span>
            </span>
            <span className={`tb-caret ${menuOpen ? "up" : ""}`}>▾</span>
          </button>
          {menuOpen && (
            <div className="tb-menu">
              <button
                className="tb-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  onSignOut();
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
