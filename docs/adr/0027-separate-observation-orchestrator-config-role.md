# ADR 0027: Separate the observation orchestrator configuration role

Status: accepted

Date: 2026-08-12

## Context

The local machine was found running two observation supervisors. The original
instance used the strategy orchestration configuration and `outputs`, while a
later Startup entry passed the Huatai broker configuration as both the strategy
and alert configuration and wrote scheduler state into `outputs_live`. The
second instance remained non-trading, but it fragmented operational truth and
could report a successful daily run that had not observed the registered frozen
strategy candidate.

Configuration inheritance made field-based guessing unsafe: a broker override
can inherit the default candidate list, and a frozen candidate can inherit many
ordinary strategy settings. Paths alone are also not durable role identifiers.

## Decision

1. Every operational top-level configuration declares one explicit
   `operations.config_role`: `observation_orchestrator`, `broker_live`, or
   `research_candidate`.
2. The observation Startup installer, supervisor, and daemon call one structured
   role audit before writing a launcher, lock, state, status, or starting a child
   process.
3. A valid observation orchestrator owns the requested output directory and
   registers at least one existing, research-only, preregistered forward
   candidate with a distinct cache and output directory.
4. Output redirection is accepted only for the daemon's explicit `DryRun` test
   mode. The installer and production supervisor never request that exception.
5. The deployment gate requires an orchestrator role and matching output
   ownership before rendering. It leaves detailed candidate topology failures to
   its existing individual checks so operators retain precise remediation.
6. The broker configuration remains usable only as the separate alert/live
   configuration for observation. It cannot become the strategy orchestrator by
   argument substitution.
7. Daemon status distinguishes core observation (`running`) from post-core
   read-only/report/alert work (`finalizing`). A matching core status artifact
   can change the displayed phase but cannot terminate the child or record final
   success; the complete runner exit remains authoritative.

## Consequences

- A wrong Startup command fails before creating operational evidence instead of
  silently producing a second truth source.
- Candidate configs can no longer render a misleading orchestrator deployment
  gate directly; the default orchestration config remains the authoritative
  entry point.
- Existing tests that isolate daemon output must use explicit `DryRun`, while
  production output ownership remains strict.
- Changing a config role is an architectural change and requires review; it does
  not activate a strategy or grant broker authority.

## Alternatives considered

- Infer roles from filenames: rejected because paths are mutable and not part of
  the behavioral contract.
- Infer roles only from inherited fields: rejected because deep merge preserves
  unrelated defaults and caused the original ambiguity.
- Allow multiple supervisors with different output directories: rejected because
  the product has one authoritative daily observation workflow and one operator
  status.
- Fix only the current Startup file: rejected because the same invalid argument
  combination could recur without a code-level guard.
