# ADR 0009: Quantify survivorship exposure without claiming point-in-time correction

Status: accepted

Date: 2026-08-11

## Context

The trusted extended-history validation binds a fixed strategy to an
authoritatively completed 800-symbol fetch. That proves the selected rows and
strategy run are reproducible, but the 800 symbols were chosen using current
membership and liquidity. The market cache also contains additional symbols
with shorter history. Neither dataset contains authoritative daily investable
membership, complete delisted-security history, or historical risk-warning
state.

Without an explicit artifact, a long effective date range can be mistaken for
a point-in-time full-market test. Silently treating the extra cached symbols as
a historical universe would be equally misleading because their cache coverage
starts at different dates and is not membership evidence.

## Decision

1. Extended-history validation writes `survivorship_bias_audit.csv` and
   `survivorship_bias_snapshots.csv` before evaluating reusable completed
   evidence.
2. The committed current pool comes from the completed per-symbol history
   provenance. The audit compares that pool with every symbol that has cached
   prices inside the configured research range and records annual last-trading-
   day coverage snapshots.
3. Listing dates are used only for a descriptive listed/not-yet-listed count.
   Missing dates and the known `2000-01-01` placeholder are reported as
   unreliable; they cannot establish historical eligibility.
4. Cached non-pool symbols are evidence of sample restriction, not proof that
   they are delisted securities or valid point-in-time constituents.
5. Until authoritative daily membership, delisted securities, and historical
   risk-warning states exist, the audit records
   `point_in_time_universe_available=False`,
   `survivorship_bias_corrected=False`, and `survivorship_risk_level=high`.
6. Completed extended-history status binds the joint SHA-256 of the audit and
   annual snapshots. Missing or changed audit evidence prevents completed-state
   reuse.
7. The Chinese report surfaces the failed checks as high-priority warnings.
   These artifacts explain research limitations and cannot promote a candidate,
   activate a strategy, create execution orders, or authorize broker submission.

## Consequences

Positive:

- Operators can distinguish long price history from a point-in-time universe.
- The amount of current-pool restriction is reproducible by year and bound to
  the completed validation state.
- Missing delisted and historical eligibility data remains visible instead of
  being converted into a false pass.

Costs:

- The audit does not remove survivorship bias and therefore cannot upgrade the
  historical result to promotion-grade evidence.
- Cached coverage ratios depend on the current cache and must be regenerated
  when that cache changes.
- A true correction requires a separately governed point-in-time membership
  source and a new validation design.

## Alternatives considered

### Treat every cached symbol as historically investable

Rejected because price presence is not evidence of index membership, listing
status, risk-warning status, or actual availability to the strategy on that day.

### Suppress the long-history result until point-in-time data exists

Rejected because the run remains useful as fixed-pool stress evidence when its
scope and limitations are prominent and machine-readable.

### Estimate missing historical membership from current metadata

Rejected because this would create precise-looking but unauthoritative evidence
and could hide delisted-security survivorship bias.
