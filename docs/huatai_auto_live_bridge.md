# 华泰证券自动实盘桥接说明

当前系统新增 `scripts/huatai_command_bridge.py`，用于优先打通华泰证券方向的 command 适配器链路。

这个脚本同时承载模拟后端、官方客户端只读后端和实验性的桌面 Canary 后端。默认模板仍会拒绝 self-test，不会提交真实订单；只有显式使用 `--simulate`（或设置 `ASHARE_QUANT_HUATAI_SIMULATE=1`）时才返回模拟委托号。桌面 Canary 会操作真实客户端，因此仅允许单笔、不超过 1000 元，并且还要同时通过严格策略门禁、实时账户对账、HMAC 完整性、短时武装票据和逐次人工确认；当前生产配置保持急停开启、提交关闭。

## 配置示例

```yaml
auto_live:
  enabled: false
  broker_adapter: command
  allow_command_adapter: false
  allow_broker_submit: false
  allow_simulated_account_snapshot: false
  allow_outside_trading_window: false
  allow_stale_tickets: false
  max_ticket_age_days: 0
  account_snapshot_max_age_seconds: 300
  trading_window:
    timezone: Asia/Shanghai
    weekdays: [0, 1, 2, 3, 4]
    windows: ["09:25-11:30", "13:00-14:57"]
  command_adapter:
    command:
      - .venv/Scripts/python.exe
      - scripts/huatai_command_bridge.py
      - --config
      - configs/huatai_bridge_template.yaml
      - --simulate
    timeout_seconds: 30
    working_dir: ""
```

桥接器必须支持：

- `self_test`
- `submit_orders`
- `query_orders`
- `cancel_orders`
- `account_snapshot`

真实华泰接入时，应保留外层 JSON 契约，只替换桥接器内部后端调用。真实后端必须返回券商委托号，并支持按券商委托号查询委托状态、成交数量、均价、成交金额和费用。

## 安全边界

- 不在配置文件保存交易密码。
- 不允许在缺少券商委托号时自动重试提交。
- 每次桌面 Canary 提交都必须先即时检查买入、卖出、当日委托和撤单页签契约；自检失败时不消耗一次性武装票据，也不创建提交动作。
- 不允许 query 返回 `unknown`、`failed`、`rejected` 后继续视为正常自动实盘。
- 若华泰官方或柜台接口不可用，应保持 `auto_live.enabled=false` 和 `allow_broker_submit=false`。
- 真实提交前必须先运行 `python -m ashare_quant auto-live-window`，并确认 `outputs/auto_live_window_audit.csv` 全部通过；模拟账户可重置，但不能替代真实账户核验。


## 撤单要求

华泰实盘桥接器在接入真实后端前，必须实现 `cancel_orders`，并把券商撤单结果归一化为系统支持的 `cancel_status`。当前脚手架只提供 `--simulate` 演练，不会调用真实券商。


## 账户快照要求

华泰实盘桥接器在接入真实后端前，必须实现 `account_snapshot`。该能力只读券商账户现金、可用资金、总资产和持仓明细，用于提交订单前与本地账户状态对账。真实后端必须返回 `environment=live` 和稳定的脱敏 `account_id`。当前脚手架只返回 `environment=simulation`，即使余额与本地模拟账户一致也不会通过正式账户门禁。真实快照还必须回传请求中的配置指纹，并在默认 300 秒有效期内用于提交。`auto-live-run` 会在快照无效时先自动执行只读刷新，刷新失败不会进入委托提交。

## 华泰桥接完整性

`configs/huatai_auto_live_template.yaml` 已默认固定华泰桥接脚本与桥接配置 SHA-256，并要求 HMAC-SHA256。任何脚本或配置变化都要先人工复核，再更新固定哈希；HMAC 密钥使用 `scripts/set_auto_live_hmac_key.ps1 -PersistUser` 生成，不应放入 YAML。

## 华泰桥接器回放认证

接入真实后端后，先保留一组脱敏的 submit/query/cancel/account_snapshot 请求与回报，运行：

```powershell
python -m ashare_quant auto-live-adapter-replay --dir outputs/huatai_replay
```

华泰模拟桥接器可以得到 `certified_simulation`，但不能解除真实实盘门禁。只有真实账户快照返回 `environment=live`，且同一 ticket_id 的券商委托号在提交、查询和撤单阶段保持一致时，才会得到 `certified_live`。

## 华泰无人值守运行

华泰真实后端通过回放认证后，先以审计模式运行服务并观察锁、心跳和周期历史。提交服务还必须启用服务级双开关，并显式使用 `--submit`。推荐由 `scripts/install_auto_live_task.ps1` 注册 Windows 任务；默认安装为审计任务，注册提交任务需要 `-Submit -ConfirmSubmit`。

## 推荐运行周期

真实华泰后端完成审计后，运维入口使用 `python -m ashare_quant auto-live-cycle --submit`。系统会先恢复/查询前批，生成卖单优先且受金额与笔数上限约束的顺序批次，再刷新真实账户快照并复核全部门禁；每次只提交当前批次，随后立即查询委托并生成成交候选。前批未全部终态时后续批次不会放行；显式写回后还会重新读取券商账户完成最终对账。模拟账户闭环测试可额外使用 `--confirm-account-sync`，真实账户启用前必须另行核对成交回写与券商资金持仓对账。

阶段证据写入 `auto_live_cycle.csv`、`auto_live_cycle_summary.csv` 和 `auto_live_cycle_history.csv`。周期失败不会自动删除提交锁、释放不确定风险预留或重发相同工单。

## 提交锁恢复要求

华泰桥接器被调用前，系统会写入 `auto_live_submission_intent.csv` 和状态为 `submitting` 的 `auto_live_run.csv`。运维检查使用：

```powershell
python -m ashare_quant auto-live-lock
```

桥接器必须保证 `ticket_id` 可用于券商端幂等查询。出现过期 `auto_live_submit.lock` 时，先执行 `auto-live-query` 并在客户端核对委托，不得直接重发；查询可从匹配指纹的持久化提交请求重建中断批次。系统不会自动删除锁，且上一批全部进入 `filled/canceled/rejected/failed` 前不会放行新批次。并发点击的阻断记录保存在 `auto_live_blocked_attempt.csv`，原提交回报不会被覆盖。

## 独立风险与停机开关

华泰桥接器调用前，系统会执行 `auto-live-risk` 等价审计，并在 `auto_live_risk_reservations.csv` 预留当日额度。真实账户快照必须提供有效的 `available_cash` 和 `total_asset`，系统按“本批买入金额 + 现金缓冲”校验，不使用本批卖出回款覆盖买入。

`auto-live-stop` 创建本地停机文件后不得调用提交接口；`auto-live-resume --confirm` 只清除本地文件，不能绕过其他门禁。适配器自身仍应具备券商侧撤单、账户冻结或人工停机能力。 本地停机会在提交锁后和进程启动前复核，但不能撤回已经发往券商的请求；已启动批次仍需查询并按需撤单。
