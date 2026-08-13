# ADR 0036: Preregister an independent 52-week-high research family

Status: accepted

Date: 2026-08-13

## Context

The four-test small-capital benchmark-relative family is exhausted and every
registered experiment was rejected. Other locally evaluated directions,
including residual momentum, reference anchoring, and volume moving-average
distance, were also rejected and cannot be relabeled as new hypotheses.
Point-in-time fundamental coverage is not yet authoritative enough for a new
quality or valuation experiment.

Published evidence on 52-week-high momentum in China is mixed. Recent long-sample
work reports an effect, while earlier and alternative samples report weak or no
aggregate effect. That makes high proximity a distinct, falsifiable hypothesis,
not a promise of excess return.

## Decision

1. Add an opt-in `near_52_week_high` factor equal to current close divided by the
   trailing maximum close, standardized cross-sectionally. It is neutral unless
   `high_52_week` is explicitly configured.
2. Require the complete configured lookback and expose availability so stocks
   without 252 sessions are excluded before scoring.
3. Create a new fixed family, `small_capital_52week_high_v1`, with family-wise
   alpha 0.05 and at most two experiments. Each registered experiment therefore
   uses Bonferroni nominal alpha 0.025.
4. Preregister experiment one as a point-in-time CSI 300, twenty-stock, equal-
   weighted, industry-neutral, monthly portfolio with 90% high proximity and 10%
   liquidity. Initial capital is CNY 100,000 and the system ceiling remains CNY
   200,000.
5. Test the simple high-proximity mechanism first. Do not add a February or
   turnover interaction until a second experiment is separately specified and
   registered before evaluation.
6. Reuse the hash-bound completed market cache, but isolate output, state, and
   research candidate paths. Do not inherit old candidate or execution authority.
7. This change preregisters only. It does not run historical validation, create a
   forward candidate, edit daily observation, generate orders, or call a broker.

## Consequences

- The new family is economically and statistically separate from the exhausted
  four-test family, with an explicit and smaller future experiment budget.
- Any strategy, threshold, capital, or registration drift invalidates the frozen
  identity before evaluation.
- A failed first experiment cannot be tuned in place. A second experiment, if
  justified, must consume the only remaining family slot.
- Passing historical gates could permit a later forward preregistration review
  only; it would grant no paper or live execution authority.

## Alternatives considered

- Reuse residual momentum or reference anchoring: rejected because both already
  have locally governed rejection evidence.
- Start with quality and valuation: deferred because point-in-time fundamental
  history is not authoritative enough.
- Register unlimited variants: rejected because it would recreate the same
  selection-bias problem the experiment registry is intended to prevent.
