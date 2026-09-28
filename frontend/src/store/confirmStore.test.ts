import { describe, expect, it } from 'vitest';
import { confirmDialog, useConfirmStore } from './confirmStore';

describe('confirmStore', () => {
  it('resolves true when settled with true', async () => {
    const promise = confirmDialog('Delete this?', { title: 'Confirm', danger: true });
    expect(useConfirmStore.getState().pending?.message).toBe('Delete this?');
    expect(useConfirmStore.getState().pending?.title).toBe('Confirm');
    expect(useConfirmStore.getState().pending?.danger).toBe(true);

    useConfirmStore.getState().settle(true);
    await expect(promise).resolves.toBe(true);
    expect(useConfirmStore.getState().pending).toBeNull();
  });

  it('resolves false when settled with false', async () => {
    const promise = confirmDialog('Proceed?');
    useConfirmStore.getState().settle(false);
    await expect(promise).resolves.toBe(false);
  });

  it('a second request cancels the first instead of stacking', async () => {
    const first = confirmDialog('First?');
    const second = confirmDialog('Second?');
    expect(useConfirmStore.getState().pending?.message).toBe('Second?');

    await expect(first).resolves.toBe(false);
    useConfirmStore.getState().settle(true);
    await expect(second).resolves.toBe(true);
  });
});
