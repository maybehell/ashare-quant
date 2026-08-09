# ADR 0002: Manifest-bound derived research evidence

Status: accepted

Date: 2026-08-09

## Context

The market cache intentionally retains historical symbols across incremental
refreshes. The latest completed fetch, however, defines the exact symbol scope
that may be described as current. Backtests already honored that scope, while
readiness, cost stress, and capital-sensitivity reuse could hash retained rows
or trust runs produced from an older market-data identity. This created false
staleness reports and could also allow internally consistent but outdated
derived evidence.

## Decision

All execution-relevant derived research evidence must bind to the exact market
data identity in the current main `run_manifest.csv`:

1. Backtest, readiness, and cost-stress inputs use the same latest completed
   fetch symbol scope.
2. Capital-sensitivity variants may be reused only when both their backtest
   configuration and market-data fingerprint match the main run manifest.
3. Practical readiness independently checks the capital-sensitivity reference
   fingerprint and fails closed when the binding is missing or different.
4. Long research workflows refresh prerequisite evidence in dependency order
   and publish stage progress, but never gain order-generation authority.

## Consequences

Positive:

- Retained historical symbols no longer create conflicting hashes between
  research components.
- A stale capital experiment cannot satisfy the current practical gate merely
  because all of its own variants used the same old data.
- Repeated one-click runs can reuse exact matching capital evidence while still
  explaining long first-run work through progress stages.

Costs:

- A completed market refresh invalidates capital-sensitivity evidence and may
  require several isolated backtests.
- Historical capital audits without the new reference fingerprint remain
  readable but cannot pass the current practical gate.

## Alternatives considered

### Treat any internally consistent capital experiment as current

Rejected because consistency among variants does not prove consistency with the
strategy evidence that will generate orders.

### Hash the entire retained cache everywhere

Rejected because intentionally retained symbols outside the latest completed
fetch would dilute the audited investable scope and recreate false mismatches.

### Ignore market-data changes for capital sensitivity

Rejected because board lots, prices, eligible holdings, and cash drag can all
change with the market snapshot.
