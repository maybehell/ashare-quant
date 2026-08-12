# ADR 0019: Preserve upstream portfolio intent in board-lot planning

Status: accepted

Date: 2026-08-12

## Context

The portfolio builder can blend a benchmark reference anchor, factor satellite,
and industry constraints into one target-weight vector. The small-capital
constructor historically accepted only ranked factor scores. It therefore
discarded the upstream target vector before selecting and sizing board lots.

That behavior was acceptable for the original rank-only small-capital strategy,
but it made a reference-anchored configuration semantically misleading: research
reported an anchored fractional portfolio while the executable simulation rebuilt
a concentrated rank portfolio. Changing legacy behavior in place would also make
existing v1/v2 evidence non-reproducible.

## Decision

1. Add optional upstream target_weights input to board-lot planning.
2. Keep rank-only behavior unchanged unless
   strategy.capital_aware.respect_target_weights=true.
3. Require planning_schema_version=2 for target-aware planning. Schema v1
   remains supported and retains its historical behavior.
4. In target-aware mode, admit only symbols with positive upstream target weight,
   prioritize larger upstream targets subject to affordability and risk caps, and
   add one board lot at a time only when it reduces absolute target-value error.
5. Pass the fractional target through backtest, walk-forward, paper, and broker
   preview workflows. Missing target input fails closed in target-aware mode.
6. Evaluate the first target-aware 100,000 yuan configuration once on the current
   cache and label it research-only and optimized on current cache. It cannot be
   forward-preregistered or activated from that evidence.

## Consequences

- Reference anchors and other upstream weighting policies now survive board-lot
  discretization when schema v2 is explicitly enabled.
- Legacy evidence remains reproducible because schema v1 ignores the new optional
  input.
- Ten-stock portfolios still cannot replicate all 300 index members; target-aware
  compression reduces semantic drift but does not guarantee benchmark
  outperformance or low tracking error.
- Any future planning change requires a new schema identity or an explicit
  backwards-compatible extension.
