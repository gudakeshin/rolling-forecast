import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/notifications', () => ({
  listNotifications: vi.fn().mockResolvedValue({
    unread_count: 2,
    notifications: [
      {
        id: 'n1', kind: 'approval_pending', title: 'FC-2026-10-v3 awaits your approval', body: null,
        entity_type: 'forecast_version', entity_id: 'v3', link_panel: 'approvals',
        link_params: { version_id: 'v3' }, read: false, created_at: new Date().toISOString(),
      },
      {
        id: 'n2', kind: 'anomaly_detected', title: 'Critical anomaly: Freight', body: 'Spike vs history',
        entity_type: 'forecast_line_result', entity_id: 'r9', link_panel: 'anomaly_dashboard',
        link_params: { version_id: 'v3' }, read: false, created_at: new Date().toISOString(),
      },
    ],
  }),
  markNotificationRead: vi.fn().mockResolvedValue({ success: true }),
  markAllNotificationsRead: vi.fn().mockResolvedValue({ success: true }),
}));

import { markNotificationRead } from '../../api/notifications';
import { NotificationBell } from './NotificationBell';
import { usePrimaryPanelStore } from '../../store/panelStore';

describe('NotificationBell (plan 3.3)', () => {
  it('shows the unread count and opens the linked panel when a notification is chosen', async () => {
    render(<NotificationBell />);
    const bell = screen.getByRole('button', { name: /notifications/i });
    await waitFor(() => expect(bell.textContent).toContain('2'));

    fireEvent.click(bell);
    fireEvent.click(screen.getByText('Critical anomaly: Freight'));

    expect(markNotificationRead).toHaveBeenCalledWith('n2');
    expect(usePrimaryPanelStore.getState().panelType).toBe('anomaly_dashboard');
    expect(usePrimaryPanelStore.getState().panelParams).toMatchObject({ version_id: 'v3' });
  });
});
