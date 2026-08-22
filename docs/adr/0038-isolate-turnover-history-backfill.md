# ADR 0038: Isolate and identity-bind turnover history backfill

Status: accepted

Date: 2026-08-22

## Context

ADR 0037 permits a future low-turnover hypothesis only after a complete,
provenance-bound point-in-time turnover cache exists. The committed 800-symbol
price cache predates turnover preservation and is frozen evidence for completed
research. Rewriting it would invalidate historical identities. A full refresh
is also a long, failure-prone network operation that must resume without mixing
rows from changed source scope or configuration.

The generic market fetch owns the formal market cache and includes metadata,
benchmark, membership, and price replacement behavior that this narrow research
acquisition does not need. Partial turnover rows have no strategy authority and
must not appear complete merely because one batch succeeded.

## Decision

1. Use the exact `research_turnover_backfill` configuration role. It requires
   research-only validation, inactive execution application, disabled broker
   submission, active emergency stop, and distinct source and target caches.
2. Derive the symbol/date scope only from a completed committed source-price
   cache. Bind every successful symbol to the source-price hash, source-scope
   hash, and effective backfill-configuration hash.
3. Store partial turnover rows, per-symbol provenance, failures, and progress in
   the isolated target namespace. Write provenance before corresponding partial
   rows so interruption can discard recoverable work but cannot authorize
   unproven data.
4. Reject resume when source prices, source scope, or configuration identity has
   changed. Preserve the old namespace for audit and require a new namespace.
5. Publish `turnover_history` only after every source-scope symbol passes the
   provider-reported quality, minimum-row, and date-coverage checks with no
   remaining failure. Coverage audit remains `blocked` until that publication
   and exact identity match.
6. Acquisition and audit do not calculate strategy returns, register an
   experiment, create a candidate, start observation, initialize an account,
   create orders, or call a broker.

## Consequences

- A bounded command can make restart-safe progress without rewriting formal
  prices or treating a partial batch as usable research evidence.
- Source or policy drift is visible and fails closed instead of silently mixing
  histories.
- Completing the cache permits only a separate preregistration review. It does
  not itself authorize historical evaluation or forward operation.
- The isolated cache consumes additional disk space and needs explicit cleanup
  only after its audit trail is no longer required.

## Alternatives considered

- Refresh the generic formal market cache in place: rejected because it mutates
  frozen research evidence and broadens the acquisition blast radius.
- Copy the full cache and overwrite its prices with new history: rejected
  because turnover ownership and completion would remain ambiguous.
- Allow strategy research on completed symbols while other symbols are pending:
  rejected because changing cross-sectional scope creates selection bias and
  makes repeated partial results incomparable.
