import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ForecastVersion } from '../types/forecast';
import { getVersions } from '../api/forecast';

interface VersionState {
  versions: ForecastVersion[];
  activeVersionId: string | null;
  activeScenario: string;
  isLoading: boolean;
  error: string | null;

  setActiveVersionId: (id: string | null) => void;
  setActiveScenario: (scenario: string) => void;
  hydrate: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const useVersionStore = create<VersionState>()(
  persist(
    (set, get) => ({
      versions: [],
      activeVersionId: null,
      activeScenario: 'base',
      isLoading: false,
      error: null,

      setActiveVersionId: (id) => {
        const v = get().versions.find((x) => x.id === id);
        set({
          activeVersionId: id,
          ...(v?.scenario ? { activeScenario: v.scenario } : {}),
        });
      },

      setActiveScenario: (scenario) => {
        const inScenario = get().versions.filter(
          (v) => (v.scenario || 'base') === scenario,
        );
        const keep =
          get().activeVersionId &&
          inScenario.some((v) => v.id === get().activeVersionId);
        set({
          activeScenario: scenario,
          activeVersionId: keep
            ? get().activeVersionId
            : inScenario[0]?.id ?? get().activeVersionId,
        });
      },

      hydrate: async () => {
        set({ isLoading: true, error: null });
        try {
          const versions = await getVersions();
          const current = get().activeVersionId;
          const scenario = get().activeScenario || 'base';
          const stillValid = current && versions.some((v) => v.id === current);
          const preferred =
            versions.find((v) => (v.scenario || 'base') === scenario) ||
            versions[0] ||
            null;
          set({
            versions,
            activeVersionId: stillValid ? current : preferred?.id ?? null,
            activeScenario: stillValid
              ? versions.find((v) => v.id === current)?.scenario || scenario
              : preferred?.scenario || scenario,
            isLoading: false,
          });
        } catch (e: any) {
          set({ error: e?.message || 'Failed to load versions', isLoading: false });
        }
      },

      refresh: async () => get().hydrate(),
    }),
    {
      name: 'rf_version_store',
      partialize: (s) => ({
        activeVersionId: s.activeVersionId,
        activeScenario: s.activeScenario,
      }),
    },
  ),
);

export function useActiveVersion(): ForecastVersion | null {
  return useVersionStore((s) => s.versions.find((v) => v.id === s.activeVersionId) ?? null);
}

export function versionsByScenario(versions: ForecastVersion[]): Record<string, ForecastVersion[]> {
  const out: Record<string, ForecastVersion[]> = {};
  for (const v of versions) {
    const key = v.scenario || 'base';
    (out[key] ||= []).push(v);
  }
  return out;
}
