'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuthStore } from '@/lib/stores/authStore';
import { authApi } from '@/lib/api/auth';

export default function LoginPage() {
  const router = useRouter();
  const { login, isLoading, error, clearError } = useAuthStore();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [msBusy, setMsBusy] = useState(false);
  const [msError, setMsError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    try {
      await login({ email, password });
      router.push('/');
    } catch (err) {
      // Error surfaced via the store's `error`.
      console.error('Login failed:', err);
    }
  };

  const handleMicrosoftSignIn = async () => {
    setMsError(null);
    setMsBusy(true);
    try {
      const { auth_url } = await authApi.getMicrosoftLoginUrl();
      window.location.href = auth_url;
    } catch (err: any) {
      setMsError(err.response?.data?.detail || "Couldn't start Microsoft sign-in.");
      setMsBusy(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.shell}>
        <div style={styles.brand}>
          <div className="word" style={styles.word}>
            Collecct<span style={{ color: 'var(--accent-2)' }}>.</span>
          </div>
          <div style={styles.brandSub}>Government BD, captured.</div>
        </div>

        <div style={styles.card}>
          <h1 style={styles.title}>Welcome back</h1>
          <p style={styles.subtitle}>Sign in to your account to continue.</p>

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

            <div style={styles.group}>
              <label style={styles.label} htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                style={styles.input}
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
              <div style={{ marginTop: 8, textAlign: 'right' }}>
                <Link href="/auth/forgot-password" style={styles.link}>
                  Forgot password?
                </Link>
              </div>
            </div>

            <button
              type="submit"
              className="btn primary"
              style={styles.submit}
              disabled={isLoading}
            >
              {isLoading && <span className="spin" />}
              {isLoading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <div style={styles.divider}>
            <span style={styles.dividerLine} />
            <span style={styles.dividerText}>or</span>
            <span style={styles.dividerLine} />
          </div>

          {msError && <div style={styles.errorBox}>{msError}</div>}
          <button
            type="button"
            className="btn ghost"
            style={styles.submit}
            onClick={handleMicrosoftSignIn}
            disabled={msBusy}
          >
            {msBusy && <span className="spin" />}
            {msBusy ? 'Redirecting…' : 'Sign in with Microsoft'}
          </button>

          <div style={styles.footer}>
            Don&apos;t have an account?{' '}
            <Link href="/auth/signup" style={styles.linkStrong}>
              Sign up
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
  brandSub: {
    marginTop: 8,
    fontSize: 12,
    letterSpacing: '0.04em',
    color: 'var(--muted)',
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
  subtitle: { marginTop: 6, fontSize: 13.5, color: 'var(--muted)' },
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
  divider: {
    marginTop: 20,
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  dividerLine: { flex: 1, height: 1, background: 'var(--line)' },
  dividerText: { fontSize: 11.5, color: 'var(--faint)', letterSpacing: '0.03em' },
  submit: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 4,
  },
  footer: {
    marginTop: 22,
    textAlign: 'center',
    fontSize: 13,
    color: 'var(--muted)',
  },
  link: { color: 'var(--accent)', textDecoration: 'none', fontSize: 12.5 },
  linkStrong: {
    color: 'var(--accent)',
    textDecoration: 'none',
    fontWeight: 600,
  },
};
