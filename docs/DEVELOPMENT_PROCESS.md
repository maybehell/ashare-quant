# Development Process

Status: mandatory

Last reviewed: 2026-08-09

## 1. Before editing

1. Read `AGENTS.md`, `docs/ARCHITECTURE.md`, the latest development-log entries,
   and relevant ADRs.
2. Inspect the current worktree and generated evidence. Do not rely on an old
   report or conversation summary as proof of current behavior.
3. Classify the change:
   - research/data;
   - strategy governance;
   - account/execution;
   - broker/live safety;
   - UI/reporting;
   - infrastructure/documentation.
4. Identify the owning module, affected artifacts, compatibility requirements,
   safety invariants, and evidence needed for completion.

## 2. Plan and design

For a nontrivial change, write a short implementation plan before editing. If it
changes a durable boundary, state format, strategy authority, or safety policy,
add an ADR before or with the implementation.

Prefer existing module ownership and public services. Add an abstraction only
when it removes a real dependency violation, duplicated policy, or unstable
external interface.

## 3. Implementation rules

- Keep edits scoped to the owning layer.
- Preserve backwards-compatible artifact readers when historical evidence must
  remain auditable.
- Write new artifacts atomically and include schema/version/identity fields when
  they carry authority.
- Use structured parsers and serializers for structured data.
- Never weaken a gate merely to make a workflow pass.
- Never combine read-only refresh, ledger initialization, and submission into one
  implicit action.
- Never tune a frozen candidate using its forward observation results.

## 4. Verification matrix

| Change class | Minimum verification |
|---|---|
| Documentation only | governance test, links/content inspection |
| Narrow domain behavior | focused unit tests |
| Shared config/artifact schema | focused tests plus compatibility tests |
| Research/strategy logic | unit tests, no-future-data checks, benchmark and deterministic rerun |
| Account/financial state | unit tests, reconciliation, idempotency, cash/position invariants |
| Broker read-only | adapter tests, no protected artifact creation, snapshot provenance |
| Live submit/cancel/fill | focused tests, full regression, fail-closed negative cases, recovery/replay, safety-switch audit |
| UI/report | behavior tests plus rendered inspection and encoding check |

Full regression is required for shared configuration, execution artifacts,
strategy authority, account state, or live-control changes unless the log states
why it could not be run.

## 5. Documentation and log

Every repository modification updates `docs/DEVELOPMENT_LOG.md` in the same
change. The entry records:

- date and concise objective;
- files/components changed;
- behavior and architecture impact;
- tests/commands actually run and exact result;
- safety checks and protected artifacts when relevant;
- known limitations and next action.

Update `docs/ARCHITECTURE.md` only when architecture actually changes. Add or
supersede an ADR when the reason and alternatives should survive beyond the
implementation details.

## 6. Completion review

Before reporting completion:

1. Re-read the original requirement and map it to authoritative evidence.
2. Check that tests cover the changed behavior, including failure paths.
3. Inspect generated output or runtime state when tests are indirect.
4. Audit for accidental live capability changes.
5. Update the development log with real results, not planned results.
6. State remaining blockers plainly; do not relabel partial readiness as live
   readiness.

## 7. Development-log entry template

```markdown
## YYYY-MM-DD - Short title

**Objective**

One sentence describing the user-visible or operational outcome.

**Changes**

- Component/file and concrete behavior.

**Architecture and safety**

- Boundary, state, compatibility, and safety impact.

**Verification**

- `exact command`: exact result.

**Remaining work**

- Known limitation, blocker, or `None`.
```
