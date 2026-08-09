# 自动实盘 command 适配器契约

`auto_live.broker_adapter=command` 用于把系统生成的已审计订单交给一个外部程序。外部程序可以是券商 API 包装器、柜台客户端桥接程序或中间服务命令行入口。

当前仓库提供 `scripts/command_broker_bridge_template.py` 作为模板。它默认不连接券商，也不会提交订单；只有显式传入 `--simulate-submit` 或设置 `ASHARE_QUANT_BRIDGE_SIMULATE_SUBMIT=1` 时，才返回模拟 submitted 结果。

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
      - scripts/command_broker_bridge_template.py
      - --simulate-submit
    timeout_seconds: 30
    working_dir: ""
```

真实提交前必须同时满足系统门禁、适配器自检、人工确认策略晋级和 `allow_broker_submit=true`。上面的示例刻意保持关闭状态，避免误提交。

## 提交时间窗与工单日期

运行真实提交前先执行：

```powershell
python -m ashare_quant auto-live-window
```

系统会生成 `outputs/auto_live_window_audit.csv`，逐项核验当前时区、工作日、交易时段、工单执行日期和 `ready` 状态。command 适配器的非演练提交会强制使用同一套审计逻辑；任一项目失败时，券商桥接程序不会被调用。

默认只允许周一至周五的 `09:25-11:30`、`13:00-14:57`，且工单必须属于当天。`allow_outside_trading_window=true` 和 `allow_stale_tickets=true` 只用于离线演练与自动化测试，正式配置应保持 `false`。模拟账户可以重置或修改，但这不代表真实账户、真实委托或券商登录已获授权。

## 调用方式

系统会调用：

```powershell
python scripts/command_broker_bridge_template.py REQUEST_JSON RESULT_JSON
```

如果配置命令里没有 `{request}` / `{result}` 占位符，系统会把两个路径自动追加到命令末尾。也可以显式写成：

```yaml
command:
  - .venv/Scripts/python.exe
  - scripts/command_broker_bridge_template.py
  - "{request}"
  - "{result}"
```

## self_test 请求

运行 `python -m ashare_quant auto-live-adapter-selftest` 时，系统写入一个无订单请求：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "mode": "self_test",
  "run_id": "20260704153000",
  "result_path": "outputs/auto_live_adapter_selftest_result_20260704153000.json",
  "auto_live_config_sha256": "...",
  "orders": [],
  "expected_response": {
    "schema_version": "ashare_quant.command_adapter.v1",
    "run_id": "20260704153000",
    "status": "ok",
    "capabilities": ["submit_orders", "query_orders", "cancel_orders", "account_snapshot"],
    "auto_live_config_sha256": "..."
  }
}
```

适配器必须写回 JSON object，并至少包含：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "run_id": "20260704153000",
  "status": "ok",
  "capabilities": ["submit_orders", "query_orders", "cancel_orders", "account_snapshot"],
  "auto_live_config_sha256": "..."
}
```

`status` 可以是 `ok`、`ready` 或 `dry_run`。系统会校验 schema、run_id、配置指纹和 `submit_orders`、`query_orders`、`cancel_orders`、`account_snapshot` 能力。

## 桥接文件与请求完整性

正式 command 配置应同时启用：

```yaml
auto_live:
  command_adapter:
    require_pinned_files: true
    pinned_files:
      - path: scripts/broker_bridge.py
        sha256: 完整的64位SHA256
    require_hmac_integrity: true
    integrity_key_env: ASHARE_QUANT_ADAPTER_HMAC_KEY
