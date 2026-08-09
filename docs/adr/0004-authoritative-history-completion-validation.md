# ADR 0004: Validate fixed strategy only after authoritative history completion

Status: accepted

Date: 2026-08-10

## Context

The bounded historical backfill originally wrote a complete progress row before
the formal market-cache write finished. It also inherited the normal market
refresh success-ratio policy, which could allow a final bounded batch with
missing symbols or failed benchmark acquisition to appear complete. A daemon
that trusted either state could stop too early or start a long-history backtest
against incomplete evidence.

Completing the data is also not the same event as validating the strategy.
Running a backtest automatically is useful, but automatic candidate promotion or
execution authorization would violate the separation between research evidence
and strategy authority.

## Decision

The historical completion and validation workflow uses two ordered states:

1. A bounded data run remains in_progress while any requested symbol is missing
   or any final acquisition stage reports a failure.
2. The data layer writes formal market outputs first and writes progress
   complete last. A failed formal write cannot leave a new complete marker.
3. The local daemon treats complete as permission to run only the isolated
   history-validate research command.
4. history-validate requires research_only configuration, disabled broker
   submission, active emergency stop, isolated output and state paths, zero
   remaining symbols, and zero failures.
5. The validation status binds the progress generation time, fixed backtest
   configuration hash, and byte hash of the formal prices file.
6. Exact completed evidence is reusable. Changed data, configuration, progress,
   or a missing report forces a new validation.
7. Validation may generate backtest outputs and a report only. Generic execution
   orders, candidate promotion, strategy activation, broker access, live-ledger
   initialization, and submission remain forbidden.
8. The daemon records validation failure or timeout and retries later. It exits
   successfully only after the fixed-strategy validation completes.

## Consequences

Positive:

- A completion marker proves the formal cache commit occurred.
- Missing symbols and transient benchmark failures remain retryable.
- Long-history evidence is generated automatically without granting authority.
- Repeated Windows logons do not rerun an unchanged completed validation.
- Operators can distinguish data completion from fixed-strategy validation.

Costs:

- A final source failure can delay completion even when most prices are present.
- Byte-level parquet changes conservatively trigger revalidation.
- Historical completion now has an additional bounded backtest stage before the
  temporary daemon exits.

## Alternatives considered

### Trust the normal fetch success ratio

Rejected because a long-history validation scope is explicit; partial success
cannot prove that exact scope.

### Write complete before formal outputs

Rejected because an interrupted final commit would leave false authority.

### Automatically promote a strategy after a passing long-history backtest

Rejected because the historical sample was selected for research and cannot
replace preregistered forward evidence or human governance.

### Require a human to launch the backtest after data completion

Rejected as the only path because it makes routine evidence generation depend
on operator presence. The automated research command remains bounded and
non-authoritative.
