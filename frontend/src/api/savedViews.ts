import { apiDelete, apiGet, apiPost } from './client';

export interface SavedView {
  id: string;
  name: string;
  panel_type: string;
  panel_params: Record<string, any>;
  business_unit_id: string;
  created_at: string | null;
}

export interface SavedViewInput {
  name: string;
  panel_type: string;
  panel_params: Record<string, any>;
}

export async function listSavedViews(): Promise<SavedView[]> {
  const res = await apiGet<{ views: SavedView[]; count: number }>('/saved-views');
  return res.views;
}

export async function createSavedView(body: SavedViewInput): Promise<SavedView> {
  return apiPost<SavedView>('/saved-views', body);
}

export async function deleteSavedView(id: string): Promise<{ id: string; deleted: boolean }> {
  const result = await apiDelete<{ id: string; deleted: boolean }>(`/saved-views/${id}`);
  if (!result) throw new Error('Failed to delete saved view');
  return result;
}
