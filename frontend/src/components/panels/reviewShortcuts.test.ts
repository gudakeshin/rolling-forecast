import { describe, expect, it } from 'vitest';
import { previewLines, reviewShortcutFor } from './ReviewDashboardPanel';

const button = document.createElement('button');
const input = document.createElement('input');

describe('reviewShortcutFor', () => {
  it('maps row keys to actions', () => {
    expect(reviewShortcutFor({ key: 'a', target: button })).toBe('approve');
    expect(reviewShortcutFor({ key: 'r', target: button })).toBe('reject');
    expect(reviewShortcutFor({ key: 'x', target: button })).toBe('select');
    expect(reviewShortcutFor({ key: 'j', target: button })).toBe('next');
    expect(reviewShortcutFor({ key: 'ArrowDown', target: button })).toBe('next');
    expect(reviewShortcutFor({ key: 'k', target: button })).toBe('prev');
    expect(reviewShortcutFor({ key: 'ArrowUp', target: button })).toBe('prev');
    expect(reviewShortcutFor({ key: 'Enter', target: button })).toBeNull();
  });

  it('ignores typing in fields and modified keys', () => {
    expect(reviewShortcutFor({ key: 'a', target: input })).toBeNull();
    expect(reviewShortcutFor({ key: 'k', target: button, metaKey: true })).toBeNull();
    expect(reviewShortcutFor({ key: 'a', target: button, ctrlKey: true })).toBeNull();
  });
});

describe('previewLines', () => {
  it('lists short sets in full and summarizes long ones', () => {
    expect(previewLines(['A', 'B'])).toBe('A, B');
    expect(previewLines(['A', 'B', 'C', 'D'], 2)).toBe('A, B and 2 more');
  });
});
