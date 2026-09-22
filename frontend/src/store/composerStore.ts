import { create } from 'zustand';

interface ComposerState {
  /** Bumped whenever something (the ⌘K palette, a panel action) wants the
   * chat composer focused. InputBar watches this and calls .focus(). */
  focusRequestId: number;
  requestFocus: () => void;
}

export const useComposerStore = create<ComposerState>((set) => ({
  focusRequestId: 0,
  requestFocus: () => set((s) => ({ focusRequestId: s.focusRequestId + 1 })),
}));
