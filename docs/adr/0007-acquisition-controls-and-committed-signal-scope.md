# ADR 0007: Separate acquisition controls from strategy and bind signal scope

Status: accepted

Date: 2026-08-10

## Context

The raw Parquet cache intentionally retains historical rows for symbols from
older fetches. The latest successful fetch committed 800 symbols, while the raw
cache still contained 1,503 symbols. Backtest and readiness already used the
latest completed fetch manifest, but paper, shadow, and broker-preview paths
calculated daily coverage from the raw cache. The historical maximum symbol
count therefore raised the completeness threshold and falsely classified the
current 800-symbol date as stale.

Two new long-history acquisition settings also changed the backtest
configuration hash even though they control staging and resume behavior, not
strategy semantics. This made otherwise current evidence appear to belong to a
different strategy.

## Decision

1. The latest completed `fetch_summary` and `fetch_history_provenance` define
   the authoritative symbol scope for backtest, readiness, stress, capital
   feasibility, paper/shadow signals, and broker previews.
2. Paper and shadow provenance records whether committed-scope binding passed
   and includes its manifest summary.
3. Account valuation can still price held symbols outside the committed
   strategy universe. Only its latest complete market date is resolved from the
   committed scope.
4. When committed-scope evidence is unavailable, research can conservatively
   inspect the raw cache and records a failed scope binding. Existing readiness
   and execution gates remain responsible for failing closed; the fallback does
   not grant execution authority.
5. `history_backfill_tolerance_days` and `resume_partial_history` are
   acquisition controls and are excluded from the backtest strategy hash.
   Actual data changes remain represented by market-file and run-manifest data
   hashes.
6. Frozen candidate identities are not rewritten. Restoring the prior strategy
   hash preserves existing preregistration while current data evidence is
   refreshed independently.

## Consequences

Positive:

- Retained old symbols cannot make a current committed fetch appear stale.
- Research, shadow, paper, and preview calculations use one explicit universe.
- Operational history scheduling changes no longer invalidate strategy
  identity, while resulting data changes remain auditable.
- Account holdings outside the strategy universe remain markable.

Costs:

- A malformed or missing fetch manifest remains visible as an unbound research
  fallback and must be interpreted with readiness evidence.
- Historical artifacts created before this rule may contain raw-cache scope and
  should not be mixed with newly bound evidence.
- Future acquisition settings require an explicit identity classification.

## Alternatives considered

### Delete symbols not present in the latest fetch

Rejected because it would discard useful historical data and make recovery and
extended-history research harder.

### Lower the daily coverage threshold

Rejected because it would hide the scope mismatch and weaken incomplete-day
detection.

### Include every data setting in strategy identity

Rejected because retry budgets and resume mechanics do not change portfolio
logic; their observable data effects are already bound separately.

### Rewrite frozen candidate manifests with a new hash

Rejected because the corrected hash returns to the previously frozen strategy
identity and preserves forward-only evidence boundaries.
