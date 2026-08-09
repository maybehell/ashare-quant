from __future__ import annotations

from typing import Any

import pandas as pd


INDUSTRY_RISK_COLUMNS = ["check", "passed", "value", "threshold", "severity", "message"]


def industry_relative_policy(config: dict[str, Any]) -> dict[str, Any]:
    strategy = config.get("strategy", {})
    if not isinstance(strategy, dict):
        return {}
    policy = strategy.get("industry_relative_cap", {})
    return policy if isinstance(policy, dict) else {}


def post_trade_industry_policy(config: dict[str, Any]) -> dict[str, Any]:
    strategy = config.get("strategy", {})
    if not isinstance(strategy, dict):
        return {}
    relative = industry_relative_policy(config)
    if bool(relative.get("enabled", False)):
        return {**relative, "mode": "relative"}
    absolute = strategy.get("post_trade_industry_cap", {})
    if isinstance(absolute, dict) and bool(absolute.get("enabled", False)):
        return {
            **absolute,
            "mode": "absolute",
            "absolute_cap": float(strategy.get("max_industry_weight", 1.0)),
        }
    return {}


def build_industry_risk_audit(exposure: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    policy = post_trade_industry_policy(config)
    if not policy:
        return _frame(
            [
                _row(
                    "industry_risk_ready",
                    True,
                    "not_applicable",
                    "post-trade industry policy enabled",
                    "info",
                    "未启用成交后行业漂移审计。",
                )
            ]
        )

    mode = str(policy.get("mode", "relative"))
    tolerance = float(policy.get("drift_tolerance", 0.015))
    required = {"industry", "weight"}
    if mode == "relative":
        required |= {"reference_weight", "active_cap"}
    if exposure.empty or not required.issubset(exposure.columns):
        return _frame(
            [
                _row(
                    "industry_risk_ready",
                    False,
                    "missing_exposure",
                    f"max cap overrun <= {tolerance:.4f}",
                    "blocker",
                    "行业上限已启用，但缺少可审计的成交后行业暴露。",
                )
            ]
        )

    audited = exposure.copy()
    numeric_columns = ["weight"]
    if mode == "relative":
        numeric_columns += ["reference_weight", "active_cap"]
    for column in numeric_columns:
        audited[column] = pd.to_numeric(audited[column], errors="coerce")
    audited = audited.loc[audited[numeric_columns].notna().all(axis=1)].copy()
    if audited.empty:
        return _frame(
            [
                _row(
                    "industry_risk_ready",
                    False,
                    "invalid_exposure",
                    f"max cap overrun <= {tolerance:.4f}",
                    "blocker",
                    "行业暴露缺少有效权重或上限，无法完成成交后审计。",
                )
            ]
        )

    if mode == "relative":
        audited["effective_cap"] = audited["active_cap"]
        reference_total = float(audited["reference_weight"].sum())
        reference_complete = 0.98 <= reference_total <= 1.02
    else:
        absolute_cap = float(policy.get("absolute_cap", 1.0))
        if not 0 < absolute_cap <= 1:
            return _frame(
                [
                    _row(
                        "industry_risk_ready",
                        False,
                        absolute_cap,
                        "0 < max_industry_weight <= 1",
                        "blocker",
                        "绝对行业上限配置无效。",
                    )
                ]
            )
        audited["effective_cap"] = absolute_cap
        reference_total = 1.0
        reference_complete = True

    audited["cap_overrun"] = (audited["weight"] - audited["effective_cap"]).clip(lower=0.0)
    strict = audited[audited["cap_overrun"] > 1e-9]
    severe = audited[audited["cap_overrun"] > tolerance + 1e-9]
    max_overrun = float(audited["cap_overrun"].max())
    strict_names = _industry_names(strict)
    severe_names = _industry_names(severe)
    ready = reference_complete and severe.empty

    rows: list[dict[str, object]] = []
    if mode == "relative":
        rows.append(
            _row(
                "industry_reference_weight_complete",
                reference_complete,
                reference_total,
                "0.98 <= sum <= 1.02",
                "ok" if reference_complete else "blocker",
                "行业参考权重必须完整，才能解释主动行业偏离。",
            )
        )
    else:
        rows.append(
            _row(
                "industry_absolute_cap_configured",
                True,
                float(policy["absolute_cap"]),
                "strategy.max_industry_weight",
                "ok",
                "成交后绝对行业审计绑定策略行业上限。",
            )
        )
    rows.extend(
        [
            _row(
                "industry_strict_cap_violation_count",
                True,
                int(len(strict)),
                0,
                "warning" if len(strict) else "ok",
                "严格目标上限超限行业：" + (strict_names or "无") + "；小幅成交漂移由容忍阈值判断。",
            ),
            _row(
                "industry_max_cap_overrun",
                max_overrun <= tolerance + 1e-9,
                max_overrun,
                tolerance,
                "ok" if max_overrun <= tolerance + 1e-9 else "blocker",
                "成交后实际行业权重超过目标上限的最大幅度不得超过漂移容忍值。",
            ),
            _row(
                "industry_drift_tolerance_violation_count",
                severe.empty,
                int(len(severe)),
                0,
                "ok" if severe.empty else "blocker",
                "超过漂移容忍值的行业：" + (severe_names or "无") + "。",
            ),
            _row(
                "industry_risk_ready",
                ready,
                "passed" if ready else "blocked",
                f"max cap overrun <= {tolerance:.4f}",
                "ok" if ready else "blocker",
                "成交后行业风险在容忍范围内。" if ready else "成交后行业风险超出容忍范围，禁止进入执行就绪状态。",
            ),
        ]
    )
    return _frame(rows)


def industry_risk_ready(audit: pd.DataFrame | None, config: dict[str, Any]) -> tuple[bool, object, object]:
    policy = post_trade_industry_policy(config)
    if not policy:
        return True, "not_applicable", "post-trade industry policy enabled"
    tolerance = float(policy.get("drift_tolerance", 0.015))
    if audit is None or audit.empty or not {"check", "passed"}.issubset(audit.columns):
        return False, "missing", f"max cap overrun <= {tolerance:.4f}"
    selected = audit[audit["check"].astype(str) == "industry_risk_ready"]
    if selected.empty:
        return False, "missing final audit row", f"max cap overrun <= {tolerance:.4f}"
    row = selected.iloc[-1]
    passed = str(row.get("passed", "")).strip().lower() in {"true", "1", "yes"}
    return passed, row.get("value", ""), row.get("threshold", f"max cap overrun <= {tolerance:.4f}")

def _industry_names(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    ordered = frame.sort_values("cap_overrun", ascending=False)
    return ", ".join(
        f"{row.industry}(+{float(row.cap_overrun):.2%})" for row in ordered.itertuples(index=False)
    )


def _row(check: str, passed: bool, value: object, threshold: object, severity: str, message: str) -> dict[str, object]:
    return {
        "check": check,
        "passed": bool(passed),
        "value": value,
        "threshold": threshold,
        "severity": severity,
        "message": message,
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=INDUSTRY_RISK_COLUMNS)
