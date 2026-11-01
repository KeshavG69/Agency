"use client";

import { useState } from "react";
import { organizationsApi, type Organization, type OrgWorkflow, type TaskFlow } from "@/lib/api/organizations";

/**
 * Organisation settings → "How you bid": the contract vehicles this firm holds, which SAM.gov
 * notices the daily feed brings in, and the task flow every pursuit follows.
 *
 * Mounted with `key={org.id}` by the panel, so the editable state is seeded once from the
 * server and a background refetch never overwrites what the admin is typing.
 */

const FLOWS: { key: TaskFlow; title: string; steps: string }[] = [
  {
    key: "capture",
    title: "Full capture",
    steps: "Analyse → decide → approve capture → call the customer → review documents → submit",
  },
  {
    key: "gsa_quote",
    title: "Quick quote (GSA RFQ)",
    steps: "Review RFQ → decide → check requirements → price → write quote → final check → submit",
  },
];

/** A list of removable chips plus a free-text box and one-click suggestions. */
function ChipList({
  values,
  onChange,
  suggestions,
  placeholder,
  disabled,
}: {
  values: string[];
  onChange: (next: string[]) => void;
  suggestions: string[];
  placeholder: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const has = (v: string) => values.some((x) => x.toLowerCase() === v.toLowerCase());
  const add = (v: string) => {
    const t = v.trim();
    if (t && !has(t)) onChange([...values, t]);
    setDraft("");
  };
  const open = suggestions.filter((s) => !has(s));
  return (
    <div className="wf-chips">
      <div className="wf-chip-row">
        {values.map((v) => (
          <span key={v} className="wf-chip">
            {v}
            {!disabled && (
              <button aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
                ×
              </button>
            )}
          </span>
        ))}
        {!disabled && (
          <input
            className="wf-chip-input"
            value={draft}
            placeholder={values.length ? "Add another…" : placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                add(draft);
              }
            }}
            onBlur={() => draft.trim() && add(draft)}
          />
        )}
      </div>
      {!disabled && open.length > 0 && (
        <div className="wf-suggest">
          {open.map((s) => (
            <button key={s} className="wf-suggest-btn" onClick={() => add(s)}>
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function WorkflowSettings({
  workflow,
  isAdmin,
  onSaved,
}: {
  workflow: OrgWorkflow;
  isAdmin: boolean;
  onSaved: (org: Organization) => void;
}) {
  const [vehicles, setVehicles] = useState<string[]>(workflow.contract_vehicles);
  const [feedOn, setFeedOn] = useState(workflow.sam_feed.enabled);
  const [agencies, setAgencies] = useState<string[]>(workflow.sam_feed.agencies);
  const [flow, setFlow] = useState<TaskFlow>(workflow.task_flow);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const updated = await organizationsApi.updateOrganization({
        contract_vehicles: vehicles,
        sam_feed: { enabled: feedOn, agencies },
        task_flow: flow,
      });
      onSaved(updated);
      setMsg({
        text: flow !== workflow.task_flow ? "Saved — your task list is being rebuilt." : "Saved.",
        ok: true,
      });
    } catch {
      setMsg({ text: "Couldn't save — try again.", ok: false });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="sec-title">How you bid</div>
      {!isAdmin && <p className="wf-hint">Only an admin can change these.</p>}
      <div className="wf-form">
        <div className="wf-field">
          <label className="wf-label">Contract vehicles · what you hold</label>
          <ChipList
            values={vehicles}
            onChange={setVehicles}
            suggestions={workflow.suggested_vehicles}
            placeholder="e.g. GSA MAS"
            disabled={!isAdmin}
          />
          <div className="wf-hint">Offered as choices when you add an RFQ by hand.</div>
        </div>

        <div className="wf-field">
          <label className="wf-label">SAM.gov feed</label>
          <label className="wf-toggle">
            <input type="checkbox" checked={feedOn} disabled={!isAdmin} onChange={(e) => setFeedOn(e.target.checked)} />
            Bring in new SAM.gov notices every day
          </label>
          {feedOn ? (
            <>
              <div className="wf-sub">Only from these agencies</div>
              <ChipList
                values={agencies}
                onChange={setAgencies}
                suggestions={["General Services Administration"]}
                placeholder="Leave empty for every agency"
                disabled={!isAdmin}
              />
              <div className="wf-hint">
                {agencies.length
                  ? "Only notices from these agencies come in, matched on your NAICS codes from SAM.gov."
                  : "Every agency, matched on your NAICS codes from SAM.gov."}{" "}
                GSA eBuy RFQs aren&apos;t on SAM.gov — add those by hand.
              </div>
            </>
          ) : (
            <div className="wf-hint">Off — only opportunities you add by hand will appear.</div>
          )}
        </div>

        <div className="wf-field">
          <label className="wf-label">Task flow</label>
          <div className="wf-flows">
            {FLOWS.map((f) => (
              <label key={f.key} className={`wf-flow ${flow === f.key ? "on" : ""}`}>
                <input
                  type="radio"
                  name="task-flow"
                  checked={flow === f.key}
                  disabled={!isAdmin}
                  onChange={() => setFlow(f.key)}
                />
                <span className="wf-flow-t">{f.title}</span>
                <span className="wf-flow-s">{f.steps}</span>
              </label>
            ))}
          </div>
          <div className="wf-hint">The steps each opportunity puts on your Today list, timed back from its due date.</div>
        </div>

        {isAdmin && (
          <div className="wf-actions">
            <button className="btn primary" onClick={save} disabled={saving}>
              {saving ? (
                <>
                  <span className="spin" /> Saving…
                </>
              ) : (
                "Save"
              )}
            </button>
            {msg && <span className={msg.ok ? "wf-ok" : "wf-err"}>{msg.text}</span>}
          </div>
        )}
      </div>
    </>
  );
}
