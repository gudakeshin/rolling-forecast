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
import { toast } from '../../store/toastStore';
import { useI18n } from '../../i18n/useI18n';
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
  const { t } = useI18n();
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
        if (!cancelled) setError(t('sidebar.loadError'));
        console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setConversations, t]);

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
    } catch (e: any) {
      toast.error(e?.message || t('sidebar.loadError'));
    }
  };

  const handleDelete = async (c: Conversation, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (isStreaming || deletingId) return;
    const ok = window.confirm(
      t('sidebar.deleteConfirm', { title: c.title || t('sidebar.untitled') }),
    );
    if (!ok) return;

    setDeletingId(c.id);
    try {
      await deleteConversation(c.id);
      removeConversation(c.id);
    } catch (err) {
      console.error(err);
      setError(t('sidebar.deleteError'));
    } finally {
      setDeletingId(null);
    }
  };

  if (!sidebarExpanded) {
    return (
      <aside
        className="w-12 h-full shrink-0 border-r border-surface-700/50 bg-surface-900 flex flex-col items-center py-3 gap-2"
        aria-label={t('sidebar.conversations')}
      >
        <button
          type="button"
          onClick={toggleSidebar}
          className="p-2 rounded-lg text-surface-400 hover:text-white hover:bg-surface-800 min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label={t('sidebar.expand')}
          title={t('sidebar.expand')}
        >
          <PanelLeftOpen className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={startNew}
          className="p-2 rounded-lg text-surface-400 hover:text-white hover:bg-surface-800 min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label={t('sidebar.new')}
          title={t('sidebar.new')}
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
      className="w-64 sm:w-56 h-full shrink-0 border-r border-surface-700/50 bg-surface-900 flex flex-col transition-[width] duration-200"
      aria-label={t('sidebar.conversations')}
    >
      <div className="p-3 border-b border-surface-700/50 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-sm text-surface-300">
          <MessagesSquare className="w-4 h-4 text-deloitte-green" />
          {t('sidebar.chats')}
        </div>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={startNew}
            className="p-1.5 rounded-md text-surface-400 hover:text-white hover:bg-surface-800 min-h-[36px] min-w-[36px] flex items-center justify-center"
            aria-label={t('sidebar.new')}
            title={t('sidebar.new')}
          >
            <MessageSquarePlus className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={toggleSidebar}
            className="p-1.5 rounded-md text-surface-400 hover:text-white hover:bg-surface-800 min-h-[36px] min-w-[36px] flex items-center justify-center"
            aria-label={t('sidebar.collapse')}
            title={t('sidebar.collapse')}
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-surface-500 px-2 py-3">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            {t('sidebar.loading')}
          </div>
        )}
        {error && <p className="text-xs text-red-400 px-2 py-2">{error}</p>}
        {!loading && !conversations.length && (
          <p className="text-xs text-surface-500 px-2 py-3">{t('sidebar.empty')}</p>
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
                <div className="font-medium truncate">{c.title || t('sidebar.untitled')}</div>
                <div className="text-xs text-surface-500 mt-0.5">
                  {t('sidebar.messages', { count: c.message_count ?? 0 })}
                </div>
              </button>
              <button
                type="button"
                onClick={(e) => handleDelete(c, e)}
                disabled={isStreaming || deleting}
                className="opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 focus:opacity-100 self-center mr-1 p-1.5 rounded-md text-surface-500 hover:text-red-400 hover:bg-red-500/10 transition-opacity disabled:opacity-40 min-h-[36px] min-w-[36px]"
                aria-label={`Delete ${c.title || t('sidebar.untitled')}`}
                title="Delete"
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
