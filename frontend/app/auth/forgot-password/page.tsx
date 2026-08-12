'use client';

import { useState } from 'react';
import Link from 'next/link';
import AuthShell from '../AuthShell';
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
    <AuthShell>
      {success ? (
        <>
          <h1 className="auth-h1">Check your email</h1>
          <p className="auth-sub">
            If an account exists for <strong>{email}</strong>, a reset link is on its way.
          </p>
          <div className="auth-note" style={{ marginTop: 20 }}>
            The link expires shortly. If it isn&apos;t there, check your spam folder.
          </div>
          <div className="auth-actions">
            <button className="btn ghost auth-alt" onClick={() => setSuccess(false)}>
              Send another link
            </button>
          </div>
        </>
      ) : (
        <>
          <h1 className="auth-h1">Reset your password</h1>
          <p className="auth-sub">
            Enter your work email and we&apos;ll send you a link to set a new one.
          </p>

          <form onSubmit={handleSubmit}>
            {error && (
              <div className="auth-error" role="alert" style={{ marginTop: 20, marginBottom: 0 }}>
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

              <button type="submit" className="btn primary auth-submit" disabled={isLoading}>
                {isLoading && <span className="spin" />}
                {isLoading ? 'Sending…' : 'Send reset link'}
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

