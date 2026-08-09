from __future__ import annotations

import argparse
from pathlib import Path
import re
import secrets
import sys
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.live_secrets import write_live_secrets  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure DPAPI-protected live credentials.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--output", default="data/secrets/live_credentials.dpapi")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    window = tk.Tk()
    window.title("量化系统安全凭据")
    window.geometry("520x330")
    window.resizable(False, False)
    window.configure(bg="#f5f5f7")

    frame = ttk.Frame(window, padding=28)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="本机安全凭据", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
    ttk.Label(frame, text="信息使用 Windows DPAPI 加密，仅当前 Windows 用户可解密。", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 20))

    ttk.Label(frame, text="QQ 邮箱").pack(anchor="w")
    email = ttk.Entry(frame)
    email.insert(0, args.email)
    email.configure(state="readonly")
    email.pack(fill="x", pady=(4, 12))

    ttk.Label(frame, text="QQ 邮箱授权码（不是 QQ 密码）").pack(anchor="w")
    smtp_code = ttk.Entry(frame, show="*")
    smtp_code.pack(fill="x", pady=(4, 12))

    ttk.Label(frame, text="华泰客户号 / 登录账号").pack(anchor="w")
    account_id = ttk.Entry(frame, show="*")
    account_id.pack(fill="x", pady=(4, 18))

    status = tk.StringVar(value="")

    def save() -> None:
        code = smtp_code.get().strip().replace(" ", "")
        account = account_id.get().strip()
        if not re.fullmatch(r"[^@\s]+@qq\.com", args.email, re.IGNORECASE):
            messagebox.showerror("无法保存", "邮箱必须是有效的 QQ 邮箱地址。", parent=window)
            return
        if len(code) < 8:
            messagebox.showerror("无法保存", "请输入 QQ 邮箱生成的 SMTP 授权码。", parent=window)
            return
        if len(account) < 4:
            messagebox.showerror("无法保存", "请输入华泰客户号或登录账号。", parent=window)
            return
        payload = {
            "schema_version": "ashare_quant.live_credentials.v1",
            "smtp": {
                "host": "smtp.qq.com",
                "port": 465,
                "security": "ssl",
                "username": args.email,
                "password": code,
                "sender": args.email,
                "recipients": [args.email],
                "verify_tls": True,
            },
            "huatai": {"account_id": account},
            "integrity": {"hmac_key": secrets.token_urlsafe(48)},
        }
        try:
            target = write_live_secrets(PROJECT_ROOT / args.output, payload)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=window)
            return
        smtp_code.delete(0, "end")
        account_id.delete(0, "end")
        status.set(f"已加密保存到 {target.name}")
        messagebox.showinfo("配置完成", "凭据已使用 Windows DPAPI 加密保存。", parent=window)
        window.destroy()

    ttk.Button(frame, text="加密保存", command=save).pack(fill="x")
    ttk.Label(frame, textvariable=status).pack(anchor="w", pady=(8, 0))
    smtp_code.focus_set()
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
