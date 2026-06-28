import apiClient from './client';
import { LoginCredentials, SignupData, User } from '@/lib/types';

interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

interface SignupResponse {
  email: string;
  message: string;
  requires_verification: boolean;
}

interface VerifyEmailResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export const authApi = {
  // Log in with email + password.
  login: async (credentials: LoginCredentials): Promise<LoginResponse> => {
    const response = await apiClient.post<LoginResponse>('/api/auth/login', credentials);
    return response.data;
  },

  // Sign up a new user (may be invite-only depending on backend config).
  signup: async (data: SignupData): Promise<SignupResponse> => {
    const response = await apiClient.post<SignupResponse>('/api/auth/signup', data);
    return response.data;
  },

  // Fetch the current authenticated user.
  getCurrentUser: async (): Promise<User> => {
    const response = await apiClient.get<User>('/api/auth/me');
    return response.data;
  },

  // Log out — revokes the refresh token server-side.
  logout: async (): Promise<void> => {
    const refreshToken =
      typeof window !== 'undefined' ? localStorage.getItem('refresh_token') : null;
    await apiClient.post('/api/auth/logout', { refresh_token: refreshToken });
  },

  // Verify an email-verification token; returns tokens + user to log in.
  verifyEmail: async (token: string): Promise<VerifyEmailResponse> => {
    const response = await apiClient.post<VerifyEmailResponse>('/api/auth/verify-email', {
      token,
    });
    return response.data;
  },

  // Resend the verification email.
  resendVerification: async (email: string): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>(
      '/api/auth/resend-verification',
      { email }
    );
    return response.data;
  },

  // Request a password-reset link.
  forgotPassword: async (email: string): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>('/api/auth/forgot-password', {
      email,
    });
    return response.data;
  },

  // Reset the password using a reset token.
  resetPassword: async (
    token: string,
    newPassword: string
  ): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>('/api/auth/reset-password', {
      token,
      new_password: newPassword,
    });
    return response.data;
  },
};
