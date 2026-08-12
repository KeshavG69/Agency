'use client';

import { useState } from 'react';
import Link from 'next/link';
import AuthShell from '../AuthShell';
import { useAuthStore } from '@/lib/stores/authStore';

export default function SignupPage() {
  const { signup, isLoading, error, clearError } = useAuthStore();

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [validationError, setValidationError] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    setValidationError('');

    if (!termsAccepted) {
      setValidationError('You must accept the Terms and Conditions.');
      return;
    }
    if (password !== confirmPassword) {
      setValidationError('Passwords do not match.');
      return;
    }
    if (password.length < 8) {
      setValidationError('Password must be at least 8 characters.');
      return;
    }

    try {
      await signup({ firstName, lastName, email, password, terms_accepted: termsAccepted });
      setSubmitted(true);
    } catch (err) {
      console.error('Signup failed:', err);
    }
  };

  if (submitted) {
    return (
      <AuthShell facts={false}>
        <h1 className="auth-h1">Check your email</h1>
        <p className="auth-sub">
          A verification link is on its way to <strong>{email}</strong>. Open it to finish
          setting up your account.
        </p>
        <div className="auth-note" style={{ marginTop: 20 }}>
          Not there? Check your spam folder — the link is valid for a limited time.
        </div>
        <div className="auth-foot">
          <Link href="/auth/login">Back to sign in</Link>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <h1 className="auth-h1">Create your account</h1>
      <p className="auth-sub">
        Collecct may be invite-only. If sign-up is closed, ask an admin for an invitation.
      </p>

      <form onSubmit={handleSubmit}>
        {(error || validationError) && (
          <div className="auth-error" role="alert" style={{ marginTop: 20, marginBottom: 0 }}>
            {error || validationError}
          </div>
        )}

        <div className="auth-fields">
          <div className="auth-row2">
            <div className="auth-group">
              <label className="auth-label" htmlFor="firstName">
                First name
              </label>
              <input
                id="firstName"
                type="text"
                className="auth-input"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                required
                autoComplete="given-name"
                autoFocus
              />
            </div>
            <div className="auth-group">
              <label className="auth-label" htmlFor="lastName">
                Last name
              </label>
              <input
                id="lastName"
                type="text"
                className="auth-input"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                required
                autoComplete="family-name"
              />
            </div>
          </div>

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
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
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
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
            {/* Ticking live, so a rule can be satisfied before submitting rather than
                learned from a rejection. */}
            <ul className="auth-reqs">
              <li className={password.length >= 8 ? 'met' : undefined}>
                <span className="tick" aria-hidden="true">
                  {password.length >= 8 ? '\u2713' : '\u2013'}
                </span>
                At least 8 characters
              </li>
              <li
                className={
                  confirmPassword.length > 0 && password === confirmPassword ? 'met' : undefined
                }
              >
                <span className="tick" aria-hidden="true">
                  {confirmPassword.length > 0 && password === confirmPassword ? '\u2713' : '\u2013'}
                </span>
                Both entries match
              </li>
            </ul>
          </div>

          <label className="auth-check">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => setTermsAccepted(e.target.checked)}
            />
            <span>I agree to the Terms and Conditions and Privacy Policy.</span>
          </label>

          <button type="submit" className="btn primary auth-submit" disabled={isLoading}>
            {isLoading && <span className="spin" />}
            {isLoading ? 'Creating account…' : 'Create account'}
          </button>
        </div>
      </form>

      <div className="auth-foot">
        Already have an account? <Link href="/auth/login">Sign in</Link>
      </div>
    </AuthShell>
  );
}

