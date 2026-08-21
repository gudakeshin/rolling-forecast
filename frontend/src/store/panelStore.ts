import { create } from 'zustand';
import type { PanelData } from '../types/forecast';
import { useVersionStore } from './versionStore';

type PanelWidth = 'normal' | 'wide';

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

const PANEL_WIDTH_KEY = 'rf_panel_width_mode';

function readWidthMode(): PanelWidth {
  try {
    const v = localStorage.getItem(PANEL_WIDTH_KEY);
    return v === 'wide' ? 'wide' : 'normal';
  } catch {
    return 'normal';
  }
}

export const usePanelStore = create<PanelState>((set) => ({
  isOpen: false,
  panelType: null,
  panelParams: {},
  panelData: null,
  isLoading: false,
  widthMode: readWidthMode(),

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
      localStorage.setItem(PANEL_WIDTH_KEY, mode);
    } catch {
      /* ignore */
    }
    set({ widthMode: mode });
  },

  toggleWidth: () =>
    set((state) => {
      const mode: PanelWidth = state.widthMode === 'wide' ? 'normal' : 'wide';
      try {
        localStorage.setItem(PANEL_WIDTH_KEY, mode);
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

// Expose store reference for cross-store refresh without import cycles.
(globalThis as { __RF_PANEL_STORE__?: typeof usePanelStore }).__RF_PANEL_STORE__ =
  usePanelStore;
