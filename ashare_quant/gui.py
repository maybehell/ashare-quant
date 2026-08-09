from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import BOTH, DISABLED, END, LEFT, NORMAL, BooleanVar, StringVar, Text, Tk
from tkinter import messagebox
from tkinter import ttk

from .challengers import primary_challenger_config_paths
from .config import load_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
LIVE_OUTPUT_DIR = PROJECT_ROOT / "outputs_live"
REPORT_PATH = OUTPUT_DIR / "report.html"
LOCAL_STATUS_PATH = OUTPUT_DIR / "local_operator_status.html"

COLORS = {
    "bg": "#f5f5f7",
    "card": "#ffffff",
    "line": "#d2d2d7",
    "text": "#1d1d1f",
    "muted": "#6e6e73",
    "blue": "#007aff",
    "green": "#34c759",
    "orange": "#ff9500",
    "red": "#ff3b30",
}


class QuantGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("A 股量化系统")
        self.root.geometry("1280x860")
        self.root.minsize(1120, 760)
        self.running = False
        self.current_process: subprocess.Popen[str] | None = None
        self.pending_commands: list[list[str]] = []
        self.task_started_at: float | None = None
        self.task_command = ""

        self.config_path = StringVar(value="")
        self.max_symbols = StringVar(value="800")
        self.use_metadata = BooleanVar(value=True)
        self.daily_refresh = BooleanVar(value=False)
        self.allow_stale = BooleanVar(value=False)
        self.cached_universe = BooleanVar(value=True)
        self.allow_weak_strategy = BooleanVar(value=False)
        self.reset_account_before_practical = BooleanVar(value=False)
        self.status_text = StringVar(value="就绪")
        self.command_text = StringVar(value="")
        self.data_badge = StringVar(value="读取中")
        self.next_action_text = StringVar(value="正在读取门禁状态。")
        self.primary_action_text = StringVar(value="执行建议")
        self.primary_action_commands: list[list[str]] = [["source-health"]]

        self.kpi_vars = {
            "total_return": StringVar(value="-"),
            "excess_return": StringVar(value="-"),
            "max_drawdown": StringVar(value="-"),
            "local_status": StringVar(value="-"),
            "broker_status": StringVar(value="-"),
            "reliability": StringVar(value="-"),
            "practical_status": StringVar(value="-"),
            "strict_status": StringVar(value="-"),
            "execution_status": StringVar(value="-"),
            "order_status": StringVar(value="-"),
        }

        self.configure_style()
        self.build_layout()
        self.refresh_summary()

    def configure_style(self) -> None:
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Root.TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"], relief="flat", borderwidth=1)
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 10))
        style.configure("CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("CardValue.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Section.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("ProfileTitle.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("ProfileRole.TLabel", background=COLORS["card"], foreground=COLORS["blue"], font=("Microsoft YaHei UI", 9))
        style.configure("Body.TLabel", background=COLORS["card"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
        style.configure("Status.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("Accent.TButton", background=COLORS["blue"], foreground="#ffffff", padding=(14, 8), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#0a84ff"), ("disabled", "#a6a6aa")], foreground=[("disabled", "#ffffff")])
        style.configure("Soft.TButton", background="#e8e8ed", foreground=COLORS["text"], padding=(12, 8), borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#dedee3")])
        style.configure("Danger.TButton", background="#ffe5e5", foreground=COLORS["red"], padding=(12, 8), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#ffd6d6")])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=COLORS["line"], padding=6)
        style.configure("TCheckbutton", background=COLORS["card"], foreground=COLORS["text"])
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background="#e8e8ed", padding=(14, 8), foreground=COLORS["muted"])
        style.map("TNotebook.Tab", background=[("selected", COLORS["card"])], foreground=[("selected", COLORS["text"])])

    def build_layout(self) -> None:
        self.root.title("A 股量化系统")
        self.root.geometry("1120x760")
        self.root.minsize(980, 680)

        outer = ttk.Frame(self.root, style="Root.TFrame", padding=24)
        outer.pack(fill=BOTH, expand=True)

        header = ttk.Frame(outer, style="Root.TFrame")
        header.pack(fill="x")
        title_box = ttk.Frame(header, style="Root.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="A 股量化系统", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="盘后研究、模拟盘与华泰只读对账；默认禁止提交真实订单。", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_text, style="Status.TLabel").pack(side="right")

        kpis = ttk.Frame(outer, style="Root.TFrame")
        kpis.pack(fill="x", pady=(22, 16))
        cards = [
            ("策略收益", "total_return"),
            ("超额收益", "excess_return"),
            ("最大回撤", "max_drawdown"),
            ("华泰只读", "broker_status"),
        ]
        for idx, (label, key) in enumerate(cards):
            self.metric_card(kpis, label, self.kpi_vars[key], idx)
        for col in range(4):
            kpis.columnconfigure(col, weight=1)

        main = ttk.Frame(outer, style="Root.TFrame")
        main.pack(fill=BOTH, expand=True)
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        control = ttk.Frame(main, style="Card.TFrame", padding=22)
        control.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        ttk.Label(control, text="日常运行", style="ProfileTitle.TLabel").pack(anchor="w")
        ttk.Label(control, textvariable=self.data_badge, style="Muted.TLabel", wraplength=280).pack(anchor="w", pady=(10, 18))
        ttk.Button(control, text="一键运行系统", style="Accent.TButton", command=self.run_one_click).pack(fill="x", pady=(0, 10))
        ttk.Button(control, text="停止当前任务", style="Danger.TButton", command=self.stop_current).pack(fill="x", pady=(0, 10))
        ttk.Button(control, text="刷新状态", style="Soft.TButton", command=self.refresh_summary).pack(fill="x", pady=(0, 10))
        ttk.Button(control, text="打开报告", style="Soft.TButton", command=self.open_report).pack(fill="x", pady=(0, 10))
        ttk.Button(control, text="打开输出目录", style="Soft.TButton", command=self.open_outputs).pack(fill="x")
        ttk.Separator(control).pack(fill="x", pady=18)
        ttk.Label(control, text="当前建议", style="Body.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(control, textvariable=self.next_action_text, style="Muted.TLabel", wraplength=280).pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(main, style="Root.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)

        log_card = ttk.Frame(right, style="Card.TFrame", padding=14)
        log_card.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        ttk.Label(log_card, text="运行日志", style="ProfileTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.log = Text(log_card, wrap="word", bg="#111111", fg="#f5f5f7", insertbackground="#f5f5f7", relief="flat", padx=12, pady=12)
        self.log.pack(fill=BOTH, expand=True)

        summary_card = ttk.Frame(right, style="Card.TFrame", padding=14)
        summary_card.grid(row=1, column=0, sticky="nsew")
        ttk.Label(summary_card, text="状态摘要", style="ProfileTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.summary = Text(summary_card, wrap="word", bg=COLORS["card"], fg=COLORS["text"], relief="flat", padx=12, pady=12)
        self.summary.pack(fill=BOTH, expand=True)

        footer = ttk.Frame(outer, style="Root.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, textvariable=self.command_text, style="Subtitle.TLabel").pack(anchor="w")

        hidden = ttk.Frame(outer, style="Root.TFrame")
        self.default_profile = Text(hidden)
        self.adaptive_profile = Text(hidden)
        self.execution_dashboard = Text(hidden)

    def metric_card(self, parent: ttk.Frame, label: str, value: StringVar, column: int, row: int = 0) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 8, 0 if column == 3 else 8),
            pady=(0 if row == 0 else 8, 0),
        )
        ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=value, style="CardValue.TLabel").pack(anchor="w", pady=(8, 0))

    def build_sidebar(self, sidebar: ttk.Frame) -> None:
        tabs = ttk.Notebook(sidebar)
        tabs.pack(fill=BOTH, expand=True)

        daily = ttk.Frame(tabs, style="Card.TFrame", padding=14)
        settings = ttk.Frame(tabs, style="Card.TFrame", padding=14)
        tabs.add(daily, text="日常")
        tabs.add(settings, text="设置")

        ttk.Label(daily, text="数据状态", style="Body.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(daily, textvariable=self.data_badge, style="Muted.TLabel", wraplength=238).pack(anchor="w", pady=(8, 10))
        ttk.Label(daily, text="下一步建议", style="Body.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(daily, textvariable=self.next_action_text, style="Muted.TLabel", wraplength=238).pack(anchor="w", pady=(6, 10))
        ttk.Button(daily, textvariable=self.primary_action_text, style="Accent.TButton", command=self.run_primary_action).pack(fill="x", pady=(0, 8))
        ttk.Button(daily, text="实用一键流程", style="Soft.TButton", command=self.run_practical).pack(fill="x", pady=(0, 8))
        ttk.Button(daily, text="严格日常流程", style="Soft.TButton", command=self.run_daily).pack(fill="x", pady=4)
        ttk.Button(daily, text="完整研究流程", style="Soft.TButton", command=self.run_research).pack(fill="x", pady=4)
        ttk.Separator(daily).pack(fill="x", pady=12)
        ttk.Button(daily, text="刷新本地股票池", style="Soft.TButton", command=lambda: self.run_command(self.cached_fetch_command())).pack(fill="x", pady=4)
        ttk.Button(daily, text="网络测试", style="Soft.TButton", command=lambda: self.run_command(["probe"])).pack(fill="x", pady=4)
        ttk.Button(daily, text="数据源健康", style="Soft.TButton", command=lambda: self.run_command(["source-health"])).pack(fill="x", pady=4)
        ttk.Button(daily, text="环境自检", style="Soft.TButton", command=lambda: self.run_command(["doctor"])).pack(fill="x", pady=4)
        ttk.Separator(daily).pack(fill="x", pady=12)
        ttk.Button(daily, text="打开基线报告", style="Soft.TButton", command=self.open_report).pack(fill="x", pady=4)
        ttk.Button(daily, text="打开候选报告", style="Soft.TButton", command=self.open_adaptive_report).pack(fill="x", pady=4)
        ttk.Button(daily, text="刷新状态", style="Soft.TButton", command=self.refresh_summary).pack(fill="x", pady=4)
        ttk.Button(daily, text="停止当前任务", style="Danger.TButton", command=self.stop_current).pack(fill="x", pady=(12, 0))

        ttk.Label(settings, text="运行参数", style="Body.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(settings, text="配置文件（留空使用默认配置）", style="Muted.TLabel").pack(anchor="w", pady=(14, 4))
        ttk.Entry(settings, textvariable=self.config_path, width=32).pack(fill="x")
        ttk.Label(settings, text="行情池数量", style="Muted.TLabel").pack(anchor="w", pady=(12, 4))
        ttk.Entry(settings, textvariable=self.max_symbols, width=12).pack(fill="x")
        ttk.Checkbutton(settings, text="补全行业/上市日期", variable=self.use_metadata).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(settings, text="刷新时使用本地股票池", variable=self.cached_universe).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(settings, text="日常流程先刷新行情", variable=self.daily_refresh).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(settings, text="允许历史缓存演练", variable=self.allow_stale).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(settings, text="允许弱策略演练", variable=self.allow_weak_strategy).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(settings, text="实用流程前重置模拟账户", variable=self.reset_account_before_practical).pack(anchor="w", pady=(6, 10))
        ttk.Separator(settings).pack(fill="x", pady=10)
        ttk.Button(settings, text="刷新远程行情池", style="Soft.TButton", command=lambda: self.run_command(self.fetch_command())).pack(fill="x", pady=4)
        ttk.Button(settings, text="账户状态", style="Soft.TButton", command=lambda: self.run_command(["account-state"])).pack(fill="x", pady=4)
        ttk.Button(settings, text="真实账户模板", style="Soft.TButton", command=lambda: self.run_command(["account-template"])).pack(fill="x", pady=4)
        ttk.Button(settings, text="重置模拟账户", style="Danger.TButton", command=self.run_account_reset).pack(fill="x", pady=(10, 4))
    def build_workspace(self, workspace: ttk.Frame) -> None:
        quick = ttk.Frame(workspace, style="Root.TFrame")
        quick.pack(fill="x", pady=(0, 12))
        quick_buttons = [
            ("候选研究", ["research"]),
            ("基线对比", ["baselines"]),
            ("正式对比", ["compare"]),
            ("只刷基准", ["fetch", "--benchmark-only"]),
            ("回测", ["backtest"]),
            ("压力测试", ["stress"]),
            ("观察记录", ["observe", "--quiet"]),
            ("冻结候选", ["freeze-candidate"]),
            ("实用门禁", ["ready"]),
            ("执行就绪", ["execution-ready"]),
            ("账户计划", ["account-plan"]),
            ("账户修复", ["account-repair-template"]),
            ("导入预检", ["account-import-check", "--file", "outputs/account_import_from_state_template.csv"]),
            ("可估值预检", ["account-import-check", "--file", "outputs/account_import_priced_only_template.csv"]),
            ("实盘路线图", ["roadmap"]),
            ("实用模拟", ["paper", "--practical"]),
            ("导出工单", ["export-orders"]),
            ("预览工单", ["rpa-preview"]),
            ("刷新报告", ["report"]),
        ]
        for idx, (label, command) in enumerate(quick_buttons):
            row = idx // 5
            col = idx % 5
            ttk.Button(quick, text=label, style="Soft.TButton", command=lambda cmd=command: self.run_command(cmd)).grid(
                row=row, column=col, sticky="we", padx=(0 if col == 0 else 6, 0), pady=(0 if row == 0 else 6, 0)
            )
            quick.columnconfigure(col, weight=1)

        notebook = ttk.Notebook(workspace)
        notebook.pack(fill=BOTH, expand=True)
        profiles_frame = ttk.Frame(notebook, style="Root.TFrame", padding=12)
        profiles_header = ttk.Frame(profiles_frame, style="Root.TFrame")
        profiles_header.pack(fill="x", pady=(0, 10))
        profiles_title = ttk.Frame(profiles_header, style="Root.TFrame")
        profiles_title.pack(side="left", fill="x", expand=True)
        ttk.Label(profiles_title, text="策略档案", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            profiles_title,
            text="回测可信度与严格实盘门禁分开显示；候选策略只有前向证据达标后才能晋级。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        profile_actions = ttk.Frame(profiles_header, style="Root.TFrame")
        profile_actions.pack(side="right")
        ttk.Button(profile_actions, text="基线报告", style="Soft.TButton", command=self.open_report).pack(side="left", padx=(0, 6))
        ttk.Button(profile_actions, text="候选报告", style="Soft.TButton", command=self.open_adaptive_report).pack(side="left")

        profile_cards = ttk.Frame(profiles_frame, style="Root.TFrame")
        profile_cards.pack(fill=BOTH, expand=True)
        profile_cards.columnconfigure(0, weight=1, uniform="profile")
        profile_cards.columnconfigure(1, weight=1, uniform="profile")
        profile_cards.rowconfigure(0, weight=1)
        self.default_profile = self.strategy_profile_card(profile_cards, "默认基线策略", "研究 / 模拟盘基线", 0)
        self.adaptive_profile = self.strategy_profile_card(profile_cards, "自适应候选策略", "冻结观察中", 1)

        profiles_footer = ttk.Frame(profiles_frame, style="Root.TFrame")
        profiles_footer.pack(fill="x", pady=(10, 0))
        ttk.Button(profiles_footer, text="打开基线输出", style="Soft.TButton", command=self.open_outputs).pack(side="left")
        ttk.Button(profiles_footer, text="打开候选输出", style="Soft.TButton", command=self.open_adaptive_outputs).pack(side="left", padx=(6, 0))
        notebook.add(profiles_frame, text="策略档案")

        log_frame = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        self.log = Text(log_frame, wrap="word", bg="#111111", fg="#f5f5f7", insertbackground="#f5f5f7", relief="flat", padx=12, pady=12)
        self.log.pack(fill=BOTH, expand=True)
        notebook.add(log_frame, text="运行日志")

        summary_frame = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        self.summary = Text(summary_frame, wrap="word", bg=COLORS["card"], fg=COLORS["text"], relief="flat", padx=12, pady=12)
        self.summary.pack(fill=BOTH, expand=True)
        notebook.add(summary_frame, text="当前摘要")

        execution_frame = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        self.execution_dashboard = Text(execution_frame, wrap="word", bg=COLORS["card"], fg=COLORS["text"], relief="flat", padx=12, pady=12)
        self.execution_dashboard.pack(fill=BOTH, expand=True)
        notebook.add(execution_frame, text="执行准备")

        guide_frame = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        self.guide = Text(guide_frame, wrap="word", bg=COLORS["card"], fg=COLORS["text"], relief="flat", padx=12, pady=12)
        self.guide.pack(fill=BOTH, expand=True)
        self.guide.insert(END, GUIDE_TEXT)
        self.guide.configure(state=DISABLED)
        notebook.add(guide_frame, text="运行手册")

        footer = ttk.Frame(workspace, style="Root.TFrame")
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, textvariable=self.command_text, style="Subtitle.TLabel").pack(anchor="w")

    def strategy_profile_card(self, parent: ttk.Frame, title: str, role: str, column: int) -> Text:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
        ttk.Label(card, text=title, style="ProfileTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=role, style="ProfileRole.TLabel").pack(anchor="w", pady=(3, 10))

        content_frame = ttk.Frame(card, style="Card.TFrame")
        content_frame.pack(fill=BOTH, expand=True)
        content = Text(
            content_frame,
            wrap="word",
            bg=COLORS["card"],
            fg=COLORS["text"],
            relief="flat",
            padx=0,
            pady=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )
        scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=content.yview)
        content.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        content.pack(side="left", fill=BOTH, expand=True)
        content.configure(state=DISABLED)
        return content
    def fetch_command(self) -> list[str]:
        command = ["fetch", "--max-symbols", self.max_symbols.get().strip() or "800"]
        if not self.use_metadata.get():
            command.append("--no-metadata")
        if self.cached_universe.get():
            command.append("--cached-universe")
        return command

    def cached_fetch_command(self) -> list[str]:
        command = ["fetch", "--cached-universe", "--max-symbols", self.max_symbols.get().strip() or "800"]
        if not self.use_metadata.get():
            command.append("--no-metadata")
        return command

    def daily_command(self) -> list[str]:
        command = ["daily"]
        if self.daily_refresh.get():
            command.extend(["--refresh", "--max-symbols", self.max_symbols.get().strip() or "800"])
            if not self.use_metadata.get():
                command.append("--no-metadata")
            if self.cached_universe.get():
                command.append("--cached-universe")
        if self.allow_stale.get():
            command.append("--allow-stale")
        if self.allow_weak_strategy.get():
            command.append("--allow-weak-strategy")
        return command

    def practical_command(self) -> list[str]:
        command = ["practical"]
        if self.daily_refresh.get():
            command.extend(["--refresh", "--max-symbols", self.max_symbols.get().strip() or "800"])
            if not self.use_metadata.get():
                command.append("--no-metadata")
            if self.cached_universe.get():
                command.append("--cached-universe")
        if self.allow_stale.get():
            command.append("--allow-stale")
        if self.reset_account_before_practical.get():
            command.extend(["--reset-account", "--confirm-account-reset"])
        return command

    def with_config(self, command: list[str]) -> list[str]:
        config = self.config_path.get().strip()
        if config:
            return ["--config", config, *command]
        return command

    def run_daily(self) -> None:
        self.run_command(self.daily_command())

    def run_practical(self) -> None:
        self.run_command(self.practical_command())

    def run_one_click(self) -> None:
        command = ["local-run", "--broker-optional"]
        if self.daily_refresh.get():
            command.extend(["--refresh", "--max-symbols", self.max_symbols.get().strip() or "800"])
            if not self.use_metadata.get():
                command.append("--no-metadata")
            if self.cached_universe.get():
                command.append("--cached-universe")
        if self.allow_stale.get():
            command.append("--allow-stale")
        if not self.use_metadata.get() and "--no-metadata" not in command:
            command.append("--no-metadata")
        self.run_command(command)

    def run_primary_action(self) -> None:
        commands = [list(command) for command in self.primary_action_commands if command]
        if not commands:
            commands = [["source-health"]]
        self.run_sequence(commands)

    def run_research(self) -> None:
        self.run_command(["workflow"])

    def run_account_reset(self) -> None:
        confirmed = messagebox.askyesno(
            "重置模拟账户",
            "这会清空本地模拟账户的持仓、订单、成交和旧 RPA 文件，并重置为现金账户。确认继续？",
        )
        if confirmed:
            self.run_command(["account-reset", "--confirm"])

    def run_sequence(self, commands: list[list[str]]) -> None:
        if self.running:
            self.append_log("已有任务正在运行，请等待完成。\n")
            return
        self.pending_commands = [self.with_config(command) for command in commands]
        self.start_next_command()

    def run_command(self, command: list[str]) -> None:
        if self.running:
            self.append_log("已有任务正在运行，请等待完成。\n")
            return
        self.pending_commands = [self.with_config(list(command))]
        self.start_next_command()

    def start_next_command(self) -> None:
        if not self.pending_commands:
            self.running = False
            self.status_text.set("\u5b8c\u6210")
            self.command_text.set("")
            self.refresh_summary()
            return
        args = self.pending_commands.pop(0)
        self.running = True
        self.task_started_at = time.monotonic()
        self.task_command = "python -m ashare_quant " + " ".join(args)
        self.current_process = None
        self.status_text.set("\u8fd0\u884c\u4e2d")
        self.command_text.set(self.task_command)
        self.append_log(f"\n$ python -m ashare_quant {' '.join(args)}\n")
        thread = threading.Thread(target=self._run_subprocess, args=(args,), daemon=True)
        thread.start()
        self.root.after(30000, self.check_running_task)

    def _run_subprocess(self, args: list[str]) -> None:
        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            self.current_process = subprocess.Popen(
                [sys.executable, "-m", "ashare_quant", *args],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert self.current_process.stdout is not None
            for line in self.current_process.stdout:
                self.root.after(0, self.append_log, line)
            code = self.current_process.wait()
            self.root.after(0, self.finish_command, code)
        except Exception as exc:
            self.root.after(0, self.append_log, f"\u4efb\u52a1\u5931\u8d25: {exc}\n")
            self.root.after(0, self.finish_command, 1)

    def finish_command(self, code: int) -> None:
        self.append_log(f"\u9000\u51fa\u7801: {code}\n")
        self.current_process = None
        self.task_started_at = None
        self.task_command = ""
        if code == 0 and self.pending_commands:
            self.start_next_command()
            return
        self.pending_commands = []
        self.running = False
        self.status_text.set("\u5b8c\u6210" if code == 0 else f"\u5931\u8d25: {code}")
        self.refresh_summary()

    def check_running_task(self) -> None:
        if not self.running:
            return
        elapsed = time.monotonic() - self.task_started_at if self.task_started_at else 0.0
        process = self.current_process
        if process is None and elapsed > 5:
            self.append_log("\u672a\u53d1\u73b0\u540e\u53f0\u8fdb\u7a0b\uff0c\u5df2\u81ea\u52a8\u91ca\u653e\u4efb\u52a1\u9501\u3002\u53ef\u91cd\u65b0\u8fd0\u884c\u3002\n")
            self.pending_commands = []
            self.running = False
            self.task_started_at = None
            self.task_command = ""
            self.status_text.set("\u5df2\u89e3\u9501")
            self.command_text.set("")
            self.refresh_summary()
            return
        if process is not None:
            code = process.poll()
            if code is not None:
                self.finish_command(code)
                return
        self.append_log(f"\u4efb\u52a1\u4ecd\u5728\u8fd0\u884c\uff0c\u5df2\u8017\u65f6 {format_elapsed(elapsed)}\u3002\u5f53\u524d\u547d\u4ee4\uff1a{self.task_command or '-'}\n")
        self.root.after(30000, self.check_running_task)

    def stop_current(self) -> None:
        if self.current_process and self.current_process.poll() is None:
            self.pending_commands = []
            self.current_process.terminate()
            self.append_log("\u5df2\u8bf7\u6c42\u505c\u6b62\u5f53\u524d\u4efb\u52a1\u3002\n")
        elif self.running:
            self.pending_commands = []
            self.running = False
            self.current_process = None
            self.task_started_at = None
            self.task_command = ""
            self.command_text.set("")
            self.status_text.set("\u5df2\u89e3\u9501")
            self.append_log("\u672a\u53d1\u73b0\u540e\u53f0\u8fdb\u7a0b\uff0c\u5df2\u6e05\u7406\u4efb\u52a1\u72b6\u6001\u3002\u53ef\u91cd\u65b0\u8fd0\u884c\u3002\n")
            self.refresh_summary()
        else:
            self.append_log("\u5f53\u524d\u6ca1\u6709\u6b63\u5728\u8fd0\u884c\u7684\u4efb\u52a1\u3002\n")

    def append_log(self, text: str) -> None:
        self.log.configure(state=NORMAL)
        self.log.insert(END, text)
        self.log.see(END)

    def refresh_summary(self) -> None:
        challenger_output_dir = primary_challenger_output_dir(self.config_path.get())
        metrics = read_key_value_csv(OUTPUT_DIR / "performance_metrics.csv", "metric", "value")
        quality = read_key_value_csv(OUTPUT_DIR / "data_quality.csv", "item", "value")
        source_health = read_rows(OUTPUT_DIR / "data_source_health.csv")
        manifest = read_key_value_csv(OUTPUT_DIR / "run_manifest.csv", "item", "value")
        tickets = read_rows(OUTPUT_DIR / "rpa_orders.csv")
        research = read_rows(OUTPUT_DIR / "strategy_research.csv")
        compare = read_rows(OUTPUT_DIR / "strategy_backtest_compare.csv")
        promotion = read_rows(OUTPUT_DIR / "strategy_promotion.csv")
        readiness = read_rows(OUTPUT_DIR / "live_readiness.csv")
        practical = read_rows(OUTPUT_DIR / "practical_readiness.csv")
        reliability = read_rows(OUTPUT_DIR / "strategy_reliability.csv")
        candidate_stability = read_rows(OUTPUT_DIR / "strategy_candidate_stability.csv")
        candidate_forward_progress = read_rows(OUTPUT_DIR / "live_candidate_forward_progress.csv")
        execution = read_rows(OUTPUT_DIR / "execution_readiness.csv")
        execution_modes = read_rows(OUTPUT_DIR / "execution_modes.csv")
        execution_roadmap = read_rows(OUTPUT_DIR / "execution_readiness_roadmap.csv")
        order_source = read_rows(OUTPUT_DIR / "order_source_diagnostics.csv")
        account_state = read_rows(OUTPUT_DIR / "account_state_audit.csv")
        account_reconciliation = read_rows(OUTPUT_DIR / "account_reconciliation.csv")
        account_plan = read_rows(OUTPUT_DIR / "account_readiness_plan.csv")
        account_path_decision = read_rows(OUTPUT_DIR / "account_path_decision.csv")
        account_repair = read_rows(OUTPUT_DIR / "account_repair_review.csv")
        account_excluded = read_rows(OUTPUT_DIR / "account_repair_excluded_positions.csv")
        account_import_check = read_rows(OUTPUT_DIR / "account_import_check_audit.csv")
        auto_live_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_summary.csv")
        auto_live_journal_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_journal_summary.csv")
        auto_live_binding_audit = read_rows(LIVE_OUTPUT_DIR / "auto_live_account_binding_audit.csv")
        auto_live_recovery_points = read_rows(LIVE_OUTPUT_DIR / "auto_live_recovery_points.csv")
        auto_live_recovery_audit = read_rows(LIVE_OUTPUT_DIR / "auto_live_recovery_audit.csv")
        auto_live_alert_audit = read_rows(LIVE_OUTPUT_DIR / "auto_live_alert_audit.csv")
        auto_live_alert_outbox = read_rows(LIVE_OUTPUT_DIR / "auto_live_alert_outbox.csv")
        auto_live_rollout_audit = read_rows(LIVE_OUTPUT_DIR / "auto_live_rollout_audit.csv")
        auto_live_readiness = read_rows(LIVE_OUTPUT_DIR / "auto_live_readiness.csv")
        auto_live_cycle_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_cycle_summary.csv")
        auto_live_batch_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_batch_summary.csv")
        adapter_replay_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_adapter_replay_summary.csv")
        auto_live_service_summary = read_rows(LIVE_OUTPUT_DIR / "auto_live_service_summary.csv")
        auto_live_service_heartbeat = read_rows(LIVE_OUTPUT_DIR / "auto_live_service_heartbeat.csv")
        local_operator = read_rows(OUTPUT_DIR / "local_operator_status.csv")
        daily_observation = read_rows(OUTPUT_DIR / "daily_observation_summary.csv")
        daily_observation_audit = read_rows(OUTPUT_DIR / "daily_observation_audit.csv")
        deployment_gate = read_rows(OUTPUT_DIR / "deployment_gate.csv")

        self.kpi_vars["total_return"].set(format_percent(metrics.get("total_return")))
        self.kpi_vars["excess_return"].set(format_percent(metrics.get("excess_total_return")))
        self.kpi_vars["max_drawdown"].set(format_percent(metrics.get("max_drawdown")))
        self.kpi_vars["reliability"].set(reliability_label(reliability))
        self.kpi_vars["practical_status"].set(readiness_status(practical))
        self.kpi_vars["strict_status"].set(readiness_status(readiness))
        self.kpi_vars["execution_status"].set(execution_status(execution))
        self.kpi_vars["local_status"].set(local_operator_status_label(local_operator))
        self.kpi_vars["broker_status"].set(
            deployment_gate_label(
                deployment_gate, "broker_read_only_ready", "已连接", "待登录"
            )
        )

        ready = sum(1 for row in tickets if row.get("status") == "ready")
        blocked = sum(1 for row in tickets if row.get("status") and row.get("status") != "ready")
        self.kpi_vars["order_status"].set(f"{ready} ready / {blocked} blocked")

        latest_price = quality.get("price_end", manifest.get("prices_last_date", "-"))
        age = inferred_age_days(latest_price) or quality.get("price_cache_age_days", "")
        self.data_badge.set(cache_badge(latest_price, age))
        self.set_readonly_text(
            self.default_profile,
            strategy_profile_summary(OUTPUT_DIR, "默认基线策略"),
        )
        self.set_readonly_text(
            self.adaptive_profile,
            strategy_profile_summary(challenger_output_dir, "主要挑战者"),
        )

        failed_checks = [row for row in readiness if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_practical = [row for row in practical if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_source_health = [row for row in source_health if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_reliability = [row for row in reliability if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_candidate_stability = [row for row in candidate_stability if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_execution = [row for row in execution if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_auto_live = [row for row in auto_live_readiness if str(row.get("passed", "")).lower() not in {"true", "1"}]
        failed_daily_observation = [row for row in daily_observation_audit if str(row.get("passed", "")).lower() not in {"true", "1"}]
        latest_cycle = auto_live_cycle_summary[0] if auto_live_cycle_summary else {}
        latest_journal = auto_live_journal_summary[0] if auto_live_journal_summary else {}
        binding_failed = [row for row in auto_live_binding_audit if str(row.get("passed", "")).lower() not in {"true", "1"}]
        recovery_failed = [row for row in auto_live_recovery_audit if str(row.get("passed", "")).lower() not in {"true", "1"}]
        alert_failed = [row for row in auto_live_alert_audit if str(row.get("passed", "")).lower() not in {"true", "1"}]
        pending_alerts = [row for row in auto_live_alert_outbox if str(row.get("status", "")).lower() == "pending"]
        rollout_failed = [row for row in auto_live_rollout_audit if str(row.get("passed", "")).lower() not in {"true", "1"}]
        latest_batch = auto_live_batch_summary[0] if auto_live_batch_summary else {}
        latest_replay = adapter_replay_summary[0] if adapter_replay_summary else {}
        latest_service = auto_live_service_summary[0] if auto_live_service_summary else {}
        latest_observation = daily_observation[0] if daily_observation else {}
        observation_labels = {
            "not_run": "\u672a\u542f\u7528",
            "running": "\u6b63\u5728\u8fd0\u884c",
            "completed": "\u6700\u8fd1\u8fd0\u884c\u6210\u529f",
            "failed": "\u6700\u8fd1\u8fd0\u884c\u5931\u8d25",
            "stale": "\u7ed3\u679c\u5df2\u8fc7\u671f",
            "stale_lock": "\u4efb\u52a1\u9501\u5f02\u5e38",
        }
        observation_status = observation_labels.get(str(latest_observation.get("status", "not_run")), "\u672a\u77e5")
        latest_heartbeat = auto_live_service_heartbeat[0] if auto_live_service_heartbeat else {}
        advice = recommended_action(source_health, practical, readiness, execution, order_source, promotion)
        operator_action = local_operator_next_action(local_operator)
        self.next_action_text.set(operator_action or str(advice.get("message", "-")))
        self.primary_action_text.set(str(advice.get("label", "执行建议")))
        self.primary_action_commands = [list(command) for command in advice.get("commands", [["source-health"]])]  # type: ignore[arg-type]
        best_research = research[0] if research else {}
        best_formal = compare[0] if compare else {}
        lines = [
            "本地运行",
            f"  状态: {local_operator_status_label(local_operator)}",
            f"  策略模式: {local_operator_item_value(local_operator, 'strategy_operating_mode')}",
            f"  自动观察: {local_operator_item_value(local_operator, 'observation_daemon')}",
            f"  正式候选: {local_operator_item_value(local_operator, 'formal_forward_candidate')}",
            f"  券商阶段: {local_operator_item_value(local_operator, 'broker_deployment_roadmap')}",
            f"  华泰客户端: {local_operator_item_value(local_operator, 'huatai_client')}",
            f"  只读账户刷新: {local_operator_item_value(local_operator, 'huatai_read_only_refresh')}",
            f"  安全默认状态: {local_operator_item_value(local_operator, 'safe_default_state')}",
            f"  下一步: {operator_action or advice.get('message', '-')}",
            "",
            "实盘部署门禁",
            f"  策略证据: {deployment_gate_label(deployment_gate, 'strategy_ready')}",
            f"  华泰只读: {deployment_gate_label(deployment_gate, 'broker_read_only_ready', '已连接', '待登录')}",
            f"  执行链路: {deployment_gate_label(deployment_gate, 'broker_execution_ready', '已就绪', '未就绪')}",
            f"  安全停机: {deployment_gate_label(deployment_gate, 'safe_default', '生效', '异常')}",
            f"  Canary 申请: {deployment_gate_label(deployment_gate, 'ready_to_request_canary', '可申请', '不可申请')}",
            f"  完整策略资金: {deployment_gate_label(deployment_gate, 'full_strategy_capital_ready', '满足', '不满足')}",
            f"  资金运行模式: {deployment_gate_value(deployment_gate, 'capital_operating_mode')}",
            f"  完整策略复核: {deployment_gate_label(deployment_gate, 'ready_for_live_strategy_review', '可申请', '不可申请')}",
            "",
            "\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf",
            f"  \u72b6\u6001: {observation_status}",
            f"  \u6a21\u5f0f: {latest_observation.get('mode', 'observation_only')}",
            f"  \u6700\u8fd1\u5b8c\u6210: {latest_observation.get('finished_at', '-') or '-'}",
            f"  \u7ed3\u679c\u5e74\u9f84: {latest_observation.get('age_hours', '-') or '-'} \u5c0f\u65f6",
            f"  \u5ba1\u8ba1\u672a\u901a\u8fc7: {len(failed_daily_observation)}",
            "",
            "绩效",
            f"  最新权益: {metrics.get('latest_equity', '-')}",
            f"  策略收益: {format_percent(metrics.get('total_return'))}",
            f"  基准收益: {format_percent(metrics.get('benchmark_total_return'))}",
            f"  超额收益: {format_percent(metrics.get('excess_total_return'))}",
            f"  最大回撤: {format_percent(metrics.get('max_drawdown'))}",
            f"  信息比率: {format_float(metrics.get('information_ratio'))}",
            f"  年化换手: {format_percent(metrics.get('annualized_turnover'))}",
            f"  可信度: {reliability_label(reliability)}",
            "",
            "操作建议",
            f"  {advice.get('message', '-')}",
            f"  主按钮: {advice.get('label', '-')}",
            "",
            "数据",
            f"  股票列表: {quality.get('stock_count', '-')}",
            f"  有行情股票: {quality.get('price_symbol_count', '-')}",
            f"  行情结束: {latest_price}",
            f"  缓存年龄: {age or '-'} 天",
            f"  数据源健康未通过: {len(failed_source_health)}",
            *failed_check_lines(failed_source_health, prefix="  数据源: ", value_key="next_action"),
            f"  基准结束: {quality.get('benchmark_end', manifest.get('benchmark_last_date', '-'))}",
            f"  策略: {quality.get('strategy_name', manifest.get('strategy_name', '-'))}",
            "",
            "策略研究",
            f"  当前候选第一名: {best_research.get('name', '-')}",
            f"  第一名反过拟合通过: {best_research.get('anti_overfit_passed', '-')}",
            f"  第一名样本内超额: {format_percent(best_research.get('in_sample_excess_return'))}",
            f"  第一名样本外超额: {format_percent(best_research.get('out_of_sample_excess_return'))}",
            f"  第一名收益集中度: {format_float(best_research.get('recent_excess_concentration'))}",
            f"  正式回测第一名: {best_formal.get('strategy_name', '-')}",
            f"  正式第一名超额: {format_percent(best_formal.get('excess_total_return'))}",
            *strategy_promotion_lines(promotion, prefix="  Promotion: "),
            "",
            "可信度与过拟合",
            f"  未通过检查数: {len(failed_reliability)}",
            *failed_check_lines(failed_reliability, prefix="  "),
            f"  候选稳定性未通过检查数: {len(failed_candidate_stability)}",
            *failed_check_lines(failed_candidate_stability, prefix="  候选: "),
            *candidate_forward_progress_lines(candidate_forward_progress, prefix="  前向: "),
            "",
            "门禁",
            f"  实用门禁未通过检查数: {len(failed_practical)}",
            f"  严格实盘未通过检查数: {len(failed_checks)}",
            *failed_check_lines(failed_practical, prefix="  实用: "),
            *failed_check_lines(failed_checks, prefix="  严格: "),
            "",
            "执行就绪",
            f"  状态: {execution_status(execution)}",
            f"  阻断/待处理项: {len(failed_execution)}",
            *failed_check_lines(failed_execution, prefix="  ", value_key="next_action"),
            *roadmap_lines(execution_roadmap, prefix="  路线: "),
            "",
            "自动实盘门控",
            f"  状态: {auto_live_status_text(auto_live_summary)}",
            f"  允许自动实盘: {auto_live_allowed_text(auto_live_summary)}",
            f"  未通过项: {len(failed_auto_live)}",
            f"  审计链: {latest_journal.get('status', '未生成')}，事件 {latest_journal.get('event_count', 0)} 条，校验 {latest_journal.get('passed_checks', 0)}/{latest_journal.get('total_checks', 0)}",
            f"  真实账户绑定: {'通过' if auto_live_binding_audit and not binding_failed else ('未要求' if not auto_live_binding_audit else '未通过')}，失败项 {len(binding_failed)}",
            f"  提交前恢复点: {len(auto_live_recovery_points)} 个，恢复校验失败项 {len(recovery_failed)}",
            f"  外部告警: 校验失败项 {len(alert_failed)}，待发送 {len(pending_alerts)} 条",
            f"  分阶段投产: 授权校验失败项 {len(rollout_failed)}",
            f"  适配器回放认证: {latest_replay.get('status', '未认证')} ({latest_replay.get('profile', '-')})",
            *failed_check_lines(failed_auto_live, prefix="  ", value_key="next_action"),
            "",
            "无人值守服务",
            f"  状态: {latest_service.get('status', '未运行')}",
            f"  模式: {latest_service.get('mode', '-')}",
            f"  周期/阻断/异常: {latest_service.get('cycle_count', '0')}/{latest_service.get('blocked_cycle_count', '0')}/{latest_service.get('failed_cycle_count', '0')}",
            f"  最近心跳: {latest_heartbeat.get('heartbeat_at', '-')}",
            f"  下次运行: {latest_heartbeat.get('next_run_at', '-') or '-'}",
            "",
            "自动实盘批次",
            f"  状态: {latest_batch.get('status', '未规划')}",
            f"  原始/当前/排队订单: {latest_batch.get('source_order_count', '0')}/{latest_batch.get('selected_order_count', '0')}/{latest_batch.get('queued_order_count', '0')}",
            f"  当前批次: {latest_batch.get('selected_batch_no', '0')}/{latest_batch.get('batch_count', '0')}",
            f"  活动批次: {latest_batch.get('active_batch_count', '0')}",
            "",
            "最近自动实盘周期",
            f"  状态: {latest_cycle.get('status', '未运行')}",
            f"  模式: {latest_cycle.get('mode', '-')}",
            f"  阻断阶段: {latest_cycle.get('blocking_stage', '-') or '-'}",
            f"  提交/查询/成交: {latest_cycle.get('submitted_count', '0')}/{latest_cycle.get('queried_count', '0')}/{latest_cycle.get('fill_count', '0')}",
            f"  账户同步: {latest_cycle.get('account_sync_completed', 'False')}",
            "",
            "账户准备",
            *account_plan_lines(account_plan, prefix="  "),
            *account_repair_lines(account_repair, prefix="  "),
            *account_excluded_lines(account_excluded, prefix="  "),
            *account_import_check_lines(account_import_check, prefix="  "),
            "",
            "订单工单",
            f"  ready: {ready}",
            f"  blocked: {blocked}",
        ]
        if tickets:
            lines.append(f"  最近订单日期: {max(row.get('date', '') for row in tickets)}")
        lines.extend(
            [
                "",
                "建议",
                "  实用一键流程会检查回测超额、样本外、走步验证、成本压力、账户审计和 RPA 前置条件。",
                "  严格实盘会额外要求样本内稳定、冻结后前向验证和 shadow 观察，不通过时只应继续模拟盘。",
                "  完整研究流程会依次运行候选策略比较、正式回测、压力测试和报告生成。",
                "  RPA 工单只用于辅助填字段，最终提交必须人工核对。",
            ]
        )

        self.summary.configure(state=NORMAL)
        self.summary.delete("1.0", END)
        self.summary.insert(END, "\n".join(lines))
        self.summary.configure(state=DISABLED)

        execution_lines = execution_dashboard_lines(
            execution=execution,
            execution_modes=execution_modes,
            roadmap=execution_roadmap,
            account_plan=account_plan,
            account_path_decision=account_path_decision,
            order_source=order_source,
            account_state=account_state,
            account_reconciliation=account_reconciliation,
            account_import_check=account_import_check,
            tickets=tickets,
        )
        self.execution_dashboard.configure(state=NORMAL)
        self.execution_dashboard.delete("1.0", END)
        self.execution_dashboard.insert(END, "\n".join(execution_lines))
        self.execution_dashboard.configure(state=DISABLED)

    def set_readonly_text(self, widget: Text, lines: list[str]) -> None:
        widget.configure(state=NORMAL)
        widget.delete("1.0", END)
        widget.insert(END, "\n".join(lines))
        widget.configure(state=DISABLED)

    def open_report(self) -> None:
        path = LOCAL_STATUS_PATH if LOCAL_STATUS_PATH.exists() else REPORT_PATH
        self.open_report_path(path, "本地运行状态")

    def open_adaptive_report(self) -> None:
        path = primary_challenger_output_dir(self.config_path.get()) / "report.html"
        self.open_report_path(path, "主要挑战者报告")

    def open_report_path(self, path: Path, label: str) -> None:
        if path.exists():
            webbrowser.open(path.resolve().as_uri())
        else:
            self.append_log(f"{label}不存在，请先用对应配置运行 backtest、stress 和 report。\n")

    def open_outputs(self) -> None:
        self.open_output_path(OUTPUT_DIR)

    def open_adaptive_outputs(self) -> None:
        self.open_output_path(primary_challenger_output_dir(self.config_path.get()))

    def open_output_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path.resolve())  # type: ignore[attr-defined]


def one_click_commands() -> list[list[str]]:
    return [["local-run", "--broker-optional"]]


def primary_challenger_output_dir(config_path: str | Path | None = None) -> Path:
    """Resolve the configured challenger instead of pinning the UI to an old experiment."""
    try:
        selected = str(config_path or "").strip()
        strategy_config_path = Path(selected) if selected else None
        if strategy_config_path is not None and not strategy_config_path.is_absolute():
            strategy_config_path = PROJECT_ROOT / strategy_config_path
        strategy_config = load_config(strategy_config_path)
        challengers = primary_challenger_config_paths(strategy_config)
        challenger_value = challengers[0] if challengers else ""
        if not challenger_value:
            return OUTPUT_DIR
        challenger_path = Path(challenger_value)
        if not challenger_path.is_absolute():
            challenger_path = PROJECT_ROOT / challenger_path
        challenger_config = load_config(challenger_path)
        output_path = Path(str(challenger_config.get("paths", {}).get("output_dir", "outputs") or "outputs"))
        return output_path if output_path.is_absolute() else PROJECT_ROOT / output_path
    except (OSError, RuntimeError, ValueError):
        return OUTPUT_DIR


def local_operator_item_value(rows: list[dict[str, str]], item: str, default: str = "-") -> str:
    for row in reversed(rows):
        if str(row.get("item", "")).strip() == item:
            value = str(row.get("value", "")).strip()
            return value or default
    return default


def deployment_gate_label(
    rows: list[dict[str, str]],
    check: str,
    passed_label: str = "通过",
    failed_label: str = "未通过",
) -> str:
    for row in reversed(rows):
        if str(row.get("check", "")).strip() != check:
            continue
        passed = str(row.get("passed", "")).strip().lower() in {"true", "1", "yes"}
        return passed_label if passed else failed_label
    return "未检查"


def deployment_gate_value(
    rows: list[dict[str, str]],
    check: str,
    default: str = "未检查",
) -> str:
    for row in reversed(rows):
        if str(row.get("check", "")).strip() == check:
            value = str(row.get("value", "")).strip()
            return value or default
    return default


def local_operator_status_label(rows: list[dict[str, str]]) -> str:
    status = local_operator_item_value(rows, "overall_status", "missing").lower()
    labels = {
        "blocked": "待处理",
        "observation_degraded": "观察降级",
        "observation_only": "仅观察",
        "paper_only": "仅模拟",
        "operator_review_required": "待人工复核",
    }
    return labels.get(status, "未检查" if status == "missing" else status)


def local_operator_next_action(rows: list[dict[str, str]]) -> str:
    overall = next(
        (row for row in reversed(rows) if str(row.get("item", "")).strip() == "overall_status"),
        {},
    )
    if str(overall.get("passed", "")).strip().lower() in {"true", "1", "yes"}:
        return ""
    return local_operator_item_value(rows, "next_action", "")


def recommended_action(
    source_health: list[dict[str, str]],
    practical: list[dict[str, str]],
    strict: list[dict[str, str]],
    execution: list[dict[str, str]],
    order_source: list[dict[str, str]],
    promotion: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    if has_failed_check(source_health, "partial_recovery_available"):
        return {
            "label": "恢复 partial 行情",
            "message": "发现本地 partial 行情比正式缓存更新。先合并已下载行情，再刷新基准和报告。",
            "commands": [["recover-partial"], ["source-health"], ["report"]],
        }
    if has_failed_check(source_health, "price_cache_age_days") or has_failed_check(practical, "price_cache_age_days"):
        return {
            "label": "小样本刷新行情",
            "message": "行情缓存已过期。先用 100 只股票验证数据源和 checkpoint，再扩大到 800。",
            "commands": [["fetch", "--cached-universe", "--max-symbols", "100", "--no-metadata"], ["source-health"]],
        }
    if has_failed_check(source_health, "raw_latest_coverage"):
        return {
            "label": "检查数据源健康",
            "message": "最新行情日覆盖率不足，系统会使用上一完整行情日。先查看数据源健康，再决定是否重试刷新。",
            "commands": [["source-health"]],
        }
    if any(has_failed_check(practical, check) for check in ["evidence_current", "cost_stress_config_hash", "cost_stress_data_hash"]):
        return {
            "label": "重建证据链",
            "message": "配置、缓存或压力测试指纹不一致。需要重新回测、压力测试并刷新报告。",
            "commands": [["backtest"], ["stress"], ["report"]],
        }
    if has_failed_check(execution, "order_source_verified") or checked_row_value(order_source, "rpa_exportable") == "False":
        return {
            "label": "生成实用订单",
            "message": "当前 orders.csv 不是可执行 paper 来源。先跑实用流程生成可审计订单和 RPA 工单。",
            "commands": [["practical"]],
        }
    if collection_passed(practical) and not collection_passed(strict):
        if has_failed_check(strict, "candidate_forward_trading_days") or has_failed_check(strict, "shadow_observed_signal_count"):
            top_promotion = promotion[0] if promotion else {}
            if top_promotion.get("promotion_status") in {"collecting_evidence", "forward_underperforming"}:
                return {
                    "label": "观察所有候选",
                    "message": "实用门禁可用，但严格实盘仍缺前向验证或 Shadow 样本。优先同时观察默认策略和候选策略，更新晋级看板。",
                    "commands": [["observe-candidates", "--quiet", "--skip-stress"]],
                }
            return {
                "label": "记录观察信号",
                "message": "实用门禁可用，但严格实盘还缺冻结后前向验证或 Shadow 观察。继续观察，不升级实盘。",
                "commands": [["observe", "--quiet"]],
            }
        return {
            "label": "查看实盘路线图",
            "message": "实用门禁可用，但严格实盘仍有缺口。查看路线图定位剩余阻断项。",
            "commands": [["roadmap"]],
        }
    if collection_passed(practical) and collection_passed(strict):
        return {
            "label": "执行前检查",
            "message": "策略门禁已通过。仍需检查账户、订单来源、RPA 前置检查和人工最终确认。",
            "commands": [["execution-ready"]],
        }
    if practical:
        return {
            "label": "刷新实用门禁",
            "message": "实用门禁仍有阻断项。先刷新门禁结果并查看具体失败项。",
            "commands": [["ready"]],
        }
    return {
        "label": "初始化检查",
        "message": "还没有完整状态输出。先生成数据源、门禁和执行检查摘要。",
        "commands": [["source-health"], ["ready"], ["execution-ready"]],
    }


def has_failed_check(rows: list[dict[str, str]], check: str) -> bool:
    for row in rows:
        if row.get("check") == check:
            return str(row.get("passed", "")).lower() not in {"true", "1", "yes"}
    return False

def reliability_label(rows: list[dict[str, str]]) -> str:
    grade = row_value(rows, "reliability_grade")
    score = row_value(rows, "reliability_score")
    if not grade:
        return "-"
    return f"{grade}/{score}" if score else grade


def auto_live_status_text(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "\u672a\u751f\u6210"
    status = rows[0].get("status", "")
    return {
        "blocked_safety": "\u5b89\u5168\u95e8\u63a7\u963b\u65ad",
        "blocked_execution_and_evidence": "\u6267\u884c\u4e0e\u5b9e\u76d8\u8bc1\u636e\u540c\u65f6\u963b\u65ad",
        "blocked_execution": "\u6267\u884c\u94fe\u8def\u963b\u65ad",
        "collecting_evidence": "\u6b63\u5728\u7d2f\u79ef\u5b9e\u76d8\u8bc1\u636e",
        "ready_for_manual_review": "\u53ef\u4eba\u5de5\u590d\u6838\uff0c\u4e0d\u5141\u8bb8\u81ea\u52a8\u63d0\u4ea4",
        "auto_live_ready": "\u81ea\u52a8\u5b9e\u76d8\u5c31\u7eea",
    }.get(str(status), str(status or "-"))


def auto_live_allowed_text(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "\u5426"
    return "\u662f" if str(rows[0].get("auto_live_allowed", "")).lower() in {"true", "1", "yes"} else "\u5426"


def strategy_profile_summary(output_dir: Path, display_name: str) -> list[str]:
    metrics = read_key_value_csv(output_dir / "performance_metrics.csv", "metric", "value")
    manifest = read_key_value_csv(output_dir / "run_manifest.csv", "item", "value")
    reliability = read_rows(output_dir / "strategy_reliability.csv")
    stability = read_rows(output_dir / "strategy_candidate_stability.csv")
    practical = read_rows(output_dir / "practical_readiness.csv")
    strict = read_rows(output_dir / "live_readiness.csv")
    forward = read_rows(output_dir / "live_candidate_forward_progress.csv")
    shadow = read_rows(output_dir / "shadow_forward_audit.csv")

    if not any((metrics, manifest, reliability, stability, practical, strict, forward, shadow)):
        return [
            f"档案  {display_name}",
            "",
            "尚未生成输出。",
            "请先使用对应配置运行 backtest、stress 和 report。",
        ]

    strategy_name = manifest.get("strategy_name", "-")
    start = row_value(reliability, "backtest_start") or manifest.get("prices_first_date", "-")
    end = row_value(reliability, "backtest_end") or manifest.get("prices_last_date", "-")
    trading_days = row_value(reliability, "backtest_trading_days") or "-"
    forward_row = forward[0] if forward else {}
    observed_days = forward_row.get("observed_forward_trading_days", "0")
    required_days = forward_row.get("required_forward_trading_days", "-")
    forward_status = forward_row.get("status", "未生成")
    forward_excess = format_percent(forward_row.get("forward_excess_return"))
    overfit_status = forward_row.get("overfit_status", "未评估")
    candidate_id = forward_row.get("candidate_id", "-")
    shadow_count = checked_row_value(strict, "shadow_observed_signal_count") or "0"
    shadow_target = checked_row_threshold(strict, "shadow_observed_signal_count") or "-"
    latest_shadow = shadow[-1] if shadow else {}
    shadow_status = latest_shadow.get("status", "未生成")
    shadow_days = latest_shadow.get("observed_trading_days", "0")
    practical_ok = collection_passed(practical)
    strict_ok = collection_passed(strict)

    if strict_ok:
        position = "严格门禁已通过；实际执行仍需券商回报、成交对账和人工确认。"
    elif practical_ok:
        position = "可继续模拟盘与经审计的人工工单；严格实盘仍被前向验证阻断。"
    else:
        position = "仅用于研究和模拟演练；实用门禁修复前不得进入真实账户。"

    return [
        f"档案  {display_name}",
        f"策略标识  {strategy_name}",
        f"回测区间  {start} 至 {end}  ·  {trading_days} 交易日",
        "",
        f"策略 / 基准收益  {format_percent(metrics.get('total_return'))} / {format_percent(metrics.get('benchmark_total_return'))}",
        f"累计超额  {format_percent(metrics.get('excess_total_return'))}",
        f"信息比率 / 最大回撤  {format_float(metrics.get('information_ratio'))} / {format_percent(metrics.get('max_drawdown'))}",
        f"年化换手  {format_percent(metrics.get('annualized_turnover'))}",
        "",
        f"研究可信度  {reliability_label(reliability)}",
        f"参数邻域稳定性  {readiness_status(stability)}",
        f"实用门禁  {readiness_status(practical)}",
        f"严格实盘  {readiness_status(strict)}",
        "",
        f"冻结候选  {candidate_id}",
        f"前向验证  {observed_days}/{required_days} 交易日  ·  {forward_status}",
        f"前向超额 / 过拟合解除  {forward_excess} / {overfit_status}",
        f"Shadow  {shadow_count} 个可评估信号  ·  门槛 {shadow_target}  ·  最新 {shadow_status}（{shadow_days} 日）",
        "",
        f"当前定位  {position}",
        f"证据生成  {manifest.get('generated_at', '-')}",
    ]


def collection_passed(rows: list[dict[str, str]]) -> bool:
    return bool(rows) and all(str(row.get("passed", "")).lower() in {"true", "1", "yes"} for row in rows)


def checked_row_threshold(rows: list[dict[str, str]], check: str) -> str:
    for row in rows:
        if row.get("check") == check:
            return row.get("threshold", "")
    return ""

def readiness_status(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "未评估"
    failed = [row for row in rows if str(row.get("passed", "")).lower() not in {"true", "1", "yes"}]
    return "通过" if not failed else f"未通过 {len(failed)}"


def execution_status(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "未评估"
    failed = [row for row in rows if str(row.get("passed", "")).lower() not in {"true", "1", "yes"}]
    if not failed:
        return "执行就绪"
    safety = rows_by_severity(failed, "safety")
    if safety:
        return f"安全阻断 {len(failed)}"
    execution_blockers = rows_by_severity(failed, "execution")
    live_pending = rows_by_severity(failed, "live")
    if execution_blockers:
        suffix = f"；严格待验证 {len(live_pending)}" if live_pending else ""
        return f"人工工单阻断 {len(execution_blockers)}{suffix}"
    if live_pending:
        return f"人工工单就绪；严格待验证 {len(live_pending)}"
    return f"待处理 {len(failed)}"


def rows_by_severity(rows: list[dict[str, str]], token: str) -> list[dict[str, str]]:
    return [row for row in rows if token in str(row.get("severity", ""))]


def failed_check_lines(rows: list[dict[str, str]], prefix: str = "", limit: int = 3, value_key: str = "threshold") -> list[str]:
    lines = []
    for row in rows[:limit]:
        check = row.get("check") or row.get("item") or "-"
        value = row.get("value", "-")
        threshold = row.get(value_key, "") or row.get("threshold", "-")
        lines.append(f"{prefix}{check}: {value} / {threshold}")
    if len(rows) > limit:
        lines.append(f"{prefix}还有 {len(rows) - limit} 项，查看报告或 CSV。")
    return lines


def strategy_promotion_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}strategy_promotion.csv not generated"]
    top = rows[0]
    return [
        f"{prefix}{top.get('strategy_name', '-')} -> {top.get('promotion_status', '-')} / {top.get('recommendation', '-')}",
        f"{prefix}forward {top.get('forward_days', '-')}/{top.get('required_forward_days', '-')} days, excess {format_percent(top.get('forward_excess_return'))}",
        f"{prefix}shadow {top.get('shadow_observed_signals', '-')}/{top.get('shadow_required_signals', '-')} signals, avg excess {format_percent(top.get('shadow_avg_excess_return'))}",
        f"{prefix}high-cost excess {format_percent(top.get('high_cost_excess_return'))}, overfit {top.get('anti_overfit_status', '-')}",
    ]


def candidate_forward_progress_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}未生成，运行 report。"]
    row = rows[0]
    status = row.get("status", "-")
    observed = row.get("observed_forward_trading_days", "0")
    required = row.get("required_forward_trading_days", "-")
    remaining = row.get("remaining_forward_trading_days", "-")
    excess = format_percent(row.get("forward_excess_return"))
    overfit = row.get("overfit_status", "-")
    active_drawdown = format_percent(row.get("forward_active_max_drawdown"))
    active_drawdown_limit = format_percent(row.get("max_forward_active_drawdown"))
    tracking_error = format_percent(row.get("forward_tracking_error"))
    tracking_error_limit = format_percent(row.get("max_forward_tracking_error"))
    information_ratio = format_float(row.get("forward_information_ratio"))
    information_ratio_limit = format_float(row.get("min_forward_information_ratio"))
    action = row.get("next_action", "")
    lines = [f"{prefix}冻结后验证: {status}，{observed}/{required} 交易日，还差 {remaining} 日，前向超额 {excess}，过拟合状态 {overfit}"]
    lines.append(
        f"{prefix}\u4e3b\u52a8\u56de\u64a4 {active_drawdown} / \u4e0a\u9650 {active_drawdown_limit}\uff0c"
        f"\u8ddf\u8e2a\u8bef\u5dee {tracking_error} / \u4e0a\u9650 {tracking_error_limit}\uff0c"
        f"\u4fe1\u606f\u6bd4\u7387 {information_ratio} / \u4e0b\u9650 {information_ratio_limit}"
    )
    if action:
        lines.append(f"{prefix}下一步: {action}")
    return lines


def roadmap_lines(rows: list[dict[str, str]], prefix: str = "", limit: int = 3) -> list[str]:
    lines = []
    for row in rows[:limit]:
        area = row.get("area", "-")
        command = row.get("command", "-")
        action = row.get("next_action", "")
        lines.append(f"{prefix}{area}: {command}；{action}")
    if len(rows) > limit:
        lines.append(f"{prefix}还有 {len(rows) - limit} 步，查看执行准备路线图。")
    return lines


def account_plan_lines(rows: list[dict[str, str]], prefix: str = "", limit: int = 3) -> list[str]:
    if not rows:
        return [f"{prefix}未生成，运行 account-plan。"]
    lines = []
    for row in rows[:limit]:
        path = row.get("path", "-")
        command = row.get("command", "-")
        action = row.get("next_action", "")
        lines.append(f"{prefix}{path}: {command}；{action}")
    if len(rows) > limit:
        lines.append(f"{prefix}还有 {len(rows) - limit} 步，查看 account_readiness_plan.csv。")
    return lines


def account_repair_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}修复模板未生成，运行 account-repair-template。"]
    positions = [row for row in rows if row.get("symbol")]
    missing = [row for row in positions if row.get("price_status") == "missing_price"]
    ready = [row for row in positions if row.get("price_status") == "priced"]
    return [f"{prefix}修复模板: 可导入 {len(ready)} 行，缺价待处理 {len(missing)} 行。"]


def account_excluded_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}排除清单: 0 行。"]
    return [f"{prefix}排除清单: {len(rows)} 行；使用可估值模板前必须人工确认。"]


def account_import_check_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}导入预检查未生成，运行 account-import-check。"]
    failed = [row for row in rows if str(row.get("passed", "")).lower() not in {"true", "1", "yes"}]
    status = "通过" if not failed else f"阻断 {len(failed)} 项"
    lines = [f"{prefix}导入预检查: {status} ({len(rows) - len(failed)}/{len(rows)})"]
    for row in failed[:2]:
        lines.append(f"{prefix}  {row.get('check', '-')}: {row.get('value', '-')} / {row.get('threshold', '-')}")
    return lines


def execution_dashboard_lines(
    *,
    execution: list[dict[str, str]],
    roadmap: list[dict[str, str]],
    account_plan: list[dict[str, str]],
    account_path_decision: list[dict[str, str]] | None = None,
    order_source: list[dict[str, str]],
    account_state: list[dict[str, str]],
    account_reconciliation: list[dict[str, str]],
    account_import_check: list[dict[str, str]],
    tickets: list[dict[str, str]],
    execution_modes: list[dict[str, str]] | None = None,
) -> list[str]:
    execution_modes = execution_modes or []
    account_path_decision = account_path_decision or []
    failed_execution = [row for row in execution if str(row.get("passed", "")).lower() not in {"true", "1", "yes"}]
    manual_blockers = rows_by_severity(failed_execution, "execution")
    live_pending = rows_by_severity(failed_execution, "live")
    order_context = checked_row_value(order_source, "orders_file_context") or "未生成"
    exportable = checked_row_value(order_source, "rpa_exportable") or "False"
    ready = sum(1 for row in tickets if row.get("status") == "ready")
    blocked = sum(1 for row in tickets if row.get("status") and row.get("status") != "ready")
    account_state_status = check_collection_status(account_state)
    reconciliation_status = check_collection_status(account_reconciliation)
    import_check_status = check_collection_status(account_import_check)

    lines = [
        "执行准备",
        f"  当前状态: {execution_status(execution)}",
        f"  人工工单阻断项: {len(manual_blockers)}",
        f"  严格实盘待验证项: {len(live_pending)}",
        f"  订单来源: {order_context}",
        f"  RPA 可导出: {exportable}",
        f"  账户状态审计: {account_state_status}",
        f"  账户/订单对账: {reconciliation_status}",
        f"  导入预检查: {import_check_status}",
        f"  工单: {ready} ready / {blocked} blocked",
        "",
        "执行模式",
        *execution_mode_lines(execution_modes, prefix="  "),
        "",
        "账户路径预判",
        *account_path_decision_lines(account_path_decision, prefix="  "),
        "",
        "人工工单阻断",
        *execution_blocker_lines(manual_blockers, prefix="  "),
        "",
        "严格实盘证据",
        *execution_blocker_lines(live_pending, prefix="  "),
        "",
        "建议路线",
        *roadmap_lines(roadmap, prefix="  ", limit=6),
        "",
        "账户准备",
        *account_plan_lines(account_plan, prefix="  ", limit=6),
        "",
        "安全边界",
        "  回测/研究 orders.csv 不能直接导出到券商客户端。",
        "  只有 paper/practical 来源、账户对账通过、RPA 前检查通过的订单才可进入工单。",
        "  RPA 只辅助填字段，最终提交必须人工确认。",
    ]
    return lines


def execution_mode_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}未生成，运行 execution-ready。"]
    labels = {
        "manual_ticket_review": "人工工单核对",
        "rpa_click_fill": "RPA 点击填单",
        "strict_live_execution": "严格实盘执行",
    }
    lines: list[str] = []
    for row in rows:
        label = labels.get(row.get("mode", ""), row.get("mode", "-"))
        status = row.get("status", "-")
        blockers = row.get("blockers", "")
        lines.append(f"{prefix}{label}: {status}" + (f" ({blockers})" if blockers else ""))
    return lines


def account_path_decision_lines(rows: list[dict[str, str]], prefix: str = "") -> list[str]:
    if not rows:
        return [f"{prefix}未生成，运行 account-paths。"]
    labels = {
        "current_account_state": "当前账户状态",
        "simulation_reset": "模拟账户重置",
        "real_account_full_import": "真实账户完整导入",
        "real_account_priced_only_import": "真实账户可估值导入",
    }
    lines: list[str] = []
    for row in rows:
        label = labels.get(row.get("path", ""), row.get("path", "-"))
        status = row.get("status", "-")
        blockers = row.get("blockers", "")
        lines.append(f"{prefix}{label}: {status}" + (f" ({blockers})" if blockers else ""))
    return lines


def execution_blocker_lines(rows: list[dict[str, str]], prefix: str = "", limit: int = 8) -> list[str]:
    if not rows:
        return [f"{prefix}无阻断项。"]
    lines: list[str] = []
    for row in rows[:limit]:
        check = row.get("check", "-")
        value = row.get("value", "-")
        action = row.get("next_action", "") or row.get("message", "")
        lines.append(f"{prefix}{check}: {value}")
        if action:
            lines.append(f"{prefix}  下一步: {action}")
    if len(rows) > limit:
        lines.append(f"{prefix}还有 {len(rows) - limit} 项，查看 execution_readiness.csv。")
    return lines


def check_collection_status(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "未生成"
    passed = [row for row in rows if str(row.get("passed", "")).lower() in {"true", "1", "yes"}]
    return "通过" if len(passed) == len(rows) else f"{len(passed)}/{len(rows)}"


def checked_row_value(rows: list[dict[str, str]], check: str) -> str:
    for row in rows:
        if row.get("check") == check:
            return row.get("value", "")
    return ""


def row_value(rows: list[dict[str, str]], item: str) -> str:
    for row in rows:
        if row.get("item") == item:
            return row.get("value", "")
    return ""


def read_key_value_csv(path: Path, key: str, value: str) -> dict[str, str]:
    rows = read_rows(path)
    return {row.get(key, ""): row.get(value, "") for row in rows if row.get(key)}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def format_percent(value: str | None) -> str:
    if value in {None, ""}:
        return "-"
    try:
        return f"{float(value):.2%}"
    except ValueError:
        return str(value)


def format_float(value: str | None) -> str:
    if value in {None, ""}:
        return "-"
    try:
        return f"{float(value):.2f}"
    except ValueError:
        return str(value)


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def cache_badge(latest_price: str, age: str) -> str:
    if latest_price in {"", "-"}:
        return "未发现行情缓存"
    try:
        days = int(float(age))
    except (TypeError, ValueError):
        return f"最新行情 {latest_price}"
    if days > 10:
        return f"缓存过期：最新行情 {latest_price}，距今天 {days} 天"
    if days > 3:
        return f"缓存偏旧：最新行情 {latest_price}，距今天 {days} 天"
    return f"缓存可用：最新行情 {latest_price}，距今天 {days} 天"


def inferred_age_days(latest_price: str) -> str:
    try:
        latest = date.fromisoformat(str(latest_price))
    except ValueError:
        return ""
    return str(max(0, (date.today() - latest).days))


GUIDE_TEXT = """日常运行逻辑

1. 盘后先刷新行情池。
   如果网络不稳定，先把行情池数量改成 50 或 100 测试；确认可用后再改回 800。
2. 点击“日常盘后流程”。
   它会生成模拟盘订单、导出 RPA 工单并预览拦截原因。行情缓存过期或实盘门禁未通过时会停止。
3. 检查订单工单。
   ready 表示可人工核对；blocked 表示系统拦截。常见原因包括订单过期、非整手买入、单笔金额超限。
4. 实盘辅助只能用于人工确认。
   系统不会点击最终提交。任何下单前都要核对账户现金、持仓、价格、数量和涨跌停状态。

研究复盘逻辑

点击“完整研究流程”会依次运行 research、backtest、stress、report。
research 会比较配置里的候选策略，输出 strategy_research.csv；它不会自动修改正式策略。
报告里的“策略可信度与过拟合检查”会检查回测长度、样本内外、走步折数、滚动窗口、年度稳定性和调参污染。可信度不是收益预测，只用于判断证据是否足够。

安全边界

这个系统适合作为本地研究、模拟盘和人工确认订单辅助。严格实盘级需要冻结参数后的前向验证和 shadow 观察全部达标。无人值守自动实盘仍需要券商回报、撤单、成交同步和异常恢复。"""


def main() -> None:
    root = Tk()
    QuantGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
