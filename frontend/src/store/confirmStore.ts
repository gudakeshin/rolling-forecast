import { create } from 'zustand';

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Styles the confirm button as destructive (red) instead of the default accent. */
  danger?: boolean;
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

interface ConfirmState {
  pending: PendingConfirm | null;
  request: (opts: ConfirmOptions) => Promise<boolean>;
  settle: (value: boolean) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  pending: null,

  request: (opts) =>
    new Promise<boolean>((resolve) => {
      // Only one confirm dialog can be open at a time — a second request
      // while one is pending cancels the first rather than stacking dialogs.
      get().pending?.resolve(false);
      set({ pending: { ...opts, resolve } });
    }),

  settle: (value) => {
    const pending = get().pending;
    if (!pending) return;
    set({ pending: null });
    pending.resolve(value);
  },
}));

/** Promise-based replacement for window.confirm, rendered via <ConfirmDialogHost/>. */
export function confirmDialog(message: string, opts?: Omit<ConfirmOptions, 'message'>): Promise<boolean> {
  return useConfirmStore.getState().request({ message, ...opts });
}
