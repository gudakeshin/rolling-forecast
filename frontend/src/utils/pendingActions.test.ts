import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({ apiPost: vi.fn() }));

import { apiPost } from '../api/client';
import { useChatStore } from '../store/chatStore';
import { dispatchRecommendedAction } from './recommendedActions';

describe('pending agent actions', () => {
  beforeEach(() => {
    vi.mocked(apiPost).mockReset();
    useChatStore.getState().setMessages([]);
  });

  it('approve posts to the confirm endpoint and shows the result', async () => {
    vi.mocked(apiPost).mockResolvedValue({ success: true, skill: 'apply_override', message: 'done' });
    const handled = await dispatchRecommendedAction({
      type: 'confirm_pending_action',
      pending_action_id: 'abc',
      label: 'Approve apply_override',
    });
    expect(handled).toBe(true);
    expect(apiPost).toHaveBeenCalledWith('/chat/pending-actions/abc/confirm');
    const last = useChatStore.getState().messages.at(-1);
    expect(last?.content).toContain('Approved `apply_override`');
  });

  it('discard posts to the discard endpoint', async () => {
    vi.mocked(apiPost).mockResolvedValue({ discarded: true, skill: 'apply_override' });
    await dispatchRecommendedAction({ type: 'discard_pending_action', pending_action_id: 'abc', label: 'Discard' });
    expect(apiPost).toHaveBeenCalledWith('/chat/pending-actions/abc/discard');
    expect(useChatStore.getState().messages.at(-1)?.content).toContain('nothing was changed');
  });

  it('an expired approval says so instead of failing silently', async () => {
    vi.mocked(apiPost).mockRejectedValue(new Error('404'));
    await dispatchRecommendedAction({ type: 'confirm_pending_action', pending_action_id: 'gone', label: 'Approve' });
    expect(useChatStore.getState().messages.at(-1)?.content).toContain('expired');
  });
});
