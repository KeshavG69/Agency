import apiClient from './client';
import { Invitation, ValidateTokenResponse, User } from '@/lib/types';

interface AcceptInvitationResponse {
  access_token: string;
  refresh_token: string;
  token_type?: string;
  user: User;
}

// New-user accepts carry profile fields; existing-user accepts carry only the token.
export type AcceptInvitationRequest =
  | { token: string }
  | {
      token: string;
      firstName: string;
      lastName: string;
      password: string;
      terms_accepted: boolean;
    };

export interface InvitationStats {
  pending: number;
  accepted: number;
  expired: number;
  revoked: number;
  total: number;
}

export const invitationsApi = {
  // Send an invitation (admin only).
  sendInvitation: async (data: {
    email: string;
    role: 'admin' | 'user';
  }): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>('/api/invitations', data);
    return response.data;
  },

  // List invitations, optionally filtered by status (admin only).
  listInvitations: async (
    status?: 'pending' | 'accepted' | 'expired' | 'revoked'
  ): Promise<Invitation[]> => {
    const params = status ? { status } : {};
    const response = await apiClient.get<Invitation[]>('/api/invitations', { params });
    return response.data;
  },

  // Invitation statistics (admin only).
  getStats: async (): Promise<InvitationStats> => {
    const response = await apiClient.get<InvitationStats>('/api/invitations/stats');
    return response.data;
  },

  // Revoke a pending invitation (admin only).
  revokeInvitation: async (invitationId: string): Promise<void> => {
    await apiClient.delete(`/api/invitations/${invitationId}`);
  },

  // Validate an invitation token (public).
  validateToken: async (token: string): Promise<ValidateTokenResponse> => {
    const response = await apiClient.get<ValidateTokenResponse>(
      `/api/invitations/validate/${token}`
    );
    return response.data;
  },

  // Accept an invitation (public). Returns tokens + user.
  acceptInvitation: async (
    data: AcceptInvitationRequest
  ): Promise<AcceptInvitationResponse> => {
    const response = await apiClient.post<AcceptInvitationResponse>(
      '/api/invitations/accept',
      data
    );
    return response.data;
  },
};
