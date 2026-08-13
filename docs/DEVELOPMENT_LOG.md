# Development Log

This is the chronological engineering record for the repository. New entries are
added directly below this introduction, newest first. It records verified facts;
it is not a release-marketing document and does not replace generated audit
artifacts.

## 2026-08-13 - Add read-only candidate history decision

**Objective**

Close the P1 visibility gap where an exact frozen candidate could keep appearing
as merely collecting forward data after its authoritative history failed.

**Roadmap phase**

P1 strategy evidence. This change exposes a bound historical decision but does
not permanently quarantine the candidate or advance P2.

**Changes**

- Added `candidate-history-decision`, a read-only audit that binds the frozen
  candidate, authoritative history config, completed status, backtest identity,
  formal-price hash, input binding, fixed thresholds, and result artifacts.
- The decision is `supported`, `rejected`, or `blocked`; incomplete identity
  evidence blocks rather than being interpreted as a strategy failure.
- Bound candidate `d55d124b5800e123` to its existing exact-strategy historical
  validation configuration and updated the roadmap baseline.

**Architecture and safety**

- Owned by strategy governance in `candidate_history.py`; ADR 0033 records the
  durable boundary.
- The command writes one audit CSV only. It does not change the candidate
  manifest or failure history, stop observation, activate a strategy, initialize
  any account, generate orders, or call a broker.
- Broker submission remains disabled and the emergency stop remains active.

**Verification**

- Synthetic exact-pass, performance-rejection, and identity-block tests passed;
  focused candidate/history/documentation regression: 38 passed in 7.90s.
- The real audit was `rejected`, decision SHA-256
  `4f79a6e3a6fcfb929c370f3428755cd12a0e936bc66965f94989ffc08f9eeeb7`.
- It failed total excess, information ratio, held-out excess, walk-forward
  consistency and both bootstrap gates. Candidate and failure-history hashes
  were unchanged.
- Full regression: 1,223 tests and 39 subtests passed in 369.90 seconds.

**Remaining work**

- Add a separate explicit, idempotent permanent rejection action bound to the
  decision hash. Do not couple it to historical validation or daily observation.

## 2026-08-13 - Reject fourth fixed-family history experiment

**Objective**

Run the preregistered fourth and final P1 historical experiment exactly once,
apply its frozen economic and statistical gates, and preserve the result without
promotion.

**Roadmap phase**

P1 strategy evidence remains blocked. All four experiments in the fixed family
are rejected; this result grants no forward, activation, or execution authority.

**Result**

- The fixed CNY 100,000 strategy returned 31.2675% from 2018-07-04 through
  2026-08-07 versus 39.5598% for CSI 300, for total excess of -8.2922%.
- Out-of-sample excess was -1.4618%; information ratio was -0.1311; maximum
  drawdown was -10.7241%.
- Five of 13 non-overlapping walk-forward tests were positive (38.4615%). The
  paired moving-block bootstrap two-sided p-value was 0.93.
- The machine decision was `rejected` because total excess, information ratio,
  out-of-sample excess, walk-forward consistency, positive observed excess, and
  Bonferroni significance all failed their preregistered gates.

**Evidence and safety**

- The run completed with exact experiment registration SHA-256
  `14089a3a08ce82840848b438911eab4ab57a325709593c900fe50fe0bc42f26c`,
  strategy SHA-256 `a7833559e04e85a9931f6e3fb3eee68664ebfcd431e61106583b435e77ed9352`,
  and formal price SHA-256
  `b1eb8529bcd3b948e705bdac27859bf6a21b6f91921c05329f4479f553b84c5c`.
- All 800 requested symbols completed, with 1,965 effective trading days. The
  command exited 0 and generated the final signed status evidence.
- Research output contained neither `orders.csv` nor `rpa_orders.csv`; protected
  live account, live order, and active-strategy artifacts remained absent.
- A second production invocation was intentionally not performed because the
  safety reviewer did not authorize a command that could overwrite frozen
  evidence. Reuse semantics remain covered by focused automated tests.

**Remaining work**

- Do not tune this family on the same history. Define a new research phase and
  independent evidence budget before evaluating another strategy family.

## 2026-08-13 - Preregister fourth fixed-family history experiment

**Objective**

Freeze the fourth and final P1 historical hypothesis before its first evaluation,
with machine-enforced economic and multiple-testing decisions.

**Roadmap phase**

P1 strategy evidence. The experiment is preregistered but has not been run and
therefore has no pass, rejection, forward, activation, or execution authority.

**Changes**

- Added explicit medium-reversal lookback validation and factor-availability
  filtering for stocks without the required 60-session history.
- Preregistered experiment 4/4 as a CNY 100,000, twenty-stock, point-in-time CSI
  300 portfolio with fixed 90% medium-reversal and 10% liquidity weights.
- Bound strategy SHA-256 `a7833559e04e85a9931f6e3fb3eee68664ebfcd431e61106583b435e77ed9352`,
  the existing 800/800 history input, fixed economic thresholds, and Bonferroni
  nominal alpha 0.0125.
- Added a registered-experiment decision engine and ADR 0032. Workflow completion
  is now distinct from experiment `passed` or `rejected` status.

**Architecture and safety**

- The change is owned by research and strategy governance. It does not register a
  forward candidate, initialize any ledger, generate execution orders, or call a
  broker.
- Missing held-out, walk-forward, bootstrap, or economic evidence rejects the
  experiment. A completed rejected result remains reusable audit evidence only.
- Broker submission remains disabled and emergency-stop policy is unchanged.

**Verification**

- Focused registration, factor, decision, history, and compatibility suite:
  37 passed in 1.90s.
- Full regression: 1,220 tests and 39 subtests passed in 201.68 seconds.
- No historical validation output or state database existed after verification.

**Remaining work**

- Commit and push this preregistration before the one allowed historical run.

## 2026-08-13 - Add skipped medium-reversal research capability


**Objective**

Prepare one independently motivated factor for a possible fourth P1 hypothesis
without registering or evaluating that hypothesis on the known history cache.

**Roadmap phase**

P1 strategy evidence. This is research capability only; the experiment family
remains at three rejected trials out of four permitted trials.

**Changes**

- Added an opt-in medium-reversal factor: negative return from 60 trading days
  ago to 5 trading days ago, leaving the latest week outside the signal.
- Added synthetic formula and opt-in tests, ADR 0031, and Chinese research notes
  linked to the pre-evaluation independent rationale.

**Architecture and safety**

- No candidate configuration, experiment registry entry, input binding, forward
  manifest, execution order, account state, or broker setting changed.
- The factor is neutral unless explicitly configured. It cannot promote a
  strategy, initialize a ledger, or call the broker boundary.
- Broker submission remains disabled and the emergency stop policy is unchanged.

**Verification**

- Synthetic medium-reversal formula tests: 2 passed in 0.68s.
- Expanded factor, validation, and governance regression: 54 passed in 1.25s.
- Full regression: 1,209 tests and 39 subtests passed in 272.74 seconds.

**Remaining work**

- In a separate commit, add parameter validation and preregister the fourth
  experiment with exact strategy hash, input binding, thresholds, and alpha
  budget before its first backtest.

## 2026-08-13 - Add cross-experiment selection accounting

**Objective**

Close the P1 credibility gap caused by evaluating distinct strategy hypotheses
over time without one machine-readable cumulative trial count.

**Roadmap phase**

P1 strategy evidence. This adds governance before a possible fourth historical
hypothesis; it does not register or run that hypothesis yet.

**Changes**

- Added a versioned registry for the three already evaluated small-capital,
  benchmark-relative historical experiments, including rejected outcomes.
- Added opt-in history-validation binding for experiment sequence, config and
  strategy identity, complete decision thresholds, and a fixed Bonferroni alpha
  budget of 0.0125 within a four-test family.
- Added ADR 0030 and architecture rules for cross-date experiment selection.

**Architecture and safety**

- The registry belongs to strategy governance and does not alter strategy or
  backtest hashes. Existing completed evidence remains compatible.
- Registry validation occurs before a bound backtest starts and cannot promote a
  candidate, create execution orders, initialize a live ledger, or call a broker.
- Broker submission remains disabled and no live safety switch changed.

**Verification**

- Focused experiment-registry and history-validation tests: 13 passed in 0.99s.
- Expanded governance regression: 22 passed in 1.21s.
- Final focused registry, history, configuration, and governance regression: 24 passed in 1.34s.
- Final full regression: 1,207 tests and 39 subtests passed in 247.48 seconds.

**Remaining work**

- Preregister the fourth and final family experiment in a separate commit before
  its first historical evaluation, or stop the family without consuming it.

## 2026-08-13 - Complete candidate-matching long-history evidence

**Objective**

Execute the first unmet P1 milestone by evaluating the unchanged frozen
small-capital candidate against a preregistered, completed 800-symbol history
input, without granting strategy or broker authority.

**Roadmap phase**

P1 strategy evidence. The candidate-matching historical experiment is complete,
but P1 remains blocked because performance thresholds failed and the forward
window remains 0/60 sessions.

**Changes**

- Added a candidate-specific history configuration that inherits the frozen
  forward candidate, preserving strategy hash
  `1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9`.
- Added required history-input binding for the shared read-only research cache:
  resolved cache path, formal-price SHA-256, progress frame and timestamp,
  800/800 symbol counts, and the 2018-01-01 to 2026-08-07 interval.
- Persisted the input-binding identity in validation status and required it for
  deterministic completed-result reuse. Legacy unbound status remains readable.
- Registered the candidate history configuration as the authoritative history
  evidence source for the local operator. The operator now separates current
  evidence identity from performance against the candidate's existing frozen
  live-guard thresholds.
- Added ADR 0029 and updated architecture and roadmap baseline.

**Architecture and safety**

- Shared history is read-only and preregistered; candidate forward cache,
  candidate history output, research state, default outputs, broker outputs, and
  live ledger retain separate owners.
- The validation cannot promote itself, initialize a ledger, create `orders.csv`
  or `rpa_orders.csv`, or invoke a broker.
- No frozen strategy parameter or threshold changed. Emergency stop remains
  active and broker/service submission remain disabled.

**Verification**

- History config role and exact real-cache input binding passed; binding identity
  is `01b1b6b54575c2d93e1250fbfa65cc55f9e552f180e067bc2f07451b5a061909`.
- Real fixed-candidate validation completed over 800/800 symbols and 1,965
  effective trading days. Strategy return was 25.96%, benchmark return 39.56%,
  cumulative excess -13.60%, information ratio -0.1603, and maximum drawdown
  -11.57%.
- The result failed the existing frozen minimum 2% excess and 0.30 information
  ratio thresholds; drawdown remained within the 25% maximum. Local status now
  reports `authoritative_candidate=match;performance=fail` and `passed=False`.
- Exact completed evidence was deterministically reused in 5.8 seconds without
  changing its generated timestamp or metrics.
- Final P1 and documentation-governance regression: 52 passed in 3.32 seconds.
- First JUnit full regression found one transient Windows ACL failure in the
  directory-retry test; the isolated test then passed in 0.81 seconds. Final
  JUnit full regression: 1,238 tests, zero failures/errors/skips in 393.035
  seconds.
- Candidate history execution processes exited. Protected live ledger, generic
  orders, active execution strategy, and research-output execution order files
  remain absent.

**Remaining work**

- Keep the failed historical result immutable and continue the candidate's
  untuned 60-session forward observation. It does not qualify for P2, P4, or live
  execution from this evidence.
- If research continues, create a newly preregistered successor experiment with
  independent rationale and a new forward window; never tune this frozen
  candidate against the observed historical result.
## 2026-08-13 - Establish an evidence-gated delivery roadmap

**Objective**

Create one authoritative implementation sequence from the current safe local
system to a possible limited live rollout, with explicit capital, evidence, and
authorization gates.

**Roadmap phase**

P0 governance and safe operating baseline. This change defines sequencing only;
it grants no strategy or broker authority.

**Changes**

- Added `docs/ROADMAP.md` with the current baseline and P0-P6 deliverables,
  exit criteria, blockers, priorities, and prohibited authority shortcuts.
- Fixed the strategy capital ceiling at CNY 200,000, retained CNY 1,000 as the
  maximum one-order Canary, and separated forward evidence, small-capital
  executability, broker read-only soak, shadow reconciliation, Canary, and
  limited rollout.
- Added the roadmap to `AGENTS.md`, the development process, README, architecture
  cross-reference, and documentation-governance tests so future changes must
  identify their phase and unmet gate.

**Architecture and safety**

- The architecture remains the source of truth for system boundaries; the
  roadmap owns delivery order and exit criteria. Editing roadmap status cannot
  authorize execution.
- Emergency stop, broker submission, service submission, strategy identity,
  live ledger, account state, and order artifacts are unchanged.

**Verification**

- Documentation-governance test: 4 passed in 0.14 seconds.
- Required roadmap links are present in `AGENTS.md`, README, architecture, and
  development process; all changed documents decode as UTF-8.
- `git diff --check` passed with a line-ending notice only. Protected live
  ledger, generic order, and active execution strategy artifacts remain absent.

**Remaining work**

- Execute work from the earliest unmet phase: preserve P0 while independently
  validating the frozen candidate under P1 and accumulating its 60-session
  forward window.
## 2026-08-13 - Bind and complete isolated long-history validation

**Objective**

Replace stale, ambiguous long-history daemon evidence with a configuration-bound
research workflow, while keeping the frozen strategy and all live capabilities
unchanged.

**Changes**

- Added the explicit `research_history_validation` role and a structured
  `history-config-check` CLI audit for isolated output ownership, research-only
  policy, fixed strategy identity, emergency stop, and disabled submission.
- Required the history daemon and Startup installer to run that audit before
  writing state or installing a launcher. Startup verification now binds the
  current daemon hash, exact config/output paths, and live PID.
- Upgraded daemon status to schema 4 with owned output and a bounded validation
  error tail that survives the retry-wait transition.
- Made current formal frozen-candidate evidence take precedence over legacy
  research-promotion rows in Chinese operator guidance, including candidate ID
  and config filename.
- Separated long-history artifact currency from authoritative-candidate
  applicability. The reproducible 1,965-day legacy experiment is retained but
  labeled `authoritative_candidate=not_applicable` for the current frozen hash.
- Added ADR 0028 and updated the unified architecture.

**Architecture and safety**

- The history workflow remains research-only and cannot use the default
  observation role or Huatai broker role. Only explicit `DryRun` may redirect
  output for isolated tests.
- The old daemon PID 24148 was stopped only after matching its start time, script,
  research config, and output path. No cache or evidence was deleted.
- Direct fixed-strategy validation reused the authoritative 800/800 completed
  cache, produced no generic/live order artifact, and exited 0. The fixed
  strategy hash remained
  `4852068647783a8365d3ed625d0ae3b86a7bbc46ad6712d692cb5db5e0d125bc`.
