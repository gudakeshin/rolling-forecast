import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { tabScopedTokenStorage } from './sessionToken';
import type { User, TokenResponse } from '../types/auth';
import { apiGet } from '../api/client';

export type Capability =
  | 'can_input'
  | 'can_generate'
  | 'can_override'
  | 'can_review'
  | 'can_publish'
  | 'can_admin'
  | 'can_manage_drivers';

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (response: TokenResponse) => void;
  /** Replace this tab's access token. The refresh token never reaches
   * script: it lives in an httpOnly cookie. */
  setTokens: (access: string) => void;
  setUser: (user: User) => void;
  logout: () => void;
  /** Fetch /auth/me and hydrate capability flags. */
  fetchMe: () => Promise<User | null>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      isAuthenticated: false,

      login: (response: TokenResponse) => {
        set({
          token: response.access_token,
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

      setTokens: (access) => {
        set({
          token: access,
          isAuthenticated: Boolean(access),
        });
      },

      setUser: (user: User) => {
        set({ user });
      },

      logout: () => {
        const access = get().token;
        // Best-effort server revoke (the refresh cookie is sent automatically
        // and cleared by the response). Raw fetch avoids client↔store cycles.
        void fetch('/api/auth/logout', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-Auth-Cookie': '1',
            ...(access ? { Authorization: `Bearer ${access}` } : {}),
          },
          body: JSON.stringify({}),
        }).catch(() => undefined);
        set({ token: null, user: null, isAuthenticated: false });
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
      storage: createJSONStorage(() => tabScopedTokenStorage),
      partialize: (state) => ({
        token: state.token,
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
