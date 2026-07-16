import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User, TokenResponse } from '../types/auth';
import { apiGet } from '../api/client';

export type Capability =
  | 'can_input'
  | 'can_generate'
  | 'can_override'
  | 'can_review'
  | 'can_publish'
  | 'can_admin';

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (response: TokenResponse) => void;
  setTokens: (access: string, refresh: string | null) => void;
  setUser: (user: User) => void;
  logout: () => void;
  /** Fetch /auth/me and hydrate capability flags. */
  fetchMe: () => Promise<User | null>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,

      login: (response: TokenResponse) => {
        set({
          token: response.access_token,
          refreshToken: response.refresh_token ?? null,
          isAuthenticated: true,
          user: {
            id: response.user_id,
            username: response.username,
            email: '',
            full_name: response.username,
            business_unit: null,
            role_name: response.role,
            is_active: true,
          },
        });
      },

      setTokens: (access, refresh) => {
        set({
          token: access,
          refreshToken: refresh,
          isAuthenticated: Boolean(access),
        });
      },

      setUser: (user: User) => {
        set({ user });
      },

      logout: () => {
        const access = get().token;
        const refresh = get().refreshToken;
        // Best-effort server revoke — raw fetch avoids client↔store import cycles
        if (access || refresh) {
          void fetch('/api/auth/logout', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(access ? { Authorization: `Bearer ${access}` } : {}),
            },
            body: JSON.stringify({ refresh_token: refresh }),
          }).catch(() => undefined);
        }
        set({ token: null, refreshToken: null, user: null, isAuthenticated: false });
      },

      fetchMe: async () => {
        if (!get().token) return null;
        try {
          const me = await apiGet<User>('/auth/me');
          set({ user: me, isAuthenticated: true });
          return me;
        } catch {
          return null;
        }
      },
    }),
    {
      name: 'forecast-auth',
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);

/** Selector hook — hide/disable UI only; server remains authoritative. */
export function useCan(perm: Capability): boolean {
  return useAuthStore((s) => Boolean(s.user?.[perm]));
}
