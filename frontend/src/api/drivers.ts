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
  status: string;
  composition_group?: string | null;
  notes?: string | null;
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

export async function uploadDriversFile(file: File): Promise<any> {
  return uploadFile('/upload/drivers?ingest=true', file);
}
