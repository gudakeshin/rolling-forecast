import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useNavItems } from './useNavItems';
import { useAuthStore } from '../store/authStore';
import { useVersionStore } from '../store/versionStore';

function comparisonItem() {
  const { result } = renderHook(() => useNavItems());
  return result.current.flatMap((g) => g.items).find((i) => i.key === 'comparison')!;
}

describe('comparison nav item', () => {
  it('stays visible but disabled with a reason until two versions exist', () => {
    useAuthStore.setState({ user: { can_generate: true } as any });
    useVersionStore.setState({
      versions: [{ id: 'v1', scenario: 'base', status: 'draft' } as any],
      activeVersionId: 'v1',
      activeScenario: 'base',
    });
    const item = comparisonItem();
    expect(item.show).toBe(true);
    expect(item.disabledReason).toBe('nav.comparison.needsTwo');

    useVersionStore.setState({
      versions: [
        { id: 'v1', scenario: 'base', status: 'draft' } as any,
        { id: 'v2', scenario: 'base', status: 'draft' } as any,
      ],
    });
    expect(comparisonItem().disabledReason).toBeUndefined();
  });
});
