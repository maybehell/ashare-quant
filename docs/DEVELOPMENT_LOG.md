# Development Log

This is the chronological engineering record for the repository. New entries are
added directly below this introduction, newest first. It records verified facts;
it is not a release-marketing document and does not replace generated audit
artifacts.

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
