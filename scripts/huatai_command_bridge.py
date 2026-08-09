from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.autolive_integrity import finalize_adapter_response, verify_adapter_request_integrity
from ashare_quant.huatai_canary import consume_desktop_canary_arm, desktop_canary_certification_status, submit_desktop_canary, validate_canary_orders

SCHEMA_VERSION = "ashare_quant.command_adapter.v1"
BRIDGE_NAME = "huatai_command_bridge"
BROKER_NAME = "huatai"
REQUIRED_CAPABILITIES = ["submit_orders", "query_orders", "cancel_orders", "account_snapshot"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Huatai command adapter bridge scaffold for ashare_quant.")
    parser.add_argument("--config", default="configs/huatai_bridge_template.yaml", help="Huatai bridge config file.")
    parser.add_argument("--simulate", action="store_true", help="Use simulation mode. No broker API is called.")
    parser.add_argument("request_path", help="Path to request JSON from ashare_quant.")
    parser.add_argument("result_path", help="Path where bridge writes response JSON.")
    return parser.parse_args(argv)


def load_config(path: str) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.exists():
        return {}
    text = candidate.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError:
            return {}
        payload = yaml.safe_load(text) or {}
    return payload if isinstance(payload, dict) else {}


def simulate_enabled(args: argparse.Namespace, config: dict[str, Any]) -> bool:
    env_value = os.environ.get("ASHARE_QUANT_HUATAI_SIMULATE", "")
    cfg = config.get("huatai", config)
    return bool(args.simulate or env_value.strip().lower() in {"1", "true", "yes", "on"} or cfg.get("simulate") is True)


def read_request(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request JSON must be an object")
    return payload


def write_result(path: Path, payload: dict[str, Any], request: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if request is not None:
        payload = finalize_adapter_response(request, payload)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def base_response(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(request.get("run_id", "")),
        "auto_live_config_sha256": str(request.get("auto_live_config_sha256", "")),
        "bridge_name": BRIDGE_NAME,
        "broker_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "broker": BROKER_NAME,
    }


def order_ticket_id(order: object, index: int) -> str:
    if isinstance(order, dict):
        value = order.get("ticket_id")
        if value is not None and str(value).strip():
            return str(value)
    return f"missing-ticket-{index}"


def validate_order(order: object) -> str:
    if not isinstance(order, dict):
        return "order_not_object"
    symbol = str(order.get("symbol", "")).zfill(6)
    side = str(order.get("side", "")).lower()
    try:
        quantity = int(float(order.get("quantity", 0)))
    except (TypeError, ValueError):
        return "invalid_quantity"
    if len(symbol) != 6 or not symbol.isdigit():
        return "invalid_symbol"
    if side not in {"buy", "sell"}:
        return "invalid_side"
    if quantity <= 0:
        return "invalid_quantity"
    if side == "buy" and quantity % 100 != 0:
        return "buy_quantity_not_lot_size"
    return ""


def desktop_read_only_enabled(config: dict[str, Any]) -> bool:
    cfg = config.get("huatai", config)
    return str(cfg.get("live_backend", "") or "").strip().lower() == "desktop_read_only"


def desktop_canary_enabled(config: dict[str, Any]) -> bool:
    cfg = config.get("huatai", config)
    return str(cfg.get("live_backend", "") or "").strip().lower() == "desktop_canary"


def create_desktop_canary_driver(request: dict[str, Any] | None = None) -> Any:
    from ashare_quant.huatai_desktop_live import HuataiDesktopCanaryDriver

    stop_path = str((request or {}).get("emergency_stop_path", "") or "").strip()
    return HuataiDesktopCanaryDriver(emergency_stop_path=stop_path or None)


def read_desktop_snapshot(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    cfg = config.get("huatai", config)
    value = str(cfg.get("desktop_snapshot_path", "") or "").strip()
    if not value:
        return {}, "desktop_snapshot_path is not configured"
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"desktop snapshot is unreadable: {exc}"
    if not isinstance(payload, dict):
        return {}, "desktop snapshot must be a JSON object"
    if payload.get("schema_version") != "ashare_quant.huatai_desktop_snapshot.v1":
        return {}, "desktop snapshot schema is invalid"
    if str(payload.get("status", "")).lower() != "ok" or str(payload.get("environment", "")).lower() != "live":
        return {}, "desktop snapshot is not a successful live snapshot"
    if not isinstance(payload.get("account"), dict) or not isinstance(payload.get("positions"), list):
        return {}, "desktop snapshot account or positions payload is invalid"
    try:
        generated = datetime.fromisoformat(str(payload.get("generated_at", "")))
        if generated.tzinfo is None:
            return {}, "desktop snapshot generated_at must include timezone"
        age = (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds()
        max_age = max(float(cfg.get("desktop_snapshot_max_age_seconds", 300)), 1.0)
    except (TypeError, ValueError):
        return {}, "desktop snapshot generated_at or max age is invalid"
    if age < -30 or age > max_age:
        return {}, f"desktop snapshot is stale: age_seconds={age:.1f};max={max_age:g}"
    return payload, ""


def self_test_response(request: dict[str, Any], simulate: bool, config: dict[str, Any]) -> dict[str, Any]:
    response = base_response(request)
    cfg = config.get("huatai", config)
    configured_broker = str(cfg.get("broker", BROKER_NAME) or BROKER_NAME).lower()
    if request.get("schema_version") != SCHEMA_VERSION:
        response.update({"status": "failed", "capabilities": [], "message": "Unsupported schema_version."})
        return response
    if configured_broker != BROKER_NAME:
        response.update({"status": "failed", "capabilities": [], "message": "Bridge config broker must be huatai."})
        return response
    if desktop_read_only_enabled(config):
        _, error = read_desktop_snapshot(config)
        if error:
            response.update({"status": "failed", "capabilities": ["account_snapshot"], "message": error})
        else:
            response.update({"status": "ok", "capabilities": ["account_snapshot"], "message": "Huatai official desktop read-only snapshot is ready; all trading capabilities remain disabled."})
        return response
    if desktop_canary_enabled(config):
        certified, reason = desktop_canary_certification_status(
            config,
            expected_config_sha256=str(request.get("auto_live_config_sha256", "")),
        )
        if not certified:
            response.update({
                "status": "ready",
                "capabilities": ["account_snapshot"],
                "message": f"Read-only account snapshot is ready; trading capabilities remain disabled: {reason}",
            })
        else:
            try:
                driver = create_desktop_canary_driver(request)
                driver_ready, driver_reason = driver.runtime_selftest()
            except Exception as exc:
                driver_ready, driver_reason = False, f"desktop_canary_driver_unavailable:{type(exc).__name__}:{exc}"
            response.update(
                {
                    "status": "ok" if driver_ready else "failed",
                    "capabilities": REQUIRED_CAPABILITIES if driver_ready else ["account_snapshot"],
                    "message": "Huatai desktop Canary driver is certified and ready." if driver_ready else driver_reason,
                }
            )
        return response
    if simulate:
        response.update({"status": "ok", "capabilities": REQUIRED_CAPABILITIES, "message": "Huatai bridge simulation is ready. No broker API will be called."})
        return response
    response.update({"status": "failed", "capabilities": [], "message": "No audited Huatai live backend is configured. Keep auto-live disabled or run with --simulate for testing."})
    return response


def request_emergency_stop_active(request: dict[str, Any]) -> bool:
    value = str(request.get("emergency_stop_path", "") or "").strip()
    return bool(value and Path(value).exists())


def submit_response(
    request: dict[str, Any],
    simulate: bool,
    config: dict[str, Any] | None = None,
    driver_factory: Any | None = None,
) -> dict[str, Any]:
    response = base_response(request)
    active_config = config or {}
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    if not simulate and desktop_canary_enabled(active_config):
        certified, reason = desktop_canary_certification_status(
            active_config,
            expected_config_sha256=str(request.get("auto_live_config_sha256", "")),
        )
        if not certified:
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "status": "failed",
                    "broker_order_id": "",
                    "message": reason,
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        if request_emergency_stop_active(request):
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "status": "failed",
                    "broker_order_id": "",
                    "message": "Emergency stop active inside bridge; desktop driver was not created.",
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        try:
            validate_canary_orders(orders, active_config)
        except Exception as exc:
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "status": "rejected",
                    "broker_order_id": "",
                    "message": f"Desktop Canary validation failed before arm consumption: {exc}",
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        try:
            driver = driver_factory() if driver_factory else create_desktop_canary_driver(request)
            runtime_selftest = getattr(driver, "runtime_selftest", None)
            if not callable(runtime_selftest):
                raise RuntimeError("desktop Canary driver has no runtime_selftest")
            driver_ready, driver_reason = runtime_selftest()
            if not driver_ready:
                raise RuntimeError(driver_reason or "desktop Canary runtime self-test failed")
        except Exception as exc:
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "status": "failed",
                    "broker_order_id": "",
                    "message": f"Desktop Canary runtime self-test failed before arm consumption: {type(exc).__name__}: {exc}",
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        consumed, consume_reason = consume_desktop_canary_arm(
            active_config,
            expected_config_sha256=str(request.get("auto_live_config_sha256", "")),
            run_id=str(request.get("run_id", "")),
            orders=orders,
        )
        if not consumed:
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "status": "failed",
                    "broker_order_id": "",
                    "message": consume_reason,
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        response["results"] = submit_desktop_canary(request, active_config, driver)
        return response
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        if request_emergency_stop_active(request):
            results.append({"ticket_id": ticket_id, "status": "failed", "broker_order_id": "", "message": "Emergency stop active inside bridge; broker API was not called for this order."})
            continue
        validation_error = validate_order(order)
        if validation_error:
            results.append({"ticket_id": ticket_id, "status": "rejected", "broker_order_id": "", "message": validation_error})
            continue
        if simulate:
            results.append({"ticket_id": ticket_id, "status": "submitted", "broker_order_id": f"huatai-sim-{ticket_id}", "message": "Huatai simulation accepted the order. No broker API was called."})
        else:
            results.append({"ticket_id": ticket_id, "status": "failed", "broker_order_id": "", "message": "Huatai live backend is not configured."})
    response["results"] = results
    return response


def normalized_fee_fields(queried: dict[str, Any], filled_quantity: int) -> tuple[float, str, str]:
    if filled_quantity <= 0:
        return 0.0, "not_applicable", "none"
    raw_status = str(queried.get("fee_status", "") or "").strip().lower()
    raw_source = str(queried.get("fee_source", "") or "").strip()
    try:
        fee = float(queried.get("fee"))
    except (TypeError, ValueError):
        fee = 0.0
    if raw_status == "exact" and fee >= 0 and raw_source:
        return fee, "exact", raw_source
    return 0.0, "unavailable", raw_source or "huatai_order_grid"


def query_response(
    request: dict[str, Any],
    simulate: bool,
    config: dict[str, Any] | None = None,
    driver_factory: Any | None = None,
) -> dict[str, Any]:
    response = base_response(request)
    active_config = config or {}
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    if not simulate and desktop_canary_enabled(active_config):
        certified, reason = desktop_canary_certification_status(
            active_config,
            expected_config_sha256=str(request.get("auto_live_config_sha256", "")),
            allow_consumed=True,
        )
        if not certified:
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "broker_order_id": str(order.get("broker_order_id", "")) if isinstance(order, dict) else "",
                    "broker_status": "unknown",
                    "filled_quantity": 0,
                    "avg_fill_price": 0.0,
                    "filled_amount": 0.0,
                    "fee": 0.0,
                    "message": reason,
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        try:
            driver = driver_factory() if driver_factory else create_desktop_canary_driver(request)
        except Exception as exc:
            driver = None
            driver_error = f"Desktop Canary query driver unavailable: {type(exc).__name__}: {exc}"
        results = []
        for index, order in enumerate(orders, start=1):
            ticket_id = order_ticket_id(order, index)
            broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
            if driver is None:
                queried = {"broker_status": "unknown", "message": driver_error}
            else:
                try:
                    queried = driver.query(order if isinstance(order, dict) else {})
                except Exception as exc:
                    queried = {"broker_status": "unknown", "message": f"Huatai query failed: {type(exc).__name__}: {exc}"}
            filled_quantity = int(queried.get("filled_quantity", 0) or 0)
            avg_fill_price = float(queried.get("avg_fill_price", 0) or 0)
            try:
                filled_amount = float(queried.get("filled_amount"))
            except (TypeError, ValueError):
                filled_amount = filled_quantity * avg_fill_price
            if filled_quantity > 0 and filled_amount <= 0:
                filled_amount = filled_quantity * avg_fill_price
            fee, fee_status, fee_source = normalized_fee_fields(queried, filled_quantity)
            results.append(
                {
                    "ticket_id": ticket_id,
                    "broker_order_id": str(queried.get("broker_order_id", broker_order_id) or broker_order_id),
                    "broker_status": str(queried.get("broker_status", "unknown") or "unknown"),
                    "filled_quantity": filled_quantity,
                    "avg_fill_price": avg_fill_price,
                    "filled_amount": filled_amount,
                    "fee": fee,
                    "fee_status": fee_status,
                    "fee_source": fee_source,
                    "trade_date": str(queried.get("trade_date", "") or ""),
                    "message": str(queried.get("message", queried.get("raw_status", ""))),
                }
            )
        response["results"] = results
        return response
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
        if simulate:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "broker_status": "accepted", "filled_quantity": 0, "avg_fill_price": 0.0, "filled_amount": 0.0, "fee": 0.0, "fee_status": "not_applicable", "fee_source": "none", "message": "Huatai simulation reports accepted status only."})
        else:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "broker_status": "unknown", "filled_quantity": 0, "avg_fill_price": 0.0, "filled_amount": 0.0, "fee": 0.0, "fee_status": "not_applicable", "fee_source": "none", "message": "Huatai live backend is not configured."})
    response["results"] = results
    return response


def cancel_response(
    request: dict[str, Any],
    simulate: bool,
    config: dict[str, Any] | None = None,
    driver_factory: Any | None = None,
) -> dict[str, Any]:
    response = base_response(request)
    active_config = config or {}
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    if not simulate and desktop_canary_enabled(active_config):
        certified, reason = desktop_canary_certification_status(
            active_config,
            expected_config_sha256=str(request.get("auto_live_config_sha256", "")),
            allow_consumed=True,
        )
        if not certified or request_emergency_stop_active(request):
            message = reason if not certified else "Emergency stop active inside bridge; cancel driver was not created."
            response["results"] = [
                {
                    "ticket_id": order_ticket_id(order, index),
                    "broker_order_id": str(order.get("broker_order_id", "")) if isinstance(order, dict) else "",
                    "cancel_status": "failed",
                    "message": message,
                }
                for index, order in enumerate(orders, start=1)
            ]
            return response
        try:
            driver = driver_factory() if driver_factory else create_desktop_canary_driver(request)
        except Exception as exc:
            driver = None
            driver_error = f"Desktop Canary cancel driver unavailable: {type(exc).__name__}: {exc}"
        results = []
        for index, order in enumerate(orders, start=1):
            ticket_id = order_ticket_id(order, index)
            broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
            if driver is None:
                canceled = {"cancel_status": "failed", "message": driver_error}
            else:
                try:
                    canceled = driver.cancel(order if isinstance(order, dict) else {})
                except Exception as exc:
                    canceled = {"cancel_status": "unknown", "message": f"Huatai cancel state unknown: {type(exc).__name__}: {exc}"}
            results.append(
                {
                    "ticket_id": ticket_id,
                    "broker_order_id": broker_order_id,
                    "cancel_status": str(canceled.get("cancel_status", "unknown") or "unknown"),
                    "message": str(canceled.get("message", "")),
                }
            )
        response["results"] = results
        return response
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
        if simulate:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "cancel_status": "cancel_requested", "message": "Huatai simulation accepted the cancel request. No broker API was called."})
        else:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "cancel_status": "failed", "message": "Huatai live backend is not configured."})
    response["results"] = results
    return response


