# A 股量化系统本地运行说明

系统的行情刷新、策略计算、回测、模拟盘、华泰只读审计、邮件告警和报告均在本机运行，不依赖 Codex 或其他 AI。

## 一键运行

双击项目根目录的 `run_local.cmd`。

它会依次执行：

1. 增量刷新约 800 只高流动性候选股票，不下载全市场 5500 只股票。
2. 只使用覆盖率达标的完整交易日计算因子和回测。
3. 更新模拟盘、Shadow、策略健康和执行门禁。
4. 检查并按配置启动本机已安装的华泰官方客户端；本人完成登录并打开“资金股票”页面后，系统只读抓取资金与持仓并逐项对账。登录或页面未就绪时记录阻断，但不会卡住策略阶段。
5. 重建 Canary 只读审计和中文报告。
6. 打开 `outputs/local_operator_status.html` 统一状态页。

本地一键流程不会打开券商提交开关、不会清除急停、不会签发 Canary 凭证，也不会提交或撤销订单。
它不会代填账号、密码、验证码或自动确认登录；客户端认证始终由本人完成。
启动前还会校验配置中的 `hexin.exe` SHA-256。客户端升级或文件被替换后会阻断自动启动，需重新核验官方签名和文件指纹后再更新配置。

## PowerShell 运行

常规盘后运行：

```powershell
.\scripts\local_run.ps1 -Refresh
```

只检查已有数据，不访问华泰客户端：

```powershell
.\scripts\local_run.ps1 -SkipBroker -NoOpen
```

仅当行情缓存仍在有效期内时，第二条命令才会继续策略计算；缓存过期会在数秒内停止并提示使用 `-Refresh`，不会先等待完整回测。

## 输出位置

- `outputs/local_operator_status.html`：统一状态页。
- `outputs/report.html`：策略、基准和可信度报告。
- `outputs_live/report.html`：华泰账户与 Canary 审计报告。
- `outputs/logs/local_run_*.log`：每次本地运行日志。

## 每日自动观察

系统提供仅观察模式的 Windows 计划任务脚本。它只刷新行情、运行候选策略观察并重建报告，不运行模拟盘、不访问券商提交命令，也不初始化实盘账本。

先预览任务配置，不会修改 Windows：

```powershell
.\scripts\install_observation_task.ps1
```

确认后显式注册工作日 18:30 任务：

```powershell
.\scripts\install_observation_task.ps1 -Register
```

运行状态会写入 `outputs/daily_observation_status.csv`，审计结论会写入 `outputs/daily_observation_summary.csv` 和 `outputs/daily_observation_audit.csv`，并显示在中文报告与图形界面中。

默认未注册、未运行时只显示提示；运行失败、结果过期或残留任务锁时会显示告警。

计划任务始终采用 `observation_only` 模式，不会下单，也不会初始化实盘账本。

状态页中的“本地链路正常”不等于“允许实盘”。只有策略严格门禁、账户审计和当次交易确认全部通过后，才可能进入小额 Canary；默认状态始终是提交关闭、急停开启、无交易凭证。


## 只读刷新状态页

当图形界面或启动窗口已经关闭，但需要重新判断现有证据状态时，运行：

```powershell
.\.venv\Scripts\python.exe -m ashare_quant local-status --live-config configs\huatai_desktop_canary.yaml
```

该命令只读取本地 CSV、JSON 和配置并重建 `outputs/local_operator_status.html`，不会刷新行情、访问华泰客户端、创建 Canary 凭证或提交委托。状态页中的 `local_launcher_state` 用于识别外部启动器留下的 `running`、`failed` 或过期心跳；没有心跳文件时显示 `direct_or_not_started`，表示当前是直接命令行运行或尚未启用外部心跳，不作为故障。
