# ADR 0006: Persist source-outage backoff for bounded history acquisition

Status: accepted

Date: 2026-08-10

## Context

The research-only long-history daemon processes a bounded symbol batch and
preserves validated partial prices. During a BaoStock service outage, every
symbol in a batch returned the same session-level receive error. The data command
still exited successfully because it had safely written resumable progress, so
the daemon reported an ordinary waiting state and retried the same unavailable
source every hour. Restarting the daemon also discarded any operational delay.

A source outage is different from a symbol-level empty or malformed history.
Conflating them hides source health, wastes requests, and makes progress appear
healthier than it is. It must not weaken the authoritative completion rule or
cause incomplete data to enter the formal cache.

## Decision

1. Every bounded progress record includes `source_status`, attempted-symbol
   count, and successful-symbol count.
2. A batch with zero successes is `source_unavailable` only when every attempted
   symbol carries a BaoStock session-attempt error. Mixed or symbol-specific
   failures remain `failed`; partial success is `degraded`.
3. The data command still writes validated resumable staging atomically, but
   source unavailability never advances completion.
4. The history daemon converts `source_unavailable` to audit exit code 75 and a
   four-hour bounded retry delay.
5. The daemon status records failure streak, backoff duration, and next run. A
   future persisted next-run time is honored after daemon or Windows restart,
   including in single-run mode, without making a network request.
6. A successful or non-source-outage batch clears the in-memory backoff state.
7. Local Chinese operator status displays the research outage and explicitly
   states that the formal cache was not replaced. This research item does not
   grant or remove execution authority.

## Consequences

Positive:

- Source outages are distinguishable from bad individual securities.
- Repeated restarts cannot hammer an unavailable public source.
- Operators can see exact attempted/successful counts and the next retry time.
- Existing validated staging and the authoritative completion boundary remain
  unchanged.

Costs:

- A transient recovery can go unused until the bounded backoff expires.
- BaoStock error wording changes may conservatively classify an outage as a
  generic failed batch instead of enabling the longer backoff.
- Progress artifacts gain optional columns; historical readers must continue to
  tolerate older records without them.

## Alternatives considered

### Treat a safely written failed batch as daemon success

Rejected because persistence success is not market-source success.

### Retry every symbol or reconnect indefinitely

Rejected because it extends runtime, hides a source-wide incident, and increases
load without improving evidence.

### Delete partial staging and restart the full scope

Rejected because validated symbol histories remain useful and deletion would
reduce recoverability.

### Fall back automatically to another unverified public source

Rejected for this change because provider adjustment, amount, and point-in-time
semantics require separate provenance and validation before formal use.