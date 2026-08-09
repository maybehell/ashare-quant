# ADR 0003: Budgeted historical backfill staging

Status: accepted

Date: 2026-08-10

## Context

The main market cache covered only about 750 effective trading days. Extending
the configured backtest start did not extend existing symbol histories because
incremental acquisition requested only a latest-date overlap. A full 800-symbol
backward refresh through a free public source is slow and can be interrupted.
Treating any interrupted result as the formal cache would weaken coverage,
source provenance, and reproducibility. Sharing the same partial filename with
daily refresh would also allow unrelated workflows to overwrite resumable work.

## Decision

Historical backward fill follows a staged commit protocol:

1. The expected start is computed per symbol as the later of the requested
   research start and the verified listing date.
2. A cached symbol is fetched backward when its earliest row exceeds that
   expected start by the configured tolerance.
3. Bounded runs process a configured number of unfinished symbols and persist
   namespaced partial prices, source provenance, current failures, and progress.
4. A partial symbol is resumable only when it covers both requested boundaries
   and passes normalized price-frame validation.
5. Incomplete bounded runs return an explicit `in_progress` artifact and never
   replace the formal `prices` cache.
6. The formal cache is committed only after the entire requested scope finishes
   and the existing fetch-success threshold passes.
7. The local research daemon is single-instance, time-bounded, yields to the
   daily observation window, and exits when progress becomes `complete`.

The extended-history job remains research only. Completion does not promote a
strategy, authorize orders, initialize a live ledger, or enable broker submit.

## Consequences

Positive:

- Interrupted public-source downloads resume without discarding validated work.
- Daily refresh and historical research cannot overwrite each other's staging
  files.
- Listing dates prevent repeated false gaps for newly listed stocks.
- The current formal cache remains stable until a complete replacement is ready.
- Source identity survives normal partial resume instead of being reduced to an
  untraceable generic partial source.

Costs:

- Backfill completion may require many bounded runs and temporary disk space.
- Partial rows created before provenance staging existed retain an explicit
  unknown-source fallback until they are fetched again or independently
  verified.
- The final formal commit still requires an operational window without another
  market-cache writer; the daemon therefore yields around daily observation.

## Alternatives considered

### Fetch all symbols in one foreground command

Rejected because public-source latency and interruption would make the workflow
operator-dependent and repeatedly waste completed work.

### Merge each successful symbol directly into the formal cache

Rejected because the formal cache would represent a changing, incomplete
historical scope and could feed misleading strategy evidence.

### Use a separate complete cache for long-history research

Deferred. It offers stronger physical isolation but duplicates current market
metadata and requires an explicit, audited promotion/copy protocol. Namespaced
staging plus complete-only commit preserves one formal cache with less
operational complexity.

### Ignore listing dates and request the same start for every symbol

Rejected because unavailable pre-listing periods would create false failures and
unnecessary source load.
