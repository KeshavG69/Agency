'use client';

/*
  THESIS: the entry to a capture console should look like the console, not like a login.
    Refuses the centered floating card every product ships.
  OWN-WORLD: the incumbent system, committed — one tonal step, 1px hairlines, uppercase
    micro-labels, Geist sans with mono for machine-derived values, one institutional green
    reserved for action and identity. No new palette, no new face.
  STORY: a capture rep arrives knowing what this product does before they type anything,
    then signs in with the fewest possible decisions.
  FIRST VIEWPORT: an asymmetric split — a ruled institutional panel at left carrying the
    wordmark, the positioning line and three factual capability statements; the form at
    right sitting directly on paper, primary action full-width under the fields.
  FORM: asymmetric identity rail + open form field; candidate 3 of the grounded list;
    surface seed key 45fca5b5.
*/

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuthStore } from '@/lib/stores/authStore';
import { authApi } from '@/lib/api/auth';
import AuthShell from '../AuthShell';

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
    <AuthShell>
      <h1 className="auth-h1">Sign in</h1>
      <p className="auth-sub">Use your work account to reach your organisation.</p>

      <form onSubmit={handleSubmit}>
        {/* role=alert so a failed attempt is announced, not just repainted. */}
        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}

        <div className="auth-fields">
          <div className="auth-group">
            <label className="auth-label" htmlFor="email">
              Work email
            </label>
            <input
              id="email"
              type="email"
              className="auth-input"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              autoFocus
            />
          </div>

          <div className="auth-group">
            <label className="auth-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="auth-input"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
            <div className="auth-aside-row">
              <Link href="/auth/forgot-password" className="auth-link">
                Forgot password?
              </Link>
            </div>
          </div>

          <button
            type="submit"
            className="btn primary auth-submit"
            disabled={isLoading}
          >
            {isLoading && <span className="spin" />}
            {isLoading ? 'Signing in…' : 'Sign in'}
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
        onClick={handleMicrosoftSignIn}
        disabled={msBusy}
      >
        {msBusy && <span className="spin" />}
        {msBusy ? 'Redirecting…' : 'Sign in with Microsoft'}
      </button>

      <div className="auth-foot">
        Don&apos;t have an account? <Link href="/auth/signup">Sign up</Link>
      </div>
    </AuthShell>
  );
}
