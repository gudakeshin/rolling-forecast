import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X, PanelRightClose, PanelRightOpen, Pin, Maximize2, Minimize2, BookmarkPlus } from 'lucide-react';
import { IconButton } from './Pressable';
import { t } from '../../i18n';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface SlidePanelProps {
  title: string;
  icon?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  headerExtra?: ReactNode;
  /** When set, shows expand/collapse control for the right panel width */
  widthExpanded?: boolean;
  onToggleWidth?: () => void;
  /** When set, shows a "pin beside" control (only meaningful on the primary slot). */
  onPin?: () => void;
  /** When set, shows a maximize/restore control for the panel stack. */
  onToggleMaximize?: () => void;
  isMaximized?: boolean;
  /** When set, shows a "save as view" control (named filter presets). */
  onSaveView?: () => void;
}

// Escape should close only the panel the analyst most recently opened —
// with two SlidePanels mounted at once (the panel stack), every instance
// used to install its own document-level listener, so one Escape press
// closed both. This tracks mount order and lets only the topmost act.
let panelStack: symbol[] = [];

/**
 * Shared slide-over panel shell with dialog semantics, Esc-close, Tab focus trap, and focus restore.
 */
export function SlidePanel({
  title,
  icon,
  onClose,
  children,
  headerExtra,
  widthExpanded,
  onToggleWidth,
  onPin,
  onToggleMaximize,
  isMaximized,
  onSaveView,
}: SlidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const stackTokenRef = useRef<symbol>(Symbol('slide-panel'));

  useEffect(() => {
    const token = stackTokenRef.current;
    panelStack.push(token);
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const node = panelRef.current;
    const focusable = node?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    focusable?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (panelStack[panelStack.length - 1] !== token) return;
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !node) return;

      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (el) => !el.hasAttribute('disabled') && el.getAttribute('aria-hidden') !== 'true',
      );
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;

      if (e.shiftKey) {
        if (active === first || !node.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !node.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      panelStack = panelStack.filter((tok) => tok !== token);
      previouslyFocused.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      ref={panelRef}
      className="h-full flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700/50 bg-surface-800">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-0.5 h-4 bg-deloitte-green rounded-full shrink-0" />
          {icon}
          <h3 id={titleId} className="text-sm font-semibold text-white truncate">
            {title}
          </h3>
          {headerExtra}
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {onSaveView && (
            <IconButton label={t('panel.saveView')} onClick={onSaveView} title={t('panel.saveView')}>
              <BookmarkPlus className="w-4 h-4" aria-hidden="true" />
            </IconButton>
          )}
          {onPin && (
            <IconButton label={t('panel.pin')} onClick={onPin} title={t('panel.pin')}>
              <Pin className="w-4 h-4" aria-hidden="true" />
            </IconButton>
          )}
          {onToggleMaximize && (
            <IconButton
              label={isMaximized ? t('panel.restore') : t('panel.maximize')}
              onClick={onToggleMaximize}
              title={isMaximized ? t('panel.restore') : t('panel.maximize')}
            >
              {isMaximized ? (
                <Minimize2 className="w-4 h-4" aria-hidden="true" />
              ) : (
                <Maximize2 className="w-4 h-4" aria-hidden="true" />
              )}
            </IconButton>
          )}
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