- No strategy parameter, frozen candidate identity, account state, broker adapter,
  submission switch, or emergency-stop setting changed.

**Verification**

- Direct `history-validate`: completed from local cache with exit code 0 in about
  six minutes and refreshed bound long-history evidence.
- History role, daemon, validation, local operator, and CLI focused regression:
  154 passed in 10.36 seconds.
- Applicability-focused regression after binding the authoritative candidate:
  154 passed in 10.53 seconds.
- Expanded history-governance regression: 161 passed in 10.76 seconds.
- Full regression after the final status-display fix: 1,194 tests and 39
  subtests passed in 186.71 seconds.
- Final history-role, daemon, operator, and documentation regression: 49 passed
  in 14.63 seconds.
- The current daemon published schema-4 `complete` state for 800/800 symbols,
  preserved validation exit code 0, and exited. No unnecessary history Startup
  entry or history-daemon process remains.
- The observation-only Startup entry was restored and verified with exactly one
  current supervisor and one current daemon under `configs/default.yaml`; the
  Huatai configuration is attached only for broker read-only refresh.
- Final broker refresh failed closed because the running Huatai process had no
  identifiable trading root window. The deployment gate therefore keeps
  `broker_read_only_ready=false`, `broker_execution_ready=false`, and
  `ready_to_request_canary=false`; `safe_default=true`.
- Emergency stop remains active, broker and service submission remain disabled,
  and no arming certificate exists. Protected live ledger, generic order, and
  active execution strategy artifacts remain absent.

**Remaining work**

- Continue the formal candidate's untuned 60-session forward observation. Live
  ledger initialization and all broker submission remain blocked.
- Produce an independently governed long-history validation whose fixed strategy
  identity matches the current authoritative candidate before using historical
  evidence in any promotion review. The retained 1,965-day experiment is
  reproducible but not applicable to that candidate.
- Reopen the Huatai trading module on the `funds-and-positions` page and rerun read-only
  refresh when current broker evidence is needed; do not weaken freshness or UI
  identity checks to make the gate pass.
## 2026-08-12 - Enforce one observation configuration role and truth source

**Objective**

Prevent a broker configuration or split output directory from silently becoming
the unattended strategy observer, and make long post-observation cleanup visible
without weakening completion semantics.

**Changes**

- Added explicit `operations.config_role` values for the default orchestrator,
  Huatai broker configuration, and frozen research candidate, with a structured
  `observation-config-check` CLI audit.
- Required the Startup installer, supervisor, daemon, and deployment gate to
  validate orchestrator role, output ownership, and isolated preregistered
  candidate topology before creating operational state.
- Added a `finalizing` daemon phase after a matching current child writes its
  core completion artifact while broker read-only refresh, report rendering, and
  alert delivery are still running.
- Replaced the duplicate `outputs_live` observation Startup route with the sole
  default orchestrator route under `outputs`; the completed daily observation
  was recovered without a duplicate market run.
- Added ADR 0027 and updated the unified architecture.

**Architecture and safety**

- Strategy orchestration, broker-live settings, and frozen candidate research are
  now explicit configuration capabilities. Inherited fields or filenames cannot
  grant the orchestrator role.
- `finalizing` is informational only. It neither stops the child nor records
  success early; final exit remains authoritative and failures still retry under
  existing policy.
- The migrated runtime performed observation, broker read-only queries, form
  previews, report generation, and email delivery only. No broker submission or
  live-ledger initialization was enabled.
- Emergency stop remains true and broker/service submission remain false.
  Protected live artifacts are audited again after the full regression below.

**Verification**

- Configuration-role, deployment, CLI, scheduler, and documentation focused
  regression: 161 passed in 18.15 seconds.
- Observation daemon and local-operator focused regression after phase handling:
  42 passed in 13.45 seconds; expanded role, scheduler, deployment, operator,
  and documentation regression: 208 passed in 25.22 seconds.
- Real default observation completed with exit code 0; one frozen candidate was
  observed at 0/60 post-freeze sessions, Huatai read-only refresh and email
  delivery completed, and no `outputs_live` observation process remained.
- Full regression: 1,188 tests and 39 subtests passed in 175.65 seconds.
- Final runtime audit found exactly one supervisor plus one daemon, both under
  the default orchestrator output; zero observation processes used `outputs_live`.
  The current-hash Huatai read-only refresh restored
  `broker_read_only_ready=true` with `submit_invoked=false`. Protected live
  ledger, generic orders, and active execution strategy artifacts remain absent.
- `git diff --check`: passed; line-ending conversion notices only.

**Remaining work**

- Accumulate 60 genuine post-freeze sessions. Strategy promotion, live-ledger
  initialization, and any broker submission remain blocked.

## 2026-08-12 - Require an isolated unknown-outcome recovery drill before Canary

**Objective**

Add current, configuration-bound proof that an interrupted submission cannot be
blindly retried, without invoking the broker or changing protected live state.

**Changes**

- Added `auto-live-recovery-drill`, which creates a signed synthetic submit
  request and all intermediate recovery state in an auto-deleted temporary
  directory. Only the final audit and summary are atomically published.
- The drill proves request-bound unknown-result reconstruction, duplicate
  suppression from the persisted intent, broker-order-ID recovery through a
  synthetic query, active-order blocking, and release only after terminal status.
- Added `submission_recovery_drill` as a mandatory Canary deployment check with
  schema, current `auto_live` configuration hash, 24-hour freshness, no-broker,
  and temporary-workspace-removal requirements.
- Added ADR 0026 and updated architecture ownership, broker-boundary rules, and
  the guarded live workflow.

**Architecture and safety**

- The drill never executes the command adapter, creates a live ledger, emits an
  order artifact, activates a strategy, changes safety switches, or grants
  execution authority. Missing HMAC, stale or mismatched identity, retained
  temporary state, or a broker-invoked marker fails closed.
- Real-config evidence passed 8/8 checks with
  `broker_adapter_invoked=false` and `isolated_workspace_removed=true`.
- The configuration change invalidated the old replay hash as intended. A real
  Huatai read-only refresh then restored `certified_read_only_live` for the
  current hash; account, buy/sell form readback, and empty order/trade/cancel
  probes succeeded with `submit_invoked=false`.
- Final deployment state remains blocked: `strategy_ready=false`,
  `broker_execution_ready=false`, and `ready_to_request_canary=false`, while
  `broker_read_only_ready=true` and `safe_default=true`. The full lifecycle is
  not certified, the forward window is incomplete, capital evidence is not
  compatible/current, and the isolated live ledger is not initialized.
- Emergency stop remains true; broker and service submission remain false.
  `data/huatai_live_state.sqlite3`, `data/orders.csv`, and
  `data/active_execution_strategy.json` remain absent.

**Verification**

- Recovery drill, deployment gate, CLI, and documentation focused regression:
  146 passed.
- Final full regression: 1,180 tests and 39 subtests passed in 165.27 seconds.
- Real `auto-live-recovery-drill`: 8/8 passed without broker invocation.
- Real `broker-refresh`: completed with exit code 0 and explicitly without
  broker submission; final deployment gate regenerated against current evidence.
- `git diff --check`: passed after documentation finalization.

**Remaining work**

- Accumulate the immutable candidate's 60 genuinely post-freeze trading days;
  do not tune it from forward observations or promote it early.
- Complete a separately authorized real submit/query/cancel/fill lifecycle
  certification before requesting a one-order Canary. This offline drill cannot
  substitute for broker-specific lifecycle evidence.
- Keep live-ledger initialization and any order submission blocked until their
  separate evidence and explicit authorizations exist.

## 2026-08-12 - Route and observe the frozen defensive small-capital candidate

**Objective**

Replace the invalid unattended candidate registry with the independently frozen
CNY 100,000 defensive-trend candidate and begin its forward-only observation
without creating executable orders or inheriting historical authority.

**Changes**

- Registered only
  `configs/hs300_small_capital_defensive_trend_v1_forward.yaml` in the default
  forward-observation route. Older candidate files and evidence remain untouched
  for audit but are no longer scheduled.
- Copied the complete predecessor cache into the candidate's isolated namespace
  after commit `4874061`, then preregistered and sealed candidate
  `d55d124b5800e123`. Its fixed strategy hash remains
  `1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9`;
  freeze, complete-market, raw-price, known-data, and benchmark ends are all
  2026-08-12.
- Preserved frozen input evidence for 5,555 stocks, 1,320,067 prices, 65,394
  fundamentals, 874 benchmark rows, and 13,200 membership rows. The manifest
  records `optimized_on_current_cache_at_freeze=false`,
  `promotion_eligible_at_freeze=false`, and
  `historical_selection_evidence_allowed=false`.
- Distinguished fully evidenced retired-member history increments from
  unexplained fetch failures. Acceptance requires a unique history-stage
  failure, retained prices, bound provenance, historical membership, and absence
  from both the current stock catalog and latest membership snapshot.
- Added ADR 0025 and updated the architecture data-trust rule.

**Architecture and safety**

- The real candidate cache contains two accepted retired-history failures:
  600837 has prices through 2025-02-05 and 601989 through 2025-08-12; both are
  historical CSI 300 members, absent from the current stock catalog and latest
  membership snapshot, and retain bound BaoStock provenance. No general failure
  threshold was relaxed.
- Cached `forward-daily` completed with no paper or broker orders. The candidate
  is `waiting_for_post_freeze_market_data`, has 0/60 observed trading days, one
  pending 2026-08-12 signal for earliest simulated execution on 2026-08-13, and
  requires that day's close before its first attribution.
- Deployment remains blocked: `strategy_ready=false`,
  `ready_to_request_canary=false`, and the execution strategy identity does not
  match the research candidate. Emergency stop is true; broker and service
  submission are false.
- `data/huatai_live_state.sqlite3`, `data/orders.csv`, and
  `data/active_execution_strategy.json` remain absent.

**Verification**

- Candidate identity/preflight/deployment focused tests: 68 passed.
- Data-scope, signal, membership, CLI, and strategy-governance expanded
  regression: 255 passed.
- An initial full regression passed 1,173 tests and 39 subtests. After fixing
  empty-signal CSV dtype idempotency, the final full regression passed 1,174
  tests and 39 subtests in 166.68 seconds.
- Real cached `forward-daily`: completed without paper or broker orders;
  immutable candidate observed and deployment gate regenerated.
- Same-day immutable shadow rerun: completed without conflict or evidence
  replacement after normalizing signal identity dtypes.
- Full observation-only runner: Huatai account snapshot and buy/sell form
  readback passed with `submit_invoked=false`; orders, trades, and cancels were
  empty and verified. The daemon recovered today as `completed` with exit code 0.
- `git diff --check`: passed.

**Remaining work**

- Accumulate 60 genuinely post-freeze trading sessions without changing this
  fingerprint. Do not interpret the pending freeze-date signal or historical
  cache as forward performance.
- Refresh broker read-only evidence separately when needed. Live ledger
  initialization, Canary submission, and broker submission remain blocked.

## 2026-08-12 - Fix the next small-capital forward hypothesis before evaluation

**Objective**

Record one implementable and independently observable CNY 100,000 strategy
hypothesis before copying market data or seeing any result for its fingerprint.

**Pre-evaluation registration**

- Added configs/hs300_small_capital_defensive_trend_v1_forward.yaml with
  point-in-time CSI 300 membership, twenty equal target positions, 5% stock and
  25% industry caps, monthly rebalancing, and schema-2 capital-aware board-lot
  planning.
- Fixed strategy SHA-256 is
  1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9.
- Factor weights were fixed from an external structural hypothesis: 55% combined
  low-volatility/downside/drawdown, 35% combined momentum/trend, and 10%
  liquidity. No current-cache result was inspected to select them.
- ADR 0024 records the rationale and rejects retuning prior failed candidates.
  Rank buffering was removed before evaluation because validation correctly
  rejects combining it with capital-aware planning.

**Broker read-only stability**

- Replaced minimized-window child-control discovery with restoration of the
  unique minimized UIA root owned by the pinned Huatai executable, after
  verifying exactly one pinned market process and one same-directory trading
  process. Success requires the root to become visible and non-minimized.
- Real window test changed IsIconic from 1 to 0 and returned verified
  restoration. A full broker refresh then captured the live read-only account,
  verified buy and sell form readback with submit_invoked=false, and verified
  empty orders, trades, and cancels before reporting completion without broker
  submission.

**Architecture and safety**

- No candidate cache has been copied and no backtest, observation, or
  preregistration has run for the new hash at this commit boundary. The next
  commit must preserve this ordering before generating the immutable manifest.
- The candidate is research-only with automatic live and broker submit disabled
  and emergency stop enabled. Broker refresh did not initialize the live ledger.

**Verification**

- New strategy config validation: no issues; fixed hash recomputed exactly.
- Huatai client, snapshot, and CLI focused regression: 135 passed.
- Real read-only refresh: account snapshot ready, positions 0, form previews
  verified, three monitor probes empty and verified, no broker submission.

**Remaining work**

- Commit and push this preregistration record, then copy the complete predecessor
  cache into the isolated namespace, generate and seal the immutable candidate,
  switch unattended observation to it, and accumulate new post-freeze sessions.
## 2026-08-12 - Separate terminal strategy blocks from read-only operations

**Objective**

Stop deterministic invalid-candidate retries while preserving unattended broker
read-only refresh, reporting, alerting, and explicit fail-closed operator state.

**Changes**

- Added runner exit code 20 and status `strategy_blocked` for frozen-candidate
  preflight failures in both ordinary native-process and PowerShell exception
  paths.
- Kept broker read-only refresh, report rendering, and durable alert dispatch
  after a strategy block.
- Made the observation daemon treat exit 20 as terminal for the trading day and
  recover that state idempotently from the runner artifact after a restart.
- Exposed `strategy_blocked` separately from daemon readiness in local status.
- Added ADR 0023 and focused tests for status aggregation, script ordering,
  no-retry behavior, and restart recovery.

**Architecture and safety**

- Strategy completion and freshness remain failed. No candidate was repaired,
  promoted, or activated.
- Reinstalled and restarted the current-user observation supervisor with the
  corrected scripts. Supervisor PID 9732 and daemon PID 17088 were verified
  alive; broker read-only refresh and email alerting remain enabled.
- A real run failed candidate preflight in 1.425 seconds with exit code 20. The
  daemon recovered it as `strategy_blocked_no_retry` with an empty
  `next_retry_at`.
- The independent broker refresh failed because the Huatai window was minimized;
  a critical email alert was delivered. An earlier same-session read-only
  refresh had authenticated and produced the account snapshot, so this is a
  current window-availability failure rather than submission activity.
- Local status reports the daemon ready, scheduled observation
  `strategy_blocked`, and overall `blocked`. Configured emergency stop is
  true, broker and service submission are false, and live ledger, order, and
  active-strategy artifacts remain absent.

**Verification**

- Observation scheduling focused regression: 38 passed; daemon recovery focused
  regression: 20 passed.
