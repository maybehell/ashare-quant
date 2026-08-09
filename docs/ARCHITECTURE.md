# AShare Quant Unified Architecture

Status: authoritative

Version: 1.0

Last reviewed: 2026-08-09

## 1. Purpose

This document is the single architectural source of truth for AShare Quant.
It describes the system that exists, the boundaries every change must preserve,
and the planned path from local research to guarded live operation.

The system is a local A-share daily quantitative platform. Its end-to-end flow
is:

`market data -> research -> frozen candidate -> forward evidence -> execution
strategy -> orders -> risk gates -> broker adapter -> reconciliation -> report`

Live operation means controlled, auditable execution. It does not mean that a
backtest, GUI button, RPA preview, or broker login is sufficient authorization
to submit an order.

## 2. Architectural principles

1. **Fail closed:** missing, stale, ambiguous, corrupted, or mismatched evidence
   blocks the next privileged step.
2. **Evidence before authority:** research produces evidence; signed governance
   grants authority; execution consumes authority. These are distinct stages.
3. **One owner per state:** every mutable artifact has one component responsible
   for writing it.
4. **Replayable decisions:** important decisions include identities, hashes,
   timestamps, configuration fingerprints, and source provenance.
5. **Capability separation:** research, paper trading, broker read-only access,
   form preview, and final submission remain separately enabled capabilities.
6. **Point-in-time correctness:** strategy inputs must respect when data became
   available and must not use future constituents or financial information.
7. **Idempotent recovery:** repeated observation, query, settlement, and alert
   operations must not duplicate financial effects.

## 3. System layers

### 3.1 Interfaces and orchestration

Owners: `cli.py`, `gui.py`, `local_operator.py`, `observation_task.py`,
`observation_progress.py`, and PowerShell scripts under `scripts/`.

Responsibilities:

- Parse operator intent and select an application workflow.
- Display state without redefining business or safety rules.
- Schedule observation and read-only refresh work.
- Return explicit exit status and artifact paths.

Rules:

- Interfaces call domain/application services; they do not duplicate gate logic.
- A GUI action maps to the same service and checks as its CLI equivalent.
- Scheduling cannot silently add submission capability.
- Required unattended observation must have both a fresh live heartbeat and a verified current-user Startup entry; stale status files are not liveness evidence.

### 3.2 Configuration, data, and market infrastructure

Owners: `config.py`, `io_utils.py`, `data.py`, `baostock_source.py`,
`source_health.py`, `index_membership.py`, `index_symbols.py`,
`trading_day_status.py`, and `fundamental_policy.py`.

Responsibilities:

- Merge and validate configuration.
- Acquire, normalize, cache, and fingerprint market/reference data.
- Preserve provenance, trading dates, listing dates, industries, and
  point-in-time index membership.

Rules:

- Normalized schemas are the boundary; source-specific columns do not leak into
  strategy or execution code.
- Network failure may preserve a clearly identified old cache, but cannot label
  it fresh.
- Cache replacement and partial recovery are atomic and auditable.

### 3.3 Strategy research and portfolio construction

Owners: `universe.py`, `factors.py`, `forward_factors.py`, `portfolio.py`,
`industry_risk.py`, `backtest.py`, `walkforward.py`, `research.py`,
`stress.py`, `factor_robustness.py`, `selection_bias.py`,
`statistical_validation.py`, `capital_aware.py`, and
`capital_sensitivity.py`.

Responsibilities:

- Build the eligible point-in-time universe.
- Compute lagged factors and constrained target portfolios.
- Simulate A-share rules, costs, liquidity, lots, and T+1 behavior.
- Measure benchmark-relative, out-of-sample, robustness, and capital-feasibility
  evidence.

Rules:

- Research modules never call broker or RPA modules.
- Parameter selection and final evaluation evidence are distinguished.
- Every formal result binds configuration, input data, benchmark, and code-level
  assumptions through manifests or hashes.

### 3.4 Strategy governance and promotion

Owners: `candidate.py`, `candidate_family.py`, `challengers.py`,
`research_governance.py`, `selected_candidate_refresh.py`, `shadow.py`,
`shadow_policy.py`, `evidence.py`, `readiness.py`, `validation.py`,
`deployment_gate.py`, `strategy_health.py`, and `strategy_activation.py`.

