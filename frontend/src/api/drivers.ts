import { apiGet, apiPatch, apiPost, uploadFile } from './client';

export interface DriverFreshness {
  driver_id: number;
  last_period: string | null;
  n_periods: number;
  stale: boolean;
  days_since?: number | null;
}

export interface Driver {
  id: number;
  key: string;
  name: string;
  driver_type: string;
  unit?: string | null;
  currency?: string | null;
  aggregation: string;
  business_unit?: string | null;
  geography?: string | null;
  product_line?: string | null;
  description?: string | null;
  source?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  freshness?: DriverFreshness;
}

export interface DriverLink {
  id: number;
  driver_id: number;
  line_item_id: number;
  link_type: string;
  relation: string;
  lag: number;
  coefficient?: number | null;
  elasticity?: number | null;
  p_value_adj?: number | null;
  r2?: number | null;
  n_obs?: number | null;
  fit_method?: string | null;
  discovery_run_id?: string | null;
  status: string;
  composition_group?: string | null;
  notes?: string | null;
}

export interface DiscoveryCandidate {
  driver_id: number;
  driver_key: string;
  driver_type: string;
  lag: number;
  n_obs: number;
  coefficient?: number | null;
  elasticity?: number | null;
  p_value?: number | null;
  p_value_adj?: number | null;
  diff_p_value_adj?: number | null;
  placebo_p?: number | null;
  r2?: number | null;
  passed: boolean;
  reject_reason?: string | null;
}

export interface DiscoverySummary {
  reason?: string;
  n_tests?: number;
  n_survivors?: number;
  family_size?: number;
  max_lag?: number;
  min_overlap?: number;
  superseded_links?: number;
  candidates?: DiscoveryCandidate[];
}

export interface DiscoveryRun {
  id: string;
  line_item_id: number | null;
  status: string;
  config?: Record<string, unknown> | null;
  summary?: DiscoverySummary | null;
  created_by?: string | null;
  created_at?: string | null;
  links: DriverLink[];
}

export interface DiscoveryRunPayload {
  line_item_id: number;
  max_lag?: number;
  alpha?: number;
  max_survivors?: number;
  enable_placebo?: boolean;
  placebo_draws?: number;
  driver_ids?: number[];
}

export interface DriverCreatePayload {
  key: string;
  name: string;
  driver_type?: string;
  unit?: string;
  currency?: string;
  aggregation?: string;
  business_unit?: string;
  description?: string;
  source?: string;
}

export async function listDrivers(opts?: {
  driver_type?: string;
  include_freshness?: boolean;
}): Promise<Driver[]> {
  const qs = new URLSearchParams();
  if (opts?.driver_type) qs.set('driver_type', opts.driver_type);
  if (opts?.include_freshness) qs.set('include_freshness', 'true');
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiGet(`/drivers${suffix}`);
}

export async function createDriver(payload: DriverCreatePayload): Promise<Driver> {
  return apiPost('/drivers', payload);
}

export async function updateDriver(
  id: number,
  payload: Partial<DriverCreatePayload>,
): Promise<Driver> {
  return apiPatch(`/drivers/${id}`, payload);
}

export async function listDriverLinks(driverId: number): Promise<DriverLink[]> {
  return apiGet(`/drivers/${driverId}/links`);
}

export async function createDriverLink(payload: {
  driver_id: number;
  line_item_id: number;
  relation?: string;
  lag?: number;
  coefficient?: number;
  status?: string;
  composition_group?: string;
  notes?: string;
}): Promise<DriverLink> {
  return apiPost('/drivers/links', payload);
}

export async function promoteDriverLink(linkId: number): Promise<DriverLink> {
  return apiPost(`/drivers/links/${linkId}/promote`, {});
}

/** Phase 9 — statistical discovery. Only ever produces candidate links. */
export async function runDiscovery(payload: DiscoveryRunPayload): Promise<DiscoveryRun> {
  return apiPost('/drivers/discovery/run', payload);
}

export async function getDiscovery(runId: string): Promise<DiscoveryRun> {
  return apiGet(`/drivers/discovery/${runId}`);
}

export async function uploadDriversFile(file: File): Promise<any> {
  return uploadFile('/upload/drivers?ingest=true', file);
}
