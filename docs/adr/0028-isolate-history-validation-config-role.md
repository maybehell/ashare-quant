# ADR 0028: Isolate the fixed-strategy history-validation configuration role

Status: accepted

Date: 2026-08-13

## Context

The extended-history backfill had completed all 800 requested symbols, but its
daemon status still showed an old validation failure and an obsolete script
identity. The current Startup entry was missing, while the installer considered
any fresh process status sufficient without binding it to the current daemon,
configuration, or output directory. A default observation or broker-live
configuration could also be passed to the history scripts without an explicit
capability check.

The fixed validation itself succeeded when rerun directly against the completed
cache. The operational problem was therefore ambiguous and stale research
orchestration evidence, not a reason to alter the frozen strategy.

## Decision

1. Fixed-strategy extended-history work declares the explicit
   `research_history_validation` configuration role.
2. The history daemon and Startup installer fail before creating state unless
   the role, isolated output ownership, research-only flag, inactive execution
   application, fixed strategy hash, emergency stop, and submission-off policy
   all pass one structured audit.
3. Output redirection is available only through the daemon's explicit `DryRun`
   path. The production installer never requests this exception.
4. Daemon status schema 4 records its owned output directory and a bounded error
   tail. A failed validation retains that diagnostic while waiting to retry.
5. Startup verification binds a fresh status to the current daemon hash, exact
   configuration, exact output directory, and live PID.
6. Once completed history passes fixed validation, the daemon writes `complete`
   and exits. A persistent Startup entry is not required for already complete
   evidence and is not installed merely to make a status indicator green.
7. Current formal frozen-candidate evidence takes precedence over older research
   promotion records in operator guidance.
8. Long-history artifact currency and candidate applicability are separate. A
   current, reproducible historical experiment whose strategy hash differs from
   the authoritative frozen candidate is labeled non-applicable and cannot
   support that candidate's promotion or execution readiness.

## Consequences

- Observation, broker-live, and history-validation capabilities cannot be
  exchanged by changing command arguments.
- Stale or unrelated processes cannot satisfy history-daemon liveness checks.
- Operators receive the relevant validation error instead of a generic retry
  message, while old schema files remain readable but not current evidence.
- Completed long-history research does not activate, promote, or authorize a
  strategy and cannot create broker orders or a live ledger.

## Alternatives considered

- Reinstall the old daemon unchanged: rejected because it would preserve weak
  process identity checks and poor failure diagnostics.
- Keep a daemon running after successful validation: rejected because complete,
  immutable research evidence has no remaining acquisition job.
- Change strategy parameters in response to the stale failure: rejected because
  the direct fixed-strategy validation succeeded and the candidate is frozen.
