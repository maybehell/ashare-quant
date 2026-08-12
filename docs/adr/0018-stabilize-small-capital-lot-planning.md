# ADR 0018: Stabilize and identify small-capital lot planning

Status: accepted

Date: 2026-08-12

## Context

The first small-capital strategy showed an implausible improvement when simulated
commission and slippage increased. The capital-aware constructor used the active
scenario commission to decide whether another board lot was affordable, so a cost
stress changed both expenses and target holdings. Small changes in account equity
could also cross board-lot affordability boundaries and create divergent paths.

While testing a fixed planning cost and conservative equity step, a second issue
was found: quantities selected from rounded planning equity were encoded as
weights against planning equity, then decoded by the order layer against actual
equity. That changed the intended quantity. Existing capital-sensitivity cache
identity also could not distinguish planning algorithm revisions.

## Decision

1. Keep legacy behavior when no planning options are configured.
2. Allow capital-aware strategies to use fixed planning commission, minimum
   commission, transfer fee, and a downward planning-equity step. Actual fills
   and account cash continue to use scenario execution costs and actual equity.
3. Encode selected lot values against actual investable equity so the order layer
   reconstructs the intended board-lot quantity.
4. Require supported planning implementations to declare
   `planning_schema_version=1`; this field is part of backtest and capital-
   sensitivity configuration identity and invalidates incompatible cached runs.
5. Block small-capital forward preregistration whenever
   `optimized_on_current_cache=true`, even if numerical admission checks pass.
6. Keep the isolated `v2` configuration research-only. Its historical result is
   an evaluated experiment, not a candidate or execution strategy.

## Consequences

- Cost stress no longer directly changes target construction through its
  commission rate when fixed planning costs are enabled.
- A planning-equity step reduces boundary sensitivity but cannot make different
  capital levels economically equivalent; capital fidelity remains an explicit
  gate.
- Algorithm changes require a new supported schema identity rather than silent
  reuse of old sensitivity evidence.
- The evaluated `v2` reduced cost-path deviation but failed intended-capital and
  walk-forward gates, so it remains ineligible for forward preregistration.
