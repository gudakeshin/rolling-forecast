import { apiDelete, apiGet, apiPatch, apiPost } from './client';

export interface SavedViewDto {
  id: string;
  name: string;
  panel_type: string;
  panel_params: Record<string, any>;
  is_shared: boolean;
  is_owner: boolean;
  created_at: string | null;
}

export interface SavedViewInput {
  name: string;
  panel_type: string;
  panel_params: Record<string, any>;
  is_shared?: boolean;
}

export async function listSavedViews(): Promise<SavedViewDto[]> {
  const res = await apiGet<{ views: SavedViewDto[] }>('/saved-views');
  return res.views;
}

export async function createSavedView(body: SavedViewInput): Promise<SavedViewDto> {
  return apiPost<SavedViewDto>('/saved-views', body);
}

export async function updateSavedView(
  id: string,
  body: { name?: string; is_shared?: boolean },
): Promise<SavedViewDto> {
  return apiPatch<SavedViewDto>(`/saved-views/${id}`, body);
}

export async function deleteSavedView(id: string): Promise<void> {
  await apiDelete(`/saved-views/${id}`);
}
