# ADR 0034: Bind permanent history rejection to decision hash

Status: accepted

Date: 2026-08-13

## Context

ADR 0033 separated read-only candidate history assessment from durable
quarantine. Candidate `d55d124b5800e123` now has an exact, reproducible rejected
decision, but its permanent failure history still contains only older forward
failures. An implicit write during validation or daily observation would violate
capability separation, while an unbound manual status edit could record a result
from stale or mismatched evidence.

## Decision

1. Add a separate `candidate-history-reject` command that requires both explicit
   confirmation and the complete current decision SHA-256.
2. Recompute the read-only decision immediately before writing. Block missing
   confirmation, malformed or stale hashes, non-rejected decisions, and candidate
   identity mismatches.
3. Append `authoritative_history_rejected` to the candidate failure history with
   candidate, strategy, behavior, decision, evidence, and failed-gate identities.
4. Make the write idempotent for one candidate and one decision hash. Never
   replace or merge a conflicting historical rejection record.
5. Preserve legacy failure-history rows by adding new columns as empty values.
6. Expose historical rejection as `quarantined_history_rejection`; retain
   `quarantined_forward_failure` for existing forward failures.
7. Do not couple the command to historical validation, daily observation,
   candidate deletion, successor activation, account initialization, orders, or
   broker access.

## Consequences

- A known historical failure can become a durable promotion blocker without
  changing the frozen candidate or its forward evidence.
- Repeating the exact command does not add another row or change its timestamp.
- Changed evidence or thresholds create a different decision hash and cannot
  silently rewrite the recorded rejection.
- A successor requires a new preregistered strategy identity and independent
  evidence budget; quarantine does not select or activate that successor.

## Alternatives considered

- Write quarantine automatically after history validation: rejected because
  research computation must not mutate durable governance state as a side effect.
- Accept candidate ID without an evidence hash: rejected because the operator
  could bind stale or unrelated evidence.
- Delete the failed candidate manifest: rejected because immutable evidence must
  remain auditable.
