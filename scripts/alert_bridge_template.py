from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.autolive_alerts import (  # noqa: E402
    ALERT_SCHEMA,
    finalize_alert_response,
    verify_alert_request,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulation-only auto-live alert command bridge.")
    parser.add_argument("--simulate", action="store_true", help="Acknowledge alerts in simulation only.")
    parser.add_argument("request_path")
    parser.add_argument("result_path")
    return parser.parse_args(argv)


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
        write_result(result_path, request, {**base, "status": "failed", "environment": "simulation", "capabilities": [], "message": reason})
        return 2
    if not args.simulate:
        write_result(result_path, request, {**base, "status": "failed", "environment": "unknown", "capabilities": [], "message": "No real alert backend configured."})
        return 1
    if request.get("mode") == "self_test":
        response = {**base, "status": "ok", "environment": "simulation", "capabilities": ["alert"], "message": "Simulation alert bridge ready; no external notification was sent."}
    elif request.get("mode") == "deliver":
        response = {**base, "status": "delivered", "environment": "simulation", "capabilities": ["alert"], "message": "Simulation alert recorded; no external notification was sent."}
    else:
        response = {**base, "status": "failed", "environment": "simulation", "capabilities": ["alert"], "message": "Unsupported alert mode."}
    write_result(result_path, request, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
