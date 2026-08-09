# ADR 0005: Constrain unattended Huatai read-only desktop recovery

Status: accepted

Date: 2026-08-10

## Context

The daily observation scheduler can refresh Huatai account evidence without an
operator, but a running client may expose only the market window, a minimized
trading window, or a trading login page. Treating all of these states as a
missing trading page caused repeated polling, stale reports, and ambiguous
operator guidance. Launching another client process could also create multiple
windows and make desktop identity ambiguous.

Read-only continuity is useful, but desktop recovery must not turn account
observation into credential automation, ledger initialization, or order
submission.

## Decision

1. Scheduled broker read-only refresh runs after market observation and before
   report, local status, and alert rendering.
2. The broker refresh owns its success or failure status for that run. A normal
   local-status pass does not overwrite broker-refresh failure evidence.
3. Desktop recovery accepts only the configured executable after SHA-256
   validation and requires one pinned Huatai market process and one same-folder
   trading process.
4. Recovery first restores one uniquely identified minimized trading root. If
   none exists and configuration explicitly permits it, it sends exactly one
   F12 navigation key to one uniquely identified visible market root.
5. Existing Huatai processes block a fallback duplicate launch when restore and
   navigation cannot be proven safe.
6. A detected trading login signature is reported as
   `broker_authentication_required` and exits without retrying. Credentials and
   login confirmation remain manual.
7. Refresh audit records whether window recovery was attempted, its exact
   result message, and the number of snapshot attempts.
8. Read-only recovery never initializes a live ledger, creates an execution
   order, submits/cancels an order, or changes submission safety switches.

## Consequences

Positive:

- Daily reports and email alerts reflect the latest broker-readonly result.
- Expired trading sessions fail quickly with an actionable reason.
- Duplicate desktop instances and ambiguous roots remain blocked.
- UI recovery is auditable and remains separate from authentication and orders.

Costs:

- The operator must manually authenticate the Huatai trading session when it
  expires.
- A client UI or automation-ID change may conservatively block refresh until the
  adapter and tests are updated.
- Changing the adapter or desktop configuration invalidates stale Canary
  certification and requires separate re-certification before any future
  submission review.

## Alternatives considered

### Automate the trading login

Rejected because it expands read-only observation into credential entry and a
security-sensitive confirmation action.

### Launch the client again whenever no trading grid is visible

Rejected because duplicate processes and roots make broker identity and UI
state ambiguous.

### Keep polling without classifying login state

Rejected because it delays alerts and obscures the actual operator action.

### Run broker refresh after report generation

Rejected because the report and alert would describe stale broker evidence for
that daily run.