from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.message import EmailMessage
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.autolive_alerts import (  # noqa: E402
    ALERT_SCHEMA,
    finalize_alert_response,
    verify_alert_request,
)
from ashare_quant.live_secrets import read_live_secrets  # noqa: E402


ENV_PREFIX = "ASHARE_QUANT_SMTP_"
MAX_CONTEXT_CHARS = 6000


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMTP bridge for signed auto-live alerts.")
    parser.add_argument("--secret-file", default="", help="DPAPI encrypted credential file.")
    parser.add_argument("request_path")
    parser.add_argument("result_path")
    return parser.parse_args(argv)


def _required_env(name: str) -> str:
    value = os.environ.get(f"{ENV_PREFIX}{name}", "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable {ENV_PREFIX}{name}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def smtp_settings(secret_file: str | Path | None = None) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    if secret_file:
        payload = read_live_secrets(Path(secret_file))
        candidate = payload.get("smtp", {}) if isinstance(payload.get("smtp"), dict) else {}
        stored = dict(candidate)

    def value(name: str, stored_key: str, default: object = "") -> str:
        env_value = os.environ.get(f"{ENV_PREFIX}{name}")
        if env_value is not None and env_value.strip():
            return env_value.strip()
        return str(stored.get(stored_key, default) or "").strip()

    security = value("SECURITY", "security", "starttls").lower()
    if security not in {"starttls", "ssl"}:
        raise RuntimeError(f"{ENV_PREFIX}SECURITY must be starttls or ssl")
    default_port = 465 if security == "ssl" else 587
    try:
        port = int(value("PORT", "port", default_port))
        timeout = float(value("TIMEOUT", "timeout", 15))
    except ValueError as exc:
        raise RuntimeError("SMTP port and timeout must be numeric") from exc
    raw_recipients = os.environ.get(f"{ENV_PREFIX}TO")
    if raw_recipients is not None and raw_recipients.strip():
        recipients = [item.strip() for item in raw_recipients.replace(";", ",").split(",") if item.strip()]
    else:
        stored_recipients = stored.get("recipients", [])
        recipients = [str(item).strip() for item in stored_recipients if str(item).strip()] if isinstance(stored_recipients, list) else []
    required = {
        "host": value("HOST", "host"),
        "username": value("USERNAME", "username"),
        "password": value("PASSWORD", "password"),
        "sender": value("FROM", "sender"),
    }
    missing = [name for name, item in required.items() if not item]
    if missing:
        raise RuntimeError("missing SMTP settings: " + ",".join(f"{ENV_PREFIX}{name.upper()}" for name in missing))
    if not recipients:
        raise RuntimeError(f"{ENV_PREFIX}TO must contain at least one recipient")
    verify_value = os.environ.get(f"{ENV_PREFIX}VERIFY_TLS")
    verify_tls = _bool_env("VERIFY_TLS", True) if verify_value is not None else bool(stored.get("verify_tls", True))
    return {
        **required,
        "port": port,
        "recipients": recipients,
        "security": security,
        "timeout": max(timeout, 1.0),
        "verify_tls": verify_tls,
    }


def build_message(request: dict[str, Any], settings: dict[str, Any]) -> EmailMessage:
    mode = str(request.get("mode", ""))
    event_type = str(request.get("event_type", "unknown"))
    severity = str(request.get("severity", "info")).upper()
    message = str(request.get("message", ""))
    context = request.get("context", {})
    context_json = json.dumps(context if isinstance(context, dict) else {}, ensure_ascii=False, indent=2)
    if len(context_json) > MAX_CONTEXT_CHARS:
        context_json = context_json[:MAX_CONTEXT_CHARS] + "\n...[truncated]"
    subject = "A-share quant alert self-test" if mode == "self_test" else f"A-share quant [{severity}] {event_type}"
    body = (
        f"Mode: {mode}\n"
        f"Event: {event_type}\n"
        f"Severity: {severity}\n"
        f"Run ID: {request.get('run_id', '')}\n"
        f"Generated: {request.get('generated_at', '')}\n\n"
        f"{message}\n\nContext:\n{context_json}\n"
    )
    email = EmailMessage()
    email["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:180]
    email["From"] = settings["sender"]
    email["To"] = ", ".join(settings["recipients"])
    email.set_content(body)
    return email


def send_message(
    message: EmailMessage,
    settings: dict[str, Any],
    *,
    smtp_factory: Callable[..., Any] | None = None,
) -> None:
    context = ssl.create_default_context() if settings["verify_tls"] else ssl._create_unverified_context()
    factory = smtp_factory or (smtplib.SMTP_SSL if settings["security"] == "ssl" else smtplib.SMTP)
    kwargs = {"host": settings["host"], "port": settings["port"], "timeout": settings["timeout"]}
    if settings["security"] == "ssl":
        kwargs["context"] = context
    with factory(**kwargs) as client:
        if settings["security"] == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings["username"], settings["password"])
        client.send_message(message)


def write_result(path: Path, request: dict[str, Any], response: dict[str, Any]) -> None:
    payload = finalize_alert_response(request, response)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    request_path = Path(args.request_path)
    result_path = Path(args.result_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    valid, reason = verify_alert_request(request)
    base = {
        "schema_version": ALERT_SCHEMA,
        "run_id": str(request.get("run_id", "")),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not valid:
        write_result(result_path, request, {**base, "status": "failed", "environment": "unknown", "capabilities": [], "message": reason})
        return 2
    if request.get("mode") not in {"self_test", "deliver"}:
        write_result(result_path, request, {**base, "status": "failed", "environment": "unknown", "capabilities": [], "message": "unsupported alert mode"})
        return 2
    try:
        settings = smtp_settings(args.secret_file or None)
        send_message(build_message(request, settings), settings)
    except Exception as exc:
        # Never include SMTP credentials in bridge output or parent-process logs.
        message = f"SMTP delivery failed: {type(exc).__name__}: {exc}"
        write_result(result_path, request, {**base, "status": "failed", "environment": "live", "capabilities": ["alert"], "message": message[:400]})
        return 1
    status = "ok" if request.get("mode") == "self_test" else "delivered"
    write_result(result_path, request, {**base, "status": status, "environment": "live", "capabilities": ["alert"], "message": "SMTP message accepted by the configured server."})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
