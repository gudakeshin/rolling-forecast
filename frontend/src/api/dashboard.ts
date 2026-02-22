import { apiGet, apiPost } from './client';

// ─── Dashboard API Module ────────────────────────────────

export interface DashboardSummary {
  version_id: string;
  version_name: string;
  total_p50: number;
  total_p10: number;
  total_p90: number;
  total_line_items: number;
  override_count: number;
  confidence_high: number;
  confidence_medium: number;
  confidence_low: number;
}

export interface AccuracyOverview {
  avg_mape: number;
  median_mape: number;
  avg_bias: number;
  hit_rate: number;
  total_comparisons: number;
}

export interface ReviewAction {
  item_id: string;
  action: 'approve' | 'reject';
  comment?: string;
}

export interface DriverSubmission {
  version_id: string;
  values: Record<string, { value: number; source: string }>;
  notes?: string;
}

// ─── Executive Dashboard ──────────────────────────────────

export async function getExecutiveSummary(versionId: string): Promise<any> {
  return apiGet(`/panel/executive-dashboard/${versionId}`);
}

// ─── Review Dashboard ─────────────────────────────────────

export async function getReviewSummary(versionId: string): Promise<any> {
  return apiGet(`/panel/review-dashboard/${versionId}`);
}

export async function submitReviewAction(action: ReviewAction): Promise<any> {
  return apiPost('/panel/review-item', action);
}

export async function batchReviewActions(actions: ReviewAction[]): Promise<any> {
  return apiPost('/panel/batch-review', { actions });
}

export async function acceptAiRecommendations(versionId: string): Promise<any> {
  return apiPost('/panel/accept-ai-recommendations', { version_id: versionId });
}

export async function rescoreForecasts(versionId: string): Promise<any> {
  return apiPost(`/panel/rescore-forecasts/${versionId}`, {});
}

export async function rescoreAll(): Promise<any> {
  return apiPost('/panel/rescore-all', {});
}

// ─── Accuracy Tracking ───────────────────────────────────

export async function getAccuracySummary(versionId: string): Promise<any> {
  return apiGet(`/panel/accuracy-tracking/${versionId}`);
}

// ─── Driver Inputs ────────────────────────────────────────

export async function getDriverInputsSummary(versionId: string): Promise<any> {
  return apiGet(`/panel/driver-inputs/${versionId}`);
}

export async function submitDriverInputs(submission: DriverSubmission): Promise<any> {
  return apiPost('/panel/driver-inputs/submit', submission);
}
