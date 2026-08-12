from datetime import date
import unittest

import pandas as pd

from ashare_quant.models import Order, Position
from ashare_quant.portfolio import Portfolio, generate_rebalance_orders
from ashare_quant.rules import buy_quantity_for_value, calc_fee, configured_rebalance_dates, is_limit_down, is_limit_up, next_business_day, price_limit_rate, round_buy_quantity


class RuleTests(unittest.TestCase):
    def test_price_limit_rates(self):
        self.assertEqual(price_limit_rate("600000", "main"), 0.10)
        self.assertEqual(price_limit_rate("300001", "gem"), 0.20)
        self.assertEqual(price_limit_rate("688001", "star"), 0.20)
        self.assertEqual(price_limit_rate("302132", "gem"), 0.20)
        self.assertEqual(price_limit_rate("600001", "main", is_st=True), 0.05)

    def test_limit_checks(self):
        self.assertTrue(is_limit_up(11.0, 10.0, 0.10))
        self.assertTrue(is_limit_down(9.0, 10.0, 0.10))

    def test_buy_lot_rounding(self):
        self.assertEqual(round_buy_quantity(199, 100), 100)
        self.assertEqual(round_buy_quantity(99, 100), 0)
        self.assertEqual(buy_quantity_for_value(999.9999999999, 10.0, 100), 100)
        self.assertEqual(buy_quantity_for_value(999.99, 10.0, 100), 0)

    def test_fee_contains_sell_stamp_tax(self):
        cfg = {"commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "transfer_fee_rate": 0.00001}
        self.assertGreater(calc_fee("sell", 100000, cfg), calc_fee("buy", 100000, cfg))

    def test_next_business_day_skips_weekend(self):
        self.assertEqual(next_business_day(date(2024, 1, 5)), date(2024, 1, 8))

    def test_quarterly_rebalance_uses_first_trading_date_of_each_quarter(self):
        dates = [date(2024, 1, 2), date(2024, 2, 1), date(2024, 4, 1), date(2024, 6, 3)]
        self.assertEqual(configured_rebalance_dates(dates, "quarterly"), {date(2024, 1, 2), date(2024, 4, 1)})

    def test_unknown_rebalance_frequency_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported rebalance"):
            configured_rebalance_dates([date(2024, 1, 2)], "daily")
    def test_rebalance_sell_order_never_exceeds_sellable_quantity(self):
        portfolio = Portfolio(
            cash=0,
            positions={"600000": Position(symbol="600000", quantity=100, cost=9.0, locked_quantity=60)},
        )
        config = {"trading": {"reserve_cash_ratio": 0.01, "lot_size": 100}}

        orders = generate_rebalance_orders(
            date(2026, 7, 16),
            target_weights=pd.Series(dtype=float),
            portfolio=portfolio,
            close_prices={"600000": 10.0},
            config=config,
        )

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, "sell")
        self.assertEqual(orders[0].quantity, 40)

    def test_rebalance_buy_allows_exactly_one_lot_target(self):
        portfolio = Portfolio(cash=10_000)
        config = {"trading": {"reserve_cash_ratio": 0.0, "lot_size": 100}}

        orders = generate_rebalance_orders(
            date(2026, 7, 16),
            target_weights=pd.Series({"600000": 0.10}),
            portfolio=portfolio,
            close_prices={"600000": 10.0},
            config=config,
        )

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, "buy")
        self.assertEqual(orders[0].quantity, 100)

    def test_rebalance_fully_exits_position_without_float_odd_lot(self):
        portfolio = Portfolio(
            cash=0,
            positions={"600276": Position(symbol="600276", quantity=200, cost=40.0)},
        )
        config = {"trading": {"reserve_cash_ratio": 0.05, "lot_size": 100}}

        orders = generate_rebalance_orders(
            date(2026, 7, 16),
            target_weights=pd.Series(dtype=float),
            portfolio=portfolio,
            close_prices={"600276": 43.70},
            config=config,
        )

        self.assertEqual([(order.side, order.quantity) for order in orders], [("sell", 200)])

    def test_rebalance_orders_sells_before_buys_to_release_cash(self):
        portfolio = Portfolio(
            cash=0,
            positions={"600001": Position(symbol="600001", quantity=100, cost=9.0)},
        )
        config = {"trading": {"reserve_cash_ratio": 0.0, "lot_size": 100}}

        orders = generate_rebalance_orders(
            date(2026, 7, 16),
            target_weights=pd.Series({"000001": 1.0}),
            portfolio=portfolio,
            close_prices={"000001": 10.0, "600001": 10.0},
            config=config,
        )

        self.assertEqual(
            [(order.side, order.symbol) for order in orders],
            [("sell", "600001"), ("buy", "000001")],
        )

    def test_sell_first_execution_can_fund_rebalance_buy(self):
        portfolio = Portfolio(
            cash=10.0,
            positions={"600001": Position(symbol="600001", quantity=100, cost=9.0)},
        )
        config = {
            "trading": {
                "reserve_cash_ratio": 0.0,
                "lot_size": 100,
                "slippage_bps": 0,
                "max_volume_participation": 1.0,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "transfer_fee_rate": 0.0,
            }
        }
        close_prices = {"000001": 10.0, "600001": 10.0}
        orders = generate_rebalance_orders(
            date(2026, 7, 16),
            target_weights=pd.Series({"000001": 1.0}),
            portfolio=portfolio,
            close_prices=close_prices,
            config=config,
        )
        prices = pd.DataFrame(
            [
                {"symbol": "000001", "open": 10.0, "volume": 10000},
                {"symbol": "600001", "open": 10.0, "volume": 10000},
            ]
        )
        stocks = pd.DataFrame(
            [
                {"symbol": "000001", "board": "main", "is_st": False},
                {"symbol": "600001", "board": "main", "is_st": False},
            ]
        )

        fills = portfolio.execute_orders(
            orders,
            date(2026, 7, 16),
            prices,
            stocks,
            close_prices,
            config,
        )

        self.assertEqual(
            [(fill.side, fill.symbol, fill.quantity) for fill in fills],
            [("sell", "600001", 100), ("buy", "000001", 100)],
        )
        self.assertEqual(portfolio.positions["600001"].quantity, 0)
        self.assertEqual(portfolio.positions["000001"].quantity, 100)

    def test_t1_locked_position(self):
        portfolio = Portfolio(cash=100000)
        order = Order(date=date(2024, 1, 2), symbol="600000", side="buy", quantity=100)
        fill = portfolio._apply_fill(order, date(2024, 1, 2), 100, 10, 5)
        self.assertEqual(fill.quantity, 100)
        self.assertEqual(portfolio.positions["600000"].sellable_quantity, 0)
        portfolio.unlock_t1()
        self.assertEqual(portfolio.positions["600000"].sellable_quantity, 100)


