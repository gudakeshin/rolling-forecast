import { useEffect, useRef } from 'react';
import { Bell } from 'lucide-react';
import { useNotificationStore } from '../../store/notificationStore';
import { usePanelStore } from '../../store/panelStore';
import { useI18n } from '../../i18n/useI18n';
import type { AppNotification } from '../../api/notifications';

const POLL_MS = 60_000;

function timeAgo(iso: string | null): string {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function NotificationBell() {
  const { t } = useI18n();
  const { notifications, unreadCount, isOpen, loading, setOpen, fetchNotifications, markRead, markAllRead } =
    useNotificationStore();
  const rootRef = useRef<HTMLDivElement>(null);
  const openPanel = usePanelStore((s) => s.openPanel);

  useEffect(() => {
    fetchNotifications();
    const id = window.setInterval(() => {
      if (!document.hidden) fetchNotifications();
    }, POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const onClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [isOpen, setOpen]);

  const handleSelect = (n: AppNotification) => {
    if (!n.read) markRead(n.id);
    setOpen(false);
    if (n.link_panel) openPanel(n.link_panel, n.link_params || {});
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen(!isOpen)}
        className="relative p-2 rounded-lg text-surface-400 hover:text-deloitte-green hover:bg-surface-700/40 transition-colors"
        title={t('notifications.bell')}
        aria-label={t('notifications.bell')}
        aria-expanded={isOpen}
      >
        <Bell className="w-4 h-4" />
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 min-w-[15px] h-[15px] px-[3px] rounded-full bg-red-500 text-white text-[9px] font-bold flex items-center justify-center leading-none">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          className="absolute right-0 top-full mt-1.5 w-80 max-w-[90vw] max-h-[70vh] overflow-hidden flex flex-col bg-surface-800 border border-surface-700/60 rounded-xl shadow-2xl z-50"
          role="menu"
        >
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-surface-700/50">
            <span className="text-sm font-semibold text-white">{t('notifications.title')}</span>
            {unreadCount > 0 && (
              <button
                type="button"
                onClick={() => markAllRead()}
                className="text-[11px] font-medium text-deloitte-green hover:underline"
              >
                {t('notifications.markAllRead')}
              </button>
            )}
          </div>
          <div className="overflow-y-auto flex-1">
            {loading && notifications.length === 0 ? (
              <p className="text-xs text-surface-500 text-center py-6">{t('notifications.loading')}</p>
            ) : notifications.length === 0 ? (
              <p className="text-xs text-surface-500 text-center py-6">{t('notifications.empty')}</p>
            ) : (
              notifications.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => handleSelect(n)}
                  className={`w-full text-left px-3 py-2.5 border-b border-surface-700/30 hover:bg-surface-700/40 transition-colors ${
                    n.read ? '' : 'bg-deloitte-green/5'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    {!n.read && (
                      <span className="w-1.5 h-1.5 rounded-full bg-deloitte-green mt-1.5 shrink-0" aria-hidden="true" />
                    )}
                    <div className={`min-w-0 flex-1 ${n.read ? 'pl-3.5' : ''}`}>
                      <div className="text-xs font-semibold text-surface-100 truncate">{n.title}</div>
                      {n.body && (
                        <div className="text-[11px] text-surface-400 line-clamp-2 mt-0.5">{n.body}</div>
                      )}
                      <div className="text-[10px] text-surface-500 mt-1">{timeAgo(n.created_at)}</div>
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
