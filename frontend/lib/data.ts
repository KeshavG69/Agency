// Types + live API calls. No sample/fake data — everything comes from the backend.
// All calls go through the shared axios client (apiClient), which injects the
// auth Bearer token and transparently refreshes it on 401. The only exception is
// the Excel upload (multipart), which uses fetch with a manual auth header.

import apiClient from "@/lib/api/client";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function authHeader(): Record<string, string> {
  const t = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export type BidDecision = "Bid" | "No-Bid" | "Watch";

export interface DocItem {
  id: string;
  title: string;
  type: string;
  url: string;
  status: string;
  created_at?: string;
  agent_id?: string; // "manual_upload" = user-uploaded source doc; "capture_agent" = generated
  sharepoint_url?: string | null; // set once the file is filed into the Bid's SharePoint folder
}
export interface CallItem {
  name: string;
  talking_point: string;
  status?: string;
  created_at?: string;
}
export interface TaskItem {
  name: string;
  description?: string;
  status?: string;
  due_date?: string | null;
  created_at?: string;
}
export interface RecommendedContact {
  name: string;
  email?: string | null;
  company?: string | null;
  title?: string | null;
  relevance_score?: number | null;
  reason?: string;
  suggested_outreach?: string;
  source?: string | null; // "manual" when a rep added the contact from the Contacts tab
}
// One outreach email the Mail Agent drafted — shaped for the artifact + send tool.
export interface OutreachDraft {
  to?: string | null;
  to_name?: string | null;
  cc?: string[];
  subject: string;
  body: string;
  is_html?: boolean;
  grounded_on?: string[];
}

// ---- Analyst risk assessment ----
// `blocker` is a HARD DISQUALIFIER (forces No-Bid). Everything else is a risk to weigh or a
// gate for the rep to confirm — never a reason to reject on its own.
export type RiskSeverity = "blocker" | "high" | "medium" | "low";
export type RiskLevel = "Low" | "Medium" | "High";
export type RiskFactorKind =
  | "capability"
  | "eligibility"
  | "competition"
  | "past_performance"
  | "scope_clarity"
  | "schedule"
  | "contract_type"
  | "teaming";
export interface RiskFactor {
  factor: RiskFactorKind;
  severity: RiskSeverity;
  note: string;
}

export interface Opportunity {
  id: string;
  title: string;
  agency?: string;
  naics?: string;
  set_aside?: string;
  estimated_value?: number | null;
  response_deadline?: string;
  stage: string;
  bid_decision?: BidDecision;
  decision_overridden?: boolean; // true when a human set the verdict manually
  assigned_to?: string[]; // member user-ids this opportunity is assigned to (empty = unassigned)
  priority_score?: number;
  analyst_rationale?: string;
  // Structured risk from the Analyst — the same judgement its rationale explains in prose,
  // kept as fields so the UI can show a meter and name each risk with its reasoning.
  risk_level?: RiskLevel | null;
  risk_factors?: RiskFactor[];
  poc_name?: string;
  capture_approved?: boolean;
  captured_at?: string | null; // set when the Capture agent finishes its deliverables
  capture_error?: string | null; // why a capture run terminally failed
  // Set when a capture run terminally failed. A first-class state — NOT captured_at — so a
  // dead run leaves "Processing" without masquerading as "Capture complete".
  capture_failed_at?: string | null;
  ingesting?: boolean; // manual upload -> parse -> digest -> Analyst pipeline still running
  ingest_error?: string | null; // set when the ingest pipeline terminally failed
  // raw opportunity fields surfaced in the detail view
  source?: string; // "sam.gov" | "excel" — where this opportunity was ingested from
  solicitation_number?: string;
  notice_id?: string;
  psc_code?: string;
  opp_type?: string;
  posted_date?: string;
  place_of_performance?: string;
  poc_email?: string;
  description?: string;
  link?: string | null;
  analyzed_at?: string;
  calls?: CallItem[];
  tasks?: TaskItem[];
  documents?: DocItem[];
  recommended_contacts?: RecommendedContact[]; // CRM agent's graph search results
  contacts_searched_at?: string | null;
  outreach_drafts?: OutreachDraft[]; // Mail agent's per-contact drafts
  outreach_drafted_at?: string | null;
  sharepoint_folder?: SharePointFolder | null; // Bid workspace auto-created in SharePoint
  sharepoint_folder_at?: string | null;
}

// Pointer to the per-Bid folder tree created in SharePoint (Shared Documents library).
export interface SharePointFolder {
  drive_id?: string;
  folder_id?: string;
  name?: string;
  web_url?: string;
  library?: string;
  subfolders?: Record<string, { id?: string; web_url?: string }>;
}

// One item currently living in a Bid folder (read LIVE from SharePoint).
export interface SharePointFile {
  subfolder: string;
  id?: string;
  name?: string;
  web_url?: string;
  edit_url?: string | null; // opens the file in Office-for-the-web edit mode (files only)
  size?: number;
  is_folder?: boolean;
  modified?: string | null;
}
export interface SharePointFilesResponse {
  connected: boolean;
  folder?: SharePointFolder | null;
  files: SharePointFile[];
  error?: string;
}

// The Documents tab's LIVE read of the Bid folder — a file a human drops into SharePoint
// shows up here automatically (the "read" half of two-way sync).
export async function fetchOpportunitySharePointFiles(id: string): Promise<SharePointFilesResponse> {
  const { data } = await apiClient.get(`/api/opportunities/${id}/sharepoint-files`);
  return data;
}

// One relevant incoming mail (from a known contact on an active Bid), surfaced on the
// Dashboard. DRAFT-ONLY: `suggested_reply` is text only until the user explicitly asks to
// create a real Outlook draft — nothing here ever gets sent by the app.
export interface MailTriageCard {
  id: string;
  organization_id: string;
  employee_email: string;
  opportunity_id: string;
  message_id: string;
  sender_email: string;
  sender_name?: string | null;
  subject: string;
  snippet: string;
  received_at?: string | null;
  conversation_id?: string | null;
  web_link?: string | null; // opens the original mail in Outlook
  status: "unread" | "read" | "dismissed" | "replied";
  suggested_reply?: string | null;
  reply_error?: string | null; // set if reply generation exhausted its retries
}

export async function fetchMailTriage(): Promise<{ cards: MailTriageCard[] }> {
  const { data } = await apiClient.get("/api/mail-triage");
  return data;
}

export async function markMailTriageRead(id: string): Promise<void> {
  await apiClient.post(`/api/mail-triage/${id}/read`);
}

export async function dismissMailTriage(id: string): Promise<void> {
  await apiClient.post(`/api/mail-triage/${id}/dismiss`);
}

export async function draftMailTriageReply(id: string): Promise<{ drafting_started: boolean; task_id: string }> {
  const { data } = await apiClient.post(`/api/mail-triage/${id}/draft-reply`);
  return data;
}

export async function createOutlookDraft(
  id: string,
  comment: string,
): Promise<{ created: boolean; web_link?: string | null }> {
  const { data } = await apiClient.post(`/api/mail-triage/${id}/create-outlook-draft`, { comment });
  return data;
}

// The pipeline board columns.
export const STAGES = ["Discover", "Qualify", "Capture", "Pursue", "Submitted"];

// REMOVED: fetchOpportunities() — it hit GET /api/opportunities, which returned the whole
// org enriched in one payload (~10 MB / 9.5 s on a large org). It had no callers left; the
// pipeline uses fetchOpportunityPage() and the detail pane fetches one record at a time.

// ---- Paginated pipeline (server-side filter/search/calendar + slim rows) ----
// List rows are SLIM: no documents/calls/tasks/recommended_contacts/outreach_drafts/
// analyst_rationale/description. Those come only from fetchOpportunity(id).
export interface PipelineParams {
  status?: string; // "all" | "Bid" | "Watch" | "No-Bid" | "captured" | "ingesting" | "processing" | "new"
  agencies?: string[];
  naics?: string[];
  setAsides?: string[];
  source?: string;
  value?: string; // "any" | "lt1m" | "1to10m" | "gt10m"
  due?: string; // "any" | "7" | "30" | "90"
  q?: string;
  postedDate?: string | null;
}

function pipelineQuery(p: PipelineParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (p.status && p.status !== "all") qs.set("status", p.status);
  (p.agencies ?? []).forEach((a) => qs.append("agency", a));
  (p.naics ?? []).forEach((n) => qs.append("naics", n));
  (p.setAsides ?? []).forEach((s) => qs.append("set_aside", s));
  if (p.source) qs.set("source", p.source);
  if (p.value && p.value !== "any") qs.set("value", p.value);
  if (p.due && p.due !== "any") qs.set("due", p.due);
  if (p.q && p.q.trim()) qs.set("q", p.q.trim());
  if (p.postedDate) qs.set("posted_date", p.postedDate);
  return qs;
}

export interface OpportunityPage {
  items: Opportunity[];
  total: number;
  offset: number;
  limit: number;
  // Present unless the caller asked for withCounts: false — see fetchOpportunityPage.
  counts?: Record<string, number>;
  in_flight?: number;
}
export async function fetchOpportunityPage(
  p: PipelineParams & { offset?: number; limit?: number; withCounts?: boolean },
): Promise<OpportunityPage> {
  const qs = pipelineQuery(p);
  qs.set("offset", String(p.offset ?? 0));
  qs.set("limit", String(p.limit ?? 50));
  // The status pill counts now ride along with the page — rows, total and counts are
  // fetched concurrently server-side, so one request replaces two. Pass false where the
  // caller does not render the pills (e.g. the Bid sidebar).
  if (p.withCounts === false) qs.set("with_counts", "false");
  const { data } = await apiClient.get(`/api/opportunities/page?${qs.toString()}`);
  return data;
}

export async function fetchOpportunityCounts(
  p: PipelineParams,
): Promise<{ counts: Record<string, number>; in_flight: number }> {
  const { data } = await apiClient.get(`/api/opportunities/counts?${pipelineQuery(p).toString()}`);
  return data;
}

export async function fetchFacets(): Promise<{ agencies: string[]; naics: string[]; set_asides: string[] }> {
  const { data } = await apiClient.get("/api/opportunities/facets");
  return data;
}

export async function fetchPostedDates(p: PipelineParams): Promise<string[]> {
  const { data } = await apiClient.get(`/api/opportunities/posted-dates?${pipelineQuery(p).toString()}`);
  return data.dates ?? [];
}

// The FULL enriched opportunity (documents/calls/tasks + heavy fields) for the detail pane.
export async function fetchOpportunity(id: string): Promise<Opportunity> {
  const { data } = await apiClient.get(`/api/opportunities/${id}`);
  return data;
}

// The org's Bid set (few) for the left sidebar + dashboard — independent of the paged list.
//
// Pages through rather than asking for one huge response. The API caps a page at 100, and
// the previous single `limit: 500` call had two problems: it would now be rejected, and it
// SILENTLY TRUNCATED at 500 — an org with more active pursuits than that simply lost the
// rest from its sidebar with no error anywhere. Looping is both correct and bounded: it
// stops as soon as a short page comes back.
export async function fetchBids(): Promise<Opportunity[]> {
  const PAGE_SIZE = 100;
  const MAX_PAGES = 20; // 2,000 active bids is far beyond real; a guard against a bad total
  const all: Opportunity[] = [];

  for (let i = 0; i < MAX_PAGES; i++) {
    const page = await fetchOpportunityPage({
      status: "Bid",
      offset: all.length,
      limit: PAGE_SIZE,
      // The sidebar never renders the status pills, so skip computing them per page.
      withCounts: false,
    });
    all.push(...page.items);
    if (page.items.length < PAGE_SIZE || all.length >= page.total) break;
  }
  return all;
}

// Trigger an on-demand SAM.gov pull for this org (NAICS-filtered, still-open notices).
// Runs in the background (download + ingest + Analyst); the UI polls fetchOpportunityPage.
export interface SamScanResult {
  scan_started?: boolean;
  task_id?: string;
  organization_id?: string;
}
export async function pullFromSam(lookbackDays = 1): Promise<SamScanResult> {
  const { data } = await apiClient.post(`/api/ingestion/sam/scan?lookback_days=${lookbackDays}`);
  return data;
}

export async function uploadExcel(file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  // Multipart — let the browser set the boundary; just add the auth header.
  const res = await fetch(`${API_BASE}/api/ingestion/excel`, {
    method: "POST",
    headers: authHeader(),
    body: form,
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
}

export interface ManualOpportunityResult {
  opportunity_id: string;
  created: boolean;
  files: number;
  processing: boolean;
}

// Manually add an opportunity: title + solicitation number + description + files.
// Multipart (files) → raw fetch + auth header, like uploadExcel. The backend uploads
// the files, parses + digests them (small model), and runs the Analyst in the background.
export async function createManualOpportunity(
  fields: { title: string; number?: string; description?: string },
  files: File[],
): Promise<ManualOpportunityResult> {
  const form = new FormData();
  form.append("title", fields.title);
  if (fields.number) form.append("number", fields.number);
  if (fields.description) form.append("description", fields.description);
  for (const f of files) form.append("files", f); // repeated key → FastAPI list[UploadFile]
  const res = await fetch(`${API_BASE}/api/opportunities/manual`, {
    method: "POST",
    headers: authHeader(),
    body: form,
  });
  if (!res.ok) throw new Error(`Add opportunity failed: ${res.status}`);
  return res.json();
}

// ---- Contact knowledge graph ----
export interface GraphNode {
  id: string;
  label: string;
  type: "Person" | "Company";
  email?: string;
  title?: string | null;
  company?: string | null;
  external?: boolean;
  enriched?: boolean;
  weight?: number;
}
export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}
export interface ContactGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export async function fetchContactGraph(): Promise<ContactGraph> {
  const { data } = await apiClient.get("/api/contacts/graph");
  return data;
}

// ---- Contacts LIST (the graph is too heavy at ~3k nodes) ----
export interface ContactRow {
  name: string;
  email?: string | null;
  company?: string | null;
  title?: string | null;
  corr_count?: number;
  industry?: string | null;
  last_contact?: string | null;
}
// `total` is present only on the first page (offset 0); the client keeps it and pages the rest.
export interface ContactsPage {
  items: ContactRow[];
  total: number | null;
}
export async function fetchContactsPage(
  offset: number,
  limit: number,
  q: string,
): Promise<ContactsPage> {
  const { data } = await apiClient.get("/api/contacts", { params: { offset, limit, q } });
  return data;
}

// ---- SharePoint document structure graph ----
export interface SPNode {
  id: string;
  type: "site" | "library" | "folder" | "file" | "list";
  name: string;
  path?: string;
  ext?: string | null;
  web_url?: string | null;
  item_count?: number | null;
}
export interface SPGraph {
  nodes: SPNode[];
  edges: GraphEdge[];
  stats?: { sites: number; libraries: number; folders: number; files: number };
}

export async function fetchSharePointGraph(): Promise<SPGraph> {
  const { data } = await apiClient.get("/api/sharepoint/graph");
  return data;
}

// ---- Composio / Outlook connection ----
export interface OutlookStatus {
  connected: boolean;
  status: string | null;
  connected_account_id: string | null;
}

export async function getOutlookStatus(): Promise<OutlookStatus> {
  const { data } = await apiClient.get("/api/composio/status?provider=outlook");
  return data;
}

// Start OAuth — returns the Microsoft consent URL to send the user to.
export async function connectOutlook(callbackUrl: string): Promise<{ auth_url: string }> {
  const { data } = await apiClient.post("/api/composio/connect", {
    provider: "outlook",
    callback_url: callbackUrl,
  });
  return data;
}

// Disconnect a connected account so the user can connect a different one.
export async function disconnectOutlook(connectedAccountId: string): Promise<void> {
  await apiClient.post("/api/composio/disconnect", { connected_account_id: connectedAccountId });
}

// Kick off the contacts download (called after the user returns from OAuth).
export async function syncOutlookContacts(): Promise<void> {
  await apiClient.post("/api/composio/outlook/sync-contacts");
}

// ---- Outlook contact review (pick which contacts to ingest) ----
export interface ContactCandidate {
  email: string;
  name?: string | null;
  company?: string | null;
  title?: string | null;
  count?: number;
  last_seen?: string | null;
  domain?: string;
  external?: boolean;
  category: "work" | "personal";
}
export interface ContactPreview {
  contacts: ContactCandidate[];
  count: number;
  work: number;
  personal: number;
}

// Fetch the candidate contacts (classified work/personal) WITHOUT graphing them —
// powers the review dialog. Synchronous on the backend; can take a few seconds.
export async function previewOutlookContacts(): Promise<ContactPreview> {
  const { data } = await apiClient.get("/api/composio/outlook/contacts/preview");
  return data;
}

// Enrich + graph ONLY the contacts the user ticked in the review dialog.
export async function ingestOutlookContacts(
  contacts: ContactCandidate[],
): Promise<{ selected: number; task_id: string }> {
  const { data } = await apiClient.post("/api/composio/outlook/contacts/ingest", { contacts });
  return data;
}

// ---- Composio / SharePoint connection (mirrors Outlook; provider-generic backend) ----
export async function getConnStatus(provider: string): Promise<OutlookStatus> {
  const { data } = await apiClient.get(`/api/composio/status?provider=${provider}`);
  return data;
}

export async function getSharePointStatus(): Promise<OutlookStatus> {
  return getConnStatus("sharepoint");
}

// SharePoint is TWO chained Composio connections under one "Connect Library" click:
// Graph (structure/ACL/write) first, then REST (exact site-group member emails — Graph
// can't resolve those). connectSharePoint starts stage 1; connectSharePointRest starts
// stage 2 once stage 1 is ACTIVE (see the oauth-callback page, which drives the chain).
export async function connectSharePoint(callbackUrl: string): Promise<{ auth_url: string }> {
  const { data } = await apiClient.post("/api/composio/connect", {
    provider: "sharepoint",
    callback_url: callbackUrl,
  });
  return data;
}

export async function connectSharePointRest(callbackUrl: string): Promise<{ auth_url: string }> {
  const { data } = await apiClient.post("/api/composio/connect", {
    provider: "sharepoint_rest",
    callback_url: callbackUrl,
  });
  return data;
}

// Disconnects BOTH SharePoint connections (Graph + REST) in one action. `failed` lists any
// stage whose delete call itself errored (still connected) — the caller should surface that
// rather than assuming full success.
export async function disconnectSharePoint(): Promise<{
  disconnected: string[];
  failed: string[];
  library_cleared: boolean;
}> {
  const { data } = await apiClient.post("/api/composio/sharepoint/disconnect");
  return data;
}

// Kick off the SharePoint structure crawl (called after the user returns from OAuth).
export async function syncSharePointStructure(): Promise<void> {
  await apiClient.post("/api/composio/sharepoint/sync-structure");
}

// ---- SharePoint folder picker (which folders to actually ingest) ----
// Ingestion is opt-OUT: `excluded_paths` is the org's saved exclusion list — everything
// NOT in it is ingested. A cheap shallow browse (sites -> libraries -> top-level folders,
// no ACL) powers the checkbox tree; the real crawl (syncSharePointStructure) applies it.
export interface SPBrowseNode {
  id: string;
  type: "site" | "library" | "folder" | "file";
  name: string;
  path: string;
  parent_id: string | null;
}
export interface SPBrowseResponse {
  nodes: SPBrowseNode[];
  excluded_paths: string[];
}

export async function browseSharePointFolders(): Promise<SPBrowseResponse> {
  const { data } = await apiClient.get("/api/composio/sharepoint/browse-folders");
  return data;
}

export async function saveSharePointExcludedFolders(excludedPaths: string[]): Promise<void> {
  await apiClient.post("/api/composio/sharepoint/excluded-folders", { excluded_paths: excludedPaths });
}

export async function runAnalyst(): Promise<void> {
  await apiClient.post("/api/opportunities/analyze/run");
}

// Analyze only the opportunities the user hand-picked from the SAM.gov pull.
export async function analyzeSelected(ids: string[]): Promise<{ started: number }> {
  const { data } = await apiClient.post("/api/opportunities/analyze/selected", { ids });
  return data;
}

// Human override of the Analyst verdict — flip Bid / Watch / No-Bid.
export async function setDecision(id: string, decision: BidDecision): Promise<void> {
  await apiClient.post(`/api/opportunities/${id}/decision`, { decision });
}

// Assign an opportunity to members (by user id). Admin only. Empty list = unassign.
export async function assignOpportunity(id: string, userIds: string[]): Promise<void> {
  await apiClient.post(`/api/opportunities/${id}/assign`, { user_ids: userIds });
}

// Replace an opportunity's contact list (manual add/remove from the Contacts tab). The UI
// sends the whole list it wants to keep, so add and remove are the same call. Returns the
// stored list echoed back by the server.
export async function updateOpportunityContacts(
  id: string,
  contacts: RecommendedContact[],
): Promise<RecommendedContact[]> {
  const { data } = await apiClient.put(`/api/opportunities/${id}/contacts`, { contacts });
  return data.recommended_contacts ?? contacts;
}

// Mint a FRESH presigned URL for a generated document (the stored one expires).
export async function getDocUrl(documentId: string): Promise<string> {
  const { data } = await apiClient.get(`/api/documents/${documentId}/url`);
  return data.url;
}

// ---- Call plan (consolidated BD call sheet across the pipeline) ----
export interface CallPlanItem {
  // null for a pursuit that reached capture but has no Analyst call row — it still appears
  // (and preps calls), it just has nothing to mark Done/Dismiss.
  call_id: string | null;
  captured?: boolean;
  opportunity_id: string;
  opportunity_title?: string;
  agency?: string;
  priority_score?: number;
  bid_decision?: BidDecision;
  response_deadline?: string;
  poc_name?: string;
  poc_email?: string;
  name?: string;
  talking_point?: string;
  status: string; // "Planned" | "Done" | "Dismissed"
  created_at?: string;
  contacts?: CallContact[]; // everyone worth calling — the dialog's per-person tabs
}

export async function fetchCallPlan(): Promise<CallPlanItem[]> {
  const { data } = await apiClient.get("/api/calls/plan");
  return data.calls ?? [];
}

export async function setCallStatus(callId: string, status: string): Promise<void> {
  await apiClient.post(`/api/calls/${callId}/status`, { status });
}

// ---- Per-contact call briefs ("how do I talk to THIS person?") ----
// The Call Plan dialog has one tab per contact on a pursuit; each tab is its own brief, run
// on demand when the rep opens it. Every brief is grounded in that contact's WHOLE ORG — the
// agent reads every thread in the rep's mailbox with anyone at their email domain.
export interface CallContact {
  name?: string | null;
  email: string;
  title?: string | null;
  company?: string | null;
  source?: string; // "poc" | "recommended" | "manual"
}
// No contact name/email here — the caller already knows who the brief is for (the tab they
// clicked), and the stored doc carries `contact_email`.
export interface CallBriefBody {
  org_name: string;
  summary: string;
  relationship?: string | null;
  org_context?: string | null;
  approach: string; // THE line: how to talk to this person
  talking_points: string[];
  open_threads: string[];
  suggested_ask: string;
}
export interface CallBriefDoc {
  opportunity_id: string;
  contact_email: string;
  org_domain: string;
  brief: CallBriefBody;
  mail_count?: number;
  refreshed_at?: string;
}
export interface CallBriefsResponse {
  opportunity_id: string;
  briefs: CallBriefDoc[];
  pending: string[]; // contact emails currently being prepared in the background
}

// Kick off the brief for ONE contact (runs in the background). `queued` is false when one is
// already being prepared — either way a brief is on its way.
export async function prepCall(
  opportunityId: string,
  contactEmail: string,
): Promise<{ opportunity_id: string; contact_email: string; queued: boolean; pending: boolean }> {
  const { data } = await apiClient.post("/api/calls/brief", {
    opportunity_id: opportunityId,
    contact_email: contactEmail,
  });
  return data;
}

// Every contact-brief for one pursuit + which are still being prepared — one payload for the
// whole dialog, so the tabs don't each poll separately.
export async function fetchCallBriefs(opportunityId: string): Promise<CallBriefsResponse> {
  const { data } = await apiClient.get(
    `/api/calls/brief/${encodeURIComponent(opportunityId)}`,
  );
  return data;
}

// ---- Outreach collision ("someone's already talking to this contact") ----
export interface CollisionItem {
  employee_email: string;
  action: string; // "drafted" | "sent"
  opportunity_title?: string | null;
  created_at?: string;
}

// For each contact email, which OTHER teammates have drafted/sent to them.
export async function fetchCollisions(
  emails: string[],
): Promise<Record<string, CollisionItem[]>> {
  if (emails.length === 0) return {};
  const { data } = await apiClient.post("/api/mail/collisions", { emails });
  return data.collisions ?? {};
}

// Approve ONE opportunity for capture — immediately runs the capture agents on it.
export async function approveCapture(id: string): Promise<void> {
  await apiClient.post(`/api/opportunities/${id}/approve-capture`);
}

// Run the Mail Agent over an opportunity's recommended contacts (one draft each).
export async function runOutreach(id: string): Promise<void> {
  await apiClient.post(`/api/opportunities/${id}/outreach`);
}

// Regenerate the outreach draft for ONE contact (by email) on an opportunity.
export async function runOutreachOne(id: string, email: string): Promise<void> {
  await apiClient.post(`/api/opportunities/${id}/outreach/one`, { email });
}

// Send ONE drafted email via Outlook — the human-approved 'Send' click.
export async function sendMail(draft: OutreachDraft): Promise<void> {
  try {
    await apiClient.post("/api/mail/send", draft);
  } catch (e: any) {
    throw new Error(e?.response?.data?.detail || `Send failed: ${e?.response?.status ?? ""}`);
  }
}
