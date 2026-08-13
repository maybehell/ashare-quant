# ADR 0031: Add a skipped medium-reversal research factor

Status: accepted

Date: 2026-08-13

## Context

The first three registered small-capital historical hypotheses failed. A fourth
test must be materially different and fixed before evaluation. Existing code has
a 20-day short-reversal factor but cannot express the published China A-share
signal based on roughly three months of returns while skipping the latest week.

## Decision

Add an opt-in `medium_reversal` factor defined as the negative return from 60
trading days ago to 5 trading days ago. Keep it neutral unless both its lookback
keys are explicitly present. This change adds research capability only; it does
not register the fourth experiment or run historical evaluation.

## Consequences

- Formula behavior is covered with synthetic paths whose latest five sessions
  move opposite to the older return.
- A later commit must add validation, exact strategy identity, fixed thresholds,
  input binding, registry entry, and documentation before the first backtest.
- The factor grants no candidate, forward, account, order, or broker authority.
