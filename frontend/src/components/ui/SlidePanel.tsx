import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { IconButton } from './Pressable';

interface SlidePanelProps {
  title: string;
  icon?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  headerExtra?: ReactNode;
}

/**
 * Shared slide-over panel shell with dialog semantics, Esc-close, and focus restore.
 */
export function SlidePanel({ title, icon, onClose, children, headerExtra }: SlidePanelProps) {
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
        <IconButton label="Close panel" onClick={onClose}>
          <X className="w-4 h-4" aria-hidden="true" />
        </IconButton>
      </div>
      <div className="flex-1 overflow-y-auto p-4">{children}</div>
    </div>
  );
}
