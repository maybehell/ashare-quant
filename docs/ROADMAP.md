# AShare Quant Development Roadmap

Status: authoritative delivery plan

Last reviewed: 2026-08-22

## 1. Purpose

This roadmap turns the architecture into ordered, evidence-gated delivery work.
It does not authorize live trading. `docs/ARCHITECTURE.md` remains authoritative
for boundaries and invariants; this file is authoritative for sequencing,
current milestones, and exit criteria.

Progress is gate-based rather than date-based. A phase is complete only when
current authoritative artifacts prove every exit criterion. Work may proceed in
parallel where dependencies allow, but a later phase cannot grant authority that
an earlier phase has not earned.

## 2. Fixed constraints

- Target market: Shanghai and Shenzhen A-shares at daily frequency.
- Intended account size: no strategy portfolio above CNY 200,000; primary
  operating range CNY 50,000-100,000.
- Default behavior remains fail closed.
- Research, paper trading, broker read-only access, live-ledger initialization,
  order preview, and submission are separate capabilities.
- No strategy is required or represented as guaranteed to outperform a
  benchmark.
- A frozen candidate is never tuned using its forward observation results.
- Desktop automation remains subordinate to broker evidence and reconciliation;
  it cannot infer a successful order from a click.

## 3. Current baseline

As of 2026-08-22:

| Area | Current evidence | State |
|---|---|---|
| Local operation | Startup entry plus one supervisor and one daemon | operating |
| Market history | 800/800 cached symbols | complete for the bound cache |
| Formal candidate | `d55d124b5800e123`, exact history decision and permanent record bound | rejected |
| Candidate history validation | matching 1,965-day evidence completed; performance thresholds failed | blocked |
| Successor research | 52-week-high experiment 1 rejected; optional point-in-time turnover capability added but current formal cache has no turnover field | data refresh required; no new experiment registered |
| Broker read-only | Huatai process found; trading UI not loaded | blocked |
| Account binding | 8/13 checks | blocked |
| Live strategy authority | no active execution strategy | absent by design |
| Live ledger and orders | no live ledger and no generic order artifact | absent by design |
| Submission controls | emergency stop on; submit and service off | safe default |

Generated gate artifacts, not this snapshot, determine current readiness.

## 4. Delivery phases

### P0 - Preserve the safe operating baseline

Objective: keep unattended observation reliable without adding trading authority.

Deliverables:

- Current configuration-role audits for observation, historical research, and
  broker-live configuration.
- Verified Startup supervisor/daemon identity and visible failure diagnostics.
- Protected-artifact and safety-switch audit in every live-boundary change.

Exit criteria:

- `safe_default=true` and all submission controls remain closed.
- Daily observation is recoverable after reboot and cannot create broker orders.
- Full regression remains green for shared or live-boundary changes.

### P1 - Establish strategy evidence for the current candidate

Objective: determine whether the frozen small-capital candidate deserves further
review without reusing in-sample tuning as proof.

Deliverables:

- Untuned 60-session forward observation for candidate `d55d124b5800e123`.
- Independently governed long-history validation with the exact candidate
  strategy identity, point-in-time inputs, benchmark mapping, fees, slippage,
  price limits, suspensions, and T+1 behavior.
- Walk-forward and regime breakdowns, concentration, turnover, capacity,
  drawdown, and benchmark-relative attribution.
- A signed promote, extend-observation, or reject decision that does not alter
  the frozen evidence.

Exit criteria:

- 60/60 valid post-freeze sessions with no identity or timing mismatch.
- Historical and forward artifacts match the candidate hash and pass the
  preregistered thresholds.
- Repeated runs are deterministic and no-future-data tests pass.
- Failure produces rejection or a newly preregistered successor, never a lowered
  threshold or retroactive edit.

### P2 - Prove small-capital executability

Objective: prove that a strategy passing research gates can actually be traded
with no more than CNY 200,000.

Deliverables:

- Lot-aware sensitivity runs at CNY 50,000, 100,000, 150,000, and 200,000.
- Portfolio construction that reports cash drag, integer-weight error, minimum
  viable capital, fees, slippage, liquidity participation, and concentration.
- Stress scenarios for limit locks, suspensions, partial fills, delayed exits,
  and adverse execution cost.

Exit criteria:

