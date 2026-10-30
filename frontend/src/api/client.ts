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

export async function apiPatch<T>(url: string, body?: any): Promise<T> {
  const response = await fetchApi(url, {
    method: 'PATCH',
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
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'Upload failed');
  }
  return response.json();
}

interface QueuedJob {
  job_id: string;
  status: 'queued';
  kind?: string;
}

function isQueuedJob(value: unknown): value is QueuedJob {
  return Boolean(value && typeof value === 'object' && 'job_id' in value && (value as QueuedJob).status === 'queued');
}

/** Endpoints that may hand slow work to the background queue answer either
 * with their result (inline) or `{job_id, status: 'queued'}` (HTTP 202). This
 * returns the result either way, polling GET /jobs/{id} until it settles. */
export async function awaitJob<T>(
  response: T | QueuedJob,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<T> {
  if (!isQueuedJob(response)) return response as T;
  const interval = opts.intervalMs ?? 1500;
  const deadline = Date.now() + (opts.timeoutMs ?? 10 * 60_000);
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, interval));
    const job = await apiGet<{ status: string; result?: T; error?: string }>(`/jobs/${response.job_id}`);
    if (job.status === 'completed') return job.result as T;
    if (job.status === 'failed') throw new Error(job.error || 'Background job failed');
  }
  throw new Error('Background job is still running; check notifications for the result');
}

/** Download an authenticated file response (e.g. a server-side export) by name. */
export async function apiDownload(url: string, filename: string): Promise<void> {
  const response = await fetchApi(url);
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Download failed' }));
    throw new Error(error.detail || 'Download failed');
  }
  const blobUrl = URL.createObjectURL(await response.blob());
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

export { fetchApi, tryRefreshAccessToken };
