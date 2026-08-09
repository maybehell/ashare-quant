# Development Log

This is the chronological engineering record for the repository. New entries are
added directly below this introduction, newest first. It records verified facts;
it is not a release-marketing document and does not replace generated audit
artifacts.
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
