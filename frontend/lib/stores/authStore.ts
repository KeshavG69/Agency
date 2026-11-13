import { create } from 'zustand';
import { User, LoginCredentials, SignupData } from '@/lib/types';
import { authApi } from '@/lib/api/auth';
import { useConnectionStore } from '@/lib/stores/connectionStore';
import { useUiStore } from '@/lib/stores/uiStore';
import { useToastStore } from '@/lib/stores/toastStore';

/**
 * What a sign-in / sign-up failure may SAY on screen. A 4xx carries our own message
 * ("Invalid email or password"); anything else is a server fault whose text is not for users —
 * it can carry hostnames or stack detail — so it gets a fixed sentence instead.
 */
function authErrorMessage(error: any, fallback: string): string {
  const status = error?.response?.status;
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && typeof status === 'number' && status >= 400 && status < 500) {
    return detail;
  }
  if (!error?.response) return "Can't reach the server. Check your connection and try again.";
  return fallback;
}

interface SignupResult {
  email: string;
  message: string;
  requires_verification: boolean;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  error: string | null;
  isInitializing: boolean;

  // Actions
  login: (
    credentials:
      | LoginCredentials
      | { access_token: string; refresh_token: string; user: User }
  ) => Promise<void>;
  signup: (data: SignupData) => Promise<SignupResult>;
  logout: () => Promise<void>;
  fetchUser: () => Promise<void>;
  initializeAuth: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isLoading: false,
  error: null,
  isInitializing: true,

  login: async (credentials) => {
    try {
      set({ isLoading: true, error: null });

      // Support both email/password login and direct token login (email verify / invite accept).
      let response;
      if ('access_token' in credentials) {
        response = credentials;
      } else {
        response = await authApi.login(credentials);
      }

      localStorage.setItem('access_token', response.access_token);
      localStorage.setItem('refresh_token', response.refresh_token);

      set({ user: response.user, isLoading: false });
    } catch (error: any) {
      set({
        error: authErrorMessage(error, 'Sign-in is temporarily unavailable. Please try again in a moment.'),
        isLoading: false,
      });
      throw error;
    }
  },

  signup: async (data) => {
    try {
      set({ isLoading: true, error: null });
      const response = await authApi.signup(data);
      // No auto-login — the user must verify their email first.
      set({ isLoading: false });
      return response;
    } catch (error: any) {
      set({
        error: authErrorMessage(error, "Couldn't create your account. Please try again."),
        isLoading: false,
      });
      throw error;
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      // Clear cached connection + UI state so neither leaks to the next user on this browser.
      try {
        useConnectionStore.getState().reset();
        useUiStore.getState().reset();
        useToastStore.getState().clear();
      } catch {
        /* store not ready — ignore */
      }
      set({ user: null });
    }
  },

  fetchUser: async () => {
    try {
      const user = await authApi.getCurrentUser();
      set({ user });
    } catch (error: any) {
      // 401 just means not authenticated — expected, don't log it.
      if (error?.response?.status !== 401) {
        console.error('Failed to fetch user:', error);
      }
      set({ user: null });
    }
  },

  initializeAuth: async () => {
    set({ isInitializing: true });
    try {
      const accessToken =
        typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;

      if (accessToken) {
        await get().fetchUser();
      } else {
        set({ user: null });
      }
    } finally {
      set({ isInitializing: false });
    }
  },

  clearError: () => set({ error: null }),
}));
