import { create } from 'zustand';
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type AppNotification,
} from '../api/notifications';

interface NotificationState {
  notifications: AppNotification[];
  unreadCount: number;
  isOpen: boolean;
  loading: boolean;

  setOpen: (open: boolean) => void;
  fetchNotifications: () => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  isOpen: false,
  loading: false,

  setOpen: (open) => set({ isOpen: open }),

  fetchNotifications: async () => {
    set({ loading: true });
    try {
      const res = await listNotifications();
      set({ notifications: res.notifications, unreadCount: res.unread_count, loading: false });
    } catch {
      set({ loading: false });
    }
  },

  markRead: async (id) => {
    const prev = get().notifications;
    set({
      notifications: prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
      unreadCount: Math.max(0, get().unreadCount - (prev.find((n) => n.id === id)?.read ? 0 : 1)),
    });
    try {
      await markNotificationRead(id);
    } catch {
      // Next poll reconciles state if this failed silently.
    }
  },

  markAllRead: async () => {
    set({
      notifications: get().notifications.map((n) => ({ ...n, read: true })),
      unreadCount: 0,
    });
    try {
      await markAllNotificationsRead();
    } catch {
      // Next poll reconciles state if this failed silently.
    }
  },
}));
