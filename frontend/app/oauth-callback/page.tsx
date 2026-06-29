"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  getConnStatus,
  syncOutlookContacts,
  syncSharePointStructure,
} from "@/lib/data";
import { useConnectionStore, type Provider } from "@/lib/stores/connectionStore";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const LABEL: Record<string, string> = { outlook: "Outlook", sharepoint: "SharePoint" };

function CallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  // Which provider this OAuth round-trip was for (set before redirect). Default outlook.
  const provider =
    (typeof window !== "undefined" && sessionStorage.getItem("pendingProvider")) || "outlook";
  const name = LABEL[provider] ?? provider;
  const [msg, setMsg] = useState(`Finishing the ${name} connection…`);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (params.get("error")) {
        setMsg("Connection was not completed. Returning…");
        await sleep(1800);
        if (!cancelled) router.push("/");
        return;
      }

      // Composio stores the tokens server-side; poll until the account is ACTIVE.
      for (let i = 0; i < 20 && !cancelled; i++) {
        try {
          const s = await getConnStatus(provider);
          if (s.connected) {
            // Cache the connection so the app never has to poll status again.
            useConnectionStore
              .getState()
              .setConnection(provider as Provider, true, s.connected_account_id);
            if (provider === "sharepoint") {
              setMsg("Connected — mapping your SharePoint documents…");
              await syncSharePointStructure();
            } else {
              setMsg("Connected — downloading your contacts…");
              await syncOutlookContacts();
            }
            sessionStorage.removeItem("pendingProvider");
            await sleep(800);
            if (!cancelled) router.push(`/?connected=${provider}`);
            return;
          }
        } catch {
          /* backend may still be settling; keep polling */
        }
        await sleep(1500);
      }

      if (!cancelled) {
        setMsg("Still connecting — you can head back; it’ll finish in the background.");
        await sleep(2000);
        router.push("/");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [router, params, provider]);

  return (
    <div className="loading-full" style={{ flexDirection: "column", gap: 14 }}>
      <div className="word" style={{ fontFamily: "var(--font-display)", fontSize: 30 }}>
        Collecct<span style={{ color: "var(--accent-2)" }}>.</span>
      </div>
      <div style={{ fontStyle: "normal", fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--muted)" }}>
        {msg}
      </div>
    </div>
  );
}

export default function OAuthCallback() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <CallbackInner />
    </Suspense>
  );
}
