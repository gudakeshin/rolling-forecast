import { create, type StoreApi, type UseBoundStore } from 'zustand';
import { createContext, createElement, useContext, type ReactNode } from 'react';
import type { PanelData } from '../types/forecast';
import { useVersionStore } from './versionStore';

type PanelWidth = 'normal' | 'wide';
export type PanelSlot = 'primary' | 'secondary';

interface PanelState {
  isOpen: boolean;
  panelType: string | null;
  panelParams: Record<string, any>;
  panelData: PanelData | null;
  isLoading: boolean;
  widthMode: PanelWidth;

  openPanel: (type: string, params: Record<string, any>) => void;
  closePanel: () => void;
  setPanelData: (data: PanelData | null) => void;
  setLoading: (loading: boolean) => void;
  setWidthMode: (mode: PanelWidth) => void;
  toggleWidth: () => void;
  /** Bump panelParams so PanelContainer's fetch effect re-runs. */
  refreshPanel: () => void;
  /** Append rows for paginated "Load more" without resetting the panel. */
  appendPanelRows: (rows: unknown[]) => void;
  setPanelParams: (params: Record<string, any>) => void;
}

type PanelStoreHook = UseBoundStore<StoreApi<PanelState>>;

const PANEL_WIDTH_KEY = 'rf_panel_width_mode';
const SECONDARY_PANEL_WIDTH_KEY = 'rf_panel2_width_mode';

function readWidthMode(key: string): PanelWidth {
  try {
    const v = localStorage.getItem(key);
    return v === 'wide' ? 'wide' : 'normal';
  } catch {
    return 'normal';
  }
}

/** Two independent instances of this shape back the primary/secondary slots. */
function createPanelStore(widthKey: string): PanelStoreHook {
  return create<PanelState>((set) => ({
    isOpen: false,
    panelType: null,
    panelParams: {},
    panelData: null,
    isLoading: false,
    widthMode: readWidthMode(widthKey),

    openPanel: (type, params) => {
      let merged = { ...params };
      if (!merged.version_id && !merged.version_id_a) {
        const active = useVersionStore.getState().activeVersionId;
        if (active) merged = { version_id: active, ...merged };
      }
      set({
        isOpen: true,
        panelType: type,
        panelParams: merged,
        panelData: null,
        isLoading: true,
      });
    },

    closePanel: () =>
      set({
        isOpen: false,
        panelType: null,
        panelParams: {},
        panelData: null,
        isLoading: false,
      }),

    setPanelData: (data) => set({ panelData: data, isLoading: false }),

    setLoading: (loading) => set({ isLoading: loading }),

    setWidthMode: (mode) => {
      try {
        localStorage.setItem(widthKey, mode);
      } catch {
        /* ignore */
      }
      set({ widthMode: mode });
    },

    toggleWidth: () =>
      set((state) => {
        const mode: PanelWidth = state.widthMode === 'wide' ? 'normal' : 'wide';
        try {
          localStorage.setItem(widthKey, mode);
        } catch {
          /* ignore */
        }
        return { widthMode: mode };
      }),

    refreshPanel: () =>
      set((state) => {
        if (!state.panelType) return state;
        return {
          panelParams: { ...state.panelParams, _refresh: Date.now(), offset: 0 },
          isLoading: true,
        };
      }),

    appendPanelRows: (rows) =>
      set((state) => {
        if (!state.panelData) return state;
        const prev = (state.panelData.data?.rows as unknown[]) || [];
        return {
          panelData: {
            ...state.panelData,
            data: {
              ...state.panelData.data,
              rows: [...prev, ...rows],
            },
          },
        };
      }),

    setPanelParams: (params) => set({ panelParams: params }),
  }));
}

/** The "active workspace" slot — everything before the panel stack existed
 * pointed at this one store, so it must keep behaving exactly as before. */
export const usePrimaryPanelStore: PanelStoreHook = createPanelStore(PANEL_WIDTH_KEY);
/** The pinned/reference slot shown beside the primary one (Phase 4.1). */
export const useSecondaryPanelStore: PanelStoreHook = createPanelStore(SECONDARY_PANEL_WIDTH_KEY);

/**
 * Lets a panel-content component call the plain `usePanelStore` hook below
 * and transparently resolve to whichever slot it's rendered in —
 * `PanelContainer` wraps the secondary slot's subtree in a Provider pointing
 * at `useSecondaryPanelStore`. Outside any Provider (the default) it resolves
 * to the primary store, so every pre-existing call site is unaffected.
 */
const PanelStoreContext = createContext<PanelStoreHook>(usePrimaryPanelStore);

export function PanelStoreProvider({
  store,
  children,
}: {
  store: PanelStoreHook;
  children: ReactNode;
}) {
  return createElement(PanelStoreContext.Provider, { value: store }, children);
}

function usePanelStoreHook(): PanelState;
function usePanelStoreHook<T>(selector: (state: PanelState) => T): T;
function usePanelStoreHook<T>(selector?: (state: PanelState) => T): T | PanelState {
  const store = useContext(PanelStoreContext);
  return selector ? store(selector) : store();
}
// Imperative (non-component) call sites always target the primary/active
// panel — they run outside React so they can't resolve via context, and
// semantically a background action (a recommended-action dispatch, a
// drill-through click from chat) should land in the analyst's active
// workspace slot, never silently redirect into a pinned reference panel.
usePanelStoreHook.getState = usePrimaryPanelStore.getState;
usePanelStoreHook.setState = usePrimaryPanelStore.setState;
usePanelStoreHook.subscribe = usePrimaryPanelStore.subscribe;

/** Drop-in replacement for the old single-slot store — same shape, same
 * default (primary) behavior for every existing caller. */
export const usePanelStore = usePanelStoreHook as unknown as PanelStoreHook;

/** Copies the current primary panel into the secondary slot so the analyst
 * can keep it visible while navigating elsewhere in the active slot. */
export function pinPrimaryToSecondary(): void {
  const { panelType, panelParams } = usePrimaryPanelStore.getState();
  if (!panelType) return;
  useSecondaryPanelStore.getState().openPanel(panelType, panelParams);
}

interface WorkspaceLayoutState {
  /** Which slot (if any) is shown solo, full-width. */
  maximizedSlot: PanelSlot | null;
  setMaximizedSlot: (slot: PanelSlot | null) => void;
  toggleMaximize: (slot: PanelSlot) => void;
}

export const useWorkspaceLayoutStore = create<WorkspaceLayoutState>((set) => ({
  maximizedSlot: null,
  setMaximizedSlot: (slot) => set({ maximizedSlot: slot }),
  toggleMaximize: (slot) =>
    set((state) => ({ maximizedSlot: state.maximizedSlot === slot ? null : slot })),
}));

// Expose both store references for cross-store refresh without import cycles
// (versionStore reads these on active-version switch to refresh whichever
// open panel(s) are showing that version's data).
(globalThis as { __RF_PANEL_STORE__?: PanelStoreHook }).__RF_PANEL_STORE__ =
  usePrimaryPanelStore;
(globalThis as { __RF_PANEL_STORE_2__?: PanelStoreHook }).__RF_PANEL_STORE_2__ =
  useSecondaryPanelStore;