- Full regression: 1,166 tests and 39 subtests passed in 334.12 seconds.
- Real runner/daemon verification: status `strategy_blocked`, exit code 20,
  daemon message `strategy_blocked_no_retry`, no next retry.
- `git diff --check`: passed; only expected PowerShell line-ending notices.

**Remaining work**

- Restore and keep the Huatai funds/positions window visible for the next
  read-only refresh. Create a new immutable preregistered candidate cycle only
  after independent data and forward evidence are available; do not mutate the
  three invalid registrations. Live submission remains blocked.
## 2026-08-12 - Install unattended read-only observation and bind running status to its child

**Objective**

Make forward evidence and broker read-only refresh run locally without AI
operation, while keeping strategy execution halted and broker submission off.

**Changes**

- Installed current-user Startup entry
  `AshareQuantDailyObservation.cmd` for weekday 18:30 observation with three
  bounded attempts, a two-hour child timeout, email alerting, and broker
  read-only refresh.
- Started and verified the hidden observation-only supervisor and daemon. The
  live process chain was supervisor PID 23148, daemon PID 10608, daily runner PID
  17136, and `aq forward-daily --refresh --quiet` PID 13916.
- Changed observation status aggregation so daemon `running` counts as an active
  task only when the recorded child PID is alive. A dead child now fails the
  daemon audit instead of appearing scheduled or healthy.
- Separated the runner's owned status/log directory from the global market-data
  coordination lock. The daemon now passes `outputs_live` explicitly while the
  shared `outputs/daily_observation.lock.json` continues to serialize cache work.
- Added a local forward-candidate identity preflight before any candidate data
  store or network access. Invalid manifests, hashes, and freeze boundaries now
  fail immediately instead of refreshing evidence that cannot be inherited.
- Made history-backfill `DryRun` deterministic by bypassing observation-window
  deferral only in dry-run mode. Real backfill still yields to observation locks
  and quiet periods.

**Architecture and safety**

- This is orchestration and monitoring work. It does not create paper or live
  orders, initialize a live ledger, activate a strategy, or enable submission.
- Real status verification showed the observation task as `running`, all five
  observation audits passing, and the overall operator status still `blocked`
  by strategy and stale broker evidence.
- The first unattended run fetched two missing symbols and completed the
  forward refresh, then failed closed because all three configured candidate
  hashes no longer matched preregistration. Broker read-only refresh separately
  failed because Huatai was not authenticated on the funds/positions page. The
  critical email alert was delivered.
- After preflight hardening, the same invalid candidate set failed in 0.727
  seconds before market access. The old supervisor stopped cleanly and the
  corrected supervisor restarted as PID 27064 with its prior retry state intact.
- Automatic live emergency stop remains active; broker and service submission
  remain disabled.

**Verification**

- Observation/local-operator focused regression: 44 passed; expanded scheduler,
  output-ownership, history, and local-operator regression: 72 passed.
- Forward preflight and full CLI regression: 104 passed.
- History daemon regression: 9 passed.
- An intermediate full regression exposed the live-lock-dependent history dry
  run and otherwise passed 1,156 tests; after all CLI and scheduler changes, the
  final full regression passed 1,162 tests and 39 subtests in 318.14 seconds.

**Remaining work**

- Remove or replace the three stale candidate registrations only through a new
  preregistered evidence cycle; do not mutate their old manifests. Refresh the
  Huatai read-only snapshot after login. Strategy activation remains blocked
  until independent forward and point-in-time evidence passes.
## 2026-08-12 - Reject the preregistered concentrated history test

**Objective**

Run the one fixed extended-history evaluation registered in commit `8f19a8a`
and preserve its result without retuning the strategy on the same cache.

**Evidence**

- `history-validate` completed on 1,965 effective trading days from 2018-01-02
  through 2026-08-07. Strategy return was 33.33218% versus CSI 300 39.55975%,
  for cumulative excess return -6.22757% and information ratio -0.03457.
- Maximum drawdown was -46.17461%, slightly worse than the benchmark's -45.60262%.
  Fixed-parameter walk-forward positive ratio was 6/13, average test excess was
  -0.49816%, and the worst test fold was -16.47914%.
- Average cash ratio was 51.60% and median cash ratio was 30.01%. The run made
  356 trades across 127 symbols with two market-state rejects. Concentration
  improved implementation versus the 100-stock target, but did not deliver
  stable excess return and remained underinvested in parts of the sample.
- The survivorship audit remained high risk: no point-in-time full-market
  universe, delisted-security history, or historical risk-warning state exists.
  The result therefore serves only as a rejection test.
- Focused configuration, history-validation, documentation, sensitivity, and
  validation tests passed: 51. Full regression passed: 1,155 tests and 39
  subtests in 160.65 seconds.

**Decision and safety**

- Marked this exact fingerprint rejected and current-cache evaluated. No weight,
  lookback, holding-count, or timing change will be tested on this cache.
- No candidate was created or activated. `data/orders.csv`,
  `data/active_execution_strategy.json`, and `data/huatai_live_state.sqlite3`
  remain absent. Broker submission remains disabled and emergency stop remains
  active.

**Remaining work**

- Do not optimize another candidate on the same known sample. Accumulate new
  forward sessions or obtain authoritative point-in-time full-market history
  before opening a new strategy research cycle.
## 2026-08-12 - Preregister one concentrated CNY 100,000 history test

**Objective**

Freeze a materially smaller-position-count small-account hypothesis before its
first evaluation on the existing 2018-2026 extended-history cache.

**Pre-evaluation registration**

- Added `configs/small_capital_100k_v5_extended_history.yaml`: ten equal-weight
  positions, 10% stock cap, 30% industry cap, quarterly rebalance, fixed
  momentum/low-risk/trend/liquidity weights, and board-lot planning schema 2.
- Fixed strategy SHA-256 is
  `95310e6619d742d340f03e2ebc3a667e6b4408ca1652d983aaf55bc7f450b406`;
  backtest configuration SHA-256 is
  `2945b62605620ec414e1f27bd90c65c959b438cb4c31ff2c50a49f4e2d7819ab`.
- The inherited cache currently contains 2,007,871 rows for 1,503 symbols from
  2018-01-02 through 2026-08-07. It will be read without a fetch or refresh.
- Configuration validation passed. No historical evaluation of this exact
  fingerprint had been run when this entry was written.

**Architecture and safety**

- ADR 0022 limits this to one rejection-oriented diagnostic. The cache has
  current-universe survivorship bias, so even a favorable result cannot authorize
  candidate admission, activation, paper/live orders, a live ledger, or broker
  submission.
- Output and SQLite paths are isolated. Automatic live is disabled, broker submit
  is disabled, and emergency stop remains active.

**Remaining work**

- Commit and push this preregistration before running `history-validate` once.
- Record the fixed result without adjusting this strategy on the same cache.
## 2026-08-12 - Enforce the CNY 200,000 system capital boundary

**Objective**

Make the user's CNY 200,000 maximum strategy size an application invariant
across research, portfolio construction, RPA review, and automatic-live risk.

**Changes**

- Added an unconditional CNY 200,000 system ceiling; an optional strategy-family
  budget can only make the limit stricter.
- Added fail-closed checks for backtest cash, sensitivity levels, direct variant
  construction, RPA limits, automatic-live buy limits, rollout stages, and gross
  turnover above twice the effective capital limit.
- Capped deployable equity in backtest, paper, shadow, and broker-preview paths,
  so a larger broker account or retained profit cannot expand strategy exposure.
- Runtime automatic-live risk limits clamp unsafe configured values before a
  submission audit can pass.
- Added capital policy to backtest identity, migrated the default to CNY 100,000,
  migrated every repository config to at most CNY 200,000, and rebound isolated
  extended-history validation to the new fixed strategy hash.
- Added ADR 0021 and a repository-wide configuration regression test.

**Evidence**

- All 58 loadable repository configurations passed the capital scan. Maximum
  backtest cash and maximum sensitivity level are both exactly CNY 200,000.
- Current CNY 100,000 default run manifest matches the current configuration.
  Total return was 26.51823% versus CSI 300 22.16332%, cumulative excess 4.35491%,
  and maximum drawdown -14.30887%. It ended with eight positions, zero odd lots,
  and zero cash-constrained buy rejects.
- The same strategy still failed stability: 2024 and 2025 excess returns were
  -13.13058% and -9.95370%; fixed-parameter walk-forward positive ratio was 1/3,
  average test excess -8.21524%, and worst fold -21.40835%.
- Four-level capital sensitivity completed on identical current market data:
  CNY 30,000, 50,000, 100,000, and 200,000. Only CNY 200,000 met the existing
  implementation-fidelity criteria; average cash ratios were 98.14%, 93.51%,
  79.86%, and 62.44%, respectively. This confirms the 100-stock default is not
  an acceptable small-account live strategy despite positive aggregate return.
- Focused capital, execution-risk, manifest, portfolio, paper, broker-preview,
  and backtest tests passed: 101 tests. The final expanded capital suite passed 74 tests.
- Full regression passed: 1153 tests and 39 subtests in 161.38 seconds.

**Safety**

- No candidate was activated, no broker order was submitted, and no live ledger
  was initialized. Current strategy evidence remains blocked for live use.
- The Huatai Canary configuration remains emergency-stopped with broker submit
  disabled. Gross rotation capacity does not raise deployable capital or buy
  exposure above CNY 200,000.

**Remaining work**

- Design the next small-account candidate around a materially smaller position
  count and preregister it before independent forward observation; do not tune
  the current default on the same cache.
- Regenerate longer-history evidence under the new fixed CNY 100,000 identity
  before citing it. Continue broker read-only evidence refresh independently of
  strategy promotion.

## 2026-08-12 - Version rebalance execution and reject non-robust small-capital evidence

**Objective**

Correct board-lot rebalance semantics, bind the revision to backtest identity,
and re-evaluate the strongest small-capital research result against both its
original partial cache and the complete 395-member CSI 300 reconstruction.

**Changes**

- Rebalance generation now emits deterministic sells before buys, exits zero
  targets exactly subject to T+1 sellability, and rounds nonzero-target partial
  sells down to board lots.
- Added `trading.rebalance_execution_schema_version=2` to the default backtest
  identity and reject explicitly unsupported execution versions.
- Extended small-capital admission with fail-closed order, rejection, and final
  position evidence; residual odd lots must be zero and cash-constrained buy
  rejects must not exceed 5%.
- Added the fixed, research-only complete-member replication config and ADR 0020.
  V2 and V4 are explicitly marked rejected; neither can authorize execution.

**Evidence**

- The existing isolated historical reconstruction was reused without mutation:
  395 membership symbols all have prices, with 341,390 rows from 2023-01-03
  through 2026-08-10; all point-in-time membership quality checks pass.
- Execution-schema-v2 V2 rerun: total return 50.82926%, benchmark 22.16332%,
  excess 28.66594%, maximum drawdown -30.72925%, 0 odd lots, and 1/102
  cash-constrained buys. It still failed annual consistency: 2024 excess was
  -13.18183%; fixed-parameter walk-forward positive ratio was 1/3, average
  excess -0.81002%, and worst excess -8.52689%.
- One fixed complete-member V4 replication: total return 11.16948%, benchmark
  22.36078%, excess -11.19130%, maximum drawdown -31.20262%, 0 odd lots, and
  8/89 cash-constrained buys. Positive annual excess ratio was 1/4; the worst
  walk-forward fold was -8.82586%.
- Small-capital admission blocked both candidates. Aggregate historical
  outperformance alone did not override current-cache optimization, drawdown,
  annual, walk-forward, cost-evidence, capital-fidelity, or execution gates.
- Focused validation, rule, manifest, and readiness regression: 53 passed.
- Full regression: 1144 passed and 39 subtests passed in 161.43 seconds.

**Safety**

- Work was limited to research backtests and evidence auditing. No strategy was
  activated, no broker order was submitted, and no live ledger was initialized.
- Broker submission remains disabled and these rejected candidates remain
  ineligible for forward preregistration or execution.

**Remaining work**

- Do not tune V2 or V4 further on the same period. A next challenger must be
  preregistered before independent future observation and retain the 200,000
  yuan hard capital ceiling.
- Refresh capital sensitivity and cost stress only for a future preregistered
  challenger that first clears aggregate, annual, and walk-forward criteria.
## 2026-08-12 - Preserve benchmark targets through small-capital lot planning

**Objective**

Make small-capital simulations faithfully discretize the portfolio weights
produced by benchmark anchors and other upstream weighting policies, then perform
one fixed 100,000 yuan CSI 300 enhanced-strategy evaluation without tuning to the
result.

**Changes**

- Added optional upstream target weights to the capital-aware lot constructor and
  passed them through backtest, walk-forward, paper, and broker-preview workflows.
- Added planning schema version 2 with explicit respect_target_weights behavior:
  only positive upstream targets are admitted, and additional board lots are
  added one at a time when they reduce absolute target-value error.
- Preserved schema version 1 behavior and added compatibility, target-priority,
  allocation, missing-target, schema, and non-numeric failure tests.
- Added research-only configs/small_capital_100k_v3.yaml using point-in-time CSI
  300 membership, a 70% liquidity reference core, a 30% defensive factor
  satellite, eight to ten positions, and full-exposure benchmark-relative
  behavior.
- Added ADR 0019 and updated the account/execution architecture responsibility
  for versioned preservation of upstream portfolio intent.

**Architecture and safety**

- The planning change affects research, paper, and read-only preview target
  construction only. It does not enable broker submission, initialize a live
  ledger, activate a strategy, or create generic execution orders.
- Schema v2 fails closed without an upstream target. Schema v1 ignores the new
  optional input so existing v1/v2 evidence remains replayable.
- The v3 config remains research-only, optimized on the current cache, auto-live
  disabled, broker submit disabled, and emergency stop active.
- The first fixed v3 evaluation was rejected. It cannot be preregistered,
  activated, or used as execution authority.

**Verification**

- Focused planning, validation, reference-anchor, and governance tests:
  40 passed in 0.89s.
- Cross-module backtest, paper, walk-forward, broker-preview, sensitivity,
  readiness, and validation tests: 83 passed in 4.23s.
- Authoritative full regression in the original machine-bound workspace:
  1135 passed, 39 subtests passed in 161.30s.
- Schema v1 compatibility rerun exactly reproduced the recorded v2 result:
  total return 47.06807%, benchmark return 22.16332%, and excess return 24.90475%.
- The single v3 evaluation covered 750 trading days from 2023-07-06 through
  2026-08-07. It returned -12.40564% versus benchmark +22.16332%, for
  -34.56896% excess, with -33.04888% maximum drawdown.
- V3 annual excess was negative in all four calendar years. Walk-forward positive
  ratio was 1/3 and the worst fold excess was -8.77460%.
