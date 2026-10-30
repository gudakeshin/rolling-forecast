import { apiDelete, apiGet, apiPost } from './client';

export type KpiDisplayFormat = 'number' | 'currency' | 'percent' | 'ratio';

export interface KpiDefinition {
  id: string;
  business_unit_id: string;
  name: string;
  formula: string;
  display_format: KpiDisplayFormat;
  description: string | null;
}

export interface KpiResult {
  id: string;
  name: string;
  formula: string;
  display_format: KpiDisplayFormat;
  description: string | null;
  total: number | null;
  by_period: { period: string; value: number | null }[];
  missing: string[];
  error: string | null;
}

export interface KpiInput {
  name: string;
  formula: string;
  display_format: KpiDisplayFormat;
  description?: string | null;
}

export type FormulaCheck =
  | { valid: true; references: { reference: string; kind: 'item' | 'category' }[] }
  | { valid: false; error: string };

export async function evaluateKpis(versionId: string): Promise<KpiResult[]> {
  const res = await apiGet<{ kpis: KpiResult[] }>(`/kpis/evaluate/${versionId}`);
  return res.kpis;
}

export async function validateKpiFormula(formula: string): Promise<FormulaCheck> {
  return apiPost<FormulaCheck>('/kpis/validate', { formula });
}

export async function createKpi(body: KpiInput): Promise<KpiDefinition> {
  return apiPost<KpiDefinition>('/kpis', body);
}

export async function deleteKpi(id: string): Promise<void> {
  await apiDelete(`/kpis/${id}`);
}
