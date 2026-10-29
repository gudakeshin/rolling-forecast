import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 't', refreshToken: null, setTokens: vi.fn(), logout: vi.fn() }) },
}));

import { awaitJob } from './client';

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('awaitJob', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('returns an inline result untouched', async () => {
    expect(await awaitJob({ success: true })).toEqual({ success: true });
  });

  it('polls a queued job until it completes', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ status: 'running' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'completed', result: { total_lines_rescored: 7 } }));
    const out = await awaitJob({ job_id: 'j1', status: 'queued' }, { intervalMs: 1 });
    expect(out).toEqual({ total_lines_rescored: 7 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/jobs/j1');
  });

  it('surfaces a failed job as an error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse({ status: 'failed', error: 'warehouse unreachable' }));
    await expect(awaitJob({ job_id: 'j2', status: 'queued' }, { intervalMs: 1 })).rejects.toThrow('warehouse unreachable');
  });
});
