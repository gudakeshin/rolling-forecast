/** Row, version and summary shapes for the forecast table panel. */
import { type AIAction, type DriverContext, type ExogInfo } from './shared/forecastContext';

// ── Types ────────────────────────────────────
export interface QualitySummary {
  avg_confidence: number;
  critical_count: number;
  warning_count: number;
  ok_count: number;
  total_scored: number;
}

export interface CalibrationSummary {
  calibrated_lines: number;
  total_lines: number;
  /** Mean realized coverage across lines with enough closed cycles; null = no claim. */
  realized_coverage: number | null;
  lines_with_realized_coverage: number;
  target_coverage: number;
}

export interface ForecastRow {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code?: string;
  category: string;
  period?: string;
  periods?: string;
  period_count?: number;
  p50?: number;
  total_p50?: number;
  avg_p50?: number;
  p10?: number;
  p90?: number;
  p10_range?: number;
  p90_range?: number;
  confidence_score?: number;
  min_confidence?: number;
  avg_confidence?: number;
  confidence_level: string;
  model_type: string;
  model_mape?: number;
  is_overridden: boolean;
  // Review queue: Chronos-2's range disagrees with this line (second opinion).
  models_disagree?: boolean;
  override_value?: number;
  override_count?: number;
  indent_level?: number;
  is_subtotal?: boolean;
  ai_recommendation?: string;
  ai_reasoning?: string;
  ai_risk_score?: number;
  ai_actions?: AIAction[];
  materiality?: string;
  root_cause?: string;
  driver_context?: DriverContext;
  review_status?: string;
  exog_used?: boolean;
  exog?: ExogInfo | null;
}

export interface VersionInfo {
  id: string;
  name: string;
  status: string;
  total_line_items?: number;
  high_confidence_count?: number;
  medium_confidence_count?: number;
  low_confidence_count?: number;
}

export type ForecastTableRow = ForecastRow & Record<string, unknown>;