- At least one approved operating capital tier passes the frozen allocation,
  liquidity, concentration, turnover, and cash-buffer limits.
- The recommended portfolio never assumes capital above CNY 200,000.
- Benchmark-relative results remain acceptable after conservative costs; there
  is no requirement or promise of guaranteed excess return.

### P3 - Certify broker read-only operation and account truth

Objective: maintain a current, reproducible view of the real account without
creating financial state or orders.

Deliverables:

- Reliable Huatai login, notice handling, trading-root identification, and
  `funds-and-positions` navigation.
- Fresh cash, positions, orders, trades, cancels, and broker clock evidence.
- Current account binding, adapter pin manifest, read-only replay certification,
  and email-alert self-test.
- A documented manual recovery path when the desktop client changes layout or
  cannot be identified.

Exit criteria:

- `broker_read_only_ready=true` across a defined soak period and after reboot.
- Snapshots reconcile to visible broker totals and carry current account,
  environment, run, timestamp, and adapter identities.
- Read-only refresh creates neither the live ledger nor an order artifact.

### P4 - Run shadow execution and reconciliation

Objective: exercise the complete order lifecycle without broker submission.

Deliverables:

- Candidate-to-execution identity review and signed activation proposal.
- Explicit live-ledger initialization preview and account-import reconciliation.
- Daily shadow orders with provenance, risk decisions, expected fills, broker
  comparisons, and end-of-day reconciliation.
- Crash/restart, duplicate suppression, unknown-outcome, cancel, and settlement
  recovery drills.

Exit criteria:

- A minimum 20-session shadow soak has no unexplained cash, position, order, or
  identity mismatch.
- Every order is reproducible from frozen strategy evidence and current account
  state.
- Recovery drills pass without invoking broker submission.

### P5 - Request one small Canary

Objective: request, but never silently grant, authority for one controlled real
order of at most CNY 1,000.

Deliverables:

- Current signed rollout approval and account binding.
- Fresh broker and alert evidence, valid order provenance, inactive emergency
  stop review, and a short-lived single-use confirmation.
- Pre-submit preview, human review, post-submit query, fill/fee reconciliation,
  and incident rollback procedure.

Exit criteria:

- `ready_to_request_canary=true` before any operator authorization.
- One explicitly authorized order reaches a known terminal state and reconciles
  exactly; a timeout remains an unknown outcome until queried.
- Emergency stop, restart recovery, and alerts are verified after the exercise.

This phase requires a new explicit user authorization. Completing earlier
phases does not authorize submission.

### P6 - Limited live operation

Objective: operate a tightly capped strategy with continuous reconciliation and
an immediate return to safe default on uncertainty.

Deliverables:

- Staged limits for order count, gross buy amount, cash buffer, and daily loss.
- Health, latency, data freshness, alert, and reconciliation service objectives.
- Backup/restore and operator incident runbooks with periodic drills.

Exit criteria:

- Canary evidence remains current and a separate limited-rollout approval exists.
- No unresolved reconciliation, unknown outcome, stale evidence, or integrity
  mismatch exists.
- Any breached invariant automatically blocks new submissions.

## 5. Priority work queue

Work is selected from the earliest phase whose exit criteria are not met:

1. Keep daily observation and protected-state audits healthy.
2. Define an independently motivated, preregistered successor research phase;
   do not continue tuning the exhausted four-test family or inherit the rejected
   candidate's authority.
   The first 52-week-high experiment is rejected, and the February result is not
   translated into an unsupported cash-timing rule. Build an isolated,
   provenance-complete turnover cache, audit its point-in-time coverage, then
   decide whether to preregister a separate low-turnover family. Do not evaluate
   the new factor against the current cache before registration.
3. Accumulate a genuine 60-session forward window only for a successor that
   first passes its independent history gate.
4. Complete the four-tier small-capital evidence for the approved candidate.
5. Restore and soak Huatai read-only account evidence.
6. Implement P4 shadow reconciliation only after P1-P3 gates pass.
7. Do not initialize the live ledger or request Canary authority before P4 exits.

## 6. Change governance

Every repository change must identify its roadmap phase in the development log.
If work changes a phase boundary, exit criterion, capital ceiling, evidence
authority, or live-safety policy, update this roadmap and the architecture in the
same change and add or supersede an ADR.

Roadmap progress is derived from current generated evidence. Never mark a phase
complete by editing this document alone.
