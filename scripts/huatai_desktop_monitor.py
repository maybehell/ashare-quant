from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.huatai_desktop_monitor import probe_read_only_page  # noqa: E402


PAGES = {
    "orders": "当日委托",
    "trades": "当日成交",
    "cancels": "撤单[F3]",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Huatai read-only grid monitor.")
    parser.add_argument("--page", choices=sorted(PAGES), required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    target = args.output or f"outputs_live/huatai_{args.page}_probe.json"
    output = PROJECT_ROOT / target
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = probe_read_only_page(PAGES[args.page])
        payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception as exc:
        payload = {
            "page": PAGES[args.page],
            "status": "failed",
            "environment": "unknown",
            "broker": "huatai",
            "submit_invoked": False,
            "cancel_invoked": False,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": f"{type(exc).__name__}: {exc}",
        }
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(f"Huatai {args.page} probe: {payload['status']}")
    return 0 if payload["status"] == "empty_verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
