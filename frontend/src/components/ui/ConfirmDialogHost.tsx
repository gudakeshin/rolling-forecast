import { useEffect, useId, useRef } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useConfirmStore } from '../../store/confirmStore';
import { t } from '../../i18n';

const FOCUSABLE_SELECTOR = 'button:not([disabled])';

/**
 * Single app-wide confirm dialog, driven by confirmDialog() (store/confirmStore.ts).
 * Mounted once at the App root, mirroring ToastHost — replaces window.confirm so
 * destructive actions get a themed, keyboard-accessible prompt instead of the
 * browser's native (and untestable) confirm box.
 */
export function ConfirmDialogHost() {
  const pending = useConfirmStore((s) => s.pending);
  const settle = useConfirmStore((s) => s.settle);
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!pending) return;
    confirmBtnRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        settle(false);
      } else if (e.key === 'Tab') {
        const node = dialogRef.current;
        if (!node) return;
        const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey ? active === first : active === last) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [pending, settle]);

  if (!pending) return null;

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- backdrop-click dismiss; Escape and the Cancel button already cover keyboard/AT
    <div
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/30 px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) settle(false);
      }}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-sm rounded-xl bg-surface-800 border border-surface-700/50 shadow-xl shadow-black/40 p-5"
      >
        <div className="flex items-start gap-3">
          {pending.danger && (
            <AlertTriangle className="w-5 h-5 mt-0.5 text-red-400 flex-shrink-0" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-white">
              {pending.title || t('dialog.confirm')}
            </h2>
            <p className="mt-1.5 text-sm text-surface-300 leading-snug">{pending.message}</p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => settle(false)}
            className="px-3 py-1.5 rounded-lg text-sm font-medium text-surface-300 hover:bg-surface-700/40 transition-colors"
          >
            {pending.cancelLabel || t('dialog.cancel')}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            onClick={() => settle(true)}
            className={
              pending.danger
                ? 'px-3 py-1.5 rounded-lg text-sm font-medium bg-red-500/90 text-white hover:bg-red-500 transition-colors'
                : 'px-3 py-1.5 rounded-lg text-sm font-medium bg-deloitte-green text-white hover:bg-deloitte-green-dark transition-colors'
            }
          >
            {pending.confirmLabel || t('dialog.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
