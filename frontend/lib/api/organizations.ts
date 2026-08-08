import apiClient from './client';
import { TeamMember } from '@/lib/types';

export interface CompanyDetails {
  uei?: string;
  legal_business_name?: string | null;
  cage_code?: string | null;
  registration_status?: string | null;
  registration_expiration?: string | null;
  physical_address?: {
    line1?: string | null;
    city?: string | null;
    state?: string | null;
    zip?: string | null;
    country?: string | null;
  };
  entity_url?: string | null;
  naics?: string[];
  business_types?: string[];
}

export interface Organization {
  id?: string;
  _id?: string;
  name: string;
  uei?: string | null;
  // Capability focus areas (e.g. "DevSecOps", "AI engineering"). Stored as a list; the admin
  // enters them comma-separated. Given to the agents as a RANKING signal, never a filter.
  keywords?: string[] | null;
  company_details?: CompanyDetails | null;
  created_at?: string;
  updated_at?: string;
}

export interface OrganizationStats {
  member_count?: number;
  pending_invitations?: number;
  [key: string]: unknown;
}

export const organizationsApi = {
  // Current user's organization.
  getMyOrganization: async (): Promise<Organization> => {
    const response = await apiClient.get<Organization>('/api/organizations/me');
    return response.data;
  },

  // Update the organization's name and/or UEI (admin only).
  updateOrganization: async (
    payload: { name?: string; uei?: string; keywords?: string }
  ): Promise<Organization> => {
    const response = await apiClient.patch<Organization>('/api/organizations/me', payload);
    return response.data;
  },

  // Fetch + save the org's SAM.gov entity details from its stored UEI (admin only).
  lookupUei: async (): Promise<CompanyDetails> => {
    const response = await apiClient.post<CompanyDetails>('/api/organizations/me/uei-lookup');
    return response.data;
  },

  // List organization members (admin only).
  getMembers: async (): Promise<TeamMember[]> => {
    const response = await apiClient.get<TeamMember[]>('/api/organizations/me/members');
    return response.data;
  },

  // Member + pending-invitation counts (admin only).
  getStats: async (): Promise<OrganizationStats> => {
    const response = await apiClient.get<OrganizationStats>('/api/organizations/me/stats');
    return response.data;
  },

  // Promote a member to admin (admin only).
  promoteMember: async (userId: string): Promise<void> => {
    await apiClient.post(`/api/organizations/members/${userId}/promote`);
  },

  // Demote a member to user (admin only).
  demoteMember: async (userId: string): Promise<void> => {
    await apiClient.post(`/api/organizations/members/${userId}/demote`);
  },

  // Remove a member from the organization (admin only).
  removeMember: async (userId: string): Promise<void> => {
    await apiClient.delete(`/api/organizations/members/${userId}`);
  },
};
