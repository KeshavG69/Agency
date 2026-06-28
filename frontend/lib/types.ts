// Shared auth/account types for the Collecct frontend.

export interface User {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  organization_id: string | null;
  role: 'admin' | 'user' | null;
  status: string | null;
  created_at?: string | null;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface SignupData {
  firstName: string;
  lastName: string;
  email: string;
  password: string;
  terms_accepted: boolean;
}

export interface TeamMember {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  role: 'admin' | 'user';
  status: string;
  joinedAt?: string;
}

export interface Invitation {
  id?: string;
  _id?: string;
  email: string;
  role: 'admin' | 'user';
  status: string;
  createdAt?: string;
  expiresAt?: string;
}

export interface ValidateTokenResponse {
  email: string;
  organization_name: string;
  role: string;
  invited_by_name: string;
  expiresAt: string;
  createdAt: string;
  user_exists: boolean;
}
