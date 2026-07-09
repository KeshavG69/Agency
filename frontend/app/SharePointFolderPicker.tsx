"use client";

import { useEffect, useMemo, useState } from "react";
import { browseSharePointFolders, saveSharePointExcludedFolders, type SPBrowseNode } from "@/lib/data";

/**
 * Folder-picker for the Library — ingestion is opt-OUT: everything is included by default,
 * and unticking a site/library/folder excludes it (and everything inside it, via the
 * backend's path-prefix match — see sharepoint_graph_client._is_excluded). A cheap shallow
 * browse (no ACL, top-level folders only) powers the tree; the real crawl applies the saved
 * exclusion on the next sync. Mirrors ContactReviewModal's checkbox-list pattern/CSS classes.
 */
interface TreeNode extends SPBrowseNode {
  children: TreeNode[];
}

function buildTree(nodes: SPBrowseNode[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  nodes.forEach((n) => byId.set(n.id, { ...n, children: [] }));
  const roots: TreeNode[] = [];
  for (const n of byId.values()) {
    const parent = n.parent_id ? byId.get(n.parent_id) : null;
    (parent ? parent.children : roots).push(n);
  }
  return roots;
}

function isExcluded(path: string, excluded: Set<string>): boolean {
  if (excluded.has(path)) return true;
  for (const p of excluded) if (path.startsWith(`${p}/`)) return true;
  return false;
}

export default function SharePointFolderPicker({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<SPBrowseNode[]>([]);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    browseSharePointFolders()
      .then((res) => {
        setNodes(res.nodes);
        setExcluded(new Set(res.excluded_paths));
      })
      .catch(() => setError("Couldn't read the SharePoint structure. Make sure Library is connected."))
      .finally(() => setLoading(false));
  }, []);

  // Files aren't pickable — only sites/libraries/folders.
  const tree = useMemo(() => buildTree(nodes.filter((n) => n.type !== "file")), [nodes]);

  const toggle = (path: string, currentlyIncluded: boolean) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      if (currentlyIncluded) next.add(path);
      else next.delete(path);
      return next;
    });

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveSharePointExcludedFolders(Array.from(excluded));
      onSaved();
    } catch {
      setError("Couldn't save your selection.");
      setSaving(false);
    }
  };

  const Row = ({ node, depth }: { node: TreeNode; depth: number }) => {
    const included = !isExcluded(node.path, excluded);
    return (
      <>
        <label className={`crm-row ${included ? "on" : ""}`} style={{ paddingLeft: 12 + depth * 20 }}>
          <input type="checkbox" checked={included} onChange={() => toggle(node.path, included)} />
          <span className="crm-check" aria-hidden />
          <span className="crm-main">
            <span className="crm-name">{node.name}</span>
          </span>
          <span className="crm-meta">
            <span
              className="crm-co"
              style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: "0.04em" }}
            >
              {node.type}
            </span>
          </span>
        </label>
        {node.children.map((c) => (
          <Row key={c.id} node={c} depth={depth + 1} />
        ))}
      </>
    );
  };

  return (
    <div className="crm-backdrop" onClick={onClose}>
      <div className="crm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="crm-head">
          <div>
            <h2>Select folders to ingest</h2>
            <div className="crm-sub">
              Everything is included by default. Untick a site, library, or folder to exclude it
              — and everything inside it — from the knowledge graph.
            </div>
          </div>
          <button className="crm-x" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="crm-body">
          {loading ? (
            <div className="crm-state">
              <span className="spin" /> Reading your SharePoint structure…
            </div>
          ) : error ? (
            <div className="crm-state crm-err">{error}</div>
          ) : tree.length === 0 ? (
            <div className="crm-state">No SharePoint structure found — connect and sync Library first.</div>
          ) : (
            <div className="crm-rows">
              {tree.map((n) => (
                <Row key={n.id} node={n} depth={0} />
              ))}
            </div>
          )}
        </div>

        <div className="crm-foot">
          <span className="crm-total">{excluded.size} excluded</span>
          <div className="crm-actions">
            <button className="sel-link" onClick={onClose}>
              Cancel
            </button>
            <button className="btn primary" onClick={save} disabled={loading || !!error || saving}>
              {saving ? "Saving…" : "Save & resync"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
