import unittest
from datetime import date

import pandas as pd

from ashare_quant.trading_day_status import _status_row


class TradingDayStatusTests(unittest.TestCase):
    def calendar(self, day: str, is_trading_day: bool) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": date.fromisoformat(day),
                    "is_trading_day": is_trading_day,
                    "source": "test_calendar",
                    "retrieved_at": "2026-01-01T00:00:00",
                }
            ]
        )

    def test_weekend_is_closed_without_calendar_or_network(self):
        row = _status_row(
            pd.DataFrame(),
            date(2026, 8, 1),
            refresh_attempted=False,
            refresh_succeeded=False,
            refresh_error="",
        )

        self.assertEqual(row["status"], "closed")
        self.assertFalse(row["is_trading_day"])
        self.assertTrue(row["calendar_covered"])
        self.assertEqual(row["source"], "weekday_rule")

    def test_cached_open_session_is_confirmed(self):
        row = _status_row(
            self.calendar("2026-08-03", True),
            date(2026, 8, 3),
            refresh_attempted=False,
            refresh_succeeded=False,
            refresh_error="",
        )

        self.assertEqual(row["status"], "open")
        self.assertTrue(row["is_trading_day"])
        self.assertEqual(row["source"], "test_calendar")

    def test_exchange_holiday_is_closed_even_on_weekday(self):
        row = _status_row(
            self.calendar("2026-10-01", False),
            date(2026, 10, 1),
            refresh_attempted=True,
            refresh_succeeded=True,
            refresh_error="",
        )

        self.assertEqual(row["status"], "closed")
        self.assertFalse(row["is_trading_day"])
        self.assertIn("marks this date closed", row["message"])

    def test_missing_weekday_calendar_fails_closed_with_refresh_error(self):
        row = _status_row(
            pd.DataFrame(),
            date(2026, 8, 3),
            refresh_attempted=True,
            refresh_succeeded=False,
            refresh_error="ConnectionError: unavailable",
        )

        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["is_trading_day"])
        self.assertFalse(row["calendar_covered"])
        self.assertIn("Refresh failed", row["message"])


if __name__ == "__main__":
    unittest.main()
