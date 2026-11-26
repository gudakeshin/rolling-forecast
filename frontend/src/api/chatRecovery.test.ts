import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./client', () => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  tryRefreshAccessToken: vi.fn(),
}));
vi.mock('../store/authStore', () => ({
  useAuthStore: { getState: () => ({ token: 't', logout: vi.fn() }) },
}));

import { apiGet } from './client';
import { sendMessage, type SSEEvent } from './chat';

function droppingBody(firstChunk: string): ReadableStream<Uint8Array> {
  let sent = false;
  return new ReadableStream({
    pull(controller) {
      if (!sent) {
        sent = true;
        controller.enqueue(new TextEncoder().encode(firstChunk));
      } else {
        controller.error(new TypeError('network error'));
      }
    },
  });
}

describe('sendMessage recovery', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('recovers the saved reply after the connection drops, without re-sending', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(droppingBody('event: message_start\ndata: {"conversation_id":"c1"}\n\n'), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(apiGet).mockResolvedValue({
      id: 'c1',
      title: '',
      messages: [{ role: 'assistant', content: 'saved reply', content_blocks: [] }],
    });

    const events: SSEEvent[] = [];
    const done = sendMessage('hi', null, (e) => events.push(e));
    await vi.advanceTimersByTimeAsync(2500);
    await done;

    expect(fetchMock).toHaveBeenCalledTimes(1); // the message was not posted again
    expect(events.map((e) => e.event)).toEqual(['message_start', 'reconnecting', 'message_end']);
    expect(events.at(-1)?.data.content).toBe('saved reply');
  });
});
