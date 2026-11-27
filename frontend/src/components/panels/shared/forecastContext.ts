/**
 * Shapes of the per-line context the backend attaches to forecast and review
 * rows (app/api/dashboard/_common.py builds the driver context). Shared by
 * the forecast table and the review dashboard, which used to declare them twice.
 */

export interface AIAction {
  type: string;
  label: string;
  detail: string;
}

export interface DriverDependency {
  name: string;
  category: string;
  relationship: string;
  weight: number;
  forecast_total: number;
}

export interface ActualsTrend {
  periods: string[];
  values: number[];
  direction: string;
  direction_pct: number;
  mom_growth_pct: number | null;
  yoy_growth_pct: number | null;
  recent_avg: number;
  last_actual: number;
  last_period: string;
}

export interface DriverInput {
  business_unit: string;
  field: string;
  value: number | null;
  reason: string;
  prior_value: number | null;
  submitted_at: string | null;
}

export interface ActiveOverride {
  period: string;
  original_value: number;
  override_value: number;
  reason: string;
  delta_pct: number;
}

export interface DriverContext {
  narrative: string;
  dependencies: DriverDependency[];
  actuals_trend: ActualsTrend | null;
  driver_inputs: DriverInput[];
  active_overrides: ActiveOverride[];
}

export interface ExogModeScores {
  n_folds: number;
  mase_forecast: number | null;
  mase_known: number | null;
}

export interface ExogInfo {
  mode?: string;
  columns?: string[];
  drivers?: Array<{
    driver_id?: number;
    driver_key?: string;
    relation?: string;
    lag?: number;
  }>;
  exog_mode_scores?: ExogModeScores | null;
}
