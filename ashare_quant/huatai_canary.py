from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol


HARD_MAX_CANARY_ORDERS = 1
HARD_MAX_CANARY_AMOUNT = Decimal("1000")


@dataclass(frozen=True)
class CanaryOrder:
    ticket_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Decimal

    @property
    def amount(self) -> Decimal:
        return self.limit_price * self.quantity


@dataclass(frozen=True)
class BrokerSubmission:
    status: str
    broker_order_id: str = ""
    message: str = ""


class CanarySubmitDriver(Protocol):
    def submit(self, order: CanaryOrder, *, run_id: str) -> BrokerSubmission: ...


HEADER_ALIASES = {
    "broker_order_id": {"合同编号", "委托编号", "申报编号", "合同号"},
    "symbol": {"证券代码", "股票代码", "代码"},
    "name": {"证券名称", "股票名称", "名称"},
    "side": {"买卖标志", "买卖方向", "操作", "方向"},
    "quantity": {"委托数量", "申报数量", "数量"},
    "limit_price": {"委托价格", "申报价格", "价格"},
    "filled_quantity": {"成交数量", "已成数量", "成交股数"},
    "avg_fill_price": {"成交均价", "成交价格", "均价"},
    "status": {"委托状态", "状态说明", "状态", "备注"},
}

TRADE_HEADER_ALIASES = {
    "broker_order_id": {"\u5408\u540c\u7f16\u53f7", "\u59d4\u6258\u7f16\u53f7", "\u7533\u62a5\u7f16\u53f7", "\u5408\u540c\u53f7"},
    "trade_id": {"\u6210\u4ea4\u7f16\u53f7", "\u6210\u4ea4\u5e8f\u53f7", "\u6210\u4ea4\u53f7"},
    "symbol": {"\u8bc1\u5238\u4ee3\u7801", "\u80a1\u7968\u4ee3\u7801", "\u4ee3\u7801"},
    "side": {"\u4e70\u5356\u6807\u5fd7", "\u4e70\u5356\u65b9\u5411", "\u64cd\u4f5c", "\u65b9\u5411"},
    "filled_quantity": {"\u6210\u4ea4\u6570\u91cf", "\u6210\u4ea4\u80a1\u6570", "\u6210\u4ea4\u6570"},
    "fill_price": {"\u6210\u4ea4\u4ef7\u683c", "\u6210\u4ea4\u4ef7", "\u6210\u4ea4\u5747\u4ef7"},
    "filled_amount": {"\u6210\u4ea4\u91d1\u989d"},
    "total_fee": {"\u8d39\u7528\u5408\u8ba1", "\u624b\u7eed\u8d39\u5408\u8ba1", "\u603b\u8d39\u7528", "\u8d39\u7528"},
    "commission": {"\u4f63\u91d1", "\u624b\u7eed\u8d39"},
    "stamp_tax": {"\u5370\u82b1\u7a0e"},
    "transfer_fee": {"\u8fc7\u6237\u8d39"},
    "other_fee": {"\u5176\u4ed6\u8d39", "\u89c4\u8d39"},
    "trade_date": {"\u6210\u4ea4\u65e5\u671f", "\u4ea4\u6613\u65e5\u671f", "\u65e5\u671f"},
    "trade_time": {"\u6210\u4ea4\u65f6\u95f4", "\u4ea4\u6613\u65f6\u95f4", "\u65f6\u95f4"},
}


STATUS_ALIASES = {
    "accepted": ("已报", "已确认", "未成交", "已受理"),
    "partially_filled": ("部成", "部分成交"),
    "filled": ("已成", "全部成交", "已成交"),
    "canceled": ("已撤", "撤单", "已撤单"),
    "rejected": ("废单", "拒绝", "未报", "错误"),
}


def validate_canary_orders(raw_orders: object, config: dict[str, Any]) -> list[CanaryOrder]:
    if not isinstance(raw_orders, list):
        raise ValueError("orders must be a list")
    policy = _canary_policy(config)
    max_orders = min(int(policy.get("max_orders", HARD_MAX_CANARY_ORDERS)), HARD_MAX_CANARY_ORDERS)
    max_amount = min(_decimal(policy.get("max_order_amount", HARD_MAX_CANARY_AMOUNT)), HARD_MAX_CANARY_AMOUNT)
    if len(raw_orders) != 1 or len(raw_orders) > max_orders:
        raise ValueError("desktop Canary requires exactly one order")
    orders = [_normalize_order(raw_orders[0])]
    order = orders[0]
    if order.amount > max_amount:
        raise ValueError(f"Canary order amount {order.amount} exceeds hard limit {max_amount}")
    return orders