Responsibilities:

- Preregister and freeze candidate identities and inputs.
- Accumulate immutable forward and shadow evidence.
- Prevent selection-bias and current-cache optimization from masquerading as
  forward validation.
- Activate only a fully identified candidate and rotate execution epochs.

Rules:

- Backtest rank alone never activates a strategy.
- Historical reruns cannot increase forward-observation days.
- Candidate changes create a new identity and restart applicable evidence.
- Activation changes strategy authority only; it does not enable broker submit.

### 3.5 Account and execution domain

Owners: `models.py`, `rules.py`, `risk.py`, `account.py`, `paper.py`,
`execution.py`, `execution_artifacts.py`, `order_integrity.py`, `fills.py`,
`reconciliation.py`, `execution_reconciliation.py`, `order_audit.py`, and
`execution_quality.py`.

Responsibilities:

- Represent orders, fills, cash, positions, available quantity, and fees.
- Generate deterministic paper orders from an authorized strategy epoch.
- Apply A-share lot, T+1, price-limit, suspension, fee, and liquidity rules.
- Reconcile every financial state transition.

Rules:

- Order artifacts carry provenance and execution-epoch identity.
- Fill imports are idempotent and bounded by source orders.
- SQLite financial effects and exported evidence must be reconcilable.
- Paper and live ledgers use explicit paths and are never implicitly exchanged.

### 3.6 Broker boundary and live-safety control plane

Owners: `autolive*.py`, `broker_initialization.py`, `broker_preview.py`,
`broker_readonly_history.py`, `canary_capital.py`, `huatai_*.py`, `rpa.py`,
`live_secrets.py`, and bridge/alert scripts under `scripts/`.

Responsibilities:

- Read broker account state through a pinned adapter.
- Certify preview, monitor, query, cancel, fill, and recovery behavior.
- Enforce account binding, staged rollout, cash/order limits, emergency stop,
  freshness windows, single-use confirmation, and incident alerts.
- Maintain append-only journals and recovery points around privileged actions.

Rules:

- Broker-specific UI details remain behind the adapter boundary.
- Read-only refresh cannot submit orders or initialize the live ledger.
- Submission is impossible unless every independent gate passes in the current
  run; old reports are not authority.
- Unknown or partial outcomes trigger query/recovery and critical alerting, not
  blind retry.
- Adapter/pin/config changes invalidate stale certification and signatures.

### 3.7 Presentation and reporting

Owners: `report.py`, `dashboard.py`, `diagnostics.py`, `remediation.py`,
`observation_plan.py`, and `account_plan.py`.

Responsibilities:

- Render Chinese operator-facing summaries from authoritative artifacts.
- Compare strategy with benchmark and expose confidence limitations.
- Present blockers, remediation, and evidence age without changing state.

Rules:

- Reports explain evidence; they do not grant execution authority.
- Human-readable labels must preserve the underlying machine check identity.
- Missing sections are shown as missing, not silently treated as passing.

## 4. Dependency direction

Allowed direction:

`interfaces -> application workflows -> domain/governance -> infrastructure`

Additional constraints:

- Research may depend on normalized market infrastructure and shared domain
  rules, but never on broker implementations.
- Broker control may consume signed execution artifacts and account-domain
  models, but cannot invoke research to invent an order during submission.
- Reporting may read all output artifacts but must not mutate financial state.
- Shared helpers must remain small and policy-free; do not move business rules
  into `io_utils.py` merely to avoid an import.
- Cross-layer cycles require an ADR and a migration plan; they are not accepted
  as incidental convenience.

## 5. State and artifact ownership

| State/artifact | Authoritative writer | Consumers | Mutation policy |
|---|---|---|---|
| Parquet market cache and provenance | data layer | research, valuation | atomic refresh, source/date audit |
| Research/backtest outputs | research workflow | governance, reports | replace by run; bind manifest; simulated orders use `backtest_orders.csv` only |
| Frozen candidate and sealed inputs | candidate governance | forward observer, activation | immutable identity |
| Active execution strategy/epoch | strategy activation | paper/live order generation | atomic activation/deactivation |
| Paper SQLite ledger | account/paper domain | reports, reconciliation | transactional |
| Broker read-only snapshot/history | broker adapter/history | binding, risk, reports | append/audit; no financial writes |
| Live SQLite ledger | broker initialization/fills | risk, settlement | explicit initialization; transactional |
| Orders/RPA tickets | paper/execution pipeline | preview/live control | `orders.csv` and `rpa_orders.csv` are never written by backtest; provenance-bound, quarantine stale batch |
| Rollout approval/confirmation | live control plane | submission gate | signed, expiring, single purpose |
| Auto-live journal/recovery | live control plane | recovery, audit | append-only/hash chained |
| HTML/CSV reports | presentation | operator | derived, never authoritative for submit |

