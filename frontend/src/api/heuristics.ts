import { apiGet, apiPost } from './client';

export type HeuristicStatus = 'candidate' | 'active' | 'rejected' | 'superseded';
export type HeuristicKind = 'error_bias' | 'override_pattern';

export interface LearnedHeuristic {
  id: number;
  scope: string;
  line_item_id: number | null;
  category: string | null;
  model_type: string | null;
  horizon_bucket: string | null;
  kind: HeuristicKind;
  statement: string;
  effect_size: number | null;
  evidence?: Record<string, unknown> | null;
  status: HeuristicStatus;
  source: string | null;
  proposed_at: string | null;
  approved_by: string | null;
  review_by: string | null;
  /** Override-derived heuristics stay advisory — they never touch selection. */
  influences_selection: boolean;
  superseded_ids?: number[];
}

export interface HeuristicListResponse {
  heuristics: LearnedHeuristic[];
  count: number;
}

export interface ReflectionRunSummary {
  detected: number;
  error_bias: number;
  override_pattern: number;
  created: number;
  updated: number;
  skipped_active: number;
  min_cycles: number;
  max_candidates: number;
}

export async function listHeuristics(opts?: {
  status?: HeuristicStatus;
  kind?: HeuristicKind;
  limit?: number;
}): Promise<HeuristicListResponse> {
  const qs = new URLSearchParams();
  if (opts?.status) qs.set('status', opts.status);
  if (opts?.kind) qs.set('kind', opts.kind);
  if (opts?.limit) qs.set('limit', String(opts.limit));
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiGet(`/heuristics${suffix}`);
}

/** Reviewer/admin only — mines closed cycles and writes candidates. */
export async function runReflectionPass(payload?: {
  min_cycles?: number;
  max_candidates?: number;
  kinds?: HeuristicKind[];
}): Promise<ReflectionRunSummary> {
  return apiPost('/heuristics/run', payload || {});
}

export async function promoteHeuristic(id: number): Promise<LearnedHeuristic> {
  return apiPost(`/heuristics/${id}/promote`, {});
}

export async function rejectHeuristic(id: number): Promise<LearnedHeuristic> {
  return apiPost(`/heuristics/${id}/reject`, {});
}
