# ADR 0033: Separate candidate history decision from quarantine

Status: accepted

Date: 2026-08-13

## Context

The frozen small-capital candidate has a completed exact-strategy historical
validation that failed benchmark-relative thresholds, while its forward display
still says 0/60 sessions. Existing quarantine state records forward failures but
does not bind authoritative historical evidence. Automatically reusing that path
would make a read-only research command silently change durable candidate state.

## Decision

1. Add a read-only candidate history decision before adding any quarantine write.
2. Require the configured history file, candidate strategy, fixed strategy,
   completed status, backtest identity, formal-price hash, and input binding to
   match exactly.
3. Evaluate total and held-out excess, information ratio, drawdown, walk-forward
   fold count and consistency, and bootstrap evidence against current fixed gates.
4. Report identity or artifact gaps as `blocked`, economic failures as
   `rejected`, and all gates passing as `supported`.
5. Do not let any decision mutate the candidate manifest, failure history,
   observation schedule, execution strategy, account, orders, or broker state.
6. A later permanent rejection action must be explicit, idempotent, and bound to
   the decision hash; it is not a side effect of this audit.

## Consequences

- The operator can distinguish "still collecting" from "historically rejected"
  without creating authority or destroying forward evidence.
- A changed threshold or input produces a new signed decision identity.
- Historical support remains necessary but insufficient for promotion; 60 valid
  forward sessions and every other P1 gate remain required.

## Alternatives considered

- Automatically quarantine after `history-validate`: rejected because research
  completion must not silently mutate candidate governance state.
- Reuse forward-failure rows without history provenance: rejected because the
  evidence and semantics differ.
- Ignore the historical failure until 60 forward sessions: rejected because it
  presents a known failed candidate as merely incomplete.
