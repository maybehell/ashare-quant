# ADR 0010: Require complete point-in-time index-member price history

Status: accepted

Date: 2026-08-11

## Context

The point-in-time CSI 300 strategy cached valid monthly constituent snapshots,
but its membership-only fetch selected only the latest snapshot's 300 symbols.
The research interval contained 395 distinct members. Consequently, old
constituents that later left the index were absent from the committed price
scope, and early snapshots had materially lower price coverage even though the
membership dates themselves were correct.

The V2 forward candidate cache is immutable through its freeze boundary.
Backfilling old rows into that cache would invalidate its frozen input identity
and could silently mix repaired historical evidence with existing forward
observations.

## Decision

1. A membership-only fetch requests the union of every constituent appearing
   in snapshots inside the configured research interval. Latest-snapshot-only
   selection remains the behavior for ordinary non-membership-only fetches.
2. Point-in-time membership quality requires canonical index identity, allowed
   source provenance, exact snapshot cardinality, no missing or future update
   dates, benchmark-calendar alignment, bounded gaps, and snapshot freshness.
3. Price coverage must be 100% across distinct historical members and 100% in
   every individual snapshot. Stock-catalog coverage remains a separate 98%
   metadata threshold and cannot compensate for missing prices.
4. The composite result is written to dedicated CSV artifacts, included in the
   Chinese report, and enforced by practical and strict readiness. Missing or
   malformed evidence fails closed.
5. A cold membership-only fetch prepares dependencies in the order benchmark,
   point-in-time membership, then member prices. Quality auditing may run
   before stocks and prices exist, but produces a blocked artifact instead of
   crashing.
6. Frozen candidate caches are not repaired in place. Historical reconstruction
   uses a separate cache, output directory, state database, strategy name, and
   research-only status. Its results cannot inherit forward observations,
   promote a candidate, activate a strategy, or authorize broker submission.

## Consequences

Positive:

- Exited index members can no longer disappear silently from point-in-time
  backtests.
- Reports distinguish valid constituent dates from complete price evidence.
- New point-in-time environments can be initialized with one fetch workflow.
- Frozen forward evidence remains immutable and separately auditable.

Costs:

- The first complete fetch requests every distinct historical member and takes
  longer than fetching only the current index.
- Delisted symbols may require a wider request interval before a provider
  returns their pre-delisting rows.
- Passing this gate does not prove historical ST flags, complete industry
  metadata, or point-in-time fundamentals.

## Alternatives considered

### Allow 98% historical price coverage

Rejected because even a few omitted exited members reintroduce the exact
survivorship path the gate is intended to prevent.

### Backfill the frozen V2 candidate cache

Rejected because changing pre-freeze market data would invalidate candidate
identity and contaminate existing forward evidence.

### Accept a single unverified fallback source for missing delisted symbols

Rejected for this reconstruction. BaoStock returned the two initially missing
symbols when the request covered their pre-delisting history, so weakening
source trust was unnecessary.
