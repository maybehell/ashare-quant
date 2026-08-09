from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.huatai_desktop_rpa import (  # noqa: E402
    HuataiDesktopPreviewDriver,
    run_form_preview,
    validate_preview_order,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Huatai live form preview. Submission is not implemented.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["buy", "sell"], required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--output", default="outputs_live/huatai_form_preview.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    output = PROJECT_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        order = validate_preview_order(args.symbol, args.side, args.price, args.quantity)
        payload = run_form_preview(HuataiDesktopPreviewDriver(), order)
        payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception as exc:
        payload = {
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
        print(f"Huatai form preview failed: {exc}")
        return 2
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(
        f"Huatai form preview verified: {payload['side']} {payload['symbol']} "
        f"x{payload['quantity']}; submit_invoked=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
