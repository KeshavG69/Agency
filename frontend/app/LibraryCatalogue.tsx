"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteLibraryDocument,
  fixLibraryCategory,
  libraryDocumentUrl,
  rerunLibraryAnalysis,
  uploadLibraryDocuments,
  type LibraryCategory,
  type LibraryCounts,
  type LibraryDocument,
} from "@/lib/data";
import { libraryQuery, queryKeys } from "@/lib/queries";
import { useToastStore } from "@/lib/stores/toastStore";

export const CATEGORY_LABEL: Record<LibraryCategory, string> = {
  active_contract: "Active contract",
  past_performance: "Past performance",
  cpars: "CPARS / rating",
  resume: "Resume",
  past_proposal: "Past proposal",
  capture_plan: "Capture plan",
  capability_statement: "Capability statement",
  technical_approach: "Technical approach",
  management_plan: "Management / plans",
  pricing: "Pricing",
  certification: "Certification",
  contract_legal: "Contract / legal",
  solicitation: "Solicitation",
  other: "Other",
};
const CATEGORIES = Object.keys(CATEGORY_LABEL) as LibraryCategory[];

// "review" and "problems" are filters over status, not categories — they sit in the same row
// because they answer the same question: which documents should I look at?
type Filter = "all" | "review" | "problems" | LibraryCategory;

// The three libraries: contracts we hold, what we've done before, and what we're producing now.
const SHELVES = [
  { key: "active", label: "Active contracts" },
  { key: "past", label: "Past work" },
  { key: "output", label: "Current bids" },
] as const;
type Shelf = "all" | (typeof SHELVES)[number]["key"];

const shelfOf = (d: LibraryDocument): Shelf =>
  d.shelf === "output" ? "output" : d.category === "active_contract" ? "active" : "past";

const STATUS_LABEL: Record<LibraryDocument["status"], string> = {
  pending: "Waiting",
  analysing: "Reading…",
  done: "Done",
  needs_review: "Needs review",
  failed: "Couldn't read",
  skipped: "Not read",
};

const money = (n?: number | null) =>
  n == null
    ? null
    : n >= 1e6
      ? `$${+(n / 1e6).toFixed(1)}M` // $12.4M, $3M — never round a contract value to a lie
      : n >= 1e3
        ? `$${Math.round(n / 1e3)}K`
        : `$${n}`;

/** One fact with the words it was read from, shown on hover and underneath. */
function Fact({ label, value, quote }: { label: string; value?: string | number | null; quote?: string }) {
  if (value == null || value === "") return null;
  return (
    <div className="lib-fact">
      <span className="lib-fact-k">{label}</span>
      <span className="lib-fact-v">{value}</span>
      {quote && <span className="lib-fact-q">“{quote}”</span>}
    </div>
  );
}

function List({ label, items }: { label: string; items?: string[] }) {
  if (!items?.length) return null;
  return <Fact label={label} value={items.join(", ")} />;
}