```

`auto-live-adapter-files` 会在启动外部程序前逐文件比较完整 SHA-256。已配置固定文件时，即使 `require_pinned_files=false`，哈希不匹配仍会阻断，防止错误配置降低保护。

HMAC 密钥只从环境变量读取，最少 32 字节。请求包含规范化 payload 哈希与 HMAC；结果同时绑定请求哈希和结果哈希。提交程序启动后若结果签名失败，系统按“券商受理状态不明”处理，禁止自动重发。

```powershell
.\scripts\set_auto_live_hmac_key.ps1 -PersistUser
python -m ashare_quant auto-live-adapter-files
python -m ashare_quant auto-live-adapter-selftest
```

脚本不会打印密钥，也不会将密钥写入项目。轮换密钥后必须重启服务/计划任务，并重新执行 self-test 和回放认证。

## 离线生命周期回放认证

无订单 self-test 只能证明命令可启动和能力声明正确，不能证明真实券商字段映射。将脱敏后的提交、查询、撤单和账户快照请求/结果 JSON 放在同一目录后运行：

```powershell
python -m ashare_quant auto-live-adapter-replay --dir outputs/broker_replay
```

认证不会启动桥接器，也不会发送订单。它会检查 schema、run_id、当前配置指纹、逐票 ticket_id、唯一 broker_order_id、查询/撤单委托号稳定性、成交数量边界、账户环境和资金字段。输出为 `auto_live_adapter_replay_summary.csv` 与 `auto_live_adapter_replay_audit.csv`。

正式 command 配置应设置：

```yaml
auto_live:
  require_adapter_replay_certification: true
  adapter_replay_dir: outputs/broker_replay
```

`certified_simulation` 仅证明模拟闭环，真实自动实盘门禁只接受 `certified_live`，并要求回放证据中的 `auto_live_config_sha256` 与当前配置完全一致。

## submit_orders 请求

自动提交阶段会写入：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "mode": "submit_orders",
  "run_id": "20260704153100",
  "result_path": "outputs/auto_live_command_result_20260704153100.json",
  "auto_live_config_sha256": "...",
  "tickets_sha256": "...",
  "orders": [
    {
      "ticket_id": "t1",
      "symbol": "600000",
      "side": "buy",
      "quantity": 100,
      "limit_price": 10.0
    }
  ]
}
```

适配器返回：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "run_id": "20260704153100",
  "results": [
    {
      "ticket_id": "t1",
      "status": "submitted",
      "broker_order_id": "broker-123",
      "message": "accepted"
    }
  ]
}
```

逐票 `status` 接受 `submitted`、`failed`、`rejected`、`unknown`、`dry_run`。外部进程启动后的超时、异常退出、结果缺失、无效 JSON 或漏回票据会被系统归为 `unknown`，整次运行标记为 `submission_uncertain` 并写入去重台账，禁止直接重试。真实桥接程序应把券商返回的委托编号写入 `broker_order_id`，并把拒单、余额不足、价格非法、连接失败等原因写入 `message`。

## 模板自检

只验证协议和系统链路：

```powershell
python scripts/command_broker_bridge_template.py --simulate-submit request.json result.json
```

接入真实券商时，应复制模板并替换 `submit_response` 中的失败逻辑，调用券商 API 后再按上述契约返回逐票结果。不要把模板的模拟 submitted 结果当作真实成交或真实委托回报。


## cancel_orders 请求

运行 `python -m ashare_quant auto-live-query` 时，系统也会查询 `submission_uncertain` 的订单；这类订单可能没有 `broker_order_id`，桥接器必须支持按稳定的 `ticket_id` 查询并回传找回的券商委托号。恢复结果写入 `auto_live_submission_recovery.csv`，未恢复前禁止重新提交。

运行 `python -m ashare_quant auto-live-cancel` 时，系统读取最新 `auto_live_order_status.csv`，只撤销 `accepted`、`submitted`、`partially_filled` 且有 `broker_order_id` 的委托。桥接程序需要返回逐笔结果：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "run_id": "20260704154000",
  "results": [
    {"ticket_id": "t1", "broker_order_id": "broker-123", "cancel_status": "cancel_requested", "message": "accepted by broker"}
  ]
}
```

`cancel_status` 必须归一化到 `canceled`、`cancel_requested`、`already_filled`、`not_cancelable`、`rejected`、`failed`、`unknown`。系统只把 `canceled`、`cancel_requested`、`already_filled` 视为自动恢复成功。


## account_snapshot 请求

