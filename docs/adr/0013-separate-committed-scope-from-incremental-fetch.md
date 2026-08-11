# ADR 0013: Separate committed market scope from incremental fetch work

Status: accepted

Date: 2026-08-11

## Context

Forward-candidate refresh used one symbol list for two different meanings: the
complete universe authorized for downstream calculations and the smaller set
that still needed a network request. After the gap filter reduced an 800-symbol
candidate to ten pending symbols, the fetch summary and provenance files
published those ten as the latest committed scope. Paper and shadow logic could
then calculate a concentrated portfolio from the contracted universe.

A second defect regenerated the undated stock catalog and historical synthetic
fundamentals after new market metadata arrived. That changed inputs at or before
a candidate's freeze boundary. Frozen hashes detected the mutation, but the
candidate cache had already lost its original contents.

## Decision

1. A candidate refresh records a complete committed symbol scope separately
   from the incremental request subset. Fetch summaries expose requested and
   fetched counts for the committed scope plus attempted and refreshed counts
   for the current network work.
2. Per-symbol history provenance is retained across successful increments,
   restricted to the current committed scope, and replaced only for symbols
   refreshed in the current attempt.
3. A symbol that enters a dynamic committed universe without current provenance
   is part of the incremental gap even when its cached price date is current.
4. A complete summary passes only when every committed symbol has provenance,
   the stage is complete, and the current attempt has no failures.
5. Paper and shadow signal generation fail closed when the committed scope is
   missing or incomplete.
6. For an immutable candidate, the complete stock catalog and fundamentals at
   or before the freeze boundary are frozen inputs. Refresh may append only
   post-freeze prices, benchmark rows, and fundamentals. Missing frozen
   baselines abort the refresh.
7. A corrupted frozen identity is retired. Reconstruction occurs in an isolated
   research-only cache and receives a new candidate identity with no inherited
   forward observations or execution authority.

## Consequences

Positive:

- Small daily network requests no longer narrow the portfolio universe.
- Dynamic liquidity-ranked universes obtain provenance before new entrants can
  affect a signal.
- Frozen input hashes remain stable across metadata and fundamental refreshes.
- Invalid manifests cannot silently fall back to all cached symbols for paper
  or shadow evidence.
- Attempt size and committed-scope size are independently auditable.

Costs:

- Existing candidate caches with contracted or incomplete provenance require a
  controlled full-scope refresh before they can generate signals.
- A candidate whose frozen inputs were already changed cannot be repaired under
  the same identity; its forward window restarts under a replacement identity.
- Fetch summaries gain two columns and provenance files retain rows across
  increments, so readers must continue to use named columns rather than
  positional schemas.

## Alternatives considered

### Treat every incremental subset as a new committed universe

Rejected because request scheduling is operational state and must not redefine
strategy input scope.

### Rebuild provenance from cached prices alone

Rejected because price rows do not prove which source and validation policy
produced them.

### Allow shadow observation when the committed scope is invalid

Rejected because research evidence generated from an ambiguous universe could
later be mistaken for valid forward evidence.

### Repair frozen files and update their hashes

Rejected because that would rewrite the experiment after observations began and
could transfer evidence to a different data identity.
