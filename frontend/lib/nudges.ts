// The relationship engine's nudges — the drafted outreach behind a relationship_* Today
// card. Mirrors backend/routers/relationships.py (prefix /api/relationships); every route
// scopes to the JWT's organization, so no org id is sent from here.

import apiClient from "@/lib/api/client";

export type NudgeKind = "relationship_touch" | "relationship_personal";
export type NudgeStatus = "open" | "approved" | "dismissed" | "expired";

// One drafted outreach + the machine-assembled evidence behind it. The `reason` and cadence
// numbers come from arithmetic + stored facts, never the model — the card shows its evidence.
export interface RelationshipNudge {
  id: string;
  organization_id: string;
  owner_email: string;
  kind: NudgeKind;

  contact_email: string;
  contact_name?: string | null;
  contact_company?: string | null;

  subject: string;
  body: string;
  reason: string; // "warm (42 emails); quiet 47 days; you noted: golf."
  drew_on: string[]; // the stored facts the draft leaned on

  corr_count: number;
  days_since?: number | null;
  overdue_by?: number | null;

  // The last thing the two of them actually said — the evidence a rep checks before sending.
  last_subject?: string | null;
  last_contact?: string | null; // YYYY-MM-DD

  status: NudgeStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export async function fetchNudge(nudgeId: string): Promise<RelationshipNudge> {
  const { data } = await apiClient.get(`/api/relationships/nudges/${nudgeId}`);
  return data;
}

// Approve or dismiss a nudge. Terminal server-side (the sweep never reopens it), and it
// closes the paired Today card. NOTE: approve records the decision; the actual outbound
// send is a separate, explicit human action (see the dialog — it posts /api/mail/send).
export async function decideNudge(
  nudgeId: string,
  action: "approve" | "dismiss",
): Promise<{ decided: string }> {
  const { data } = await apiClient.post(`/api/relationships/nudges/${nudgeId}/decide`, {
    action,
  });
  return data;
}
