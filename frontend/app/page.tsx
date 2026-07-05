"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchOpportunities,
  pullFromSam,
  analyzeSelected,
  setDecision,
  assignOpportunity,
  getDocUrl,
  fetchOpportunitySharePointFiles,
  type SharePointFile,
  type SharePointFilesResponse,
  fetchCallPlan,
  setCallStatus,
  fetchCollisions,
  type CallPlanItem,
  type CollisionItem,
  approveCapture,
  runOutreach,
  runOutreachOne,
  sendMail,
  connectOutlook,
  disconnectOutlook,
  syncOutlookContacts,
  previewOutlookContacts,
  ingestOutlookContacts,
  type ContactCandidate,
  connectSharePoint,
  connectSharePointRest,
  disconnectSharePoint,
  getConnStatus,
  syncSharePointStructure,
  type Opportunity,
  type OutreachDraft,
  type BidDecision,
  type DocItem,
} from "@/lib/data";
import ContactsGraph from "./ContactsGraph";
import SharePointGraph from "./SharePointGraph";
import MailTriagePanel from "./MailTriage";
import CalendarStrip, { toLocalIso } from "./CalendarStrip";
import FilePreview from "./FilePreview";
import AssignModal from "./AssignModal";
import ContactReviewModal from "./ContactReviewModal";
import TopBar, { type ViewKey } from "./TopBar";
import BidSidebar from "./BidSidebar";
import FilterBar, { type Facets, EMPTY_FACETS, activeFacetCount } from "./FilterBar";
import { dueLabel } from "@/lib/format";
import { useAuthStore } from "@/lib/stores/authStore";
import { useConnectionStore } from "@/lib/stores/connectionStore";
import { organizationsApi, type Organization } from "@/lib/api/organizations";
import { invitationsApi } from "@/lib/api/invitations";
import type { User, TeamMember, Invitation } from "@/lib/types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const money = (n?: number | null) =>
  n == null
    ? "—"
    : n >= 1_000_000
      ? `$${(n / 1_000_000).toFixed(1)}M`
      : `$${(n / 1000).toFixed(0)}K`;

const badgeClass = (d?: BidDecision) =>
  d === "Bid" ? "bid" : d === "No-Bid" ? "nobid" : d === "Watch" ? "watch" : "none";

const priColor = (p?: number) =>
  p == null
    ? "var(--line-strong)"
    : p >= 80
      ? "var(--bid)"
      : p >= 50
        ? "var(--watch)"
        : "var(--nobid)";

const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};


// Pipeline stages shown as the portfolio nav. "All" first.
const FILTERS: { key: string; label: string; match: (o: Opportunity) => boolean }[] = [
  { key: "all", label: "All opportunities", match: () => true },
  { key: "Bid", label: "Bid — pursue", match: (o) => o.bid_decision === "Bid" },
  { key: "Watch", label: "Watch — revisit", match: (o) => o.bid_decision === "Watch" },
  { key: "No-Bid", label: "No-bid", match: (o) => o.bid_decision === "No-Bid" },
  { key: "captured", label: "Capture complete", match: (o) => !!o.captured_at },
  { key: "new", label: "Awaiting analysis", match: (o) => !o.bid_decision },
];

type TabKey = "info" | "contacts" | "documents" | "activity";