def account_snapshot_response(request: dict[str, Any], simulate: bool, config: dict[str, Any]) -> dict[str, Any]:
    response = base_response(request)
    if desktop_read_only_enabled(config) or desktop_canary_enabled(config):
        payload, error = read_desktop_snapshot(config)
        if error:
            response.update({"environment": "unknown", "account": {}, "positions": [], "message": error})
        else:
            response.update({"environment": "live", "account": payload["account"], "positions": payload["positions"], "broker_time": "", "generated_at": payload.get("generated_at", ""), "message": payload.get("message", "Huatai desktop read-only snapshot.") + " Broker server time is unavailable in desktop read-only mode."})
        return response
    if simulate:
        response.update(
            {
                "broker": BROKER_NAME,
                "environment": "simulation",
                "account": {
                    "account_id": "huatai-sim-account",
                    "cash": 1000000.0,
                    "available_cash": 1000000.0,
                    "market_value": 0.0,
                    "total_asset": 1000000.0,
                    "frozen_cash": 0.0,
                },
                "positions": [],
                "message": "Huatai simulation account snapshot. No broker API was called.",
            }
        )
    else:
        response.update(
            {
                "broker": BROKER_NAME,
                "environment": "unknown",
                "account": {"account_id": "", "cash": -1.0, "available_cash": -1.0, "market_value": -1.0, "total_asset": -1.0, "frozen_cash": -1.0},
                "positions": [],
                "message": "Huatai live backend is not configured.",
            }
        )
    return response


