from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .audit import signal_identity_frames_equal

from .candidate import candidate_freeze_boundary_evidence, load_candidate_manifest
from .config import project_path
from .data import DataStore
from .io_utils import atomic_to_csv
from .shadow_policy import shadow_evaluation_identity
from .validation import filter_complete_market_data


SHADOW_AUDIT_COLUMNS = [
    "signal_date",
    "execution_date",
    "latest_date",
    "observed_trading_days",
    "covered_weight",
    "target_weight",
    "coverage_ratio",
    "evaluation_schema_version",
    "entry_price_basis",
    "return_basis",
    "entry_cost_rate",
    "max_target_weight",
    "configured_max_stock_weight",
    "weight_cap_passed",
    "portfolio_return",
    "benchmark_return",
    "excess_return",
    "active_max_drawdown",
    "tracking_error",
    "information_ratio",
    "signal_hash",
    "strategy_hash",
    "candidate_id",
    "evidence_type",
    "rebalance_count",
    "cumulative_turnover",
    "cumulative_cost",
    "status",
]

SHADOW_COMPONENT_COLUMNS = [
    "signal_date",
    "execution_date",
    "symbol",
    "target_weight",
    "anchor_weight",
    "satellite_weight",
    "signal_hash",
    "strategy_hash",
    "candidate_id",
]

SHADOW_ATTRIBUTION_COLUMNS = [
    "signal_date",
    "execution_date",
    "latest_date",
    "observed_trading_days",
    "covered_weight",
    "target_weight",
    "coverage_ratio",
    "evaluation_schema_version",
    "entry_price_basis",
    "return_basis",
    "entry_cost_rate",
    "anchor_covered_weight",
    "satellite_covered_weight",
    "portfolio_return",
    "anchor_return",
    "satellite_return",
    "benchmark_return",
    "excess_return",
    "anchor_excess_contribution",
    "satellite_excess_contribution",
    "signal_hash",
    "strategy_hash",
    "candidate_id",
    "status",
]