def submit_desktop_canary(
    request: dict[str, Any],
    config: dict[str, Any],
    driver: CanarySubmitDriver,
) -> list[dict[str, Any]]:
    policy = _canary_policy(config)
    if not bool(policy.get("enabled", False)):
        raise RuntimeError("desktop Canary backend is disabled")
    if not bool(policy.get("allow_submit", False)):
        raise RuntimeError("desktop Canary submit switch is disabled")
    orders = validate_canary_orders(request.get("orders", []), config)
    run_id = str(request.get("run_id", "") or "").strip()
    if not run_id:
        raise RuntimeError("desktop Canary request run_id is missing")
    results: list[dict[str, Any]] = []
    for order in orders:
        try:
            submission = driver.submit(order, run_id=run_id)
            status = str(submission.status or "unknown").lower()
            if status not in {"submitted", "rejected", "failed", "unknown"}:
                status = "unknown"
            broker_order_id = str(submission.broker_order_id or "")
            if status == "submitted" and not broker_order_id:
                status = "unknown"
            results.append(
                {
                    "ticket_id": order.ticket_id,
                    "status": status,
                    "broker_order_id": broker_order_id,
                    "message": submission.message or "Huatai desktop Canary submission completed.",
                }
            )
        except Exception as exc:
            # Once the desktop submit path is entered, an exception cannot prove
            # that the broker rejected the order. Unknown prevents blind retry.
            results.append(
                {
                    "ticket_id": order.ticket_id,
                    "status": "unknown",
                    "broker_order_id": "",
                    "message": f"Huatai desktop submission state is unknown: {type(exc).__name__}: {exc}",
                }
            )
    return results


def desktop_canary_certification_status(
    config: dict[str, Any],
    *,
    expected_config_sha256: str,
    now: datetime | None = None,
    required_stage: str | None = None,
    allow_consumed: bool = False,
) -> tuple[bool, str]:
    policy = _canary_policy(config)
    value = str(policy.get("certification_path", "") or "").strip()
    if not value:
        return False, "desktop_canary_certification_path_missing"
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "desktop_canary_certification_unreadable"
    if not isinstance(payload, dict) or payload.get("schema_version") != "ashare_quant.huatai_desktop_canary_certification.v1":
        return False, "desktop_canary_certification_schema_invalid"
    status = str(payload.get("status", "")).lower()
    if status == "consumed":
        if not allow_consumed:
            return False, "desktop_canary_arm_already_consumed"
        stage = str(payload.get("certification_stage", "")).lower()
    else:
        stage = status
    required_stage = str(required_stage or policy.get("required_certification_stage", "certified")).lower()
    if required_stage not in {"armed", "certified"}:
        return False, "desktop_canary_required_stage_invalid"
    if stage not in {"armed", "certified"}:
        return False, "desktop_canary_not_armed"
    if required_stage == "certified" and stage != "certified":
        return False, "desktop_canary_full_lifecycle_not_certified"
    if str(payload.get("auto_live_config_sha256", "")) != str(expected_config_sha256):
        return False, "desktop_canary_certification_config_mismatch"
    armed_required = [
        "submit_form_preview",
        "empty_order_monitor",
        "account_snapshot_live",
        "operator_confirmation_required",
    ]
    full_required = ["submit_contract", "query_contract", "cancel_contract", "nonempty_order_parser"]
    required = armed_required + (full_required if required_stage == "certified" else [])
    missing = [item for item in required if payload.get(item) is not True]
    if missing:
        return False, "desktop_canary_certification_incomplete:" + ";".join(missing)
    try:
        max_orders = int(payload.get("max_orders", 0))
        max_amount = Decimal(str(payload.get("max_order_amount", 0)))
    except (ValueError, InvalidOperation):
        return False, "desktop_canary_certification_limits_invalid"
    if max_orders != 1 or max_amount <= 0 or max_amount > HARD_MAX_CANARY_AMOUNT:
        return False, "desktop_canary_certification_limits_invalid"
    hmac_required = bool(policy.get("require_hmac", False))
    if hmac_required:
        key_env = str(policy.get("hmac_key_env", "ASHARE_QUANT_ADAPTER_HMAC_KEY"))
        key = os.environ.get(key_env, "").encode("utf-8")
        if len(key) < 32:
            return False, "desktop_canary_certification_hmac_key_missing"
        expected_hmac = certification_hmac_sha256(payload, key)
        if not hmac.compare_digest(str(payload.get("hmac_sha256", "")), expected_hmac):
            return False, "desktop_canary_certification_hmac_invalid"
    try:
        expires = datetime.fromisoformat(str(payload.get("expires_at", "")))
    except ValueError:
        return False, "desktop_canary_certification_expiry_invalid"
    if expires.tzinfo is None:
        return False, "desktop_canary_certification_expiry_invalid"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("certification time must be timezone-aware")
    if current.astimezone(timezone.utc) > expires.astimezone(timezone.utc):
        return False, "desktop_canary_certification_expired"
    return True, f"consumed_{stage}" if status == "consumed" else stage


