'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { invitationsApi, AcceptInvitationRequest } from '@/lib/api/invitations';
import { useAuthStore } from '@/lib/stores/authStore';
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
      <div style={styles.page}>
        <div style={styles.shell}>
          <div style={styles.brand}>
            <div className="word" style={styles.word}>
              Collecct<span style={{ color: 'var(--accent-2)' }}>.</span>
            </div>
          </div>
          <div style={styles.card}>
            <h1 style={styles.title}>Validating invitation…</h1>
            <p style={styles.subtitle}>One moment while we check your link.</p>
          </div>
        </div>
      </div>
    );
  }

  if (validationStatus === 'invalid' || validationStatus === 'expired') {
    return (
      <div style={styles.page}>
        <div style={styles.shell}>
          <div style={styles.brand}>
            <div className="word" style={styles.word}>
              Collecct<span style={{ color: 'var(--accent-2)' }}>.</span>
            </div>
          </div>
          <div style={styles.card}>
            <h1 style={styles.title}>
              {validationStatus === 'expired' ? 'Invitation expired' : 'Invalid invitation'}
            </h1>
            <p style={styles.subtitle}>{error || 'This invitation link is no longer valid.'}</p>
            <p style={{ ...styles.muted, textAlign: 'left', marginTop: 10 }}>
              Please ask your organization administrator for a new invitation.
            </p>
            <button
              className="btn primary"
              style={{ ...styles.submit, marginTop: 18 }}
              onClick={() => router.push('/auth/login')}
            >
              Go to sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  const isExistingUser = !!invitationData?.user_exists;

  return (
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--accent-2)' }}>.</span>
          </div>
        </div>

        <div style={styles.card}>
          <h1 style={styles.title}>You&apos;re invited</h1>
          <p style={styles.subtitle}>
            {isExistingUser
              ? 'Accept this invitation to join the team.'
              : 'Create your account to join the team.'}
          </p>

          {invitationData && (
            <div style={styles.inviteInfo}>
              <div style={styles.infoRow}>
                <span style={styles.infoKey}>Organization</span>
                <span style={styles.infoVal}>{invitationData.organization_name}</span>
              </div>
              <div style={styles.infoRow}>
                <span style={styles.infoKey}>Invited by</span>
                <span style={styles.infoVal}>{invitationData.invited_by_name}</span>
              </div>
              <div style={styles.infoRow}>
                <span style={styles.infoKey}>Email</span>
                <span style={styles.infoVal}>{invitationData.email}</span>
              </div>
              <div style={styles.infoRow}>
                <span style={styles.infoKey}>Role</span>
                <span style={styles.roleBadge}>{invitationData.role}</span>
              </div>
              {invitationData.expiresAt && (
                <div style={styles.expires}>Expires {formatDate(invitationData.expiresAt)}</div>
              )}
            </div>
          )}

          <form onSubmit={handleSubmit} style={styles.form}>
            {error && <div style={styles.errorBox}>{error}</div>}

            {!isExistingUser && (
              <>
                <div style={styles.row2}>
                  <div style={styles.group}>
                    <label style={styles.label} htmlFor="firstName">
                      First name
                    </label>
                    <input
                      id="firstName"
                      type="text"
                      style={styles.input}
                      placeholder="John"
                      value={firstName}
                      onChange={(e) => setFirstName(e.target.value)}
                      required
                      autoComplete="given-name"
                    />
                  </div>
                  <div style={styles.group}>
                    <label style={styles.label} htmlFor="lastName">
                      Last name
                    </label>
                    <input
                      id="lastName"
                      type="text"
                      style={styles.input}
                      placeholder="Doe"
                      value={lastName}
                      onChange={(e) => setLastName(e.target.value)}
                      required
                      autoComplete="family-name"
                    />
                  </div>
                </div>

                <div style={styles.group}>
                  <label style={styles.label} htmlFor="password">
                    Password
                  </label>
                  <input
                    id="password"
                    type="password"
                    style={styles.input}
                    placeholder="At least 8 characters"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                  />
                </div>

                <div style={styles.group}>
                  <label style={styles.label} htmlFor="confirmPassword">
                    Confirm password
                  </label>
                  <input
                    id="confirmPassword"
                    type="password"
                    style={styles.input}
                    placeholder="Re-enter your password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                  />
                </div>

                <label style={styles.checkRow}>
                  <input
                    type="checkbox"
                    checked={termsAccepted}
                    onChange={(e) => setTermsAccepted(e.target.checked)}
                    style={styles.checkbox}
                  />
                  <span style={styles.checkLabel}>
                    I agree to the Terms and Conditions and Privacy Policy.
                  </span>
                </label>
              </>
            )}

            {isExistingUser && (
              <div style={styles.infoBox}>
                You already have an account with this email. Accept to join{' '}
                <strong>{invitationData?.organization_name}</strong>.
              </div>
            )}

            <button
              type="submit"
              className="btn primary"
              style={styles.submit}
              disabled={isSubmitting}
            >
              {isSubmitting && <span className="spin" />}
              {isSubmitting
                ? 'Joining…'
                : isExistingUser
                  ? 'Accept invitation'
                  : 'Accept & create account'}
            </button>
          </form>

          <div style={styles.footer}>
            Already have an account?{' '}
            <Link href="/auth/login" style={styles.linkStrong}>
              Sign in
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <AcceptInvitationContent />
    </Suspense>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: '100vh',
    height: 'auto',
    overflowY: 'auto',
    background: 'var(--paper)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  shell: { width: '100%', maxWidth: 440 },
  brand: { textAlign: 'center', marginBottom: 26 },
  word: {
    fontFamily: 'var(--font-display)',
    fontSize: 32,
    fontWeight: 500,
    letterSpacing: '-0.02em',
    lineHeight: 1,
    color: 'var(--ink)',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--line)',
    borderRadius: 16,
    padding: '30px 30px 26px',
    boxShadow: '0 12px 34px rgba(24, 21, 17, 0.06)',
  },
  title: {
    fontFamily: 'var(--font-display)',
    fontSize: 24,
    fontWeight: 500,
    letterSpacing: '-0.015em',
    color: 'var(--ink)',
  },
  subtitle: { marginTop: 6, fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.5 },
  inviteInfo: {
    marginTop: 20,
    background: 'var(--surface-2)',
    border: '1px solid var(--line)',
    borderRadius: 12,
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 9,
  },
  infoRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  infoKey: {
    fontSize: 10.5,
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    color: 'var(--faint)',
  },
  infoVal: { fontSize: 13, color: 'var(--ink)', fontWeight: 500, textAlign: 'right' },
  roleBadge: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    padding: '3px 9px',
    borderRadius: 999,
    background: 'var(--accent-soft)',
    color: 'var(--accent)',
  },
  expires: {
    marginTop: 4,
    paddingTop: 9,
    borderTop: '1px solid var(--line)',
    fontSize: 11.5,
    color: 'var(--faint)',
  },
  form: { marginTop: 20, display: 'flex', flexDirection: 'column', gap: 16 },
  row2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 },
  group: { display: 'flex', flexDirection: 'column', gap: 6 },
  label: {
    fontSize: 11,
    letterSpacing: '0.07em',
    textTransform: 'uppercase',
    color: 'var(--faint)',
  },
  input: {
    width: '100%',
    padding: '10px 12px',
    background: 'var(--surface-2)',
    border: '1px solid var(--line-strong)',
    borderRadius: 9,
    fontSize: 13.5,
    color: 'var(--ink)',
    fontFamily: 'var(--font-sans)',
    outline: 'none',
  },
  checkRow: { display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' },
  checkbox: {
    marginTop: 2,
    width: 15,
    height: 15,
    accentColor: 'var(--accent)',
    cursor: 'pointer',
  },
  checkLabel: { fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.45 },
  errorBox: {
    background: 'var(--watch-soft)',
    border: '1px solid var(--line-strong)',
    borderRadius: 9,
    padding: '10px 14px',
    fontSize: 13,
    color: '#b4453a',
  },
  infoBox: {
    background: 'var(--accent-soft)',
    border: '1px solid var(--line)',
    borderRadius: 9,
    padding: '10px 14px',
    fontSize: 12.5,
    color: 'var(--accent)',
    lineHeight: 1.5,
  },
  submit: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 4,
  },
  muted: { fontSize: 11.5, color: 'var(--faint)', lineHeight: 1.5 },
  footer: { marginTop: 20, textAlign: 'center', fontSize: 13, color: 'var(--muted)' },
  linkStrong: { color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 },
};