def shadow_archive_dir_from_config(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "shadow_archive"


def archive_shadow_outputs(
    archive_dir: Path,
    signal_date: str,
    orders: pd.DataFrame,
    signal: pd.DataFrame,
    provenance: pd.DataFrame,
) -> dict[str, Path]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_date = signal_date.replace("-", "")
    evidence = provenance_mapping(provenance)
    candidate_id = str(evidence.get("candidate_id", "") or "").strip()
    safe_candidate = "".join(char for char in candidate_id if char.isalnum() or char in {"-", "_"})[:32]
    archive_stem = safe_date if not safe_candidate else f"{safe_date}_{safe_candidate}"
    paths = {
        f"shadow_archive/{archive_stem}_orders.csv": archive_dir / f"{archive_stem}_orders.csv",
        f"shadow_archive/{archive_stem}_signal.csv": archive_dir / f"{archive_stem}_signal.csv",
        f"shadow_archive/{archive_stem}_provenance.csv": archive_dir / f"{archive_stem}_provenance.csv",
    }
    archive_paths = list(paths.values())
    existing = [path.exists() for path in archive_paths]
    if any(existing):
        if not all(existing):
            raise RuntimeError(f"Incomplete immutable shadow archive: {archive_stem}")
        existing_evidence = provenance_mapping(pd.read_csv(archive_paths[-1]))
        identity_keys = ("signal_date", "execution_date", "strategy_hash", "candidate_id")
        changed = [
            key
            for key in identity_keys
            if str(existing_evidence.get(key, "") or "") != str(evidence.get(key, "") or "")
        ]
        existing_signal = pd.read_csv(archive_paths[1], dtype={"symbol": str})
        if not signal_identity_frames_equal(existing_signal, signal):
            changed.append("signal_identity")
        if changed:
            raise RuntimeError(
                f"Immutable shadow evidence conflict for {archive_stem}: {', '.join(changed)}"
            )
        return paths

    orders.to_csv(archive_paths[0], index=False, encoding="utf-8-sig")
    signal.to_csv(archive_paths[1], index=False, encoding="utf-8-sig")
    provenance.to_csv(archive_paths[2], index=False, encoding="utf-8-sig")
    return paths

def write_shadow_component_outputs(
    config: dict[str, Any],
    components: pd.DataFrame,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    current_path = output_dir / "shadow_component_snapshot.csv"
    frame = components.reindex(columns=SHADOW_COMPONENT_COLUMNS).copy()
    atomic_to_csv(frame, current_path, index=False, encoding="utf-8-sig")
    paths = {"shadow_component_snapshot.csv": current_path}
    if frame.empty:
        return paths

    first = frame.iloc[0]
    signal_date = str(first.get("signal_date", "")).replace("-", "")
    candidate_id = str(first.get("candidate_id", "") or "").strip()
    safe_candidate = "".join(char for char in candidate_id if char.isalnum() or char in {"-", "_"})[:32]
    stem = signal_date if not safe_candidate else f"{signal_date}_{safe_candidate}"
    archive_dir = output_dir / "shadow_component_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{stem}_components.csv"
    if archive_path.exists():
        existing = pd.read_csv(archive_path, dtype={"symbol": str}).reindex(columns=SHADOW_COMPONENT_COLUMNS)
        if not shadow_component_frames_equal(existing, frame):
            raise RuntimeError(f"Immutable shadow component evidence conflict for {stem}")
    else:
        atomic_to_csv(frame, archive_path, index=False, encoding="utf-8-sig")
    paths[f"shadow_component_archive/{archive_path.name}"] = archive_path
    return paths

def shadow_component_frames_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    if len(left) != len(right):
        return False
    identity_columns = [
        "signal_date",
        "execution_date",
        "symbol",
        "strategy_hash",
        "candidate_id",
    ]
    numeric_columns = ["target_weight", "anchor_weight", "satellite_weight"]
    left_frame = left.reindex(columns=SHADOW_COMPONENT_COLUMNS).copy()
    right_frame = right.reindex(columns=SHADOW_COMPONENT_COLUMNS).copy()
    for frame in (left_frame, right_frame):
        for column in identity_columns:
            frame[column] = frame[column].fillna("").astype(str)
        for column in numeric_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.sort_values(identity_columns, inplace=True)
        frame.reset_index(drop=True, inplace=True)
    if not left_frame[identity_columns].equals(right_frame[identity_columns]):
        return False
    for column in numeric_columns:
        left_values = left_frame[column]
        right_values = right_frame[column]
        both_missing = left_values.isna() & right_values.isna()
        tolerance = 1e-12 + 1e-10 * right_values.abs().fillna(0.0)
        if not ((left_values.sub(right_values).abs() <= tolerance) | both_missing).all():
            return False
    return True


def run_shadow_component_attribution(config: dict[str, Any]) -> Path:
    store = DataStore.from_config(config)
    _, prices, _ = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    output_dir = project_path(config["paths"]["output_dir"])
    attribution = build_shadow_component_attribution(
        output_dir / "shadow_component_archive", prices, benchmark, config=config
    )
    path = output_dir / "shadow_component_attribution.csv"
    atomic_to_csv(attribution, path, index=False, encoding="utf-8-sig")
    return path


def provenance_mapping(frame: pd.DataFrame) -> dict[str, str]:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {
        str(row.item): "" if pd.isna(row.value) else str(row.value)
        for row in frame.itertuples(index=False)
    }


def run_shadow_audit(config: dict[str, Any]) -> Path:
    store = DataStore.from_config(config)
    _, prices, _ = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    audit = build_shadow_forward_audit(
        shadow_archive_dir_from_config(config),
        prices,
        benchmark,
        max_stock_weight=float(config.get("strategy", {}).get("max_stock_weight", 1.0)),
        config=config,
    )
    manifest = load_candidate_manifest(config)
    audit = exclude_pre_freeze_candidate_evidence(audit, manifest)
    continuous, curve = build_continuous_shadow_portfolios(
        shadow_archive_dir_from_config(config), prices, benchmark, config, manifest
    )
    if not continuous.empty:
        audit = pd.concat([audit, continuous], ignore_index=True).reindex(columns=SHADOW_AUDIT_COLUMNS)
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(curve, output_dir / "shadow_continuous_nav.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(continuous, output_dir / "shadow_continuous_summary.csv", index=False, encoding="utf-8-sig")
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "shadow_forward_audit.csv"
    audit.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def exclude_pre_freeze_candidate_evidence(
    audit: pd.DataFrame,
    manifest: dict[str, Any] | None,
) -> pd.DataFrame:
    if audit.empty or not manifest or str(manifest.get("freeze_mode", "")) != "forward_only_preregistration":
        return audit
    boundary_ok, freeze_date, _ = candidate_freeze_boundary_evidence(manifest)
    freeze_boundary = pd.to_datetime(freeze_date, errors="coerce")
    if not boundary_ok or pd.isna(freeze_boundary) or "execution_date" not in audit.columns:
        return audit
    result = audit.copy()
    candidate_id = str(manifest.get("candidate_id", "") or "")
    candidate_match = (
        result.get("candidate_id", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .eq(candidate_id)
    )
    execution_dates = pd.to_datetime(result["execution_date"], errors="coerce")
    invalid = candidate_match & execution_dates.notna() & execution_dates.le(freeze_boundary)
    if invalid.any():
        result.loc[invalid, "status"] = "pre_freeze_not_evidence"
        result.loc[invalid, "observed_trading_days"] = 0
        for column in (
            "portfolio_return", "benchmark_return", "excess_return",
            "active_max_drawdown", "tracking_error", "information_ratio",
        ):
            if column in result.columns:
                result.loc[invalid, column] = pd.NA
    return result

CONTINUOUS_NAV_COLUMNS = [
    "date", "candidate_id", "strategy_hash", "portfolio_nav", "benchmark_nav",
    "active_nav", "active_daily_return",
    "cash_weight", "gross_exposure", "daily_return", "benchmark_daily_return",
    "turnover", "transaction_cost", "rebalance", "coverage_ratio", "signal_date",
]


def build_continuous_shadow_portfolios(
    archive_dir: Path,
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay immutable signals as a continuous, monthly-rebalanced close-price portfolio."""
    if not archive_dir.exists() or prices.empty:
        return pd.DataFrame(columns=SHADOW_AUDIT_COLUMNS), pd.DataFrame(columns=CONTINUOUS_NAV_COLUMNS)
    records: list[dict[str, Any]] = []
    for signal_path in sorted(archive_dir.glob("*_signal.csv")):
        provenance = read_provenance(signal_path)
        candidate_id = str(provenance.get("candidate_id", "") or "")
        execution_date = pd.to_datetime(provenance.get("execution_date"), errors="coerce")
        if not candidate_id or pd.isna(execution_date):
            continue
        if manifest and candidate_id == str(manifest.get("candidate_id", "") or ""):
            freeze_date = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
            if not pd.isna(freeze_date) and execution_date <= freeze_date:
                continue
        signal = pd.read_csv(signal_path, dtype={"symbol": str})
        if signal.empty or not {"symbol", "target_weight"}.issubset(signal.columns):
            continue
        records.append({
            "candidate_id": candidate_id,
            "strategy_hash": str(provenance.get("strategy_hash", "") or ""),
            "signal_hash": str(provenance.get("signal_hash", "") or ""),
            "signal_date": str(provenance.get("signal_date", "") or ""),
            "execution_date": execution_date.date(),
            "signal": signal,
        })
    if not records:
        return pd.DataFrame(columns=SHADOW_AUDIT_COLUMNS), pd.DataFrame(columns=CONTINUOUS_NAV_COLUMNS)

    summaries: list[dict[str, Any]] = []
    curves: list[pd.DataFrame] = []
    for candidate_id in sorted({row["candidate_id"] for row in records}):
        candidate_records = [row for row in records if row["candidate_id"] == candidate_id]
        strategy_hashes = {row["strategy_hash"] for row in candidate_records}
        candidate_records.sort(key=lambda row: (row["execution_date"], row["signal_date"], row["signal_hash"]))
        selected: list[dict[str, Any]] = []
        seen_months: set[tuple[int, int]] = set()
        for row in candidate_records:
            month = (row["execution_date"].year, row["execution_date"].month)
            if not selected or month not in seen_months:
                selected.append(row)
                seen_months.add(month)
        summary, curve = _evaluate_continuous_candidate(selected, prices, benchmark, config)
        if len(strategy_hashes) != 1:
            summary["status"] = "continuous_identity_conflict"
            summary["observed_trading_days"] = 0
            for column in (
            "portfolio_return", "benchmark_return", "excess_return",
            "active_max_drawdown", "tracking_error", "information_ratio",
        ):
                summary[column] = ""
        summaries.append(summary)
        if not curve.empty:
            curves.append(curve)
    return (
        pd.DataFrame(summaries).reindex(columns=SHADOW_AUDIT_COLUMNS),
        pd.concat(curves, ignore_index=True).reindex(columns=CONTINUOUS_NAV_COLUMNS)
        if curves else pd.DataFrame(columns=CONTINUOUS_NAV_COLUMNS),
    )


def _evaluate_continuous_candidate(
    signals: list[dict[str, Any]],
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    policy = shadow_evaluation_identity(config)
    first = signals[0]
    start_date = first["execution_date"]
    signal_start = pd.to_datetime(first["signal_date"], errors="coerce")
    signal_start_date = start_date if pd.isna(signal_start) else signal_start.date()
    strategy_hash = first["strategy_hash"]
    base = {
        "signal_date": first["signal_date"], "execution_date": start_date.isoformat(),
        "latest_date": "", "observed_trading_days": 0, "covered_weight": 0.0,
        "target_weight": 0.0, "coverage_ratio": 0.0,
        "evaluation_schema_version": policy["shadow_evaluation_schema_version"],
        "entry_price_basis": policy["shadow_candidate_execution_price_basis"],
        "return_basis": policy["shadow_candidate_return_basis"],
        "entry_cost_rate": policy["shadow_candidate_buy_cost_rate"],
        "max_target_weight": "", "configured_max_stock_weight": float(config.get("strategy", {}).get("max_stock_weight", 1.0)),
        "weight_cap_passed": True, "portfolio_return": "", "benchmark_return": "", "excess_return": "",
        "active_max_drawdown": "", "tracking_error": "", "information_ratio": "",
        "signal_hash": first["signal_hash"], "strategy_hash": strategy_hash,
        "candidate_id": first["candidate_id"], "evidence_type": "continuous_portfolio",
        "rebalance_count": 0, "cumulative_turnover": 0.0, "cumulative_cost": 0.0,
        "status": "continuous_not_observable",
    }
    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"], errors="coerce").dt.date
    price_data["symbol"] = price_data["symbol"].astype(str).str.zfill(6)
    for column in ("open", "close"):
        price_data[column] = pd.to_numeric(price_data.get(column), errors="coerce")
    price_data = price_data.dropna(subset=["date", "close"])
    dates = sorted(dt for dt in price_data["date"].unique() if dt >= signal_start_date)
    execution_dates = [dt for dt in dates if dt >= start_date]
    if signal_start_date not in dates or start_date not in dates:
        return base, pd.DataFrame(columns=CONTINUOUS_NAV_COLUMNS)
    close_wide = price_data.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index().ffill()
    open_wide = price_data.pivot_table(index="date", columns="symbol", values="open", aggfunc="last").sort_index()
    signal_by_date = {row["execution_date"]: row for row in signals}
    benchmark_data = benchmark.copy()
    if not benchmark_data.empty:
        benchmark_data["date"] = pd.to_datetime(benchmark_data["date"], errors="coerce").dt.date
        benchmark_data["close"] = pd.to_numeric(benchmark_data["close"], errors="coerce")
        benchmark_close = benchmark_data.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").set_index("date")["close"].sort_index()
    else:
        benchmark_close = pd.Series(dtype=float)
    if signal_start_date not in benchmark_close.index or float(benchmark_close.loc[signal_start_date]) <= 0:
        return base, pd.DataFrame(columns=CONTINUOUS_NAV_COLUMNS)

    cash = 1.0
    positions: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    cumulative_turnover = 0.0
    cumulative_cost = 0.0
    minimum_coverage = 1.0
    previous_nav = 1.0
    previous_benchmark_nav = 1.0
    buy_rate = float(policy["shadow_candidate_buy_cost_rate"])
    sell_rate = float(policy["shadow_candidate_sell_cost_rate"])
    for trade_date in dates:
        closes = close_wide.loc[trade_date]
        opens = open_wide.loc[trade_date] if trade_date in open_wide.index else pd.Series(dtype=float)
        turnover = 0.0
        transaction_cost = 0.0
        rebalanced = False
        coverage_ratio = minimum_coverage
        active_signal_date = ""
        signal_row = signal_by_date.get(trade_date)
        if signal_row is not None:
            signal = signal_row["signal"].copy()
            signal["symbol"] = signal["symbol"].astype(str).str.zfill(6)
            signal["target_weight"] = pd.to_numeric(signal["target_weight"], errors="coerce").fillna(0.0)
            signal = signal[signal["target_weight"] > 0].drop_duplicates("symbol", keep="last")
            target_weight = float(signal["target_weight"].sum())
            max_target_weight = float(signal["target_weight"].max()) if not signal.empty else 0.0
            configured_cap = float(base["configured_max_stock_weight"])
            if max_target_weight > configured_cap + 1e-10:
                base["weight_cap_passed"] = False
            available = signal[signal["symbol"].map(lambda symbol: symbol in opens.index and pd.notna(opens[symbol]) and float(opens[symbol]) > 0)].copy()
            covered_weight = float(available["target_weight"].sum())
            coverage_ratio = covered_weight / target_weight if target_weight > 0 else 0.0
            held_prices_available = all(
                symbol in opens.index and pd.notna(opens[symbol]) and float(opens[symbol]) > 0
                for symbol in positions
            )
            if not held_prices_available:
                coverage_ratio = 0.0
            minimum_coverage = min(minimum_coverage, coverage_ratio)
            if coverage_ratio >= 0.95 and held_prices_available:
                nav_open = cash + sum(quantity * float(opens[symbol]) for symbol, quantity in positions.items())
                targets = dict(zip(available["symbol"], available["target_weight"]))
                symbols = set(positions) | set(targets)
                buy_notional = 0.0
                sell_notional = 0.0
                new_positions: dict[str, float] = {}
                for symbol in symbols:
                    price = float(opens[symbol])
                    current_value = positions.get(symbol, 0.0) * price
                    target_value = nav_open * float(targets.get(symbol, 0.0))
                    difference = target_value - current_value
                    if difference > 0:
                        buy_notional += difference
                    else:
                        sell_notional -= difference
                    if target_value > 0:
                        new_positions[symbol] = target_value / price
                transaction_cost = buy_notional * buy_rate + sell_notional * sell_rate
                cash = nav_open - sum(nav_open * weight for weight in targets.values()) - transaction_cost
                positions = new_positions
                turnover = (buy_notional + sell_notional) / nav_open if nav_open else 0.0
                cumulative_turnover += turnover
                cumulative_cost += transaction_cost
                rebalanced = True
                active_signal_date = signal_row["signal_date"]
                base["covered_weight"] = covered_weight
                base["target_weight"] = target_weight
                base["max_target_weight"] = max_target_weight
                base["signal_hash"] = signal_row["signal_hash"]
                base["rebalance_count"] = int(base["rebalance_count"]) + 1
        nav = cash + sum(quantity * float(closes.get(symbol, 0.0)) for symbol, quantity in positions.items())
        benchmark_nav = float(benchmark_close.loc[trade_date] / benchmark_close.loc[signal_start_date]) if trade_date in benchmark_close.index else float("nan")
        rows.append({
            "date": trade_date.isoformat(), "candidate_id": first["candidate_id"], "strategy_hash": strategy_hash,
            "portfolio_nav": nav, "benchmark_nav": benchmark_nav,
            "cash_weight": cash / nav if nav else 0.0, "gross_exposure": (nav - cash) / nav if nav else 0.0,
            "daily_return": nav / previous_nav - 1 if rows and previous_nav else 0.0,
            "benchmark_daily_return": benchmark_nav / previous_benchmark_nav - 1 if rows and previous_benchmark_nav and pd.notna(benchmark_nav) else 0.0,
            "turnover": turnover, "transaction_cost": transaction_cost, "rebalance": rebalanced,
            "coverage_ratio": coverage_ratio, "signal_date": active_signal_date,
        })
        previous_nav = nav
        if pd.notna(benchmark_nav):
            previous_benchmark_nav = benchmark_nav
    curve = pd.DataFrame(rows)
    valid_nav = (
        pd.to_numeric(curve["portfolio_nav"], errors="coerce")
        / pd.to_numeric(curve["benchmark_nav"], errors="coerce")
    )
    curve["active_nav"] = valid_nav
    curve["active_daily_return"] = (
        pd.to_numeric(curve["portfolio_nav"], errors="coerce").pct_change()
        - pd.to_numeric(curve["benchmark_nav"], errors="coerce").pct_change()
    )
    curve = curve.reindex(columns=CONTINUOUS_NAV_COLUMNS)
    base["latest_date"] = dates[-1].isoformat()
    base["observed_trading_days"] = max(len(execution_dates) - 1, 0)
    base["coverage_ratio"] = minimum_coverage
    base["cumulative_turnover"] = cumulative_turnover
    base["cumulative_cost"] = cumulative_cost
    if len(execution_dates) >= 2 and not curve.empty and pd.notna(curve["benchmark_nav"].iloc[-1]):
        base["portfolio_return"] = float(curve["portfolio_nav"].iloc[-1] - 1.0)
        base["benchmark_return"] = float(curve["benchmark_nav"].iloc[-1] - 1.0)
        base["excess_return"] = float(base["portfolio_return"] - base["benchmark_return"])
        active_nav = pd.to_numeric(curve["active_nav"], errors="coerce").dropna()
        active_returns = pd.to_numeric(curve["active_daily_return"], errors="coerce").dropna()
        if not active_nav.empty:
            active_drawdown = active_nav / active_nav.cummax() - 1.0
            base["active_max_drawdown"] = float(max(0.0, -active_drawdown.min()))
        if len(active_returns) >= 2:
            active_std = float(active_returns.std(ddof=1))
            base["tracking_error"] = active_std * (252.0 ** 0.5)
            if active_std > 1e-12:
                base["information_ratio"] = float(active_returns.mean()) / active_std * (252.0 ** 0.5)
            else:
                base["information_ratio"] = 0.0
        if not bool(base["weight_cap_passed"]):
            base["status"] = "continuous_weight_cap_violation"
        else:
            base["status"] = "continuous_ok" if minimum_coverage >= 0.95 else "continuous_partial_coverage"
    return base, curve

def build_shadow_forward_audit(
    archive_dir: Path,
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    max_stock_weight: float | None = None,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not archive_dir.exists():
        return pd.DataFrame(columns=SHADOW_AUDIT_COLUMNS)
    for signal_path in sorted(archive_dir.glob("*_signal.csv")):
        signal = pd.read_csv(signal_path, dtype={"symbol": str})
        provenance = read_provenance(signal_path)
        rows.append(
            evaluate_shadow_signal(
                signal, provenance, prices, benchmark,
                max_stock_weight=max_stock_weight, config=config
            )
        )
    audit = pd.DataFrame(rows, columns=SHADOW_AUDIT_COLUMNS)
    if audit.empty:
        return audit
    evidence_key = ["signal_date", "signal_hash", "strategy_hash", "candidate_id"]
    return (
        audit.drop_duplicates(evidence_key, keep="last")
        .sort_values(["signal_date", "candidate_id"])
        .reset_index(drop=True)
    )

def build_shadow_component_attribution(
    archive_dir: Path,
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("*_components.csv")):
            components = pd.read_csv(path, dtype={"symbol": str})
            rows.append(evaluate_shadow_components(components, prices, benchmark, config=config))
    frame = pd.DataFrame(rows, columns=SHADOW_ATTRIBUTION_COLUMNS)
    if frame.empty:
        return frame
    return (
        frame.drop_duplicates(
            ["signal_date", "signal_hash", "strategy_hash", "candidate_id"],
            keep="last",
        )
        .sort_values(["signal_date", "candidate_id"])
        .reset_index(drop=True)
    )


def evaluate_shadow_components(
    components: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = shadow_evaluation_identity(config or {})
    first = components.iloc[0] if not components.empty else pd.Series(dtype=object)
    signal_date = str(first.get("signal_date", "") or "")
    execution_date = str(first.get("execution_date", "") or "")
    base = {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "latest_date": "",
        "observed_trading_days": 0,
        "covered_weight": 0.0,
        "target_weight": 0.0,
        "coverage_ratio": 0.0,
        "evaluation_schema_version": policy["shadow_evaluation_schema_version"],
        "entry_price_basis": policy["shadow_entry_price_basis"],
        "return_basis": policy["shadow_return_basis"],
        "entry_cost_rate": policy["shadow_entry_cost_rate"],
        "anchor_covered_weight": 0.0,
        "satellite_covered_weight": 0.0,
        "portfolio_return": "",
        "anchor_return": "",
        "satellite_return": "",
        "benchmark_return": "",
        "excess_return": "",
        "anchor_excess_contribution": "",
        "satellite_excess_contribution": "",
        "signal_hash": str(first.get("signal_hash", "") or ""),
        "strategy_hash": str(first.get("strategy_hash", "") or ""),
        "candidate_id": str(first.get("candidate_id", "") or ""),
        "status": "not_observable",
    }
    if components.empty or prices.empty or not execution_date:
        return base

    frame = components.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    for column in ("target_weight", "anchor_weight", "satellite_weight"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce").fillna(0.0)
    frame = frame[frame["target_weight"] > 0]
    target_weight = float(frame["target_weight"].sum())
    base["target_weight"] = target_weight
    if frame.empty:
        return base

    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"]).dt.date
    entry_target = pd.to_datetime(execution_date).date()
    trading_dates = sorted(dt for dt in price_data["date"].unique() if dt >= entry_target)
    if len(trading_dates) < 2:
        return base
    entry_date, latest_date = trading_dates[0], trading_dates[-1]
    base["latest_date"] = latest_date.isoformat()
    base["observed_trading_days"] = len(trading_dates) - 1
    entry = price_data[price_data["date"] == entry_date][["symbol", "close"]].rename(columns={"close": "entry_close"})
    latest = price_data[price_data["date"] == latest_date][["symbol", "close"]].rename(columns={"close": "latest_close"})
    merged = frame.merge(entry, on="symbol", how="left").merge(latest, on="symbol", how="left")
    merged["entry_close"] = pd.to_numeric(merged["entry_close"], errors="coerce")
    merged["latest_close"] = pd.to_numeric(merged["latest_close"], errors="coerce")
    covered = merged.dropna(subset=["entry_close", "latest_close"])
    covered = covered[(covered["entry_close"] > 0) & (covered["latest_close"] > 0)]
    if covered.empty:
        return base

    covered["asset_return"] = covered["latest_close"] / (covered["entry_close"] * (1.0 + float(policy["shadow_entry_cost_rate"]))) - 1
    total_weight = float(covered["target_weight"].sum())
    anchor_weight = float(covered["anchor_weight"].sum())
    satellite_weight = float(covered["satellite_weight"].sum())
    base["covered_weight"] = total_weight
    base["coverage_ratio"] = total_weight / target_weight if target_weight > 0 else 0.0
    base["anchor_covered_weight"] = anchor_weight
    base["satellite_covered_weight"] = satellite_weight
    if total_weight <= 0:
        return base

    anchor_contribution = float((covered["asset_return"] * covered["anchor_weight"]).sum())
    satellite_contribution = float((covered["asset_return"] * covered["satellite_weight"]).sum())
    portfolio_return = anchor_contribution + satellite_contribution
    anchor_return = "" if anchor_weight <= 0 else float((covered["asset_return"] * covered["anchor_weight"]).sum() / anchor_weight)
    satellite_return = "" if satellite_weight <= 0 else float((covered["asset_return"] * covered["satellite_weight"]).sum() / satellite_weight)
    benchmark_return = benchmark_period_return(benchmark, entry_date, latest_date)
    base["portfolio_return"] = portfolio_return
    base["anchor_return"] = anchor_return
    base["satellite_return"] = satellite_return
    base["benchmark_return"] = benchmark_return
    if benchmark_return != "":
        benchmark_value = float(benchmark_return)
        base["excess_return"] = portfolio_return - benchmark_value
        base["anchor_excess_contribution"] = anchor_contribution - benchmark_value * anchor_weight / total_weight
        base["satellite_excess_contribution"] = satellite_contribution - benchmark_value * satellite_weight / total_weight
    base["status"] = "partial_coverage" if base["coverage_ratio"] < 0.95 else "ok"
    return base


def read_provenance(signal_path: Path) -> dict[str, str]:
    provenance_path = signal_path.with_name(signal_path.name.replace("_signal.csv", "_provenance.csv"))
    if not provenance_path.exists():
        return {}
    frame = pd.read_csv(provenance_path)
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {str(row.item): "" if pd.isna(row.value) else str(row.value) for row in frame.itertuples(index=False)}


def evaluate_shadow_signal(
    signal: pd.DataFrame,
    provenance: dict[str, str],
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    max_stock_weight: float | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = shadow_evaluation_identity(config or {})
    signal_date = str(provenance.get("signal_date", signal["signal_date"].iloc[0] if "signal_date" in signal and not signal.empty else ""))
    execution_date = str(provenance.get("execution_date", signal["execution_date"].iloc[0] if "execution_date" in signal and not signal.empty else ""))
    base = {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "latest_date": "",
        "observed_trading_days": 0,
        "covered_weight": 0.0,
        "target_weight": 0.0,
        "coverage_ratio": 0.0,
        "evaluation_schema_version": policy["shadow_evaluation_schema_version"],
        "entry_price_basis": policy["shadow_entry_price_basis"],
        "return_basis": policy["shadow_return_basis"],
        "entry_cost_rate": policy["shadow_entry_cost_rate"],
        "max_target_weight": "",
        "configured_max_stock_weight": "" if max_stock_weight is None else float(max_stock_weight),
        "weight_cap_passed": "" if max_stock_weight is None else False,
        "portfolio_return": "",
        "benchmark_return": "",
        "excess_return": "",
        "signal_hash": provenance.get("signal_hash", ""),
        "strategy_hash": provenance.get("strategy_hash", ""),
        "candidate_id": provenance.get("candidate_id", ""),
        "status": "not_observable",
    }
    if signal.empty or prices.empty or not execution_date:
        return base

    signal = signal.copy()
    signal["symbol"] = signal["symbol"].astype(str).str.zfill(6)
    signal["target_weight"] = pd.to_numeric(signal["target_weight"], errors="coerce").fillna(0.0)
    signal = signal[signal["target_weight"] > 0].copy()
    target_weight = float(signal["target_weight"].sum())
    base["target_weight"] = target_weight
    max_target_weight = float(signal["target_weight"].max()) if not signal.empty else 0.0
    base["max_target_weight"] = max_target_weight
    weight_cap_passed = bool(
        max_stock_weight is None or max_target_weight <= float(max_stock_weight) + 1e-10
    )
    base["weight_cap_passed"] = "" if max_stock_weight is None else weight_cap_passed
    if signal.empty:
        return base

    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"]).dt.date
    entry_target = pd.to_datetime(execution_date).date()
    trading_dates = sorted(dt for dt in price_data["date"].unique() if dt >= entry_target)
    if len(trading_dates) < 2:
        return base
    entry_date = trading_dates[0]
    latest_date = trading_dates[-1]
    base["latest_date"] = latest_date.isoformat()
    base["observed_trading_days"] = len(trading_dates) - 1

    entry = price_data[price_data["date"] == entry_date][["symbol", "close"]].rename(columns={"close": "entry_close"})
    latest = price_data[price_data["date"] == latest_date][["symbol", "close"]].rename(columns={"close": "latest_close"})
    merged = signal[["symbol", "target_weight"]].merge(entry, on="symbol", how="left").merge(latest, on="symbol", how="left")
    merged["entry_close"] = pd.to_numeric(merged["entry_close"], errors="coerce")
    merged["latest_close"] = pd.to_numeric(merged["latest_close"], errors="coerce")
    covered = merged.dropna(subset=["entry_close", "latest_close"])
    covered = covered[(covered["entry_close"] > 0) & (covered["latest_close"] > 0)]
    covered_weight = float(covered["target_weight"].sum())
    base["covered_weight"] = covered_weight
    base["coverage_ratio"] = covered_weight / target_weight if target_weight > 0 else 0.0
    if covered.empty or covered_weight <= 0:
        return base

    covered_return = covered["latest_close"] / (covered["entry_close"] * (1.0 + float(policy["shadow_entry_cost_rate"]))) - 1
    portfolio_return = float((covered_return * covered["target_weight"]).sum())
    benchmark_return = benchmark_period_return(benchmark, entry_date, latest_date)
    excess_return = "" if benchmark_return == "" else portfolio_return - float(benchmark_return)
    base["portfolio_return"] = portfolio_return
    base["benchmark_return"] = benchmark_return
    base["excess_return"] = excess_return
    if not weight_cap_passed:
        base["status"] = "weight_cap_violation"
    else:
        base["status"] = "partial_coverage" if base["coverage_ratio"] < 0.95 else "ok"
    return base


def benchmark_period_return(benchmark: pd.DataFrame, entry_date: Any, latest_date: Any) -> float | str:
    if benchmark.empty:
        return ""
    bench = benchmark.copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.date
    entry_rows = bench[bench["date"] >= entry_date].sort_values("date")
    latest_rows = bench[bench["date"] <= latest_date].sort_values("date")
    if entry_rows.empty or latest_rows.empty:
        return ""
    entry_close = float(pd.to_numeric(entry_rows["close"].iloc[0], errors="coerce"))
    latest_close = float(pd.to_numeric(latest_rows["close"].iloc[-1], errors="coerce"))
    if entry_close <= 0:
        return ""
    return latest_close / entry_close - 1
