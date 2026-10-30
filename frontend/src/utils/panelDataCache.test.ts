import { beforeEach, describe, expect, it } from 'vitest';
import { clearPanelDataCache, getCachedPanelData, setCachedPanelData } from './panelDataCache';

describe('panelDataCache (plan 4.1)', () => {
  beforeEach(() => clearPanelDataCache());

  it('returns what was cached for the same panel + params', () => {
    setCachedPanelData('forecast_table|/panel/forecast-table/v1?offset=0', { rows: [1] }, 1000);
    expect(getCachedPanelData('forecast_table|/panel/forecast-table/v1?offset=0', 2000)).toEqual({ rows: [1] });
    expect(getCachedPanelData('forecast_table|/panel/forecast-table/v2?offset=0', 2000)).toBeUndefined();
  });

  it('expires entries', () => {
    setCachedPanelData('k', 1, 0);
    expect(getCachedPanelData('k', 120_001)).toBeUndefined();
  });

  it('evicts the least recently used past its size limit', () => {
    for (let i = 0; i < 16; i += 1) setCachedPanelData(`k${i}`, i, 0);
    getCachedPanelData('k0', 1); // touch the oldest
    setCachedPanelData('k16', 16, 2);
    expect(getCachedPanelData('k0', 3)).toBe(0);
    expect(getCachedPanelData('k1', 3)).toBeUndefined();
  });
});
