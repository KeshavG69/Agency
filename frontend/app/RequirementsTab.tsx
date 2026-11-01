"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addRequirementItem,
  approveRequirements,
  attachRequirementFile,
  editRequirementItem,
  getDocUrl,
  readRequirements,
  removeRequirementItem,
  type Opportunity,
  type ProposalRequirements,
  type RequirementItem,
} from "@/lib/data";
import { queryKeys, requirementsQuery } from "@/lib/queries";
import { useToastStore } from "@/lib/stores/toastStore";

/**
 * Everything this solicitation says must be submitted, read from its original files.
 *
 * It is a DRAFT until a person approves it: every line shows the words it came from, anything
 * that couldn't be confirmed in the files is flagged, and all of it can be edited. Once work
 * starts it doubles as the to-do list: tick a line off, attach the finished file.
 */

const KIND_LABEL: Record<RequirementItem["kind"], string> = {
  volume: "Volume",
  plan: "Plan",
  resume: "Resumes",
  form: "Form",
  certification: "Certification",
  pricing: "Pricing",
  past_performance: "Past performance",
  letter: "Letter",
  attachment: "Attachment",
  other: "Other",
};

/** A fact and the words it came from. Renders nothing when the solicitation doesn't state it. */
function Fact({
  label,
  value,
  sheet,
  name,
}: {
  label: string;
  value?: string | null;
  sheet: { evidence?: Record<string, string>; unverified?: string[] };
  name: string;
}) {
  if (!value) return null;
  const quote = sheet.evidence?.[name];
  return (
    <div className="lib-fact">
      <span className="lib-fact-k">{label}</span>
      <span className="lib-fact-v">
        {value}
        {sheet.unverified?.includes(name) && <span className="req-flag req-flag-inline">Check this</span>}
      </span>
      {quote && <span className="lib-fact-q">“{quote}”</span>}
    </div>
  );
}

