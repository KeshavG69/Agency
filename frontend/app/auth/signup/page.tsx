'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useAuthStore } from '@/lib/stores/authStore';

export default function SignupPage() {
  const { signup, isLoading, error, clearError } = useAuthStore();

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setValidationError('');

    if (!termsAccepted) {
      setValidationError('You must accept the Terms and Conditions.');
      return;
    }
    if (password !== confirmPassword) {
      setValidationError('Passwords do not match.');
      return;
    }
    if (password.length < 8) {
      setValidationError('Password must be at least 8 characters.');
      return;
    }

    try {
      await signup({ firstName, lastName, email, password, terms_accepted: termsAccepted });
      setSubmitted(true);
    } catch (err) {
      console.error('Signup failed:', err);
    }
  };

  if (submitted) {
    return (
      <div style={styles.page}>
        <div style={styles.shell}>
          <div style={styles.brand}>
            <div className="word" style={styles.word}>
              Collecct<span style={{ color: 'var(--bid-ink)' }}>.</span>
            </div>
          </div>
          <div style={styles.card}>
            <h1 style={styles.title}>Check your email</h1>
            <p style={styles.subtitle}>
              We&apos;ve sent a verification link to <strong>{email}</strong>. Click it to verify
              your account and finish signing in.
            </p>
            <div style={{ ...styles.infoBox, marginTop: 20 }}>
              Didn&apos;t get it? Check your spam folder — the link is valid for a limited time.
            </div>
            <div style={styles.footer}>
              <Link href="/auth/login" style={styles.linkStrong}>
                Back to sign in
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--bid-ink)' }}>.</span>
          </div>
          <div style={styles.brandSub}>Government BD, captured.</div>
        </div>

        <div style={styles.card}>
          <h1 style={styles.title}>Create your account</h1>
          <p style={styles.subtitle}>Get started with Collecct.</p>

          <form onSubmit={handleSubmit} style={styles.form}>
            {(error || validationError) && (
              <div style={styles.errorBox}>{error || validationError}</div>
            )}

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
              <label style={styles.label} htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                style={styles.input}
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
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

            <button
              type="submit"
              className="btn primary"
              style={styles.submit}
              disabled={isLoading}
            >
              {isLoading && <span className="spin" />}
              {isLoading ? 'Creating account…' : 'Create account'}
            </button>
          </form>

          <p style={styles.muted}>
            Collecct may be invite-only — if sign-up is closed, ask an admin for an invitation.
          </p>

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
  brandSub: { marginTop: 8, fontSize: 12, letterSpacing: '0.04em', color: 'var(--muted)' },
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
  subtitle: { marginTop: 6, fontSize: 13.5, color: 'var(--muted)' },
  form: { marginTop: 22, display: 'flex', flexDirection: 'column', gap: 16 },
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
  checkbox: { marginTop: 2, width: 15, height: 15, accentColor: 'var(--accent)', cursor: 'pointer' },
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
    color: 'var(--bid-ink)',
  },
  submit: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 4,
  },
  muted: { marginTop: 14, fontSize: 11.5, color: 'var(--faint)', textAlign: 'center', lineHeight: 1.5 },
  footer: { marginTop: 18, textAlign: 'center', fontSize: 13, color: 'var(--muted)' },
  linkStrong: { color: 'var(--bid-ink)', textDecoration: 'none', fontWeight: 600 },
};
