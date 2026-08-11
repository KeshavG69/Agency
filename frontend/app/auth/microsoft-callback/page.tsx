'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { authApi } from '@/lib/api/auth';
import { useAuthStore } from '@/lib/stores/authStore';

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
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--bid-ink)' }}>.</span>
          </div>
        </div>

        <div style={styles.card}>
          {status === 'working' && (
            <div style={{ textAlign: 'center' }}>
              <h1 style={styles.title}>Signing you in…</h1>
              <p style={styles.subtitle}>Confirming your Microsoft account.</p>
            </div>
          )}

          {status === 'success' && (
            <div style={{ textAlign: 'center' }}>
              <h1 style={styles.title}>You&apos;re in</h1>
              <p style={styles.subtitle}>Taking you to Collecct…</p>
            </div>
          )}

          {status === 'error' && (
            <div>
              <h1 style={styles.title}>Sign-in failed</h1>
              <p style={styles.subtitle}>{errorMessage}</p>
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

export default function MicrosoftCallbackPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <MicrosoftCallbackContent />
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
    textAlign: 'center',
  },
  subtitle: { marginTop: 6, fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.5, textAlign: 'center' },
  footer: { marginTop: 22, textAlign: 'center', fontSize: 13, color: 'var(--muted)' },
  linkStrong: { color: 'var(--bid-ink)', textDecoration: 'none', fontWeight: 600 },
};
