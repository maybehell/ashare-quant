# ADR 0029: Bind candidate history validation to shared immutable research input

Status: accepted

Date: 2026-08-13

## Context

The authoritative small-capital forward candidate has strategy identity
`1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9`, while
the completed 800-symbol historical experiment used a different strategy. The
candidate's forward cache contains only its point-in-time CSI 300 observation
universe and must not be replaced or expanded with the broader research cache.
Copying the completed history would duplicate a large dataset without improving
provenance, while silently sharing a mutable cache would make validation inputs
ambiguous.

## Decision

1. Candidate history validation inherits the frozen candidate configuration so
   its strategy identity cannot drift through duplicated strategy fields.
2. It may read the completed shared research cache only when a required input
   binding preregisters the resolved cache path, formal price file SHA-256,
   progress frame, completion timestamp, symbol counts, and requested interval.
3. The binding is checked before backtest output is written. Any mismatch fails
   closed and requires a new explicitly reviewed experiment configuration; the
   old binding is never silently repaired.
4. The input-binding identity is persisted in history validation status and is
   required for completed-result reuse.
5. Candidate forward cache, candidate history output, history state database,
   default outputs, broker outputs, and live ledger remain distinct owners.
6. History evidence may support rejection or later review but cannot promote the
   candidate, initialize a ledger, create execution orders, or call a broker.

## Consequences

- The completed 800-symbol cache can be reused without copying roughly tens of
  megabytes of prices or mutating candidate forward evidence.
- A later data refresh invalidates reuse instead of changing the experiment
  underneath an existing report.
- Legacy history status without a declared binding remains readable, but new
  candidate evidence records the binding identity explicitly.

## Alternatives considered

- Copy the entire shared cache: rejected because duplicate bytes do not create
  stronger provenance and introduce another mutable owner.
- Point the candidate forward configuration at the shared cache: rejected because
  it would contaminate forward-only state and violate ownership boundaries.
- Share the cache without a hash and progress binding: rejected because the same
  config could silently evaluate different historical inputs.
