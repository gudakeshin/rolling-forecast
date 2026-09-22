import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiGet } from '../api/client';
import { useAuthStore } from './authStore';

export interface WorkspaceOption {
  id: string;
  name: string;
}

interface WorkspaceState {
  /** The company whose data the app is currently scoped to. */
  currentBusinessUnitId: string | null;
  currentBusinessUnitName: string | null;
  /** Only populated for cross-company (can_view_all_bus) users. */
  businessUnits: WorkspaceOption[];
  isLoading: boolean;

  setCurrent: (id: string | null, name: string | null) => void;
  hydrate: () => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      currentBusinessUnitId: null,
      currentBusinessUnitName: null,
      businessUnits: [],
      isLoading: false,

      setCurrent: (id, name) => {
        set({ currentBusinessUnitId: id, currentBusinessUnitName: name });
      },

      hydrate: async () => {
        const user = useAuthStore.getState().user;
        if (!user) return;

        // A single-company user's workspace is fixed by their account, not a
        // choice — always reflect it, ignoring any stale persisted pick.
        if (!user.can_view_all_bus) {
          set({
            currentBusinessUnitId: user.business_unit_id ?? null,
            currentBusinessUnitName: user.business_unit ?? null,
            businessUnits: [],
          });
          return;
        }

        set({ isLoading: true });
        try {
          const units = await apiGet<WorkspaceOption[]>('/admin/business-units');
          const current = get().currentBusinessUnitId;
          const stillValid = current && units.some((u) => u.id === current);
          const fallback = stillValid
            ? units.find((u) => u.id === current) || null
            : units.find((u) => u.id === user.business_unit_id) || units[0] || null;
          set({
            businessUnits: units,
            currentBusinessUnitId: fallback?.id ?? null,
            currentBusinessUnitName: fallback?.name ?? null,
            isLoading: false,
          });
        } catch {
          set({ isLoading: false });
        }
      },
    }),
    {
      name: 'rf_workspace_store',
      partialize: (s) => ({
        currentBusinessUnitId: s.currentBusinessUnitId,
        currentBusinessUnitName: s.currentBusinessUnitName,
      }),
    },
  ),
);
