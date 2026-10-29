import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { InputBar } from './InputBar';
import { FollowUpChips } from './FollowUpChips';
import { stepHistory } from '../../utils/promptHistory';

vi.mock('../../api/dashboard', () => ({
  searchLineItems: vi.fn().mockResolvedValue({
    items: [
      { id: 1, name: 'Product Revenue', account_code: '4000', category: 'Revenue' },
      { id: 2, name: 'Service Revenue', account_code: '4100', category: 'Revenue' },
    ],
  }),
}));

function setup() {
  const onSend = vi.fn();
  render(<InputBar onSend={onSend} onStop={vi.fn()} isStreaming={false} />);
  const box = screen.getByRole('textbox') as HTMLTextAreaElement;
  const type = (value: string) => fireEvent.change(box, { target: { value, selectionStart: value.length } });
  return { onSend, box, type };
}

describe('composer (plan 6.2)', () => {
  beforeEach(() => localStorage.clear());

  it('/ opens the command menu and Enter runs the highlighted command', () => {
    const { onSend, box, type } = setup();
    type('/gen');
    expect(screen.getByRole('listbox')).toBeTruthy();
    expect(screen.getByRole('option', { name: /\/generate/ })).toBeTruthy();
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Generate a baseline forecast');
  });

  it('@ searches line items and Enter inserts the chosen one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { box, type } = setup();
    type('Explain @rev');
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2));
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box.value).toBe('Explain Service Revenue ');
    vi.useRealTimers();
  });

  it('↑ recalls sent prompts, ↓ walks back to an empty composer', () => {
    const { onSend, box, type } = setup();
    type('first question');
    fireEvent.keyDown(box, { key: 'Enter' });
    type('second question');
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(box.value).toBe('second question');
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(box.value).toBe('first question');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(box.value).toBe('second question');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    expect(box.value).toBe('');
  });

  it('arrows inside a typed draft do not replace it with history', () => {
    const { box, type } = setup();
    type('sent');
    fireEvent.keyDown(box, { key: 'Enter' });
    type('a draft');
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(box.value).toBe('a draft');
  });

  it('history survives a remount', () => {
    const first = setup();
    first.type('persisted');
    fireEvent.keyDown(first.box, { key: 'Enter' });
    document.body.innerHTML = '';
    const second = setup();
    fireEvent.keyDown(second.box, { key: 'ArrowUp' });
    expect(second.box.value).toBe('persisted');
  });
});

describe('stepHistory', () => {
  it('moves through bounds', () => {
    expect(stepHistory(0, null, -1)).toBeNull();
    expect(stepHistory(3, null, -1)).toBe(2);
    expect(stepHistory(3, 0, -1)).toBe(0);
    expect(stepHistory(3, 2, 1)).toBeNull();
  });
});

describe('FollowUpChips (plan 6.2)', () => {
  const message = (types: string[]) =>
    ({ id: 'm', role: 'assistant', content: '', content_blocks: types.map((type) => ({ type, data: {} })) }) as any;

  it('offers context-aware continuations and sends the one clicked', () => {
    const onSend = vi.fn();
    render(<FollowUpChips message={message(['table'])} onSend={onSend} />);
    const chips = screen.getAllByRole('button');
    expect(chips).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'What are the biggest deviations?' }));
    expect(onSend).toHaveBeenCalledWith('What are the biggest deviations?');
  });

  it('stays out of the way when the reply already has actions', () => {
    const { container } = render(<FollowUpChips message={message(['action'])} onSend={vi.fn()} />);
    expect(container.innerHTML).toBe('');
  });
});
