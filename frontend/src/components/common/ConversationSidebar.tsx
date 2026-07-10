import { useEffect, useState } from 'react';
import {
  MessageSquarePlus,
  MessagesSquare,
  Loader2,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { deleteConversation, getConversation, getConversations } from '../../api/chat';
import { useChatStore } from '../../store/chatStore';
import type { Conversation } from '../../types/chat';

export function ConversationSidebar() {
  const {
    conversations,
    activeConversationId,
    sidebarExpanded,
    setConversations,
    removeConversation,
    setActiveConversation,
    setMessages,
    toggleSidebar,
    isStreaming,
  } = useChatStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

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

  const handleDelete = async (c: Conversation, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (isStreaming || deletingId) return;
    const ok = window.confirm(
      `Delete “${c.title || 'Untitled'}”? This cannot be undone.`,
    );
    if (!ok) return;

    setDeletingId(c.id);
    try {
      await deleteConversation(c.id);
      removeConversation(c.id);
    } catch (err) {
      console.error(err);
      setError('Could not delete conversation');
    } finally {
      setDeletingId(null);
    }
  };

  if (!sidebarExpanded) {
    return (
      <aside
        className="w-12 shrink-0 border-r border-surface-700/50 bg-surface-900 flex flex-col items-center py-3 gap-2"
        aria-label="Conversation history collapsed"
      >
        <button
          type="button"
          onClick={toggleSidebar}
          className="p-2 rounded-lg text-surface-400 hover:text-white hover:bg-surface-800 min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="Expand chats sidebar"
          title="Expand chats"
        >
          <PanelLeftOpen className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={startNew}
          className="p-2 rounded-lg text-surface-400 hover:text-white hover:bg-surface-800 min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="New conversation"
          title="New conversation"
        >
          <MessageSquarePlus className="w-4 h-4" />
        </button>
        <div className="mt-2 text-deloitte-green" aria-hidden="true">
          <MessagesSquare className="w-4 h-4" />
        </div>
      </aside>
    );
  }

  return (
    <aside
      className="w-56 shrink-0 border-r border-surface-700/50 bg-surface-900 flex flex-col transition-[width] duration-200"
      aria-label="Conversation history"
    >
      <div className="p-3 border-b border-surface-700/50 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-sm text-surface-300">
          <MessagesSquare className="w-4 h-4 text-deloitte-green" />
          Chats
        </div>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={startNew}
            className="p-1.5 rounded-md text-surface-400 hover:text-white hover:bg-surface-800 min-h-[36px] min-w-[36px] flex items-center justify-center"
            aria-label="New conversation"
            title="New conversation"
          >
            <MessageSquarePlus className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={toggleSidebar}
            className="p-1.5 rounded-md text-surface-400 hover:text-white hover:bg-surface-800 min-h-[36px] min-w-[36px] flex items-center justify-center"
            aria-label="Collapse chats sidebar"
            title="Collapse chats"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>
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
          const deleting = deletingId === c.id;
          return (
            <div
              key={c.id}
              className={`group flex items-stretch gap-0.5 rounded-lg border transition-colors ${
                active
                  ? 'bg-deloitte-green/15 border-deloitte-green/30'
                  : 'border-transparent hover:bg-surface-800'
              }`}
            >
              <button
                type="button"
                onClick={() => openConversation(c)}
                disabled={isStreaming}
                className={`flex-1 min-w-0 text-left rounded-lg px-2.5 py-2 text-xs ${
                  active ? 'text-white' : 'text-surface-300'
                }`}
              >
                <div className="font-medium truncate">{c.title || 'Untitled'}</div>
                <div className="text-xs text-surface-500 mt-0.5">
                  {c.message_count ?? 0} messages
                </div>
              </button>
              <button
                type="button"
                onClick={(e) => handleDelete(c, e)}
                disabled={isStreaming || deleting}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 self-center mr-1 p-1.5 rounded-md text-surface-500 hover:text-red-400 hover:bg-red-500/10 transition-opacity disabled:opacity-40"
                aria-label={`Delete ${c.title || 'conversation'}`}
                title="Delete conversation"
              >
                {deleting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Trash2 className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
