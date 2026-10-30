import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CustomKpis, formatKpiValue } from './CustomKpis';
import { useAuthStore } from '../../store/authStore';
import type { KpiResult } from '../../api/kpis';

vi.mock('../../api/kpis', () => ({
  evaluateKpis: vi.fn(),
  validateKpiFormula: vi.fn(),
  createKpi: vi.fn(),
  deleteKpi: vi.fn(),
}));

function kpi(partial: Partial<KpiResult>): KpiResult {
  return {
    id: 'k1',
    name: 'Gross margin %',
    formula: '([category:Revenue] - [category:COGS]) / [category:Revenue]',
    display_format: 'percent',
    description: null,
    total: 0.4,
    by_period: [],
    missing: [],
    error: null,
    ...partial,
  };
}

function asUser(canAdmin: boolean) {
  useAuthStore.setState({ user: { can_admin: canAdmin } as any });
}

describe('formatKpiValue', () => {
  it('formats by display format and shows a dash for no value', () => {
    expect(formatKpiValue(0.4, 'percent')).toBe('40.0%');
    expect(formatKpiValue(1.234, 'ratio')).toBe('1.23x');
    expect(formatKpiValue(null, 'number')).toBe('—');
    expect(formatKpiValue(Number.NaN, 'currency')).toBe('—');
  });
});

describe('CustomKpis', () => {
  beforeEach(() => asUser(false));

  it('renders KPI values and notes for missing inputs', () => {
    render(
      <CustomKpis
        versionId="v1"
        initial={[kpi({}), kpi({ id: 'k2', name: 'Freight share', total: null, missing: ['[Freight]'] })]}
      />,
    );
    expect(screen.getByText('40.0%')).toBeTruthy();
    expect(screen.getByText('No forecast for [Freight]')).toBeTruthy();
    expect(screen.queryByText('Manage')).toBeNull();
  });

  it('hides the card from non-admins when there are no KPIs', () => {
    const { container } = render(<CustomKpis versionId="v1" initial={[]} />);
    expect(container.innerHTML).toBe('');
  });

  it('offers management to admins, even with no KPIs yet', () => {
    asUser(true);
    render(<CustomKpis versionId="v1" initial={[]} />);
    expect(screen.getByText('Manage')).toBeTruthy();
    expect(screen.getByText(/No KPIs yet/)).toBeTruthy();
  });
});
