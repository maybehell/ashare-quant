# ADR 0032: Separate history completion from experiment decision

Status: accepted

Date: 2026-08-13

## Context

A completed historical backtest proves that computation and evidence generation
finished. It does not prove that the preregistered hypothesis passed. The fourth
fixed-family experiment also has a Bonferroni-adjusted nominal alpha that must be
enforced by code before its first evaluation.

## Decision

1. Keep workflow status `completed` separate from experiment decision `passed`
   or `rejected`.
2. Registry-bound history validation evaluates total excess return, information
   ratio, maximum drawdown, held-out excess, walk-forward fold count and positive
   ratio, positive observed excess, and paired block-bootstrap two-sided p-value.
3. Missing evidence rejects the experiment. The p-value must not exceed the
   registered nominal alpha, currently 0.0125 for the four-test family.
4. A registry-bound completed result is reusable only when it carries the exact
   preregistration identity and an explicit decision.
5. Either decision is research evidence only and grants no candidate, account,
   order, activation, or broker authority.

## Consequences

- A favorable headline return cannot bypass held-out, walk-forward, drawdown, or
  multiple-testing checks.
- Legacy unregistered history remains readable and reusable under its former
  semantics.
- The registry remains immutable input; generated decisions are stored in the
  isolated history-validation status artifact.