- CSI 300 snapshot structure passed: 44 snapshots, exactly 300 unique constituents
  each, no future update dates, and acceptable staleness. Historical-member price
  coverage failed at 261/395 (66.0759%), so point-in-time membership readiness
  remained false.
- Small-capital readiness correctly blocked v3 on current-cache optimization,
  missing capital/cost evidence, drawdown, annual consistency, and walk-forward
  checks.
- Four-level capital sensitivity was intentionally not run for v3 because the
  base strategy and prerequisite historical-member coverage had already failed;
  running it could not make the experiment admissible.

**Remaining work**

- Do not tune or promote v3. Complete historical CSI 300 constituent price
  coverage before evaluating another benchmark-member strategy.
- Retain v2 as research-only despite its positive aggregate backtest: it still
  fails intended-capital fidelity and walk-forward consistency gates.
- A future small-capital challenger must be preregistered against independent
  data and evaluated once; no backtest result guarantees benchmark
  outperformance.
## 2026-08-12 - Stabilize lot planning and reject isolated v2

**Objective**

Remove direct cost-scenario influence from small-capital target construction,
bind planning algorithm revisions to evidence identity, and evaluate one isolated
v2 without weakening admission thresholds.

**Changes**

- Added optional fixed planning commission/fees and downward planning-equity steps
  to `capital_aware.py`; actual fills still use real scenario costs and equity.
- Fixed selected-lot weight encoding to use actual investable equity, preserving
  intended quantities when planning equity is rounded down.
- Added `planning_schema_version=1` validation and identity through the v2 strategy
  configuration so stale sensitivity runs cannot be reused after algorithm changes.
- Added the fail-closed `optimized_on_current_cache=false` admission check.
- Added isolated `small_capital_100k_v2.yaml`, focused tests, ADR 0018, and
  documented the evaluated rejection.

**Architecture and safety**

- Legacy v1 behavior remains unchanged when planning options are absent; its
  return, drawdown, turnover, benchmark, and excess metrics reproduced exactly.
- V2 is research-only, marked optimized on the current cache, and remains blocked
  from candidate preregistration. No active strategy, live ledger, broker call,
  or execution order is created.
- Planning schema is strategy/backtest identity. Unsupported versions fail config
  validation instead of silently consuming incompatible artifacts.

**Verification**

- Capital-aware, config, readiness, sensitivity, stress, warmup, and candidate
  focused regression: `67 passed in 10.91s`.
- V1 compatibility rerun reproduced total return `36.36508%`, maximum drawdown
  `-32.86025%`, annualized turnover `4.89533`, benchmark return `22.16332%`, and
  excess return `14.20176%`.
- V2 base return was `47.06807%`, excess return `24.90475%`, maximum drawdown
  `-30.58596%`, and annualized turnover `4.25174`.
- V2 high-cost return was `46.40267%`; symmetric cost-path deviation improved to
  `0.66540` percentage points and passed the two-point limit.
- V2 failed forward evidence: fixed walk-forward positive ratio `33.33%`, worst
  fold excess `-8.53%`, and current-cache optimization remains true.
- Four fresh schema-bound capital runs found only CNY 200,000 passed; CNY 100,000
  trailed that reference by `47.13` percentage points and failed capital fidelity.
- Extended focused regression including documentation governance: `70 passed in 11.18s`.
- Full regression after fail-closed missing-schema validation: `1127 passed, 39 subtests passed in 311.27s`.
- Final audit refreshed v1 and v2 admission artifacts; both remain current-cache
  research only with broker submission disabled and emergency stop active. No
  active strategy, live ledger, candidate, generic/RPA/live/auto order file exists.

**Remaining work**

- Do not preregister v2 or continue same-cache parameter search. Design the next
  version before acquiring/separating evaluation data, then preserve that holdout.
- Independent live-readiness, broker, rollout, and confirmation gates remain
  mandatory even for a future strategy that passes research admission.

## 2026-08-12 - Gate small-capital forward preregistration

**Objective**

Move the capped CNY 100,000 research family toward controlled operation by making
its research-to-forward admission criteria explicit and fail closed.

**Changes**

- Added `small_capital_readiness.py` and the `small-capital-readiness` CLI command
  to audit capital fidelity, current identities, drawdown, annual consistency,
  fixed-parameter walk-forward evidence, and cost-path stability.
- Added symmetric total-return cost-path sensitivity, so a large favorable change
  under higher costs fails instead of being misreported as robustness.
- Integrated the audit into the research workflow and candidate preregistration;
  enabled small-capital configurations cannot bypass failed evidence by changing
  experiment status.
- Added focused positive and fail-closed tests plus ADR 0017, architecture, and
  operating-document updates.

**Architecture and safety**

- The new artifact is research admission evidence only. A complete pass allows
  forward-only preregistration, not activation, orders, live-ledger initialization,
  or broker submission.
- The current configuration remains `structural_backtest_only`; `auto_live.enabled`
  and `allow_broker_submit` remain false, and `emergency_stop` remains true.
- No broker adapter, account state, active strategy, candidate manifest, order,
  or protected live artifact is created or modified by this command.

**Verification**

- `python -m pytest tests/test_small_capital_readiness.py tests/test_candidate.py tests/test_capital_sensitivity.py -q`:
  `34 passed in 4.20s`.
- `python -m ashare_quant --config configs/small_capital_100k.yaml small-capital-readiness`:
  generated a current audit; capital boundary, isolation, backtest identity,
  capital fidelity, cost identity, and high-cost excess passed.
- The current admission result is blocked by `32.86%` maximum drawdown, `50%`
  positive annual excess ratio, `-11.43%` worst annual excess, `66.67%`
  positive walk-forward ratio, `-10.24%` worst fold excess, and `3.79` percentage
  points of symmetric cost-path deviation.
- Focused suite including documentation governance: `37 passed in 4.15s`.
- Full regression: `1122 passed, 39 subtests passed in 158.09s`.
- Final safety audit confirmed research-only status, no active execution strategy,
  no live ledger, candidate, generic/RPA/live/auto order file, broker submission
  disabled, and emergency stop active.

**Remaining work**

- Redesign the small-capital strategy as a new research version without relaxing
  these thresholds merely to pass, then rerun the complete fixed evidence set.
- Only after admission passes may a new immutable candidate begin its own untuned
  forward observation; independent live-readiness and broker gates still apply.

## 2026-08-12 - Add a hard-capped CNY 100,000 strategy study

**Objective**

Design and backtest an A-share strategy that is executable around CNY 100,000
and refuses any configured research capital above CNY 200,000.

**Changes**

- Added an opt-in `capital_budget` validator covering intended capital,
  backtest initial cash, and every capital-sensitivity level.
- Added `configs/small_capital_100k.yaml`: quarterly rebalance, eight-to-ten
  integer-lot targets, five-percent reserve, 16% stock cap, 30% industry cap,
  and a fixed momentum/low-volatility/low-drawdown/liquidity factor mix.
- Added isolated 30,000/50,000/100,000/200,000 yuan sensitivity runs and
  documented the design, evidence, and promotion boundary in ADR 0016.

**Architecture and safety**

- The configuration is research-only and cannot activate a strategy, generate
  generic/live/RPA orders, initialize a live ledger, or access a broker.
  `auto_live.enabled=false`, `allow_broker_submit=false`, and the emergency
  stop remains active.
- Existing frozen candidates and their forward evidence were not changed.
- Capital levels above CNY 200,000 fail before sensitivity backtests begin.

**Verification**

- Focused validation, capital-aware, sensitivity, and documentation tests:
  `35 passed in 1.16s`.
- Full regression: `1119 passed, 39 subtests passed in 180.76s`.
- The CNY 100,000 backtest covered 750 trading days from 2023-07-06 through
  2026-08-07: total return 36.37%, benchmark return 22.16%, excess return
  14.20%, maximum drawdown -32.86%, annualized turnover 4.90, and average
  deployed cash 25.98%.
- Fixed walk-forward evaluation produced three non-overlapping folds, 66.67%
  positive test excess, 3.97% average test excess, and -10.24% worst-fold
  excess.
- Four-level sensitivity used one current market-data fingerprint. CNY 100,000
  was the first passing level; CNY 30,000 and 50,000 failed return-fidelity
  thresholds. The CNY 30,000 return of 101.54% is classified as concentrated
  board-lot path dependence, not superior strategy evidence.
- Safety audit found no active execution strategy, live ledger, generic order,
  RPA order, or auto-live order artifact.

**Remaining work**

- A separate high-cost stress command did not start because the platform
  permission review disconnected; no stress result is claimed.
- Maximum drawdown remains too high and one walk-forward fold had negative
  excess. Keep the strategy research-only and accumulate a new untuned forward
  window before any promotion review.


## 2026-08-11 - Dismiss Huatai post-login notices safely

**Objective**

Remove the normal Huatai navigation announcement after login without changing
notice preferences or granting broader desktop authority.

**Changes**

- Added `huatai_notice.py` as an isolated broker-boundary capability with an
  atomic redacted `huatai_notice_audit.csv`.
- Bound dismissal to one SHA-256-pinned Huatai process pair and exactly one
  visible `xiadan.exe` `#32770` dialog containing the navigation label, both
  notice-preference labels, and one `CCustomTabCtrl`.
- The driver sends one `WM_CLOSE` to the identified dialog and confirms it is
  no longer visible. It never clicks either preference, uses generic keys,
  discovers a generic close button, or uses screen coordinates.
- Integrated dismissal after a successful account snapshot and before desktop
  Canary preview. A blocked or ambiguous notice skips Canary desktop navigation
  for that refresh.
- Enabled the capability in both Huatai configurations, added ADR 0015, and
  updated broker-boundary architecture and artifact ownership.

**Architecture and safety**

- Notice dismissal cannot access credentials, orders, ledger initialization,
  account import, emergency-stop state, or broker submission.
- Real audit reported `detected=true`, `close_count=1`, and
  `dismissal_confirmed=true`.
- The subsequent buy/sell previews and order/trade/cancel empty monitors all
  passed with `submit_invoked=false` and `cancel_invoked=false`.
- The live SQLite ledger, live orders, RPA orders, and auto-live orders remained
  absent. Emergency stop remained active and broker submission remained
  disabled.
- Current desktop Canary sandbox certification passed after fresh evidence, but
  it does not authorize submission or weaken any independent live gate.

**Verification**

- `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_huatai_notice.py tests\\test_huatai_login.py tests\\test_cli.py -q`:
  `115 passed in 1.92s`.
- Real `broker-refresh --live-config configs/huatai_desktop_canary.yaml`:
  completed without broker submission; the notice was closed once, buy/sell
  preview passed, and all three empty monitors passed.
- `.\\.venv\\Scripts\\python.exe -m pytest -q`:
  `1116 passed, 39 subtests passed in 311.25s`.

**Remaining work**

- The notice-close path is complete for the currently observed Huatai dialog.
  Any future marker/layout change intentionally blocks until reviewed.

## 2026-08-11 - Validate Huatai automatic login read-only recovery

**Objective**

Repair the login-control Unicode contract and verify one real DPAPI-backed
Huatai login without creating orders or initializing the live ledger.

**Changes**

- Replaced the corrupted login-button literals with ASCII Python Unicode
  escapes for the exact `ç»å½` and `ç¨æ·ç»å½` UIA names.
- Added a regression test that binds both runtime constants to their exact
  Unicode values.
- Kept strict process, root-window, parent-control, readback, and single-click
  requirements unchanged.

**Architecture and safety**

- The first refresh observed only the market window and exhausted bounded
  trading-module readiness with `login_attempted=false`; it did not access
  credentials or click login.
- One F12 navigation was sent to the unique SHA-256-pinned market window. The
  next snapshot explicitly classified `broker_authentication_required`.
- The controlled refresh verified account-field readback, entered the
  current-user DPAPI credential, clicked the exact login control once, and
  confirmed `account_snapshot_ready` after three snapshots.
- The login audit contains neither password nor account fields. The live SQLite
  ledger, live orders, RPA orders, and auto-live orders remained absent.
- Emergency stop stayed active and broker submission stayed disabled. Canary
  remained blocked because current order-form preview/monitor certification did
  not pass; this was not bypassed.

**Verification**

- Real `broker-refresh --live-config configs/huatai_desktop_canary.yaml`:
  completed without broker submission; login audit reported
  `attempted=true`, `account_field_verified=true`,
  `login_click_count=1`; refresh reported `status=ok` and
  `login_status=authenticated`.
- `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_huatai_login.py tests\\test_cli.py tests\\test_live_secrets.py tests\\test_local_operator.py -q`:
  `140 passed in 3.28s`.
- `.\\.venv\\Scripts\\python.exe -m pytest -q`:
  `1109 passed, 39 subtests passed in 313.58s`.

**Remaining work**

- Repair and separately re-certify the current Huatai order-form preview and
  monitor contracts before any future Canary review. Automatic login success
  grants no submission authority.

## 2026-08-11 - Add constrained Huatai automatic login

**Objective**

Allow an expired Huatai desktop trading session to recover during read-only
broker refresh without granting order, ledger, or submission authority.

**Changes**

- Added `huatai_login.py` as a login-only broker-boundary module. It validates
  the pinned Huatai processes and exact UIA login signature, verifies account
  readback, enters a DPAPI-protected password, and clicks the exact login button
  at most once.
- Added `broker-login-enroll`, which accepts the trading password only through
  two matching hidden prompts and preserves existing SMTP and integrity secrets
  in the current-user DPAPI payload.
- Integrated one bounded login attempt into `broker-refresh` only when the
  current collector output explicitly reports authentication and the structured
  desktop stage agrees. Snapshot polling confirms success without another click.
- Added redacted `huatai_login_audit.csv` evidence and login fields to the
  read-only refresh status. Updated both Huatai configs and the local operator
  guidance.
- Added ADR 0014 and updated the broker-boundary architecture and artifact
  ownership table.

**Architecture and safety**

- ADR 0014 supersedes only ADR 0005's manual-authentication requirement. Window
  identity, duplicate-process blocking, and all order safety rules remain.
- Captcha, second factor, unexpected controls, process/window ambiguity, missing
  credentials, account mismatch, and confirmation timeout fail closed.
- The login module has no order, cancel, ledger-initialization, account-import,
  emergency-stop, or submission-switch capability.
- Configuration remains `emergency_stop=true` and
  `allow_broker_submit=false`. The live SQLite ledger, live order CSV, RPA order
  CSV, and auto-live order CSV were verified absent after the change.
- Changing the live configs invalidates stale Canary certification by design;
  no re-certification, live ledger initialization, or broker order was performed.

**Verification**

- `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_huatai_login.py tests\\test_live_secrets.py tests\\test_local_operator.py -q`:
  `38 passed in 3.30s`.
- The first CLI regression exposed a stale-snapshot login trigger in three
  existing offline/error tests. The trigger was bound to explicit current
  collector authentication output; the rerun of login and CLI tests produced
  `107 passed in 2.59s`.
