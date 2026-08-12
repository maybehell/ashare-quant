# ADR 0020: Execute rebalance sells before buys and close positions exactly

Status: accepted

Date: 2026-08-12

## Context

Rebalance sell quantities were derived with an unguarded floating-point
division. A full exit from 200 shares could therefore become 199 shares and
leave a synthetic one-share position. Generated orders were also sorted only by
symbol, so a buy could execute before a sell that was intended to fund it. That
created avoidable insufficient-cash rejects and diverged from an executable
small-account rebalance batch.

## Decision

1. A zero target exits the complete currently sellable quantity. T+1 locked
   quantity remains unavailable.
2. A nonzero target converts target value to target shares with a bounded
   floating-point tolerance before deriving a sell quantity.
3. Rebalance generation returns all sells before all buys, with deterministic
   symbol order inside each side.
4. Buy board-lot rounding, fees, price limits, suspension checks, volume limits,
   and execution-time affordability remain unchanged.
5. Existing research evidence is stale after this execution-semantic revision
   and must be rerun before it is cited.
6. `trading.rebalance_execution_schema_version=2` is part of the backtest
   configuration hash. The small-capital admission gate also requires current
   order, rejection, and final-position evidence, rejects residual odd lots,
   and caps cash-constrained buy rejects.

## Consequences

- Full exits no longer manufacture one-share residual positions.
- Successful sells can fund later buys in the same simulated rebalance batch.
- A blocked sell can still cause a later buy to be partially filled or rejected;
  the execution layer continues to use actual cash and fails closed.
- Backtests and paper orders may differ from prior runs, so old manifests cannot
  authorize the revised behavior.
