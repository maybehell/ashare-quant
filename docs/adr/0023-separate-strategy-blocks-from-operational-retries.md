# ADR 0023: Separate strategy blocks from operational retries

Status: accepted

Date: 2026-08-12

## Context

The unattended daily observation workflow combines a market observation attempt
with independent read-only duties: broker snapshot refresh, report rendering,
and alert delivery. A frozen candidate identity mismatch is deterministic and
cannot become valid through repeated network refreshes. Treating it like a
transient data-source failure caused redundant same-day retries and obscured the
fact that the scheduler itself remained healthy.

## Decision

1. The runner classifies frozen-candidate preflight failure as exit code 20 and
   publishes `strategy_blocked`, including when PowerShell surfaces native
   stderr through its exception path.
2. A strategy block does not mark the observation complete or fresh and cannot
   authorize candidate repair, activation, order creation, ledger
   initialization, or broker submission.
3. The runner still performs configured broker read-only refresh, report
   rendering, and durable alert delivery before returning the strategy-blocked
   exit code.
4. The daemon treats exit code 20 as terminal for that trading day. It remains
   healthy and monitored but does not repeat the same deterministic strategy
   attempt until a later trading day.
5. Operator status exposes `strategy_blocked` separately from scheduler liveness
   and from transient operational failure.

## Consequences

- Transient failures continue to use bounded retries.
- Strategy evidence remains fail closed and visibly stale while independent
  read-only operational evidence can continue to refresh.
- A valid new preregistration or an immutable replacement candidate remains a
  governed research action; the scheduler never mutates stale registrations.
## Alternatives considered

- Retry every deterministic identity mismatch: rejected because unchanged
  preregistration evidence cannot recover through market or broker refresh.
- Abort before read-only follow-up work: rejected because broker freshness,
  reporting, and alert delivery are independent operational responsibilities.
- Treat the run as completed when follow-up work succeeds: rejected because
  strategy observation evidence would still be absent and must remain stale.