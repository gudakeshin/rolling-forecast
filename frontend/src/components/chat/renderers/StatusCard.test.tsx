import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';

vi.mock('../../../api/client', () => ({
  apiGet: vi.fn().mockResolvedValue({ job_id: 'j1', status: 'running', progress: 0.3 }),
}));

import { apiGet } from '../../../api/client';
import { StatusCard } from './StatusCard';

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

describe('StatusCard job poll (plan 4.5)', () => {
  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
    vi.mocked(apiGet).mockClear();
  });

  it('polls every 2s while visible and pauses while the tab is hidden', async () => {
    vi.useFakeTimers();
    render(
      <StatusCard data={{ label: 'Queued', progress: 0, step: '', is_complete: false, job_id: 'j1' }} />,
    );
    await act(async () => {});
    expect(apiGet).toHaveBeenCalledTimes(1); // immediate first poll

    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(apiGet).toHaveBeenCalledTimes(3);

    setHidden(true);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(apiGet).toHaveBeenCalledTimes(3); // no polling in a background tab

    setHidden(false);
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(apiGet).toHaveBeenCalledTimes(4);
  });
});
