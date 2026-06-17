// Types + live API calls. No sample/fake data — everything comes from the backend.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type BidDecision = "Bid" | "No-Bid" | "Watch";

export interface DocItem {
  title: string;
  type: string;
  url: string;
  status: string;
}
export interface CallItem {
  name: string;
  talking_point: string;
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
  priority_score?: number;
  analyst_rationale?: string;
  poc_name?: string;
  calls?: CallItem[];
  documents?: DocItem[];
}

// The pipeline board columns.
export const STAGES = ["Discover", "Qualify", "Capture", "Pursue", "Submitted"];

export async function fetchOpportunities(): Promise<Opportunity[]> {
  const res = await fetch(`${API_BASE}/api/opportunities`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${res.status}`);
  const data = await res.json();
  return data.opportunities ?? [];
}

export async function uploadExcel(file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/api/ingestion/excel`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
}

export async function runAnalyst(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/opportunities/analyze/run`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Run analyst failed: ${res.status}`);
}