// Auth gate: the whole console is behind login. While auth initializes we show a
// splash; if there's no session we bounce to /auth/login. Console only mounts
// (and only fires its data loads) once we have an authenticated user.
export default function Page() {
  const { user, isInitializing } = useAuthStore();

  useEffect(() => {
    if (!isInitializing && !user && typeof window !== "undefined") {
      window.location.href = "/auth/login";
    }
  }, [isInitializing, user]);

  if (isInitializing || !user) {
    return (
      <div className="loading-full" style={{ flexDirection: "column", gap: 14 }}>
        <div className="word" style={{ fontFamily: "var(--font-display)", fontSize: 30 }}>
          Collecct<span style={{ color: "var(--accent-2)" }}>.</span>
        </div>
        <div style={{ fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--muted)" }}>
          {isInitializing ? "Signing you in…" : "Redirecting to sign in…"}
        </div>
      </div>
    );
  }

  return <Console user={user} />;
}

function Console({ user }: { user: User }) {
  const isAdmin = user.role === "admin";
  const logout = useAuthStore((s) => s.logout);
  const onSignOut = async () => {
    await logout();
    if (typeof window !== "undefined") window.location.href = "/auth/login";
  };
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [facets, setFacets] = useState<Facets>(EMPTY_FACETS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("info");
  const [pulling, setPulling] = useState(false);
  const [analyzingSel, setAnalyzingSel] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [members, setMembers] = useState<TeamMember[]>([]); // for the assign dropdown (admins)
  const [viewingDate, setViewingDate] = useState<string | null>(null); // calendar day, null = All
  const [capturing, setCapturing] = useState(false);
  const [outreachBusy, setOutreachBusy] = useState<string | null>(null);
  const [outreachOne, setOutreachOne] = useState<string | null>(null);
  // Connection state is cached (set on the OAuth redirect), not fetched on every load.
  const outlook = useConnectionStore((s) => s.outlook);
  const sharepoint = useConnectionStore((s) => s.sharepoint);
  const setConnection = useConnectionStore((s) => s.setConnection);
  const outlookConnected = outlook.connected;
  const outlookAccount = outlook.accountId;
  const spConnected = sharepoint.connected;
  const [connecting, setConnecting] = useState(false);
  const [spConnecting, setSpConnecting] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [spResyncing, setSpResyncing] = useState(false);
  // Bumped whenever a SharePoint sync starts (manual resync, or the post-connect OAuth
  // landing) so the Library graph knows to start polling for the background crawl's
  // result — the user shouldn't have to reload the page to see it appear.
  const [spGraphRefresh, setSpGraphRefresh] = useState(0);
  // Outlook contact-review dialog (pick which contacts to ingest).
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewContacts, setReviewContacts] = useState<ContactCandidate[]>([]);
  const [view, setView] = useState<ViewKey>("dashboard");

  const load = useCallback(async () => {
    try {
      const data = await fetchOpportunities();
      setOpps(data);
      setError(null);
      return data;
    } catch {
      setError("Can't reach the backend — start it on :8000.");
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Restore this user's saved filters (decision + facets) once on mount.
  const filtersKey = `collecct:filters:${user.email}`;
  useEffect(() => {
    try {
      const raw = localStorage.getItem(filtersKey);
      if (raw) {
        const p = JSON.parse(raw);
        if (p.facets) setFacets({ ...EMPTY_FACETS, ...p.facets });
        if (typeof p.filter === "string") setFilter(p.filter);
      }
    } catch {
      /* ignore malformed cache */
    }
  }, [filtersKey]);

  // Persist filters whenever they change.
  useEffect(() => {
    try {
      localStorage.setItem(filtersKey, JSON.stringify({ facets, filter }));
    } catch {
      /* storage may be unavailable */
    }
  }, [filtersKey, facets, filter]);

  // Keep non-admins out of the admin-only views (e.g. if demoted mid-session).
  useEffect(() => {
    if (!isAdmin && (view === "documents" || view === "org")) {
      setView("pipeline");
    }
  }, [isAdmin, view]);

  // Admins need the member roster to assign opportunities.
  useEffect(() => {
    if (isAdmin) organizationsApi.getMembers().then(setMembers).catch(() => {});
  }, [isAdmin]);

  // Assign an opportunity to members (admin). Optimistic; reverts on failure.
  const onAssign = async (id: string, userIds: string[]) => {
    setError(null);
    setOpps((prev) => prev.map((o) => (o.id === id ? { ...o, assigned_to: userIds } : o)));
    try {
      await assignOpportunity(id, userIds);
    } catch {
      setError("Couldn't update the assignment — reverting.");
      load();
    }
  };

  const onConnectOutlook = async () => {
    setConnecting(true);
    try {
      sessionStorage.setItem("pendingProvider", "outlook");
      const { auth_url } = await connectOutlook(`${window.location.origin}/oauth-callback`);
      window.location.href = auth_url; // off to Microsoft consent; returns to /oauth-callback
    } catch {
      setError("Couldn't start the Outlook connection — check COMPOSIO_* settings.");
      setConnecting(false);
    }
  };

  // "Connect Library" is ONE click but SharePoint is really two Composio connections
  // chained back-to-back — Graph first (structure/ACL/write), then REST (exact
  // site-group member emails, which Graph alone can't resolve). We check which stage
  // still needs authorizing and start there; the oauth-callback page drives the rest
  // of the chain automatically once the user returns from Microsoft.
  const onConnectSharePoint = async () => {
    setSpConnecting(true);
    try {
      sessionStorage.setItem("pendingProvider", "sharepoint");
      const graphStatus = await getConnStatus("sharepoint");
      if (!graphStatus.connected) {
        sessionStorage.setItem("spStage", "sharepoint");
        const { auth_url } = await connectSharePoint(`${window.location.origin}/oauth-callback`);
        window.location.href = auth_url;
        return;
      }
      const restStatus = await getConnStatus("sharepoint_rest");
      if (!restStatus.connected) {
        sessionStorage.setItem("spStage", "sharepoint_rest");
        const { auth_url } = await connectSharePointRest(`${window.location.origin}/oauth-callback`);
        window.location.href = auth_url;
        return;
      }
      // Both stages were already connected (e.g. a stale local cache) — nothing to do.
      sessionStorage.removeItem("pendingProvider");
      setConnection("sharepoint", true, graphStatus.connected_account_id ?? null);
      setSpConnecting(false);
    } catch {
      setError("Couldn't start the SharePoint connection — check the COMPOSIO_SHAREPOINT_* settings.");
      setSpConnecting(false);
    }
  };

  const onDisconnectSharePoint = async () => {
    setSpConnecting(true);
    setError(null);
    try {
      const result = await disconnectSharePoint();
      // Clear the local cache regardless — the next "Connect" click always re-checks the
      // real backend status per stage, so this never causes a stage to be skipped. But if a
      // stage's delete call itself errored, tell the admin rather than implying full success.
      setConnection("sharepoint", false, null);
      if (result.failed.length > 0) {
        setError(
          `SharePoint disconnect was incomplete (${result.failed.join(", ")} still connected) — try again.`,
        );
      }
    } catch {
      setError("Couldn't disconnect SharePoint.");
    } finally {
      setSpConnecting(false);
    }
  };

  const onDisconnectOutlook = async () => {
    if (!outlookAccount) return;
    setConnecting(true);
    setError(null);
    try {
      await disconnectOutlook(outlookAccount);
      setConnection("outlook", false, null);
    } catch {
      setError("Couldn't disconnect Outlook.");
    } finally {
      setConnecting(false);
    }
  };

  // Open the contact-review dialog: fetch candidates (classified work/personal),
  // let the user pick, then ingest only the selected ones. Used both right after
  // connecting Outlook and on an explicit "Resync".
  const openContactReview = useCallback(async () => {
    setReviewOpen(true);
    setReviewLoading(true);
    setReviewError(null);
    setReviewContacts([]);
    try {
      const { contacts } = await previewOutlookContacts();
      setReviewContacts(contacts);
    } catch {
      setReviewError("Couldn't read your Outlook contacts. Make sure Outlook is connected, then retry.");
    } finally {
      setReviewLoading(false);
    }
  }, []);

  const onResyncContacts = () => openContactReview();

  // Confirm: enrich + graph only the selected contacts (background task).
  const onConfirmContacts = async (selected: ContactCandidate[]) => {
    setReviewOpen(false);
    setResyncing(true);
    setError(null);
    try {
      await ingestOutlookContacts(selected);
      setError(`Importing ${selected.length} contact${selected.length === 1 ? "" : "s"} — this runs in the background.`);
    } catch {
      setError("Couldn't import the selected contacts.");
    } finally {
      setResyncing(false);
    }
  };

  // Returning from the Outlook OAuth round-trip lands on /?review=outlook — open the
  // review dialog automatically, then strip the param so a refresh doesn't re-open it.
  // Landing on /?connected=sharepoint (after the Graph+REST OAuth chain) bumps
  // spGraphRefresh so the Library graph starts polling for the background crawl's
  // result automatically — the user shouldn't have to manually reload the page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    if (p.get("review") === "outlook") {
      openContactReview();
    }
    if (p.get("connected") === "sharepoint") {
      setSpGraphRefresh((n) => n + 1);
    }
    if (p.has("review") || p.has("connected")) {
      p.delete("review");
      p.delete("connected");
      const qs = p.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
    }
  }, [openContactReview]);

  // Resync the org's SharePoint structure (admin; background task).
  const onResyncSharePoint = async () => {
    setSpResyncing(true);
    setError(null);
    try {
      await syncSharePointStructure();
      setError("SharePoint resync started — it runs in the background.");
      setSpGraphRefresh((n) => n + 1);
    } catch {
      setError("Couldn't start the SharePoint resync.");
    } finally {
      setSpResyncing(false);
    }
  };

  const onPullSam = async () => {
    setPulling(true);
    setError(null);
    setPicked(new Set());
    setView("pipeline"); // jump to the list so the incoming notices are actually visible
    const before = opps.length;
    try {
      await pullFromSam(1); // only TODAY's new still-open notices (fresh-per-day)
      // Ingest-only: the matched opportunities land unanalyzed for the user to review.
      // The first pull of the day downloads ~217 MB in the worker, so be patient.
      setFilter("new"); // surface the freshly-matched, awaiting-analysis list
      await load();
      for (let i = 0; i < 45; i++) {
        await sleep(4000);
        const fresh = await load();
        if (fresh.length > before) break; // new arrivals landed
      }
    } catch {
      setError(
        "SAM.gov pull failed — check the backend, worker, SAM_GOV_API_KEY, and that your company UEI is set in Organisation settings.",
      );
    } finally {
      setPulling(false);
    }
  };

  // Toggle one opportunity in the review selection (without opening its detail).
  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // Analyst core: send ids → poll until every one has a verdict. Shared by the
  // pipeline "Analyze selected" action and the Dashboard "Analyze pending" action.
  const analyzeIds = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      await analyzeSelected(ids);
      for (let i = 0; i < 60; i++) {
        await sleep(4000);
        const fresh = await load();
        if (ids.every((id) => fresh.find((o) => o.id === id)?.bid_decision)) break;
      }
    },
    [load],
  );

  // Send only the hand-picked opportunities to the Analyst, then poll for their verdicts.
  const onAnalyzeSelected = async () => {
    const ids = [...picked];
    if (!ids.length) return;
    setAnalyzingSel(true);
    setError(null);
    try {
      await analyzeIds(ids);
      setPicked(new Set());
    } catch {
      setError("Couldn't analyze the selected opportunities — is the backend + worker running?");
    } finally {
      setAnalyzingSel(false);
    }
  };

  // Jump from the Dashboard into an opportunity's detail in the Pipeline.
  const openOpp = (id: string) => {
    setSelectedId(id);
    setTab("info");
    setFilter("all");
    setFacets(EMPTY_FACETS); // clear filters so the opened pursuit is actually in the list
    setViewingDate(null);
    setView("pipeline");
  };

  // Human override of the Analyst verdict (Bid / Watch / No-Bid).
  const onSetDecision = async (id: string, decision: BidDecision) => {
    setError(null);
    // Optimistic: reflect the new verdict on the UI immediately; persist in the background.
    setOpps((prev) =>
      prev.map((o) =>
        o.id === id ? { ...o, bid_decision: decision, decision_overridden: true } : o,
      ),
    );
    try {
      await setDecision(id, decision);
    } catch {
      setError("Couldn't save the decision — reverting.");
      await load(); // re-sync from the server to undo the optimistic change
    }
  };

  const onApproveCapture = async (id: string) => {
    setCapturing(true);
    setError(null);
    try {
      await approveCapture(id);
      for (let i = 0; i < 90; i++) {
        await sleep(5000);
        const fresh = await load();
        const updated = fresh.find((o) => o.id === id);
        if (updated?.captured_at) break;
      }
    } catch {
      setError("Capture failed — is the backend + worker running?");
    } finally {
      setCapturing(false);
    }
  };

  const onRunOutreach = async (id: string) => {
    setOutreachBusy(id);
    setError(null);
    const before = opps.find((o) => o.id === id)?.outreach_drafted_at ?? null;
    try {
      await runOutreach(id);
      for (let i = 0; i < 60; i++) {
        await sleep(5000);
        const fresh = await load();
        const updated = fresh.find((o) => o.id === id);
        if (updated && updated.outreach_drafted_at && updated.outreach_drafted_at !== before) break;
      }
    } catch {
      setError("Couldn't draft outreach — is the backend + worker running?");
    } finally {
      setOutreachBusy(null);
    }
  };

  const onRunOutreachOne = async (id: string, email: string) => {
    setOutreachOne(email);
    setError(null);
    const draftBefore = opps
      .find((o) => o.id === id)
      ?.outreach_drafts?.find((d) => (d.to ?? "").toLowerCase() === email.toLowerCase())?.body;
    try {
      await runOutreachOne(id, email);
      for (let i = 0; i < 30; i++) {
        await sleep(4000);
        const fresh = await load();
        const d = fresh
          .find((o) => o.id === id)
          ?.outreach_drafts?.find((x) => (x.to ?? "").toLowerCase() === email.toLowerCase());
        if (d && d.body !== draftBefore) break;
      }
    } catch {
      setError("Couldn't regenerate this email — is the backend + worker running?");
    } finally {
      setOutreachOne(null);
    }
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of FILTERS) c[f.key] = opps.filter(f.match).length;
    return c;
  }, [opps]);

  // The Bid pursuits shown in the left sidebar (always visible).
  const bidOpps = useMemo(() => opps.filter((o) => o.bid_decision === "Bid"), [opps]);

  // Distinct facet values for the filter pickers (derived from the loaded opps).
  const facetOptions = useMemo(() => {
    const uniq = (arr: (string | undefined | null)[]) =>
      [...new Set(arr.filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b));
    return {
      agencies: uniq(opps.map((o) => o.agency)),
      naics: uniq(opps.map((o) => o.naics)),
      setAsides: uniq(opps.map((o) => o.set_aside)),
    };
  }, [opps]);

  // Everything matching the active status filter + facets + search, BEFORE the
  // calendar-day filter. The calendar dots derive from this so a dot only shows on
  // days that actually have opportunities in the current view.
  const dayPool = useMemo(() => {
    const f = FILTERS.find((x) => x.key === filter) ?? FILTERS[0];
    const q = query.trim().toLowerCase();
    const inValue = (v?: number | null) => {
      switch (facets.value) {
        case "lt1m":
          return v != null && v < 1_000_000;
        case "1to10m":
          return v != null && v >= 1_000_000 && v <= 10_000_000;
        case "gt10m":
          return v != null && v > 10_000_000;
        default:
          return true;
      }
    };
    const inDue = (s?: string | null) => {
      if (facets.due === "any") return true;
      const d = dueLabel(s).days;
      return d != null && d >= 0 && d <= Number(facets.due);
    };
    return opps
      .filter(f.match)
      .filter((o) => facets.agencies.length === 0 || (!!o.agency && facets.agencies.includes(o.agency)))
      .filter((o) => facets.naics.length === 0 || (!!o.naics && facets.naics.includes(o.naics)))
      .filter((o) => facets.setAsides.length === 0 || (!!o.set_aside && facets.setAsides.includes(o.set_aside)))
      .filter((o) => inValue(o.estimated_value))
      .filter((o) => inDue(o.response_deadline))
      .filter(
        (o) =>
          !q ||
          o.title.toLowerCase().includes(q) ||
          (o.agency ?? "").toLowerCase().includes(q) ||
          (o.solicitation_number ?? "").toLowerCase().includes(q),
      );
  }, [opps, filter, query, facets]);

  const availableDates = useMemo(
    () => [...new Set(dayPool.map((o) => o.posted_date).filter(Boolean) as string[])],
    [dayPool],
  );

  const visible = useMemo(
    () => dayPool.filter((o) => !viewingDate || o.posted_date === viewingDate),
    [dayPool, viewingDate],
  );

  const selected = opps.find((o) => o.id === selectedId) ?? null;
  return (
    <main className="console">
      <TopBar
        user={user}
        isAdmin={isAdmin}
        view={view}
        onNavigate={setView}
        onPull={onPullSam}
        pulling={pulling}
        onSignOut={onSignOut}
      />
      <div className="workspace">
        <BidSidebar bids={bidOpps} selectedId={selectedId} onOpen={openOpp} />
        <div className="main">
      {view === "dashboard" ? (
        <section className="graph-pane">
          <DashboardView opps={opps} loading={loading} onOpen={openOpp} />
        </section>
      ) : view === "callplan" ? (
        <section className="graph-pane">
          <CallPlanView />
        </section>
      ) : view === "contacts" ? (
        <section className="graph-pane">
          <ConnectBar
            connected={outlookConnected}
            connectedLabel="Contacts connected (Outlook)"
            connectLabel="Connect your Contacts (Outlook)"
            hint="Syncs your Outlook address book + email correspondents into your private contact graph."
            busy={connecting}
            resyncing={resyncing}
            onConnect={onConnectOutlook}
            onDisconnect={onDisconnectOutlook}
            onResync={onResyncContacts}
          />
          <ContactsGraph />
        </section>
      ) : view === "documents" ? (
        <section className="graph-pane">
          {isAdmin && (
            <ConnectBar
              connected={spConnected}
              connectedLabel="Library connected (SharePoint)"
              connectLabel="Connect your Library (SharePoint)"
              hint="Org-wide. Crawls your SharePoint document structure so agents can ground on your library. Microsoft will show two sign-in confirmations back-to-back (one click here starts both)."
              busy={spConnecting}
              resyncing={spResyncing}
              onConnect={onConnectSharePoint}
              onDisconnect={onDisconnectSharePoint}
              onResync={onResyncSharePoint}
            />
          )}
          <SharePointGraph refreshSignal={spGraphRefresh} />
        </section>
      ) : view === "org" ? (
        <section className="graph-pane">
          <OrgPanel meEmail={user.email} />
        </section>
      ) : (
        <div className="pipeline">
          <FilterBar
            filters={FILTERS.map((f) => ({ key: f.key, label: f.label }))}
            filter={filter}
            onFilter={setFilter}
            counts={counts}
            facets={facets}
            onFacets={setFacets}
            options={facetOptions}
            onClear={() => setFacets(EMPTY_FACETS)}
          />
          <div className="pipeline-panes">
      {/* ---------------- master list ---------------- */}
      <section className="list">
        <div className="list-head">
          <h2>
            Opportunities
            <span className="c">{visible.length}</span>
          </h2>
          <input
            className="search"
            placeholder="Search title, agency, solicitation #…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {(() => {
            const pickable = visible.filter((o) => !o.bid_decision);
            if (pickable.length === 0) return null;
            const allOn = pickable.every((o) => picked.has(o.id));
            return (
              <div className="sel-bar">
                <button
                  className="sel-link"
                  onClick={() =>
                    setPicked((prev) => {
                      const next = new Set(prev);
                      if (allOn) pickable.forEach((o) => next.delete(o.id));
                      else pickable.forEach((o) => next.add(o.id));
                      return next;
                    })
                  }
                >
                  {allOn ? "Clear all" : `Select all ${pickable.length}`}
                </button>
                <button
                  className="mini-btn"
                  disabled={picked.size === 0 || analyzingSel}
                  onClick={onAnalyzeSelected}
                >
                  {analyzingSel ? "Analyzing…" : `Analyze ${picked.size} selected →`}
                </button>
              </div>
            );
          })()}
          <div className="cal-wrap">
            <CalendarStrip
              selectedDate={viewingDate}
              availableDates={availableDates}
              onSelect={setViewingDate}
            />
          </div>
        </div>
        <div className="rows">
          {loading && <div style={{ padding: 24, color: "var(--faint)" }}>Loading…</div>}
          {!loading && visible.length === 0 && (
            <div style={{ padding: 24, color: "var(--faint)", fontSize: 13 }}>
              {opps.length === 0 ? (
                "No opportunities yet — pull from SAM.gov or upload an Excel to begin."
              ) : viewingDate ? (
                `No fresh opportunities posted on ${fmtDate(viewingDate)}.`
              ) : activeFacetCount(facets) > 0 ? (
                <>
                  No opportunities match your filters.{" "}
                  <button className="sel-link" onClick={() => setFacets(EMPTY_FACETS)}>
                    Clear filters
                  </button>
                </>
              ) : (
                "Nothing in this view."
              )}
            </div>
          )}
          {visible.map((o, i) => (
            <button
              key={o.id}
              className={`row ${o.id === selectedId ? "sel" : ""}`}
              style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}
              onClick={() => {
                setSelectedId(o.id);
                setTab("info");
              }}
            >
              <div className="row-top">
                {!o.bid_decision && (
                  <span
                    className={`chk ${picked.has(o.id) ? "on" : ""}`}
                    role="checkbox"
                    aria-checked={picked.has(o.id)}
                    title="Select for analysis"
                    onClick={(e) => toggleSelect(o.id, e)}
                  >
                    {picked.has(o.id) ? "✓" : ""}
                  </span>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row-title">{o.title}</div>
                  {o.agency && <div className="row-agency">{o.agency}</div>}
                </div>
                <span className={`badge ${badgeClass(o.bid_decision)}`}>
                  {o.bid_decision ?? "New"}
                </span>
              </div>
              <div className="row-meta">
                <span className="pri-dot" style={{ background: priColor(o.priority_score) }} />
                {o.priority_score != null && (
                  <span className="pscore">
                    P<b>{o.priority_score}</b>
                  </span>
                )}
                {o.solicitation_number && <span className="sol">{o.solicitation_number}</span>}
                <span className="val">{money(o.estimated_value)}</span>
                {o.source === "sam.gov" && <span className="src-tag">SAM.gov</span>}
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* ---------------- detail ---------------- */}
      <section className="detail">
        {!selected ? (
          <div className="empty-detail">
            <div className="big">Select an opportunity</div>
            <div>Pick one from the list to see its verdict, contacts, and documents.</div>
          </div>
        ) : (
          <Detail
            opp={selected}
            tab={tab}
            setTab={setTab}
            capturing={capturing}
            isAdmin={isAdmin}
            members={members}
            onAssign={(ids) => onAssign(selected.id, ids)}
            onApproveCapture={() => onApproveCapture(selected.id)}
            onSetDecision={(d) => onSetDecision(selected.id, d)}
            outreachBusy={outreachBusy === selected.id}
            onRunOutreach={() => onRunOutreach(selected.id)}
            outreachOne={outreachOne}
            onRunOutreachOne={(email) => onRunOutreachOne(selected.id, email)}
          />
        )}
      </section>
          </div>
        </div>
      )}
        </div>
      </div>

      {reviewOpen && (
        <ContactReviewModal
          contacts={reviewContacts}
          loading={reviewLoading}
          error={reviewError}
          onConfirm={onConfirmContacts}
          onClose={() => setReviewOpen(false)}
        />
      )}

      {error && <div className="toast">{error}</div>}
    </main>
  );
}

