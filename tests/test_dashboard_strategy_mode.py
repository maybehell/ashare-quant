import pandas as pd

from ashare_quant.dashboard import dashboard_strategy_mode


def test_central_quarantine_overrides_stale_successor_summary() -> None:
    mode, message = dashboard_strategy_mode(
        {
            "one_click_summary": pd.DataFrame(
                [
                    {
                        "strategy_operating_mode": "successor_observation",
                        "strategy_operating_message": "observe old challenger",
                    }
                ]
            ),
            "decision": pd.DataFrame(
                [{"decision": "do_not_promote_quarantined_dependency"}]
            ),
            "strategy_health": pd.DataFrame(
                [{"check": "paper_orders_allowed", "value": False}]
            ),
            "local_operator_status": pd.DataFrame(),
        }
    )

    assert mode == "strategy_halted"
    assert "\u5df2\u9694\u79bb" in message


def test_fresh_local_status_overrides_one_click_when_not_quarantined() -> None:
    mode, message = dashboard_strategy_mode(
        {
            "one_click_summary": pd.DataFrame(
                [{"strategy_operating_mode": "old", "strategy_operating_message": "old"}]
            ),
            "decision": pd.DataFrame([{"decision": "research_only"}]),
            "strategy_health": pd.DataFrame(),
            "local_operator_status": pd.DataFrame(
                [
                    {
                        "item": "strategy_operating_mode",
                        "value": "successor_observation",
                        "message": "current",
                    }
                ]
            ),
        }
    )

    assert (mode, message) == ("successor_observation", "current")
