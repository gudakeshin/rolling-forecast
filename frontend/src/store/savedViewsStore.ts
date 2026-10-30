import { create } from 'zustand';
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
  type SavedViewDto,
} from '../api/savedViews';
import { shareablePanelParams } from '../utils/panelParams';

export interface SavedView {
  id: string;
  name: string;
  panelType: string;
  panelParams: Record<string, any>;
  isShared: boolean;
  isOwner: boolean;
  createdAt: string | null;
}

interface SavedViewsState {
  views: SavedView[];
  /** Load the views saved in the user's active company (own + shared). */
  load: () => Promise<void>;
  saveView: (name: string, panelType: string, panelParams: Record<string, any>) => Promise<SavedView>;
  deleteView: (id: string) => Promise<void>;
  setShared: (id: string, isShared: boolean) => Promise<void>;
}

/** Key the old per-browser store persisted under (zustand `persist`). */
export const LEGACY_STORAGE_KEY = 'rf_saved_views';

function fromDto(v: SavedViewDto): SavedView {
  return {
    id: v.id,
    name: v.name,
    panelType: v.panel_type,
    panelParams: v.panel_params,
    isShared: v.is_shared,
    isOwner: v.is_owner,
    createdAt: v.created_at,
  };
}

/** Views saved before the server table existed, if this browser has any. */
function readLegacyViews(): { name: string; panelType: string; panelParams: Record<string, any> }[] {
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return [];
    const views = JSON.parse(raw)?.state?.views;
    return Array.isArray(views)
      ? views.filter((v) => v && typeof v.name === 'string' && typeof v.panelType === 'string')
      : [];
  } catch {
    return [];
  }
}

/** Upload this browser's pre-server views once, then drop the local copy.
 * The key is removed only after every upload succeeded, so a failed run is
 * retried on the next load rather than losing views. */
async function uploadLegacyViews(): Promise<void> {
  const legacy = readLegacyViews();
  if (!legacy.length) return;
  for (const v of legacy) {
    await createSavedView({
      name: v.name,
      panel_type: v.panelType,
      panel_params: shareablePanelParams(v.panelParams ?? {}),
    });
  }
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // Storage unavailable: nothing to clean up.
  }
}

let legacyMigration: Promise<void> | null = null;

/** Single-flight: concurrent loads (e.g. React StrictMode's double effect)
 * share one upload instead of each re-uploading the same views. A failed
 * run clears the slot so the next load retries. */
function migrateLegacyViews(): Promise<void> {
  if (!legacyMigration) {
    legacyMigration = uploadLegacyViews().catch((e) => {
      legacyMigration = null;
      throw e;
    });
  }
  return legacyMigration;
}

/** Test hook: forget a finished migration so each test starts clean. */
export function resetLegacyMigrationForTests(): void {
  legacyMigration = null;
}

/** Named panel+filter combinations an analyst can reopen in one click.
 * Stored server-side (`/api/saved-views`) per user and company, optionally
 * shared with the rest of the company. */
export const useSavedViewsStore = create<SavedViewsState>()((set) => ({
  views: [],

  load: async () => {
    try {
      await migrateLegacyViews();
    } catch (e) {
      console.error('Saved views: legacy migration failed, will retry', e);
    }
    const views = await listSavedViews();
    set({ views: views.map(fromDto) });
  },

  saveView: async (name, panelType, panelParams) => {
    const created = fromDto(
      await createSavedView({
        name,
        panel_type: panelType,
        panel_params: shareablePanelParams(panelParams),
      }),
    );
    set((state) => ({ views: [...state.views, created] }));
    return created;
  },

  deleteView: async (id) => {
    await deleteSavedView(id);
    set((state) => ({ views: state.views.filter((v) => v.id !== id) }));
  },

  setShared: async (id, isShared) => {
    const updated = fromDto(await updateSavedView(id, { is_shared: isShared }));
    set((state) => ({ views: state.views.map((v) => (v.id === id ? updated : v)) }));
  },
}));
