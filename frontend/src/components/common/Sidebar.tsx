import { useEffect, useMemo, useState } from 'react';
import {
  LayoutDashboard,
  Table,
  Edit,
  GitCompare,
  Shield,
  CheckSquare,
  AlertTriangle,
  Target,
  Sparkles,
  Brain,
  GitBranch,
  FileInput,
  Activity,
  FolderOpen,
  Settings2,
  MessagesSquare,
  MessageSquarePlus,
  Trash2,
  Loader2,
} from 'lucide-react';
import { useAuthStore, useCan } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { useChatStore } from '../../store/chatStore';
import { getConversation, getConversations, deleteConversation } from '../../api/chat';
import { toast } from '../../store/toastStore';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import type { Conversation } from '../../types/chat';

interface NavItem {
  key: string;
  labelKey: MessageKey;
  icon: typeof Table;
  show: boolean;
  onClick: () => void;
}

interface NavGroup {
  titleKey: MessageKey;
  items: NavItem[];
}

export function Sidebar() {
  const { t } = useI18n();
  const user = useAuthStore((s) => s.user);
  const activePanel = usePanelStore((s) => s.panelType);
  const isPanelOpen = usePanelStore((s) => s.isOpen);
  const openPanel = usePanelStore((s) => s.openPanel);
  const versions = useVersionStore((s) => s.versions);
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const activeScenario = useVersionStore((s) => s.activeScenario);

  const canGenerate = useCan('can_generate');
  const canOverride = useCan('can_override');
  const canReview = useCan('can_review');
  const canInput = useCan('can_input');
  const canPublish = useCan('can_publish');
  const canAdmin = useCan('can_admin');
  const canManageDrivers = useCan('can_manage_drivers');
  const isAdminRole = canAdmin || user?.role_name === 'admin';

  const scenarioVersions = versions.filter((v) => (v.scenario || 'base') === activeScenario);
  const compareTargetId = scenarioVersions.find((v) => v.id !== activeVersionId)?.id ?? null;

  const open = (panel: string, params: Record<string, any> = {}) => () => openPanel(panel, params);

  const groups: NavGroup[] = [
    {
      titleKey: 'nav.group.forecast',
      items: [
        {
          key: 'forecast_table',
          labelKey: 'nav.forecast',
          icon: Table,
          show: canGenerate || canReview,
          onClick: open('forecast_table'),
        },
        {
          key: 'overrides',
          labelKey: 'nav.overrides',
          icon: Edit,
          show: canOverride,
          onClick: open('overrides'),
        },
        {
          key: 'comparison',
          labelKey: 'nav.comparison',
          icon: GitCompare,
          show: (canGenerate || canReview) && Boolean(activeVersionId && compareTargetId),
          onClick: open('comparison', {
            version_id_a: activeVersionId,
            version_id_b: compareTargetId,
          }),
        },
      ],
    },
    {
      titleKey: 'nav.group.review',
      items: [
        {
          key: 'review_dashboard',
          labelKey: 'nav.review',
          icon: Shield,
          show: canReview,
          onClick: open('review_dashboard'),
        },
        {
          key: 'approvals',
          labelKey: 'nav.approvals',
          icon: CheckSquare,
          show: canReview || canPublish,
          onClick: open('approvals'),
        },
        {
          key: 'anomaly_dashboard',
          labelKey: 'nav.anomalies',
          icon: AlertTriangle,
          show: canReview,
          onClick: open('anomaly_dashboard'),
        },
      ],
    },
    {
      titleKey: 'nav.group.intelligence',
      items: [
        {
          key: 'accuracy_tracking',
          labelKey: 'nav.accuracy',
          icon: Target,
          show: canReview || canGenerate,
          onClick: open('accuracy_tracking'),
        },
        {
          key: 'explainability',
          labelKey: 'nav.explain',
          icon: Sparkles,
          show: canReview || canGenerate,
          onClick: open('explainability'),
        },
        {
          key: 'heuristics',
          labelKey: 'nav.heuristics',
          icon: Brain,
          show: canReview || canAdmin,
          onClick: open('heuristics'),
        },
        {
          key: 'what_if',
          labelKey: 'nav.whatIf',
          icon: GitBranch,
          show: canGenerate,
          onClick: open('what_if'),
        },
      ],
    },
    {
      titleKey: 'nav.group.inputs',
      items: [
        {
          key: 'driver_inputs',
          labelKey: 'nav.drivers',
          icon: FileInput,
          show: canInput,
          onClick: open('driver_inputs'),
        },
        {
          key: 'drivers',
          labelKey: 'nav.causalDrivers',
          icon: Activity,
          show: canManageDrivers || isAdminRole,
          onClick: open('drivers'),
        },
      ],
    },
    {
      titleKey: 'nav.group.library',
      items: [
        {
          key: 'document_library',
          labelKey: 'nav.documents',
          icon: FolderOpen,
          show: true,
          onClick: open('document_library'),
        },
      ],
    },
    {
      titleKey: 'nav.group.admin',
      items: [
        {
          key: 'skill_editor',
          labelKey: 'nav.skills',
          icon: Settings2,
          show: isAdminRole,
          onClick: open('skill_editor'),
        },
        {
          key: 'admin_console',
          labelKey: 'nav.admin',
          icon: Shield,
          show: isAdminRole,
          onClick: open('admin_console'),
        },
      ],
    },
  ];

  return (
    <nav
      className="w-[216px] shrink-0 h-full bg-surface-800 border-r border-surface-700/60 overflow-y-auto flex flex-col"
      aria-label={t('nav.sidebar')}
    >
      <div className="p-2.5 pt-3.5">
        <button
          type="button"
          onClick={open('executive_dashboard')}
          className={`w-full flex items-center gap-2 px-2.5 py-2.5 rounded-xl text-[13px] font-bold transition-colors mb-3 ${
            isPanelOpen && activePanel === 'executive_dashboard'
              ? 'bg-deloitte-green/12 text-deloitte-green'
              : 'text-surface-200 hover:bg-surface-700/50'
          }`}
        >
          <LayoutDashboard className="w-4 h-4 shrink-0" aria-hidden="true" />
          {t('nav.executive')}
        </button>

        {groups.map((group) => {
          const visible = group.items.filter((i) => i.show);
          if (!visible.length) return null;
          return (
            <div key={group.titleKey} className="mb-3.5">
              <div className="px-2.5 pb-1.5 text-[9.5px] font-bold text-surface-500 uppercase tracking-wider">
                {t(group.titleKey)}
              </div>
              {visible.map((item) => {
                const active = isPanelOpen && activePanel === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={item.onClick}
                    className={`w-full flex items-center gap-2 px-2.5 py-[7px] rounded-lg text-xs mb-0.5 transition-colors text-left ${
                      active
                        ? 'bg-deloitte-green/12 text-deloitte-green font-semibold'
                        : 'text-surface-200 hover:bg-surface-700/40 font-medium'
                    }`}
                  >
                    <item.icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                    <span className="truncate">{t(item.labelKey)}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>

      <RecentChats />
    </nav>
  );
}

/** Compact, collapsible recent-conversations list — folds the old persistent
 * ConversationSidebar rail into the single mockup-style nav column. */
function RecentChats() {
  const { t } = useI18n();
  const {
    conversations,
    activeConversationId,
    isStreaming,
    setConversations,
    removeConversation,
    setActiveConversation,
    setMessages,
  } = useChatStore();
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const list = await getConversations();
        if (!cancelled) setConversations(list);
      } catch (e) {
        if (!cancelled) console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    if (isStreaming || deletingId) return;
    const ok = window.confirm(t('sidebar.deleteConfirm', { title: c.title || t('sidebar.untitled') }));
    if (!ok) return;
    setDeletingId(c.id);
    try {
      await deleteConversation(c.id);
      removeConversation(c.id);
    } catch (err: any) {
      toast.error(err?.message || t('sidebar.deleteError'));
    } finally {
      setDeletingId(null);
    }
  };

  const visible = useMemo(
    () => (showAll ? conversations : conversations.slice(0, 6)),
    [conversations, showAll],
  );

  return (
    <div className="mt-auto border-t border-surface-700/60 p-2.5">
      <div className="flex items-center justify-between px-1 pb-1.5">
        <div className="flex items-center gap-1.5 text-[10px] font-bold text-surface-500 uppercase tracking-wider">
          <MessagesSquare className="w-3 h-3" aria-hidden="true" />
          {t('sidebar.chats')}
        </div>
        <button
          type="button"
          onClick={startNew}
          className="p-1 rounded-md text-surface-400 hover:text-deloitte-green hover:bg-surface-700/40 min-h-[28px] min-w-[28px] flex items-center justify-center"
          aria-label={t('sidebar.new')}
          title={t('sidebar.new')}
        >
          <MessageSquarePlus className="w-3.5 h-3.5" />
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-surface-500 px-1.5 py-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          {t('sidebar.loading')}
        </div>
      )}
      {!loading && !conversations.length && (
        <p className="text-xs text-surface-500 px-1.5 py-2">{t('sidebar.empty')}</p>
      )}

      <div className="max-h-40 overflow-y-auto space-y-0.5">
        {visible.map((c) => {
          const active = c.id === activeConversationId;
          const deleting = deletingId === c.id;
          return (
            <div
              key={c.id}
              className={`group flex items-stretch rounded-lg ${
                active ? 'bg-deloitte-green/12' : 'hover:bg-surface-700/40'
              }`}
            >
              <button
                type="button"
                onClick={() => openConversation(c)}
                disabled={isStreaming}
                className={`flex-1 min-w-0 text-left rounded-lg px-2 py-1.5 text-xs truncate ${
                  active ? 'text-deloitte-green font-semibold' : 'text-surface-300'
                }`}
                title={c.title || t('sidebar.untitled')}
              >
                {c.title || t('sidebar.untitled')}
              </button>
              <button
                type="button"
                onClick={(e) => handleDelete(c, e)}
                disabled={isStreaming || deleting}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 self-center mr-1 p-1 rounded-md text-surface-500 hover:text-red-400 transition-opacity disabled:opacity-40"
                aria-label={`Delete ${c.title || t('sidebar.untitled')}`}
              >
                {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
              </button>
            </div>
          );
        })}
      </div>

      {conversations.length > 6 && (
        <button
          type="button"
          onClick={() => setShowAll((s) => !s)}
          className="mt-1 px-1.5 text-xs font-medium text-deloitte-green hover:underline"
        >
          {showAll ? t('sidebar.showLess') : t('sidebar.showAll')}
        </button>
      )}
    </div>
  );
}
