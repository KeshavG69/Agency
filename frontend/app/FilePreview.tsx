"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getDocUrl } from "@/lib/data";

/** Extension from a URL or filename (ignores query/hash). */
function extOf(s: string): string {
  return (s.split("?")[0].split("#")[0].split(".").pop() || "").toLowerCase();
}

/**
 * Inline document preview modal — same approach as the Kroolo enterprise-search viewer:
 * render Office files (docx/pptx/xlsx) through Microsoft's Office Online embed viewer, fall
 * back to the Google Docs viewer if it stalls, and PDFs straight in an iframe.
 *
 * It first asks the backend for a FRESH presigned URL (the stored one expires), so previews
 * never break, then hands that live URL to the viewer. "Download" uses the same fresh URL.
 */
export default function FilePreview({
  documentId,
  title,
  onClose,
}: {
  documentId: string;
  title: string;
  onClose: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [urlError, setUrlError] = useState(false);

  // Fetch a fresh, non-expired link for this document.
  useEffect(() => {
    let alive = true;
    getDocUrl(documentId)
      .then((u) => alive && setUrl(u))
      .catch(() => alive && setUrlError(true));
    return () => {
      alive = false;
    };
  }, [documentId]);

  const ext = useMemo(() => (url ? extOf(url) : "") || extOf(title), [url, title]);
  const isPdf = ext === "pdf";

  const officeUrl = useMemo(
    () => (url ? `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(url)}` : ""),
    [url],
  );
  const googleUrl = useMemo(
    () => (url ? `https://docs.google.com/gview?url=${encodeURIComponent(url)}&embedded=true` : ""),
    [url],
  );

  const [viewerIndex, setViewerIndex] = useState(0); // 0 = Office, 1 = Google
  const [status, setStatus] = useState<"loading" | "loaded" | "timeout">("loading");
  const timeoutRef = useRef<number | null>(null);

  const src = !url ? "" : isPdf ? url : viewerIndex === 0 ? officeUrl : googleUrl;

  useEffect(() => {
    if (!src) return;
    setStatus("loading");
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => setStatus("timeout"), 9000);
    return () => {
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    };
  }, [src]);

  useEffect(() => {
    if (status === "timeout" && !isPdf && viewerIndex === 0) setViewerIndex(1);
  }, [status, isPdf, viewerIndex]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const exhausted = status === "timeout" && (isPdf || viewerIndex === 1);

  return (
    <div className="preview-overlay" onClick={onClose}>
      <div className="preview-modal" onClick={(e) => e.stopPropagation()}>
        <div className="preview-head">
          <span className="preview-title">{title}</span>
          <div className="preview-actions">
            {url && (
              <a href={url} target="_blank" rel="noreferrer" className="preview-btn">
                Download ↓
              </a>
            )}
            <button className="preview-btn" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>
        <div className="preview-body">
          {urlError ? (
            <div className="preview-fallback">Couldn’t load this document. Try again later.</div>
          ) : !url ? (
            <div className="preview-fallback">Loading preview…</div>
          ) : exhausted ? (
            <div className="preview-fallback">
              <div>Preview isn’t available for this document.</div>
              <div style={{ marginTop: 6, opacity: 0.8 }}>Use Download to view it.</div>
            </div>
          ) : (
            <iframe
              key={src}
              title={title}
              src={src}
              className="preview-frame"
              loading="lazy"
              referrerPolicy="no-referrer"
              onLoad={() => setStatus("loaded")}
            />
          )}
        </div>
      </div>
    </div>
  );
}