function Detail({ doc, onClose }: { doc: LibraryDocument; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.push);
  type LibraryData = { documents: LibraryDocument[]; counts: LibraryCounts };
  const fix = useMutation({
    mutationFn: (c: LibraryCategory) => fixLibraryCategory(doc.item_id, c),
    // Show the choice at once: without this the select snapped back to the old category for
    // the ~2s round trip, which reads as "it didn't take".
    onMutate: async (c) => {
      await qc.cancelQueries({ queryKey: queryKeys.library });
      const previous = qc.getQueryData<LibraryData>(queryKeys.library);
      qc.setQueryData<LibraryData>(queryKeys.library, (old) =>
        old && {
          ...old,
          documents: old.documents.map((d) =>
            d.item_id === doc.item_id ? { ...d, category: c, category_override: c, status: "pending" } : d,
          ),
        },
      );
      return { previous };
    },
    onSuccess: () => toast("Category saved — re-reading the file for matching facts.", "success"),
    onError: (_e, _c, ctx) => {
      if (ctx?.previous) qc.setQueryData(queryKeys.library, ctx.previous);
      toast("Couldn't save the category — try again.");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: queryKeys.library }),
  });
  const remove = useMutation({
    mutationFn: () => deleteLibraryDocument(doc.item_id),
    onSuccess: () => {
      onClose();
      qc.invalidateQueries({ queryKey: queryKeys.library });
    },
    onError: () => toast("Couldn't remove it — try again."),
  });
  const readable = doc.status !== "skipped";
  const ev = (o: { evidence?: Record<string, string> }, k: string) => o.evidence?.[k];

  return (
    <aside className="lib-detail">
      <div className="lib-detail-head">
        <div>
          <div className="lib-detail-name">{doc.name}</div>
          <div className="lib-detail-path mono">{doc.path}</div>
        </div>
        <button className="mini-btn" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      {doc.web_url && (
        <a className="lib-open" href={doc.web_url} target="_blank" rel="noreferrer">
          Open in SharePoint ↗
        </a>
      )}
      {doc.source === "upload" && (
        <div className="lib-upload-actions">
          <button
            className="lib-open"
            onClick={async () => {
              try {
                window.open(await libraryDocumentUrl(doc.item_id), "_blank", "noopener");
              } catch {
                toast("Couldn't open that file.");
              }
            }}
          >
            Open file ↗
          </button>
          <button className="sel-link" disabled={remove.isPending} onClick={() => remove.mutate()}>
            Remove
          </button>
        </div>
      )}

      {readable && (
        <label className="lib-cat-fix">
          <span>Category</span>
          <select
            value={doc.category ?? ""}
            disabled={fix.isPending || doc.status === "pending" || doc.status === "analysing"}
            onChange={(e) => fix.mutate(e.target.value as LibraryCategory)}
          >
            {!doc.category && <option value="">—</option>}
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABEL[c]}
              </option>
            ))}
          </select>
          <span className="lib-cat-note">
            {doc.corrected_by
              ? `Set by ${doc.corrected_by}`
              : doc.confidence != null
                ? `${Math.round(doc.confidence * 100)}% sure · wrong? change it`
                : ""}
          </span>
        </label>
      )}

      <div className={`lib-status s-${doc.status}`}>{STATUS_LABEL[doc.status]}</div>
      {doc.skip_reason && <p className="lib-muted">{doc.skip_reason}</p>}
      {doc.error && <p className="lib-muted">{doc.error}</p>}
      {doc.summary && <p className="lib-summary">{doc.summary}</p>}

      {doc.contracts?.map((c, i) => (
        <section key={`c${i}`} className="lib-sheet">
          <h4>Contract{doc.contracts!.length > 1 ? ` ${i + 1}` : ""}</h4>
          <Fact label="Title" value={c.title} quote={ev(c, "title")} />
          <Fact label="Customer" value={c.customer} quote={ev(c, "customer")} />
          <Fact label="Agency" value={c.agency} quote={ev(c, "agency")} />
          <Fact label="Contract #" value={c.contract_number} quote={ev(c, "contract_number")} />
          <Fact label="Role" value={c.role} quote={ev(c, "role")} />
          <Fact label="Value" value={money(c.value_usd)} quote={ev(c, "value_usd")} />
          <Fact label="Start" value={c.start_date} quote={ev(c, "start_date")} />
          <Fact label="End" value={c.end_date} quote={ev(c, "end_date")} />
          <Fact label="NAICS" value={c.naics} quote={ev(c, "naics")} />
          <Fact label="Rating" value={c.rating} quote={ev(c, "rating")} />
          <Fact label="Scope" value={c.scope} quote={ev(c, "scope")} />
          <Fact label="Customer POC" value={c.customer_poc} quote={ev(c, "customer_poc")} />
        </section>
      ))}
      {doc.people?.map((p, i) => (
        <section key={`p${i}`} className="lib-sheet">
          <h4>{p.name || `Person ${i + 1}`}</h4>
          <Fact label="Role" value={p.current_role} quote={ev(p, "current_role")} />
          <Fact label="Experience" value={p.years_experience != null ? `${p.years_experience} yrs` : null} quote={ev(p, "years_experience")} />
          <Fact label="Clearance" value={p.clearance} quote={ev(p, "clearance")} />
          <Fact label="Education" value={p.education} quote={ev(p, "education")} />
          <List label="Certifications" items={p.certifications} />
          <List label="Skills" items={p.skills} />
        </section>
      ))}
      {doc.proposal && (
        <section className="lib-sheet">
          <h4>Proposal</h4>
          <Fact label="Agency" value={doc.proposal.agency} quote={ev(doc.proposal, "agency")} />
          <Fact label="Solicitation #" value={doc.proposal.solicitation_number} quote={ev(doc.proposal, "solicitation_number")} />
          <Fact label="Title" value={doc.proposal.title} quote={ev(doc.proposal, "title")} />
          <Fact label="Volume" value={doc.proposal.volume} quote={ev(doc.proposal, "volume")} />
          <Fact label="Submitted" value={doc.proposal.submitted_date} quote={ev(doc.proposal, "submitted_date")} />
          <Fact label="Outcome" value={doc.proposal.outcome} quote={ev(doc.proposal, "outcome")} />
        </section>
      )}
      {doc.certifications?.map((c, i) => (
        <section key={`z${i}`} className="lib-sheet">
          <h4>{c.name || "Certification"}</h4>
          <Fact label="Issuer" value={c.issuer} quote={ev(c, "issuer")} />
          <Fact label="Number" value={c.number} quote={ev(c, "number")} />
          <Fact label="Expires" value={c.expires} quote={ev(c, "expires")} />
        </section>
      ))}
      {doc.legal && (
        <section className="lib-sheet">
          <h4>{doc.legal.kind || "Agreement"}</h4>
          <List label="Parties" items={doc.legal.parties} />
          <Fact label="Effective" value={doc.legal.effective_date} quote={ev(doc.legal, "effective_date")} />
          <Fact label="Expires" value={doc.legal.expires} quote={ev(doc.legal, "expires")} />
        </section>
      )}
    </aside>
  );
}

