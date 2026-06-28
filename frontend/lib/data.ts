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
  title: string;
  type: string;
  url: string;
  status: string;
  created_at?: string;
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
  priority_score?: number;
  analyst_rationale?: string;
  poc_name?: string;
  capture_approved?: boolean;
  captured_at?: string | null; // set when the Capture agent finishes its deliverables
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
}

// The pipeline board columns.
export const STAGES = ["Discover", "Qualify", "Capture", "Pursue", "Submitted"];

export async function fetchOpportunities(): Promise<Opportunity[]> {
  const { data } = await apiClient.get("/api/opportunities");
  return data.opportunities ?? [];
}

// Trigger an on-demand SAM.gov pull for this org (NAICS-filtered, still-open notices).
// Runs in the background (download + ingest + Analyst); the UI polls fetchOpportunities.
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

// ---- Composio / SharePoint connection (mirrors Outlook; provider-generic backend) ----
export async function getConnStatus(provider: string): Promise<OutlookStatus> {
  const { data } = await apiClient.get(`/api/composio/status?provider=${provider}`);
  return data;
}

export async function getSharePointStatus(): Promise<OutlookStatus> {
  return getConnStatus("sharepoint");
}

export async function connectSharePoint(callbackUrl: string): Promise<{ auth_url: string }> {
  const { data } = await apiClient.post("/api/composio/connect", {
    provider: "sharepoint",
    callback_url: callbackUrl,
  });
  return data;
}

export async function disconnectSharePoint(connectedAccountId: string): Promise<void> {
  await apiClient.post("/api/composio/disconnect", { connected_account_id: connectedAccountId });
}

// Kick off the SharePoint structure crawl (called after the user returns from OAuth).
export async function syncSharePointStructure(): Promise<void> {
  await apiClient.post("/api/composio/sharepoint/sync-structure");
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
