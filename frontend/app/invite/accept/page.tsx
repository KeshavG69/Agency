'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import AuthShell from '@/app/auth/AuthShell';
import { invitationsApi, AcceptInvitationRequest } from '@/lib/api/invitations';
import { useAuthStore } from '@/lib/stores/authStore';
import { authApi } from '@/lib/api/auth';
import { ValidateTokenResponse } from '@/lib/types';

type ValidationStatus = 'loading' | 'valid' | 'invalid' | 'expired';

function AcceptInvitationContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const ran = useRef(false);

  const [validationStatus, setValidationStatus] = useState<ValidationStatus>('loading');
  const [invitationData, setInvitationData] = useState<ValidateTokenResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [msBusy, setMsBusy] = useState(false);
  const [msError, setMsError] = useState<string | null>(null);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    if (!token) {
      setValidationStatus('invalid');
      setError('No invitation token provided.');
      return;
    }

    (async () => {
      try {
        const data = await invitationsApi.validateToken(token);
        setInvitationData(data);
        setValidationStatus('valid');
        setError(null);
      } catch (err: any) {
        const detail = err.response?.data?.detail || 'Invalid or expired invitation.';
        setValidationStatus(detail.toLowerCase().includes('expired') ? 'expired' : 'invalid');
        setError(detail);
      }
    })();
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!token) {
      setError('Invalid invitation token.');
      return;
    }

    const isExistingUser = invitationData?.user_exists;

    if (!isExistingUser) {
      if (!firstName.trim() || !lastName.trim()) {
        setError('Please enter your first and last name.');
        return;
      }
      if (password.length < 8) {
        setError('Password must be at least 8 characters.');
        return;
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match.');
        return;
      }
      if (!termsAccepted) {
        setError('You must accept the Terms and Conditions to create an account.');
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const data: AcceptInvitationRequest = isExistingUser
        ? { token }
        : {
            token,
            firstName: firstName.trim(),
            lastName: lastName.trim(),
            password,
            terms_accepted: termsAccepted,
          };

      const response = await invitationsApi.acceptInvitation(data);

      localStorage.setItem('access_token', response.access_token);
      localStorage.setItem('refresh_token', response.refresh_token);

      await useAuthStore.getState().fetchUser();
      router.push('/');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to accept invitation.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Alternative to the form above: prove identity via Microsoft instead of typing a name +
  // password (new user) or just clicking Accept (existing user). The invite token rides
  // through Microsoft's `state` param, so whoever comes back is routed to
  // /auth/microsoft-callback, not back here — that page finishes the login the same way for
  // either flow.
  const handleMicrosoftAccept = async () => {
    if (!token) return;
    setMsError(null);
    setMsBusy(true);
    try {
      const { auth_url } = await authApi.getMicrosoftLoginUrl(token);
      window.location.href = auth_url;
    } catch (err: any) {
      setMsError(err.response?.data?.detail || "Couldn't start Microsoft sign-in.");
      setMsBusy(false);
    }
  };

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        month: 'long',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return dateStr;
    }
  };

  if (validationStatus === 'loading') {
    return (
      <AuthShell facts={false}>
        <span className="auth-working">
          <i />
          Checking
        </span>
        <h1 className="auth-h1" style={{ marginTop: 14 }}>
          Validating your invitation
        </h1>
        <p className="auth-status-sub">One moment while we check the link.</p>
      </AuthShell>
    );
  }

  if (validationStatus === 'invalid' || validationStatus === 'expired') {
    return (
      <AuthShell facts={false}>
        <h1 className="auth-h1">
          {validationStatus === 'expired' ? 'This invitation has expired' : 'Invitation not valid'}
        </h1>
        <p className="auth-status-sub">{error || 'This invitation link is no longer valid.'}</p>
        <div className="auth-note" style={{ marginTop: 20 }}>
          Ask your organisation&apos;s administrator to send a new one — invitations are
          single-use and time-limited.
        </div>
        <div className="auth-actions">
          <button className="btn primary auth-submit" onClick={() => router.push('/auth/login')}>
            Go to sign in
          </button>
        </div>
      </AuthShell>
    );
  }

  const isExistingUser = !!invitationData?.user_exists;

  return (
    <AuthShell facts={false}>
      <h1 className="auth-h1">You&apos;re invited</h1>
      <p className="auth-sub">
        {isExistingUser
          ? 'Accept this invitation to join the team.'
          : 'Create your account to join the team.'}
      </p>

      {/* The invitation's own facts, ruled like a record — who, where, and as what. */}
      {invitationData && (
        <dl className="auth-kv">
          <div>
            <dt>Organisation</dt>
            <dd>{invitationData.organization_name}</dd>
          </div>
          <div>
            <dt>Invited by</dt>
            <dd>{invitationData.invited_by_name}</dd>
          </div>
          <div>
            <dt>Email</dt>
            <dd>{invitationData.email}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>
              <span className="pill">{invitationData.role}</span>
            </dd>
          </div>
          {invitationData.expiresAt && (
            <div>
              <dt>Expires</dt>
              <dd>{formatDate(invitationData.expiresAt)}</dd>
            </div>
          )}
        </dl>
      )}

      <form onSubmit={handleSubmit}>
        {error && (
          <div className="auth-error" role="alert" style={{ marginTop: 20, marginBottom: 0 }}>
            {error}
          </div>
        )}

        <div className="auth-fields">
          {!isExistingUser && (
            <>
              <div className="auth-row2">
                <div className="auth-group">
                  <label className="auth-label" htmlFor="firstName">
                    First name
                  </label>
                  <input
                    id="firstName"
                    type="text"
                    className="auth-input"
                    value={firstName}
                    onChange={(e) => setFirstName(e.target.value)}
                    required
                    autoComplete="given-name"
                  />
                </div>
                <div className="auth-group">
                  <label className="auth-label" htmlFor="lastName">
                    Last name
                  </label>
                  <input
                    id="lastName"
                    type="text"
                    className="auth-input"
                    value={lastName}
                    onChange={(e) => setLastName(e.target.value)}
                    required
                    autoComplete="family-name"
                  />
                </div>
              </div>

              <div className="auth-group">
                <label className="auth-label" htmlFor="password">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  className="auth-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                />
              </div>

              <div className="auth-group">
                <label className="auth-label" htmlFor="confirmPassword">
                  Confirm password
                </label>
                <input
                  id="confirmPassword"
                  type="password"
                  className="auth-input"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                />
              </div>
                <ul className="auth-reqs">
                  <li className={password.length >= 8 ? 'met' : undefined}>
                    <span className="tick" aria-hidden="true">
                      {password.length >= 8 ? '✓' : '–'}
                    </span>
                    At least 8 characters
                  </li>
                  <li
                    className={
                      confirmPassword.length > 0 && password === confirmPassword
                        ? 'met'
                        : undefined
                    }
                  >
                    <span className="tick" aria-hidden="true">
                      {confirmPassword.length > 0 && password === confirmPassword
                        ? '✓'
                        : '–'}
                    </span>
                    Both entries match
                  </li>
                </ul>

              <label className="auth-check">
                <input
                  type="checkbox"
                  checked={termsAccepted}
                  onChange={(e) => setTermsAccepted(e.target.checked)}
                />
                <span>I agree to the Terms and Conditions and Privacy Policy.</span>
              </label>
            </>
          )}

          {isExistingUser && (
            <div className="auth-note">
              You already have an account with this email. Accept to join{' '}
              <strong>{invitationData?.organization_name}</strong>.
            </div>
          )}

          <button type="submit" className="btn primary auth-submit" disabled={isSubmitting}>
            {isSubmitting && <span className="spin" />}
            {isSubmitting
              ? 'Joining…'
              : isExistingUser
                ? 'Accept invitation'
                : 'Accept & create account'}
          </button>
        </div>
      </form>

      <div className="auth-or">
        <i />
        <span>or</span>
        <i />
      </div>

      {msError && (
        <div className="auth-error" role="alert">
          {msError}
        </div>
      )}
      <button
        type="button"
        className="btn ghost auth-alt"
        onClick={handleMicrosoftAccept}
        disabled={msBusy}
      >
        {msBusy && <span className="spin" />}
        {msBusy ? 'Redirecting…' : 'Continue with Microsoft'}
      </button>

      <div className="auth-foot">
        Already have an account? <Link href="/auth/login">Sign in</Link>
      </div>
    </AuthShell>
  );
}

export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <AcceptInvitationContent />
    </Suspense>
  );
}
