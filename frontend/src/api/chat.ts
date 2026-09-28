import { useAuthStore } from '../store/authStore';
import type { Conversation, ChatMessage } from '../types/chat';
import { apiGet, apiDelete, tryRefreshAccessToken } from './client';

// SSE parser — handles \r\n line endings from sse-starlette

async function postChatMessage(
  content: string,
  conversationId: string | null,
  signal?: AbortSignal,
  retried = false,
): Promise<Response> {
  const token = useAuthStore.getState().token;

  const response = await fetch('/api/chat/message', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      content,
    }),
    signal,
  });

  if (response.status === 401) {
    const ok = await tryRefreshAccessToken();
    if (ok && !retried) {
      return postChatMessage(content, conversationId, signal, true);
    }
    if (!ok) {
      useAuthStore.getState().logout();
      window.location.href = '/login';
      throw new Error('Session expired. Please log in again.');
    }
  }

  return response;
}

export async function sendMessage(
  content: string,
  conversationId: string | null,
  onEvent: (event: { event: string; data: any }) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await postChatMessage(content, conversationId, signal);

  if (!response.ok) {
    throw new Error('Failed to send message');
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';
  let currentData = '';

  try {
    // eslint-disable-next-line no-constant-condition -- SSE stream read loop
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '');

        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6);
        } else if (line === '' && currentEvent && currentData) {
          try {
            const data = JSON.parse(currentData);
            onEvent({ event: currentEvent, data });
          } catch (e) {
            console.warn('[SSE] Failed to parse event data:', currentEvent, e);
          }
          currentEvent = '';
          currentData = '';
        }
      }
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      throw err;
    }
    throw err;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

export async function getConversations(): Promise<Conversation[]> {
  return apiGet<Conversation[]>('/chat/conversations');
}

export async function getConversation(id: string): Promise<{
  id: string;
  title: string;
  messages: ChatMessage[];
}> {
  return apiGet(`/chat/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  await apiDelete(`/chat/conversations/${id}`);
}
