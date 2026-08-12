# AShare Quant Unified Architecture

Status: authoritative

Version: 1.0

Last reviewed: 2026-08-11

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
- Run scheduled broker read-only refresh after market observation and before
  report/status/alert rendering, preserving broker failure as the current
  operator evidence without rerunning the market workflow.
- Return explicit exit status and artifact paths.
- Recompute strategy, backtest, and formal-price identities before exposing a
  completed long-history validation in the operator status or report.

Rules:

- Interfaces call domain/application services; they do not duplicate gate logic.
- A GUI action maps to the same service and checks as its CLI equivalent.
- Scheduling cannot silently add submission capability.
- A research scheduler may invoke fixed-parameter validation after authoritative
  data completion, but cannot promote a candidate, activate a strategy, create
  execution orders, or call the broker boundary.
- Required unattended observation and research backfill must have a fresh status
  bound to the current script plus a verified current-user Startup entry; stale
  status files are not liveness evidence.

### 3.2 Configuration, data, and market infrastructure

Owners: `config.py`, `io_utils.py`, `data.py`, `baostock_source.py`,
`source_health.py`, `index_membership.py`, `index_symbols.py`,
`trading_day_status.py`, and `fundamental_policy.py`.

Responsibilities:

- Merge and validate configuration.
- Acquire, normalize, cache, and fingerprint market/reference data.
- Preserve provenance, trading dates, listing dates, industries, and
  point-in-time index membership.
- Audit historical index-member coverage and publish machine-readable snapshot
  completeness evidence before readiness can consume the strategy result.

Rules:

- Normalized schemas are the boundary; source-specific columns do not leak into
  strategy or execution code.
- Network failure may preserve a clearly identified old cache, but cannot label
  it fresh.
- Cache replacement and partial recovery are atomic and auditable.
- A cold membership-only fetch is orchestrated in dependency order: benchmark
  history, point-in-time membership, then the research-period union of member
  prices. Pre-price membership audits produce blocked evidence instead of
  aborting initialization.
- Point-in-time index readiness requires 100% historical-member price coverage
  and 100% coverage in every snapshot. Missing exited-member prices block both
  practical and strict readiness; metadata coverage cannot substitute for them.
- Frozen candidate caches are never historically repaired in place. Corrective
  reconstruction uses isolated paths and research-only identity, and its
  historical output cannot inherit forward evidence or execution authority.
- An immutable candidate refresh preserves the complete frozen stock catalog and
  every fundamental row at or before the freeze boundary. It may append only
  post-freeze prices, benchmark rows, and fundamentals; a missing frozen baseline
  fails closed instead of being reconstructed from newer market metadata.
- Historical gaps are evaluated per symbol against the requested research start
  and the verified listing date; incremental latest-date overlap alone is not
  sufficient evidence of historical coverage.
- Long historical refreshes use validated, namespaced partial prices,
  provenance, failures, and progress. A request budget may stage incomplete
  work, but only complete batches that pass the normal fetch-success threshold
  may replace the formal market cache.
- Bounded history progress distinguishes source-wide unavailability from
  symbol-level failures. A source-wide outage returns a non-success audit code
  and enters a persisted restart-safe backoff; it never advances completion or
  replaces the formal cache.
- Historical source failover is ordered and provenance preserving. Every
  candidate source must pass normalized price-structure validation before it is
  selected; a non-empty but invalid primary response continues to the next
  source. The isolated extended-history job may use Eastmoney, Sina, and Tencent
  through AkShare; Sina rows are accepted only after recent overlap validation.
  If committed overlap is missing, insufficient, or inconsistent, a second
  alternate provider must pass the same close, volume, and amount thresholds;
  verifier identity and errors are persisted. Tencent STAR Market volume is
  normalized as shares while its other supported A-share boards are normalized
  from lots; Tencent estimated turnover remains verifier-only. The fixed
  validation job rejects estimated amount and untrusted resumed provenance.
- The isolated AkShare auto-failover history job serializes provider requests
  because its in-process V8 decoder is not safe under the observed threaded
  workload; the default BaoStock daily worker setting is unaffected.
- Partial checkpoints write source provenance before corresponding prices so a
  crash can lose recoverable work but cannot leave unproven prices eligible for
  resume. Persisted source-outage backoff is reused only when the effective
  default/override source-plan identity is unchanged.
- A history series that starts after the configured coverage tolerance is not
  accepted merely because its rows are structurally valid. A separately queried
  alternate provider must report the same first trading date; that expected,
  observed, verifier, and status evidence is persisted and must still match
  before a later run may resume the series.
- A bounded history daemon may use the 15-minute healthy interval only after a
  zero-exit batch reports source status `ok`, zero failures, at least one
  attempt, and equal attempted/successful counts. Degraded batches use the
  regular interval; source-wide failures retain the longer persisted backoff.
