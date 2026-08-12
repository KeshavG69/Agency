'use client';

import type { ReactNode } from 'react';

/**
 * The shared frame for every surface outside the console: sign in, sign up, password
 * recovery, email verification, OAuth returns and invitation acceptance.
 *
 * WHY THIS EXISTS. All seven of those pages carried their own copy of the same
 * `page / shell / brand / card` style object. They had already drifted — different radii,
 * different shadows, one hardcoded error red — and every future change meant seven edits
 * with six chances to miss one. The frame lives here now; a page supplies only its content.
 *
 * The composition is the one the sign-in surface established: a ruled institutional panel
 * carrying the identity, and the page's own content on open paper beside it. Nothing is
 * boxed in a floating card, because that arrangement said nothing about the product.
 */

/**
 * What Collecct is, stated as fact.
 *
 * Every line is recorded product truth from PRODUCT.md — the ingestion sources, the parsed
 * solicitation that grounds the agents, and the rule that a verdict always carries its
 * evidence. PRODUCT.md also records that there are no customers, testimonials, benchmarks
 * or usage numbers yet, so none are invented to fill this panel.
 */
const FACTS: ReactNode[] = [
  <>
    Federal opportunities, your Outlook history and your SharePoint library in{' '}
    <b>one pipeline</b>.
  </>,
  <>
    Agents read the <b>full solicitation</b>, not the notice summary, and return a
    bid/no-bid call with a priority score.
  </>,
  <>
    Every verdict arrives with the <b>named risks behind it</b>. Nothing is asserted
    without its evidence.
  </>,
];

export default function AuthShell({
  children,
  /**
   * Status surfaces (verifying an email, returning from OAuth) are a sentence and a button.
   * They keep the frame and the identity, but the three-fact block would be furniture
   * around a message the visitor is waiting on, so they opt out of it.
   */
  facts = true,
}: {
  children: ReactNode;
  facts?: boolean;
}) {
  return (
    <div className="auth-split">
      <aside className="auth-aside">
        <div>
          <div className="auth-word">
            Collecct<span className="dot">.</span>
          </div>
          <p className="auth-tag">
            The capture console for government business development.
          </p>

          {facts && (
            <ul className="auth-facts">
              {FACTS.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="auth-sources">
          <span>SAM.gov</span>
          <span>Outlook</span>
          <span>SharePoint</span>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-form">{children}</div>
      </main>
    </div>
  );
}