export default function LibraryCatalogue({ isAdmin }: { isAdmin?: boolean }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.push);
  const q = useQuery(libraryQuery());
  const fileRef = useRef<HTMLInputElement | null>(null);
  const upload = useMutation({
    mutationFn: uploadLibraryDocuments,
    onSuccess: () => {
      toast("Uploaded — reading your documents now.", "success");
      qc.invalidateQueries({ queryKey: queryKeys.library });
    },
    onError: () => toast("Couldn't upload those files — try again."),
  });
  const uploadControl = (
    <>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const picked = Array.from(e.target.files ?? []);
          if (picked.length) upload.mutate(picked);
          e.target.value = "";
        }}
      />
      <button className="mini-btn" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
        {upload.isPending ? "Uploading…" : "Upload documents"}
      </button>
    </>
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [shelf, setShelf] = useState<Shelf>("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rerun = useMutation({
    mutationFn: rerunLibraryAnalysis,
    onSuccess: (r) => {
      toast(r.retried_failed ? `Retrying ${r.retried_failed} file(s).` : "Reading any waiting files.", "info");
      qc.invalidateQueries({ queryKey: queryKeys.library });
    },
    onError: () => toast("Couldn't start the analysis — try again."),
  });

  const docs = useMemo(() => q.data?.documents ?? [], [q.data]);
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return docs.filter((d) => {
      if (shelf !== "all" && shelfOf(d) !== shelf) return false;
      if (filter === "review" && d.status !== "needs_review") return false;
      if (filter === "problems" && d.status !== "failed") return false;
      if (filter !== "all" && filter !== "review" && filter !== "problems") {
        if (d.category !== filter || (d.status !== "done" && d.status !== "needs_review")) return false;
      }
      if (!needle) return true;
      return [d.name, d.path, d.summary].some((f) => (f || "").toLowerCase().includes(needle));
    });
  }, [docs, shelf, filter, search]);
  const selected = selectedId ? docs.find((d) => d.item_id === selectedId) ?? null : null;

  if (q.isError && !q.data) return <div className="graph-empty">Couldn&apos;t load the library analysis.</div>;
  if (!q.data) return <div className="graph-empty">Loading the library…</div>;

  const st = q.data.counts.status;
  const readable = docs.length - (st.skipped ?? 0);
  const read = (st.done ?? 0) + (st.needs_review ?? 0);
  const inFlight = (st.pending ?? 0) + (st.analysing ?? 0);

  if (docs.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">Add your company documents</div>
        Upload active contracts, past proposals, past performance and resumes. They&apos;re read and sorted
        automatically, and the writers use them to draft your proposals.
        {uploadControl}
      </div>
    );

  const chip = (key: Filter, label: string, n?: number) =>
    (n ?? 1) > 0 || filter === key ? (
      <button key={key} className={`lib-chip ${filter === key ? "on" : ""}`} onClick={() => setFilter(key)}>
        {label}
        {n != null && <span className="c">{n}</span>}
      </button>
    ) : null;

  return (
    <div className="lib-wrap">
      <div className="view-toolbar">
        <input
          className="view-search"
          placeholder="Search name, folder, summary…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="view-count">
          {read} of {readable} documents read
          {inFlight > 0 && <> · reading {inFlight} now</>}
          {(st.failed ?? 0) > 0 && <> · {st.failed} couldn&apos;t be read</>}
        </div>
        {uploadControl}
        {isAdmin && (st.failed ?? 0) + (st.pending ?? 0) > 0 && (
          <button className="mini-btn" disabled={rerun.isPending} onClick={() => rerun.mutate()}>
            Retry
          </button>
        )}
      </div>
      <div className="lib-shelves" role="tablist" aria-label="Library">
        {[{ key: "all", label: "Everything" } as const, ...SHELVES].map((s) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={shelf === s.key}
            className={`lib-shelf ${shelf === s.key ? "on" : ""}`}
            onClick={() => setShelf(s.key)}
          >
            {s.label}
            <span className="c">{s.key === "all" ? docs.length : docs.filter((d) => shelfOf(d) === s.key).length}</span>
          </button>
        ))}
      </div>
      <div className="lib-chips">
        {chip("all", "All", docs.length)}
        {chip("review", "Needs review", st.needs_review ?? 0)}
        {chip("problems", "Couldn't read", st.failed ?? 0)}
        <span className="lib-chip-sep" />
        {CATEGORIES.map((c) => chip(c, CATEGORY_LABEL[c], q.data!.counts.category[c] ?? 0))}
      </div>
      <div className="lib-body">
        <div className="view-table-wrap">
          <table className="view-table lib-table">
            <colgroup>
              <col style={{ width: "27%" }} />
              <col style={{ width: "20%" }} />
              <col />
              <col style={{ width: "12%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>Name</th>
                <th>Category</th>
                <th>Summary</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr
                  key={d.item_id}
                  className={d.item_id === selectedId ? "on" : ""}
                  onClick={() => setSelectedId(d.item_id === selectedId ? null : d.item_id)}
                >
                  <td className="vt-name" title={d.path || d.name}>
                    {d.name}
                  </td>
                  <td>
                    {d.category && (d.status === "done" || d.status === "needs_review") ? (
                      <span className={`lib-badge cat-${d.category}`}>
                        <span className="lib-badge-t">{CATEGORY_LABEL[d.category]}</span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="lib-muted" title={d.summary || d.skip_reason || d.error || ""}>
                    {d.summary || d.skip_reason || d.error || ""}
                  </td>
                  <td className={`lib-st s-${d.status}`}>{STATUS_LABEL[d.status]}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="view-empty-row">
                    No documents match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {selected && <Detail doc={selected} onClose={() => setSelectedId(null)} />}
      </div>
    </div>
  );
}
