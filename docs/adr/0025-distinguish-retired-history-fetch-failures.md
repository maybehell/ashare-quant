# ADR 0025: Distinguish retired-member history fetch failures

Status: accepted

Date: 2026-08-12

## Context

Point-in-time index research retains prices for securities that later leave the
index or the current stock catalog. An incremental request through the current
market date can legitimately return no rows for such a security even though its
historical series and source provenance are complete through its tradable life.
Treating every such empty increment as a current-scope failure prevents frozen
candidates from producing forward signals. Ignoring `failed_items` wholesale
would hide genuine current-market gaps.

## Decision

The committed fetch scope may account for a failure as a retired historical
member only when all of the following are proven by committed artifacts:

1. The failure is unique and occurred in the history stage.
2. The symbol has retained prices and bound history-source provenance.
3. The symbol appears in historical point-in-time membership.
4. The symbol is absent from both the current stock catalog and the latest index
   membership snapshot.
5. The failure table row count exactly matches `fetch_summary.failed_items`.

All failures not satisfying every condition remain unexplained and block signal
generation. Accepted retired history is retained in the scoped prices so old
point-in-time research remains reproducible; it is not added to the current
tradable stock catalog.

## Consequences

- Delisted or otherwise retired historical members no longer block a current
  signal merely because an increment after their trading life is empty.
- Current members, current catalog securities, unbound data, duplicate failure
  rows, non-history failures, and missing evidence still fail closed.
- The fetch summary continues to report the original failure count. Scope audit
  text separately reports accepted retired and unexplained counts.
