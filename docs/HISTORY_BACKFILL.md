# 长历史行情补全运行手册

## 目的

该任务把固定研究策略的历史样本从当前约 750 个有效交易日向 2018 年扩展，
用于提高回测可信度和稳定性。它只处理研究行情，不调整策略参数，不生成交易
订单，不连接券商，也不改变实盘开关。

## 当前自动运行方式

当前用户启动项 AshareQuantHistoryBackfill.cmd 已安装。登录 Windows 后，
守护脚本每 60 分钟尝试一批，每批最多请求 25 只股票，最长运行 30 分钟。
17:30 至 21:30 以及每日观察任务运行期间会主动让路。全部 800 只完成后，
守护进程自动退出。

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

## 权威状态与暂存文件

- history_backfill_daemon_status.json：守护进程、下一次运行时间和最近退出码。
- history_backfill_progress_extended_history_validation.parquet：请求、完成、剩余和失败数量。
- prices_partial_extended_history_validation.parquet：已验证且可恢复的历史暂存行情。
- fetch_history_partial_provenance_extended_history_validation.parquet：暂存行情来源证据。
- fetch_failures_extended_history_validation.parquet：仅代表最近一批的失败项。

未完成时，上述文件都不是正式回测输入。系统不会替换 data/cache/prices.parquet。
只有 800 只全部完成并通过正常抓取成功门槛后，数据层才原子合并正式缓存。

## 完成后的验证顺序

1. 确认进度状态为 complete，并审计来源、日期范围、失败数和正式缓存指纹。
2. 使用 extended_history_validation 配置运行固定参数回测。
3. 比较更长历史下的超额收益、最大回撤、年度稳定性、滚动窗口和 bootstrap 区间。
4. 继续使用冻结后的前向观察，不把历史扩展结果直接当作策略晋级依据。
5. 任何策略激活、实盘账本初始化、Canary 认证和提交开关仍走各自独立门禁。