/** Types and constants shared by the admin console tabs. */

export interface BusinessUnit {
  id: string;
  name: string;
}

export interface AnalysisProfileResponse {
  business_unit_id: string;
  settings: Record<string, string | number | boolean>;
  overridden_fields: string[];
  field_types: Record<string, 'bool' | 'int' | 'float' | 'str'>;
}

export const ROLE_PERMISSION_KEYS: { key: string; label: string }[] = [
  { key: 'can_input', label: 'Input drivers' },
  { key: 'can_generate', label: 'Generate forecasts' },
  { key: 'can_override', label: 'Override values' },
  { key: 'can_review', label: 'Review/approve' },
  { key: 'can_publish', label: 'Publish' },
  { key: 'can_admin', label: 'Admin' },
  { key: 'can_view_all_bus', label: 'View all companies' },
  { key: 'can_manage_drivers', label: 'Manage drivers' },
];

export const emptyPresetForm = {
  name: '',
  description: '',
  model_type: 'auto',
  candidate_models: [] as string[],
  default_horizon_months: '',
};

export interface FxRate {
  id: string;
  from_currency: string;
  to_currency: string;
  period: string;
  rate: number;
  rate_type: string;
}

/** Every tab reports errors and notices to the console's banner. */
export interface AdminTabProps {
  onError: (message: string) => void;
  onMessage: (message: string) => void;
}
