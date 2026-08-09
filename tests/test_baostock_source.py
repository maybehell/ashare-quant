from types import SimpleNamespace

import pandas as pd
import pytest

from ashare_quant.baostock_source import enrich_stock_metadata_baostock, fetch_trade_calendar_frame_baostock, result_frame


class FakeResult:
    def __init__(self, fields, rows, error_code="0", error_msg="success"):
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = error_msg
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeBaoStock:
    def __init__(self, login_code="0"):
        self.login_code = login_code
        self.logout_calls = 0

    def login(self):
        return SimpleNamespace(error_code=self.login_code, error_msg="success" if self.login_code == "0" else "denied")

    def logout(self):
        self.logout_calls += 1
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_stock_basic(self):
        return FakeResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [
                ["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"],
                ["sz.300001", "特锐德", "2009-10-30", "", "1", "1"],
                ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],
            ],
        )

    def query_stock_industry(self):
        return FakeResult(
            ["updateDate", "code", "code_name", "industry", "industryClassification"],
            [
                ["2026-07-20", "sh.600000", "浦发银行", "J66货币金融服务", "证监会行业分类"],
                ["2026-07-20", "sz.300001", "特锐德", "C38电气机械和器材制造业", "证监会行业分类"],
            ],
        )


def test_trade_calendar_normalizes_sessions():
    class CalendarClient:
        def query_trade_dates(self, start_date=None, end_date=None):
            assert start_date == "2026-07-24"
            assert end_date == "2026-07-27"
            return FakeResult(
                ["calendar_date", "is_trading_day"],
                [
                    ["2026-07-24", "1"],
                    ["2026-07-25", "0"],
                    ["2026-07-26", "0"],
                    ["2026-07-27", "0"],
                ],
            )

    frame = fetch_trade_calendar_frame_baostock(
        CalendarClient(),
        pd.Timestamp("2026-07-24").date(),
        pd.Timestamp("2026-07-27").date(),
    )

    assert frame["date"].tolist() == [
        pd.Timestamp("2026-07-24").date(),
        pd.Timestamp("2026-07-25").date(),
        pd.Timestamp("2026-07-26").date(),
        pd.Timestamp("2026-07-27").date(),
    ]
    assert frame["is_trading_day"].tolist() == [True, False, False, False]
    assert frame["source"].unique().tolist() == ["baostock_query_trade_dates"]

def test_result_frame_rejects_source_error():
    with pytest.raises(RuntimeError, match="query failed"):
        result_frame(FakeResult([], [], error_code="100", error_msg="query failed"))


def test_baostock_metadata_updates_only_requested_symbols_and_preserves_name():
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "name": "浦发银行", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False},
            {"symbol": "300001", "name": "ST特锐德", "board": "gem", "industry": "unknown", "list_date": "2000-01-01", "is_st": True},
        ]
    )
    source = FakeBaoStock()

    enriched, provenance = enrich_stock_metadata_baostock(stocks, ["600000"], module=source)

    rows = enriched.set_index("symbol")
    assert rows.at["600000", "list_date"] == "1999-11-10"
    assert rows.at["600000", "industry"] == "J66货币金融服务"
    assert rows.at["300001", "list_date"] == "2000-01-01"
    assert rows.at["300001", "name"] == "ST特锐德"
    assert provenance["symbol"].tolist() == ["600000"]
    assert provenance.loc[0, "source"] == "baostock"
    assert source.logout_calls == 1


def test_baostock_login_failure_does_not_query_or_logout():
    source = FakeBaoStock(login_code="1")
    stocks = pd.DataFrame(
        [{"symbol": "600000", "name": "A", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False}]
    )

    with pytest.raises(RuntimeError, match="denied"):
        enrich_stock_metadata_baostock(stocks, module=source)
    assert source.logout_calls == 0
