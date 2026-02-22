import { create } from 'zustand';
import type { PanelData } from '../types/forecast';

interface PanelState {
  isOpen: boolean;
  panelType: string | null;
  panelParams: Record<string, any>;
  panelData: PanelData | null;
  isLoading: boolean;

  openPanel: (type: string, params: Record<string, any>) => void;
  closePanel: () => void;
  setPanelData: (data: PanelData) => void;
  setLoading: (loading: boolean) => void;
}

export const usePanelStore = create<PanelState>((set) => ({
  isOpen: false,
  panelType: null,
  panelParams: {},
  panelData: null,
  isLoading: false,

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
}));