// A connect / disconnect / resync strip shown at the top of the Contacts and
// Library sections (the connection lives WHERE its data lives, not in the nav).
function ConnectBar({
  connected,
  connectedLabel,
  connectLabel,
  hint,
  busy,
  resyncing,
  onConnect,
  onDisconnect,
  onResync,
}: {
  connected: boolean;
  connectedLabel: string;
  connectLabel: string;
  hint: string;
  busy: boolean;
  resyncing: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onResync: () => void;
}) {
  return (
    <div className={`connect-bar ${connected ? "on" : ""}`}>
      <span className="cb-dot" />
      <div className="cb-text">
        <div className="cb-title">{connected ? connectedLabel : connectLabel}</div>
        <div className="cb-hint">{hint}</div>
      </div>
      {connected ? (
        <div className="cb-actions">
          <button className="mini-btn" onClick={onResync} disabled={resyncing}>
            {resyncing ? "Refreshing…" : "Refresh"}
          </button>
          <button className="sel-link" onClick={onDisconnect} disabled={busy}>
            {busy ? "…" : "Disconnect"}
          </button>
        </div>
      ) : (
        <div className="cb-actions">
          <button className="mini-btn" onClick={onConnect} disabled={busy}>
            {busy ? "Opening Microsoft…" : "Connect"}
          </button>
        </div>
      )}
    </div>
  );
}

