import { useAuthStore } from '../store/authStore';

const BASE_URL = '/api';

let refreshInFlight: Promise<boolean> | null = null;

async function tryRefreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refresh = useAuthStore.getState().refreshToken;
    if (!refresh) return false;
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      useAuthStore.getState().setTokens(data.access_token, data.refresh_token ?? null);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function fetchApi(url: string, options: RequestInit = {}, retried = false): Promise<Response> {
  const token = useAuthStore.getState().token;

  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(`${BASE_URL}${url}`, {
    ...options,
    headers,
  });

  if (response.status === 401 && !retried && !url.startsWith('/auth/')) {
    const ok = await tryRefreshAccessToken();
    if (ok) {
      return fetchApi(url, options, true);
    }
    useAuthStore.getState().logout();
    window.location.href = '/login';
  }

  return response;
}

export async function apiGet<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetchApi(url, init);
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || 'Request failed');
  }
  return response.json();
}

export async function apiPost<T>(url: string, body?: any): Promise<T> {
  const response = await fetchApi(url, {
    method: 'POST',
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || 'Request failed');
  }
  return response.json();
}

export async function apiPut<T>(url: string, body?: any): Promise<T> {
  const response = await fetchApi(url, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || 'Request failed');
  }
  return response.json();
}

export async function apiDelete<T = void>(url: string): Promise<T | void> {
  const response = await fetchApi(url, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || 'Request failed');
  }
  if (response.status === 204) return;
  const text = await response.text();
  if (!text) return;
  return JSON.parse(text) as T;
}

export async function uploadFile(url: string, file: File): Promise<any> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetchApi(url, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error('Upload failed');
  }
  return response.json();
}

export { fetchApi, tryRefreshAccessToken };
