import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'info';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
  action?: ToastAction;
}

interface PushOptions {
  action?: ToastAction;
  durationMs?: number;
}

interface ToastState {
  toasts: Toast[];
  push: (kind: ToastKind, message: string, opts?: PushOptions) => void;
  dismiss: (id: string) => void;
}

const AUTO_DISMISS_MS = 4000;
// Longer than a plain toast — an undo action needs time to be seen and clicked,
// not just read.
const UNDO_DISMISS_MS = 8000;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],

  push: (kind, message, opts) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((state) => ({ toasts: [...state.toasts, { id, kind, message, action: opts?.action }] }));
    window.setTimeout(() => {
      get().dismiss(id);
    }, opts?.durationMs ?? AUTO_DISMISS_MS);
  },

  dismiss: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

/** Non-hook helper for use outside React components. */
export const toast = {
  success: (message: string) => useToastStore.getState().push('success', message),
  error: (message: string) => useToastStore.getState().push('error', message),
  info: (message: string) => useToastStore.getState().push('info', message),
  /** A toast with an "Undo" button, up for `durationMs` before it's too late to reverse. */
  undo: (message: string, onUndo: () => void, durationMs = UNDO_DISMISS_MS) =>
    useToastStore.getState().push('info', message, {
      action: { label: 'Undo', onClick: onUndo },
      durationMs,
    }),
};
