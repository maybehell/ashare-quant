# ADR 0011: Make forward-failure quarantine sticky

Status: accepted

Date: 2026-08-11

## Context

Candidate failure history already prevented the same strategy fingerprint from
being frozen again after an early forward degradation or terminal forward
failure. However, current candidate validation and activation did not consume
that history. A legacy candidate could therefore show an irreversible failure in
`live_candidate_forward_progress.csv` while a later recovery in benchmark NAV
made `live_candidate_validation.csv` report only a warning or, eventually, a
valid candidate.

The inconsistency was amplified because legacy candidate progress preferred a
shadow-portfolio observation whenever one existed, while legacy validation used
the benchmark comparison curve. The same candidate consequently reported 23 and
37 observed days in artifacts written by the same audit workflow.

## Decision

1. Early forward degradation, failed forward excess, and failed forward risk are
   terminal for the matching strategy or behavior fingerprint. A later recovery
   cannot clear the recorded failure.
2. Candidate validation publishes `candidate_quarantine_clear`. A matching
   terminal record fails that check and sets `forward_validation_status` to
   `quarantined_forward_failure`.
3. Candidate activation requires `candidate_quarantine_clear` in addition to the
   existing current validation, strict readiness, identity, and forward checks.
4. Legacy frozen candidates use the benchmark comparison curve consistently for
   both validation and progress. Forward-only preregistered candidates continue
   to use their immutable shadow observation policy.
5. Progress may continue to report current days and returns for research, but a
   quarantined candidate remains ineligible for paper/RPA authority, activation,
   or live execution. Further use requires a genuinely different strategy
   behavior and a new preregistration and forward window.

## Consequences

Positive:

- A terminal risk event cannot disappear because a later cumulative return
  crosses back above the early-stop threshold.
- Progress, validation, strict readiness, and activation now agree on both the
  observation source and the candidate's authority state.
- Operators can still inspect post-failure research behavior without confusing
  observation with restored eligibility.

Costs:

- A transient breach permanently retires that strategy fingerprint even if it
  later recovers.
- Existing quarantined candidates cannot be revived by extending the observation
  window; a successor must have a new independently justified behavior identity.

## Alternatives considered

### Clear quarantine after a recovery period

Rejected because it turns a predeclared hard stop into a tunable recovery rule
selected after observing the path.

### Keep failure history only as a warning

Rejected because activation could then contradict the append-only risk record.

### Use shadow observations for all candidate types

Rejected because legacy candidates were frozen and historically validated on the
benchmark comparison curve. Changing their authority basis retroactively would
rewrite evaluation semantics; immutable shadow evidence remains mandatory only
for forward-only preregistered candidates.
