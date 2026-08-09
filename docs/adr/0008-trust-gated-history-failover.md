# ADR 0008: Trust-gate historical failover and checkpoint provenance first

Status: accepted

Date: 2026-08-10

## Context

The independent long-history job was blocked by a BaoStock session outage.
AkShare already exposed multiple historical providers. A local probe showed that
Sina returned full 2018-2026 adjusted history with reported volume and amount,
while Tencent required estimated amount. Simply changing the provider would
have improved availability but could silently mix adjustment bases, field
units, and untrusted prior staging.

The existing checkpoint wrote partial prices without the matching provenance.
A bounded process timeout could therefore preserve new rows but lose the source
records needed to trust and resume them. Audit of the current staging found 97
symbols marked `resumed_partial_unknown_source` and one symbol with estimated
amount. These records were structurally valid but insufficient for independent
strategy validation.

## Decision

1. AkShare history failover is ordered as Eastmoney, Sina, then Tencent. The
   existing optional BaoStock fallback remains separately configurable.
2. Sina uses `stock_zh_a_daily` adjusted prices and its reported volume and
   amount fields. It is identified explicitly as `stock_zh_a_daily` in
   per-symbol provenance.
3. Alternate source rows are compared with the latest configured number of
   common committed dates. Median relative errors for close, volume, and amount
   are recorded and must remain within configured limits.
4. The isolated extended-history job uses the latest 10 common dates, rejects
   estimated amount, and resumes only rows whose provenance explicitly records
   reported amount. Unknown or estimated prior staging is reacquired and no
   longer counts as completed.
5. Checkpoints write provenance first and prices second. BaoStock progress
   callbacks carry both frames and provenance. A crash may leave unused
   provenance, but cannot make a price frame resumable without its source.
6. The extended-history job disables BaoStock fallback while that provider is
   known unavailable. This does not alter the default daily configuration and
   can be reviewed separately after provider recovery.
7. Source outage backoff records a source-plan identity derived from both the
   default and override configuration files. A changed plan does not inherit an
   old provider outage delay; an unchanged plan does.
8. Source selection, overlap windows, trust requirements, and retry controls are
   acquisition identity rather than strategy identity. Actual accepted rows
   remain bound by data and run-manifest hashes.
9. The unattended daemon may schedule the next bounded batch after 15 minutes
   only when the completed batch exits zero, reports source status `ok`, has no
   failed items, attempts at least one symbol, and succeeds for every attempt.
   Any other non-outage result uses the regular 60-minute interval, while a
   source-wide outage retains the persisted 240-minute backoff.

## Consequences

Positive:

- Independent history can progress without weakening amount quality.
- Provider unit or adjustment mismatches fail before entering resumable staging.
- Unknown-source staging can no longer inflate completion.
- Process timeouts preserve only work with matching provenance.
- Switching to a healthy source is not blocked by stale outage state.
- A healthy public source can complete independent evidence collection in
  bounded 25-symbol batches without waiting an hour between successful batches.

Costs:

- Trusted completion fell from an apparent 139 symbols to 52 when unknown and
  estimated staging was removed; these symbols must be reacquired.
- Recent overlap validation proves current provider semantics, not the absence
  of every historical vendor correction. Full-history diagnostics and fixed
  strategy validation remain required after completion.
- Disabling BaoStock fallback can leave source-specific failures for a later
  batch instead of trying every provider in one run.
- The accelerated interval increases request cadence while healthy. The
  25-symbol budget, 15-minute lower bound, quiet window, observation lock,
  regular fallback interval, and source-outage backoff limit that cost.

## Alternatives considered

### Accept Tencent estimated amount for all symbols

Rejected because adjusted close multiplied by volume can distort historical
liquidity around corporate actions and would dominate the independent sample.

### Trust structurally valid staging without source records

Rejected because schema validity is not evidence of provider, adjustment, or
amount semantics.

### Validate against the entire mixed historical cache

Rejected because the current cache contains provider-era differences. Recent
committed overlap is used to validate current field units; final long-history
validation remains separate.

### Write prices before provenance

Rejected because a timeout between those writes can create unproven resumable
prices.
