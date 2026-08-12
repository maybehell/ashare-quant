# ADR 0017: Gate small-capital forward preregistration

Status: accepted

Date: 2026-08-12

## Context

The CNY 100,000 research family passes board-lot capital fidelity, but its current
historical evidence is not stable enough to start an immutable forward candidate.
Its maximum drawdown exceeds 30%, only half of calendar years beat the benchmark,
the worst fixed-parameter walk-forward fold loses more than 10% relative to the
benchmark, and a higher-cost simulation changes total return by nearly four
percentage points in the favorable direction. The last behavior is a discrete
integer-lot trade-path change, not proof that higher costs improve the strategy.

Changing the configuration status alone must not bypass those findings.

## Decision

1. Add a machine-readable small-capital admission audit owned by the research
   layer and published as `small_capital_live_readiness.csv`.
2. Bind backtest and cost-stress evidence to the current configuration and market
   file hashes, and require current capital-sensitivity evidence at the intended
   account size.
3. Block forward-only preregistration on capital-boundary, execution-isolation,
   drawdown, annual consistency, fixed-parameter walk-forward, high-cost excess,
   or cost-path-stability failure.
4. Apply the admission assertion inside generic candidate preregistration whenever
   `small_capital_live_guard.enabled=true`, so an interface or status edit cannot
   bypass it.
5. Treat a pass only as permission to create a new immutable research candidate
   and start forward shadow observation. It grants no strategy activation, order,
   live-ledger, or broker-submission authority.

## Consequences

- The current small-capital strategy remains `structural_backtest_only` and is
  explicitly blocked from forward preregistration.
- Favorable but large high-cost return changes fail the same stability check as
  unfavorable changes.
- Admission-threshold changes are evaluated immediately against current,
  identity-bound evidence; strategy or execution changes invalidate that evidence.
- A future strategy revision must first pass this audit, then complete its own
  untuned forward window and all independent execution/live gates.
