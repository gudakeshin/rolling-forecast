import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { CalibrationBanner } from './ForecastTablePanel';

describe('CalibrationBanner (plan 1.5)', () => {
  it('reports realized coverage once cycles have closed', () => {
    const { container } = render(
      <CalibrationBanner
        calibration={{
          calibrated_lines: 12, total_lines: 15, realized_coverage: 0.78,
          lines_with_realized_coverage: 6, target_coverage: 0.8,
        }}
      />,
    );
    const text = container.textContent ?? '';
    expect(text).toContain('12 of 15');
    expect(text).toContain('78% of actuals landed inside the 80% band');
  });

  it('makes no coverage claim without closed cycles', () => {
    const { container } = render(
      <CalibrationBanner
        calibration={{
          calibrated_lines: 3, total_lines: 3, realized_coverage: null,
          lines_with_realized_coverage: 0, target_coverage: 0.8,
        }}
      />,
    );
    expect(container.textContent).toContain('Not enough closed cycles yet');
    expect(container.textContent).not.toContain('landed inside');
  });
});
