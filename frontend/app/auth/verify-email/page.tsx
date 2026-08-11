'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import AuthShell from '../AuthShell';
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
    <AuthShell facts={false}>
      {status === 'verifying' && (
        <>
          <span className="auth-working">
            <i />
            Verifying
          </span>
          <h1 className="auth-h1" style={{ marginTop: 14 }}>
            Confirming your address
          </h1>
          <p className="auth-status-sub">This only takes a moment.</p>
        </>
      )}

      {status === 'success' && (
        <>
          <h1 className="auth-h1">Email verified</h1>
          <p className="auth-status-sub">
            You&apos;re all set — signing you in and taking you to Collecct…
          </p>
        </>
      )}

      {status === 'error' && (
        <>
          <h1 className="auth-h1">Verification failed</h1>
          <div className="auth-error" role="alert" style={{ marginTop: 14, marginBottom: 0 }}>
            {errorMessage}
          </div>

          {resendDone ? (
            <div className="auth-note" style={{ marginTop: 20 }}>
              A new verification link is on its way to <strong>{resendEmail}</strong>.
            </div>
          ) : (
            <form onSubmit={handleResend}>
              <div className="auth-fields" style={{ marginTop: 22 }}>
                <div className="auth-group">
                  <label className="auth-label" htmlFor="resendEmail">
                    Send a new link to
                  </label>
                  <input
                    id="resendEmail"
                    type="email"
                    className="auth-input"
                    placeholder="you@company.com"
                    value={resendEmail}
                    onChange={(e) => setResendEmail(e.target.value)}
                    required
                    autoComplete="email"
                  />
                </div>
                <button type="submit" className="btn primary auth-submit" disabled={resendBusy}>
                  {resendBusy && <span className="spin" />}
                  {resendBusy ? 'Sending…' : 'Send new link'}
                </button>
              </div>
            </form>
          )}

          <div className="auth-foot">
            <Link href="/auth/login">Back to sign in</Link>
          </div>
        </>
      )}
    </AuthShell>
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
