from datetime import date

from ashare_quant.baostock_source import (
    baostock_index_code,
    baostock_code,
    fetch_benchmark_frame_baostock,
    fetch_history_frame_baostock,
)

from test_baostock_source import FakeResult


class FakeHistoryClient:
    def __init__(self):
        self.calls = []

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.calls.append((code, fields, kwargs))
        if fields == "date,code,close":
            return FakeResult(
                ["date", "code", "close"],
                [["2024-01-02", code, "3500.5"]],
            )
        return FakeResult(
            ["date", "code", "open", "high", "low", "close", "volume", "amount", "tradestatus"],
            [
                ["2024-01-02", code, "10", "11", "9", "10.5", "1000", "10500", "1"],
                ["2024-01-03", code, "", "", "", "", "", "", "0"],
            ],
        )


def test_baostock_history_uses_qfq_and_discards_suspended_rows():
    client = FakeHistoryClient()

    frame, provenance = fetch_history_frame_baostock(
        client,
        "600000",
        date(2024, 1, 1),
        date(2024, 1, 31),
    )

    assert baostock_code("600000") == "sh.600000"
    assert baostock_code("300001") == "sz.300001"
    assert len(frame) == 1
    assert frame.loc[0, "amount"] == 10500
    assert provenance["source"] == "baostock_query_history_k_data_plus_qfq"
    assert client.calls[0][2]["adjustflag"] == "2"


def test_baostock_benchmark_uses_unadjusted_index_close():
    client = FakeHistoryClient()

    frame = fetch_benchmark_frame_baostock(
        client,
        "000300",
        "CSI 300",
        date(2024, 1, 1),
        date(2024, 1, 31),
    )

    assert frame.loc[0, "close"] == 3500.5
    assert baostock_index_code("sh000300") == "sh.000300"
    assert baostock_index_code("沪深300") == "sh.000300"
    assert baostock_index_code("000300") == "sh.000300"
    assert baostock_index_code("399001") == "sz.399001"
    assert client.calls[0][0] == "sh.000300"
    assert client.calls[0][2]["adjustflag"] == "3"