- `.\\.venv\\Scripts\\python.exe -m pytest -q`:
  `1108 passed, 39 subtests passed in 335.72s`.
- One preceding full run had a single host-level Windows `os.replace` access
  denial in an existing recovery test (`1107 passed`); that test passed alone
  in `1.13s`, and the final full rerun above passed cleanly.
- `load_config('configs/huatai_desktop_canary.yaml')` audit:
  `login_enabled=true`, `emergency_stop=true`,
  `allow_broker_submit=false`.
- `git diff --check`: passed after newline normalization.

**Remaining work**

- The operator must run the hidden-prompt enrollment command locally before the
  first automatic login. No trading password was requested in chat or written
  during development.
- Perform a separately observed read-only login recovery after enrollment.
  Captcha/MFA and future Huatai UI changes remain intentionally manual.

## 2026-08-11 - Preserve frozen candidate inputs and committed market scope

**Objective**

Restore reliable daily forward observation after a real 2026-08-11 refresh
exposed frozen-input mutation, committed-scope contraction, and conflicting
same-date shadow evidence.

**Changes**

- Immutable candidate refresh now preserves the complete frozen stock catalog
  and all fundamentals at or before the freeze date. Missing frozen baselines
  fail closed; only post-freeze prices, benchmark rows, and fundamentals may be
  appended.
- Separated the complete committed symbol scope from the smaller incremental
  request subset. Per-symbol source provenance is merged across refreshes, and
  newly entering liquidity-ranked symbols are fetched when provenance is
  missing even if their cached price date is current.
- Paper and shadow signal generation now reject an incomplete committed fetch
  scope instead of falling back to every cached symbol.
- Added an explicit research-only full-scope refresh mode for controlled
  provenance reconstruction; the daily default remains incremental.
- Retired the corrupted point-in-time v2 identity without repairing its frozen
  cache. A provisional v3 was also retired before forward attribution because
  its pre-registration scope was only 393/395.
- Created hs300_point_in_time_core_v4_forward only after its isolated cache
  reached 395/395 with zero failures, then preregistered and sealed it.
- Retired the original minimum-overlay identity after a same-date immutable
  component conflict and created
  breadth_guarded_minimum_overlay_v2_forward with no inherited observations.
- Updated the active forward-only candidate set, the authoritative
  architecture invariants, and ADR 0013. Strategy weights and risk thresholds
  were not tuned from the newly observed returns.

**Architecture and safety**

- A fetch attempt, committed market scope, frozen research identity, and shadow
  evidence are distinct states. Incremental operational work cannot redefine a
  frozen identity or silently narrow the strategy's formal universe.
- Corrupted or conflicting candidate evidence was retained and retired rather
  than overwritten. Replacement candidates use isolated cache, output, state,
  and manifest paths and start their forward windows at zero.
- The workflow remained research-only. It did not activate an execution
  strategy, initialize a live ledger, create generic/live/RPA order files, or
  call the broker boundary.
- Final configuration audit: emergency_stop=True and
  allow_broker_submit=False.

**Verification**

- Real network reconstruction completed at 800/800 and zero failures for both
  broad candidates. The point-in-time replacement completed at 395/395 and zero
  failures after restart-safe partial-cache retry.
- Successful forward-daily completed without paper or broker orders. Current
  candidate states are:
  6bd915f30663d669=10/60,
  4cd5415b054138e4=0/60, and
  2174e0a77b9902f2=0/60; all three report current market data and passing
  frozen-input evidence.
- Deployment remains blocked at common forward window 0/60; no selected
  execution strategy exists.
- Focused data/paper tests: 75 passed in 2.61s.
- Shared CLI, candidate, deployment, data, and paper tests:
  236 passed in 11.02s.
- The first full run reported 1101 passed plus one Windows WinError 5 in an
  unrelated directory-retry test. That test passed alone, and the complete
  rerun passed: 1102 passed, 39 subtests passed in 161.12s.
- Protected paths remained absent:
  data/active_execution_strategy.json,
  data/huatai_live_state.sqlite3, generic orders, RPA orders, and live
  workspace orders.

**Remaining work**

- Do not compare or promote the two new candidates until they accumulate the
  fixed 60-day forward window; the common comparison window is currently zero.
- Continue untuned daily observation and monitor committed-scope churn,
  provenance completeness, factor degradation, drawdown, turnover, and
  candidate-set identity.
- Restore current Huatai read-only authentication and complete replay, alert,
  sandbox, account-binding, and capital gates before any separately authorized
  live-ledger initialization or Canary submission.

## 2026-08-11 - Bind live evidence to one current derived bundle

**Objective**

Rebuild an internally consistent research evidence set and prevent stale or
mixed derived reports from being published into the live audit workspace.

**Changes**

- Added exact current-data recomputation for factor-robustness evidence and a
  strict `factor_robustness_data_current` readiness check. The expected hash
  respects the same configured scope and forward-only freeze boundary as the
  robustness run.
- Added factor-robustness detail and summary to the required live evidence
  allowlist. Source and destination publication now require current factor
  candidate/config/data/isolation checks and current cost-stress config/data
  checks.
- Snapshotted every allowlisted source file before copy and added one
  `evidence_bundle_sha256` to every sync row and success summary. Source changes
  before, during, or after copy abort success.
- Recorded the durable publication boundary in `docs/ARCHITECTURE.md` and ADR
  0012.

**Architecture and safety**

- The change spans strategy governance and the read-only live evidence boundary.
  It does not activate a strategy or add broker capability.
- Evidence sync still excludes orders, positions, account state, broker access,
  ledger initialization, and submission. A success summary is written only
  after destination base and derived identity verification.
- Existing flat output paths remain compatible. An interrupted copy has no
  authority without a successful bundle summary.

**Verification**

- Rebuilt the main research evidence on the current committed cache:
  `backtest`, `stress`, and `factor-robustness` all exited 0.
- The factor data hash recomputed from the effective frames is
  `16cb4ec0aea1ab370ec07d81d251d8f266d0ac69050a03dc26d36760eb6412c0`
  and now matches strict readiness. Current cost-stress data identity reports
  `match`.
- Factor robustness remains honestly blocked as `review_required`: eight of
  nine perturbation scenarios passed, one was fragile, and the baseline
  anti-overfit check failed. The current high-cost scenario reports 83.55%
  excess return but -27.43% maximum drawdown, failing the strict -25% limit.
- Focused factor, readiness, and sync tests passed: `44 passed in 40.97s`.
  The source-mutation sync suite passed separately: `6 passed in 0.88s`.
- Full regression passed:
  `1099 passed, 39 subtests passed in 167.57s (0:02:47)`.
- Real local-only publication succeeded with 36 copied files, one generated
  manifest, and bundle
  `a2f634c6f760336a8f233da4c4b416e6048ccbf3673777b313a12c73ec80ae7d`.
  The summary confirms orders, positions, and account state were not copied.
- The deployment gate still reports `strategy_ready=False`,
  `broker_read_only_ready=False`, and `ready_to_request_canary=False`.
  Protected active-strategy, live-ledger, global order, RPA order, and
  live-workspace order paths remained absent. `emergency_stop` remained true
  and `allow_broker_submit` remained false.

**Remaining work**

- Do not activate the quarantined main candidate. The three independent
  successors remain incomplete; the shortest authoritative forward window is
  4/60 trading days and their freshness is currently 0/3.
- Refresh market and forward-candidate observations beyond 2026-08-07 without
  tuning from their observed path, then continue the fixed 60-day gates.
- Restore current Huatai read-only authentication/evidence and complete replay,
  alert, sandbox, account-binding, and Canary-capital gates before any separate
  authorization to initialize a live ledger.
- Strategy robustness and strict drawdown failures require a new independently
  justified successor, not relaxed thresholds or post-hoc tuning.

## 2026-08-11 - Make forward-failure quarantine irreversible

**Objective**

Remove contradictory candidate authority reports and ensure that an early or
terminal forward failure cannot later recover into strategy activation.

**Changes**

- Added a shared lookup for the latest terminal failure matching either the
  strategy hash or behavior hash. Candidate validation now publishes
  `candidate_quarantine_clear`, and both validation and progress report
  `quarantined_forward_failure` after a matching terminal record.
- Required the quarantine check in candidate activation. Current positive
  returns remain visible for research, but cannot restore execution authority.
- Made legacy candidate progress use the same benchmark-comparison evidence as
  legacy validation. Immutable shadow observations remain the evidence source
  for forward-only preregistered candidates.
- Recorded the durable strategy-governance rule in `docs/ARCHITECTURE.md` and
  ADR 0011.

**Verification**

- Before the fix, the same candidate `fb600e42987b51aa` reported 23 observed
  days with `early_forward_degradation` in progress, but 37 days with
  `early_forward_warning` in validation.
- Focused candidate, activation, readiness, and strategy-health tests passed:
  `89 passed, 2 subtests passed in 12.17s`.
- Full regression passed: `1096 passed, 39 subtests passed in 300.39s`.
- Documentation governance tests passed: `3 passed in 0.13s`.
- A current `candidate-audit` made validation, progress, and strict readiness
  agree on 37 days, -4.7761% excess return, and
  `quarantined_forward_failure`. The append-only failure record remains
  `early_forward_degradation@2026-07-16T16:38:27+08:00`.
- A local-only `live-evidence-sync` was attempted after source inspection proved
  it does not access the broker, generate orders, or initialize a ledger. It
  failed closed because stocks, prices, and fundamentals row/hash identities
  did not match; no stale evidence was published.
- Protected paths `data/active_execution_strategy.json`,
  `data/huatai_live_state.sqlite3`, `outputs/orders.csv`, and
  `outputs/rpa_orders.csv` remained absent. `emergency_stop` remained true and
  `allow_broker_submit` remained false.

**Remaining work**

- Treat the main candidate as permanently retired from activation. Continue the
  three independently frozen successors to their untuned 60-day forward gates.
- Rebuild one internally consistent current research evidence bundle before
  publishing to the live audit workspace; do not bypass manifest identity
  checks.
- Broker submission remains disabled, and no live ledger exists.

## 2026-08-11 - Complete low-risk candidate verification

**Verification**

- Focused configuration, candidate, documentation, and activation regressions
  passed: `51 passed in 9.77s`.
- The candidate report directory contains zero-row submission-intent and
  confirmation safety reports generated by normal report rendering; they are not
  signed broker actions and carry no execution authority.
- Protected global order and live-ledger paths remained absent. The pre-existing
  execution epoch retained its 2026-07-26 modification time.

**Remaining work**

- Keep the rejected candidate outside forward observation and execution selection.

## 2026-08-11 - Preregister and reject an independent low-risk candidate

**Objective**

Refresh the complete CSI 300 reconstruction metadata, then test one independently
specified, low-turnover China A-share low-risk hypothesis without a parameter
sweep or any authority to change the execution strategy.

**Changes**

- Refreshed BaoStock metadata for the reconstructed point-in-time universe. The
  scoped unknown-industry and placeholder-listing-date ratios both fell to 0.25%.
- Added an isolated CSI 300 low-risk candidate using 120 equal-weighted stocks,
  industry neutralization, 120-day total/downside/idiosyncratic volatility inputs,
  a 252-day market regression window, quarterly rebalancing, and no timing overlay.
- Cloned the audited reconstruction cache into a dedicated candidate cache, then
  preregistered and sealed candidate `bc163b8fc5a47b57` before its first historical
  evaluation. The freeze boundary is 2026-08-10 and historical promotion evidence
  is explicitly disallowed.
- Marked the fixed candidate rejected after its first diagnostic evaluation. No
  lookback, factor-weight, holding-count, or rebalance-frequency tuning followed.

**Architecture and safety**

- The candidate owns separate cache, output, SQLite, governance, and manifest
  paths and remains outside the default forward-observation set.
- The configuration remains research-only, automatic live execution is disabled,
  broker submission is disabled, and its emergency stop is active.
- No active strategy, execution epoch, live ledger, broker certificate, generic
  order, RPA order, or submission artifact was created or changed.

**Verification**

- Configuration validation returned no errors. The isolated cache copied 12 files
  and 32,063,461 bytes exactly from the audited reconstruction snapshot.
- Historical diagnostics covered 2024-01-17 through 2026-08-10. Strategy return was
  34.47% versus 45.61% for the benchmark; excess return was -11.15%, information
  ratio -0.458, and annualized turnover 2.27.
- The candidate reduced maximum drawdown from 15.66% to 12.31%, but last-30%
  out-of-sample excess was -4.38%; all three walk-forward test folds were negative,
  with average test excess -5.66%.
- The point-in-time data gate remained complete for 395 priced symbols, while both
  unknown-industry and imputed-list-date ratios were 0.25%.

**Remaining work**

- Do not promote or forward-observe this rejected low-risk candidate.
- Continue untuned observation of the already frozen candidates. A future new
  hypothesis must be independently justified and preregistered before evaluation.
- Broker submission and real-account ledger initialization remain prohibited until
  the existing live gates pass and the user separately authorizes those actions.

## 2026-08-11 - Enforce complete point-in-time index-member history

**Objective**

Remove the latest-constituent survivorship path from CSI 300 point-in-time
research, preserve the immutable V2 forward candidate, and measure the strategy
again on a separately governed complete historical-member cache.

**Changes**

- Changed membership-only acquisition from the latest 300 constituents to the
  union of all constituents in snapshots inside the configured research range.
- Added machine-readable membership quality and per-snapshot artifacts covering
  identity, provenance, cardinality, future dates, calendar alignment, gaps,
  freshness, catalog coverage, historical-member prices, and minimum snapshot
  price coverage.
- Required 100% historical-member and per-snapshot price coverage. The composite
  gate now blocks both practical and strict readiness and appears in the Chinese
  report.
- Made membership quality generation fail closed without crashing when stocks
  and prices do not yet exist.
- Added cold-start orchestration so a new membership-only fetch prepares
  benchmark history and point-in-time membership before requesting member
  prices.
- Added the isolated
  `configs/hs300_point_in_time_history_reconstruction.yaml` research design.
  It does not alter or inherit authority from the immutable V2 forward candidate.
- Updated architecture and accepted ADR 0010.

**Architecture and safety**

- This change is owned by data acquisition, data trust, research diagnostics,
  readiness, and reporting. It does not change broker adapters, account state,
  live-ledger ownership, order generation, activation, or submission.
- V2 remained immutable through 2026-07-31. Its incremental refresh accepted
  only post-freeze rows and did not rewrite missing historical constituents.
- V3 uses separate cache, output, state, candidate, governance, and strategy
  identities. Its status is `historical_data_reconstruction` and its results
  are diagnostic only.
- Missing membership quality fails practical and strict readiness. Research
  output cannot authorize execution.

**Verified evidence**

- V2 before refresh committed only 300 of 395 historical members. Historical
  member coverage was 75.9494%, and the earliest snapshot covered 190/300.
