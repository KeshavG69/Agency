"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createManualOpportunity, type ManualOpportunityResult } from "@/lib/data";
import { organizationQuery } from "@/lib/queries";

/**
 * Manually add an opportunity — title + solicitation number + description + files.
 * The backend uploads the files to iDrive, parses + digests them (small model), and
 * runs the Analyst in the background. Mirrors SharePointFolderPicker's modal shell
 * (.crm-* classes) and AssignModal's Escape-to-close + async-save pattern.
 */
export default function AddOpportunityModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (r: ManualOpportunityResult) => void;
}) {
  const [title, setTitle] = useState("");
  const [number, setNumber] = useState("");
  const [description, setDescription] = useState("");
  const [agency, setAgency] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [sin, setSin] = useState("");
  // The org's own contract vehicles (Organisation settings → How you bid).
  const vehicles = useQuery(organizationQuery()).data?.org?.workflow?.contract_vehicles ?? [];
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !saving && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  const removeFile = (idx: number) => setFiles((prev) => prev.filter((_, i) => i !== idx));

  const save = async () => {
    if (!title.trim()) {
      setError("Give the opportunity a title.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await createManualOpportunity(
        {
          title: title.trim(),
          number: number.trim() || undefined,
          description: description.trim() || undefined,
          agency: agency.trim() || undefined,
          dueDate: dueDate || undefined,
          contractVehicle: vehicle || undefined,
          sin: sin.trim() || undefined,
        },
        files,
      );
      onCreated(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add the opportunity. Please try again.");
      setSaving(false);
    }
  };

  return (
    <div className="crm-backdrop" onClick={() => !saving && onClose()}>
      <div className="crm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="crm-head">
          <div>
            <h2>Add opportunity</h2>
            <div className="crm-sub">
              Add a solicitation or GSA RFQ by hand. Attach its documents — they’re parsed and
              the Analyst scores it once processing finishes. The due date sets your task timeline.
            </div>
          </div>
          <button className="crm-x" onClick={onClose} aria-label="Close" disabled={saving}>
            ×
          </button>
        </div>

        <div className="crm-body">
          <label className="aof-field">
            <span className="aof-label">Title *</span>
            <input
              className="aof-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Medical-Grade Water Quality Testing Services"
              autoFocus
            />
          </label>

          <label className="aof-field">
            <span className="aof-label">Solicitation number</span>
            <input
              className="aof-input"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              placeholder="e.g. HT940726QE006"
            />
          </label>

          <div className="aof-row">
            <label className="aof-field">
              <span className="aof-label">Agency</span>
              <input
                className="aof-input"
                value={agency}
                onChange={(e) => setAgency(e.target.value)}
                placeholder="e.g. Department of Veterans Affairs"
              />
            </label>
            <label className="aof-field">
              <span className="aof-label">Due date</span>
              <input className="aof-input" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
            </label>
          </div>

          <div className="aof-row">
            <label className="aof-field">
              <span className="aof-label">Contract vehicle</span>
              <select className="aof-input" value={vehicle} onChange={(e) => setVehicle(e.target.value)}>
                <option value="">{vehicles.length ? "— None / open market —" : "Add yours in Organisation settings"}</option>
                {vehicles.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label className="aof-field">
              <span className="aof-label">SIN</span>
              <input
                className="aof-input"
                value={sin}
                onChange={(e) => setSin(e.target.value)}
                placeholder="e.g. 54151S"
              />
            </label>
          </div>

          <label className="aof-field">
            <span className="aof-label">Description</span>
            <textarea
              className="aof-input aof-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short summary of the opportunity (optional — the documents ground the analysis)."
              rows={3}
            />
          </label>

          <div className="aof-field">
            <span className="aof-label">Documents</span>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                const picked = Array.from(e.target.files || []);
                setFiles((prev) => [...prev, ...picked]);
                if (fileRef.current) fileRef.current.value = ""; // allow re-picking the same file
              }}
            />
            <button className="btn ghost" type="button" onClick={() => fileRef.current?.click()}>
              + Add files
            </button>
            {files.length > 0 && (
              <ul className="aof-files">
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    <span className="aof-fname">{f.name}</span>
                    <span className="aof-fsize">{(f.size / 1024).toFixed(0)} KB</span>
                    <button
                      className="aof-fx"
                      type="button"
                      onClick={() => removeFile(i)}
                      aria-label={`Remove ${f.name}`}
                      disabled={saving}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {error && <div className="crm-state crm-err">{error}</div>}
        </div>

        <div className="crm-foot">
          <span className="crm-total">{files.length ? `${files.length} file${files.length > 1 ? "s" : ""}` : "No files"}</span>
          <div className="crm-actions">
            <button className="sel-link" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn primary" onClick={save} disabled={saving || !title.trim()}>
              {saving ? "Adding…" : "Add opportunity"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