def failure_response(message: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = base_response(request or {})
    payload.update({"status": "failed", "capabilities": [], "message": message})
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    request_path = Path(args.request_path)
    result_path = Path(args.result_path)
    config = load_config(args.config)
    try:
        request = read_request(request_path)
    except Exception as exc:  # pragma: no cover - process boundary
        write_result(result_path, failure_response(f"Invalid request JSON: {exc}"))
        return 2
    integrity_ok, integrity_reason = verify_adapter_request_integrity(request)
    if not integrity_ok:
        write_result(result_path, failure_response(f"Request integrity failed: {integrity_reason}", request), request)
        return 2
    simulate = simulate_enabled(args, config)
    mode = str(request.get("mode", ""))
    if mode == "self_test":
        write_result(result_path, self_test_response(request, simulate, config), request)
        return 0
    if mode == "submit_orders":
        write_result(result_path, submit_response(request, simulate, config), request)
        return 0
    if mode == "query_orders":
        write_result(result_path, query_response(request, simulate, config), request)
        return 0
    if mode == "cancel_orders":
        write_result(result_path, cancel_response(request, simulate, config), request)
        return 0
    if mode == "account_snapshot":
        write_result(result_path, account_snapshot_response(request, simulate, config), request)
        return 0
    write_result(result_path, failure_response(f"Unsupported mode: {mode or 'missing'}", request), request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
