export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string;
  business_unit: string | null;
  role_name: string;
  is_active: boolean;
  can_input?: boolean;
  can_generate?: boolean;
  can_override?: boolean;
  can_review?: boolean;
  can_publish?: boolean;
  can_admin?: boolean;
  can_manage_drivers?: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type: string;
  user_id: string;
  username: string;
  role: string;
}
