# Small-Capital A-Share Strategy

Status: research only

## Objective

`configs/small_capital_100k.yaml` is a board-lot-aware strategy designed around
an intended account size of CNY 100,000. Every configured research capital level
is capped at CNY 200,000. The cap is validated before a backtest or capital
sensitivity run starts.

This strategy is not an activated execution strategy and is not part of the
existing frozen forward-candidate set.

## Predeclared design

- Quarterly rebalance to reduce minimum-commission drag.
- Eight to ten positions, with a 16% stock cap and 30% industry cap.
- Five percent cash reserve and integer-lot construction.
- Price-derived factors only: 15% medium momentum, 15% long momentum, 25% low
  volatility, 10% low drawdown, and 35% liquidity.
- Breadth and benchmark risk overlays reduce exposure in adverse regimes.
- Capital sensitivity at CNY 30,000, 50,000, 100,000, and 200,000 only.

The factor mix is intentionally small and fixed. It avoids fundamental factors
because the current cache does not provide authoritative point-in-time
fundamentals. Chinese factor research also cautions against mechanically buying
the smallest firms: Liu, Stambaugh, and Yuan exclude the smallest 30% when
constructing their China size factor. Momentum and low-volatility evidence
motivate the remaining price-derived signals, while liquidity receives a high
weight because transaction costs can erase low-volatility returns in small and
illiquid stocks.

Primary references:

- https://www.nber.org/papers/w24458
- https://academic.oup.com/rfs/article-abstract/15/1/143/1619967
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1585031
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1738316
- https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml

## Commands

```powershell
python -m ashare_quant --config configs/small_capital_100k.yaml backtest
python -m ashare_quant --config configs/small_capital_100k.yaml capital-sensitivity
python -m ashare_quant --config configs/small_capital_100k.yaml small-capital-readiness
```

The second command writes isolated lot-aware results for every configured cash
level. Historical superiority does not authorize paper or live trading; a new
candidate identity and new forward window would be required before promotion.

## Current backtest evidence

The 2026-08-12 run used 750 complete trading days from 2023-07-06 through
2026-08-07. The CNY 100,000 portfolio returned 36.37%, versus 22.16% for the
CSI 300 benchmark, with a -32.86% maximum drawdown, 4.90 annualized turnover,
and 25.98% average cash after deployment. Three fixed walk-forward folds had a
66.67% positive-excess ratio; the worst fold excess return was -10.24%.

Capital sensitivity used one market-data fingerprint for every level:

| Capital | Total return | Max drawdown | Average cash | Final positions | Fidelity gate |
| ---: | ---: | ---: | ---: | ---: | :--- |
| CNY 30,000 | 101.54% | -31.33% | 23.69% | 9 | Failed |
| CNY 50,000 | 49.28% | -36.56% | 24.43% | 11 | Failed |
| CNY 100,000 | 36.37% | -32.86% | 25.98% | 11 | Passed |
| CNY 200,000 | 35.50% | -33.46% | 24.29% | 11 | Passed |

The unusually high CNY 30,000 result is not treated as superior evidence. Its
return differs from the CNY 200,000 reference by 66.04 percentage points, which
shows severe board-lot and concentration path dependence. CNY 100,000 is the
first tested level passing the declared execution-fidelity thresholds.

The final position count can temporarily exceed the eight-to-ten construction
target because legacy holdings may remain while a sell is blocked or rounded;
this is execution behavior, not permission to select more than ten new targets.
The drawdown and one negative walk-forward fold keep this strategy research-only.

## Forward admission

The readiness command writes
`outputs_small_capital_100k/small_capital_live_readiness.csv`. Every blocking
row must pass before this strategy family may be preregistered for a new,
untuned forward-only observation window. A pass does not authorize paper orders,
strategy activation, live-ledger initialization, or broker submission.

The current version is blocked by maximum drawdown, annual excess-return
consistency, worst annual excess return, fixed-parameter walk-forward consistency,
worst walk-forward excess return, and cost-path stability. Higher simulated
costs currently produce a materially different and better trade path; the gate
treats this as integer-lot path instability rather than favorable robustness.