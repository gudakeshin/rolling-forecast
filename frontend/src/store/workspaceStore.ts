import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiGet, apiPost } from '../api/client';
import { useAuthStore } from './authStore';
import type { CompanyMembership } from '../types/auth';

export interface WorkspaceOption {
  id: string;
  name: string;
}

interface WorkspaceState {
  /** The company whose data the app is currently scoped to. */
  currentBusinessUnitId: string | null;
  currentBusinessUnitName: string | null;
  /** Only populated for cross-company (can_view_all_bus) users -- every BU,
   * switched locally since can_view_all_bus already grants blanket access. */
  businessUnits: WorkspaceOption[];
  /** Only populated for a non-admin user with more than one real
   * CompanyMembership grant -- switching one of these actually changes the
   * server-side active company/role (POST /auth/switch-company). */
  companies: CompanyMembership[];
  isLoading: boolean;

  /** Cross-BU admin path only: local-only, no server mutation needed. */
  setCurrent: (id: string | null, name: string | null) => void;
  /** Real multi-company path: mutates the server's active company/role,
   * then re-hydrates the authenticated user (their role may have changed). */
  switchCompany: (companyId: string) => Promise<void>;
  hydrate: () => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      currentBusinessUnitId: null,
      currentBusinessUnitName: null,
      businessUnits: [],
      companies: [],
      isLoading: false,

      setCurrent: (id, name) => {
        set({ currentBusinessUnitId: id, currentBusinessUnitName: name });
      },

      switchCompany: async (companyId: string) => {
        set({ isLoading: true });
        try {
          await apiPost('/auth/switch-company', { company_id: companyId });
          // The switch can change the role, and a role can carry
          // can_view_all_bus (e.g. reviewer/publisher) -- re-hydrate from
          // the refreshed user rather than trusting the old branch (admin
          // vs membership list) still applies.
          await useAuthStore.getState().fetchMe();
          await get().hydrate();
        } finally {
          set({ isLoading: false });
        }
      },

      hydrate: async () => {
        const user = useAuthStore.getState().user;
        if (!user) return;

        // A single-company user's workspace is fixed by their account, not a
        // choice — always reflect it, ignoring any stale persisted pick.
        if (!user.can_view_all_bus && (!user.companies || user.companies.length <= 1)) {
          set({
            currentBusinessUnitId: user.business_unit_id ?? null,
            currentBusinessUnitName: user.business_unit ?? null,
            businessUnits: [],
            companies: [],
          });
          return;
        }

        // A non-admin user with more than one real CompanyMembership grant --
        // switcher backed by switchCompany(), not the admin BU list below.
        if (!user.can_view_all_bus && user.companies && user.companies.length > 1) {
          set({
            companies: user.companies,
            businessUnits: [],
            currentBusinessUnitId: user.business_unit_id ?? null,
            currentBusinessUnitName: user.business_unit ?? null,
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
            companies: [],
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
