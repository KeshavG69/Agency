// Read surface for what the agents learned: contact facts + open suggestions, the agent
// trail on one record, and background-queue health. Mirrors backend/routers/intelligence.py
// (prefix /api/intelligence); every route scopes itself to the JWT's organization, so no
// org id is ever sent from here.
//
// Lives beside lib/data.ts rather than inside it: data.ts is the CRM surface (pipeline,
// documents, mail), this is the enrichment ledger. Both go through the same axios client,
// so auth and 401-refresh behave identically.

import apiClient from "@/lib/api/client";

// The closed vocabulary an agent may report — kept in step with backend/models/evidence.py.
// Typed as a union rather than `string` because the UI renders each kind differently
// (primary sources get a stronger badge than supporting ones), and a typo there would
// silently fall through to the default styling.
export type EvidenceKind =
  // PRIMARY — identifies THIS subject, so it can carry a fact on its own.
  | "samgov.entity-record"
  | "sam.poc-listed"
  | "outlook.thread-reply"
  | "gov-domain-rule"
  | "outlook.signature-block"
  | "pdl.domain-company"
  | "sharepoint.authored-doc"
  | "outlook.meeting-attend"
  | "company.own-website"
  // SUPPORTING — true, but consistent with many people, so never enough alone.
  | "web.cited-claim"
  | "outlook.address-book"
  | "handle.name-form"
  | "domain-derived-name"
  | "employer-only"
  // SPECIAL — records that two sources disagree.
  | "contradiction";

export interface FactEvidence {
  kind: EvidenceKind;
  detail: string; // written for a human: shown verbatim in the tooltip
  source_url?: string;
}

// The fields a fact may describe (backend FACT_FIELDS). Anything else is rejected server-side.
export type FactField =
  | "title"
  | "company"
  | "industry"
  | "phone"
  | "seniority"
  | "function"
  | "linkedin"
  | "website";

export type EvidenceBand = "VERIFIED" | "PROBABLE" | "POSSIBLE";
export type FactStatus = "APPLIED" | "PROPOSED" | "DISMISSED" | "SUPERSEDED";

// One claim about a contact, with the evidence that produced its score. A PROPOSED row is
// a *suggestion* — never render it as truth; it is waiting for a human to settle it.
export interface ContactFact {
  id: string;
  email: string;
  field: FactField;
  value: string;
  score: number;
  band: EvidenceBand | null; // null when the evidence was too weak to band
  rationale: string; // the sentence a rep reads: "Verified because …"
  evidence: FactEvidence[];
  status: FactStatus;
  created_at?: string;
  updated_at?: string;
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface ContactFactsResponse {
  email: string;
  // Settled facts, {field: value} — safe to render as truth.
  facts: Partial<Record<FactField, string>>;
  suggestions: ContactFact[];
}

export async function fetchContactFacts(email: string): Promise<ContactFactsResponse> {
  // The address is a path segment and can contain "+" and other reserved characters.
  const { data } = await apiClient.get(
    `/api/intelligence/contacts/${encodeURIComponent(email)}/facts`,
  );
  return data;
}

export interface SuggestionsPage {
  suggestions: ContactFact[];
  count: number; // rows in THIS page
  total: number; // open suggestions in the org
  offset: number;
  limit: number;
}

// The org-wide review queue, strongest first. Backend caps `limit` at 100.
export async function fetchSuggestions(
  p: { offset?: number; limit?: number } = {},
): Promise<SuggestionsPage> {
  const qs = new URLSearchParams({
    offset: String(p.offset ?? 0),
    limit: String(p.limit ?? 50),
  });
  const { data } = await apiClient.get(`/api/intelligence/suggestions?${qs.toString()}`);
  return data;
}

// The review queue grouped BY CONTACT: one entry per person + how many open suggestions they
// carry, strongest-suggestion first. The rep opens one contact and settles all of theirs.
export interface SuggestionContact {
  email: string;
  count: number;
}
export interface SuggestionContactsPage {
  contacts: SuggestionContact[];
  total: number | null; // distinct contacts to review; present only on the first page
  offset: number;
  limit: number;
}
export async function fetchSuggestionContacts(
  p: { offset?: number; limit?: number } = {},
): Promise<SuggestionContactsPage> {
  const qs = new URLSearchParams({
    offset: String(p.offset ?? 0),
    limit: String(p.limit ?? 50),
  });
  const { data } = await apiClient.get(
    `/api/intelligence/suggestions/contacts?${qs.toString()}`,
  );
  return data;
}

// THE human action: accept a suggestion (making it human-owned and unoverwritable) or
// dismiss it (never offered again). Both are enforced in the store, not here.
export async function decideFact(
  factId: string,
  accept: boolean,
): Promise<{ fact: ContactFact; decided: "accepted" | "dismissed" }> {
  const { data } = await apiClient.post(`/api/intelligence/facts/${factId}/decide`, { accept });
  return data;
}

// One step an agent took on one record. `ok: false` marks a rejection or a miss — a
// No-Bid, a contact search that found nobody — and carries the reason.
export interface AgentEvent {
  agent: string;
  subject: { type: string; id: string };
  step: string; // short verb phrase: "judged the opportunity"
  detail: string;
  tool?: string | null;
  ok: boolean;
  created_at: string;
}

// The trail for one subject (an opportunity id, a contact email…), oldest first.
export async function fetchAgentEvents(
  subjectId: string,
  limit = 200,
): Promise<{ subject_id: string; events: AgentEvent[] }> {
  const { data } = await apiClient.get(
    `/api/intelligence/events/${encodeURIComponent(subjectId)}?limit=${limit}`,
  );
  return data;
}

export interface QueueHealth {
  open: number;
  due_now: number;
  gave_up: number;
  open_by_kind: Record<string, number>;
  next_due: string | null;
}

export async function fetchQueueHealth(): Promise<QueueHealth> {
  const { data } = await apiClient.get("/api/intelligence/tasks/health");
  return data;
}
