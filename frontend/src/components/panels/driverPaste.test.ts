import { describe, expect, it } from 'vitest';
import { pastedGridUpdates } from './DriverInputPanel';
import { usePrimaryPanelStore, useSecondaryPanelStore } from '../../store/panelStore';

const items = [
  { id: 1, account_code: '4000' },
  { id: 2, account_code: '4100' },
  { id: 3, account_code: '5000' },
];

describe('pastedGridUpdates (plan 6.3)', () => {
  it('matches a code/value block by account code, in any order', () => {
    expect(pastedGridUpdates('5000\t12\r\n4000\t7\n', items, 1)).toEqual({ li_3: '12', li_1: '7' });
  });

  it('fills a single column downward from the paste target', () => {
    expect(pastedGridUpdates('10\n20\n30\n40', items, 2)).toEqual({ li_2: '10', li_3: '20' });
  });

  it('leaves a single cell to the browser', () => {
    expect(pastedGridUpdates('42', items, 1)).toBeNull();
    expect(pastedGridUpdates('', items, 1)).toBeNull();
  });

  it('has no 30-row cap', () => {
    const many = Array.from({ length: 60 }, (_, i) => ({ id: i + 1, account_code: `A${i}` }));
    const text = many.map((li) => `${li.account_code}\t${li.id}`).join('\n');
    expect(Object.keys(pastedGridUpdates(text, many, 1)!)).toHaveLength(60);
  });
});

describe('panel stack (plan 4.1)', () => {
  it('keeps two panels open independently', () => {
    usePrimaryPanelStore.getState().openPanel('forecast_table', { version_id: 'v1' });
    useSecondaryPanelStore.getState().openPanel('explainability', { version_id: 'v1' });
    expect(usePrimaryPanelStore.getState().panelType).toBe('forecast_table');
    expect(useSecondaryPanelStore.getState().panelType).toBe('explainability');
    useSecondaryPanelStore.getState().closePanel();
    expect(usePrimaryPanelStore.getState().isOpen).toBe(true);
  });
});
