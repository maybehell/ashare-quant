# ADR 0024: Preregister a small-capital defensive trend candidate

Status: accepted

Date: 2026-08-12

## Context

The concentrated ten-stock CNY 100,000 candidate failed its fixed extended
history diagnostic. A broad CSI 300 low-risk candidate also reduced drawdown but
failed benchmark-relative evidence. Existing forward candidates cannot be
silently repaired because their frozen strategy identities no longer match
current execution semantics.

Published research reports an investable low-volatility effect among large and
liquid China A shares, while China momentum evidence is less stable and recent
trend-factor research emphasizes price together with liquidity. These findings
support a defensive core with restrained trend and liquidity signals, but do
not prove that this exact portfolio will outperform.

## Decision

1. Fix strategy SHA-256 1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9 and preregister one new forward-only CSI 300 candidate before its first
   evaluation. It inherits no historical or forward authority from prior
   candidates.
2. Use twenty equal target positions for a CNY 100,000 account, with a 5% stock
   cap, 25% industry cap, point-in-time CSI 300 membership, monthly rebalancing,
   and board-lot-aware capital planning.
3. Weight low volatility, downside volatility, and drawdown at 55% in total;
   trend and momentum at 35%; and liquidity at 10%. Do not sweep these weights,
   holding count, lookbacks, or timing on the inherited cache.
4. Copy the complete predecessor cache into a new namespace, preregister the
   candidate, and seal immutable inputs only after this decision and fixed
   strategy hash have been committed and pushed.
5. Treat all post-freeze results as forward evidence. The candidate remains
   research-only and cannot activate execution, create orders, initialize a live
   ledger, or authorize broker submission.

## Consequences

- A weak early-forward result quarantines the exact fingerprint.
- A favorable result still requires the full forward horizon, implementation
  evidence, and separate activation governance.
- Existing invalid and rejected manifests remain immutable audit history.
- Rank-buffered turnover is deferred because the current execution model
  deliberately rejects combining it with capital-aware board-lot planning.

## Alternatives considered

- Retune the failed ten-stock portfolio: rejected because the same historical
  cache has already informed its rejection.
- Reuse the pure low-risk candidate: rejected because all fixed walk-forward
  folds and held-out excess return were negative.
- Use pure momentum: rejected because China A-share momentum evidence is regime
  dependent and less stable than the low-volatility evidence.