function Item({
  item,
  oppId,
  onRow,
}: {
  item: RequirementItem;
  oppId: string;
  onRow: (row: ProposalRequirements) => void;
}) {
  const toast = useToastStore((s) => s.push);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [limit, setLimit] = useState(item.page_limit ?? "");
  const [detail, setDetail] = useState(item.detail ?? "");
  const [note, setNote] = useState(item.note ?? "");

  const fail = () => toast("Couldn't save that — try again.");
  const edit = useMutation({
    mutationFn: (f: Parameters<typeof editRequirementItem>[2]) => editRequirementItem(oppId, item.id, f),
    onSuccess: onRow,
    onError: fail,
  });
  const remove = useMutation({ mutationFn: () => removeRequirementItem(oppId, item.id), onSuccess: onRow, onError: fail });
  const attach = useMutation({
    mutationFn: (f: File) => attachRequirementFile(oppId, item.id, f),
    onSuccess: onRow,
    onError: () => toast("Couldn't attach that file — try again."),
  });

  const saveEdit = () => {
    edit.mutate({ title: title.trim() || item.title, detail, page_limit: limit.trim() || null });
    setEditing(false);
  };

  return (
    <li className={`req-item ${item.done ? "done" : ""}`}>
      <label className="req-check">
        <input
          type="checkbox"
          checked={item.done}
          disabled={edit.isPending}
          aria-label={`Mark ${item.title} ready`}
          onChange={(e) => edit.mutate({ done: e.target.checked })}
        />
      </label>
      <div className="req-main">
        {editing ? (
          <div className="req-edit">
            <input className="search" value={title} aria-label="Title" onChange={(e) => setTitle(e.target.value)} />
            <input
              className="search"
              value={limit}
              aria-label="Page limit"
              placeholder="Page limit"
              onChange={(e) => setLimit(e.target.value)}
            />
            <textarea
              className="search"
              rows={3}
              value={detail}
              aria-label="What it must contain"
              onChange={(e) => setDetail(e.target.value)}
            />
            <div className="req-row-actions">
              <button className="mini-btn" onClick={saveEdit}>Save</button>
              <button className="sel-link" onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <>
            <div className="req-title-row">
              <button className="req-title" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                {item.title}
              </button>
              <span className="req-kind">{KIND_LABEL[item.kind] ?? "Other"}</span>
              {item.page_limit && <span className="req-limit">{item.page_limit}</span>}
              {!item.verified && <span className="req-flag" title="We couldn't find these exact words in the files">Check this</span>}
              {item.dropped_by_reread && <span className="req-flag">No longer listed</span>}
            </div>
            {item.detail && <p className="req-detail">{item.detail}</p>}
          </>
        )}

        {item.files.length > 0 && (
          <ul className="req-files">
            {item.files.map((f) => (
              <li key={f.id}>
                <button
                  className="req-file"
                  onClick={async () => {
                    try {
                      window.open(await getDocUrl(f.id), "_blank", "noopener");
                    } catch {
                      toast("Couldn't open that file.");
                    }
                  }}
                >
                  {f.title}
                </button>
              </li>
            ))}
          </ul>
        )}

        {open && !editing && (
          <div className="req-more">
            {item.quote && (
              <p className="req-quote">
                “{item.quote}”{item.source_ref ? ` — ${item.source_ref}` : ""}
              </p>
            )}
            {item.page_limit_quote && <p className="req-quote">“{item.page_limit_quote}”</p>}
            {item.format && <p className="req-detail">Format: {item.format}</p>}
            <textarea
              className="search"
              rows={2}
              placeholder="Note to self…"
              aria-label="Note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onBlur={() => note !== (item.note ?? "") && edit.mutate({ note })}
            />
            <div className="req-row-actions">
              <input
                ref={fileRef}
                type="file"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) attach.mutate(f);
                  e.target.value = "";
                }}
              />
              <button className="mini-btn" disabled={attach.isPending} onClick={() => fileRef.current?.click()}>
                {attach.isPending ? "Attaching…" : "Attach file"}
              </button>
              <button className="sel-link" onClick={() => setEditing(true)}>Edit</button>
              <button className="sel-link" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Remove
              </button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

export default function RequirementsTab({ opp }: { opp: Opportunity }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.push);
  const q = useQuery(requirementsQuery(opp.id));
  const [newTitle, setNewTitle] = useState("");

  const setRow = (row: ProposalRequirements) => qc.setQueryData(queryKeys.requirements(opp.id), row);
  const read = useMutation({
    mutationFn: () => readRequirements(opp.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.requirements(opp.id) }),
    onError: () => toast("Couldn't start reading — try again."),
  });
  const approve = useMutation({
    mutationFn: () => approveRequirements(opp.id),
    onSuccess: (row) => {
      setRow(row);
      toast("List approved — your next step is on Today.", "success");
    },
    onError: () => toast("Couldn't approve the list — try again."),
  });
  const add = useMutation({
    mutationFn: (title: string) => addRequirementItem(opp.id, title),
    onSuccess: (row) => {
      setRow(row);
      setNewTitle("");
    },
    onError: () => toast("Couldn't add that — try again."),
  });

  const r = q.data;
  if (q.isError && !r) return <div className="mail-err">Couldn&apos;t load the requirements.</div>;
  if (!r) return <p className="req-muted">Loading…</p>;

  if (r.status === "none" || r.status === "failed") {
    return (
      <div className="req-empty">
        <div className="sec-title first">Required documents</div>
        <p className="req-muted">
          {r.status === "failed"
            ? r.error
            : "Collecct reads this solicitation's files and lists everything you must submit: volumes, page limits, forms, resumes, and how to submit."}
        </p>
        <button className="btn primary" disabled={read.isPending} onClick={() => read.mutate()}>
          {r.status === "failed" ? "Try again" : "Read the requirements"}
        </button>
      </div>
    );
  }

  if (r.status === "reading") {
    return (
      <div className="req-empty">
        <div className="sec-title first">Required documents</div>
        <p className="req-muted">Reading the solicitation&apos;s files. This usually takes a couple of minutes.</p>
      </div>
    );
  }

  const items = r.items ?? [];
  const done = items.filter((i) => i.done).length;
  const s = r.submission ?? {};
  const f = r.format_rules ?? {};
  const pp = r.past_performance ?? {};
  const e = r.evaluation ?? {};
  const flagged =
    items.filter((i) => !i.verified).length +
    (r.pass_fail_rules ?? []).filter((p) => !p.verified).length +
    [s, f, pp, e].reduce((n, sheet) => n + (sheet.unverified?.length ?? 0), 0);

  return (
    <>
      <div className="req-head">
        <div className="req-progress">
          <div className="req-count">
            {done} of {items.length} ready
          </div>
          <div className="req-bar" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={items.length}>
            <i style={{ width: items.length ? `${(done / items.length) * 100}%` : 0 }} />
          </div>
        </div>
        <div className="req-head-actions">
          {r.status === "approved" ? (
            <span className="req-approved">Approved{r.approved_by ? ` by ${r.approved_by}` : ""}</span>
          ) : (
            <button className="btn primary" disabled={approve.isPending} onClick={() => approve.mutate()}>
              Approve this list
            </button>
          )}
          <button className="sel-link" disabled={read.isPending} onClick={() => read.mutate()} title="Read the files again, e.g. after an amendment. Your ticks, notes and files are kept.">
            Re-read
          </button>
        </div>
      </div>

      {r.summary && <p className="req-summary">{r.summary}</p>}

      {r.files?.from_summary && (
        <p className="req-warn">Only a summary of this solicitation was available, not its files. Page limits and forms may be missing.</p>
      )}
      {r.files?.truncated && <p className="req-warn">This package is very large and wasn&apos;t read in full. Check the instructions yourself.</p>}
      {r.found_instructions === false && (
        <p className="req-warn">No submission instructions were found in these files. Upload them if you have them.</p>
      )}
      {flagged > 0 && (
        <p className="req-warn">
          {flagged} line{flagged > 1 ? "s" : ""} marked “Check this” couldn&apos;t be confirmed word for word in the files.
        </p>
      )}

      {(r.pass_fail_rules ?? []).length > 0 && (
        <>
          <div className="sec-title first">Must not miss</div>
          <ul className="req-rules">
            {r.pass_fail_rules!.map((p, i) => (
              <li key={i}>
                <span>{p.rule}</span>
                {!p.verified && <span className="req-flag">Check this</span>}
                {p.quote && (
                  <span className="lib-fact-q">
                    “{p.quote}”{p.source_ref ? ` — ${p.source_ref}` : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="sec-title">What to submit</div>
      <ul className="req-list">
        {items.map((i) => (
          <Item key={i.id} item={i} oppId={opp.id} onRow={setRow} />
        ))}
      </ul>
      <form
        className="req-add"
        onSubmit={(ev2) => {
          ev2.preventDefault();
          if (newTitle.trim()) add.mutate(newTitle.trim());
        }}
      >
        <input
          className="search"
          placeholder="Add something the list missed…"
          aria-label="Add a requirement"
          value={newTitle}
          onChange={(ev2) => setNewTitle(ev2.target.value)}
        />
        <button className="mini-btn" disabled={add.isPending || !newTitle.trim()}>Add</button>
      </form>

      <div className="sec-title">How to submit</div>
      <div className="req-facts">
        <Fact label="Due" value={s.due} sheet={s} name="due" />
        <Fact label="Method" value={s.method} sheet={s} name="method" />
        <Fact label="Where" value={s.address} sheet={s} name="address" />
        <Fact label="File rules" value={s.file_rules} sheet={s} name="file_rules" />
        <Fact label="Questions due" value={s.questions_due} sheet={s} name="questions_due" />
        <Fact label="Font" value={f.font} sheet={f} name="font" />
        <Fact label="Margins" value={f.margins} sheet={f} name="margins" />
        <Fact label="Page size" value={f.page_size} sheet={f} name="page_size" />
        <Fact label="Page count" value={f.page_counting} sheet={f} name="page_counting" />
        {!s.due && <p className="req-muted">No due date is stated in these files — check the RFQ notice.</p>}
      </div>

      {(e.basis || (e.factors ?? []).length > 0) && (
        <>
          <div className="sec-title">How it&apos;s scored</div>
          <div className="req-facts">
            <Fact label="Basis" value={e.basis} sheet={e} name="basis" />
            {(e.factors ?? []).length > 0 && (
              <ol className="req-factors">
                {e.factors!.map((x, i) => (
                  <li key={i}>
                    {x.name}
                    {x.importance ? ` — ${x.importance}` : ""}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </>
      )}

      {(pp.references || pp.recency || pp.relevancy || pp.prime_rule || pp.form) && (
        <>
          <div className="sec-title">Past performance rules</div>
          <div className="req-facts">
            <Fact label="References" value={pp.references} sheet={pp} name="references" />
            <Fact label="Recency" value={pp.recency} sheet={pp} name="recency" />
            <Fact label="Relevancy" value={pp.relevancy} sheet={pp} name="relevancy" />
            <Fact label="Prime rule" value={pp.prime_rule} sheet={pp} name="prime_rule" />
            <Fact label="Form" value={pp.form} sheet={pp} name="form" />
          </div>
        </>
      )}

      {(r.prohibited ?? []).length > 0 && (
        <>
          <div className="sec-title">Do not include</div>
          <ul className="req-rules">
            {r.prohibited!.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </>
      )}

      {(r.files?.read ?? []).length > 0 && (
        <p className="req-muted req-sources">
          Read from {r.files!.read.length} file{r.files!.read.length > 1 ? "s" : ""}: {r.files!.read.join(", ")}.
          {r.files!.not_read.length > 0 && ` Not read: ${r.files!.not_read.join(", ")}.`}
        </p>
      )}
    </>
  );
}
