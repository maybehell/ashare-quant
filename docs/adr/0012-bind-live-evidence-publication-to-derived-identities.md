# ADR 0012: Bind live evidence publication to derived identities

Status: accepted

Date: 2026-08-11

## Context

The live audit publisher previously checked that the source run manifest matched
the current market cache and that required filenames existed. That proved the
base backtest identity, but it did not prove that derived evidence had been
recomputed from the same data. A current backtest could therefore coexist with
an old cost-stress file, while factor-robustness evidence was not copied to the
live workspace at all.

The publisher also verified each copied file independently. It had no single
identity for the set of source files, so an operator could not prove that every
row in a successful sync came from one stable source bundle.

## Decision

1. Required factor-robustness detail and summary files are included in the
   allowlist and required live strategy evidence set.
2. Strict readiness compares the factor-robustness data fingerprint with the
   exact frames the current configuration would load, including a forward-only
   candidate's freeze boundary.
3. Before publication, the source must pass current factor candidate, config,
   data, and forward-isolation checks plus current cost-stress config and data
   checks whenever those gates are enabled.
4. The publisher snapshots every allowlisted source file hash and derives one
   `evidence_bundle_sha256`. Every copied audit row and the success summary carry
   that bundle identity.
5. A source file that changes before, during, or after its copy aborts the
   publication. A success summary is written only after destination base and
   derived identity verification passes.
6. Evidence publication remains read-only with respect to trading: orders,
   positions, account state, broker access, ledger initialization, and broker
   submission are outside this capability.

## Consequences

Positive:

- A current base manifest can no longer make stale cost or factor evidence look
  current in the live audit workspace.
- The live report receives the factor evidence required by its own strict gate.
- One bundle hash identifies the complete copied set and detects mixed source
  generations.
- Existing strategy-quality failures remain visible without being confused with
  evidence-identity failures.

Costs:

- A strict evidence sync now requires factor robustness and cost stress to be
  regenerated after their effective data or configuration changes.
- Computing current factor identity reloads the exact research frames and adds
  time to strict readiness and publication.
- Flat-file destination compatibility is retained, so a process interruption
  can still leave copied files without a success summary. Such a state has no
  publication authority and must be replaced by a later successful bundle.

## Alternatives considered

### Trust the source run manifest alone

Rejected because derived artifacts can outlive the backtest that generated
their current base manifest.

### Copy factor files but treat their data hash as informational

Rejected because any 64-character historical hash would then satisfy the gate.

### Publish only a versioned directory and atomically switch a pointer

Deferred because existing reports and operator tools consume stable flat paths.
The bundle hash and success-summary rule add identity without a broad reader
migration; a versioned pointer can supersede this decision later.
