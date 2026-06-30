import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

// Collecct's backend base (baked at build from NEXT_PUBLIC_API_BASE). Callers include the
// full `/api/...` path, so there is no `/api` suffix here.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

// Create axios instance
export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Track if a refresh is in progress to prevent multiple simultaneous refreshes.
let isRefreshing = false;
let refreshSubscribers: ((token: string) => void)[] = [];

function onRefreshed(token: string) {
  refreshSubscribers.forEach((callback) => callback(token));
  refreshSubscribers = [];
}

function addRefreshSubscriber(callback: (token: string) => void) {
  refreshSubscribers.push(callback);
}

// Request interceptor — add Authorization header from localStorage.
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const accessToken =
      typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;

    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`;
    }

    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor — on 401, refresh the access token once and retry.
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      // Don't try to refresh on public auth routes — let them fail through.
      const isPublicRoute =
        originalRequest.url?.includes('/api/auth/login') ||
        originalRequest.url?.includes('/api/auth/signup') ||
        originalRequest.url?.includes('/api/auth/me') || // allow /me to fail silently
        originalRequest.url?.includes('/api/auth/refresh');

      if (isPublicRoute) {
        // If the refresh endpoint itself 401s, the session is dead — bounce to login.
        if (
          originalRequest.url?.includes('/api/auth/refresh') &&
          typeof window !== 'undefined'
        ) {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/auth/login';
        }
        return Promise.reject(error);
      }

      originalRequest._retry = true;

      // If a refresh is already in flight, queue this request until it resolves.
      if (isRefreshing) {
        return new Promise((resolve) => {
          addRefreshSubscriber((token: string) => {
            originalRequest.headers.Authorization = `Bearer ${token}`;
            resolve(apiClient(originalRequest));
          });
        });
      }

      isRefreshing = true;

      try {
        const refreshToken =
          typeof window !== 'undefined'
            ? localStorage.getItem('refresh_token')
            : null;

        if (!refreshToken) {
          throw new Error('No refresh token available');
        }

        const response = await apiClient.post('/api/auth/refresh', {
          refresh_token: refreshToken,
        });

        // Store the rotated tokens.
        localStorage.setItem('access_token', response.data.access_token);
        localStorage.setItem('refresh_token', response.data.refresh_token);

        isRefreshing = false;
        onRefreshed(response.data.access_token);

        // Retry the original request with the new token.
        originalRequest.headers.Authorization = `Bearer ${response.data.access_token}`;
        return apiClient(originalRequest);
      } catch (refreshError) {
        // Refresh failed — clear tokens and send the user to login.
        isRefreshing = false;
        refreshSubscribers = [];

        if (typeof window !== 'undefined') {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/auth/login';
        }

        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;
