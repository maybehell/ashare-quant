# ADR 0001: Evidence-gated layered architecture

Status: accepted

Date: 2026-08-09

## Context

AShare Quant has grown from a local backtester into a system containing strategy
research, paper accounting, candidate governance, broker read-only automation,
RPA support, and guarded live-execution components. Without a declared boundary,
convenient direct calls can allow research results, UI actions, or stale files to
acquire execution authority they should not possess.

## Decision

Adopt the layered architecture and artifact ownership defined in
`docs/ARCHITECTURE.md`.

The key decision is an evidence-gated authority chain:

1. Research creates reproducible evidence.
2. Candidate governance freezes identity and accumulates genuinely new forward
   evidence.
3. Strategy activation grants authority to one exact candidate and execution
   epoch, without enabling broker submission.
4. Account/execution creates provenance-bound order artifacts.
5. The live control plane independently validates broker, account, rollout,
   risk, recovery, alert, freshness, and operator-confirmation gates.
6. Only the broker adapter may perform broker-specific actions.

Read-only broker refresh, live-ledger initialization, and order submission remain
separate capabilities and commands.

## Consequences

Positive:

- No single backtest, report, GUI control, or adapter response can authorize a
  live trade.
- State authority and recovery paths are explicit and auditable.
- Broker implementations can change without moving broker details into strategy
  or account logic.
- Incremental refactoring can be evaluated against declared dependency rules.

Costs:

- Live readiness requires more artifacts and negative-path checks.
- Configuration and artifact changes intentionally invalidate stale approvals.
- Some current large modules require gradual decomposition to fully express the
  layers in code.

## Alternatives considered

### One end-to-end workflow controlled by the GUI

Rejected because presentation state would become execution authority and failure
recovery would be ambiguous.

### Backtest threshold directly enables paper/live execution

Rejected because it does not control selection bias, forward evidence, account
identity, order provenance, or broker outcome uncertainty.

### Broker UI automation embedded in order-generation code

Rejected because it couples strategy/account behavior to unstable UI details and
makes read-only guarantees difficult to prove.

### Immediate rewrite into services

Rejected for now. A local daily system does not need distributed-service
complexity; modular in-process boundaries and durable artifacts provide the
needed auditability with lower operational risk.
