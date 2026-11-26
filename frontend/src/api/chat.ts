import { useAuthStore } from '../store/authStore';
import type { Conversation, ChatMessage } from '../types/chat';
import { apiGet, apiDelete, tryRefreshAccessToken } from './client';

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

export interface SSEEvent {
  event: string;
  data: any;
}

/**
 * Incremental Server-Sent Events parser (WHATWG rules that matter here):
 * multi-line `data:` fields are joined with "\n" instead of the last line
 * winning, the space after the colon is optional, `:` lines are comments,
 * CRLF is accepted, and an event without `event:` is a "message".
 */
export function createSSEParser(onEvent: (e: SSEEvent) => void) {
  let buffer = '';
  let eventName = '';
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length) {
      const raw = dataLines.join('\n');
      const name = eventName || 'message';
      try {
        onEvent({ event: name, data: JSON.parse(raw) });
      } catch (e) {
        console.warn('[SSE] Failed to parse event data:', name, e);
      }
    }
    eventName = '';
    dataLines = [];
  };

  const handleLine = (rawLine: string) => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') eventName = value;
    else if (field === 'data') dataLines.push(value);
  };

  return {
    push(chunk: string) {
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(handleLine);
    },
    /** End of stream: a final event without its trailing blank line still counts. */
    end() {
      if (buffer) handleLine(buffer);
      buffer = '';
      dispatch();
    },
  };
}

const RECOVERY_POLL_MS = 2000;
const RECOVERY_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * The connection dropped before the reply finished. The server finishes the
 * turn regardless and saves the reply, so wait for it to appear in the
 * conversation instead of re-sending the message (which would run the agent,
 * and any write it makes, twice).
 */
async function recoverReply(
  conversationId: string,
  knownAssistantCount: number,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<boolean> {
  onEvent({ event: 'reconnecting', data: { conversation_id: conversationId } });
  const deadline = Date.now() + RECOVERY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (signal?.aborted) return false;
    await new Promise((r) => setTimeout(r, RECOVERY_POLL_MS));
    try {
      const conv = await getConversation(conversationId);
      const replies = conv.messages.filter((m) => m.role === 'assistant');
      if (replies.length > knownAssistantCount) {
        const reply = replies[replies.length - 1];
        onEvent({
          event: 'message_end',
          data: {
            content: reply.content,
            content_blocks: reply.content_blocks ?? [],
            tool_calls: reply.tool_calls ?? [],
            panel_payload: reply.panel_payload,
          },
        });
        return true;
      }
    } catch {
      /* still offline: keep waiting */
    }
  }
  return false;
}

export async function sendMessage(
  content: string,
  conversationId: string | null,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  // How many replies already exist, so recovery can tell the new one apart.
  let knownAssistantCount = 0;
  if (conversationId) {
    try {
      const conv = await getConversation(conversationId);
      knownAssistantCount = conv.messages.filter((m) => m.role === 'assistant').length;
    } catch {
      /* new or unreadable conversation: 0 is right */
    }
  }

  const response = await postChatMessage(content, conversationId, signal);

  if (!response.ok) {
    throw new Error('Failed to send message');
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');

  let streamConversationId = conversationId;
  let finished = false;
  const parser = createSSEParser((e) => {
    if (e.event === 'message_start' && e.data?.conversation_id) {
      streamConversationId = e.data.conversation_id;
    }
    if (e.event === 'message_end' || e.event === 'error') finished = true;
    onEvent(e);
  });

  const decoder = new TextDecoder();
  try {
    // eslint-disable-next-line no-constant-condition -- SSE stream read loop
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.end();
  } catch (err: any) {
    if (err?.name === 'AbortError') throw err;
    parser.end();
    if (!finished && streamConversationId) {
      if (await recoverReply(streamConversationId, knownAssistantCount, onEvent, signal)) return;
    }
    throw err;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }

  // The stream closed cleanly but without a reply (a proxy timeout, say).
  if (!finished && streamConversationId) {
    if (await recoverReply(streamConversationId, knownAssistantCount, onEvent, signal)) return;
    throw new Error('The connection closed before the reply arrived');
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
