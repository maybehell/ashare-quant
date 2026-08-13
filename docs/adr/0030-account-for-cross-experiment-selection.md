# ADR 0030: Account for cross-experiment selection

Status: accepted

Date: 2026-08-13

## Context

Candidate-family diagnostics measure selection inside one research run, but the
repository has also evaluated materially different strategies on different days.
Keeping only a successful survivor would understate the effective number of
attempts and overstate confidence. Three small-capital benchmark-relative
historical hypotheses have now failed.

## Decision

1. Maintain a versioned experiment registry containing failed and successful
   trials, immutable strategy identity, rationale, registration time, fixed
   thresholds, and a compact factual result summary.
2. Fix the first family at four experiments and family-wise alpha 0.05. Each
   experiment receives nominal alpha 0.0125 under a Bonferroni correction.
3. New history validations may require exact registry binding. Validation fails
   before backtesting when experiment ID, order, config path, strategy hash,
   thresholds, or alpha budget does not match.
4. The preregistration identity includes all immutable registrations through the
   current experiment, but excludes mutable outcome labels. Later result
   recording cannot rewrite what was fixed before evaluation.
5. Registry membership is research evidence only. It cannot promote a strategy,
   start forward observation, initialize an account, create execution orders, or
   authorize broker submission.

## Consequences

- A fourth hypothesis must be committed with its exact strategy and thresholds
  before its first evaluation. No fifth test belongs to this fixed family.
- Bonferroni correction is conservative and does not cure survivorship bias,
  weak point-in-time data, dependence between strategies, or economic overfit.
- Existing historical artifacts remain readable; strict binding is opt-in so a
  governance metadata addition does not silently invalidate completed evidence.
