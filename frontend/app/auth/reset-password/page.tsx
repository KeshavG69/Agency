'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { authApi } from '@/lib/api/auth';

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) setError('Invalid or missing reset token.');
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (!token) {
      setError('Invalid or missing reset token.');
      return;
    }

    setIsLoading(true);
    try {
      await authApi.resetPassword(token, newPassword);
      setSuccess(true);
      setTimeout(() => router.push('/auth/login'), 2000);
    } catch (err: any) {
      setError(
        err.response?.data?.detail || 'Failed to reset password. The link may have expired.'
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--bid-ink)' }}>.</span>
          </div>
        </div>

        <div style={styles.card}>
          {success ? (
            <>
              <h1 style={styles.title}>Password reset</h1>
              <p style={styles.subtitle}>
                Your password has been updated. Redirecting you to sign in…
              </p>
              <button
                className="btn primary"
                style={{ ...styles.submit, marginTop: 18 }}
                onClick={() => router.push('/auth/login')}
              >
                Go to sign in
              </button>
            </>
          ) : !token ? (
            <>
              <h1 style={styles.title}>Invalid link</h1>
              <p style={styles.subtitle}>This password reset link is invalid or has expired.</p>
              <button
                className="btn primary"
                style={{ ...styles.submit, marginTop: 18 }}
                onClick={() => router.push('/auth/forgot-password')}
              >
                Request a new link
              </button>
            </>
          ) : (
            <>
              <h1 style={styles.title}>Reset your password</h1>
              <p style={styles.subtitle}>
                Enter your new password below — at least 8 characters.
              </p>

              <form onSubmit={handleSubmit} style={styles.form}>
                {error && <div style={styles.errorBox}>{error}</div>}

                <div style={styles.group}>
                  <label style={styles.label} htmlFor="newPassword">
                    New password
                  </label>
                  <input
                    id="newPassword"
                    type="password"
                    style={styles.input}
                    placeholder="Enter new password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                    autoFocus
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
                    placeholder="Confirm new password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                  />
                </div>

                <button
                  type="submit"
                  className="btn primary"
                  style={styles.submit}
                  disabled={isLoading}
                >
                  {isLoading && <span className="spin" />}
                  {isLoading ? 'Resetting…' : 'Reset password'}
                </button>
              </form>
            </>
          )}

          <div style={styles.footer}>
            Remember your password?{' '}
            <Link href="/auth/login" style={styles.linkStrong}>
              Sign in
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <ResetPasswordForm />
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
  shell: { width: '100%', maxWidth: 412 },
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
  form: { marginTop: 22, display: 'flex', flexDirection: 'column', gap: 16 },
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
  errorBox: {
    background: 'var(--watch-soft)',
    border: '1px solid var(--line-strong)',
    borderRadius: 9,
    padding: '10px 14px',
    fontSize: 13,
    color: '#b4453a',
  },
  submit: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 4,
  },
  footer: { marginTop: 22, textAlign: 'center', fontSize: 13, color: 'var(--muted)' },
  linkStrong: { color: 'var(--bid-ink)', textDecoration: 'none', fontWeight: 600 },
};
