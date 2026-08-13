# Repository Development Contract

This file is the mandatory entry point for every code, configuration, script,
test, and documentation change in this repository.

## Required reading

Before editing, read:

1. `docs/ARCHITECTURE.md`
2. `docs/ROADMAP.md`
3. `docs/DEVELOPMENT_PROCESS.md`
4. The latest entries in `docs/DEVELOPMENT_LOG.md`
5. Relevant records under `docs/adr/`

## Rules for every change

1. Identify the owning architectural layer and preserve its dependency rules.
2. State the affected safety invariants before changing live-execution code.
3. Keep research, paper trading, broker read-only access, and broker submission
   as separate capabilities. Never enable submission as a side effect.
4. Add or update tests in proportion to the behavioral and safety impact.
5. Update `docs/DEVELOPMENT_LOG.md` in the same change. Record facts, commands,
   results, remaining risks, and any intentionally deferred work.
6. Update `docs/ARCHITECTURE.md` when module ownership, data flow, state
   ownership, public commands, or safety boundaries change.
7. Add an ADR for a durable decision that changes boundaries, state formats,
   execution semantics, data trust, or live-safety policy.
8. Do not describe a gate as passed unless an authoritative current artifact
   or test proves it.
9. Identify the roadmap phase for every change. Work from the earliest unmet
   gate, and never mark roadmap progress without current generated evidence.

## Live-trading invariants

- Default behavior is fail closed.
- Read-only broker access must not create orders or initialize a live ledger.
- Broker submission requires an explicit enable switch, inactive emergency
  stop, current signed rollout approval, current account binding, fresh broker
  evidence, valid order provenance, and a short-lived single-use confirmation.
- Research/backtest results cannot directly authorize execution.
- Existing live state and evidence are never silently reset, repaired, or
  replaced.
- Changes to adapter code, bridge configuration, or pinned files invalidate
  stale signatures and must be re-certified.

## Completion checklist

- Relevant focused tests pass.
- Full regression is run for shared or live-execution changes.
- Generated reports or UI are inspected when affected.
- Protected live artifacts and safety switches are audited when affected.
- Development log is updated.
- Architecture and ADRs are updated when required.
