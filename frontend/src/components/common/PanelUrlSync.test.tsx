import { beforeEach, describe, expect, it } from 'vitest';
import { act, render } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { PanelUrlSync } from './PanelUrlSync';
import {
  pinPrimaryToSecondary,
  usePrimaryPanelStore,
  useSecondaryPanelStore,
  useWorkspaceLayoutStore,
} from '../../store/panelStore';

let currentSearch = '';
function LocationProbe() {
  currentSearch = useLocation().search;
  return null;
}

function mount(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <PanelUrlSync />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function resetPanels() {
  usePrimaryPanelStore.getState().closePanel();
  useSecondaryPanelStore.getState().closePanel();
  useWorkspaceLayoutStore.getState().setMaximizedSlot(null);
}

describe('panel URL sync (plan 4.2)', () => {
  beforeEach(resetPanels);

  it('a shared link reopens both panels with their filters', () => {
    const pp = encodeURIComponent(JSON.stringify({ version_id: 'v1', category: 'Revenue' }));
    const pp2 = encodeURIComponent(JSON.stringify({ version_id: 'v1' }));
    mount(`/?panel=forecast_table&pp=${pp}&panel2=explainability&pp2=${pp2}`);
    expect(usePrimaryPanelStore.getState().panelType).toBe('forecast_table');
    expect(usePrimaryPanelStore.getState().panelParams).toMatchObject({ category: 'Revenue' });
    expect(useSecondaryPanelStore.getState().panelType).toBe('explainability');
  });

  it('writes the open panel and its filters into the URL', () => {
    mount('/');
    act(() => usePrimaryPanelStore.getState().openPanel('forecast_table', { version_id: 'v2', category: 'COGS' }));
    const params = new URLSearchParams(currentSearch);
    expect(params.get('panel')).toBe('forecast_table');
    expect(JSON.parse(params.get('pp')!)).toMatchObject({ version_id: 'v2', category: 'COGS' });

    act(() => usePrimaryPanelStore.getState().closePanel());
    expect(new URLSearchParams(currentSearch).get('panel')).toBeNull();
  });

  it('ignores unknown panel types and malformed params', () => {
    mount('/?panel=not_a_panel&pp=%7Bbroken');
    expect(usePrimaryPanelStore.getState().isOpen).toBe(false);
  });
});

describe('workspace layout (plan 4.1)', () => {
  beforeEach(() => {
    resetPanels();
    localStorage.clear();
  });

  it('maximizes one slot to full width and toggles back', () => {
    const layout = useWorkspaceLayoutStore.getState();
    layout.toggleMaximize('primary');
    expect(useWorkspaceLayoutStore.getState().maximizedSlot).toBe('primary');
    layout.toggleMaximize('secondary');
    expect(useWorkspaceLayoutStore.getState().maximizedSlot).toBe('secondary');
    useWorkspaceLayoutStore.getState().toggleMaximize('secondary');
    expect(useWorkspaceLayoutStore.getState().maximizedSlot).toBeNull();
  });

  it('widens a panel and remembers the choice per slot', () => {
    usePrimaryPanelStore.getState().toggleWidth();
    expect(usePrimaryPanelStore.getState().widthMode).toBe('wide');
    expect(localStorage.getItem('rf_panel_width_mode')).toBe('wide');
    expect(useSecondaryPanelStore.getState().widthMode).toBe('normal');
  });

  it('pins the current panel beside, keeping its params', () => {
    usePrimaryPanelStore.getState().openPanel('forecast_table', { version_id: 'v3', category: 'OpEx' });
    pinPrimaryToSecondary();
    expect(useSecondaryPanelStore.getState().panelType).toBe('forecast_table');
    expect(useSecondaryPanelStore.getState().panelParams).toMatchObject({ category: 'OpEx' });
    expect(usePrimaryPanelStore.getState().isOpen).toBe(true);
  });
});
