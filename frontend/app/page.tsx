"use client";

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AgentTrail } from "@/components/agent/AgentTrail";
import { PageTransition, switchView } from "@/components/PageTransition";
import { useQueryState, useQueryStates, parseAsString, debounce } from "nuqs";
import { pipelineParsers } from "@/lib/pipeline-params";
import {
  fetchOpportunityPage,
  fetchOpportunityCounts,
  fetchFacets,
  fetchPostedDates,
  fetchOpportunity,
  pullFromSam,
  analyzeSelected,
  setDecision,
  assignOpportunity,
  updateOpportunityContacts,
  type RecommendedContact,
  getDocUrl,
  fetchOpportunitySharePointFiles,
  type SharePointFile,
  type SharePointFilesResponse,
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
import FilePreview from "./FilePreview";
import AddOpportunityModal from "./AddOpportunityModal";
import AssignModal from "./AssignModal";
import ContactReviewModal from "./ContactReviewModal";
import SharePointFolderPicker from "./SharePointFolderPicker";
import TopBar from "./TopBar";
import { useUiStore, type ViewKey, type TabKey } from "@/lib/stores/uiStore";
import { useToastStore } from "@/lib/stores/toastStore";
import CallBriefDialog from "./CallBriefDialog";
import TodayView from "./TodayView";
import DashboardView from "./DashboardView";
import RiskMeter from "./RiskMeter";
import CallPlanFilters, {
  EMPTY_CALL_FILTERS,
  type CallFilters,
} from "./CallPlanFilters";
import BidSidebar from "./BidSidebar";
import FilterBar, { type Facets, EMPTY_FACETS, activeFacetCount } from "./FilterBar";
import {
  bidsQuery,
  callPlanQuery,
  opportunityListQuery,
  opportunityQuery,
  organizationQuery,
  queryKeys,
  type OrgBundle,
} from "@/lib/queries";
import { useCollecctCache } from "@/lib/cache";
import { usePrefetchOpportunity } from "@/lib/use-prefetch";
import { dueLabel } from "@/lib/format";
import { useAuthStore } from "@/lib/stores/authStore";
import { useConnectionStore } from "@/lib/stores/connectionStore";
import { organizationsApi } from "@/lib/api/organizations";
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

// In-flight / failed state shown as a row chip, independent of the bid verdict.
// Ingesting: the upload -> parse -> digest -> Analyst pipeline is still running.
// Processing: a Capture run (agent + contact search) is in flight for a Bid.
const isIngesting = (o: Opportunity) => !!o.ingesting;
// A run that DIED is not a run in flight. Without the failure clause a crashed capture sits
// in "Processing" forever showing a spinner — four of them sat there for a month.
const isProcessing = (o: Opportunity) =>
  !!o.capture_approved && !o.captured_at && !o.capture_failed_at;
const activityChip = (o: Opportunity): { label: string; cls: string } | null => {
  if (o.ingest_error) return { label: "Ingest failed", cls: "failed" };
  if (isIngesting(o)) return { label: "Ingesting", cls: "ingesting" };
  if (o.capture_error) return { label: "Capture failed", cls: "failed" };
  if (isProcessing(o)) return { label: "Processing", cls: "processing" };
  return null;
};

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


// One shared empty array, so `query.data ?? NO_OPPS` keeps a stable identity while a query
// is still pending. A fresh `[]` per render would re-trigger every useMemo/useDeferredValue
// downstream of it on every commit.
const NO_OPPS: Opportunity[] = [];

// Same stable-identity trick for the call sheet, so `query.data ?? NO_CALLS` does not hand a
// fresh array to every downstream useMemo on each render.
const NO_CALLS: CallPlanItem[] = [];

// Pipeline stages shown as the portfolio nav. "All" first.
const FILTERS: { key: string; label: string; match: (o: Opportunity) => boolean }[] = [
  { key: "all", label: "All opportunities", match: () => true },
  { key: "ingesting", label: "Ingesting", match: (o) => isIngesting(o) },
  { key: "processing", label: "Processing", match: (o) => isProcessing(o) },
  { key: "Bid", label: "Bid — pursue", match: (o) => o.bid_decision === "Bid" },
  { key: "Watch", label: "Watch — revisit", match: (o) => o.bid_decision === "Watch" },
  { key: "No-Bid", label: "No-bid", match: (o) => o.bid_decision === "No-Bid" },
  { key: "captured", label: "Capture complete", match: (o) => !!o.captured_at },
  // Awaiting analysis = no verdict yet AND not still ingesting (those live in Ingesting).
  { key: "new", label: "Awaiting analysis", match: (o) => !o.bid_decision && !isIngesting(o) },
];

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
  const qc = useQueryClient();
  const cache = useCollecctCache();
  const prefetchOpp = usePrefetchOpportunity();
  const pushToast = useToastStore((s) => s.push);
  // Thin shim over the toast store: existing handlers call setError("msg") / setError(null).
  // A message becomes a toast; the null-clear is a no-op now that toasts auto-dismiss.
  const setError = (msg: string | null) => {
    if (msg) pushToast(msg);
  };
  // Filter state lives in the URL, so a filtered pipeline is shareable and the back button
  // works. `q` is separate from the group because it carries a debounce — folding a debounced
  // key into useQueryStates would debounce every filter with it.
  const [urlParams, setUrlParams] = useQueryStates(pipelineParsers);
  const filter = urlParams.status;
  const setFilter = useCallback(
    (v: string) => void setUrlParams({ status: v }),
    [setUrlParams],
  );
  const [query, setQuery] = useQueryState(
    "q",
    // The input stays instant; only the URL write waits for the pause.
    parseAsString.withDefault("").withOptions({ limitUrlUpdates: debounce(300) }),
  );
  // FilterBar still speaks the `Facets` shape; the URL uses the BACKEND's key names so the
  // query string can be forwarded as-is. This adapts between the two.
  const facets: Facets = useMemo(
    () => ({
      agencies: urlParams.agency,
      naics: urlParams.naics,
      setAsides: urlParams.set_aside,
      source: urlParams.source,
      value: urlParams.value,
      due: urlParams.due,
    }),
    [urlParams],
  );
  const setFacets = useCallback(
    (f: Facets) =>
      void setUrlParams({
        agency: f.agencies,
        naics: f.naics,
        set_aside: f.setAsides,
        source: f.source,
        value: f.value,
        due: f.due,
      }),
    [setUrlParams],
  );
  const selectedId = useUiStore((s) => s.selectedId);
  const setSelectedId = useUiStore((s) => s.setSelectedId);
  // Detail sheet width: null = the CSS default. The expand button sets a wide preset; dragging
  // the sheet's left edge sets a custom px width. Both write here so there's one source of truth.
  const detailWidth = useUiStore((s) => s.detailWidth);
  const setDetailWidth = useUiStore((s) => s.setDetailWidth);
  const startDetailResize = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    const onMove = (ev: MouseEvent) => {
      // The sheet is anchored to the right, so its width is the distance from the pointer to
      // the right edge. Clamp between a readable minimum and (nearly) the full window.
      const w = Math.min(Math.max(window.innerWidth - ev.clientX, 380), window.innerWidth - 48);
      setDetailWidth(w);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);
  const tab = useUiStore((s) => s.tab);
  const setTab = useUiStore((s) => s.setTab);
  const [pulling, setPulling] = useState(false);
  const [analyzingSel, setAnalyzingSel] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [members, setMembers] = useState<TeamMember[]>([]); // for the assign dropdown (admins)
  const [viewingDate, setViewingDate] = useState<string | null>(null); // calendar day, null = All
  const [capturing, setCapturing] = useState(false);
  const [outreachBusy, setOutreachBusy] = useState<string | null>(null);
  const [outreachOne, setOutreachOne] = useState<string | null>(null);
  // Connection state is cached (set on the OAuth redirect) so most loads don't need a
  // round trip. But the cache is per-browser localStorage — if the user connected on a
  // different device/browser, hit a stale attempt before a later one actually succeeded,
  // or cleared storage, the cache can say "not connected" while the backend disagrees.
  // Reconcile from the real backend status once on mount so the UI is never permanently
  // wrong just because the redirect-time cache write didn't happen on THIS browser.
  const outlook = useConnectionStore((s) => s.outlook);
  const sharepoint = useConnectionStore((s) => s.sharepoint);
  const setConnection = useConnectionStore((s) => s.setConnection);
  const outlookConnected = outlook.connected;
  const outlookAccount = outlook.accountId;
  const spConnected = sharepoint.connected;

  // Which providers are connected drives the Connect bars on Contacts and Library and the
  // Organisation panel — and nothing else. Checked when one of those is opened, once, rather
  // than on every app start regardless of destination.
  const view = useUiStore((s) => s.view);

  const wantsConnStatus =
    view === "contacts" || view === "documents" || view === "org";
  const connChecked = useRef(false);
  useEffect(() => {
    if (!wantsConnStatus || connChecked.current) return;
    connChecked.current = true;
    getConnStatus("outlook")
      .then((s) => setConnection("outlook", s.connected, s.connected_account_id ?? null))
      .catch(() => {}); // offline/unauthenticated — keep whatever the cache already says
    getConnStatus("sharepoint")
      .then((s) => setConnection("sharepoint", s.connected, s.connected_account_id ?? null))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsConnStatus]);
  const [connecting, setConnecting] = useState(false);
  const [spConnecting, setSpConnecting] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [spResyncing, setSpResyncing] = useState(false);
  // "Go refetch" signals for the graph views, now in the ui store so the graph components
  // subscribe directly instead of taking a refreshSignal prop. Bumped when a sync starts
  // (SharePoint) / an ingest completes (Contacts) / a disconnect purges the graph server-side.
  const bumpSharePointRefresh = useUiStore((s) => s.bumpSharePointRefresh);
  const bumpContactsRefresh = useUiStore((s) => s.bumpContactsRefresh);
  const spPickerOpen = useUiStore((s) => s.spPickerOpen);
  const setSpPickerOpen = useUiStore((s) => s.setSpPickerOpen);
  const addOppOpen = useUiStore((s) => s.addOppOpen);
  const setAddOppOpen = useUiStore((s) => s.setAddOppOpen);
  // Outlook contact-review dialog (pick which contacts to ingest).
  const reviewOpen = useUiStore((s) => s.reviewOpen);
  const setReviewOpen = useUiStore((s) => s.setReviewOpen);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewContacts, setReviewContacts] = useState<ContactCandidate[]>([]);
  const setView = useUiStore((s) => s.setView);
  const setPrepCallFor = useUiStore((s) => s.setPrepCallFor);
  // Every view change goes through here so the swap animates. Sibling tabs imply no
  // hierarchy, so the transition is "lateral": a fade and a 12px rise, no directional slide.
  const navigate = useCallback((v: ViewKey) => switchView(() => setView(v)), []);

  // --- server-side pagination state (replaces "load every opp") ---
  const PAGE = 50;
  const [counts, setCounts] = useState<Record<string, number>>({}); // filter-pill counts (server)
  const [decidingId, setDecidingId] = useState<string | null>(null); // row whose verdict is saving
  const [inFlight, setInFlight] = useState(0); // ingesting+processing (arms the poll)
  const [facetOptions, setFacetOptions] = useState<{ agencies: string[]; naics: string[]; setAsides: string[] }>(
    { agencies: [], naics: [], setAsides: [] },
  );
  const [availableDates, setAvailableDates] = useState<string[]>([]); // calendar dots (server)
  const [selectedOpp, setSelectedOpp] = useState<Opportunity | null>(null); // enriched, lazy-loaded
  const [detailLoading, setDetailLoading] = useState(false);
  const [debouncedQuery, setDebouncedQuery] = useState(""); // search, debounced ~300ms

  // Debounce the search box so we don't fire a server query per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(t);
  }, [query]);

  // All active filters/search/calendar folded into the server query params.
  const params = useMemo(
    () => ({
      status: filter,
      agencies: facets.agencies,
      naics: facets.naics,
      setAsides: facets.setAsides,
      source: facets.source !== "any" ? facets.source : undefined,
      value: facets.value,
      due: facets.due,
      q: debouncedQuery,
      postedDate: viewingDate,
    }),
    [filter, facets, debouncedQuery, viewingDate],
  );

  // "Load more" grows ONE query's window rather than appending a second query's result.
  // The window is part of the query key, so the bigger page is fetched while the smaller
  // one stays on screen (placeholderData) — no accumulator state to keep in step with the
  // filters, and a refetch/poll can never interleave two offsets into a duplicated list.
  //
  // The window is stored WITH the params it was opened against, so the reset on a filter
  // change is a pure derivation rather than a second piece of state to keep in sync. An
  // effect that reset it would commit one render at {new params, old window} and fire a
  // request for a page nobody asked for.
  const [listWindow, setListWindow] = useState({ params, extra: 0 });
  const extra = listWindow.params === params ? listWindow.extra : 0;
  const limit = PAGE + extra;

  const listOptions = useMemo(
    () => opportunityListQuery({ ...params, offset: 0, limit }),
    [params, limit],
  );

  // THE LOADING RULE (plan 5.2). `placeholderData: previous` lives in opportunityListQuery,
  // so a filter change, a keystroke or the 5s poll keeps the current rows painted and only
  // flips isFetching. Nothing below may gate rendering on isFetching — only on isPending.
  // Same gating as the Bid set: a 50-row page of the pipeline is meaningless on Today or
  // Contacts, and on this deployment it measured ~3.9s of the landing view's wait.
  const listQuery = useQuery({ ...listOptions, enabled: view === "pipeline" });

  const rows = listQuery.data?.items ?? NO_OPPS;
  const total = listQuery.data?.total ?? 0;

  // The list failing to reach the backend is its own toast. Fires on the false→true edge, so
  // a retry that stays failed doesn't re-toast on every poll.
  useEffect(() => {
    if (listQuery.isError) pushToast("Can't reach the backend — start it on :8000.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQuery.isError]);

  // Reload the pipeline list + the Bid set. Both live under the "opportunities" key prefix,
  // so one invalidation covers what used to be loadPage() + loadBids(). Awaitable: the SAM
  // pull and the analyst poll below want the refreshed rows before they continue.
  const reload = useCallback(() => cache.opportunities(), [cache]);

  // Display-only optimism for a verdict/assignment the user just clicked: patch the row in
  // the page currently on screen so the badge flips under their finger. There is nothing to
  // roll back — the server stays the authority and the invalidation that follows re-reads
  // it. Deliberately NOT in lib/cache.ts, which is an invalidation layer by design.
  const patchRow = useCallback(
    (id: string, patch: Partial<Opportunity>) => {
      qc.setQueryData(listOptions.queryKey, (page) =>
        page
          ? { ...page, items: page.items.map((o) => (o.id === id ? { ...o, ...patch } : o)) }
          : page,
      );
    },
    [qc, listOptions.queryKey],
  );

  // Write a freshly-fetched record through to BOTH the open pane and the cache. The
  // long-poll handlers below (capture, outreach, analyse) already hold the server's answer,
  // so writing it through is a fact, not optimism — and it stops the row's hover prefetch
  // from later handing the detail pane the pre-run copy it cached minutes ago.
  const putOpp = useCallback(
    (fresh: Opportunity) => {
      qc.setQueryData(opportunityQuery(fresh.id).queryKey, fresh);
      setSelectedOpp((prev) => (prev && prev.id === fresh.id ? fresh : prev));
    },
    [qc],
  );

  // Per-status pill counts + in-flight total (for the poll) — recomputed when filters change.
  const loadCounts = useCallback(async () => {
    try {
      const { counts: c, in_flight } = await fetchOpportunityCounts(params);
      setCounts(c);
      setInFlight(in_flight);
    } catch {
      /* leave last counts */
    }
  }, [params]);

  // Calendar dots — distinct posted dates across the active filter (correct across all pages).
  const loadDates = useCallback(async () => {
    try {
      setAvailableDates(await fetchPostedDates(params));
    } catch {
      /* leave last dates */
    }
  }, [params]);

  // The Bid set for the Call Plan sidebar + the Dashboard.
  //
  // GATED ON THE VIEW, and it matters more than it looks: `fetchBids` pages through the org's
  // ENTIRE Bid set 100 at a time, so on a few hundred pursuits it is several SEQUENTIAL round
  // trips — the single most expensive thing this shell can ask for. It used to run on mount
  // whatever you were looking at, which meant landing on Today (which never reads it) put its
  // one small request behind this, the pipeline page, the counts, the dates and the facets.
  // Today is the landing view; it should not wait on the rest of the console's data.
  const wantsBids = view === "callplan" || view === "dashboard";
  const bidsQ = useQuery({ ...bidsQuery(), enabled: wantsBids });
  const bidOpps = bidsQ.data ?? NO_OPPS;

  // Counts, calendar dots and facet options all feed the Pipeline's filter bar and nothing
  // else, so they load with the Pipeline rather than with the app.
  const onPipeline = view === "pipeline";

  // On any filter/search/status/calendar change: refresh counts + dots. The rows themselves
  // need no effect — `params` is part of the query key, so changing it IS the refetch.
  useEffect(() => {
    if (!onPipeline) return;
    loadCounts();
    loadDates();
  }, [onPipeline, loadCounts, loadDates]);

  // Facet dropdown options: once, the first time the Pipeline is opened.
  const facetsLoaded = useRef(false);
  useEffect(() => {
    if (!onPipeline || facetsLoaded.current) return;
    facetsLoaded.current = true;
    fetchFacets()
      .then((f) => setFacetOptions({ agencies: f.agencies, naics: f.naics, setAsides: f.set_asides }))
      .catch(() => {});
  }, [onPipeline]);

  // While anything is mid-flight (ingesting / a capture run), poll so the Ingesting/Processing
  // sections self-empty as workers finish — even for items not on the current page. Armed by the
  // server-side in_flight count, so it works cross-tab and after reload; disarms when idle.
  useEffect(() => {
    if (inFlight <= 0) return;
    const t = setInterval(() => {
      void reload();
      loadCounts();
    }, 5000);
    return () => clearInterval(t);
  }, [inFlight, reload, loadCounts]);

  // The detail pane lazy-loads the FULL (enriched) opportunity when a row is opened.
  // Through the query cache, not a bare fetch: that is what makes the row's hover/focus
  // prefetch pay off — an already-warm record resolves without a round trip, so the
  // "Loading…" state below never gets a chance to render.
  useEffect(() => {
    if (!selectedId) {
      setSelectedOpp(null);
      return;
    }
    let alive = true;
    setDetailLoading(true);
    qc.fetchQuery(opportunityQuery(selectedId))
      .then((o) => alive && setSelectedOpp(o))
      .catch(() => alive && setSelectedOpp(null))
      .finally(() => alive && setDetailLoading(false));
    return () => {
      alive = false;
    };
  }, [selectedId, qc]);

  // NOTE: the localStorage filter cache that used to live here is gone. Filters are in the
  // URL now (see lib/pipeline-params.ts), which is both shareable and survives a reload — and
  // a localStorage restore running on mount would immediately fight the URL for control.

  // Keep non-admins out of the admin-only views (e.g. if demoted mid-session).
  useEffect(() => {
    if (!isAdmin && (view === "documents" || view === "org")) {
      navigate("pipeline");
    }
  }, [isAdmin, view]);

  // Admins need the member roster to assign opportunities — which happens in the pursuit
  // detail sheet, reachable from the Pipeline. Fetched once, when the Pipeline is first
  // opened, rather than on every app start: on Today it was two more requests competing
  // with the only one that view actually needs.
  const membersLoaded = useRef(false);
  useEffect(() => {
    if (!isAdmin || view !== "pipeline" || membersLoaded.current) return;
    membersLoaded.current = true;
    organizationsApi.getMembers().then(setMembers).catch(() => {});
  }, [isAdmin, view]);

  // Assign an opportunity to members (admin). Optimistic; reverts on failure.
  const onAssign = async (id: string, userIds: string[]) => {
    setError(null);
    patchRow(id, { assigned_to: userIds });
    setSelectedOpp((prev) => (prev && prev.id === id ? { ...prev, assigned_to: userIds } : prev));
    try {
      await assignOpportunity(id, userIds);
      void cache.opportunity(id);
    } catch {
      setError("Couldn't update the assignment — reverting.");
      void reload();
    }
  };

  // Manual add/remove of contacts on the Contacts tab. The tab always sends the FULL list it
  // wants to keep, so add and remove are one code path. Optimistic: patch the open opp now,
  // reconcile with the server's echo, revert by refetch on failure.
  const onContactsChange = async (id: string, contacts: RecommendedContact[]) => {
    setError(null);
    const patch = (o: Opportunity): Opportunity => ({
      ...o,
      recommended_contacts: contacts,
      contacts_searched_at: o.contacts_searched_at ?? new Date().toISOString(),
    });
    setSelectedOpp((prev) => (prev && prev.id === id ? patch(prev) : prev));
    qc.setQueryData(opportunityQuery(id).queryKey, (o?: Opportunity) => (o ? patch(o) : o));
    try {
      const saved = await updateOpportunityContacts(id, contacts);
      const apply = (o: Opportunity): Opportunity => ({ ...o, recommended_contacts: saved });
      setSelectedOpp((prev) => (prev && prev.id === id ? apply(prev) : prev));
      qc.setQueryData(opportunityQuery(id).queryKey, (o?: Opportunity) => (o ? apply(o) : o));
    } catch {
      setError("Couldn't update the contact list — reverting.");
      void cache.opportunity(id);
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
      bumpSharePointRefresh(); // disconnect purges the graph server-side — re-fetch to reflect it
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
      bumpContactsRefresh(); // disconnect purges the graph server-side — re-fetch to reflect it
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
      bumpContactsRefresh();
    } catch {
      setError("Couldn't import the selected contacts.");
    } finally {
      setResyncing(false);
    }
  };

  // Returning from the Outlook OAuth round-trip lands on /?review=outlook — open the
  // review dialog automatically, then strip the param so a refresh doesn't re-open it.
  // Landing on /?connected=sharepoint (after the Graph+REST OAuth chain) bumps the
  // SharePoint refresh signal so the Library graph starts polling for the background crawl's
  // result automatically — the user shouldn't have to manually reload the page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    if (p.get("review") === "outlook") {
      openContactReview();
    }
    if (p.get("connected") === "sharepoint") {
      navigate("documents");        // land on Library tab
      bumpSharePointRefresh();
      setSpPickerOpen(true);        // open folder picker immediately
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
      bumpSharePointRefresh();
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
    navigate("pipeline"); // jump to the list so the incoming notices are actually visible
    const before = total;
    try {
      await pullFromSam(1); // only TODAY's new still-open notices (fresh-per-day)
      // Ingest-only: the matched opportunities land unanalyzed for the user to review.
      // The first pull of the day downloads ~217 MB in the worker, so be patient.
      setFilter("new"); // surface the freshly-matched, awaiting-analysis list (triggers a reload)
      for (let i = 0; i < 45; i++) {
        await sleep(4000);
        await loadCounts();
        const { total: t } = await fetchOpportunityPage({ ...params, status: "new", offset: 0, limit: PAGE });
        if (t > before) {
          await reload();
          break; // new arrivals landed
        }
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
        // Poll each picked opp by id (it may not be on the current page).
        const verdicts = await Promise.all(
          ids.map((id) =>
            fetchOpportunity(id)
              .then((o) => {
                putOpp(o);
                return !!o.bid_decision;
              })
              .catch(() => false),
          ),
        );
        if (verdicts.every(Boolean)) break;
      }
      await reload();
      await loadCounts();
    },
    [reload, loadCounts, putOpp],
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

  // Jump from the Dashboard into an opportunity's detail in the Pipeline. The detail pane
  // fetches the opp by id independently of the list, so no need to clear filters to "find" it.
  const openOpp = (id: string) => {
    setSelectedId(id);
    setTab("info");
    navigate("pipeline");
  };

  // Today's cards open the tool that FINISHES the job, not just the record — a card that
  // only linked to the pipeline would recreate the "here's the data, you work it out"
  // problem the view exists to remove.
  const openOppDocuments = (id: string) => {
    setSelectedId(id);
    setTab("documents");
    navigate("pipeline");
  };
  const prepCallFor = (id: string) => {
    setPrepCallFor(id);
    navigate("callplan");
  };

  // Refetch the open opp's full record (after a mutation completes server-side).
  const refreshSelected = useCallback(
    (id: string) => {
      fetchOpportunity(id).then(putOpp).catch(() => {});
    },
    [putOpp],
  );

  // Human override of the Analyst verdict (Bid / Watch / No-Bid).
  const onSetDecision = async (id: string, decision: BidDecision) => {
    setError(null);
    // Optimistic: reflect the new verdict on the UI immediately; persist in the background.
    patchRow(id, { bid_decision: decision, decision_overridden: true });
    setSelectedOpp((prev) =>
      prev && prev.id === id ? { ...prev, bid_decision: decision, decision_overridden: true } : prev,
    );
    try {
      await setDecision(id, decision);
      loadCounts(); // the verdict moves it between pills
      void cache.opportunity(id); // ...and the record, the list and in/out of the Bid set
    } catch {
      setError("Couldn't save the decision — reverting.");
      await reload();
      refreshSelected(id);
    }
  };

  /**
   * Set a verdict straight from a list row. Wraps the same optimistic path the detail sheet
   * uses, so a decision made here and one made there behave identically; the only addition
   * is a per-row busy id, because a row's buttons sit under the cursor and are trivially
   * double-clickable while the write is still in flight.
   */
  const onQuickDecision = async (id: string, decision: BidDecision) => {
    if (decidingId) return;
    setDecidingId(id);
    try {
      await onSetDecision(id, decision);
    } finally {
      setDecidingId(null);
    }
  };

  const onApproveCapture = async (id: string) => {
    setCapturing(true);
    setError(null);
    try {
      await approveCapture(id);
      loadCounts(); // now 'processing'
      for (let i = 0; i < 90; i++) {
        await sleep(5000);
        const fresh = await fetchOpportunity(id);
        if (fresh.captured_at) {
          putOpp(fresh);
          break;
        }
      }
      loadCounts();
      void reload();
    } catch {
      setError("Capture failed — is the backend + worker running?");
    } finally {
      setCapturing(false);
    }
  };

  const onRunOutreach = async (id: string) => {
    setOutreachBusy(id);
    setError(null);
    const before = selectedOpp?.id === id ? selectedOpp?.outreach_drafted_at ?? null : null;
    try {
      await runOutreach(id);
      for (let i = 0; i < 60; i++) {
        await sleep(5000);
        const fresh = await fetchOpportunity(id);
        if (fresh.outreach_drafted_at && fresh.outreach_drafted_at !== before) {
          putOpp(fresh);
          break;
        }
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
    const draftBefore =
      selectedOpp?.id === id
        ? selectedOpp?.outreach_drafts?.find((d) => (d.to ?? "").toLowerCase() === email.toLowerCase())?.body
        : undefined;
    try {
      await runOutreachOne(id, email);
      for (let i = 0; i < 30; i++) {
        await sleep(4000);
        const fresh = await fetchOpportunity(id);
        const d = fresh.outreach_drafts?.find((x) => (x.to ?? "").toLowerCase() === email.toLowerCase());
        if (d && d.body !== draftBefore) {
          putOpp(fresh);
          break;
        }
      }
    } catch {
      setError("Couldn't regenerate this email — is the backend + worker running?");
    } finally {
      setOutreachOne(null);
    }
  };

  // The server already applied the status filter + facets + search + calendar day, so the
  // loaded page IS the visible list. (`availableDates` and `counts` come from the server too.)
  //
  // Deferred, per plan 5.2: React paints the *previous* list while it renders the next one
  // at low priority, so a keystroke never blocks on reconciling 50 rows — and everything
  // derived below is derived from the same snapshot that is actually on screen, instead of
  // disagreeing with it for a frame.
  const deferredRows = useDeferredValue(rows);

  // Column sort for the pipeline TABLE. Client-side over the loaded window (the server
  // already applied filters/search); clicking a header cycles asc → desc, clicking another
  // column starts fresh. `null` key = server order (priority-ish), which is the default.
  type SortKey = "title" | "agency" | "response_deadline" | "priority_score";
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  /**
   * Click a column to sort it; click again to reverse; click a third time to drop back to
   * the server's default order.
   *
   * The third state is what let the separate "Sort" dropdown be deleted. That dropdown
   * offered exactly the four keys these headers already carry, so the surface had two
   * controls driving one piece of state — and the only thing it could express that a
   * header could not was "Default". Now the headers own it.
   */
  const onSort = useCallback((key: SortKey) => {
    setSortKey((prevKey) => {
      if (prevKey !== key) {
        setSortDir("asc");
        return key;
      }
      let cleared = false;
      setSortDir((prevDir) => {
        if (prevDir === "asc") return "desc";
        cleared = true;
        return "asc";
      });
      return cleared ? null : key;
    });
  }, []);

  const visible = useMemo(() => {
    if (!sortKey) return deferredRows;
    const dir = sortDir === "asc" ? 1 : -1;
    const val = (o: Opportunity): number | string => {
      if (sortKey === "response_deadline") {
        const d = o.response_deadline ? new Date(o.response_deadline).getTime() : NaN;
        return isNaN(d) ? 8.64e15 : d; // undated rows sort to the end
      }
      if (sortKey === "priority_score") return o.priority_score ?? -1;
      return ((o[sortKey] as string) ?? "").toLowerCase();
    };
    return [...deferredRows].sort((a, b) => {
      const av = val(a), bv = val(b);
      return av < bv ? -dir : av > bv ? dir : 0;
    });
  }, [deferredRows, sortKey, sortDir]);

  // Rows still awaiting a verdict — the "Select all / Analyze N" bar's population.
  const pickable = useMemo(() => visible.filter((o) => !o.bid_decision), [visible]);

  // A bigger window is in flight while the rendered page is still the smaller one. Note this
  // is NOT plain `isFetching`: a background poll refetch must not turn the button into a
  // progress state.
  const loadingMore = listQuery.isFetching && visible.length < limit && visible.length < total;

  // Infinite scroll: widen the window as the sentinel nears the viewport, so the pipeline
  // grows on scroll instead of a "Load more" click. Same `setListWindow` the button used.
  const loadMoreRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = loadMoreRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loadingMore && visible.length < total) {
          setListWindow({ params, extra: extra + PAGE });
        }
      },
      { rootMargin: "400px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [loadingMore, visible.length, total, params, extra]);

  // The open opp's full record (enriched, lazy-loaded). Falls back to the slim list row while
  // the detail fetch is in flight, so the header/title don't flash empty.
  const selected = selectedOpp ?? rows.find((o) => o.id === selectedId) ?? null;
  return (
    <main className="console">
      <TopBar
        user={user}
        isAdmin={isAdmin}
        view={view}
        onNavigate={navigate}
        onPull={onPullSam}
        pulling={pulling}
        onSignOut={onSignOut}
      />
      <div className={`workspace ${view === "callplan" ? "" : "no-bidbar"}`}>
        {view === "callplan" && (
          <BidSidebar bids={bidOpps} selectedId={selectedId} onOpen={openOpp} />
        )}
        <div className="main">
        {/* Wraps ONLY the swapping content — not the shell — so the top bar and Bid sidebar
            stay put while the view morphs. Fired by switchView(); an unrouted update
            renders instantly. */}
        <PageTransition>
      {view === "today" ? (
        <section className="graph-pane">
          <TodayView
            onOpenOpportunity={openOpp}
            onOpenDocuments={openOppDocuments}
            onPrepCall={prepCallFor}
            onOpenMail={() => navigate("dashboard")}
          />
        </section>
      ) : view === "dashboard" ? (
        <section className="graph-pane">
          {/* isPending, never isFetching: the 5s poll must not blank the agenda. */}
          <DashboardView
            opps={bidOpps}
            loading={bidsQ.isPending}
            onOpen={openOpp}
            // Every figure on the Dashboard is a population; handing the matching status to
            // the Pipeline is what turns "1,552 undecided" into somewhere to go.
            onNavigatePipeline={(status) => {
              setFilter(status);
              navigate("pipeline");
            }}
          />
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
              extraAction={{ label: "Select folders", onClick: () => setSpPickerOpen(true) }}
            />
          )}
          <SharePointGraph
            connected={spConnected}
            connecting={spConnecting}
            onConnect={onConnectSharePoint}
          />
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
          <div className="list-head-row">
            <h2>
              Opportunities
              <span className="c">{total}</span>
            </h2>
            <button className="mini-btn" onClick={() => setAddOppOpen(true)}>
              + Add opportunity
            </button>
          </div>
          <input
            className="search"
            placeholder="Search title, agency, solicitation #…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {(() => {
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
                  {allOn ? "Clear all" : `Select all ${pickable.length} loaded`}
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
        </div>
        <div className="rows">
          <table className="opp-table">
            <thead>
              <tr>
                <th className="col-chk" aria-hidden />
                {([
                  ["title", "Opportunity"],
                  ["agency", "Agency"],
                ] as [SortKey, string][]).map(([k, label]) => (
                  <th
                    key={k}
                    className={`sortable ${sortKey === k ? "sorted" : ""}`}
                    onClick={() => onSort(k)}
                  >
                    {label}
                    <span className="sort-caret">{sortKey === k ? (sortDir === "asc" ? "↑" : "↓") : ""}</span>
                  </th>
                ))}
                <th>Stage</th>
                {([
                  ["response_deadline", "Deadline"],
                  ["priority_score", "Priority"],
                ] as [SortKey, string][]).map(([k, label]) => (
                  <th
                    key={k}
                    className={`sortable num ${sortKey === k ? "sorted" : ""}`}
                    onClick={() => onSort(k)}
                  >
                    {label}
                    <span className="sort-caret">{sortKey === k ? (sortDir === "asc" ? "↑" : "↓") : ""}</span>
                  </th>
                ))}
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {/* THE LOADING RULE. The single empty branch is reachable only when there is
                  nothing to show; a refetch keeps the rows it has and reports in the footer. */}
              {visible.length === 0 ? (
                <tr className="opp-empty">
                  <td colSpan={7}>
                    {listQuery.isFetching ? (
                      "Loading…"
                    ) : filter === "all" &&
                      activeFacetCount(facets) === 0 &&
                      !viewingDate &&
                      !debouncedQuery ? (
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
                  </td>
                </tr>
              ) : (
                visible.map((o) => {
                  const chip = activityChip(o);
                  const overdue =
                    o.response_deadline && new Date(o.response_deadline).getTime() < Date.now();
                  const selectable = !o.bid_decision && !isIngesting(o);
                  return (
                    <tr
                      key={o.id}
                      className={`opp-row ${o.id === selectedId ? "sel" : ""}`}
                      tabIndex={0}
                      // Warm the full record before the click lands — hover AND focus, so
                      // keyboard users get the same instant open. prefetchQuery honours
                      // staleTime, so sweeping the list is not 50 requests.
                      onMouseEnter={() => prefetchOpp(o.id)}
                      onFocus={() => prefetchOpp(o.id)}
                      onClick={() => {
                        setSelectedId(o.id);
                        setTab("info");
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelectedId(o.id);
                          setTab("info");
                        }
                      }}
                    >
                      <td className="col-chk" onClick={(e) => e.stopPropagation()}>
                        {selectable && (
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
                      </td>
                      <td className="col-title">
                        <div className="ot-title">{o.title}</div>
                        {o.solicitation_number && (
                          <div className="ot-sub">{o.solicitation_number}</div>
                        )}
                      </td>
                      <td className="col-agency">{o.agency || "—"}</td>
                      <td className="col-stage" onClick={(e) => e.stopPropagation()}>
                        <span className={`badge row-badge ${chip ? chip.cls : badgeClass(o.bid_decision)}`}>
                          {chip ? chip.label : o.bid_decision ?? "New"}
                        </span>
                        {/* An in-flight row (ingesting / capture running) has no verdict to
                            set yet, so it keeps the plain chip and no controls. */}
                        {!chip && (
                          <span className="row-dec">
                            {(["Bid", "Watch", "No-Bid"] as BidDecision[]).map((d) => (
                              <button
                                key={d}
                                type="button"
                                className={`rd-btn ${d === o.bid_decision ? `on ${badgeClass(d)}` : ""}`}
                                disabled={decidingId === o.id}
                                aria-pressed={d === o.bid_decision}
                                title={`Mark ${d}`}
                                onClick={() => onQuickDecision(o.id, d)}
                              >
                                {d === "No-Bid" ? "No-bid" : d}
                              </button>
                            ))}
                          </span>
                        )}
                      </td>
                      <td className={`col-deadline num ${overdue ? "overdue" : ""}`}>
                        {fmtDate(o.response_deadline)}
                      </td>
                      <td className="col-pri num">
                        <span className="pri-dot" style={{ background: priColor(o.priority_score) }} />
                        {o.priority_score != null ? o.priority_score : "—"}
                      </td>
                      <td className="col-source">
                        {o.source === "sam.gov" ? (
                          <span className="src-tag">SAM.gov</span>
                        ) : o.source === "manual" ? (
                          <span className="src-tag manual">Manual</span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
          {/* Infinite-scroll sentinel: the observer above widens the window as this nears the
              viewport. Only mounted while there's more to load. */}
          {visible.length > 0 && visible.length < total && (
            <div ref={loadMoreRef} className="load-sentinel" aria-hidden />
          )}
        </div>
        {/* The footer is the ONLY thing that moves during a refetch. It is always mounted at
            a fixed height so the rows above it never shift when a fetch starts or ends. */}
        {/* aria-hidden, not aria-live: the in-flight poll re-enters this state every 5s while
            anything is ingesting, and a screen reader announcing "Updating…" twelve times a
            minute is worse than silence. The rows themselves are the accessible signal. */}
        <div
          className="flex h-6 shrink-0 items-center justify-center gap-2 text-[11px] text-muted-foreground"
          aria-hidden
        >
          {loadingMore ? (
            <>
              <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Loading more…
            </>
          ) : listQuery.isFetching && visible.length > 0 ? (
            <>
              <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Updating…
            </>
          ) : total > 0 ? (
            `${visible.length} of ${total}`
          ) : null}
        </div>
      </section>

      {/* ---------------- detail (slide-over sheet) ---------------- */}
      {/* Scrim: click-away closes the sheet. Present only while a row is open, so it never
          eats clicks on the table behind it. */}
      <div
        className={`detail-scrim ${selectedId ? "open" : ""}`}
        onClick={() => setSelectedId(null)}
        aria-hidden
      />
      <section
        className={`detail ${selectedId ? "open" : ""}`}
        style={detailWidth ? { width: `${detailWidth}px` } : undefined}
        role="dialog"
        aria-modal="true"
      >
        {/* Drag the left edge to resize. */}
        <div
          className="detail-resize"
          onMouseDown={startDetailResize}
          role="separator"
          aria-orientation="vertical"
          title="Drag to resize"
        />
        <div className="sheet-actions">
          {/* The sheet now opens wide, so this narrows it for reading beside the list —
              the inverse of what it used to do. `null` means "the CSS default", which is
              the wide one. */}
          <button
            className="sheet-btn"
            onClick={() =>
              setDetailWidth(detailWidth ? null : Math.min(720, Math.round(window.innerWidth * 0.82)))
            }
            aria-label={detailWidth ? "Widen the panel" : "Narrow the panel"}
            title={detailWidth ? "Widen" : "Narrow"}
          >
            {detailWidth ? "⤢" : "⤡"}
          </button>
          <button
            className="sheet-btn"
            onClick={() => setSelectedId(null)}
            aria-label="Close"
            title="Close"
          >
            ✕
          </button>
        </div>
        {!selected ? (
          selectedId && detailLoading ? (
            <div className="empty-detail">
              <div className="big">Loading…</div>
            </div>
          ) : (
            <div className="empty-detail">
              <div className="big">Select an opportunity</div>
              <div>Pick one from the list to see its verdict, contacts, and documents.</div>
            </div>
          )
        ) : (
          <Detail
            opp={selected}
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
            onContactsChange={(contacts) => onContactsChange(selected.id, contacts)}
          />
        )}
      </section>
          </div>
        </div>
      )}
        </PageTransition>
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

      {spPickerOpen && (
        <SharePointFolderPicker
          onClose={() => setSpPickerOpen(false)}
          onSaved={() => {
            setSpPickerOpen(false);
            onResyncSharePoint(); // apply the new selection immediately
          }}
        />
      )}

      {addOppOpen && (
        <AddOpportunityModal
          onClose={() => setAddOppOpen(false)}
          onCreated={(r) => {
            setAddOppOpen(false);
            // The new manual opp starts "ingesting" → refresh the list + counts (which arms the
            // in-flight poll), then open it so its docs/verdict land as they finish.
            void reload();
            loadCounts();
            setSelectedId(r.opportunity_id);
          }}
        />
      )}

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
  extraAction,
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
  extraAction?: { label: string; onClick: () => void };
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
          {extraAction && (
            <button className="mini-btn" onClick={extraAction.onClick}>
              {extraAction.label}
            </button>
          )}
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
function CallPlanView() {
  // A QUERY, not useState+useEffect+fetch. Every tab switch remounts this component, and the
  // old shape refetched the whole call sheet from zero behind a blank loading state each
  // time — five round trips across six navigations, on the app's slowest endpoint. Cached,
  // returning to the tab is instant and the refresh happens behind the rendered list.
  const qc = useQueryClient();
  const callsQ = useQuery(callPlanQuery());
  const calls = callsQ.data ?? NO_CALLS;
  const loading = callsQ.isPending;
  // Seeded from the store when Today's "Prep the call" navigated here, so the dialog is
  // already open on arrival. Read once at mount and cleared, so coming back to the Call Plan
  // later doesn't re-open a dialog the rep already closed.
  const prepCallFor = useUiStore((s) => s.prepCallFor);
  const setPrepCallFor = useUiStore((s) => s.setPrepCallFor);
  const [prepOpp, setPrepOpp] = useState<string | null>(prepCallFor);
  const [filters, setFilters] = useState<CallFilters>(EMPTY_CALL_FILTERS);
  const pushToast = useToastStore((s) => s.push);

  useEffect(() => {
    if (callsQ.isError) pushToast("Couldn't load the call plan — is the backend running?");
  }, [callsQ.isError, pushToast]);

  // Consume the hand-off exactly once. The local state above already picked it up on mount;
  // clearing it here stops a later visit re-opening the same dialog.
  useEffect(() => {
    if (prepCallFor) setPrepCallFor(null);
  }, [prepCallFor, setPrepCallFor]);

  const update = async (callId: string, status: string) => {
    // Optimistic against the CACHE now, so the row updates instantly and a background
    // refetch cannot resurrect the old status mid-flight.
    qc.setQueryData<CallPlanItem[]>(queryKeys.callPlan, (prev) =>
      (prev ?? []).map((c) => (c.call_id === callId ? { ...c, status } : c)),
    );
    try {
      await setCallStatus(callId, status);
    } catch {
      pushToast("Couldn't update the call — reverting.");
      void qc.invalidateQueries({ queryKey: queryKeys.callPlan });
    }
  };

  const active = calls.filter((c) => c.status === "Planned");
  // The pursuit whose call-prep dialog is open (one tab per contact).
  const prepping = calls.find((c) => c.opportunity_id === prepOpp) ?? null;

  // Every agency on the sheet, for the Agency picker.
  const agencyOptions = useMemo(
    () => Array.from(new Set(calls.map((c) => c.agency).filter(Boolean) as string[])).sort(),
    [calls],
  );

  const counts = useMemo(
    () => ({
      planned: calls.filter((c) => c.status === "Planned").length,
      done: calls.filter((c) => c.status === "Done").length,
      dismissed: calls.filter((c) => c.status === "Dismissed").length,
      all: calls.length,
    }),
    [calls],
  );

  const ordered = useMemo(() => {
    const needle = filters.q.trim().toLowerCase();
    const rows = calls.filter((c) => {
      if (filters.status !== "all") {
        const want = { planned: "Planned", done: "Done", dismissed: "Dismissed" }[filters.status];
        if (c.status !== want) return false;
      }
      if (filters.agencies.length && !filters.agencies.includes(c.agency ?? "")) return false;
      if (filters.due !== "any") {
        const { days } = dueLabel(c.response_deadline);
        if (days == null) return false;
        if (filters.due === "overdue" ? days >= 0 : days < 0 || days > Number(filters.due))
          return false;
      }
      if (needle) {
        const hay = [
          c.opportunity_title, c.agency, c.talking_point, c.poc_name, c.poc_email,
          ...(c.contacts ?? []).flatMap((p) => [p.name, p.email, p.company]),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    // Unresolved first within the current selection — a done call shouldn't sit above a
    // pursuit still waiting on a call.
    return [
      ...rows.filter((c) => c.status === "Planned"),
      ...rows.filter((c) => c.status !== "Planned"),
    ];
  }, [calls, filters]);

  return (
    <div className="callplan">
      <div className="cp-head">
        <h2>
          Call Plan{" "}
          <span className="c" title={`${active.length} still to call`}>
            {ordered.length}
          </span>
        </h2>
        <div className="cp-sub">
          Every pursuit that has been through capture, plus the calls the Analyst recommends —
          your consolidated call sheet.
        </div>
      </div>

      {calls.length > 0 && (
        <CallPlanFilters
          filters={filters}
          onChange={setFilters}
          agencyOptions={agencyOptions}
          counts={counts}
          onClear={() => setFilters(EMPTY_CALL_FILTERS)}
        />
      )}

      {loading ? (
        <div className="cp-empty">Loading…</div>
      ) : calls.length === 0 ? (
        <div className="cp-empty">
          Nothing to call on yet. A pursuit lands here once capture has run on it, or when the
          Analyst marks an opportunity <b>Bid</b> with a recommended outreach.
        </div>
      ) : ordered.length === 0 ? (
        <div className="cp-empty">
          No calls match these filters.{" "}
          <button className="sel-link" onClick={() => setFilters(EMPTY_CALL_FILTERS)}>
            Clear them
          </button>
        </div>
      ) : (
        <div className="cp-list">
          {ordered.map((c) => (
            <div
              className={`cp-card ${c.status !== "Planned" ? "muted" : ""}`}
              key={c.opportunity_id}
            >
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
                  {(c.contacts?.length ?? 0) > 0 && (
                    <button
                      className="mini-btn"
                      onClick={() => setPrepOpp(c.opportunity_id)}
                      title="Open the call prep — one brief per person"
                    >
                      Prep calls
                      <span className="cp-ct">{c.contacts!.length}</span>
                    </button>
                  )}
                  {/* Done/Dismiss act on the Analyst's call row. A pursuit that reached
                      capture without one has nothing to mark — it just preps calls. */}
                  {!c.call_id ? (
                    c.captured && <span className="cp-status done">Captured</span>
                  ) : c.status === "Planned" ? (
                    <>
                      <button
                        className="mini-btn secondary"
                        onClick={() => update(c.call_id!, "Done")}
                      >
                        Mark done
                      </button>
                      <button className="sel-link" onClick={() => update(c.call_id!, "Dismissed")}>
                        Dismiss
                      </button>
                    </>
                  ) : (
                    <>
                      <span className={`cp-status ${c.status.toLowerCase()}`}>{c.status}</span>
                      <button className="sel-link" onClick={() => update(c.call_id!, "Planned")}>
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

      {prepping && (
        <CallBriefDialog
          opportunityId={prepping.opportunity_id}
          title={prepping.opportunity_title ?? "Opportunity"}
          contacts={prepping.contacts ?? []}
          onClose={() => setPrepOpp(null)}
        />
      )}
    </div>
  );
}

function Detail({
  opp,
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
  onContactsChange,
}: {
  opp: Opportunity;
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
  onContactsChange: (contacts: RecommendedContact[]) => Promise<void>;
}) {
  const tab = useUiStore((s) => s.tab);
  const setTab = useUiStore((s) => s.setTab);
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
    // No count: the trail's length is not a to-do, and a number here would read as one.
    { key: "agent", label: "Agent" },
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
            onContactsChange={onContactsChange}
          />
        )}
        {tab === "documents" && <DocumentsTab opp={opp} />}
        {tab === "activity" && <ActivityTab opp={opp} tasks={tasks} calls={calls} />}
        {/* What the agents did to this opportunity and WHY — reasoning that was previously
            generated and thrown away. Mounted only when selected, so its 3s poll never runs
            behind another tab. */}
        {tab === "agent" && <AgentTrail subjectId={opp.id} />}
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
            <b>{(opp.documents ?? []).filter((d) => d.agent_id !== "manual_upload").length} documents</b>{" "}
            generated · {fmtDate(opp.captured_at)}
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
    ["Source", opp.source === "sam.gov" ? "SAM.gov" : opp.source === "manual" ? "Manual" : opp.source ?? "—"],
  ];
  return (
    <>
      {opp.analyst_rationale && (
        <>
          <div className="sec-title first">Analyst verdict</div>
          <p className="rationale">{opp.analyst_rationale}</p>
        </>
      )}
      {/* The same judgement the rationale explains in prose — as a meter plus each risk
          with its own reasoning, so it can be skimmed and sorted rather than read. */}
      <RiskMeter level={opp.risk_level} factors={opp.risk_factors} />
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
  onContactsChange,
}: {
  opp: Opportunity;
  outreachBusy: boolean;
  onRunOutreach: () => void;
  outreachOne: string | null;
  onRunOutreachOne: (email: string) => void;
  onContactsChange: (contacts: RecommendedContact[]) => Promise<void>;
}) {
  const relevant = opp.recommended_contacts ?? [];
  const searched = !!opp.contacts_searched_at;
  const drafts = opp.outreach_drafts ?? [];
  const drafted = !!opp.outreach_drafted_at;
  const emailable = relevant.filter((c) => c.email).length;

  // Manual add/remove. `onContactsChange` persists the whole list; the two helpers just
  // build the next list and hand it over.
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", email: "", company: "", title: "" });
  const removeContact = (idx: number) =>
    onContactsChange(relevant.filter((_, i) => i !== idx));
  const submitAdd = () => {
    const name = form.name.trim();
    if (!name) return;
    const added: RecommendedContact = {
      name,
      email: form.email.trim() || null,
      company: form.company.trim() || null,
      title: form.title.trim() || null,
      source: "manual",
    };
    void onContactsChange([...relevant, added]);
    setForm({ name: "", email: "", company: "", title: "" });
    setAdding(false);
  };

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
        <div style={{ display: "flex", gap: 8 }}>
          <button className="mini-btn" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "+ Add contact"}
          </button>
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
      </div>
      {adding && (
        <div className="rec add-contact">
          <div className="ac-grid">
            <input className="ac-in" placeholder="Name *" value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submitAdd()} autoFocus />
            <input className="ac-in" placeholder="Email" value={form.email}
              onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submitAdd()} />
            <input className="ac-in" placeholder="Company" value={form.company}
              onChange={(e) => setForm((f) => ({ ...f, company: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submitAdd()} />
            <input className="ac-in" placeholder="Title" value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && submitAdd()} />
          </div>
          <div style={{ marginTop: 8 }}>
            <button className="mini-btn" onClick={submitAdd} disabled={!form.name.trim()}>
              Add
            </button>
          </div>
        </div>
      )}
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
                      {c.source === "manual" && <span className="rec-manual">added</span>}
                    </div>
                    {c.title && (
                      <div style={{ fontSize: 12, color: "var(--faint)", marginTop: 2 }}>{c.title}</div>
                    )}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {c.relevance_score != null && (
                      <span className="pscore">
                        P<b>{c.relevance_score}</b>
                      </span>
                    )}
                    <button
                      className="rec-remove"
                      title="Remove from this opportunity"
                      aria-label={`Remove ${c.name}`}
                      onClick={() => removeContact(i)}
                    >
                      ✕
                    </button>
                  </div>
                </div>
                {/* No enrichment SUGGESTIONS here: on an opportunity a rep wants the finished
                    picture, not "is this right?" prompts. Confirming guesses lives in the
                    Contacts view (Contacts → To review). */}
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
                  <div className="rec-body" style={{ color: "var(--bid-ink)" }}>
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
  // Split user-uploaded source docs (manual ingestion) from agent-generated deliverables.
  const sourceDocs = docs.filter((d) => d.agent_id === "manual_upload");
  const generatedDocs = docs.filter((d) => d.agent_id !== "manual_upload");
  const sp = opp.sharepoint_folder ?? null;
  const [preview, setPreview] = useState<DocItem | null>(null);

  const renderDocList = (list: DocItem[]) => (
    <div className="card-list">
      {list.map((d, i) => (
        <button className="rec rec-doc" key={d.id ?? i} onClick={() => setPreview(d)} title="Preview">
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
                title={d.sharepoint_url ? "Open in SharePoint" : "Open the file"}
                onClick={async (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  try {
                    // Prefer the SharePoint copy when the file has been filed there; otherwise
                    // fall back to a freshly-minted iDrive link.
                    if (d.sharepoint_url) {
                      window.open(d.sharepoint_url, "_blank", "noopener");
                      return;
                    }
                    window.open(await getDocUrl(d.id), "_blank", "noopener");
                  } catch {
                    /* ignore — preview still works */
                  }
                }}
              >
                {d.sharepoint_url ? "Open in SharePoint ↗" : "Open ↗"}
              </a>
            </div>
          </div>
        </button>
      ))}
    </div>
  );

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
          {/* Documents the user uploaded (manual ingestion) are SOURCE material, not agent
              output — keep them in their own section so they aren't mislabeled "Generated". */}
          {sourceDocs.length > 0 && (
            <>
              <div className={`sec-title ${spCard ? "" : "first"}`}>Uploaded documents · preview</div>
              {renderDocList(sourceDocs)}
            </>
          )}
          {generatedDocs.length > 0 && (
            <>
              <div className={`sec-title ${spCard || sourceDocs.length > 0 ? "" : "first"}`}>
                Generated documents · preview
              </div>
              {renderDocList(generatedDocs)}
            </>
          )}
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
  const qc = useQueryClient();
  const [orgName, setOrgName] = useState("");
  const [uei, setUei] = useState("");
  // Held as the raw comma-separated string the admin types; split server-side on save.
  const [keywords, setKeywords] = useState("");
  const [savingOrg, setSavingOrg] = useState(false);
  // Its own flag, not savingOrg: the SAM.gov re-pull is the slow path (live fetch + site
  // scrape + the research agent) and should say so rather than hide behind "Saving…".
  const [refreshingSam, setRefreshingSam] = useState(false);
  const [orgMsg, setOrgMsg] = useState<string | null>(null);
  const [subTab, setSubTab] = useState<"settings" | "team">("settings");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"user" | "admin">("user");
  const [inviting, setInviting] = useState(false);
  const [inviteMsg, setInviteMsg] = useState<string | null>(null);

  // Cached: the panel's three reads (org + members + pending invites) as one query, so
  // re-opening Organisation is instant instead of re-fetching all three every time.
  const orgQ = useQuery(organizationQuery());
  const org = orgQ.data?.org ?? null;
  const members = orgQ.data?.members ?? [];
  const invites = orgQ.data?.invites ?? [];
  const loading = orgQ.isPending;

  // Mutations still say `load()`; it now invalidates the cache and lets the query refetch.
  const load = useCallback(
    () => qc.invalidateQueries({ queryKey: queryKeys.organization }),
    [qc],
  );

  // Seed the editable fields from the org — keyed on its id so a background refetch can
  // never overwrite what the user is currently typing.
  useEffect(() => {
    if (!org) return;
    setOrgName(org.name ?? "");
    setUei(org.uei ?? "");
    setKeywords((org.keywords ?? []).join(", "));
  }, [org?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Single action: save the name, UEI and keywords — and re-pull the SAM.gov profile ONLY
  // when the UEI itself changed.
  //
  // This used to run the lookup on every save where a UEI was merely present, which made
  // editing the keywords one of the slowest actions in the product: /me/uei-lookup busts
  // the Redis profile cache, re-fetches SAM.gov live, scrapes the company's website and
  // runs the company-research LLM agent. None of that can be affected by a keyword edit —
  // keywords are a ranking signal the agents read straight off the org document.
  const saveOrg = async () => {
    setSavingOrg(true);
    setErr(null);
    setOrgMsg(null);
    try {
      const updated = await organizationsApi.updateOrganization({
        name: orgName.trim(),
        uei: uei.trim(),
        keywords: keywords, // raw comma-separated; the server splits + de-dupes
      });
      let merged = updated;
      const nextUei = uei.trim().toUpperCase();
      const prevUei = (org?.uei ?? "").trim().toUpperCase();
      if (nextUei && nextUei !== prevUei) {
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
      // Write the saved org straight into the cache — no refetch needed, and members/invites
      // are untouched by this mutation.
      qc.setQueryData(queryKeys.organization, (prev: OrgBundle | undefined) =>
        prev ? { ...prev, org: merged } : prev,
      );
    } catch (e) {
      setErr(errText(e, "Couldn't save the organisation."));
    } finally {
      setSavingOrg(false);
    }
  };

  /** The SAM.gov re-pull, on purpose rather than as a side effect of every save. */
  const refreshSamProfile = async () => {
    setRefreshingSam(true);
    setErr(null);
    setOrgMsg(null);
    try {
      const details = await organizationsApi.lookupUei();
      qc.setQueryData(queryKeys.organization, (prev: OrgBundle | undefined) =>
        prev ? { ...prev, org: { ...prev.org, company_details: details } } : prev,
      );
      setOrgMsg("Company details re-pulled from SAM.gov.");
    } catch (e) {
      setErr(errText(e, "Couldn't reach SAM.gov."));
    } finally {
      setRefreshingSam(false);
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
                  Change it and we pull your registration (legal name, CAGE, NAICS, status) from
                  SAM.gov on save. Already correct? Use Refresh to re-pull it.
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={orgLabel}>Focus areas · what you&apos;re actively pursuing</label>
                <input
                  className="search"
                  value={keywords}
                  onChange={(e) => setKeywords(e.target.value)}
                  placeholder="DevSecOps, AI engineering, zero trust, cloud migration"
                />
                <div style={{ fontSize: 11.5, color: "var(--faint)" }}>
                  Comma-separated. Your SAM.gov registration says what you&apos;re <i>eligible</i> for;
                  this says what you&apos;re <i>good at</i>. The agents rank matching opportunities
                  higher — nothing is ever filtered out for not matching, and they match on meaning
                  (&ldquo;secure software factory&rdquo; counts as DevSecOps).
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
                <button
                  className="btn ghost"
                  onClick={refreshSamProfile}
                  disabled={savingOrg || refreshingSam || !uei.trim()}
                  title="Re-pull this company's SAM.gov registration and regenerate its profile"
                >
                  {refreshingSam ? (
                    <>
                      <span className="spin" /> Refreshing…
                    </>
                  ) : (
                    "Refresh from SAM.gov"
                  )}
                </button>
                {orgMsg && <span style={{ color: "var(--bid-ink)", fontSize: 13 }}>{orgMsg}</span>}
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
            {inviteMsg && <div style={{ color: "var(--bid-ink)", fontSize: 13, marginTop: 8 }}>{inviteMsg}</div>}

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
                          <div style={{ display: "flex", gap: 8 }}>
                            <button
                              className="mini-btn"
                              disabled={busy === id}
                              onClick={() => act(id, () => invitationsApi.resendInvitation(id))}
                            >
                              {busy === id ? "…" : "Resend"}
                            </button>
                            <button
                              className="mini-btn"
                              disabled={busy === id}
                              onClick={() => act(id, () => invitationsApi.revokeInvitation(id))}
                              style={{ color: "var(--nobid)" }}
                            >
                              {busy === id ? "…" : "Revoke"}
                            </button>
                          </div>
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
