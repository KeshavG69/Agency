'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { authApi } from '@/lib/api/auth';
import { useAuthStore } from '@/lib/stores/authStore';
import AuthShell from '../AuthShell';

type Status = 'working' | 'success' | 'error';

// Lands here after Microsoft redirects back — handles BOTH a plain login/signup and an
// invitation acceptance uniformly (the backend already resolved which one via `state`; the
// response shape is the same either way, so this page never needs to know or care which).
function MicrosoftCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuthStore();
  const ran = useRef(false);

  const [status, setStatus] = useState<Status>('working');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    const errorParam = searchParams.get('error');
    if (errorParam) {
      setStatus('error');
      setErrorMessage(
        searchParams.get('error_description') || 'Microsoft sign-in was cancelled or failed.'
      );
      return;
    }

    const code = searchParams.get('code');
    const state = searchParams.get('state');
    if (!code || !state) {
      setStatus('error');
      setErrorMessage('Missing sign-in information from Microsoft.');
      return;
    }

    (async () => {
      try {
        const { access_token, refresh_token, user } = await authApi.microsoftCallback(code, state);
        setStatus('success');
        await login({ access_token, refresh_token, user });
        setTimeout(() => router.push('/'), 900);
      } catch (err: any) {
        setStatus('error');
        setErrorMessage(err.response?.data?.detail || 'Microsoft sign-in failed.');
      }
    })();
  }, [searchParams, login, router]);

  return (
    <AuthShell facts={false}>
      {status === 'working' && (
        <>
          <span className="auth-working">
            <i />
            Signing in
          </span>
          <h1 className="auth-h1" style={{ marginTop: 14 }}>
            Confirming your Microsoft account
          </h1>
          <p className="auth-status-sub">This takes a moment. Don&apos;t close the tab.</p>
        </>
      )}

      {status === 'success' && (
        <>
          <h1 className="auth-h1">You&apos;re in</h1>
          <p className="auth-status-sub">Taking you to Collecct…</p>
        </>
      )}

      {status === 'error' && (
        <>
          <h1 className="auth-h1">Sign-in failed</h1>
          {/* The message IS the error, so it carries the alarm treatment rather than
              sitting as quiet body text under a heading nobody can act on. */}
          <div className="auth-error" role="alert" style={{ marginTop: 14, marginBottom: 0 }}>
            {errorMessage}
          </div>
          <div className="auth-actions">
            <Link href="/auth/login" className="btn primary auth-submit">
              Back to sign in
            </Link>
          </div>
        </>
      )}
    </AuthShell>
  );
}

export default function MicrosoftCallbackPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <MicrosoftCallbackContent />
    </Suspense>
  );
}
