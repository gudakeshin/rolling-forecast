import { create } from 'zustand';
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  type SavedView as ApiSavedView,
} from '../api/savedViews';
import { shareablePanelParams } from '../utils/panelParams';

export interface SavedView {
  id: string;
  name: string;
  panelType: string;
  panelParams: Record<string, any>;
  createdAt: string;
}

function fromApi(v: ApiSavedView): SavedView {
  return {
    id: v.id,
    name: v.name,
    panelType: v.panel_type,
    panelParams: v.panel_params,
    createdAt: v.created_at ?? '',
  };
}

interface SavedViewsState {
  views: SavedView[];
  isLoading: boolean;
  error: string | null;
  fetchViews: () => Promise<void>;
  saveView: (name: string, panelType: string, panelParams: Record<string, any>) => Promise<void>;
  deleteView: (id: string) => Promise<void>;
}

/** Named panel+filter combinations an analyst can reopen in one click.
 * Server-backed (SavedView table, scoped by business_unit_id) so views
 * sync across a user's devices/browsers instead of being per-browser. */
export const useSavedViewsStore = create<SavedViewsState>()((set) => ({
  views: [],
  isLoading: false,
  error: null,

  fetchViews: async () => {
    set({ isLoading: true, error: null });
    try {
      const views = (await listSavedViews()).map(fromApi);
      set({ views, isLoading: false });
    } catch (error: any) {
      set({ error: error.message, isLoading: false });
    }
  },

  saveView: async (name, panelType, panelParams) => {
    const created = await createSavedView({
      name,
      panel_type: panelType,
      panel_params: shareablePanelParams(panelParams),
    });
    set((state) => ({ views: [fromApi(created), ...state.views] }));
  },

  deleteView: async (id) => {
    await deleteSavedView(id);
    set((state) => ({ views: state.views.filter((v) => v.id !== id) }));
  },
}));
