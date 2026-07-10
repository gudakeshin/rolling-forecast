import { useAuthStore } from '../store/authStore';
import type { Conversation, ChatMessage } from '../types/chat';
import { apiGet } from './client';

// SSE parser v2 — handles \r\n line endings from sse-starlette
console.log('[chat] SSE parser v2 loaded');

export async function sendMessage(
  content: string,
  conversationId: string | null,
  onEvent: (event: { event: string; data: any }) => void
): Promise<void> {
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
  });

  if (response.status === 401) {
    useAuthStore.getState().logout();
    window.location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }

  if (!response.ok) {
    throw new Error('Failed to send message');
  }

  // Read SSE stream
  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';
  // SSE parser state — persists across chunks so events split across
  // chunk boundaries are correctly assembled.
  let currentEvent = '';
  let currentData = '';

  // eslint-disable-next-line no-constant-condition -- SSE stream read loop
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE events from buffer (handle both \r\n and \n line endings)
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // Keep incomplete line in buffer

    for (const rawLine of lines) {
      // Strip carriage return (sse-starlette uses \r\n)
      const line = rawLine.replace(/\r$/, '');

      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6);
      } else if (line === '' && currentEvent && currentData) {
        // Complete event — dispatch it
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
