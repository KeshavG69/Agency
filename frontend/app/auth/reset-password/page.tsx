'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import AuthShell from '../AuthShell';
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
    <AuthShell facts={false}>
      {success ? (
        <>
          <h1 className="auth-h1">Password reset</h1>
          <p className="auth-status-sub">
            Your password has been updated. Redirecting you to sign in…
          </p>
          <div className="auth-actions">
            <button className="btn primary auth-submit" onClick={() => router.push('/auth/login')}>
              Go to sign in
            </button>
          </div>
        </>
      ) : !token ? (
        <>
          <h1 className="auth-h1">This link has expired</h1>
          {/* Names the cause and the way out, rather than "Invalid link" with no recourse. */}
          <p className="auth-status-sub">
            Password reset links are single-use and short-lived. Request a fresh one and it
            will arrive in a moment.
          </p>
          <div className="auth-actions">
            <button
              className="btn primary auth-submit"
              onClick={() => router.push('/auth/forgot-password')}
            >
              Request a new link
            </button>
          </div>
        </>
      ) : (
        <>
          <h1 className="auth-h1">Choose a new password</h1>
          <p className="auth-sub">You&apos;ll use this to sign in from now on.</p>

          <form onSubmit={handleSubmit}>
            {error && (
              <div className="auth-error" role="alert" style={{ marginTop: 20, marginBottom: 0 }}>
                {error}
              </div>
            )}

            <div className="auth-fields">
              <div className="auth-group">
                <label className="auth-label" htmlFor="newPassword">
                  New password
                </label>
                <input
                  id="newPassword"
                  type="password"
                  className="auth-input"
                  placeholder="Enter new password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                  autoFocus
                />
              </div>

              <div className="auth-group">
                <label className="auth-label" htmlFor="confirmPassword">
                  Confirm password
                </label>
                <input
                  id="confirmPassword"
                  type="password"
                  className="auth-input"
                  placeholder="Confirm new password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                />
              </div>
                {/* The rules were previously stated once in prose and never checked, so the
                    only way to learn you had failed one was to submit. They tick live. */}
                <ul className="auth-reqs">
                  <li className={newPassword.length >= 8 ? 'met' : undefined}>
                    <span className="tick" aria-hidden="true">
                      {newPassword.length >= 8 ? '✓' : '–'}
                    </span>
                    At least 8 characters
                  </li>
                  <li
                    className={
                      confirmPassword.length > 0 && newPassword === confirmPassword
                        ? 'met'
                        : undefined
                    }
                  >
                    <span className="tick" aria-hidden="true">
                      {confirmPassword.length > 0 && newPassword === confirmPassword ? '✓' : '–'}
                    </span>
                    Both entries match
                  </li>
                </ul>

              <button type="submit" className="btn primary auth-submit" disabled={isLoading}>
                {isLoading && <span className="spin" />}
                {isLoading ? 'Resetting…' : 'Reset password'}
              </button>
            </div>
          </form>
        </>
      )}

      <div className="auth-foot">
        Remember your password? <Link href="/auth/login">Sign in</Link>
      </div>
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div className="loading-full">Loading…</div>}>
      <ResetPasswordForm />
    </Suspense>
  );
}
