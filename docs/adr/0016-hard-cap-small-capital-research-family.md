# ADR 0016: Hard-cap the small-capital research family

Status: accepted

Date: 2026-08-11

## Context

The existing strategy was developed around a much larger portfolio and could
hold almost entirely cash when simulated with CNY 100,000 because its target
position count and five-percent stock cap were incompatible with 100-share board
lots. Reusing that strategy with only a smaller `initial_cash` would therefore
measure a broken implementation rather than a usable small-capital portfolio.

The requested strategy family must never use more than CNY 200,000, while the
existing frozen candidates and their historical identities must not be mutated.

## Decision

1. Add an opt-in `capital_budget` policy that validates the intended account
   capital, backtest initial cash, and every capital-sensitivity level against a
   hard maximum before research runs.
2. Set the new family's maximum to CNY 200,000 and intended capital to CNY
   100,000.
3. Use integer-lot-aware construction with eight to ten positions, a five
   percent reserve, quarterly rebalancing, and explicit stock/industry caps.
4. Keep the configuration research-only and isolated from candidate activation,
   paper orders, the live ledger, and broker submission.
5. Preserve all existing frozen candidate configurations and evidence. A future
   promotion attempt would require a new candidate identity and forward window.

## Consequences

- A typo or later edit above CNY 200,000 fails before a partial sensitivity run
  can be mistaken for valid evidence.
- Results at different capital levels include board-lot, minimum-commission,
  slippage, liquidity, T+1, suspension, and price-limit behavior.
- The smaller portfolio is necessarily more concentrated than the legacy
  strategy and must be judged with its own drawdown, turnover, and forward
  evidence.
- The policy does not impose a retroactive cap on immutable historical research
  artifacts; they remain auditable but cannot silently become this strategy.
