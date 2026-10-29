import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { firstRunSteps } from './GettingStarted';
import { EmptyForecastState } from '../panels/EmptyForecastState';
import { useAuthStore } from '../../store/authStore';

describe('firstRunSteps', () => {
  it('starts with nothing done', () => {
    expect(firstRunSteps(false, []).map((s) => s.done)).toEqual([false, false, false, false]);
  });

  it('tracks progress from datasets and version statuses', () => {
    expect(firstRunSteps(true, ['draft']).map((s) => s.done)).toEqual([true, true, false, false]);
    expect(firstRunSteps(true, ['draft', 'in_review']).map((s) => s.done)).toEqual([true, true, true, false]);
    expect(firstRunSteps(true, ['published']).every((s) => s.done)).toBe(true);
  });
});

describe('EmptyForecastState', () => {
  beforeEach(() => useAuthStore.setState({ user: null } as any));

  it('offers only the actions the user may take', () => {
    useAuthStore.setState({ user: { can_input: true, can_generate: false } as any });
    render(<EmptyForecastState />);
    expect(screen.getByRole('button', { name: /upload actuals/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /run forecast/i })).toBeNull();
  });

  it('offers both to an analyst', () => {
    useAuthStore.setState({ user: { can_input: true, can_generate: true } as any });
    render(<EmptyForecastState />);
    expect(screen.getByRole('button', { name: /run forecast/i })).toBeTruthy();
  });
});
