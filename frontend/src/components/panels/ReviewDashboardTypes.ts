/** The review item shape returned by /panel/review-dashboard. */
import { type AIAction, type DriverContext, type ExogInfo } from './shared/forecastContext';

export interface ReviewItem {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code?: string;
  category: string;
  subcategory?: string;
  business_unit?: string;
  period_count: number;
  worst_period: string;
  p50_range: string;
  total_p50?: number;
  avg_p50?: number;
  avg_confidence: number;
  min_confidence: number;
  confidence_level: string;
  model_type: string;
  model_mape?: number;
  is_overridden: boolean;
  override_count: number;
  ai_recommendation: string;
  ai_reasoning: string;
  ai_risk_score: number;
  ai_actions?: AIAction[];
  materiality?: string;
  root_cause?: string;
  driver_context?: DriverContext;
  review_status: string | null;
  review_comment: string | null;
  reviewed_by: string | null;
  history?: { period: string; actual?: number; forecast?: number }[];
  exog_used?: boolean;
  exog?: ExogInfo | null;
}

/** The four review buckets, each a page of items with its total. */
export interface ReviewBuckets {
  ai_approved: { items: ReviewItem[]; total: number; description: string };
  needs_review: { items: ReviewItem[]; total: number; description: string };
  flagged: { items: ReviewItem[]; total: number; description: string };
  already_reviewed: { items: ReviewItem[]; total: number; description: string };
}
