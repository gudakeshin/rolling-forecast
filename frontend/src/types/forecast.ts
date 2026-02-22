export interface ForecastVersion {
  id: string;
  name: string;
  label: string | null;
  status: string;
  version_type: string;
  horizon_months: number;
  base_period: string | null;
  total_line_items: number;
  high_confidence_count: number;
  medium_confidence_count: number;
  low_confidence_count: number;
  override_count: number;
  generation_time_seconds: number | null;
  created_at: string;
}

export interface ForecastLineResult {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code: string;
  category: string;
  period: string;
  p10: number | null;
  p50: number;
  p90: number | null;
  confidence_score: number;
  confidence_level: string;
  model_type: string | null;
  is_overridden: boolean;
  override_value: number | null;
  indent_level: number;
  is_subtotal: boolean;
}

export interface PanelData {
  panel_type: string;
  title: string;
  data: Record<string, any>;
}
