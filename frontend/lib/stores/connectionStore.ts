import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Cached connection state for Outlook + SharePoint.
 *
 * We don't poll the backend for connection status on every load. Instead we KNOW a provider
 * is connected because the user came back through the OAuth redirect (/oauth-callback), which
 * writes the result here. The home page reads this cache; the callback updates it. Persisted
 * to localStorage so it survives refreshes, and cleared on logout (see authStore) so one
 * user's cache never leaks to the next.
 */
export type Provider = "outlook" | "sharepoint";

interface ConnState {
  connected: boolean;
  accountId: string | null; // needed to disconnect
}

interface ConnectionStore {
  outlook: ConnState;
  sharepoint: ConnState;
  setConnection: (provider: Provider, connected: boolean, accountId?: string | null) => void;
  reset: () => void;
}

const EMPTY: ConnState = { connected: false, accountId: null };

export const useConnectionStore = create<ConnectionStore>()(
  persist(
    (set) => ({
      outlook: { ...EMPTY },
      sharepoint: { ...EMPTY },
      setConnection: (provider, connected, accountId = null) =>
        set({ [provider]: { connected, accountId } } as Partial<ConnectionStore>),
      reset: () => set({ outlook: { ...EMPTY }, sharepoint: { ...EMPTY } }),
    }),
    { name: "collecct-connections" },
  ),
);
