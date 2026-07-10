import { create } from 'zustand';
import type { PanelData } from '../types/forecast';

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

  openPanel: (type, params) =>
    set({
      isOpen: true,
      panelType: type,
      panelParams: params,
      panelData: null,
      isLoading: true,
    }),

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
}));