## 6. Canonical workflows

### Research and promotion

1. Refresh and audit normalized data.
2. Preregister candidate family and evaluation policy.
3. Run backtest, walk-forward, benchmark, cost, factor, and selection-bias tests.
4. Freeze candidate parameters and input fingerprints.
5. Accumulate new-date forward and shadow observations.
6. Audit promotion against the preregistered policy.
7. Activate an exact candidate identity and rotate execution epoch.

### Daily safe operation

1. Refresh market data and broker read-only evidence independently.
2. Update frozen candidate observations without tuning.
3. Recompute strategy/readiness reports.
4. Generate paper orders only when the corresponding gate permits it.
5. Notify on data, strategy, broker, and operational failures.

### Guarded live cycle

1. Verify strategy authority and current execution epoch.
2. Verify fresh broker snapshot and signed account binding.
3. Verify reconciliation, order provenance, liquidity, cash, T+1, and limits.
4. Verify pinned adapter, certification, trading window, rollout approval,
   emergency stop, alert channel, recovery point, and single-use confirmation.
5. Submit once through the adapter.
6. Persist intent/result identity and query ambiguous outcomes.
7. Import verified fills idempotently and reconcile broker/local state.

## 7. Safety invariants

The following are architectural requirements, not optional implementation detail:

- `allow_broker_submit=false` always blocks submission.
- An active emergency stop always blocks submission.
- A missing live ledger cannot be initialized by read-only refresh or submission.
- A broker account mismatch or stale binding always blocks submission.
- Stale, unsigned, duplicated, or hash-mismatched artifacts always block the
  privileged action that consumes them.
- Buy quantities obey lot rules; sell availability obeys T+1 and broker truth.
- A timed-out submit is an unknown outcome until queried; it is never assumed
  failed and automatically retried.
- No strategy result can promote itself or lower its own validation threshold.
- Forward evidence is based only on dates later than the freeze/evidence date.
- All live incidents remain visible even if alert delivery initially fails.

## 8. Evolution plan

### P0: safety and evidence foundation (current)

- Maintain fail-closed live gates and Huatai read-only evidence.
- Accumulate the preregistered 60-trading-day forward window.
- Keep broker submission disabled until a separate initialization/Canary review.

### P1: stabilize application boundaries

- Split oversized `cli.py`, `report.py`, and `autolive.py` into command,
  application-service, and renderer modules without changing behavior.
- Introduce typed contracts for normalized market bars, strategy authority,
  order batches, broker snapshots, and adapter results.
- Centralize artifact path/identity schemas while retaining current formats.

### P2: durable state and recovery

- Define explicit repository interfaces around SQLite and append-only evidence.
- Add schema versions and forward-only migrations for live state.
- Add deterministic crash/restart tests across submit, query, cancel, and settle.

### P3: broker adapter maturity

- Keep desktop automation behind the existing adapter contract.
- Prefer an official broker interface if one becomes available and can provide
  stronger identity, status, and reconciliation guarantees.
- Run read-only soak tests and a separately authorized one-order Canary before
  any limited rollout.

### P4: operational hardening

- Add health/latency/error budgets for data, observation, alerts, and broker
  operations.
- Add backup restore drills and operator runbooks for unknown submission,
  reconciliation mismatch, stale data, and emergency stop recovery.

## 9. Architecture change policy

Update this document in the same change whenever any of these change:

- module ownership or dependency direction;
- canonical workflow or public command behavior;
- persistent state schema, location, ownership, or authority;
- data trust/provenance semantics;
- strategy promotion criteria;
- broker capability or live-safety invariant.

Create an ADR for a durable choice with meaningful alternatives. Do not rewrite
an accepted ADR to hide history; supersede it with a new ADR.
