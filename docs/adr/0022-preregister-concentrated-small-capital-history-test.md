# ADR 0022: Preregister a concentrated small-capital history test

Status: accepted

Date: 2026-08-12

## Context

The CNY 100,000 default strategy targets roughly 100 stocks. Its latest capital
sensitivity run retained about 80% cash at CNY 100,000, so aggregate historical
excess return does not describe a practical small-account implementation. The
research gate also correctly prevents adding more candidates to the same recent
cache after unstable candidate-family evidence.

The existing extended-history cache contains 1,503 symbols and history from 2018
through 2026. It extends the time regimes available for diagnosis, but it is
based on a current-stock universe and therefore retains survivorship bias. This
experiment reads that cache without refreshing or rewriting it and owns separate
output and SQLite paths.

## Decision

1. Preregister one materially different CNY 100,000 strategy before its first
   evaluation on the extended-history cache.
2. Hold ten equal-weight positions, cap each stock at 10% and each industry at
   30%, rebalance quarterly, and use the versioned board-lot planner.
3. Use fixed momentum, low-risk, trend-quality, and liquidity weights. Do not
   run a parameter sweep or alter the configuration after seeing its result.
4. Commit and push the configuration and fixed strategy hash before running the
   one historical diagnostic, preserving an externally visible ordering record.
5. Treat the result only as a rejection test. It cannot satisfy forward
   observation, remove survivorship-bias warnings, activate a strategy, create
   orders, initialize a live ledger, or authorize broker submission.

## Consequences

- A poor result rejects this exact fingerprint without follow-up tuning on the
  same cache.
- A favorable result is still insufficient for practical or live use. The
  strategy must separately pass complete point-in-time historical checks,
  capital sensitivity, cost stress, and a new post-freeze forward window.
- The system-wide CNY 200,000 ceiling and all live fail-closed controls remain
  unchanged.
