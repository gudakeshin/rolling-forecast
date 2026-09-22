import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { shareablePanelParams } from '../utils/panelParams';

export interface SavedView {
  id: string;
  name: string;
  panelType: string;
  panelParams: Record<string, any>;
  createdAt: string;
}

interface SavedViewsState {
  views: SavedView[];
  saveView: (name: string, panelType: string, panelParams: Record<string, any>) => void;
  deleteView: (id: string) => void;
}

/** Named panel+filter combinations an analyst can reopen in one click.
 * localStorage-only for now, per-browser (see Phase 4.2) — a shared,
 * server-backed SavedView table is the natural next step if this sticks. */
export const useSavedViewsStore = create<SavedViewsState>()(
  persist(
    (set) => ({
      views: [],

      saveView: (name, panelType, panelParams) =>
        set((state) => ({
          views: [
            ...state.views,
            {
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              name,
              panelType,
              panelParams: shareablePanelParams(panelParams),
              createdAt: new Date().toISOString(),
            },
          ],
        })),

      deleteView: (id) =>
        set((state) => ({ views: state.views.filter((v) => v.id !== id) })),
    }),
    { name: 'rf_saved_views' },
  ),
);