- V2 incremental refresh committed 393 symbols but correctly left pre-freeze
  history unchanged; its minimum snapshot coverage remained 75%.
- V3 fetched 395/395 historical members with zero final failures and committed
  341,390 price rows. It contains 45 monthly/current snapshots and 13,500
  constituent rows; every snapshot has exactly 300 members.
- V3 historical-member coverage is 395/395 and minimum per-snapshot coverage is
  100%. Stock-catalog coverage is 99.4937%; the two delisted names were absent
  from the current spot catalog but their historical prices were returned by
  BaoStock when requested from 2023.
- The corrected diagnostic backtest ran from 2023-07-06 through 2026-08-10:
  strategy return 16.9120%, benchmark return 22.3608%, excess return -5.4488%,
  maximum drawdown -10.3910%, information ratio -0.287520, and annualized
  turnover 1.705906.
- The last-30% sample excess return is -1.3293%. Walk-forward produced three
  non-overlapping test folds, 0% positive-fold ratio, average test excess
  -5.3311%, and worst test excess -8.4189%. The strategy is not eligible for
  promotion or practical/live use.
- Affected data/membership/readiness/report tests: 140 passed. Isolated
  cold-start and quality tests: 7 passed. Full regression: 1093 passed and 39
  subtests passed in 290.74 seconds.
- Playwright loaded the V3 report at 1440 x 1000, found the Chinese point-in-time
  quality section and four occurrences of `priced=395/395`, and captured
  `output/playwright/point-in-time-membership-report.png`. The only console
  error was the existing optional missing `favicon.ico`.

**Protected live-state audit**

- Absent: `data/huatai_live_state.sqlite3`,
  `outputs_live/huatai_desktop_canary_certification.json`,
  `data/active_execution_strategy.json`, main/V2/V3 `orders.csv`, and
  main/V2/V3 `rpa_orders.csv`.
- Default, V2, V3, and bridge-template configurations have
  `auto_live.enabled=false` and `allow_broker_submit=false`. Huatai
  read-only and Canary keep `allow_broker_submit=false` and
  `emergency_stop=true`.
- No broker action, live-ledger initialization, Canary arm, candidate activation,
  order creation, protected-state repair, or account mutation was performed.

**Remaining work**

- Do not tune or promote the current point-in-time parameter set: the corrected
  full-period, holdout, and walk-forward excess returns are negative.
- Design a new preregistered successor using independent economic hypotheses,
  then require frozen post-design forward evidence rather than selecting on this
  V3 result.
- Acquire authoritative industry, listing-date, historical ST/risk-warning, and
  point-in-time fundamental data. Current V3 industry and listing placeholders
  remain unsuitable for strict live conclusions.

## 2026-08-11 - Quantify long-history survivorship exposure

**Objective**

Make the fixed 800-symbol long-history result explicit about current-pool
survivorship exposure without changing the frozen strategy or weakening any
execution boundary.

**Changes**

- Added a dedicated survivorship audit that binds the completed per-symbol
  provenance, all symbols with cached prices in the configured study range,
  listing-date quality, and annual last-trading-day coverage snapshots.
- The audit deliberately records point-in-time membership, delisted-security
  history, and survivorship correction as failed evidence. Cached non-pool
  symbols are reported as sample restriction, not inferred to be valid
  historical constituents.
- Bound the joint SHA-256 of `survivorship_bias_audit.csv` and
  `survivorship_bias_snapshots.csv` into extended-history validation status.
  A missing or changed audit prevents completed-state reuse.
- Added Chinese report sections, field labels, and high-priority evidence-gap
  warnings for the new audit.
- Updated the architecture and added ADR 0009 for the durable quantify-but-do-
  not-correct policy.

**Architecture and safety**

- This change is owned by research validation and presentation. It does not
  change strategy parameters, candidate identity, account state, broker
  adapters, order generation, activation, or submission.
- The frozen strategy, backtest configuration, and formal-price identities
  remained unchanged. The historical result remains research-only and cannot
  authorize promotion or execution.
- Missing authoritative daily membership, delisted securities, and historical
  risk-warning state remains a high-severity limitation rather than a synthetic
  pass.

**Verified evidence**

- Current committed pool: 800 symbols. Price cache within the 2018-01-01 through
  2026-08-07 study request contains 1503 symbols, of which 703 are outside the
  current pool; current-pool share is 0.532269.
- Annual snapshots show that every cached symbol with year-end prices in
  2018-2022 belonged to the current pool. Non-current cached coverage becomes
  material only from 2023, so the early cross-section is not historical full-
  market evidence.
- Refreshed fixed validation completed with the same 1965 effective days,
  229.0899% total return, 39.5598% benchmark return, 189.5301% excess return,
  -33.3808% maximum drawdown, and 0.856149 information ratio.
- The completed status now binds survivorship audit SHA-256
  `fee17ad5a45833b2fa869f84fb8266d513b15953dd2728872d40f8265a1555dd`.
  Exact completed-state reuse returned in 4.323465 seconds without rerunning the
  backtest.
- Focused survivorship/history/report tests: `45 passed in 3.51s`.
- Full regression: `1086 passed, 39 subtests passed in 290.82s`, exit code zero.
- Documentation and research governance: `7 passed in 0.79s`, exit code zero.
- Playwright loaded the refreshed report at 1440 x 1000, found the Chinese audit
  section and all four high-priority warnings, and captured
  `output/playwright/survivorship-audit.png`. The only console error was the
  existing optional missing `favicon.ico`.

**Protected live-state audit**

- Absent: `data/huatai_live_state.sqlite3`,
  `outputs_live/huatai_desktop_canary_certification.json`,
  `data/active_execution_strategy.json`, main and isolated `orders.csv`, and
  main and isolated `rpa_orders.csv`.
- `allow_broker_submit=False` in default, extended-history, Huatai read-only,
  Huatai Canary, and bridge-template configurations. Extended-history emergency
  stop remains active.
- No broker action, live-ledger initialization, Canary arm, candidate activation,
  order creation, or account mutation was performed.

**Remaining work**

- Acquire or construct a separately governed point-in-time universe containing
  daily membership, delisted securities, and historical ST/risk-warning state;
  then preregister a new validation rather than rewriting this result.
- Continue frozen forward and Shadow observation. The strategy remains blocked
  from strict live use, and its 33.38% historical drawdown remains above the
  configured 25% live threshold.
- Do not tune the frozen strategy on this completed cache. Any successor strategy
  research must use a separate preregistered design and later forward evidence.

## 2026-08-11 - Complete trust-gated long-history validation

**Objective**

Complete the frozen 800-symbol history acquisition and fixed-strategy validation
without weakening source quality, execution isolation, or live-trading gates.

**Changes**

- Changed AkShare history selection so each non-empty provider response is
  normalized and structurally validated before selection. An invalid Eastmoney
  response now continues to Sina and then Tencent within the same retry.
- Preserved reported-amount requirements. A Sina candidate whose committed
  overlap is missing, insufficient, or inconsistent is accepted only after an
  independently requested Tencent series passes the same close, volume, and
  amount thresholds; verifier identity and errors are persisted.
- Normalized Tencent STAR Market volume as shares while retaining lot-to-share
  conversion for its other supported A-share boards. Tencent turnover remains
  estimated and verifier-only.
- Serialized the isolated AkShare history job with `history_workers: 1` after
  the provider's in-process V8 decoder crashed under the observed threaded
  workload. A non-zero batch exit now publishes `retry_wait` instead of a
  healthy-looking wait.
- Added `ProgressFile` injection to the daemon for hermetic dry-run tests; the
  production default progress path is unchanged.
- Bound history validation to the exact declared fixed-strategy SHA-256 and made
  operator status recompute the strategy, backtest configuration, and formal
  prices identities before displaying completed evidence.
- Added an absolute 500-symbol complete-market threshold for the isolated
  100-position validation and enforced the preregistered 1500 post-warmup
  trading-day minimum. Completed evidence cannot be reused below the current
  minimum.
- Refreshed the Chinese main report and exposed the isolated long-history result
  as research evidence only. It does not replace the current strategy report or
  authorize promotion.

**Architecture and safety**

- This change owns data acquisition, research validation, operator presentation,
  and unattended research scheduling. It does not change strategy weights,
  broker adapters, account state, order generation, activation, or submission.
- Incomplete and failed staging remained isolated until all 800 symbols and the
  final benchmark fetch completed with zero failures.
- The first 834-day validation artifact was explicitly treated as invalid
  evidence: relative 90% daily coverage had silently removed most of the
  requested history. The minimum-day gate and absolute cross-section policy were
  added before rerunning; no strategy parameter was tuned from either result.
- Research-only validation continues to forbid `orders.csv` and
  `rpa_orders.csv`. Backtest orders remain simulated research output.
- The current 800-symbol pool is selected using current membership/liquidity and
  therefore retains survivorship bias. The long-history result is stronger
  stress evidence, not a claim of point-in-time investable-universe purity.

**Verification**

- Real `600256` diagnosis: Eastmoney returned 2073 rows with 43 nonpositive
  OHLC rows; Sina returned the same 2018-01-02 through 2026-08-07 range with
  2073 rows, reported amount, and zero price issues. The fixed function selected
  `stock_zh_a_daily`.
- Real fallback verification:
  - `003816`: 1685 Sina rows; committed overlap was only 3 rows, while the
    10-row Tencent check passed with close error 0, volume error
    0.0000000344, and amount error 0.003476.
  - `688318`: 1524 Sina rows; the committed tail exposed a 100x volume-unit
    error, while normalized Tencent verification passed with close and volume
    error 0 and amount error 0.007621.
- Trusted progress advanced 708 -> 733 -> 758 -> 783. A threaded V8 fatal at
  783 left authoritative progress unchanged. Serial recovery then advanced
  783 -> 798 and, after the two cross-source fixes, 798 -> 800 with zero final
  failures.
- The daemon was restarted only at its persisted retry times. The earlier
  identity-changing restart occurred 12 seconds before its prior target and is
  retained as an audit exception; subsequent retries were not compressed.
- The superseded first validation had 834 effective days and was not accepted as
  long-history evidence after review.
- Final validation status:
  - scope `800/800`, failures `0`
  - effective/minimum trading days `1965/1500`
  - requested data range `2018-01-01..2026-08-07`; effective equity range
    `2018-07-04..2026-08-07`
  - strategy SHA-256
    `26df6a8b70566c434d9b02c0a2d90d5229fe50a7d7b78aa37d6739504451b5b3`
  - formal prices SHA-256
    `b1eb8529bcd3b948e705bdac27859bf6a21b6f91921c05329f4479f553b84c5c`
  - backtest configuration SHA-256
    `8cd6605f024765150ebe9377166498ab3519ca7f2a0440f312af8982d73fe09a`
  - total return `2.290899`, benchmark return `0.395598`, excess return
    `1.895301`, max drawdown `-0.333808`, information ratio `0.856149`
  - 13 non-overlapping walk-forward folds, positive-test ratio `0.615385`,
    worst fold excess `-0.071798`
  - reliability `69/100`, observation grade; 2024-03-05 onward contributes
    62.44% of full-period excess and `optimized_on_current_cache=True`
    remains unresolved.
- Focused data tests: `61 passed in 2.29s`.
- Focused history/daemon/operator tests after final gate changes:
  `46 passed in 3.56s`.
- Final full regression: `1084 passed, 39 subtests passed in 162.73s`, exit
  code zero.
- Playwright loaded the refreshed Chinese report successfully. The viewport had
  no overlap or mojibake; the only console error was an optional missing
  `favicon.ico`.
- Current daemon status is complete and bound to the current script/startup
  entry. The main operator row reports `days=1965` with strategy, backtest,
  and prices identities all current.

**Protected live-state audit**

- Absent: `data/huatai_live_state.sqlite3`,
  `outputs_live/huatai_desktop_canary_certification.json`,
  `data/active_execution_strategy.json`, all main/live/isolated
  `orders.csv`, and all `rpa_orders.csv`.
- `allow_broker_submit=False` in default, extended, read-only, Canary, and
  bridge configurations. Default/bridge auto-live is disabled; read-only and
  Canary configurations retain `emergency_stop=True`.
- Main operator status remains `strict_live_allowed=False`. No broker
  submission, Canary certification, live-ledger initialization, or account
  mutation was performed.

**Remaining work**

- Do not promote from this historical result. Continue frozen forward and
  shadow observation; current main evidence still reports negative forward
  excess and strict-live failure.
- Build a point-in-time universe or constituent-history study to quantify the
  survivorship bias of the current 800-symbol pool.
- Treat the 2025-2026 excess concentration and 33.38% drawdown as material
  stability risks. Any successor research must be preregistered and must not
  tune the frozen strategy on this completed sample.

## 2026-08-11 - Bind unattended history status to current runtime

**Objective**

Repair unattended runtime evidence after the history and observation Startup
entries disappeared, prevent stale history status from being reported as a
healthy task, and complete the governance record omitted by automatic commit
`81baf67`.

**Changes**

- Restored and started the current-user observation-only and research-history
  Startup entries. Their installers verified fresh live processes before
  returning.
- Upgraded history daemon status to schema version 3 and bound every status to
  the SHA-256 of the running script.
- Made local operator history status require the current mode, a fresh timestamp,
  the current script hash, and a safe current-user Startup launcher while work is
  incomplete. The rendered value now exposes age, script, and Startup evidence.
- Extended the architecture rule for authoritative unattended liveness from
  observation to research backfill and added focused stale/missing evidence
  regressions.
- Repaired the missing development-log record for commit `81baf67`: that commit
  added cross-source confirmation of legitimate late history starts, persisted
  start-boundary provenance, covered the BaoStock path, and kept estimated
  amount fail closed. Its final regression was 1075 tests plus 39 subtests.

**Architecture and safety**

- This change affects only interface/runtime evidence and the isolated research
  scheduler. It does not change strategy parameters, formal-cache acceptance,
  broker adapters, account state, execution provenance, or submission gates.
- The Huatai configuration remains emergency-stopped with broker and service
  submission disabled. Live ledger, active strategy, Canary certificate,
  generic order, and RPA order artifacts remain absent.
- GitHub Startup synchronization was not restored: the permission reviewer
  rejected unattended future pushes because their payloads cannot be known in
  advance. No workaround was attempted.

**Verification**

- Focused local-operator and history-daemon regression: `38 passed in 5.20s`.
- Final full regression: `1075 passed, 39 subtests passed in 159.76s`, exit code
  zero.
- History daemon PID 13460 published schema v3 with the current script hash. Its
  first bounded batch advanced trusted completion from 684 to 708/800; one of
  25 symbols remained unsuccessful, so the source was correctly marked degraded
  and the regular 60-minute interval was retained.
- Observation supervisor PID 3676 and observation daemon PID 5004 were live.
  Fresh `local-status` and Chinese `report.html` artifacts were regenerated.
