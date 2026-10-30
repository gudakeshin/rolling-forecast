import { create } from 'zustand';

interface ComposerState {
  /** Bumped whenever something (the ⌘K palette, a panel action) wants the
   * chat composer focused. InputBar watches this and calls .focus(). */
  focusRequestId: number;
  requestFocus: () => void;
  /** A prompt a panel action placed in the composer for the user to review
   * and send (never sent automatically). Bumping prefillId re-applies it even
   * when the same text is pushed twice. */
  prefillText: string;
  prefillId: number;
  prefill: (text: string) => void;
}

export const useComposerStore = create<ComposerState>((set) => ({
  focusRequestId: 0,
  requestFocus: () => set((s) => ({ focusRequestId: s.focusRequestId + 1 })),
  prefillText: '',
  prefillId: 0,
  prefill: (text) => set((s) => ({ prefillText: text, prefillId: s.prefillId + 1 })),
}));
