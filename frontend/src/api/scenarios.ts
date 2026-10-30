import { apiGet, apiPost } from './client';

export interface DriverShock {
  driver_id: number;
  mode: 'pct' | 'absolute' | 'replace';
  value?: number | null;
  series?: Record<string, number> | null;
}

export interface WhatIfRequest {
  base_version_id: string;
  scenario_label: string;
  shocks: DriverShock[];
}

export interface WhatIfResult {
  scenario_version_id: string;
  base_version_id: string;
  scenario_label: string;
  affected_line_items: number;
  affected_line_periods: number;
  shocked_drivers: number;
  line_items?: Array<{ line_item_id: number; line_item: string }>;
}

export async function createWhatIf(body: WhatIfRequest): Promise<WhatIfResult> {
  return apiPost('/scenarios/what-if', body);
}

export async function getBudgetBridge(
  versionId: string,
  opts?: {
    attribute?: boolean;
    convention?: string;
    materiality_pct?: number;
    page?: number;
    page_size?: number;
  },
): Promise<any> {
  const qs = new URLSearchParams();
  if (opts?.attribute) qs.set('attribute', 'true');
  if (opts?.convention) qs.set('convention', opts.convention);
  if (opts?.materiality_pct != null) qs.set('materiality_pct', String(opts.materiality_pct));
  if (opts?.page != null) qs.set('page', String(opts.page));
  if (opts?.page_size != null) qs.set('page_size', String(opts.page_size));
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiGet(`/executive/budget-bridge/${versionId}${suffix}`);
}

export async function getDriverDrilldown(
  versionId: string,
  opts?: {
    line_item_id?: number;
    period_from?: string;
    period_to?: string;
    basis?: string;
    convention?: string;
  },
): Promise<any> {
  const qs = new URLSearchParams();
  if (opts?.line_item_id != null) qs.set('line_item_id', String(opts.line_item_id));
  if (opts?.period_from) qs.set('period_from', opts.period_from);
  if (opts?.period_to) qs.set('period_to', opts.period_to);
  if (opts?.basis) qs.set('basis', opts.basis);
  if (opts?.convention) qs.set('convention', opts.convention);
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiGet(`/executive/driver-drilldown/${versionId}${suffix}`);
}