- Backtest, readiness, stress, capital-feasibility, paper/shadow signal, and
  broker-preview calculations use the exact symbol scope of the latest
  completed fetch. Account valuation may retain prices for held symbols outside
  that scope, but resolves the latest complete market date from the committed
  scope. Derived evidence that can affect execution readiness must bind to the
  current main run-manifest data identity.
- A candidate incremental refresh keeps the complete committed symbol scope
  separate from the smaller request subset. Per-symbol source provenance is
  retained across increments, newly entering symbols are fetched before the
  scope can pass, and exiting symbols are removed from that scope. Paper and
  shadow signal generation fail closed when the committed scope is incomplete.
- Data-acquisition scheduling and resumability knobs are operational identity,
  not strategy identity. They are excluded from the backtest strategy hash;
  their actual data effects remain bound by market-file and run-manifest hashes.

### 3.3 Strategy research and portfolio construction

Owners: `universe.py`, `factors.py`, `forward_factors.py`, `portfolio.py`,
`industry_risk.py`, `backtest.py`, `walkforward.py`, `research.py`,
`stress.py`, `factor_robustness.py`, `selection_bias.py`,
`statistical_validation.py`, `capital_aware.py`, `capital_budget.py`,
`capital_sensitivity.py`, `small_capital_readiness.py`, and
`history_validation.py`.

Responsibilities:

- Build the eligible point-in-time universe.
- Compute lagged factors and constrained target portfolios.
- Simulate A-share rules, costs, liquidity, lots, and T+1 behavior.
- Measure benchmark-relative, out-of-sample, robustness, and capital-feasibility
  evidence.

Rules:

- Research modules never call broker or RPA modules.
- An opt-in research capital budget fails before execution when initial cash or
  any configured sensitivity level exceeds its hard ceiling. It does not
  retroactively mutate frozen candidate evidence.
- An opt-in small-capital admission gate binds capital fidelity, current market
  identity, drawdown, annual consistency, fixed-parameter walk-forward, and
  symmetric cost-path stability before forward-only candidate preregistration.
  Passing this gate can start observation only and grants no execution authority.
- Capital-aware lot planning may use fixed conservative planning costs and
  downward-rounded planning equity, while actual fills retain scenario costs and
  actual account equity. Planning quantities are encoded against actual
  investable equity, and supported algorithm revisions carry an explicit schema
  version in backtest and capital-sensitivity identity.
- Parameter selection and final evaluation evidence are distinguished.
- Every formal result binds configuration, input data, benchmark, and code-level
  assumptions through manifests or hashes.
- Extended-history validation requires an authoritative zero-remaining,
  zero-failure completion record, isolated research outputs, a formal-price
  file hash, and a bound survivorship-exposure audit. The audit compares the
  current committed pool with available cached history but must remain failed
  for point-in-time completeness until authoritative daily membership, delisted
  securities, and historical eligibility state exist. Completed validation is
  evidence only and grants no strategy authority.

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
- Candidate preregistration invokes the small-capital admission assertion when
  that policy is enabled; changing experiment status cannot bypass failed
  research evidence.
- Small-capital candidates selected or tuned on the current cache remain blocked
  from forward preregistration regardless of their historical metrics.
- Historical reruns cannot increase forward-observation days.
- Candidate changes create a new identity and restart applicable evidence.
- A recorded early or terminal forward failure permanently quarantines the matching
  strategy or behavior fingerprint. Validation, progress, strict readiness, and
  activation consume the same append-only failure history; later returns cannot
  restore eligibility. Legacy candidates use benchmark-comparison evidence
  consistently, while forward-only preregistrations use immutable shadow evidence.
- Publishing research evidence into the live audit workspace requires a current
  base run manifest and current identities for every required derived artifact.
  Factor-robustness detail and summary are part of the allowlisted bundle. Every
  copied row carries one bundle hash, and any source mutation during publication
  aborts success; orders, positions, and account state are never included.
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
- Paper and shadow provenance records whether the committed fetch scope was
  available; missing scope evidence cannot independently authorize execution.
- Fill imports are idempotent and bounded by source orders.
- SQLite financial effects and exported evidence must be reconcilable.
- Paper and live ledgers use explicit paths and are never implicitly exchanged.

### 3.6 Broker boundary and live-safety control plane

Owners: `autolive*.py`, `broker_initialization.py`, `broker_preview.py`,
`broker_readonly_history.py`, `canary_capital.py`, `capital_modes.py`,
`huatai_*.py`, `rpa.py`,
`live_secrets.py`, and bridge/alert scripts under `scripts/`.

Responsibilities:

- Read broker account state through a pinned adapter.
- Certify preview, monitor, query, cancel, fill, and recovery behavior.
- Enforce account binding, staged rollout, cash/order limits, emergency stop,
  freshness windows, single-use confirmation, and incident alerts.
