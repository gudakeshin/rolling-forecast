import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ValueMetrics } from './ValueMetrics';
import { useAuthStore } from '../../store/authStore';

vi.mock('../../api/client', () => ({
  apiGet: vi.fn().mockResolvedValue({
    versions: [{ version_id: 'a' }, { version_id: 'b' }],
    trends: {
      cycle_days: { latest: 4, change: -6, improving: true },
      override_rate: { latest: 0.25, change: 0.1, improving: false },
      flagged_review_share: { latest: 1, change: 0.5, improving: true },
      fva_vs_naive: { latest: 20, change: 0, improving: null },
    },
  }),
}));

describe('ValueMetrics', () => {
  it('shows latest values with direction-aware trends', async () => {
    useAuthStore.setState({ user: { business_unit_id: 'bu1' } as any });
    render(<ValueMetrics />);
    await waitFor(() => expect(screen.getByText('4.0 d')).toBeTruthy());
    expect(screen.getByText('Value delivered · last 2 versions')).toBeTruthy();
    expect(screen.getByText('25%')).toBeTruthy();
    expect(screen.getByText('+20.0 pts')).toBeTruthy();
    expect(screen.getByText('(worsening)', { exact: false })).toBeTruthy();
  });
});
