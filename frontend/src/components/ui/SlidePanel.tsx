import { useEffect, useRef, type ReactNode } from 'react';
import { X, PanelRightClose, PanelRightOpen } from 'lucide-react';
import { IconButton } from './Pressable';
import { t } from '../../i18n';

interface SlidePanelProps {
  title: string;
  icon?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  headerExtra?: ReactNode;
  /** When set, shows expand/collapse control for the right panel width */
  widthExpanded?: boolean;
  onToggleWidth?: () => void;
}

/**
 * Shared slide-over panel shell with dialog semantics, Esc-close, and focus restore.
 */
export function SlidePanel({
  title,
  icon,
  onClose,
  children,
  headerExtra,
  widthExpanded,
  onToggleWidth,
}: SlidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const node = panelRef.current;
    const focusable = node?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    focusable?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previouslyFocused.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      ref={panelRef}
      className="h-full flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-labelledby="slide-panel-title"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700/50 bg-surface-800">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-0.5 h-4 bg-deloitte-green rounded-full shrink-0" />
          {icon}
          <h3 id="slide-panel-title" className="text-sm font-semibold text-white truncate">
            {title}
          </h3>
          {headerExtra}
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {onToggleWidth && (
            <IconButton
              label={widthExpanded ? t('panel.narrow') : t('panel.widen')}
              onClick={onToggleWidth}
              title={widthExpanded ? t('panel.narrow') : t('panel.widen')}
            >
              {widthExpanded ? (
                <PanelRightClose className="w-4 h-4" aria-hidden="true" />
              ) : (
                <PanelRightOpen className="w-4 h-4" aria-hidden="true" />
              )}
            </IconButton>
          )}
          <IconButton label={t('panel.close')} onClick={onClose}>
            <X className="w-4 h-4" aria-hidden="true" />
          </IconButton>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">{children}</div>
    </div>
  );
}
