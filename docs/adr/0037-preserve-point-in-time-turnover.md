# ADR 0037: Preserve point-in-time turnover before new research

Status: accepted

Date: 2026-08-22

## Context

The first independent 52-week-high experiment failed every preregistered
promotion dimension. George, Hwang, and Li report that 52-week-high momentum in
China becomes visible after excluding February returns, but excluding a return
observation in an academic long-short test is not equivalent to a deployable
long-only rule that liquidates in February. Translating it directly into
seasonal market timing would change the tested hypothesis.

Separate China evidence reports that low average daily share turnover predicts
higher future returns over a long sample. BaoStock and the primary AkShare
history source expose point-in-time daily turnover, but the normalized system
previously discarded it. The existing volume-moving-average factor is not a
substitute because volume is not normalized by shares outstanding.

Primary references:

- [George, Hwang, and Li (2023), February, share turnover, and momentum in China](https://doi.org/10.1016/j.pacfin.2023.102173)
- [Chen, Chao, and Wu (2021), Does the turnover effect matter in emerging markets? Evidence from China](https://doi.org/10.1016/j.pacfin.2021.101551)

## Decision

1. Preserve provider-reported daily turnover percentage as an optional
   `turnover` price column. Existing caches without the field remain readable.
2. Record `turnover_quality` in per-symbol source provenance. BaoStock and
   AkShare rows with actual values are `reported_percent`; sources without the
   field are `unavailable`.
3. Add an opt-in `low_turnover` factor based on the negative trailing mean daily
   turnover. Both the lookback and minimum observation count must be explicit
   when the factor has non-zero weight.
4. Expose `low_turnover_available`; a strategy declaring it required excludes
   symbols without enough point-in-time observations before scoring.
5. Keep the factor neutral unless explicitly configured. This capability change
   does not preregister or evaluate an experiment and does not consume the
   remaining 52-week-high family slot.
6. Do not translate the February result into an untested cash-timing rule. A
   future low-turnover family requires a complete, provenance-bound cache and a
   separate preregistration before any historical evaluation.

## Consequences

- A future turnover hypothesis can use the published economic variable instead
  of current market capitalization or raw volume proxies.
- Refreshed market-data fingerprints will change when reported turnover is
  present; frozen caches and completed evidence are not rewritten in place.
- Fallback symbols without reported turnover remain usable by strategies that do
  not require this factor and fail closed for strategies that do.
- No candidate, account, order, activation, or broker authority is created.

## Alternatives considered

- Convert the February exclusion result into February cash timing: rejected
  because it changes an academic return-sample control into a different
  deployable hypothesis.
- Reuse raw volume or volume moving-average distance: rejected because neither
  measures trading normalized by point-in-time shares outstanding and the local
  volume-distance direction already has rejection evidence.
- Infer historical shares from current market capitalization: rejected because
  it introduces future information and corporate-action bias.
