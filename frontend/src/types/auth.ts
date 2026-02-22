export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string;
  business_unit: string | null;
  role_name: string;
  is_active: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  username: string;
  role: string;
}
