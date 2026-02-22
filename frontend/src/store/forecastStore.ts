import { create } from 'zustand';
import type { ForecastVersion } from '../types/forecast';
import { getVersions, getVersion } from '../api/forecast';

interface ForecastState {
  // Active version
  activeVersionId: string | null;
  activeVersion: ForecastVersion | null;
  versions: ForecastVersion[];

  // Loading states
  isLoadingVersions: boolean;
  error: string | null;

  // Actions
  setActiveVersionId: (id: string | null) => void;
  setActiveVersion: (version: ForecastVersion | null) => void;
  setVersions: (versions: ForecastVersion[]) => void;
  addVersion: (version: ForecastVersion) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // Fetch actions
  fetchVersions: () => Promise<void>;
  fetchVersion: (versionId: string) => Promise<void>;
  fetchAndSetLatest: () => Promise<void>;
}

export const useForecastStore = create<ForecastState>((set, get) => ({
  activeVersionId: null,
  activeVersion: null,
  versions: [],
  isLoadingVersions: false,
  error: null,

  setActiveVersionId: (id) => set({ activeVersionId: id }),

  setActiveVersion: (version) =>
    set({ activeVersion: version, activeVersionId: version?.id || null }),

  setVersions: (versions) => set({ versions, isLoadingVersions: false }),

  addVersion: (version) =>
    set((state) => ({
      versions: [version, ...state.versions],
    })),

  setLoading: (loading) => set({ isLoadingVersions: loading }),

  setError: (error) => set({ error }),

  fetchVersions: async () => {
    set({ isLoadingVersions: true, error: null });
    try {
      const versions = await getVersions();
      set({ versions, isLoadingVersions: false });
    } catch (error: any) {
      set({ error: error.message, isLoadingVersions: false });
    }
  },

  fetchVersion: async (versionId: string) => {
    set({ isLoadingVersions: true, error: null });
    try {
      const version = await getVersion(versionId);
      set({ activeVersion: version, activeVersionId: version.id, isLoadingVersions: false });
    } catch (error: any) {
      set({ error: error.message, isLoadingVersions: false });
    }
  },

  fetchAndSetLatest: async () => {
    set({ isLoadingVersions: true, error: null });
    try {
      const versions = await getVersions();
      const latest = versions.length > 0 ? versions[0] : null;
      set({
        versions,
        activeVersion: latest,
        activeVersionId: latest?.id || null,
        isLoadingVersions: false,
      });
    } catch (error: any) {
      set({ error: error.message, isLoadingVersions: false });
    }
  },
}));