// The Dashboard: an at-a-glance view of the pipeline — what's pending, what's
// done, what's in pursuit — plus a due-date agenda (keyed on the response
// deadline, with human-relative labels like "Due tomorrow" / "Due Aug 2028").
function DashboardView({
  opps,
  loading,
  onOpen,
}: {
  opps: Opportunity[];
  loading: boolean;
  onOpen: (id: string) => void;
}) {
  // Dashboard shows ONLY the opportunities marked Bid (your active pursuits).
  const bids = useMemo(() => opps.filter((o) => o.bid_decision === "Bid"), [opps]);

  // Deadline agenda over the bids, soonest first. Overdue is excluded entirely;
  // bids without a deadline fall into their own group so they're never hidden.
  const agenda = useMemo(() => {
    const rows = bids
      .map((o) => ({ o, due: dueLabel(o.response_deadline) }))
      .filter((r) => r.due.days == null || (r.due.days ?? 0) >= 0) // drop overdue
      .sort((a, b) => (a.due.days ?? Infinity) - (b.due.days ?? Infinity));
    const inRange = (r: (typeof rows)[number], lo: number, hi: number) =>
      r.due.days != null && r.due.days >= lo && r.due.days <= hi;
    const groups: { key: string; label: string; rows: typeof rows }[] = [
      { key: "week", label: "Due this week", rows: rows.filter((r) => inRange(r, 0, 7)) },
      { key: "month", label: "Due this month", rows: rows.filter((r) => inRange(r, 8, 31)) },
      { key: "later", label: "Due later", rows: rows.filter((r) => (r.due.days ?? -1) > 31) },
      { key: "none", label: "No deadline", rows: rows.filter((r) => r.due.days == null) },
    ];
    return groups.filter((g) => g.rows.length > 0);
  }, [bids]);

  const dueThisWeek = useMemo(
    () =>
      bids.filter((o) => {
        const d = dueLabel(o.response_deadline).days;
        return d != null && d >= 0 && d <= 7;
      }).length,
    [bids],
  );
  const capturedBids = useMemo(() => bids.filter((o) => !!o.captured_at).length, [bids]);

  // Calendar day the user clicked (YYYY-MM-DD, local) — filters the right-hand panel.
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const selectedDayBids = useMemo(
    () =>
      !selectedDay
        ? []
        : bids.filter((o) => {
            if (!o.response_deadline) return false;
            const d = new Date(o.response_deadline);
            if (isNaN(d.getTime())) return false;
            return toLocalIso(new Date(d.getFullYear(), d.getMonth(), d.getDate())) === selectedDay;
          }),
    [bids, selectedDay],
  );

  const stats: { label: string; value: number; tone?: string }[] = [
    { label: "Active pursuits", value: bids.length, tone: "bid" },
    { label: "Captured", value: capturedBids, tone: "bid" },
    { label: "Due this week", value: dueThisWeek, tone: dueThisWeek > 0 ? "watch" : "" },
  ];

  // id -> title, for the mail-triage cards to show which pursuit an incoming mail belongs to.
  const opportunityTitles = useMemo(
    () => Object.fromEntries(opps.map((o) => [o.id, o.title])),
    [opps],
  );

  return (
    <div className="dash">
      <div className="dash-head">
        <div>
          <h1>Dashboard</h1>
          <div className="dash-sub">
            Your active pursuits — opportunities marked <b>Bid</b>, by deadline.
          </div>
        </div>
      </div>

      <div className="dash-stats dash-stats-3">
        {stats.map((s) => (
          <div className="dash-stat" key={s.label}>
            <div className={`ds-n ${s.tone ?? ""}`}>{s.value}</div>
            <div className="ds-l">{s.label}</div>
          </div>
        ))}
      </div>

      <MailTriagePanel opportunityTitles={opportunityTitles} onOpenOpportunity={onOpen} />

      <div className="dash-cal2">
        <DashCalendar bids={bids} selectedDay={selectedDay} onSelectDay={setSelectedDay} />

        <div className="dash-col">
          {selectedDay ? (
            <>
              <div className="dash-col-head">
                <span>Due {fmtDate(selectedDay)}</span>
                <button className="sel-link" onClick={() => setSelectedDay(null)}>
                  Clear
                </button>
              </div>
              {selectedDayBids.length === 0 ? (
                <div className="dash-empty">No active pursuits due this day.</div>
              ) : (
                <div className="dash-agenda">
                  {selectedDayBids.map((o) => (
                    <button className="dash-row" key={o.id} onClick={() => onOpen(o.id)}>
                      <div className="dr-main">
                        <div className="dr-title">{o.title}</div>
                        <div className="dr-sub">{o.agency ?? "—"}</div>
                      </div>
                      <span className={`due-chip ${dueLabel(o.response_deadline).tone}`}>
                        {dueLabel(o.response_deadline).text}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="dash-col-head">
                <span>Active pursuits · by deadline</span>
              </div>
              {loading ? (
                <div className="dash-empty">Loading…</div>
              ) : bids.length === 0 ? (
                <div className="dash-empty">
                  No active pursuits yet. Mark an opportunity <b>Bid</b> to see it here.
                </div>
              ) : agenda.length === 0 ? (
                <div className="dash-empty">No upcoming deadlines on your active pursuits.</div>
              ) : (
                <div className="dash-agenda">
                  {agenda.map((g) => (
                    <div className="agenda-group" key={g.key}>
                      <div className={`agenda-label ${g.key}`}>
                        {g.label} <span className="c">{g.rows.length}</span>
                      </div>
                      {g.rows.map(({ o, due }) => (
                        <button className="dash-row" key={o.id} onClick={() => onOpen(o.id)}>
                          <div className="dr-main">
                            <div className="dr-title">{o.title}</div>
                            <div className="dr-sub">{o.agency ?? "—"}</div>
                          </div>
                          <span className={`due-chip ${due.tone}`}>{due.text}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Month calendar for the Dashboard — marks the days that have Bid opportunities due
// (today-or-future only; overdue is never marked). Clicking a marked day selects it.
function DashCalendar({
  bids,
  selectedDay,
  onSelectDay,
}: {
  bids: Opportunity[];
  selectedDay: string | null;
  onSelectDay: (iso: string | null) => void;
}) {
  const [cursor, setCursor] = useState(() => {
    const t = new Date();
    return { y: t.getFullYear(), m: t.getMonth() };
  });

  // iso-day -> count of bids due that day (drop overdue).
  const byDay = useMemo(() => {
    const map = new Map<string, number>();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    for (const o of bids) {
      if (!o.response_deadline) continue;
      const d = new Date(o.response_deadline);
      if (isNaN(d.getTime())) continue;
      const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      if (day.getTime() < today.getTime()) continue; // no overdue
      const iso = toLocalIso(day);
      map.set(iso, (map.get(iso) ?? 0) + 1);
    }
    return map;
  }, [bids]);

  // 6-week grid (42 cells) starting on the Sunday on/before the 1st.
  const cells = useMemo(() => {
    const first = new Date(cursor.y, cursor.m, 1);
    const start = new Date(cursor.y, cursor.m, 1 - first.getDay());
    return Array.from({ length: 42 }, (_, i) =>
      new Date(start.getFullYear(), start.getMonth(), start.getDate() + i),
    );
  }, [cursor]);

  const todayIso = toLocalIso(new Date());
  const monthLabel = new Date(cursor.y, cursor.m, 1).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
  const step = (delta: number) =>
    setCursor((c) => {
      const m = c.m + delta;
      if (m < 0) return { y: c.y - 1, m: 11 };
      if (m > 11) return { y: c.y + 1, m: 0 };
      return { y: c.y, m };
    });

  return (
    <div className="dash-cal">
      <div className="dcal-head">
        <button className="dcal-nav" onClick={() => step(-1)} aria-label="Previous month">
          ‹
        </button>
        <div className="dcal-month">{monthLabel}</div>
        <button className="dcal-nav" onClick={() => step(1)} aria-label="Next month">
          ›
        </button>
        <button
          className="dcal-today"
          onClick={() => {
            const t = new Date();
            setCursor({ y: t.getFullYear(), m: t.getMonth() });
          }}
        >
          Today
        </button>
      </div>
      <div className="dcal-grid">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((w) => (
          <div className="dcal-wd" key={w}>
            {w}
          </div>
        ))}
        {cells.map((d, i) => {
          const iso = toLocalIso(d);
          const count = byDay.get(iso) ?? 0;
          const inMonth = d.getMonth() === cursor.m;
          return (
            <button
              key={i}
              className={`dcal-day ${inMonth ? "" : "muted"} ${count ? "has" : ""} ${
                iso === todayIso ? "today" : ""
              } ${iso === selectedDay ? "sel" : ""}`}
              onClick={() => (count ? onSelectDay(iso === selectedDay ? null : iso) : undefined)}
              disabled={!count}
              title={count ? `${count} due` : undefined}
            >
              <span className="dcal-num">{d.getDate()}</span>
              {count > 0 && <span className="dcal-dot">{count}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CallPlanView() {
  const [calls, setCalls] = useState<CallPlanItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCalls(await fetchCallPlan());
    } catch {
      setErr("Couldn't load the call plan — is the backend running?");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const update = async (callId: string, status: string) => {
    setErr(null);
    setCalls((prev) => prev.map((c) => (c.call_id === callId ? { ...c, status } : c))); // optimistic
    try {
      await setCallStatus(callId, status);
    } catch {
      setErr("Couldn't update the call — reverting.");
      load();
    }
  };

  const active = calls.filter((c) => c.status === "Planned");
  const resolved = calls.filter((c) => c.status !== "Planned");
  const ordered = [...active, ...resolved];

  return (
    <div className="callplan">
      <div className="cp-head">
        <h2>
          Call Plan <span className="c">{active.length}</span>
        </h2>
        <div className="cp-sub">
          Calls the Analyst recommends across your pipeline — your consolidated call sheet.
        </div>
      </div>
      {loading ? (
        <div className="cp-empty">Loading…</div>
      ) : calls.length === 0 ? (
        <div className="cp-empty">
          No calls planned yet. The Analyst adds a call here whenever it marks an
          opportunity <b>Bid</b> with a recommended outreach.
        </div>
      ) : (
        <div className="cp-list">
          {ordered.map((c) => (
            <div className={`cp-card ${c.status !== "Planned" ? "muted" : ""}`} key={c.call_id}>
              <div className="cp-top">
                <div style={{ minWidth: 0 }}>
                  <div className="cp-title">{c.opportunity_title ?? "Opportunity"}</div>
                  {c.agency && <div className="cp-agency">{c.agency}</div>}
                </div>
                <div className="cp-metric">
                  {c.priority_score != null && (
                    <span className="pscore">
                      P<b>{c.priority_score}</b>
                    </span>
                  )}
                  {c.response_deadline && (
                    <span className="cp-due">due {fmtDate(c.response_deadline)}</span>
                  )}
                </div>
              </div>
              {c.talking_point && <div className="cp-talk">“{c.talking_point}”</div>}
              <div className="cp-foot">
                <span className="cp-contact">
                  {c.poc_name || c.poc_email || "Contracting office"}
                  {c.poc_email && c.poc_name ? ` · ${c.poc_email}` : ""}
                </span>
                <div className="cp-actions">
                  {c.status === "Planned" ? (
                    <>
                      <button className="mini-btn" onClick={() => update(c.call_id, "Done")}>
                        Mark done
                      </button>
                      <button className="sel-link" onClick={() => update(c.call_id, "Dismissed")}>
                        Dismiss
                      </button>
                    </>
                  ) : (
                    <>
                      <span className={`cp-status ${c.status.toLowerCase()}`}>{c.status}</span>
                      <button className="sel-link" onClick={() => update(c.call_id, "Planned")}>
                        Reopen
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {err && <div className="toast">{err}</div>}
    </div>
  );
}

function Detail({
  opp,
  tab,
  setTab,
  capturing,
  isAdmin,
  members,
  onAssign,
  onApproveCapture,
  onSetDecision,
  outreachBusy,
  onRunOutreach,
  outreachOne,
  onRunOutreachOne,
}: {
  opp: Opportunity;
  tab: TabKey;
  setTab: (t: TabKey) => void;
  capturing: boolean;
  isAdmin: boolean;
  members: TeamMember[];
  onAssign: (userIds: string[]) => void;
  onApproveCapture: () => void;
  onSetDecision: (d: BidDecision) => void;
  outreachBusy: boolean;
  onRunOutreach: () => void;
  outreachOne: string | null;
  onRunOutreachOne: (email: string) => void;
}) {
  const captured = !!opp.captured_at;
  const docs = opp.documents ?? [];
  const calls = opp.calls ?? [];
  const tasks = opp.tasks ?? [];
  const canCapture = opp.bid_decision === "Bid";
  const [assignOpen, setAssignOpen] = useState(false);

  const tabs: { key: TabKey; label: string; count?: number }[] = [
    { key: "info", label: "Information" },
    { key: "contacts", label: "Contacts", count: opp.recommended_contacts?.length ?? 0 },
    { key: "documents", label: "Documents", count: docs.length },
    { key: "activity", label: "Activity", count: tasks.length },
  ];

  return (
    <>
      <div className="detail-head">
        <div className="dh-top">
          <div>
            <h1>{opp.title}</h1>
            <div className="dh-sub">
              {opp.agency && <span>{opp.agency}</span>}
              {opp.solicitation_number && (
                <>
                  <span className="sep">·</span>
                  <span className="sol">{opp.solicitation_number}</span>
                </>
              )}
              {opp.set_aside && (
                <>
                  <span className="sep">·</span>
                  <span>{opp.set_aside}</span>
                </>
              )}
              <span className="sep">·</span>
              <span className={`badge ${badgeClass(opp.bid_decision)}`}>
                {opp.bid_decision ?? "Unanalyzed"}
              </span>
            </div>
          </div>
          {opp.priority_score != null && (
            <div className="verdict-card">
              <div className="vp" style={{ color: priColor(opp.priority_score) }}>
                {opp.priority_score}
              </div>
              <div className="vl">Priority</div>
            </div>
          )}
        </div>

        {/* Manual override of the verdict */}
        <div className="decision-toggle">
          <span className="dt-label">Decision</span>
          {(["Bid", "Watch", "No-Bid"] as BidDecision[]).map((d) => (
            <button
              key={d}
              className={`dt-btn ${opp.bid_decision === d ? `on ${badgeClass(d)}` : ""}`}
              onClick={() => onSetDecision(d)}
              disabled={opp.bid_decision === d}
              title={`Mark as ${d}`}
            >
              {d}
            </button>
          ))}
          {opp.decision_overridden && <span className="dt-manual">set manually</span>}
        </div>

        {/* Assign to members — admin only (PriceIQ-style share modal) */}
        {isAdmin && (
          <div className="decision-toggle">
            <span className="dt-label">Members</span>
            <button className="dt-btn" onClick={() => setAssignOpen(true)}>
              {(opp.assigned_to ?? []).length > 0
                ? `Assigned · ${(opp.assigned_to ?? []).length}`
                : "Assign…"}
            </button>
            {(opp.assigned_to ?? []).length === 0 && (
              <span className="dt-manual">unassigned · open to all</span>
            )}
          </div>
        )}
        {assignOpen && (
          <AssignModal
            oppTitle={opp.title}
            members={members}
            assigned={opp.assigned_to ?? []}
            onSave={(ids) => onAssign(ids)}
            onClose={() => setAssignOpen(false)}
          />
        )}

        <div className="tabs">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={`tab ${tab === t.key ? "on" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              {t.count ? <span className="tct">{t.count}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div className="tab-body" key={tab}>
        {tab === "info" && <InfoTab opp={opp} />}
        {tab === "contacts" && (
          <ContactsTab
            opp={opp}
            outreachBusy={outreachBusy}
            onRunOutreach={onRunOutreach}
            outreachOne={outreachOne}
            onRunOutreachOne={onRunOutreachOne}
          />
        )}
        {tab === "documents" && <DocumentsTab opp={opp} />}
        {tab === "activity" && <ActivityTab opp={opp} tasks={tasks} calls={calls} />}
      </div>

      <div className="detail-foot">
        {captured ? (
          // After capture completes, the same button becomes the mail action.
          <button
            className="btn primary"
            onClick={() => {
              setTab("contacts");
              onRunOutreach();
            }}
            disabled={outreachBusy || (opp.recommended_contacts ?? []).every((c) => !c.email)}
            title="Draft an outreach email for each relevant contact"
          >
            {outreachBusy ? (
              <>
                <span className="spin" />
                Drafting mail…
              </>
            ) : opp.outreach_drafted_at ? (
              "Re-run mail"
            ) : (
              "Run mail"
            )}
          </button>
        ) : (
          <button
            className="btn primary"
            onClick={onApproveCapture}
            disabled={capturing || !canCapture}
          >
            {capturing ? (
              <>
                <span className="spin" />
                Running capture…
              </>
            ) : (
              "Approve for capture"
            )}
          </button>
        )}
        {captured ? (
          <span className="foot-note">
            <b>{(opp.documents ?? []).length} documents</b> generated · {fmtDate(opp.captured_at)}
          </span>
        ) : !canCapture ? (
          <span className="foot-note">Only “Bid” opportunities advance to capture.</span>
        ) : (
          <span className="foot-note">Generates the capture plan + customer deliverables.</span>
        )}
      </div>
    </>
  );
}

function InfoTab({ opp }: { opp: Opportunity }) {
  const rows: [string, string, boolean?][] = [
    ["Agency", opp.agency ?? "—"],
    ["Solicitation #", opp.solicitation_number ?? "—", true],
    ["NAICS", opp.naics ?? "—", true],
    ["PSC", opp.psc_code ?? "—", true],
    ["Set-aside", opp.set_aside ?? "—"],
    ["Type", opp.opp_type ?? "—"],
    ["Est. value", money(opp.estimated_value)],
    ["Response due", fmtDate(opp.response_deadline)],
    ["Place of performance", opp.place_of_performance ?? "—"],
    ["Posted", fmtDate(opp.posted_date)],
    ["Source", opp.source === "sam.gov" ? "SAM.gov" : opp.source ?? "—"],
  ];
  return (
    <>
      {opp.analyst_rationale && (
        <>
          <div className="sec-title first">Analyst verdict</div>
          <p className="rationale">{opp.analyst_rationale}</p>
        </>
      )}
      <div className="sec-title">Opportunity details</div>
      <div className="kv-grid">
        {rows.map(([k, v, mono]) => (
          <div className="kv" key={k}>
            <div className="k">{k}</div>
            <div className={`v ${mono ? "mono" : ""}`}>{v}</div>
          </div>
        ))}
      </div>
      {opp.link && (
        <a className="sam-link" href={opp.link} target="_blank" rel="noreferrer">
          View on SAM.gov ↗
        </a>
      )}
      {opp.description && (
        <>
          <div className="sec-title">Description</div>
          <p style={{ color: "var(--muted)", lineHeight: 1.6, fontSize: 13.5 }}>
            {opp.description}
          </p>
        </>
      )}
    </>
  );
}

function ContactsTab({
  opp,
  outreachBusy,
  onRunOutreach,
  outreachOne,
  onRunOutreachOne,
}: {
  opp: Opportunity;
  outreachBusy: boolean;
  onRunOutreach: () => void;
  outreachOne: string | null;
  onRunOutreachOne: (email: string) => void;
}) {
  const relevant = opp.recommended_contacts ?? [];
  const searched = !!opp.contacts_searched_at;
  const drafts = opp.outreach_drafts ?? [];
  const drafted = !!opp.outreach_drafted_at;
  const emailable = relevant.filter((c) => c.email).length;

  // Collision check: which contacts a teammate is already engaging. Keyed on the
  // actual contact emails (not just opp.id) so it refetches when a background reload
  // repopulates recommended_contacts for the same opportunity (e.g. during capture).
  const [collisions, setCollisions] = useState<Record<string, CollisionItem[]>>({});
  const contactEmailsKey = (opp.recommended_contacts ?? [])
    .map((c) => c.email)
    .filter(Boolean)
    .join(",");
  useEffect(() => {
    const emails = contactEmailsKey ? contactEmailsKey.split(",") : [];
    if (!emails.length) {
      setCollisions({});
      return;
    }
    let alive = true;
    fetchCollisions(emails)
      .then((d) => alive && setCollisions(d))
      .catch(() => alive && setCollisions({}));
    return () => {
      alive = false;
    };
  }, [contactEmailsKey]);
  return (
    <>
      <div className="sec-title first sec-row">
        <span>Relevant contacts · from your network</span>
        {emailable > 0 && (
          <button className="mini-btn" onClick={onRunOutreach} disabled={outreachBusy}>
            {outreachBusy
              ? "Drafting…"
              : drafted
                ? "Regenerate emails"
                : `Draft ${emailable} email${emailable > 1 ? "s" : ""}`}
          </button>
        )}
      </div>
      {relevant.length > 0 ? (
        <div className="card-list">
          {relevant.map((c, i) => {
            const draft =
              c.email
                ? drafts.find((d) => (d.to ?? "").toLowerCase() === c.email!.toLowerCase())
                : undefined;
            return (
              <div className="rec" key={i}>
                <div className="rec-top">
                  <div>
                    <div className="rec-name">
                      {c.name}
                      {c.company && (
                        <span style={{ color: "var(--muted)", fontWeight: 400 }}> · {c.company}</span>
                      )}
                    </div>
                    {c.title && (
                      <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 2 }}>{c.title}</div>
                    )}
                  </div>
                  {c.relevance_score != null && (
                    <span className="pscore">
                      P<b>{c.relevance_score}</b>
                    </span>
                  )}
                </div>
                {c.email && (collisions[c.email.toLowerCase()]?.length ?? 0) > 0 && (
                  <div className="collision-warn">
                    ⚠{" "}
                    {collisions[c.email.toLowerCase()]
                      .map(
                        (x) =>
                          `${x.employee_email.split("@")[0]} ${x.action} this contact` +
                          (x.opportunity_title ? ` (${x.opportunity_title})` : "") +
                          (x.created_at ? ` on ${fmtDate(x.created_at)}` : ""),
                      )
                      .join("; ")}
                    {" — coordinate before reaching out."}
                  </div>
                )}
                {c.reason && <div className="rec-body">{c.reason}</div>}
                {c.suggested_outreach && (
                  <div className="rec-body" style={{ color: "var(--accent)" }}>
                    ↳ {c.suggested_outreach}
                  </div>
                )}
                {c.email && (
                  <div style={{ marginTop: 8 }}>
                    <a href={`mailto:${c.email}`}>{c.email}</a>
                  </div>
                )}
                {/* the outreach email for THIS contact, inline */}
                {c.email &&
                  (outreachBusy || (outreachOne === c.email && !draft) ? (
                    <div className="mail-drafting">Drafting email…</div>
                  ) : draft ? (
                    <MailArtifact
                      draft={draft}
                      inline
                      regenerating={outreachOne === c.email}
                      onRegenerate={() => onRunOutreachOne(c.email!)}
                    />
                  ) : null)}
              </div>
            );
          })}
        </div>
      ) : searched ? (
        <div className="empty-tab">
          <div className="et-t">No relevant contacts</div>
          The CRM agent searched your network and found no one relevant for this opportunity.
        </div>
      ) : (
        <div className="empty-tab">
          <div className="et-t">Not searched yet</div>
          The CRM agent searches your network for relevant contacts during capture.
        </div>
      )}

      <div className="sec-title">Point of contact</div>
      {opp.poc_name || opp.poc_email ? (
        <div className="rec">
          <div className="rec-top">
            <div className="rec-name">{opp.poc_name ?? "Contracting office"}</div>
            <span className="pill">POC</span>
          </div>
          {opp.poc_email && (
            <div className="rec-body">
              <a href={`mailto:${opp.poc_email}`}>{opp.poc_email}</a>
            </div>
          )}
        </div>
      ) : (
        <div className="empty-tab">
          <div className="et-t">No contact on file</div>
          No POC was provided for this opportunity.
        </div>
      )}

    </>
  );
}

function MailArtifact({
  draft,
  inline = false,
  regenerating = false,
  onRegenerate,
}: {
  draft: OutreachDraft;
  inline?: boolean;
  regenerating?: boolean;
  onRegenerate?: () => void;
}) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [err, setErr] = useState<string | null>(null);
  const [showWhy, setShowWhy] = useState(false);
  const grounded = draft.grounded_on ?? [];

  // When the draft is regenerated (new content arrives), replace the editable fields.
  useEffect(() => {
    setSubject(draft.subject);
    setBody(draft.body);
    setStatus("idle");
    setErr(null);
  }, [draft.subject, draft.body]);

  const onSend = async () => {
    if (!draft.to) {
      setErr("No recipient email on this draft.");
      setStatus("error");
      return;
    }
    setStatus("sending");
    setErr(null);
    try {
      await sendMail({ ...draft, subject, body });
      setStatus("sent");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Send failed.");
      setStatus("error");
    }
  };

  return (
    <div className={`mail-art ${inline ? "inline" : ""} ${status === "sent" ? "sent" : ""}`}>
      <div className="mail-head">
        <div className="mail-to">
          <span className="mail-label">To</span>
          <span className="mail-rcpt">
            {draft.to_name ? `${draft.to_name} ` : ""}
            <span className="mail-addr">&lt;{draft.to ?? "—"}&gt;</span>
          </span>
        </div>
        <div className="mail-actions">
          {onRegenerate && status !== "sent" && (
            <button
              className="mail-regen"
              onClick={onRegenerate}
              disabled={regenerating || status === "sending"}
              title="Regenerate just this email"
            >
              {regenerating ? "Regenerating…" : "↻ Regenerate"}
            </button>
          )}
          {status === "sent" ? (
            <span className="mail-sent">Sent ✓</span>
          ) : (
            <button
              className="mail-send"
              onClick={onSend}
              disabled={status === "sending" || regenerating}
            >
              {status === "sending" ? "Sending…" : "Send"}
            </button>
          )}
        </div>
      </div>

      <input
        className="mail-subject"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        disabled={status === "sent"}
        placeholder="Subject"
      />
      <textarea
        className="mail-body"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={status === "sent"}
        rows={Math.min(14, Math.max(6, body.split("\n").length + 1))}
      />

      {err && <div className="mail-err">{err}</div>}

      {grounded.length > 0 && (
        <div className="mail-why">
          <button className="mail-why-t" onClick={() => setShowWhy((v) => !v)}>
            {showWhy ? "▾" : "▸"} Grounded on {grounded.length} source
            {grounded.length > 1 ? "s" : ""}
          </button>
          {showWhy && (
            <ul className="mail-why-list">
              {grounded.map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// Fixed order for the Bid subfolders when grouping the live SharePoint listing.
const SP_SUBFOLDER_ORDER = ["Solicitation", "Capture Docs", "Resources", "Response"];
const fileSize = (n?: number) =>
  n == null ? "" : n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

function DocumentsTab({ opp }: { opp: Opportunity }) {
  const docs = opp.documents ?? [];
  const sp = opp.sharepoint_folder ?? null;
  const [preview, setPreview] = useState<DocItem | null>(null);

  // The read half of two-way sync: pull the Bid folder's LIVE contents so a file a human
  // dropped into SharePoint appears here without any re-upload. Refetched per opportunity.
  const [spResp, setSpResp] = useState<SharePointFilesResponse | null>(null);
  const [spLoading, setSpLoading] = useState(false);
  useEffect(() => {
    if (!sp) {
      setSpResp(null);
      return;
    }
    let alive = true;
    setSpResp(null); // clear the previous bid's files immediately so they don't bleed across a switch
    setSpLoading(true);
    fetchOpportunitySharePointFiles(opp.id)
      .then((r) => alive && setSpResp(r))
      .catch(
        () =>
          alive &&
          setSpResp({ connected: true, files: [], error: "Couldn't read the SharePoint folder just now." }),
      )
      .finally(() => alive && setSpLoading(false));
    return () => {
      alive = false;
    };
    // Keyed on the folder id (a primitive) — `sp` is a fresh object on every poll-driven
    // reload, so depending on it would refetch needlessly.
  }, [opp.id, sp?.folder_id]);
  const spFiles = spResp?.files ?? null;
  const spError = spResp?.error ?? null;

  // Group the live files by subfolder, in the canonical order.
  const grouped = (() => {
    const by: Record<string, SharePointFile[]> = {};
    for (const f of spFiles ?? []) (by[f.subfolder] ??= []).push(f);
    const names = [
      ...SP_SUBFOLDER_ORDER.filter((n) => by[n]),
      ...Object.keys(by).filter((n) => !SP_SUBFOLDER_ORDER.includes(n)),
    ];
    return names.map((n) => ({ name: n, files: by[n] }));
  })();

  const spCard = sp?.web_url ? (
    <>
      <div className="sec-title first">Bid workspace · SharePoint</div>
      <div className="sp-folder">
        <div className="sp-folder-top">
          <div className="sp-folder-name">📁 {sp.name}</div>
          <a className="sp-open" href={sp.web_url} target="_blank" rel="noreferrer">
            Open in SharePoint ↗
          </a>
        </div>
        {/* Live folder contents, grouped by subfolder. Includes agent-written deliverables
            AND anything a person dropped straight into SharePoint. */}
        {spLoading && spResp === null ? (
          <div className="sp-files-empty">Reading the SharePoint folder…</div>
        ) : spError ? (
          <div className="sp-files-empty sp-files-error">{spError}</div>
        ) : grouped.length === 0 ? (
          <div className="sp-files-empty">
            Folder is empty. Files added here — by the capture agents or by anyone in SharePoint —
            show up automatically.
          </div>
        ) : (
          <div className="sp-groups">
            {grouped.map((g) => (
              <div className="sp-group" key={g.name}>
                <div className="sp-group-head">
                  <span className="sp-sub">{g.name}</span>
                  <span className="sp-group-ct">{g.files.length}</span>
                </div>
                <div className="sp-file-list">
                  {g.files.map((f) => (
                    <a
                      key={f.id ?? f.name}
                      className="sp-file"
                      // Files → open in Office-for-the-web edit mode (autosaves back to
                      // SharePoint); folders → just open in SharePoint.
                      href={
                        (f.is_folder ? f.web_url : f.edit_url ?? f.web_url) ?? sp.web_url ?? "#"
                      }
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span className="sp-file-ico">{f.is_folder ? "📁" : "📄"}</span>
                      <span className="sp-file-name">{f.name}</span>
                      {!f.is_folder && f.size != null && (
                        <span className="sp-file-size">{fileSize(f.size)}</span>
                      )}
                      <span className="sp-file-open">{f.is_folder ? "Open ↗" : "Edit ↗"}</span>
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  ) : null;

  return (
    <>
      {spCard}
      {docs.length === 0 ? (
        // With a SharePoint card already showing the live folder, skip the big empty block.
        spCard ? null : (
          <div className="empty-tab">
            <div className="et-t">No documents yet</div>
            Approve this opportunity for capture to generate the capture plan and customer
            deliverables.
          </div>
        )
      ) : (
        <>
          <div className={`sec-title ${spCard ? "" : "first"}`}>Generated documents · preview</div>
          <div className="card-list">
            {docs.map((d, i) => (
              <button
                className="rec rec-doc"
                key={i}
                onClick={() => setPreview(d)}
                title="Preview"
              >
                <div className="rec-top">
                  <div>
                    <span className="doc-type">{d.type.replace(/_/g, " ")}</span>
                    <div className="rec-name" style={{ marginTop: 4 }}>
                      {d.title}
                    </div>
                  </div>
                  <div className="doc-actions">
                    <span className="doc-preview-link">Preview</span>
                    <a
                      href="#"
                      onClick={async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        try {
                          window.open(await getDocUrl(d.id), "_blank", "noopener");
                        } catch {
                          /* ignore — preview still works */
                        }
                      }}
                    >
                      Open ↗
                    </a>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </>
      )}
      {preview && (
        <FilePreview
          documentId={preview.id}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  );
}

function ActivityTab({
  opp,
  tasks,
  calls,
}: {
  opp: Opportunity;
  tasks: NonNullable<Opportunity["tasks"]>;
  calls: NonNullable<Opportunity["calls"]>;
}) {
  // Build a simple event timeline from real timestamps.
  type Ev = { t: string; date?: string; s?: string };
  const events: Ev[] = [];
  if (opp.posted_date) events.push({ t: "Opportunity posted", date: opp.posted_date });
  if (opp.analyzed_at)
    events.push({
      t: `Analyst verdict: ${opp.bid_decision ?? "—"} (P${opp.priority_score ?? "—"})`,
      date: opp.analyzed_at,
      s: opp.analyst_rationale,
    });
  for (const c of calls)
    events.push({ t: c.name, date: c.created_at, s: c.talking_point });
  for (const tk of tasks)
    events.push({ t: tk.name, date: tk.created_at, s: tk.description });
  for (const d of opp.documents ?? [])
    events.push({ t: `Document: ${d.title}`, date: d.created_at, s: d.type.replace(/_/g, " ") });
  if (opp.captured_at) events.push({ t: "Capture complete", date: opp.captured_at });

  events.sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));

  if (events.length === 0) {
    return (
      <div className="empty-tab">
        <div className="et-t">No activity yet</div>
        Activity appears as the analyst and capture agents work this opportunity.
      </div>
    );
  }

  return (
    <>
      {tasks.length > 0 && (
        <>
          <div className="sec-title first">Follow-up tasks</div>
          <div className="card-list" style={{ marginBottom: 8 }}>
            {tasks.map((tk, i) => (
              <div className="rec" key={i}>
                <div className="rec-top">
                  <div className="rec-name">{tk.name}</div>
                  <span className="pill">{tk.status ?? "Open"}</span>
                </div>
                {tk.description && <div className="rec-body">{tk.description}</div>}
              </div>
            ))}
          </div>
        </>
      )}
      <div className="sec-title first">Timeline</div>
      <div className="timeline">
        {events.map((e, i) => (
          <div className="tl" key={i}>
            <div className="tl-t">{e.t}</div>
            <div className="tl-d">{fmtDate(e.date)}</div>
            {e.s && <div className="tl-s">{e.s}</div>}
          </div>
        ))}
      </div>
    </>
  );
}

// ---------------- Organisation panel (admin only): org settings + UEI + team ----------------
const orgLabel: React.CSSProperties = {
  fontSize: 11,
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  color: "var(--faint)",
};

function OrgPanel({ meEmail }: { meEmail: string }) {
  const [org, setOrg] = useState<Organization | null>(null);
  const [orgName, setOrgName] = useState("");
  const [uei, setUei] = useState("");
  const [savingOrg, setSavingOrg] = useState(false);
  const [orgMsg, setOrgMsg] = useState<string | null>(null);
  const [subTab, setSubTab] = useState<"settings" | "team">("settings");
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"user" | "admin">("user");
  const [inviting, setInviting] = useState(false);
  const [inviteMsg, setInviteMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [o, m, inv] = await Promise.all([
        organizationsApi.getMyOrganization().catch(() => null),
        organizationsApi.getMembers(),
        invitationsApi.listInvitations("pending").catch(() => [] as Invitation[]),
      ]);
      if (o) {
        setOrg(o);
        setOrgName(o.name ?? "");
        setUei(o.uei ?? "");
      }
      setMembers(m);
      setInvites(inv);
      setErr(null);
    } catch (e) {
      setErr(errText(e, "Couldn't load the organisation."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Single action: save the name + UEI, then automatically pull the company's
  // details from SAM.gov (if a UEI is set).
  const saveOrg = async () => {
    setSavingOrg(true);
    setErr(null);
    setOrgMsg(null);
    try {
      const updated = await organizationsApi.updateOrganization({
        name: orgName.trim(),
        uei: uei.trim(),
      });
      let merged = updated;
      if (uei.trim()) {
        try {
          const details = await organizationsApi.lookupUei();
          merged = { ...updated, company_details: details };
          setOrgMsg("Saved — company details pulled from SAM.gov.");
        } catch (e) {
          setOrgMsg("Saved. SAM.gov lookup failed: " + errText(e, "try again."));
        }
      } else {
        setOrgMsg("Saved.");
      }
      setOrg(merged);
    } catch (e) {
      setErr(errText(e, "Couldn't save the organisation."));
    } finally {
      setSavingOrg(false);
    }
  };

  const onInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    const email = inviteEmail.trim();
    if (!email) return;
    setInviting(true);
    setErr(null);
    setInviteMsg(null);
    try {
      await invitationsApi.sendInvitation({ email, role: inviteRole });
      setInviteMsg(`Invitation sent to ${email}.`);
      setInviteEmail("");
      await load();
    } catch (e) {
      setErr(errText(e, "Couldn't send the invitation."));
    } finally {
      setInviting(false);
    }
  };

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setErr(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(errText(e, "Action failed."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "28px 24px", width: "100%", overflowY: "auto" }}>
      <h1 style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 500, letterSpacing: "-0.015em", color: "var(--ink)" }}>
        Organisation
      </h1>
      <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 4 }}>
        Manage your company profile and team.
      </div>

      <div className="tabs" style={{ marginTop: 18 }}>
        <button className={`tab ${subTab === "settings" ? "on" : ""}`} onClick={() => setSubTab("settings")}>
          Settings
        </button>
        <button className={`tab ${subTab === "team" ? "on" : ""}`} onClick={() => setSubTab("team")}>
          Team{members.length ? <span className="tct">{members.length}</span> : null}
        </button>
      </div>

      {err && <div className="mail-err" style={{ marginTop: 12 }}>{err}</div>}

      <div className="tab-body" key={subTab}>
        {subTab === "settings" ? (
          <>
            <div className="sec-title first">Company information</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={orgLabel}>Company name</label>
                <input
                  className="search"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  placeholder="Your company name"
                />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={orgLabel}>UEI · SAM.gov Unique Entity ID</label>
                <input
                  className="search"
                  value={uei}
                  onChange={(e) => setUei(e.target.value.toUpperCase())}
                  placeholder="e.g. UFZCFEVTJG77"
                  style={{ fontFamily: "var(--font-mono)" }}
                />
                <div style={{ fontSize: 11.5, color: "var(--faint)" }}>
                  On save we pull your company&apos;s registration (legal name, CAGE, NAICS, status) from SAM.gov.
                </div>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <button className="btn primary" onClick={saveOrg} disabled={savingOrg}>
                  {savingOrg ? (
                    <>
                      <span className="spin" /> Saving…
                    </>
                  ) : (
                    "Save"
                  )}
                </button>
                {orgMsg && <span style={{ color: "var(--bid)", fontSize: 13 }}>{orgMsg}</span>}
              </div>
            </div>

            {org?.company_details && (
              <>
                <div className="sec-title">Company details · SAM.gov</div>
                <div className="kv-grid">
                  {([
                    ["Legal name", org.company_details.legal_business_name],
                    ["CAGE", org.company_details.cage_code],
                    ["Registration", org.company_details.registration_status],
                    ["Expires", org.company_details.registration_expiration],
                    ["Set-asides", (org.company_details.business_types ?? []).join(", ")],
                    [
                      "Location",
                      [
                        org.company_details.physical_address?.city,
                        org.company_details.physical_address?.state,
                        org.company_details.physical_address?.zip,
                      ]
                        .filter(Boolean)
                        .join(", "),
                    ],
                    ["NAICS", (org.company_details.naics ?? []).join(", ")],
                  ] as [string, string | null | undefined][])
                    .filter(([, v]) => v)
                    .map(([k, v]) => (
                      <div className="kv" key={k}>
                        <div className="k">{k}</div>
                        <div className="v">{v}</div>
                      </div>
                    ))}
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginTop: 4 }}>
              {([
                ["Members + invitations", members.length + invites.length],
                ["Active members", members.length],
                ["Pending", invites.length],
              ] as [string, number][]).map(([label, value]) => (
                <div
                  key={label}
                  style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12, padding: "16px 18px" }}
                >
                  <div style={{ fontFamily: "var(--font-display)", fontSize: 26, fontWeight: 500, color: "var(--ink)" }}>
                    {value}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>{label}</div>
                </div>
              ))}
            </div>

            <div className="sec-title">Invite a teammate</div>
            <form onSubmit={onInvite} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <input
                className="search"
                type="email"
                placeholder="name@company.com"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
                style={{ flex: 1, minWidth: 220 }}
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as "user" | "admin")}
                className="search"
                style={{ width: 130 }}
              >
                <option value="user">Member</option>
                <option value="admin">Admin</option>
              </select>
              <button className="btn primary" type="submit" disabled={inviting}>
                {inviting ? "Sending…" : "Send invite"}
              </button>
            </form>
            {inviteMsg && <div style={{ color: "var(--bid)", fontSize: 13, marginTop: 8 }}>{inviteMsg}</div>}

            <div className="sec-title">Members{members.length ? ` · ${members.length}` : ""}</div>
            {loading ? (
              <div style={{ color: "var(--faint)", fontSize: 13 }}>Loading…</div>
            ) : (
              <div className="card-list">
                {members.map((m) => {
                  const isSelf = (m.email || "").toLowerCase() === meEmail.toLowerCase();
                  return (
                    <div className="rec" key={m.id}>
                      <div className="rec-top">
                        <div>
                          <div className="rec-name">
                            {m.firstName} {m.lastName}
                            {isSelf && <span style={{ color: "var(--faint)", fontWeight: 400 }}> · you</span>}
                          </div>
                          <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 2 }}>{m.email}</div>
                        </div>
                        <span className="pill">{m.role === "admin" ? "Admin" : "Member"}</span>
                      </div>
                      {!isSelf && (
                        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                          {m.role === "admin" ? (
                            <button
                              className="mini-btn"
                              disabled={busy === m.id}
                              onClick={() => act(m.id, () => organizationsApi.demoteMember(m.id))}
                            >
                              {busy === m.id ? "…" : "Demote to member"}
                            </button>
                          ) : (
                            <button
                              className="mini-btn"
                              disabled={busy === m.id}
                              onClick={() => act(m.id, () => organizationsApi.promoteMember(m.id))}
                            >
                              {busy === m.id ? "…" : "Make admin"}
                            </button>
                          )}
                          <button
                            className="mini-btn"
                            disabled={busy === m.id}
                            onClick={() => act(m.id, () => organizationsApi.removeMember(m.id))}
                            style={{ color: "var(--nobid)" }}
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {invites.length > 0 && (
              <>
                <div className="sec-title">Pending invitations · {invites.length}</div>
                <div className="card-list">
                  {invites.map((inv) => {
                    const id = (inv.id ?? inv._id) as string;
                    return (
                      <div className="rec" key={id}>
                        <div className="rec-top">
                          <div>
                            <div className="rec-name">{inv.email}</div>
                            <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 2 }}>
                              Invited as {inv.role === "admin" ? "Admin" : "Member"}
                            </div>
                          </div>
                          <button
                            className="mini-btn"
                            disabled={busy === id}
                            onClick={() => act(id, () => invitationsApi.revokeInvitation(id))}
                          >
                            {busy === id ? "…" : "Revoke"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// Pull a human message out of an axios error (or anything else).
function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (e instanceof Error ? e.message : fallback) || fallback;
}
