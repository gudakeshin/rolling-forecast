import { describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { useComposerStore } from './composerStore';
import { dispatchRecommendedAction } from '../utils/recommendedActions';
import { InputBar } from '../components/chat/InputBar';

describe('composer prefill', () => {
  it('recommended-action fallback fills the composer instead of the clipboard', async () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    const before = useComposerStore.getState().prefillId;

    const handled = await dispatchRecommendedAction(
      { type: 'something_else', label: 'Explain the drop' },
      { lineItemName: 'Freight', openPanel: vi.fn() },
    );

    expect(handled).toBe(true);
    expect(writeText).not.toHaveBeenCalled();
    expect(useComposerStore.getState().prefillId).toBe(before + 1);
    expect(useComposerStore.getState().prefillText).toBe('Explain the drop for "Freight"');
  });

  it('InputBar shows a prefilled prompt without sending it', () => {
    const onSend = vi.fn();
    render(<InputBar onSend={onSend} onStop={vi.fn()} isStreaming={false} />);
    act(() => useComposerStore.getState().prefill('Investigate anomaly for Freight'));
    const box = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(box.value).toBe('Investigate anomaly for Freight');
    expect(onSend).not.toHaveBeenCalled();
  });
});
