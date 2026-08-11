"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  getConnStatus,
  connectSharePointRest,
  syncSharePointStructure,
} from "@/lib/data";
import { useConnectionStore, type Provider } from "@/lib/stores/connectionStore";
import AuthShell from "@/app/auth/AuthShell";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const LABEL: Record<string, string> = { outlook: "Outlook", sharepoint: "SharePoint" };

function CallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [msg, setMsg] = useState("Finishing the connection…");

  useEffect(() => {
    let cancelled = false;

    (async () => {
      // Resolve provider entirely inside the effect — sessionStorage is only available
      // client-side after mount, so reading it at component-body level can return stale
      // or undefined values during SSR/hydration. `spStage` is the reliable secondary
      // signal: it is only ever written for SharePoint flows (never Outlook), so if
      // `pendingProvider` is missing but `spStage` is present this is a SharePoint callback.
      const pendingProvider = sessionStorage.getItem("pendingProvider");
      const spStage = sessionStorage.getItem("spStage");
      const provider = pendingProvider || (spStage ? "sharepoint" : "outlook");
      const name = LABEL[provider] ?? provider;

      setMsg(`Finishing the ${name} connection…`);

      if (params.get("error")) {
        setMsg("Connection was not completed. Returning…");
        sessionStorage.removeItem("pendingProvider");
        sessionStorage.removeItem("spStage");
        await sleep(1800);
        if (!cancelled) router.push("/");
        return;
      }

      // SharePoint is TWO Composio connections chained under one click: Graph first
      // (structure/ACL/write), then REST (exact site-group member emails — the one
      // thing Graph can't resolve). `spStage` is the literal provider string we just
      // sent the user to Microsoft for ("sharepoint" | "sharepoint_rest").
      const resolvedSpStage = spStage || "sharepoint";
      const pollTarget = provider === "sharepoint" ? resolvedSpStage : provider;

      // Composio stores the tokens server-side; poll until the account is ACTIVE.
      for (let i = 0; i < 20 && !cancelled; i++) {
        try {
          const s = await getConnStatus(pollTarget);
          if (s.connected) {
            if (provider === "sharepoint" && resolvedSpStage === "sharepoint") {
              // Graph stage done — chain straight into the REST stage (no extra click).
              setMsg("Connected — one more step for exact permissions…");
              const rest = await getConnStatus("sharepoint_rest");
              if (!rest.connected) {
                const { auth_url } = await connectSharePointRest(
                  `${window.location.origin}/oauth-callback`,
                );
                sessionStorage.setItem("spStage", "sharepoint_rest");
                if (!cancelled) window.location.href = auth_url;
                return;
              }
              // REST was already connected (e.g. reconnecting Graph only) — fall through to finish.
            }

            // Either the REST stage just finished, or (Graph case above) REST was already
            // done — SharePoint is now fully connected. Re-read the GRAPH stage's status
            // specifically for the cached account id — `s` may be the REST stage's status
            // (whichever stage we were just polling), and the cached id should consistently
            // be the primary (Graph) connection, not whichever happened to finish last.
            if (provider === "sharepoint") {
              const graphStatus = resolvedSpStage === "sharepoint" ? s : await getConnStatus("sharepoint");
              useConnectionStore
                .getState()
                .setConnection("sharepoint", true, graphStatus.connected_account_id);
              sessionStorage.removeItem("pendingProvider");
              sessionStorage.removeItem("spStage");
              setMsg("Connected — mapping your SharePoint documents…");
              await syncSharePointStructure();
              await sleep(800);
              if (!cancelled) router.push(`/?connected=sharepoint`);
              return;
            }

            // Outlook (or any other single-stage provider).
            useConnectionStore
              .getState()
              .setConnection(provider as Provider, true, s.connected_account_id);
            sessionStorage.removeItem("pendingProvider");
            // Outlook: don't auto-ingest — send the user to the review dialog so
            // they choose which contacts to import.
            setMsg("Connected — choose which contacts to import…");
            await sleep(500);
            if (!cancelled) router.push(`/?review=outlook`);
            return;
          }
        } catch {
          /* backend may still be settling; keep polling */
        }
        await sleep(1500);
      }

      if (!cancelled) {
        setMsg("Still connecting — you can head back; it’ll finish in the background.");
        // Clear the pending markers same as the error path — nothing keeps polling once this
        // page unmounts, and a stale spStage/pendingProvider could confuse a LATER unrelated
        // redirect back to this page. A future "Connect" click always re-checks real status
        // per stage regardless, so this is just cleanup, not a functional dependency.
        sessionStorage.removeItem("pendingProvider");
        sessionStorage.removeItem("spStage");
        await sleep(2000);
        router.push("/");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [router, params]);

  return (
    <AuthShell facts={false}>
      <span className="auth-working">
        <i />
        Connecting
      </span>
      <h1 className="auth-h1" style={{ marginTop: 14 }}>
        Finishing the connection
      </h1>
      <p className="auth-status-sub">{msg}</p>
      {/* Manual escape hatch — the poll loop can take up to ~30s (or longer if a
          Composio/network hiccup stalls it) before it gives up on its own; let the user
          leave immediately instead of forcing them to wait it out. The connection still
          finishes server-side either way. */}
      <div className="auth-actions">
        <button type="button" className="btn ghost auth-alt" onClick={() => router.push("/")}>
          Back to Collecct
        </button>
      </div>
      <p className="auth-help">
        You can leave now — the connection keeps finishing in the background.
      </p>
    </AuthShell>
  );
}

export default function OAuthCallback() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <CallbackInner />
    </Suspense>
  );
}
