import { apiDelete, apiGet, apiPost, apiPut } from './client';

export interface ModelPreset {
  id: string;
  name: string;
  description: string | null;
  model_type: string;
  candidate_models: string[] | null;
  default_horizon_months: number | null;
  is_active: boolean;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
}

export interface AvailableModel {
  name: string;
  display_label: string;
  auto_selectable: boolean;
  is_benchmark: boolean;
  cost_class: string;
}

export interface ModelPresetInput {
  name: string;
  description?: string | null;
  model_type?: string;
  candidate_models?: string[] | null;
  default_horizon_months?: number | null;
}

export interface RunForecastInput {
  dataset_id?: string | null;
  horizon_months?: number | null;
  scenario?: string;
  async_job?: boolean;
}

export async function listModelPresets(includeInactive = false): Promise<ModelPreset[]> {
  const qs = includeInactive ? '?include_inactive=true' : '';
  const res = await apiGet<{ presets: ModelPreset[]; count: number }>(`/model-presets${qs}`);
  return res.presets;
}

export async function getAvailableModels(): Promise<AvailableModel[]> {
  const res = await apiGet<{ models: AvailableModel[] }>('/model-presets/available-models');
  return res.models;
}

export async function createModelPreset(body: ModelPresetInput): Promise<ModelPreset> {
  return apiPost<ModelPreset>('/model-presets', body);
}

export async function updateModelPreset(
  id: string,
  body: Partial<ModelPresetInput>,
): Promise<ModelPreset> {
  return apiPut<ModelPreset>(`/model-presets/${id}`, body);
}

export async function deactivateModelPreset(
  id: string,
): Promise<{ id: string; is_active: boolean }> {
  const result = await apiDelete<{ id: string; is_active: boolean }>(`/model-presets/${id}`);
  if (!result) throw new Error('Failed to deactivate model preset');
  return result;
}

export interface RunForecastResult {
  success: boolean;
  message: string;
  // When queued asynchronously: {job_id, status: "queued", ...}. When run
  // synchronously (no async worker configured, or async_job=false), this is
  // instead the finished result, e.g. {version_id, name, ...}.
  data: { job_id?: string; status?: string; version_id?: string; [key: string]: any };
  warnings: string[];
}

export async function runForecastWithPreset(
  presetId: string,
  body: RunForecastInput = {},
): Promise<RunForecastResult> {
  return apiPost(`/model-presets/${presetId}/run`, body);
}
