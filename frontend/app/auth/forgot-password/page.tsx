'use client';

import { useState } from 'react';
import Link from 'next/link';
import { authApi } from '@/lib/api/auth';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await authApi.forgotPassword(email);
      setSuccess(true);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to send reset link. Please try again.');
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
              <h1 style={styles.title}>Check your email</h1>
              <p style={styles.subtitle}>
                If an account exists for <strong>{email}</strong>, we&apos;ve sent a password
                reset link.
              </p>
              <div style={{ ...styles.infoBox, marginTop: 20 }}>
                The link expires shortly. If you don&apos;t see the email, check your spam folder.
              </div>
              <button
                className="btn ghost"
                style={{ ...styles.submit, marginTop: 18 }}
                onClick={() => setSuccess(false)}
              >
                Send another link
              </button>
            </>
          ) : (
            <>
              <h1 style={styles.title}>Forgot password?</h1>
              <p style={styles.subtitle}>
                Enter your email and we&apos;ll send you a link to reset your password.
              </p>

              <form onSubmit={handleSubmit} style={styles.form}>
                {error && <div style={styles.errorBox}>{error}</div>}

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
                    autoFocus
                  />
                </div>

                <button
                  type="submit"
                  className="btn primary"
                  style={styles.submit}
                  disabled={isLoading}
                >
                  {isLoading && <span className="spin" />}
                  {isLoading ? 'Sending…' : 'Send reset link'}
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
  footer: { marginTop: 22, textAlign: 'center', fontSize: 13, color: 'var(--muted)' },
  linkStrong: { color: 'var(--bid-ink)', textDecoration: 'none', fontWeight: 600 },
};