if __name__ == "__main__":
    unittest.main()


def test_partial_rebalance_sell_keeps_board_lot_quantity():
    portfolio = Portfolio(cash=0.0)
    portfolio.positions["688531"] = Position(
        symbol="688531", quantity=100, cost=67.0, locked_quantity=0
    )
    config = {"trading": {"reserve_cash_ratio": 0.0, "lot_size": 100}}

    orders = generate_rebalance_orders(
        date(2026, 7, 2),
        pd.Series({"688531": 0.01}),
        portfolio,
        {"688531": 136.65},
        config,
    )

    assert orders == []

def test_rebalance_caps_deployable_equity_at_system_limit():
    portfolio = Portfolio(cash=1_000_000.0)
    config = {"trading": {"reserve_cash_ratio": 0.0, "lot_size": 100}}

    orders = generate_rebalance_orders(
        date(2026, 8, 12),
        pd.Series({"600000": 1.0}),
        portfolio,
        {"600000": 10.0},
        config,
    )

    assert [(order.side, order.quantity) for order in orders] == [("buy", 20000)]


def test_rebalance_uses_stricter_strategy_family_capital_limit():
    portfolio = Portfolio(cash=1_000_000.0)
    config = {
        "capital_budget": {
            "enabled": True,
            "intended_account_capital": 100000,
            "max_total_capital": 100000,
        },
        "trading": {"reserve_cash_ratio": 0.0, "lot_size": 100},
    }

    orders = generate_rebalance_orders(
        date(2026, 8, 12),
        pd.Series({"600000": 1.0}),
        portfolio,
        {"600000": 10.0},
        config,
    )

    assert [(order.side, order.quantity) for order in orders] == [("buy", 10000)]
