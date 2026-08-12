# ADR 0021: Enforce a system-wide CNY 200,000 capital ceiling

Status: accepted

Date: 2026-08-12

## Context

The small-capital strategy family had an opt-in budget, but the default system
still backtested with CNY 1,000,000 and historical capital diagnostics extended
above CNY 2,000,000. Disabling the family budget or directly constructing a
capital-sensitivity variant could bypass the user's requirement that no strategy
use more than CNY 200,000. Order-flow limits alone were insufficient because a
larger imported broker account could accumulate exposure across trading days.

## Decision

1. `SYSTEM_MAX_TOTAL_CAPITAL=200000` is an application invariant and cannot be
   disabled by configuration. A strategy-family budget may only lower it.
2. Backtest cash and every capital-sensitivity level must remain at or below the
   effective limit. Direct variant construction performs the same check.
3. Strategy deployable equity is `min(current equity, effective limit)` in
   backtest, paper, shadow, and broker-preview portfolio construction. Excess
   account equity remains cash and cannot increase target exposure.
4. RPA and automatic-live single-order, batch-buy, and daily-buy limits cannot
   exceed the effective limit. Rebalance gross limits may reach twice the limit
   so a complete sell-and-buy rotation is possible without increasing capital.
5. Runtime automatic-live limits clamp unsafe configured values even before
   audit results are consumed.
6. Capital policy is part of the backtest configuration hash. Evidence produced
   under a different capital policy is stale.
7. The default account size is CNY 100,000 and standard sensitivity levels are
   CNY 30,000, 50,000, 100,000, and 200,000.

## Consequences

- No supported strategy or execution path can intentionally allocate more than
  CNY 200,000, including when a broker account contains more cash.
- Existing million-yuan backtests, sensitivity evidence, and fixed-history
  signatures no longer describe the current system and must be regenerated.
- A profitable portfolio above the limit is rebalanced toward capped target
  exposure; gains above the limit are retained as cash.
- Gross turnover can exceed CNY 200,000 during a full rotation, but buy exposure
  and resulting strategy capital remain capped.

## Alternatives considered

### Keep the ceiling opt-in per strategy family

Rejected because an omitted or disabled section would silently restore a larger
strategy.

### Limit only each order or each trading day

Rejected because repeated buys across days could still accumulate exposure above
the strategy capital ceiling.

### Scale every broker account down before import

Rejected because broker truth must remain intact. The system caps portfolio
allocation while preserving the full read-only account snapshot.