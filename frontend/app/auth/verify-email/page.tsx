'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useAuthStore } from '@/lib/stores/authStore';
import { authApi } from '@/lib/api/auth';

type Status = 'verifying' | 'success' | 'error';

function VerifyEmailContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuthStore();
  const ran = useRef(false);

  const [status, setStatus] = useState<Status>('verifying');
  const [errorMessage, setErrorMessage] = useState('');

  // Resend form state
  const [resendEmail, setResendEmail] = useState('');
  const [resendBusy, setResendBusy] = useState(false);
  const [resendDone, setResendDone] = useState(false);

  useEffect(() => {
    // Guard against StrictMode double-run (verification tokens are single-use).
    if (ran.current) return;
    ran.current = true;

    const token = searchParams.get('token');
    if (!token) {
      setStatus('error');
      setErrorMessage('No verification token provided.');
      return;
    }

    (async () => {
      try {
        const { access_token, refresh_token, user } = await authApi.verifyEmail(token);
        setStatus('success');
        await login({ access_token, refresh_token, user });
        setTimeout(() => router.push('/'), 1500);
      } catch (err: any) {
        setStatus('error');
        setErrorMessage(err.response?.data?.detail || 'Verification failed.');
      }
    })();
  }, [searchParams, login, router]);

  const handleResend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resendEmail) return;
    setResendBusy(true);
    try {
      await authApi.resendVerification(resendEmail);
      setResendDone(true);
    } catch (err) {
      console.error('Resend error:', err);
    } finally {
      setResendBusy(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--accent-2)' }}>.</span>
          </div>
        </div>

        <div style={styles.card}>
          {status === 'verifying' && (
            <div style={{ textAlign: 'center' }}>
              <h1 style={styles.title}>Verifying your email…</h1>
              <p style={styles.subtitle}>Hold on while we confirm your address.</p>
            </div>
          )}

          {status === 'success' && (
            <div style={{ textAlign: 'center' }}>
              <h1 style={styles.title}>Email verified</h1>
              <p style={styles.subtitle}>
                You&apos;re all set — signing you in and taking you to Collecct…
              </p>
            </div>
          )}

          {status === 'error' && (
            <div>
              <h1 style={styles.title}>Verification failed</h1>
              <p style={styles.subtitle}>{errorMessage}</p>

              {resendDone ? (
                <div style={{ ...styles.infoBox, marginTop: 20 }}>
                  A new verification link is on its way to <strong>{resendEmail}</strong>.
                </div>
              ) : (
                <form onSubmit={handleResend} style={styles.form}>
                  <div style={styles.group}>
                    <label style={styles.label} htmlFor="resendEmail">
                      Resend verification
                    </label>
                    <input
                      id="resendEmail"
                      type="email"
                      style={styles.input}
                      placeholder="you@company.com"
                      value={resendEmail}
                      onChange={(e) => setResendEmail(e.target.value)}
                      required
                      autoComplete="email"
                    />
                  </div>
                  <button
                    type="submit"
                    className="btn primary"
                    style={styles.submit}
                    disabled={resendBusy}
                  >
                    {resendBusy && <span className="spin" />}
                    {resendBusy ? 'Sending…' : 'Send new link'}
                  </button>
                </form>
              )}

              <div style={styles.footer}>
                <Link href="/auth/login" style={styles.linkStrong}>
                  Back to sign in
                </Link>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <div className="loading-full">Loading…</div>
      }
    >
      <VerifyEmailContent />
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
  infoBox: {
    background: 'var(--accent-soft)',
    border: '1px solid var(--line)',
    borderRadius: 9,
    padding: '10px 14px',
    fontSize: 12.5,
    color: 'var(--accent)',
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
  linkStrong: { color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 },
};
