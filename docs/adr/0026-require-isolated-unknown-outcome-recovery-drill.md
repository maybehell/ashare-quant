# ADR 0026: Require an isolated unknown-outcome recovery drill

Status: accepted

Date: 2026-08-12

## Context

The live control plane already persists a pre-submit intent, reconstructs a
missing command-adapter result as `unknown`, blocks duplicate submission, and
requires broker query evidence before settlement. Unit tests and saved adapter
replays cover those components, but an operator had no single current artifact
showing that the complete interruption path still composes correctly under the
active live configuration before requesting a Canary.

Running this check against a broker would itself introduce financial risk and
would make a deterministic software-safety prerequisite depend on an available
desktop session.

## Decision

1. Add `auto-live-recovery-drill` as a deterministic live-control command.
2. Build one signed synthetic submission request in a temporary directory, but
   never execute the command adapter.
3. Require the drill to prove request-bound `unknown` reconstruction, duplicate
   suppression from the persisted intent, broker-order-ID recovery through a
   synthetic query, continued blocking while the order is active, and release
   only after a terminal query status.
4. Delete the entire temporary workspace and publish only an atomic audit and
   summary in the configured live output directory.
5. Make a fresh, schema-valid summary bound to the current `auto_live` config a
   blocking Canary prerequisite. Any broker-invoked marker, retained workspace,
   missing HMAC, stale timestamp, or identity mismatch fails closed.
6. Treat this evidence as a software recovery check only. It does not replace
   certified real broker submit/query/cancel/fill replay, initialize a live
   ledger, activate a strategy, or grant submission authority.

## Consequences

- Operators can rerun a complete crash-recovery invariant check without a
  broker session or order risk.
- A live-config change invalidates the drill evidence and requires a new run.
- Canary readiness gains another independent blocking artifact; current
  strategy, capital, ledger, account-binding, and lifecycle blockers remain.
- The drill cannot prove broker-specific status mappings or desktop behavior;
  those remain part of separately authorized lifecycle certification.

## Alternatives considered

- Rely only on unit tests: rejected because tests do not produce current,
  configuration-bound deployment evidence.
- Reuse adapter replay certification: rejected because replay proves saved
  broker protocol evidence and has different trust and availability semantics.
- Exercise the real broker during the drill: rejected because a prerequisite
  intended to reduce order risk must not create an order risk of its own.
- Allow the prerequisite to be disabled in configuration: rejected because the
  fail-closed behavior is a system safety invariant, not a deployment option.