- Formal price cache and protected live artifacts were audited; incomplete
  staging did not create or replace any execution artifact.

**Remaining work**

- Continue the remaining 92-symbol history acquisition and run fixed-strategy
  isolated validation only after trusted completion. Investigate the one degraded
  symbol through existing bounded retries; do not relax amount provenance.
- Strategy remains non-live: the common forward window, independent history,
  strict readiness, repeated broker snapshots, explicit live-ledger
  initialization, account binding, lifecycle certification, Canary approval,
  and any submission remain separate gates.
- Restore unattended GitHub synchronization only after the operator explicitly
  accepts that future unknown repository changes may be committed and pushed to
  the configured remote.

## 2026-08-10 - Accelerate only trusted healthy history batches

**Objective**

Shorten the time to authoritative independent strategy evidence while retaining
bounded public-source use, source-failure backoff, formal-cache isolation, and
all live-execution safety boundaries.

**Changes**

- Added a separate 15-minute healthy interval to the history daemon. It applies
  only after exit zero, source status `ok`, zero failed items, at least one
  attempt, and equal attempted/successful counts.
- Kept the regular interval at 60 minutes for non-source degraded or ambiguous
  results and the persisted source-wide outage backoff at 240 minutes.
- Persisted both healthy and regular intervals in the current-user Startup
  launcher and added lower-bound and interval-order validation.
- Added static, parser, Startup-persistence, and invalid-parameter regression
  coverage. Updated the architecture, ADR 0008, and history runbook.

**Architecture and safety**

- This is research-data scheduling only. It does not change the default or
  isolated strategy parameters, strategy hash, formal-cache acceptance,
  candidate authority, broker capability, account state, or submission gates.
- The 25-symbol request budget, 15-minute lower bound, quiet window, daily
  observation lock, 60-minute fallback, and 240-minute source backoff remain
  independent traffic and concurrency controls.
- The Huatai configuration remains emergency-stopped with broker and service
  submission disabled. Live ledger, active strategy, certification, generic
  order, and RPA order artifacts remain absent.

**Verification**

- Focused daemon regression after implementation: `8 passed in 3.87s`.
  Combined daemon and documentation governance regression:
  `11 passed in 3.91s`. Final daemon, documentation, and strategy-identity
  regression: `14 passed in 4.48s`.
- Manual invalid-parameter probes rejected a 10-minute healthy interval and a
  healthy interval longer than the regular interval, both with exit code 1.
- Replaced and verified the current-user Startup launcher with
  healthy/regular/source-backoff intervals of 15/60/240 minutes.
- Real PID 4012 batch exited zero with source status `ok`, attempted and
  succeeded 25/25, zero failures, and advanced freshly revalidated progress from
  99 to 124/800. Its status scheduled the next batch exactly 15 minutes later
  with the audited accelerated-batch message.
- The formal `local-status` and `report` commands completed successfully and
  refreshed the Chinese operator artifacts.

**Remaining work**

- Continue trusted acquisition for the remaining 676 symbols, then let the
  daemon run the fixed isolated long-history validation. Do not promote or tune
  from incomplete staging.
- Strategy remains `pause_paper_orders`; the common forward window, independent
  history, and strict readiness checks remain incomplete.
- Huatai authentication, repeated cross-date read-only snapshots, explicit
  live-ledger initialization, account binding, lifecycle certification, Canary
  approval, and any submission remain separate future gates.

## 2026-08-10 - Add trust-gated historical source failover

**Objective**

Resume independent long-history evidence collection with a healthy reported-
amount source while removing untrusted staging and preserving every strategy and
live-execution boundary.

**Changes**

- Added Sina `stock_zh_a_daily` between Eastmoney and Tencent in the AkShare
  history chain, preserving reported volume and amount provenance.
- Added configurable recent-overlap validation for close, volume, and amount;
  accepted source metrics are persisted per symbol in the provenance schema.
- Made partial checkpoints write provenance before prices and extended the
  BaoStock callback to checkpoint matching provenance rows.
- Bound persisted source-outage backoff to the combined default/override source
  plan identity, so a changed provider plan does not inherit an obsolete delay.
- Changed only the isolated extended-history configuration to automatic AkShare
  failover, disabled its currently unavailable BaoStock second fallback,
  rejected estimated amount, and required trusted provenance for resume.
- Added ADR 0008, architecture rules, and runbook guidance. Acquisition trust
  controls remain outside strategy identity; accepted data remains manifest
  bound.

**Architecture and safety**

- Audit found 97 structurally valid rows with unknown resumed source and one
  Tencent estimated-amount row. They were removed from trusted completion and
  must be reacquired; the lower count is an evidence correction.
- The formal `data/cache/prices.parquet` remained unchanged with timestamp
  2026-08-09 20:40:03 throughout incomplete staging runs.
- Default strategy hash remains exactly
  `2385b5894c33875d0afc519d3dc0f3027472ad42cd64d07438d8e2a34822b707`
  and matches the current main run manifest. No strategy parameters, frozen
  candidates, promotion thresholds, or execution authority changed.
- Broker submission and service submission remain disabled, emergency stop is
  active, and live ledger, certification, active-strategy, generic-order, and
  RPA-order artifacts remain absent.

**Verification**

- Isolated probe for `000001` returned 2,086 rows from 2018-01-02 through
  2026-08-07 using Sina reported amount. Its latest 60-date overlap had close
  median error 0, volume median error below 0.000001, and amount median error
  0.56%.
- A deliberately bounded 10-minute run exposed the old provenance checkpoint
  gap and timed out safely with audit exit 124; no formal cache replacement
  occurred. The corrected no-BaoStock batch then finished with 25/25 success.
- After strict provenance filtering, trusted progress intentionally reset from
  the apparent 139 to 52 symbols. Two successful bounded batches advanced it to
  100/800 with zero current failures: 23 BaoStock and 77 Sina rows, all
  `amount_quality=reported`; all 77 Sina rows passed overlap validation.
- Focused final regression: `62 passed in 5.68s`. Full regression:
  `1067 passed, 39 subtests passed in 160.66s`.
- Current-user history daemon PID 3952 is waiting for 04:16 with source status
  `ok`; observation supervisor PID 23068 and Git sync PID 21848 remain healthy.
- Chinese local status and report were refreshed. Strategy remains
  `pause_paper_orders`, strict live is false, and Huatai read-only refresh still
  reports `broker_authentication_required`.

**Remaining work**

- Continue the trusted 100/800 acquisition and run fixed isolated validation
  only after authoritative 800/800, zero-failure completion.
- Investigate historical provider-era volume/amount discontinuities found in the
  current mixed cache; do not rely on current historical liquidity results as
  independent validation before the trusted cache completes.
- Continue the frozen 60-day common forward window without tuning. Broker
  authentication, live-ledger initialization, certification, and any Canary
  submission remain separate operator-authorized future steps.


## 2026-08-10 - Bind daily signals to committed data and back off source outages

**Objective**

Improve strategy evidence credibility and unattended stability without tuning on
current forward results, enabling broker submission, or initializing live state.

**Changes**

- Classified bounded long-history batches as `ok`, `degraded`,
  `source_unavailable`, or `failed`, and persisted attempted/successful counts.
- Added a restart-safe 240-minute source-outage backoff with audit exit code 75;
  validated staging remains resumable and cannot replace the formal cache.
- Exposed long-history source status in the Chinese local operator view as a
  research warning that does not alter execution authority.
- Excluded the new history staging/resume controls from strategy identity while
  retaining market-file and run-manifest data binding.
- Bound paper, shadow, broker capital feasibility, and broker order preview to
  the exact latest completed fetch scope. Account marking retains all held-symbol
  prices but resolves the latest complete date from that committed scope.
- Recorded the durable decisions in ADR 0006 and ADR 0007 and updated the
  architecture and history runbook.

**Architecture and safety**

- The raw cache may retain old symbols, but it no longer changes signal-date
  completeness or target construction when a valid completed-fetch manifest is
  present.
- Paper and shadow provenance exposes committed-scope binding. Missing binding
  remains research-only fallback evidence and cannot independently authorize
  execution.
- No candidate was tuned or promoted from current forward observations.
- Broker submission and service submission remain disabled, emergency stop
  remains active, and audited live-ledger, certification, active-strategy,
  generic-order, and RPA-order files remain absent.

**Verification**

- A real bounded history attempt returned exit code 75 in 52.8 seconds with
  `source_unavailable`, attempted 25, successful 0, and preserved 120/800
  validated symbols. An immediate second run honored persisted backoff in 1.0
  second without another network request.
- Focused final regression passed: `134 passed in 5.60s`; full regression passed:
  `1061 passed, 39 subtests passed in 154.52s`.
- The primary challenger observation completed in 126.1 seconds and used the
  committed 800-symbol scope through 2026-08-07 instead of the false raw-cache
  date 2026-07-21.
- The challenger retained strong historical metrics but failed current promotion
  evidence: frozen forward excess return `-11.64%` over 20 trading days and
  forward information ratio `-4.04`. Decision remains
  `do_not_promote_quarantined_dependency`; no paper permission was granted.
- Live evidence sync restored the frozen strategy hash and copied 34 evidence
  files plus one manifest without copying orders, positions, or account state.
- Restored local background tasks: observation supervisor PID 23068 with daemon
  PID 23536, history daemon PID 5344 with source backoff until 05:58, and Git
  sync daemon PID 21848 waiting for 22:30.

**Remaining work**

- Keep paper and live orders paused. Continue the frozen 60-day common forward
  observation; do not optimize against the current 20-day challenger loss.
- Complete independent long-history acquisition after the public source
  recovers, then run the fixed isolated validation without promoting from it.
- Manually restore Huatai authentication when current broker read-only evidence
  is needed. Submission, live-ledger initialization, certification, and Canary
  execution remain separately authorized future work.
## 2026-08-10 - Automate fail-closed Huatai read-only observation

**Objective**

Make daily local operation refresh Huatai read-only evidence before reporting,
recover a uniquely identified trading module when safe, and report expired
trading authentication quickly without automating login or orders.

**Changes**

- Reordered daily observation to run broker read-only refresh after market
  observation and before report, local status, and email alert generation.
- Preserved broker-refresh status instead of overwriting it with a later generic
  local-status run.
- Added pinned one-shot F12 trading-module navigation, minimized-window restore,
  and duplicate-client launch blocking.
- Added desktop login-signature classification as
  `broker_authentication_required`, explicit Chinese operator guidance, and
  structured wake message/attempt counters in the refresh audit.
- Enabled broker read-only refresh in the current-user daily observation Startup
  entry and documented the durable boundary in ADR 0005 and the architecture.

**Architecture and safety**

- Desktop recovery is limited to the SHA-256-verified executable, one exact
  market process, one same-directory trading process, and one unique window.
- The automation never fills broker credentials, confirms login, initializes a
  live ledger, creates an execution order, submits/cancels an order, or changes
  an account balance.
- Broker submission remained disabled, emergency stop remained active, service
  submission remained disabled, and stale certification remains absent after
  adapter/config changes.
- Audited live ledger, active strategy, certification, generic order, and RPA
  order paths remained absent.

**Verification**

- Focused Huatai, CLI, local operator, observation, and daemon regression:
  `174 passed in 25.31s`; final navigation/audit regression:
  `138 passed in 1.90s`.
- Final full regression: `1054 passed, 39 subtests passed in 156.84s`.
- Real `broker-refresh` failed closed in `15.8` seconds with exit code `1`, one
  snapshot attempt, and desktop stage `broker_authentication_required`; no
  window wake was repeated because the trading login page was already visible.
- The real failure invalidated stale broker account evidence and regenerated
  operator/report evidence without creating any protected live or order file.
- After verification, the paused local tasks were restored: observation supervisor
  PID 6352 and daemon PID 8408 are waiting with broker read-only refresh
  enabled; history daemon PID 15628 resumed at 120/800; Git sync PID 15516
  is waiting for 22:30 with last exit code 0.

**Remaining work**

- Manually complete Huatai trading authentication and open ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¨ÃÂÃÂµÃÂÃÂÃÂÃÂ©ÃÂÃÂÃÂÃÂÃÂÃÂ¨ÃÂÃÂÃÂÃÂ¡ÃÂÃÂ§ÃÂÃÂ¥ÃÂÃÂ¨ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ, then run
  `broker-refresh` again to refresh current account/capital evidence.
- Keep live submission disabled. Strategy promotion still requires the untuned
  forward window and independent historical validation; no current research
  result authorizes live orders.
## 2026-08-10 - Make history completion authoritative and auto-validate fixed strategy

**Objective**

Prevent incomplete or unsuccessfully committed long-history data from appearing
complete, then automatically produce isolated fixed-strategy evidence without
promoting or authorizing the strategy.

**Changes**

- Moved the long-history complete marker after the formal fetch-output commit;
  a formal writer exception now leaves no new complete marker.
- Required every requested symbol and every final acquisition stage, including
  benchmark acquisition, to finish with zero failures before bounded history can
  become complete.
- Added the research-only history-validate workflow and CLI command. It binds
  completed progress, fixed backtest configuration, and the formal prices file
  hash, writes isolated performance/report status, and reuses only exact current
  completed evidence.
- Extended the single-instance history daemon to launch bounded fixed-strategy
  validation after authoritative data completion, retry validation failures, and
  exit only after validation succeeds.
- Preserved redirected child-process exit codes by acquiring the Windows process
  handle before polling; successful exit code 0 is no longer serialized as null.
- Added ADR 0004 and updated the architecture and Chinese history runbook.

**Architecture and safety**

- Data completion and strategy validation are separate ordered states. Neither
  grants candidate promotion, strategy activation, paper/live order authority,
  broker access, live-ledger initialization, or submission.
- history-validate rejects non-research configuration, active-execution
  application, broker submission, inactive emergency stop, shared main/live
  outputs, live-ledger paths, incomplete progress, remaining symbols, or any
  reported fetch failure.
- The isolated validation explicitly fails if generic orders.csv or
  rpa_orders.csv appears.
- Broker submission and service submission remained disabled, emergency stop
  remained active, and all audited live-ledger, certification, activation, and
  generic order paths remained absent.

**Verification**

- Focused documentation, history validation, data, and CLI regression:
  153 passed in 3.45s.
- Full regression: 1047 passed, 39 subtests passed in 156.83s.
- The real history-validate command rejected current progress with exit code 1:
  status in_progress, completed 122/800, remaining 678, failures 0.
- A real slow BaoStock batch reached the 30-minute cap, was terminated with exit
  code 124, preserved 122/800 validated progress, and returned the daemon to
  waiting instead of leaving a permanent running lock.
- The current-user Startup entry was replaced and a fresh daemon heartbeat was
  verified with the 120-minute fixed-strategy validation bound.
- A minimal redirected PowerShell child reproduced null for a successful exit;
  acquiring its Process.Handle preserved exit code 0, and both daemon child
  paths now use that verified pattern.
