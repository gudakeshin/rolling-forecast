import { apiGet, apiPost, apiPut } from './client';
import type { ForecastVersion, ForecastLineResult } from '../types/forecast';

// ─── Forecast Versions ───────────────────────────────────

export async function getVersions(scenario?: string): Promise<ForecastVersion[]> {
  const qs = scenario ? `?scenario=${encodeURIComponent(scenario)}` : '';
  return apiGet<ForecastVersion[]>(`/panel/versions${qs}`);
}

export async function getVersion(versionId: string): Promise<ForecastVersion> {
  return apiGet<ForecastVersion>(`/panel/version/${versionId}`);
}

export async function getLatestVersion(): Promise<ForecastVersion | null> {
  const versions = await getVersions();
  return versions.length > 0 ? versions[0] : null;
}

// ─── Panel Data ──────────────────────────────────────────

export async function getForecastTable(versionId: string, filters?: {
  period?: string;
  category?: string;
  confidence_level?: string;
}): Promise<any> {
  const params = new URLSearchParams();
  if (filters?.period) params.set('period', filters.period);
  if (filters?.category) params.set('category', filters.category);
  if (filters?.confidence_level) params.set('confidence_level', filters.confidence_level);
  const qs = params.toString();
  return apiGet(`/panel/forecast-table/${versionId}${qs ? `?${qs}` : ''}`);
}

export async function getReviewQueue(versionId: string): Promise<any> {
  return apiGet(`/panel/review-queue/${versionId}`);
}

export async function getOverrides(versionId: string): Promise<any> {
  return apiGet(`/panel/overrides/${versionId}`);
}

export async function getComparison(versionA: string, versionB: string): Promise<any> {
  return apiGet(`/panel/comparison/${versionA}/${versionB}`);
}

// ─── Dashboard Data ──────────────────────────────────────

export async function getExecutiveDashboard(versionId: string): Promise<any> {
  return apiGet(`/panel/executive-dashboard/${versionId}`);
}

export async function getReviewDashboard(versionId: string): Promise<any> {
  return apiGet(`/panel/review-dashboard/${versionId}`);
}

export async function getAccuracyTracking(versionId: string): Promise<any> {
  return apiGet(`/panel/accuracy-tracking/${versionId}`);
}

export async function getDriverInputs(versionId: string): Promise<any> {
  return apiGet(`/panel/driver-inputs/${versionId}`);
}

// ─── Skills ─────────────────────────────────────────────

export async function getSkills(): Promise<any[]> {
  return apiGet<any[]>('/skills/');
}

export async function getSkillDefinitions(): Promise<any[]> {
  return apiGet<any[]>('/skills/definitions');
}

export async function getSkill(skillName: string): Promise<any> {
  return apiGet(`/skills/${skillName}`);
}

export async function updateSkill(skillName: string, content: string): Promise<any> {
  return apiPut(`/skills/${skillName}`, { content });
}

export async function reloadSkills(): Promise<any> {
  return apiPost('/skills/reload', {});
}
