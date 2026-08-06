import { create } from "zustand";

/**
 * App-shell UI state — the "what am I looking at" that used to live as a dozen useState
 * hooks inside the 3000-line Console component and get prop-drilled down.
 *
 * SCOPE: purely client-side view state. Server data stays in TanStack Query; URL-synced
 * filters/search stay in nuqs (lib/pipeline-params). Only put something here when more than
 * one component needs it, or it's a genuine app-wide mode — otherwise keep it local.
 *
 * This is the canonical home for ViewKey/TabKey so the store never has to import from a
 * component (which would risk an import cycle once those components read the store).
 */
export type ViewKey = "dashboard" | "pipeline" | "callplan" | "contacts" | "documents" | "org";
export type TabKey = "info" | "contacts" | "documents" | "activity" | "agent";

interface UiStore {
  // Which top-bar section is showing. The animated swap (switchView) is still the caller's
  // job — this just holds the destination.
  view: ViewKey;
  setView: (v: ViewKey) => void;

  // The opportunity detail slide-over.
  selectedId: string | null; // the opp whose detail sheet is open; null = closed
  tab: TabKey; // active tab inside the sheet
  // Sheet width: null = the CSS default. The expand button sets a wide preset; dragging the
  // sheet's left edge sets a custom px width. Both write here so there's one source of truth.
  detailWidth: number | null;
  setSelectedId: (id: string | null) => void;
  setTab: (t: TabKey) => void;
  setDetailWidth: (w: number | null) => void;

  // One-off modals mounted at the shell level.
  spPickerOpen: boolean; // SharePoint folder picker
  addOppOpen: boolean; // add-opportunity modal
  reviewOpen: boolean; // Outlook contact-review dialog
  setSpPickerOpen: (o: boolean) => void;
  setAddOppOpen: (o: boolean) => void;
  setReviewOpen: (o: boolean) => void;

  // Fire-and-forget "go refetch" signals to the graph views, which live far from the actions
  // that invalidate them (a sync finishing, or a disconnect that purges the graph
  // server-side). A caller bumps the counter; the graph's effect depends on it and refetches.
  // A monotonic counter (not a boolean) so back-to-back bumps each register as a change.
  contactsRefresh: number;
  sharePointRefresh: number;
  bumpContactsRefresh: () => void;
  bumpSharePointRefresh: () => void;

  // Back to a clean slate. Called on logout so one user's open view / selected opportunity
  // never carries into the next session on the same browser (mirrors connectionStore.reset).
  reset: () => void;
}

const INITIAL = {
  view: "dashboard" as ViewKey,
  selectedId: null,
  tab: "info" as TabKey,
  detailWidth: null,
  spPickerOpen: false,
  addOppOpen: false,
  reviewOpen: false,
  contactsRefresh: 0,
  sharePointRefresh: 0,
};

export const useUiStore = create<UiStore>((set) => ({
  ...INITIAL,
  setView: (view) => set({ view }),

  setSelectedId: (selectedId) => set({ selectedId }),
  setTab: (tab) => set({ tab }),
  setDetailWidth: (detailWidth) => set({ detailWidth }),

  setSpPickerOpen: (spPickerOpen) => set({ spPickerOpen }),
  setAddOppOpen: (addOppOpen) => set({ addOppOpen }),
  setReviewOpen: (reviewOpen) => set({ reviewOpen }),

  bumpContactsRefresh: () => set((s) => ({ contactsRefresh: s.contactsRefresh + 1 })),
  bumpSharePointRefresh: () => set((s) => ({ sharePointRefresh: s.sharePointRefresh + 1 })),

  reset: () => set({ ...INITIAL }),
}));
