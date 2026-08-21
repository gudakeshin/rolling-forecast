import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast, useToastStore } from './toastStore';

describe('toastStore', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
    vi.useFakeTimers();
  });

  it('pushes success and error toasts', () => {
    toast.success('Saved');
    toast.error('Failed');
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(2);
    expect(toasts[0].kind).toBe('success');
    expect(toasts[0].message).toBe('Saved');
    expect(toasts[1].kind).toBe('error');
  });

  it('auto-dismisses after timeout', () => {
    toast.info('Working');
    expect(useToastStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(4000);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('dismiss removes a toast by id', () => {
    toast.success('One');
    const id = useToastStore.getState().toasts[0].id;
    useToastStore.getState().dismiss(id);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
