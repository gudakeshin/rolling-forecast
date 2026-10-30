import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LEGACY_STORAGE_KEY, resetLegacyMigrationForTests, useSavedViewsStore } from './savedViewsStore';
import type { SavedViewDto } from '../api/savedViews';

vi.mock('../api/savedViews', () => ({
  listSavedViews: vi.fn(),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}));

import { createSavedView, deleteSavedView, listSavedViews, updateSavedView } from '../api/savedViews';

function dto(partial: Partial<SavedViewDto> & { id: string }): SavedViewDto {
  return {
    name: 'View',
    panel_type: 'variance',
    panel_params: {},
    is_shared: false,
    is_owner: true,
    created_at: '2026-10-29T00:00:00Z',
    ...partial,
  };
}

function setLegacy(views: unknown[]) {
  localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify({ state: { views }, version: 0 }));
}

describe('savedViewsStore', () => {
  beforeEach(() => {
    useSavedViewsStore.setState({ views: [] });
    localStorage.clear();
    resetLegacyMigrationForTests();
    vi.mocked(listSavedViews).mockReset();
    vi.mocked(createSavedView).mockReset();
    vi.mocked(updateSavedView).mockReset();
    vi.mocked(deleteSavedView).mockReset();
  });

  it('loads views from the server and maps them', async () => {
    vi.mocked(listSavedViews).mockResolvedValue([dto({ id: 'a', name: 'Mine', is_shared: true })]);
    await useSavedViewsStore.getState().load();
    const [v] = useSavedViewsStore.getState().views;
    expect(v).toMatchObject({ id: 'a', name: 'Mine', panelType: 'variance', isShared: true, isOwner: true });
  });

  it('uploads legacy browser views once, then clears them', async () => {
    setLegacy([{ id: 'old', name: 'Old view', panelType: 'forecast_table', panelParams: { category: 'Revenue' } }]);
    vi.mocked(createSavedView).mockResolvedValue(dto({ id: 'new' }));
    vi.mocked(listSavedViews).mockResolvedValue([dto({ id: 'new', name: 'Old view' })]);

    await useSavedViewsStore.getState().load();

    expect(createSavedView).toHaveBeenCalledWith({
      name: 'Old view',
      panel_type: 'forecast_table',
      panel_params: { category: 'Revenue' },
    });
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull();
    expect(useSavedViewsStore.getState().views.map((v) => v.id)).toEqual(['new']);
  });

  it('uploads legacy views only once when loads overlap', async () => {
    setLegacy([{ id: 'old', name: 'Old view', panelType: 'forecast_table', panelParams: {} }]);
    vi.mocked(createSavedView).mockResolvedValue(dto({ id: 'new' }));
    vi.mocked(listSavedViews).mockResolvedValue([dto({ id: 'new' })]);

    await Promise.all([useSavedViewsStore.getState().load(), useSavedViewsStore.getState().load()]);

    expect(createSavedView).toHaveBeenCalledTimes(1);
  });

  it('keeps legacy views when the upload fails, so the next load retries', async () => {
    setLegacy([{ id: 'old', name: 'Old view', panelType: 'forecast_table', panelParams: {} }]);
    vi.mocked(createSavedView).mockRejectedValue(new Error('offline'));
    vi.mocked(listSavedViews).mockResolvedValue([]);
    vi.spyOn(console, 'error').mockImplementation(() => {});

    await useSavedViewsStore.getState().load();
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).not.toBeNull();

    // The next load retries and succeeds.
    vi.mocked(createSavedView).mockResolvedValue(dto({ id: 'new' }));
    await useSavedViewsStore.getState().load();
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull();
  });

  it('ignores malformed legacy storage', async () => {
    localStorage.setItem(LEGACY_STORAGE_KEY, '{not json');
    vi.mocked(listSavedViews).mockResolvedValue([]);
    await useSavedViewsStore.getState().load();
    expect(createSavedView).not.toHaveBeenCalled();
  });

  it('saves, shares and deletes through the API', async () => {
    vi.mocked(createSavedView).mockResolvedValue(dto({ id: 'x', name: 'Q3' }));
    const saved = await useSavedViewsStore.getState().saveView('Q3', 'variance', { period: '2026-09' });
    expect(saved.id).toBe('x');
    expect(useSavedViewsStore.getState().views).toHaveLength(1);

    vi.mocked(updateSavedView).mockResolvedValue(dto({ id: 'x', name: 'Q3', is_shared: true }));
    await useSavedViewsStore.getState().setShared('x', true);
    expect(updateSavedView).toHaveBeenCalledWith('x', { is_shared: true });
    expect(useSavedViewsStore.getState().views[0].isShared).toBe(true);

    vi.mocked(deleteSavedView).mockResolvedValue();
    await useSavedViewsStore.getState().deleteView('x');
    expect(useSavedViewsStore.getState().views).toEqual([]);
  });

  it('leaves state unchanged when a server call fails', async () => {
    useSavedViewsStore.setState({
      views: [{ id: 'x', name: 'Q3', panelType: 'variance', panelParams: {}, isShared: false, isOwner: true, createdAt: null }],
    });
    vi.mocked(deleteSavedView).mockRejectedValue(new Error('403'));
    await expect(useSavedViewsStore.getState().deleteView('x')).rejects.toThrow('403');
    expect(useSavedViewsStore.getState().views).toHaveLength(1);
  });
});
