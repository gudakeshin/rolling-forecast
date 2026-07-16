import { CheckCircle, AlertTriangle, Info, X } from 'lucide-react';
import { useToastStore, type ToastKind } from '../../store/toastStore';

const KIND_STYLES: Record<ToastKind, { border: string; icon: typeof CheckCircle; iconClass: string }> = {
  success: {
    border: 'border-deloitte-green/30',
    icon: CheckCircle,
    iconClass: 'text-deloitte-green',
  },
  error: {
    border: 'border-red-500/30',
    icon: AlertTriangle,
    iconClass: 'text-red-400',
  },
  info: {
    border: 'border-surface-600',
    icon: Info,
    iconClass: 'text-surface-300',
  },
};

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none"
      aria-live="polite"
    >
      {toasts.map((t) => {
        const style = KIND_STYLES[t.kind];
        const Icon = style.icon;
        return (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-2.5 px-3.5 py-3 rounded-xl bg-surface-800 border ${style.border} shadow-lg shadow-black/40`}
          >
            <Icon className={`w-4 h-4 mt-0.5 flex-shrink-0 ${style.iconClass}`} />
            <p className="flex-1 text-sm text-white leading-snug">{t.message}</p>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="text-surface-500 hover:text-white transition-colors flex-shrink-0"
              aria-label="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
