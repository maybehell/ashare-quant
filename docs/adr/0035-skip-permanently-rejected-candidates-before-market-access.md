# ADR 0035: Skip permanently rejected candidates before market access

Status: accepted

Date: 2026-08-13

## Context

ADR 0034 made authoritative history rejection durable, but the daily scheduler
still treated every configured forward candidate as eligible for refresh and
observation. That could download data and recompute shadow evidence for a
candidate that can never be promoted, while an empty candidate set could make
the otherwise read-only daily workflow look operationally failed.

## Decision

1. Resolve the exact candidate identity and append-only quarantine record before
   market refresh, freshness checks, shadow signals, or observation audits.
2. Skip a candidate whose latest matching durable status is
   `authoritative_history_rejected` before all market access and computation.
3. Preserve one summary row with status `permanently_rejected`, the bound
   decision hash, and an explicit requirement for independent successor research.
4. Treat that status as an expected fail-closed research state in the daily
   orchestrator, while continuing to fail on corrupt identity, missing evidence,
   stale data, or any other observation error.
5. Do not delete the candidate registration or historical artifacts. Do not
   infer, select, activate, or initialize a successor.

## Consequences

- One-click and scheduled runs no longer spend network or compute resources on
  the permanently rejected candidate.
- Reports retain an auditable explanation instead of silently omitting the old
  candidate or presenting it as still collecting forward evidence.
- A daily run can continue its independent read-only reporting and deployment
  blockers while no eligible successor exists.
- A new candidate remains subject to preregistration and its own independent
  historical gate before forward observation begins.

## Alternatives considered

- Remove the rejected config from the orchestrator: rejected because that hides
  audit context and makes configuration drift harder to distinguish from policy.
- Continue observation for informational value: rejected because the frozen
  candidate is permanently ineligible and later data cannot restore authority.
- Fail the entire daily task: rejected because permanent rejection is a known
  safe state, while unrelated read-only health and deployment evidence must
  remain refreshable.
