import { apiGet, apiPost } from './client';

export interface AppNotification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  entity_type: string | null;
  entity_id: string | null;
  link_panel: string | null;
  link_params: Record<string, any> | null;
  read: boolean;
  created_at: string | null;
}

interface ListResponse {
  notifications: AppNotification[];
  unread_count: number;
}

export function listNotifications(limit = 50): Promise<ListResponse> {
  return apiGet<ListResponse>(`/notifications?limit=${limit}`);
}

export function markNotificationRead(id: string): Promise<{ success: boolean }> {
  return apiPost(`/notifications/${id}/read`);
}

export function markAllNotificationsRead(): Promise<{ success: boolean }> {
  return apiPost('/notifications/read-all');
}