def certification_hmac_sha256(payload: dict[str, Any], key: bytes) -> str:
    canonical = {name: value for name, value in payload.items() if name != "hmac_sha256"}
    message = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def consume_desktop_canary_arm(
    config: dict[str, Any],
    *,
    expected_config_sha256: str,
    run_id: str,
    orders: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[bool, str]:
    valid, stage = desktop_canary_certification_status(
        config,
        expected_config_sha256=expected_config_sha256,
        now=now,
        required_stage="armed",
    )
    if not valid:
        return False, stage
    if stage != "armed":
        return True, "certified_not_consumed"
    policy = _canary_policy(config)
    value = str(policy.get("certification_path", "") or "").strip()
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    lock_path = path.with_name(f".{path.name}.consume.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError:
        return False, "desktop_canary_arm_consume_lock_exists"
    try:
        valid, stage = desktop_canary_certification_status(
            config,
            expected_config_sha256=expected_config_sha256,
            now=now,
            required_stage="armed",
        )
        if not valid or stage != "armed":
            return False, stage
        payload = json.loads(path.read_text(encoding="utf-8"))
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Canary consumption time must be timezone-aware")
        canonical_orders = json.dumps(orders, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload.update(
            {
                "status": "consumed",
                "certification_stage": "armed",
                "consumed_run_id": str(run_id),
                "consumed_orders_sha256": hashlib.sha256(canonical_orders.encode("utf-8")).hexdigest(),
                "consumed_at": current.astimezone(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        if bool(policy.get("require_hmac", False)):
            key_env = str(policy.get("hmac_key_env", "ASHARE_QUANT_ADAPTER_HMAC_KEY"))
            key = os.environ.get(key_env, "").encode("utf-8")
            if len(key) < 32:
                return False, "desktop_canary_certification_hmac_key_missing"
            payload["hmac_sha256"] = certification_hmac_sha256(payload, key)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return True, "consumed_armed"
    finally:
        lock_path.unlink(missing_ok=True)


def parse_order_grid_text(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
    if len(lines) < 2:
        return []
    delimiter = "\t" if "\t" in lines[0] else None
    headers = _split_row(lines[0], delimiter)
    mapped = [_canonical_header(value) for value in headers]
    if "symbol" not in mapped or "status" not in mapped:
        raise ValueError("Huatai order grid headers are not recognized")
    rows: list[dict[str, Any]] = []
    recognized = [value for value in mapped if value]
    if len(recognized) != len(set(recognized)):
        raise ValueError("Huatai order grid contains duplicate canonical headers")
    for line in lines[1:]:
        values = _split_row(line, delimiter)
        if len(values) != len(headers):
            raise ValueError("Huatai order grid row width does not match headers")
        row = {key: value.strip() for key, value in zip(mapped, values) if key}
        quantity = _integer(row.get("quantity", 0))
        filled_quantity = _integer(row.get("filled_quantity", 0))
        broker_status = _normalize_status(row.get("status", ""))
        if quantity <= 0:
            raise ValueError("Huatai order grid contains a non-positive order quantity")
        if filled_quantity < 0 or filled_quantity > quantity:
            raise ValueError("Huatai order grid filled quantity is outside the order quantity")
        if broker_status == "filled" and filled_quantity != quantity:
            raise ValueError("Huatai filled order does not have full filled quantity")
        if broker_status == "partially_filled" and not 0 < filled_quantity < quantity:
            raise ValueError("Huatai partially filled order has inconsistent filled quantity")
        symbol = _normalize_symbol(row.get("symbol", ""))
        if not symbol:
            continue
        rows.append(
            {
                "broker_order_id": str(row.get("broker_order_id", "")),
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "side": _normalize_side(row.get("side", "")),
                "quantity": quantity,
                "limit_price": _float(row.get("limit_price", 0)),
                "filled_quantity": filled_quantity,
                "avg_fill_price": _float(row.get("avg_fill_price", 0)),
                "broker_status": broker_status,
                "raw_status": str(row.get("status", "")),
            }
        )
    return rows


def parse_trade_grid_text(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
    if len(lines) < 2:
        return []
    delimiter = "\t" if "\t" in lines[0] else None
    headers = _split_row(lines[0], delimiter)
    mapped = [_canonical_trade_header(value) for value in headers]
    required = {"broker_order_id", "symbol", "side", "filled_quantity", "fill_price"}
    if not required.issubset(set(mapped)):
        raise ValueError("Huatai trade grid headers are not recognized")
    recognized = [value for value in mapped if value]
    if len(recognized) != len(set(recognized)):
        raise ValueError("Huatai trade grid contains duplicate canonical headers")
    has_total_fee = "total_fee" in mapped
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        values = _split_row(line, delimiter)
        if len(values) != len(headers):
            raise ValueError("Huatai trade grid row width does not match headers")
        row = {key: value.strip() for key, value in zip(mapped, values) if key}
        symbol = _normalize_symbol(row.get("symbol", ""))
        if not symbol:
            continue
        quantity = _integer(row.get("filled_quantity", 0))
        price = _float(row.get("fill_price", 0))
        if quantity <= 0 or price <= 0:
            raise ValueError("Huatai trade grid contains a non-positive fill")
        amount_text = str(row.get("filled_amount", "")).strip()
        amount = _float(amount_text) if amount_text else quantity * price
        if amount <= 0:
            raise ValueError("Huatai trade grid contains a non-positive amount")
        fee_text = str(row.get("total_fee", "")).strip()
        fee = _float(fee_text) if fee_text else 0.0
        exact_fee = has_total_fee and bool(fee_text) and fee >= 0
        rows.append(
            {
                "broker_order_id": str(row.get("broker_order_id", "")),
                "trade_id": str(row.get("trade_id", "")),
                "symbol": symbol,
                "side": _normalize_side(row.get("side", "")),
                "filled_quantity": quantity,
                "fill_price": price,
                "filled_amount": amount,
                "fee": fee if exact_fee else 0.0,
                "fee_status": "exact" if exact_fee else "unavailable",
                "fee_source": "huatai_trade_grid_total_fee" if exact_fee else "huatai_trade_grid_no_total_fee",
                "trade_date": str(row.get("trade_date", "")),
                "trade_time": str(row.get("trade_time", "")),
            }
        )
    return rows


def aggregate_trade_rows(rows: list[dict[str, Any]], broker_order_id: str) -> dict[str, Any] | None:
    target = str(broker_order_id or "").strip()
    matches = [row for row in rows if target and str(row.get("broker_order_id", "")).strip() == target]
    if not matches:
        return None
    trade_ids = [str(row.get("trade_id", "")).strip() for row in matches if str(row.get("trade_id", "")).strip()]
    if len(trade_ids) != len(matches):
        raise RuntimeError("missing Huatai trade id for broker order")
    if len(trade_ids) != len(set(trade_ids)):
        raise RuntimeError("duplicate Huatai trade id for broker order")
    symbols = {str(row.get("symbol", "")) for row in matches}
    sides = {str(row.get("side", "")) for row in matches if str(row.get("side", ""))}
    if len(symbols) != 1 or len(sides) > 1:
        raise RuntimeError("Huatai trade rows disagree on symbol or side")
    quantity = sum(int(row.get("filled_quantity", 0)) for row in matches)
    amount = sum(float(row.get("filled_amount", 0)) for row in matches)
    if quantity <= 0 or amount <= 0:
        raise RuntimeError("Huatai aggregate trade quantity or amount is invalid")
    exact_fee = all(str(row.get("fee_status", "")) == "exact" for row in matches)
    fee = sum(float(row.get("fee", 0)) for row in matches) if exact_fee else 0.0
    dates = {str(row.get("trade_date", "")).strip() for row in matches if str(row.get("trade_date", "")).strip()}
    return {
        "broker_order_id": target,
        "symbol": next(iter(symbols)),
        "side": next(iter(sides)) if sides else "",
        "filled_quantity": quantity,
        "avg_fill_price": amount / quantity,
        "filled_amount": amount,
        "fee": fee,
        "fee_status": "exact" if exact_fee else "unavailable",
        "fee_source": "huatai_trade_grid_total_fee" if exact_fee else "huatai_trade_grid_no_total_fee",
        "trade_date": next(iter(dates)) if len(dates) == 1 else "",
        "trade_count": len(matches),
    }


def match_order_row(rows: list[dict[str, Any]], order: CanaryOrder) -> dict[str, Any] | None:
    matches = [
        row
        for row in rows
        if str(row.get("symbol", "")) == order.symbol
        and str(row.get("side", "")) == order.side
        and int(row.get("quantity", 0)) == order.quantity
        and abs(Decimal(str(row.get("limit_price", 0))) - order.limit_price) <= Decimal("0.001")
    ]
    if len(matches) > 1:
        raise RuntimeError("multiple Huatai order rows match the Canary order")
    return matches[0] if matches else None


def _normalize_order(value: object) -> CanaryOrder:
    if not isinstance(value, dict):
        raise ValueError("order must be an object")
    ticket_id = str(value.get("ticket_id", "") or "").strip()
    symbol = _normalize_symbol(value.get("symbol", ""))
    side = _normalize_side(value.get("side", ""))
    quantity = _integer(value.get("quantity", 0))
    price = _decimal(value.get("limit_price", 0))
    if not ticket_id:
        raise ValueError("ticket_id is required")
    if not symbol:
        raise ValueError("symbol must contain exactly six digits")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if side == "buy" and quantity % 100:
        raise ValueError("buy quantity must be a 100-share lot")
    if price <= 0 or price > Decimal("10000"):
        raise ValueError("limit_price must be in (0, 10000]")
    return CanaryOrder(ticket_id, symbol, side, quantity, price)


def _canary_policy(config: dict[str, Any]) -> dict[str, Any]:
    huatai = config.get("huatai", config)
    if not isinstance(huatai, dict):
        return {}
    value = huatai.get("desktop_canary", {})
    return value if isinstance(value, dict) else {}


def _canonical_header(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value))
    for canonical, aliases in HEADER_ALIASES.items():
        if compact in aliases:
            return canonical
    return ""


def _canonical_trade_header(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value))
    for canonical, aliases in TRADE_HEADER_ALIASES.items():
        if compact in aliases:
            return canonical
    return ""


def _split_row(line: str, delimiter: str | None) -> list[str]:
    return line.split(delimiter) if delimiter else re.split(r"\s{2,}", line.strip())


def _normalize_symbol(value: object) -> str:
    digits = "".join(re.findall(r"\d", str(value or "")))
    return digits[-6:] if len(digits) >= 6 else ""


def _normalize_side(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "b", "买入", "买"} or "买" in text:
        return "buy"
    if text in {"sell", "s", "卖出", "卖"} or "卖" in text:
        return "sell"
    return text


def _normalize_status(value: object) -> str:
    text = str(value or "").strip()
    for status, aliases in STATUS_ALIASES.items():
        if any(alias in text for alias in aliases):
            return status
    return "unknown"


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {value}") from exc


def _integer(value: object) -> int:
    try:
        return int(Decimal(str(value).replace(",", "").strip()))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid integer value: {value}") from exc


def _float(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value: {value}") from exc
