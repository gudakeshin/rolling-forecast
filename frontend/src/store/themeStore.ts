/** Light/dark theme preference (persisted). */

import { create } from 'zustand';

export type ThemeMode = 'dark' | 'light';

const STORAGE_KEY = 'rf_theme';

function applyTheme(mode: ThemeMode) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', mode);
  document.documentElement.classList.toggle('dark', mode === 'dark');
  document.documentElement.classList.toggle('light', mode === 'light');
}

function readInitial(): ThemeMode {
  if (typeof localStorage === 'undefined') return 'dark';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'dark') {
    applyTheme(stored);
    return stored;
  }
  applyTheme('dark');
  return 'dark';
}

interface ThemeState {
  theme: ThemeMode;
  setTheme: (mode: ThemeMode) => void;
  toggleTheme: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readInitial(),
  setTheme: (mode) => {
    localStorage.setItem(STORAGE_KEY, mode);
    applyTheme(mode);
    set({ theme: mode });
  },
  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark';
    get().setTheme(next);
  },
}));
