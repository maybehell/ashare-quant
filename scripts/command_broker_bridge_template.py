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

SCHEMA_VERSION = "ashare_quant.command_adapter.v1"
BRIDGE_NAME = "command_broker_bridge_template"
SUBMIT_CAPABILITY = "submit_orders"
QUERY_CAPABILITY = "query_orders"
CANCEL_CAPABILITY = "cancel_orders"
ACCOUNT_CAPABILITY = "account_snapshot"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Broker-neutral command adapter template for ashare_quant auto-live integration."
    )
    parser.add_argument(
        "--simulate-submit",
        action="store_true",
        help="Return simulated submitted results. No broker API is called.",
    )
    parser.add_argument("request_path", help="Path to the JSON request written by ashare_quant.")
    parser.add_argument("result_path", help="Path where the bridge must write a JSON result.")
    return parser.parse_args(argv)


def simulation_enabled(args: argparse.Namespace) -> bool:
    value = os.environ.get("ASHARE_QUANT_BRIDGE_SIMULATE_SUBMIT", "")
    return bool(args.simulate_submit or value.strip().lower() in {"1", "true", "yes", "on"})


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
    }


def self_test_response(request: dict[str, Any], simulate: bool) -> dict[str, Any]:
    response = base_response(request)
    if request.get("schema_version") != SCHEMA_VERSION:
        response.update(
            {
                "status": "failed",
                "capabilities": [],
                "message": "Unsupported schema_version.",
            }
        )
        return response
    if simulate:
        response.update(
            {
                "status": "ok",
                "capabilities": [SUBMIT_CAPABILITY, QUERY_CAPABILITY, CANCEL_CAPABILITY, ACCOUNT_CAPABILITY],
                "message": "Simulation bridge is ready. No broker API will be called.",
            }
        )
        return response
    response.update(
        {
            "status": "failed",
            "capabilities": [],
            "message": "Template bridge is not connected to a broker. Implement submit_orders, query_orders, cancel_orders, and account_snapshot before enabling auto-live.",
        }
    )
    return response


def order_ticket_id(order: object, index: int) -> str:
    if isinstance(order, dict):
        value = order.get("ticket_id")
        if value is not None and str(value).strip():
            return str(value)
    return f"missing-ticket-{index}"


def request_emergency_stop_active(request: dict[str, Any]) -> bool:
    value = str(request.get("emergency_stop_path", "") or "").strip()
    return bool(value and Path(value).exists())


def submit_response(request: dict[str, Any], simulate: bool) -> dict[str, Any]:
    response = base_response(request)
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        if request_emergency_stop_active(request):
            results.append(
                {
                    "ticket_id": ticket_id,
                    "status": "failed",
                    "broker_order_id": "",
                    "message": "Emergency stop active inside bridge; broker API was not called for this order.",
                }
            )
            continue
        if simulate:
            results.append(
                {
                    "ticket_id": ticket_id,
                    "status": "submitted",
                    "broker_order_id": f"sim-{ticket_id}",
                    "message": "Simulation only; no broker API was called.",
                }
            )
        else:
            results.append(
                {
                    "ticket_id": ticket_id,
                    "status": "failed",
                    "broker_order_id": "",
                    "message": "Template bridge is not connected to a broker. Implement submit_orders, query_orders, cancel_orders, and account_snapshot before enabling auto-live.",
                }
            )
    response["results"] = results
    return response


def query_response(request: dict[str, Any], simulate: bool) -> dict[str, Any]:
    response = base_response(request)
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
        if simulate:
            results.append(
                {
                    "ticket_id": ticket_id,
                    "broker_order_id": broker_order_id,
                    "broker_status": "accepted",
                    "filled_quantity": 0,
                    "avg_fill_price": 0.0,
                    "filled_amount": 0.0,
                    "fee": 0.0,
                    "message": "Simulation only; broker order status was not queried.",
                }
            )
        else:
            results.append(
                {
                    "ticket_id": ticket_id,
                    "broker_order_id": broker_order_id,
                    "broker_status": "unknown",
                    "filled_quantity": 0,
                    "avg_fill_price": 0.0,
                    "filled_amount": 0.0,
                    "fee": 0.0,
                    "message": "Template bridge is not connected to a broker. Implement query_orders before enabling auto-live.",
                }
            )
    response["results"] = results
    return response


def cancel_response(request: dict[str, Any], simulate: bool) -> dict[str, Any]:
    response = base_response(request)
    raw_orders = request.get("orders", [])
    orders = raw_orders if isinstance(raw_orders, list) else []
    results: list[dict[str, Any]] = []
    for index, order in enumerate(orders, start=1):
        ticket_id = order_ticket_id(order, index)
        broker_order_id = str(order.get("broker_order_id", "")) if isinstance(order, dict) else ""
        if simulate:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "cancel_status": "cancel_requested", "message": "Simulation only; broker cancel was not sent."})
        else:
            results.append({"ticket_id": ticket_id, "broker_order_id": broker_order_id, "cancel_status": "failed", "message": "Template bridge is not connected to a broker. Implement cancel_orders before enabling auto-live."})
    response["results"] = results
    return response


def account_snapshot_response(request: dict[str, Any], simulate: bool) -> dict[str, Any]:
    response = base_response(request)
    if simulate:
        response.update(
            {
                "broker": "simulation",
                "environment": "simulation",
                "account": {
                    "account_id": "sim-account",
                    "cash": 1000000.0,
                    "available_cash": 1000000.0,
                    "market_value": 0.0,
                    "total_asset": 1000000.0,
                    "frozen_cash": 0.0,
                },
                "positions": [],
                "message": "Simulation only; broker account was not queried.",
            }
        )
    else:
        response.update(
            {
                "broker": "unconfigured",
                "environment": "unknown",
                "account": {"account_id": "", "cash": -1.0, "available_cash": -1.0, "market_value": -1.0, "total_asset": -1.0, "frozen_cash": -1.0},
                "positions": [],
                "message": "Template bridge is not connected to a broker. Implement account_snapshot before enabling auto-live.",
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
    try:
        request = read_request(request_path)
    except Exception as exc:  # pragma: no cover - defensive process boundary
        write_result(result_path, failure_response(f"Invalid request JSON: {exc}"))
        return 2

    integrity_ok, integrity_reason = verify_adapter_request_integrity(request)
    if not integrity_ok:
        write_result(result_path, failure_response(f"Request integrity failed: {integrity_reason}", request), request)
        return 2
    simulate = simulation_enabled(args)
    mode = str(request.get("mode", ""))
    if mode == "self_test":
        write_result(result_path, self_test_response(request, simulate), request)
        return 0
    if mode == "submit_orders":
        write_result(result_path, submit_response(request, simulate), request)
        return 0
    if mode == "query_orders":
        write_result(result_path, query_response(request, simulate), request)
        return 0
    if mode == "cancel_orders":
        write_result(result_path, cancel_response(request, simulate), request)
        return 0
    if mode == "account_snapshot":
        write_result(result_path, account_snapshot_response(request, simulate), request)
        return 0

    write_result(result_path, failure_response(f"Unsupported mode: {mode or 'missing'}", request), request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
