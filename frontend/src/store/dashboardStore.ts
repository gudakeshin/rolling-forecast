import { create } from 'zustand';
import {
  getExecutiveSummary,
  getReviewSummary,
  getAccuracySummary,
} from '../api/dashboard';

interface DashboardState {
  // Dashboard data caches
  executiveData: Record<string, any> | null;
  reviewData: Record<string, any> | null;
  accuracyData: Record<string, any> | null;

  // Loading states
  isLoadingExecutive: boolean;
  isLoadingReview: boolean;
  isLoadingAccuracy: boolean;

  // Error states
  executiveError: string | null;
  reviewError: string | null;
  accuracyError: string | null;

  // Current view
  activeDashboard: 'executive' | 'review' | 'accuracy' | null;

  // Actions
  setExecutiveData: (data: Record<string, any> | null) => void;
  setReviewData: (data: Record<string, any> | null) => void;
  setAccuracyData: (data: Record<string, any> | null) => void;
  setActiveDashboard: (dashboard: 'executive' | 'review' | 'accuracy' | null) => void;
  setLoadingExecutive: (loading: boolean) => void;
  setLoadingReview: (loading: boolean) => void;
  setLoadingAccuracy: (loading: boolean) => void;
  clearAll: () => void;

  // Fetch actions
  fetchExecutive: (versionId: string) => Promise<void>;
  fetchReview: (versionId: string) => Promise<void>;
  fetchAccuracy: (versionId: string) => Promise<void>;
  fetchAllDashboards: (versionId: string) => Promise<void>;
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  executiveData: null,
  reviewData: null,
  accuracyData: null,
  isLoadingExecutive: false,
  isLoadingReview: false,
  isLoadingAccuracy: false,
  executiveError: null,
  reviewError: null,
  accuracyError: null,
  activeDashboard: null,

  setExecutiveData: (data) => set({ executiveData: data, isLoadingExecutive: false }),
  setReviewData: (data) => set({ reviewData: data, isLoadingReview: false }),
  setAccuracyData: (data) => set({ accuracyData: data, isLoadingAccuracy: false }),
  setActiveDashboard: (dashboard) => set({ activeDashboard: dashboard }),
  setLoadingExecutive: (loading) => set({ isLoadingExecutive: loading }),
  setLoadingReview: (loading) => set({ isLoadingReview: loading }),
  setLoadingAccuracy: (loading) => set({ isLoadingAccuracy: loading }),
  clearAll: () =>
    set({
      executiveData: null,
      reviewData: null,
      accuracyData: null,
      activeDashboard: null,
      executiveError: null,
      reviewError: null,
      accuracyError: null,
    }),

  fetchExecutive: async (versionId: string) => {
    set({ isLoadingExecutive: true, executiveError: null });
    try {
      const data = await getExecutiveSummary(versionId);
      set({ executiveData: data, isLoadingExecutive: false });
    } catch (error: any) {
      set({ executiveError: error.message, isLoadingExecutive: false });
    }
  },

  fetchReview: async (versionId: string) => {
    set({ isLoadingReview: true, reviewError: null });
    try {
      const data = await getReviewSummary(versionId);
      set({ reviewData: data, isLoadingReview: false });
    } catch (error: any) {
      set({ reviewError: error.message, isLoadingReview: false });
    }
  },

  fetchAccuracy: async (versionId: string) => {
    set({ isLoadingAccuracy: true, accuracyError: null });
    try {
      const data = await getAccuracySummary(versionId);
      set({ accuracyData: data, isLoadingAccuracy: false });
    } catch (error: any) {
      set({ accuracyError: error.message, isLoadingAccuracy: false });
    }
  },

  fetchAllDashboards: async (versionId: string) => {
    const { fetchExecutive, fetchReview, fetchAccuracy } = get();
    await Promise.allSettled([
      fetchExecutive(versionId),
      fetchReview(versionId),
      fetchAccuracy(versionId),
    ]);
  },
}));