- Classify capital evidence as refresh required, observation only, Canary review
  only, or full-strategy review without adapting the frozen strategy.
- Maintain append-only journals and recovery points around privileged actions.

Rules:

- Broker-specific UI details remain behind the adapter boundary.
- Read-only refresh cannot submit orders or initialize the live ledger.
- Unattended desktop recovery may restore one uniquely identified pinned trading
  window or send one navigation key to one uniquely identified pinned market
  window. When explicitly enabled, a separate login-only driver may retrieve an
  account ID and trading password from the current Windows user's DPAPI store,
  require one exact pinned login form, verify account-field readback, and click
  the exact login control once. It never handles a captcha or second factor,
  initializes a ledger, imports account state, navigates order forms, or invokes
  submit/cancel; ambiguity and failed snapshot confirmation return to manual login.
- When explicitly enabled, a separate post-login notice driver may close one
  uniquely identified pinned `xiadan.exe` `#32770` navigation announcement with
  one `WM_CLOSE` and bounded disappearance confirmation. It never changes notice
  preferences or uses generic keys, buttons, or screen coordinates; ambiguity
  blocks subsequent desktop Canary navigation for that refresh.
- A stale broker snapshot clears both effective Canary and full-strategy capital
  readiness. Stale capital-sensitivity evidence blocks full-strategy review but
  cannot turn an execution-only Canary into strategy authorization.
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
| Namespaced historical backfill staging and progress | data layer and research-only daemon | data refresh, operator | resumable and replaceable; provenance precedes price checkpoints; source status, trust metrics, source-plan identity, and restart-safe backoff are audited; never authoritative until complete formal-cache commit |
| Extended-history validation status and survivorship audit | history validation workflow | operator, later research review | bind completed progress, fixed backtest config, formal-price hash, and annual current-pool/cache coverage evidence; explicitly does not claim point-in-time correction; never promotes or authorizes execution |
| Research/backtest outputs | research workflow | governance, reports | replace by run; bind manifest; simulated orders use `backtest_orders.csv` only |
| Frozen candidate and sealed inputs | candidate governance | forward observer, activation | immutable identity |
| Active execution strategy/epoch | strategy activation | paper/live order generation | atomic activation/deactivation |
| Paper SQLite ledger | account/paper domain | reports, reconciliation | transactional |
| Broker read-only snapshot/history | broker adapter/history | binding, risk, reports | append/audit; no financial writes |
| Huatai login audit | login-only broker recovery | operator, broker refresh | atomic latest-attempt evidence; excludes account ID and password; grants no order or ledger authority |
| Huatai notice audit | post-login desktop recovery | operator, broker refresh | atomic latest-attempt evidence; excludes notice body and account data; grants no navigation or order authority |
| Capital mode assessment | deployment gate | local operator, reports | derived from fresh broker snapshot and manifest-bound sensitivity evidence; never authorizes submit |
| Live SQLite ledger | broker initialization/fills | risk, settlement | explicit initialization; transactional |
| Orders/RPA tickets | paper/execution pipeline | preview/live control | `orders.csv` and `rpa_orders.csv` are never written by backtest; provenance-bound, quarantine stale batch |
| Rollout approval/confirmation | live control plane | submission gate | signed, expiring, single purpose |
| Auto-live journal/recovery | live control plane | recovery, audit | append-only/hash chained |
| HTML/CSV reports | presentation | operator | derived, never authoritative for submit |

## 6. Canonical workflows

### Research and promotion

0. When the historical sample is insufficient, fill it backward with the
   bounded research-only daemon; persist source-outage backoff across restarts
   and retain the current formal cache until the full requested scope and final
   fetch stages complete with zero failures. Then run
   the explicitly declared fixed strategy in isolated outputs, reject any
   strategy-hash mismatch, require the configured absolute daily cross-section
   and minimum effective trading-day evidence, and record hash-bound validation
   evidence without promotion.
1. Refresh and audit normalized data.
2. Preregister candidate family and evaluation policy.
3. Run the main backtest first, then bind walk-forward, benchmark, cost, factor,
   capital-sensitivity, and selection-bias evidence to that run manifest.
4. Freeze candidate parameters and input fingerprints.
5. Accumulate new-date forward and shadow observations.
6. Audit promotion against the preregistered policy.
7. Activate an exact candidate identity and rotate execution epoch.

### Daily safe operation

1. Refresh market data, then refresh broker read-only evidence as an independent
   fail-closed phase before rendering the daily report and alert state.
2. Reclassify the capital operating mode from current broker and research
   evidence; never reuse stale balances.
3. Update frozen candidate observations without tuning.
4. Recompute strategy/readiness reports.
5. Generate paper orders only when the corresponding gate permits it.
6. Notify on data, strategy, broker, and operational failures.

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
- Recovery-point directory commits never overwrite an existing destination;
  transient filesystem access-denied retries are bounded and fail closed.
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