运行 `python -m ashare_quant auto-live-account` 时，系统会写入只读账户快照请求：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "mode": "account_snapshot",
  "run_id": "20260704154500",
  "auto_live_config_sha256": "...",
  "orders": []
}
```

桥接程序应返回券商账户现金、可用资金、持仓市值、总资产、冻结资金和持仓明细：

```json
{
  "schema_version": "ashare_quant.command_adapter.v1",
  "run_id": "20260704154500",
  "broker": "huatai",
  "environment": "live",
  "account": {
    "account_id": "masked-account",
    "cash": 1000000.0,
    "available_cash": 998000.0,
    "market_value": 2000.0,
    "total_asset": 1002000.0,
    "frozen_cash": 2000.0
  },
  "positions": [
    {"symbol": "600000", "quantity": 100, "sellable_quantity": 100, "cost": 10.0, "market_value": 1020.0, "last_price": 10.2}
  ]
}
```

系统会把该结果写入 `auto_live_broker_account_snapshot.csv`、`auto_live_broker_positions.csv` 和 `auto_live_broker_account_audit.csv`，并与本地 `account_state_snapshot.csv` / `account_state_positions.csv` 对账。真实桥接器必须返回 `environment=live`、稳定的脱敏 `account_id` 和请求中的 `auto_live_config_sha256`；模板演练返回 `simulation`，即使余额一致也会被正式门禁拦截。系统默认只接受 300 秒内、且配置指纹与当前配置一致的快照。执行 `auto-live-run` 时，如快照缺失、过期、配置不匹配或历史账户审计失败，系统会先自动调用一次只读 `account_snapshot`，并把结果写入 `auto_live_account_refresh.csv`；刷新仍失败时不会创建提交请求。仅离线演练时可显式设置 `allow_simulated_account_snapshot=true`。

## 无人值守服务与计划任务

`auto-live-service` 在周期编排外增加单实例锁、心跳、服务停止文件、失败退避和连续异常熔断。默认命令只运行一次审计：

```powershell
python -m ashare_quant auto-live-service
```

循环模式：

```powershell
python -m ashare_quant auto-live-service --loop --interval-seconds 60 --max-cycles 360
```

提交服务需要配置 `auto_live.service.enabled=true` 与 `allow_submit=true`，还要显式传入 `--submit`。服务级授权之外，所有正常自动实盘门禁仍必须通过。服务状态保存在 `auto_live_service_summary.csv`、`auto_live_service_heartbeat.csv`、`auto_live_service_history.csv`、`auto_live_service_lock_audit.csv` 和 `auto_live_service_stop_audit.csv`。

停止文件不会自动删除；遗留服务锁也不会仅因心跳过期自动清除。Windows 可用 `scripts/install_auto_live_task.ps1` 注册每日任务，脚本默认注册审计模式。

## 自动实盘周期编排

`auto-live-cycle` 是推荐的运维入口。默认只执行门禁审计，不调用券商：

```powershell
python -m ashare_quant auto-live-cycle
```

显式 `--submit` 后，系统才会按固定顺序执行：提交锁与前批恢复、订单分批、只读账户快照刷新、总门禁、独立风险限额、交易时间窗、券商提交、委托查询、成交候选生成、账户同步及后交易账户对账。任一必需阶段失败时，后续动作标记为 blocked/skipped，券商提交不会被调用。

分批规划由 `python -m ashare_quant auto-live-batch` 单独审计。规划器按订单数、单笔金额、单批买入金额、单批总金额和当日累计额度拆分，卖单优先；一次只允许一个批次进入提交。`auto_live_batch_summary.csv`、`auto_live_batch_plan.csv`、`auto_live_batch_orders.csv`、`auto_live_current_batch.csv` 和 `auto_live_active_batch.csv` 共同证明订单未丢失、批次顺序明确且活动批次不会重复提交。

```powershell
python -m ashare_quant auto-live-cycle --submit
```

只有模拟/本地账户需要验证成交回写时，才同时启用配置 `auto_live.allow_account_sync=true` 并显式确认：

```powershell
python -m ashare_quant auto-live-cycle --submit --confirm-account-sync
```

最新阶段、结论和跨周期历史分别保存在 `auto_live_cycle.csv`、`auto_live_cycle_summary.csv`、`auto_live_cycle_history.csv`。 后交易账户对账及历史保存在 `auto_live_post_trade_reconciliation.csv` 和 `auto_live_post_trade_reconciliation_history.csv`。每完成一个阶段都会原子更新这些文件，异常中断后可以定位最后完成阶段。周期不会自动撤单；提交状态不明或委托未终态时，下一周期会先尝试只读查询，仍未闭环则继续阻断新提交。

## 提交锁与崩溃恢复

正式提交前，系统会先原子写入 `outputs/auto_live_submission_intent.csv`，再把 `auto_live_run.csv` 标记为 `submitting`，最后才调用外部券商适配器。提交意图包含工单指纹、配置指纹、去重键以及预期请求/结果文件名；即使进程异常退出且锁后来被人工移除，相同工单和配置仍会被重复提交门禁拦截。

运行以下只读审计：

```powershell
python -m ashare_quant auto-live-lock
```

结果写入 `outputs/auto_live_submit_lock_audit.csv`。默认 `submit_lock_stale_seconds=900`：近期锁视为可能仍在运行，过期锁视为需要恢复的异常批次。系统不会自动删除锁。重复或并发点击写入 `outputs/auto_live_blocked_attempt.csv`，并保留原有 `auto_live_run.csv`、订单回报和确认审计。

发现过期锁时，必须先运行 `auto-live-query`，再在券商客户端按 `ticket_id`、请求文件和账户流水核对。若进程在写回结果前中断，查询命令会从已持久化的 `auto_live_command_request_<run_id>.json` 重建 `unknown` 工单；请求文件缺失、批次号不匹配或工单指纹不匹配时会失败关闭，不会查询旧批次。只有确认没有活动提交进程、券商端委托状态已查清且恢复证据已保留后，才可人工删除 `auto_live_submit.lock`。上一批全部进入 `filled/canceled/rejected/failed` 终态前，新批次仍会被阻断。不能仅依据锁的年龄重新提交。

## 独立风险限额与紧急停机

券商提交前必须运行独立风险审计：

```powershell
python -m ashare_quant auto-live-risk
```

审计结果写入 `outputs/auto_live_risk_audit.csv`，覆盖工单字段、方向白名单、单批订单数、单笔金额、单批买入/总额、单日累计买入/总额以及真实券商可用资金和现金缓冲。买入资金不依赖本批卖出回款。`auto_live_risk_reservations.csv` 会在调用券商适配器前预留当日额度；超时、状态不明或进程崩溃都不会自动释放，防止异常重启绕过日限额。

立即停止后续券商提交：

```powershell
python -m ashare_quant auto-live-stop --reason "operator review"
```

该命令原子创建 `outputs/auto_live.stop`。停机不影响报告、账户快照和委托查询。恢复必须显式确认，且存在提交锁时拒绝恢复：

```powershell
python -m ashare_quant auto-live-resume --confirm
```

清除停机文件并不代表允许下单，策略、账户、时间窗、前批终态、独立风控和适配器门禁仍会全部重新检查。配置中的 `auto_live.emergency_stop=true` 也会触发停机，且不能被 resume 命令修改。 停机会在取得提交锁后和外部进程启动前再次检查；若券商进程已经启动，必须继续执行 `auto-live-query`，必要时 `auto-live-cancel`，不能把本地停机视为已撤回请求。

## 事件审计链契约

正式 command 适配器配置应启用 auto_live.journal.enabled=true 和 require_hmac=true，密钥环境变量为 ASHARE_QUANT_ADAPTER_HMAC_KEY。

submission_intent 必须在调用券商程序前严格写入，submission_result 必须在券商调用返回后严格写入。查询、撤单、账户快照和周期结果采用尽力写入，但任何写入失败都会记录到 auto_live_journal_errors.csv，并阻断后续提交，直到对应事件被可信重放且错误标记为已解决。

使用 python -m ashare_quant auto-live-journal 执行只读验证。verified 表示已有事件且整条链通过；empty_ready 表示尚无事件但可以写入首条事件；failed 表示必须停止自动提交并人工调查。

## 真实账户与本地状态绑定

正式模板要求 auto_live.account_binding.required=true。先执行 auto-live-account 获取 environment=live 的账户快照，并确保 auto_live_broker_account_audit.csv 全部通过；再显式执行 auto-live-bind-account --confirm。绑定文件 auto_live_account_binding.json 使用 HMAC 签名，锁定券商、稳定脱敏 account_id、当前自动实盘配置指纹和本地 state_db 路径。

每次账户快照刷新后，auto_live_account_binding_audit.csv 都会重新验证绑定。当前券商登录账户变化、配置变化、状态库切换、绑定文件篡改或 HMAC 密钥不匹配时，auto_live_account_binding 门禁立即失败。模拟、paper 或 test 环境快照永远不能创建真实账户绑定。

## 券商服务器时间

正式 account_snapshot 响应必须在顶层返回 ISO 8601 且带时区的 broker_time，例如 2026-07-11T01:30:00.123+00:00。正式模板要求 require_broker_clock_sync=true，默认最大绝对漂移为 5 秒。字段缺失、无时区、解析失败或超差都会使 broker_clock_synchronized 审计失败。

模板桥接器使用本机 UTC 时间仅用于模拟协议测试。真实券商后端必须映射券商或柜台服务器时间；不得把桥接机器本机时间冒充券商时间。

## 提交前恢复点与灾难恢复

正式模板启用 auto_live.recovery.enabled=true 和 required_before_submit=true。提交锁获取、重复提交检查、前批恢复和独立风控复核通过后，系统创建原子恢复目录；恢复点成功验证后才会预留风险额度、写入 submission_intent 并调用券商。

恢复点包含 SQLite 在线备份、submission_tickets.csv、配置指定的 required_files/critical_files 和 HMAC 签名 manifest.json。auto-live-recovery-audit 会验证清单载荷、签名、全部文件哈希和 SQLite PRAGMA quick_check。

禁止在存在 unresolved submission intent 或券商委托未查询完成时直接还原 SQLite。灾难恢复顺序必须是：停止服务、保留现场、查询券商委托与成交、验证恢复点、重建本地账户状态、完成券商对账，最后才清除停机状态。

## 外部告警命令契约

告警命令接收 request_path 和 result_path 两个末尾参数，支持 self_test 与 deliver。请求和结果使用 HMAC-SHA256，结果必须绑定请求载荷哈希。self_test 成功响应必须包含 status=ok、environment=live 和 capabilities 中的 alert。

告警发件箱是持久化 CSV。通知失败记录为 pending，auto-live-alert-retry 只重试通知，不触碰订单。正式门禁要求自检未过期、配置指纹一致、真实环境、固定文件哈希有效、HMAC 密钥可用且 pending 数量为零。

模拟桥接模板只用于验证进程和签名协议。生产后端应接入至少一个独立于交易主机的通知目标，并在消息中包含服务运行号、周期号、订单运行号和处置建议。

## Canary 与分阶段投产

真实后端首次接入不得直接使用 full。正式模板默认 rollout.stage=canary，限制 1 笔且总额不超过 2 万元。approve 命令只在真实账户绑定、certified_live 回放、真实告警、自检文件和事件日志全部通过后签发短期授权。

授权文件 auto_live_rollout_approval.json 由 HMAC 签名并绑定账户、配置、阶段、限额和过期时间。auto_live_rollout_audit.csv 在总门禁和提交锁内各执行一次。锁内复核失败使用 auto_live_rollout_recheck_failed 终止流程，早于恢复点、风险预留、submission_intent 和券商调用。

阶段晋级是人工治理动作。Canary 完成后应先核对委托、成交、费用、资金、持仓、告警和恢复点，再修改到 limited；full 仅在多日小额验证和异常演练均通过后考虑。
