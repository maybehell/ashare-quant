# 长历史行情补全运行手册

## 目的

该任务把固定研究策略的历史样本从当前约 750 个有效交易日向 2018 年扩展，
用于提高回测可信度和稳定性。它只处理研究行情，不调整策略参数，不生成交易
订单，不连接券商，也不改变实盘开关。

## 当前自动运行方式

当前用户启动项 AshareQuantHistoryBackfill.cmd 已安装。登录 Windows 后，
守护脚本每 60 分钟尝试一批，每批最多请求 25 只股票，最长运行 30 分钟。
如果整批均为 BaoStock 会话级失败，状态会变为 source_unavailable、退出码为 75，
并退避 240 分钟；未到期的退避在 daemon 或 Windows 重启后仍会继承。
17:30 至 21:30 以及每日观察任务运行期间会主动让路。全部 800 只和最终基准
获取均零失败、正式缓存提交成功后，守护进程会自动运行固定参数长历史回测；
只有隔离验证成功后才退出。该步骤不会自动晋级或生成执行订单。

查看状态：

    Get-Content .\outputs_extended_history_validation\history_backfill_daemon_status.json

查看日志：

    Get-Content .\outputs_extended_history_validation\history_backfill_daemon.log -Tail 100

临时停止：

    New-Item .\outputs_extended_history_validation\history_backfill_daemon.stop

恢复前先删除停止标记，然后重新运行启动项安装器的 StartNow 模式。

## 手动运行单批

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\history_backfill_daemon.ps1 -RunOnce

无网络自检：

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\history_backfill_daemon.ps1 -RunOnce -DryRun

数据完成后也可以手动运行同一个研究验证器：

    .\scripts\aq.ps1 --config .\configs\extended_history_validation.yaml history-validate

## 权威状态与暂存文件

- history_backfill_daemon_status.json：守护进程、下一次运行时间、最近退出码、源故障连续次数和退避分钟数。
- history_backfill_progress_extended_history_validation.parquet：请求、完成、剩余、失败数量，以及 source_status、attempted_symbols、successful_symbols。
- prices_partial_extended_history_validation.parquet：已验证且可恢复的历史暂存行情。
- fetch_history_partial_provenance_extended_history_validation.parquet：暂存行情来源证据。
- fetch_failures_extended_history_validation.parquet：仅代表最近一批的失败项。
- history_validation_status.csv：固定策略验证状态、正式行情哈希、配置哈希和核心指标。

`source_unavailable` 表示当前公共源不可用，不表示 25 只股票同时发生数据异常。
该状态不会清空已验证暂存数据，也不会把失败批次计为完成。等待 `next_run_at`
后 daemon 才会再发起有界请求；无需频繁手动重启。
未完成时，暂存文件都不是正式回测输入。系统不会替换 data/cache/prices.parquet。
history_validation_status.csv 只有在完整进度、正式缓存和隔离回测全部匹配时才为
completed；该状态仍然不是策略激活或实盘授权。
只有 800 只全部完成并通过正常抓取成功门槛后，数据层才原子合并正式缓存。

## 完成后的验证顺序

1. 确认进度状态为 complete，完成数等于请求数，剩余数和失败数均为 0。
2. 确认 history_validation_status.csv 为 completed，且行情、配置哈希与当前一致。
3. 比较更长历史下的超额收益、最大回撤、年度稳定性、滚动窗口和 bootstrap 区间。
4. 继续使用冻结后的前向观察，不把历史扩展结果直接当作策略晋级依据。
5. 任何策略激活、实盘账本初始化、Canary 认证和提交开关仍走各自独立门禁。

## 2026-08-10 trusted source-chain update

The isolated extended-history job now uses the ordered AkShare chain Eastmoney,
Sina, then Tencent. Sina is accepted with its reported volume and amount only
after recent overlap checks against committed market rows. This job rejects
estimated amount and no longer resumes `resumed_partial_unknown_source` rows.
A lower completed-symbol count after this upgrade is an intentional trust
correction, not data loss from the formal cache.

Partial checkpoints persist per-symbol provenance before prices. The daemon
status schema includes `source_plan_identity`; a future source-outage delay is
reused only for the same default and override configuration fingerprints.
During the current BaoStock outage the isolated config disables BaoStock as a
second fallback, while the ordinary daily configuration remains unchanged.
None of these staging states authorizes strategy promotion, paper orders,
broker access, live-ledger initialization, certification, or submission.

## 2026-08-10 adaptive healthy scheduling

This policy supersedes the earlier fixed 60-minute cadence. The daemon keeps
the regular interval at 60 minutes and uses a separate 15-minute healthy
interval only after a bounded batch exits successfully,
reports source status `ok`, has zero failed items, attempts at least one symbol,
and succeeds for every attempt. A degraded or ambiguous batch returns to the
regular interval. Source-wide unavailability still uses the persisted
240-minute backoff, and the daily observation lock and quiet window still take
priority.

Both intervals are persisted in the current-user Startup command through
`install_history_backfill_startup.ps1`. The lower bound is 15 minutes, and the
healthy interval cannot exceed the regular interval. This scheduling policy
changes only research-data acquisition timing; it does not change the strategy
hash, formal cache acceptance, promotion evidence, account state, broker
capability, or submission authority.

## 2026-08-10 cross-source start-boundary evidence

Some listed companies can remain suspended longer than the ordinary 31-day
coverage tolerance. A structurally valid series with a late first row is no
longer counted as a successful acquisition and then discarded on every resume.
The acquisition now asks an alternate provider for the same requested range.
Only an identical first trading date is accepted as
`cross_source_confirmed_gap`.

The partial provenance persists the requested start, observed first date,
verifier source, verifier first date, and coverage status. A later resume checks
that the persisted dates still match the current request and price frame.
Missing or contradictory evidence fails closed and the symbol remains pending.
This avoids both silent truncation and permanent retry of legitimate long
suspensions.

## 2026-08-11 structural failover and runtime isolation

Each ordered AkShare response is now normalized and structurally validated
before source selection. A non-empty response with invalid OHLC values is
recorded as a source failure and the same retry continues to the next provider.
The isolated auto-failover configuration uses one history worker because the
provider stack's in-process V8 decoder has failed under the observed threaded
workload. This does not change the ordinary BaoStock-oriented worker setting.

After a non-zero batch exit, the daemon publishes `retry_wait` together with the
exit code and retains the regular bounded retry interval. It no longer presents
that state as an ordinary healthy wait. Staged progress and the formal cache are
left unchanged. Completion still requires all requested symbols, zero failures,
and an isolated validation whose fixed strategy, backtest configuration, and
formal prices hashes all match the current artifacts.

When the committed overlap is insufficient or internally inconsistent, a
reported-amount Sina candidate may replace that evidence only after an
independent Tencent series also passes the configured close, volume, and amount
overlap thresholds. The verifier identity and errors are persisted with source
provenance. Tencent volume is normalized by its observed market convention:
STAR Market rows are already shares, while the other supported A-share boards
are lots. Estimated Tencent turnover remains verifier-only and cannot become the
accepted fixed-validation source.
