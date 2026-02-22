import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User, TokenResponse } from '../types/auth';

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (response: TokenResponse) => void;
  setUser: (user: User) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
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

      setUser: (user: User) => {
        set({ user });
      },

      logout: () => {
        set({ token: null, user: null, isAuthenticated: false });
      },
    }),
    {
      name: 'forecast-auth',
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
