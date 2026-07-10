import { useEffect, useState } from 'react';
import { MessageSquarePlus, MessagesSquare, Loader2 } from 'lucide-react';
import { getConversation, getConversations } from '../../api/chat';
import { useChatStore } from '../../store/chatStore';
import type { Conversation } from '../../types/chat';

export function ConversationSidebar() {
  const {
    conversations,
    activeConversationId,
    setConversations,
    setActiveConversation,
    setMessages,
    isStreaming,
  } = useChatStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const list = await getConversations();
        if (!cancelled) setConversations(list);
      } catch (e) {
        if (!cancelled) setError('Could not load conversations');
        console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setConversations]);

  const startNew = () => {
    setActiveConversation(null);
    setMessages([]);
  };

  const openConversation = async (c: Conversation) => {
    if (isStreaming) return;
    setActiveConversation(c.id);
    try {
      const detail = await getConversation(c.id);
      setMessages(detail.messages || []);
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <aside
      className="w-56 shrink-0 border-r border-surface-700/50 bg-surface-900 flex flex-col"
      aria-label="Conversation history"
    >
      <div className="p-3 border-b border-surface-700/50 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-sm text-surface-300">
          <MessagesSquare className="w-4 h-4 text-deloitte-green" />
          Chats
        </div>
        <button
          type="button"
          onClick={startNew}
          className="p-1.5 rounded-md text-surface-400 hover:text-white hover:bg-surface-800"
          aria-label="New conversation"
          title="New conversation"
        >
          <MessageSquarePlus className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-surface-500 px-2 py-3">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Loading…
          </div>
        )}
        {error && <p className="text-xs text-red-400 px-2 py-2">{error}</p>}
        {!loading && !conversations.length && (
          <p className="text-xs text-surface-500 px-2 py-3">No conversations yet</p>
        )}
        {conversations.map((c) => {
          const active = c.id === activeConversationId;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => openConversation(c)}
              className={`w-full text-left rounded-lg px-2.5 py-2 text-xs transition-colors ${
                active
                  ? 'bg-deloitte-green/15 text-white border border-deloitte-green/30'
                  : 'text-surface-300 hover:bg-surface-800 border border-transparent'
              }`}
            >
              <div className="font-medium truncate">{c.title || 'Untitled'}</div>
              <div className="text-[10px] text-surface-500 mt-0.5">
                {c.message_count ?? 0} messages
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