- The restarted daemon recorded last_exit_code 0. Strict resume validation now
  reports 120/800 complete; a 25-symbol source failure remained in_progress with
  680 remaining and will retry instead of restoring the earlier optimistic
  count.
- Current strategy status remains successor_observation with common formal
  forward progress 4/60, strict_live_allowed false, and paper orders paused.

**Remaining work**

- Continue bounded history acquisition and run the automatic fixed-strategy
  validation when all 800 symbols and final sources complete.
- Do not promote from the extended historical result. Continue the preregistered
  60-day common forward window and reassess only after its evidence is current.
- Refresh the authenticated broker account read-only snapshot before any later
  Canary review. Capital feasibility, account binding, live-ledger
  initialization, certification, and all submit gates remain incomplete.
## 2026-08-10 - Add resumable long-history validation

**Objective**

Extend the fixed strategy's independent historical sample without tuning it,
blocking daily operation, or allowing incomplete public-source data to become
formal research evidence.

**Changes**

- Added listing-date-aware backward gap detection to incremental market
  acquisition.
- Added bounded historical request budgets, validated partial resume, source
  provenance preservation, current-batch failure replacement, and explicit
  progress artifacts.
- Isolated extended-history staging from daily refresh through a validated
  configuration namespace.
- Added a research-only extended-history configuration fixed from 2018-01-01
  through 2026-08-07.
- Added a single-instance, 30-minute-bounded local daemon and current-user
  Startup installer. It processes 25 unfinished symbols per hour, yields during
  daily observation hours, and exits automatically after completion.
- Added ADR 0003, architecture ownership rules, and a Chinese operating runbook.

**Architecture and safety**

- Incomplete history is replaceable staging only. The formal prices cache is
  committed only after the full requested scope completes and the normal fetch
  success threshold passes.
- Staging prices, provenance, failures, and progress share one research
  namespace, so daily refresh cannot overwrite this job's resumable state.
- The job is research only: it cannot tune or promote a candidate, generate
  execution orders, initialize a live ledger, access a broker, or enable
  submission.
- Broker submit remained disabled, emergency stop remained active, service
  submit remained disabled, and all audited protected live/order artifacts
  remained absent.

**Verification**

- Data and CLI focused regression: 142 passed in 3.25s.
- Documentation governance: 3 passed in 0.12s.
- Full regression: 1039 passed, 39 subtests passed in 156.25s.
- PowerShell daemon and installer parsed successfully; installer preview and
  dry-run lock/status lifecycle both exited 0.
- The first real bounded daemon batch advanced validated progress from 98/800
  to 122/800 with zero failures in about two minutes.
- Formal prices.parquet retained its 2026-08-09 20:40:03 timestamp while the
  namespaced staging files advanced on 2026-08-10.
- Current-user Startup installation was verified with a live daemon heartbeat.

**Remaining work**

- Let the bounded daemon finish the remaining historical scope; do not describe
  the strategy as long-history validated before progress is complete.
- After completion, audit source/date coverage and run the fixed strategy with
  the isolated extended-history configuration. Do not use the result to rewrite
  the frozen forward candidates.
- Continue the untuned 60-day common forward observation. Strategy health still
  blocks paper and live orders.

## 2026-08-09 - Make capital operating mode current and operator-visible

**Objective**

Prevent stale broker balances or stale small-capital research from overstating
Canary or full-strategy readiness, and expose one clear capital conclusion in
the local operator status and Chinese report.

**Changes**

- Made broker snapshot freshness clear both effective Canary and full-strategy
  capital readiness in `capital_modes.py`.
- Added manifest-bound small-capital sensitivity currentness to the deployment
  gate; stale research may remain visible but cannot permit full-strategy review.
- Added the four-state capital operating mode to one-click local status and the
  Chinese HTML report.
- Refreshed the research-only 2,500 to 600,000 yuan board-lot sensitivity study
  against the current 2026-08-07 market-data fingerprint.

**Architecture and safety**

- Capital mode is explanatory derived evidence, not execution authority. It
  never adapts the frozen strategy, enables submission, creates orders, or
  initializes a ledger.
- A stale account snapshot always yields `refresh_required`; stale sensitivity
  evidence blocks full-strategy review while preserving the independent nature
  of a separately authorized one-lot Canary.
- Broker submission remained disabled, emergency stop remained active, and no
  live ledger, activation certificate, generic order, or RPA order was created.

**Verification**

- Focused capital, local operator, report, and deployment tests: `112 passed in
  7.25s`.
- Full regression: `1031 passed, 3 skipped, 39 subtests passed in 155.45s`.
- Current sensitivity audit passed exact reference market-data binding. The
  2,500 to 30,000 yuan levels made no trades; 50,000 yuan remained 99.25% cash;
  100,000 yuan ended with one position; 600,000 yuan was the first configured
  level passing the research threshold with 54 final positions.
- Current account mode is `refresh_required` because the last broker snapshot is
  stale. Effective Canary readiness and full-strategy readiness are both false.
- Playwright CLI launch was attempted for visual inspection but the installed
  Chrome process closed during startup; generated HTML content and render-health
  checks remain part of regression verification.

**Remaining work**

- Refresh the Huatai account only after an authenticated client session is
  available; keep this a read-only action.
- The 600,000 yuan research threshold is not the 100-position full-strategy
  replication threshold. Current broker feasibility evidence still requires
  approximately 2.702 million yuan for the frozen full strategy.
- Continue the untuned 60-day forward observation and extend independent history
  before any strategy promotion review.

## 2026-08-09 - Bind derived evidence and harden recovery commits

**Objective**

Make the one-click research workflow produce one coherent, current evidence set and remove a recurring Windows recovery-point commit failure without enabling trading.

**Changes**

- Added exact main-run market-data binding and stale-evidence detection to capital sensitivity; isolated capital runs are reused only when configuration and market fingerprints both match.
- Added automatic capital-sensitivity refresh, dependency-ordered readiness/health generation, and visible stage progress to the research and practical workflows.
- Centralized latest-completed-fetch scope filtering and applied it consistently to backtest, readiness, and cost-stress hashes.
- Added bounded retry for transient Windows access-denied errors during recovery directory commit, while refusing to overwrite any existing destination.
- Added ADR 0002, architecture rules, README guidance, and focused failure-path tests.

**Architecture and safety**

- Derived research evidence that can affect execution readiness now binds to the current main `run_manifest.csv`; historical capital audits without the new fingerprint remain readable but cannot pass the current practical gate.
- Research workflows remain unable to generate generic execution orders or call the broker boundary.
- Recovery retry applies only while staging exists and the final destination does not; unknown, exhausted, missing-source, or existing-destination states fail closed.
- Broker submission remains disabled, emergency stop remains active, and no live ledger, strategy activation, Canary certificate, generic order, or RPA order artifact was created.

**Verification**

- Focused research, workflow, recovery, and documentation tests: `176 passed in 6.09s`; recovery-specific tests: `9 passed in 1.30s`.
- The first full regression exposed two real Windows `WinError 5` directory-commit failures: `1025 passed, 2 failed, 3 skipped, 39 subtests passed`.
- After bounded retry hardening, full regression passed: `1029 passed, 3 skipped, 39 subtests passed in 153.39s`.
- Real four-level capital sensitivity used one current market fingerprint with zero reused stale levels. Only `1,000,000` yuan passed; `600,000` yuan had `-17.78%` return drag, and the lower levels failed diversification/cash-drag constraints.
- The refreshed practical gate passed `26/26`; factor robustness passed. The high-cost scenario retained `72.99%` historical excess return with `-28.45%` maximum drawdown.
- Current strategy health still blocks orders: reliability `54/70`, bootstrap lower 95% excess `-20.51%`, early forward degradation, and adverse effective factor weight `75%` after 24 monitored days.

**Remaining work**

- Keep paper and live orders paused. The current account is far below the audited full-strategy capital requirement; a one-lot Canary can validate execution mechanics only.
- Continue untuned forward observation to 60 common trading days and reassess the frozen candidates; do not select the higher-backtest adaptive candidates from this cache.
- Extend the independent historical sample beyond the current 750 trading days and resolve bootstrap uncertainty before promotion review.
## 2026-08-09 - Restore unattended observation and bind research to committed data scope

**Objective**

Restore reliable unattended observation, refresh the public research cache, and prevent retained historical symbols or backtest artifacts from weakening data audits or entering an execution path.

**Changes**

- Added verified current-user Startup installation and live-heartbeat checks for the observation supervisor; fatal supervisor failures now publish a failed status.
- Made BaoStock the default history source and added freshness/coverage gates before reusing cached stock metadata.
- Bound source-health checks and backtests to the exact symbol set in the latest completed fetch manifest.
- Separated research orders into `backtest_orders.csv`; backtests no longer write the generic `orders.csv` or `rpa_orders.csv` execution artifacts.

- Updated operator diagnostics, tests, README output guidance, and architecture artifact ownership rules.

**Architecture and safety**

- A stale status JSON file is not accepted as proof that unattended observation is running; both a verified Startup entry and a fresh live process heartbeat are required.
- Research outputs cannot create generic execution-order artifacts. Broker submission remains disabled, the emergency stop remains active, and no live ledger, activation certificate, active execution strategy, or live/RPA order file was created.
- The frozen candidates were observed without changing their identities or tuning them against the newly refreshed data.

**Verification**

- Manual daily observation completed in `730.601` seconds; the three frozen candidates reached `8/60`, `7/60`, and `4/60` observations, with only `5/60` common post-policy trading days.
- Public fetch completed for `800/800` symbols through `2026-08-07`, with zero failures, reported BaoStock amount data, and `9/9` source-health checks passing.
- The committed-scope backtest covered `750` trading days from `2023-07-06` through `2026-08-07`: total return `97.07%`, annualized return `25.64%`, maximum drawdown `-28.35%`, benchmark return `22.16%`, and excess return `74.91%`. These figures are research evidence, not a live-readiness claim.
- Final full suite: `1022 passed, 3 skipped, 39 subtests passed in 296.44s`.
- Protected live and generic execution paths were audited absent after the run.

**Remaining work**

- Do not promote the strategy: the common formal forward window is only `5/60`, `optimized_on_current_cache` remains true, and bootstrap/factor-robustness gates still fail.
- Continue daily observation until the full untuned forward window is available, then reassess stability, excess return, drawdown, turnover, and factor degradation.
- The current-user Startup mechanism requires Windows logon and is not equivalent to an operating-system service.
## 2026-08-09 - Add private GitHub source synchronization

**Objective**

Publish the source to a private GitHub repository and provide AI-independent daily synchronization without uploading market, account, broker, order, or secret data.

**Changes**

- Hardened `.gitignore` for generated outputs, local state, credentials, logs, and tool caches.
- Added a fail-closed staged/tracked-file scanner in `scripts/git_sync_preflight.py`.
- Added allowlisted, non-force Git synchronization in `scripts/sync_github.ps1`.
- Added `scripts/install_github_sync_task.ps1` for a daily 22:30 Windows task.
- Added Chinese operating guidance in `docs/GITHUB_SYNC.md` and linked it from README.

**Architecture and safety**

- Source synchronization is infrastructure only and does not change strategy, broker, ledger, or submission capability.
- The task refuses pre-existing staged work, remote divergence, sensitive paths/content, and oversized/binary files.
- Runtime artifacts remain local and are not treated as source backups.

**Verification**

- `.\.venv\Scripts\python.exe -m pytest tests\test_git_sync_preflight.py tests\test_documentation_governance.py -q`: `7 passed in 0.20s`.
- `.\.venv\Scripts\python.exe -m pytest -q`: `1017 passed, 3 skipped, 39 subtests passed in 284.28s`.
- `github_sync_daemon.ps1 -RunOnce`: completed with exit code `0`, preserved the last-run audit fields, and pushed through the repository-scoped SSH key.
- Startup verification: one daemon process, state `waiting`, schedule `22:30`, next run `2026-08-09T22:30:00+08:00`, last exit code `0`.
- Private repository `maybehell/ashare-quant` was created and initial commit `f576ce3` was pushed over a repository-scoped SSH Deploy Key.
- Windows Task Scheduler registration was denied by the host; a non-admin, single-instance Startup daemon was added as the supported local fallback.
- The non-admin Startup daemon was installed, exercised, restarted, and verified as a single waiting process for the daily 22:30 schedule.

**Remaining work**

- Observe the first naturally scheduled 22:30 run in `Logger/github_sync.log`; no implementation blocker remains.

## 2026-08-09 - Establish architecture and development governance

**Objective**

Create one clear architecture plan and a development log that every later change
must follow.

**Changes**

- Added root `AGENTS.md` as the mandatory contributor/agent entry point.
- Added `docs/ARCHITECTURE.md` with current layers, dependency rules, artifact
  ownership, live-safety invariants, canonical workflows, and evolution plan.
- Added `docs/DEVELOPMENT_PROCESS.md` with change classification, verification
  matrix, completion review, and log template.
- Added ADR governance and the initial architecture decision under `docs/adr/`.
- Linked governance documents from `README.md` and added an automated presence
  and cross-link test.

**Architecture and safety**

- Documentation formalizes current fail-closed behavior; it does not enable
  broker submission, initialize a live ledger, activate a strategy, or create an
  order.
- Existing oversized orchestration/rendering modules are documented as P1
  evolution work rather than falsely represented as already decomposed.

**Verification**

- `.\.venv\Scripts\python.exe -m pytest tests\test_documentation_governance.py -q`:
  `3 passed in 0.13s`.

**Remaining work**

- Keep this log current for every subsequent repository modification.
- Accumulate formal forward strategy evidence and complete the separately
  authorized live-readiness process before any broker Canary.

## 2026-08-01 - Reconstructed safety and observation milestone

This entry is reconstructed from current source, configuration, generated
artifacts, and prior test output because a development log did not yet exist.
It intentionally avoids claiming details that are not preserved by those sources.

**Verified milestone summary**

- Huatai desktop read-only snapshots, history, adapter integrity, account
  binding, Canary certification, incident alerting, recovery, staged rollout,
  submit/query/cancel/fill, and unattended-service controls exist as separate
  components and CLI commands.
- Daily observation supports broker read-only refresh without enabling broker
  submission.
- Canary cash buffers are rollout-stage aware while explicit risk settings can
  remain stricter.
- Chinese reports include benchmark comparison and live-safety audit labels.
- The controlled Huatai configuration keeps broker submission disabled,
  emergency stop active, and unattended submission disabled.

**Known state at that milestone**

- The formal candidate forward window had not completed its required 60 trading
  days.
- Live ledger initialization and a real broker Canary remained separate,
  explicitly authorized future actions.

## Earlier development

The repository history before this log is represented by code, tests, README,
specialized documents, and generated manifests. Future work must not invent
retroactive entries; add a reconstructed entry only when its evidence source and
uncertainty are stated.
