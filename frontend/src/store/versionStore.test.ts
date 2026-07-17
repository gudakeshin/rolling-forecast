import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useVersionStore, versionsByScenario } from './versionStore';
import type { ForecastVersion } from '../types/forecast';

vi.mock('../api/forecast', () => ({
  getVersions: vi.fn(),
}));

import { getVersions } from '../api/forecast';

function makeVersion(partial: Partial<ForecastVersion> & { id: string; name: string }): ForecastVersion {
  return {
    status: 'draft',
    version_type: 'baseline',
    horizon_months: 12,
    scenario: 'base',
    created_at: '2026-01-01T00:00:00Z',
    ...partial,
  } as ForecastVersion;
}

describe('versionStore', () => {
  beforeEach(() => {
    useVersionStore.setState({
      versions: [],
      activeVersionId: null,
      activeScenario: 'base',
      isLoading: false,
      error: null,
    });
    vi.mocked(getVersions).mockReset();
  });

  it('groups versions by scenario', () => {
    const versions = [
      makeVersion({ id: 'a', name: 'Base A', scenario: 'base' }),
      makeVersion({ id: 'b', name: 'Upside', scenario: 'upside' }),
      makeVersion({ id: 'c', name: 'Base B', scenario: 'base' }),
    ];
    const grouped = versionsByScenario(versions);
    expect(grouped.base).toHaveLength(2);
    expect(grouped.upside).toHaveLength(1);
  });

  it('hydrates and selects preferred version for active scenario', async () => {
    vi.mocked(getVersions).mockResolvedValue([
      makeVersion({ id: 'up-1', name: 'Upside', scenario: 'upside' }),
      makeVersion({ id: 'base-1', name: 'Base', scenario: 'base' }),
    ]);
    useVersionStore.setState({ activeScenario: 'base', activeVersionId: null });

    await useVersionStore.getState().hydrate();

    const state = useVersionStore.getState();
    expect(state.versions).toHaveLength(2);
    expect(state.activeVersionId).toBe('base-1');
    expect(state.activeScenario).toBe('base');
  });

  it('keeps a still-valid active version across hydrate', async () => {
    vi.mocked(getVersions).mockResolvedValue([
      makeVersion({ id: 'up-1', name: 'Upside', scenario: 'upside' }),
      makeVersion({ id: 'base-1', name: 'Base', scenario: 'base' }),
    ]);
    useVersionStore.setState({
      activeScenario: 'upside',
      activeVersionId: 'up-1',
    });

    await useVersionStore.getState().hydrate();
    expect(useVersionStore.getState().activeVersionId).toBe('up-1');
  });

  it('switches scenario and picks first version in that scenario', () => {
    useVersionStore.setState({
      versions: [
        makeVersion({ id: 'base-1', name: 'Base', scenario: 'base' }),
        makeVersion({ id: 'up-1', name: 'Upside', scenario: 'upside' }),
      ],
      activeVersionId: 'base-1',
      activeScenario: 'base',
    });

    useVersionStore.getState().setActiveScenario('upside');
    expect(useVersionStore.getState().activeScenario).toBe('upside');
    expect(useVersionStore.getState().activeVersionId).toBe('up-1');
  });
});
