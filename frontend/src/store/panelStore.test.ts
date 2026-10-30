import { beforeEach, describe, expect, it } from 'vitest';
import { usePanelStore } from './panelStore';
import { useVersionStore } from './versionStore';

describe('panelStore', () => {
  beforeEach(() => {
    usePanelStore.setState({
      isOpen: false,
      panelType: null,
      panelParams: {},
      panelData: null,
      isLoading: false,
      widthMode: 'normal',
    });
    useVersionStore.setState({
      versions: [],
      activeVersionId: 'ver-active',
      activeScenario: 'base',
      isLoading: false,
      error: null,
    });
  });

  it('injects activeVersionId when openPanel omits version_id', () => {
    usePanelStore.getState().openPanel('overrides', {});
    const state = usePanelStore.getState();
    expect(state.isOpen).toBe(true);
    expect(state.panelType).toBe('overrides');
    expect(state.panelParams.version_id).toBe('ver-active');
    expect(state.isLoading).toBe(true);
  });

  it('preserves an explicit version_id over the active version', () => {
    usePanelStore.getState().openPanel('review_dashboard', { version_id: 'ver-explicit' });
    expect(usePanelStore.getState().panelParams.version_id).toBe('ver-explicit');
  });

  it('refreshPanel bumps _refresh so fetch effects re-run', () => {
    usePanelStore.getState().openPanel('accuracy_tracking', { version_id: 'ver-1' });
    usePanelStore.getState().refreshPanel();
    const params = usePanelStore.getState().panelParams;
    expect(params.version_id).toBe('ver-1');
    expect(typeof params._refresh).toBe('number');
    expect(params.offset).toBe(0);
    expect(usePanelStore.getState().isLoading).toBe(true);
  });

  it('closePanel clears type, params, and data', () => {
    usePanelStore.getState().openPanel('overrides', { version_id: 'ver-1' });
    usePanelStore.getState().setPanelData({
      panel_type: 'overrides',
      title: 'Overrides',
      data: { rows: [] },
    } as any);
    usePanelStore.getState().closePanel();
    const state = usePanelStore.getState();
    expect(state.isOpen).toBe(false);
    expect(state.panelType).toBeNull();
    expect(state.panelParams).toEqual({});
    expect(state.panelData).toBeNull();
  });
});
