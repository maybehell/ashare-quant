# AShare Quant

本项目是一个本地运行的 A 股日频量化研究与模拟盘系统，覆盖数据缓存、因子选股、回测、候选策略研究、风险门禁、模拟盘订单、RPA 辅助工单和中文 HTML 报告。

> 仅用于研究和模拟交易，不构成投资建议。默认不连接券商 API，也不会自动点击最终下单。
## 架构与开发规范

本仓库采用统一、可审计的开发约定。任何代码、配置、脚本、测试或文档修改，
都必须先阅读并遵循以下文件：

- [统一架构规划](docs/ARCHITECTURE.md)：系统分层、依赖方向、状态所有权、实盘安全不变量和演进路线。
- [开发流程](docs/DEVELOPMENT_PROCESS.md)：修改前检查、实现规则、验证矩阵和完成标准。
- [开发日志](docs/DEVELOPMENT_LOG.md)：按时间倒序记录实际改动、验证结果、风险和后续工作。
- [架构决策记录](docs/adr/README.md)：保存影响长期边界、状态和安全策略的决策及原因。
- [仓库开发契约](AGENTS.md)：所有开发者和 AI 代理必须遵循的入口规则。
- [GitHub 私有同步](docs/GITHUB_SYNC.md)：源码白名单、敏感扫描、手动同步和定时任务说明。

每次修改必须同步更新开发日志；改变模块边界、数据流、状态所有权或实盘安全策略时，
还必须更新架构文档并新增或替代 ADR。

## 当前结论

报告顶部现在分成两个状态，不是收益预测，也不是保证策略有效：

- `实用状态`：用于判断系统是否可以按当前缓存生成模拟盘/人工辅助交易清单。它重点检查当前回测证据是否过期、过去区间是否跑赢基准、样本外是否不弱于基准、回撤/换手是否可接受、成本压力测试是否仍不弱于基准。
- `策略可信度`：用于判断回测证据是否足够，不是收益预测。它检查回测交易日、样本内/样本外、最近超额是否过度集中、走步折数、滚动窗口、年度稳定性和调参污染。
- `严格实盘`：更保守，额外要求样本内稳定、足够走步折数、行业和上市日期元数据、调参污染解除、冻结后前向验证、shadow 前向观察全部通过。

严格实盘等级含义：

- `实盘级`：收益、样本内、样本外、走步验证、数据新鲜度、行业元数据、上市日期元数据、调参标记全部通过。
- 还必须有足够的 `shadow` 前向观察证据：可评估信号数量、平均超额收益、正超额比例和行情覆盖率全部达标。
- 同时要求最大回撤、年化换手和高成本压力测试通过；成本压力测试必须由当前配置生成，旧配置结果不能用于放行。
- `模拟盘级`：策略表现通过，但还有流程或证据缺口，只适合继续模拟盘和人工观察。
- `研究级`：可用于研究复盘，但不应生成实盘辅助订单。
- `不通过`：策略表现或基础数据本身未达门槛。

当前系统默认按 `practical_guard` 走实用门禁，允许精选股票池场景下的低全市场覆盖率，也允许 `optimized_on_current_cache=True` 作为提示项而不是阻断项。换句话说，报告里的 `priced_stock_coverage`、行业缺失、上市日期占位和调参标记不等于“收益一定不可信”；它们说明这份结果不能当作严格全市场/严格样本外实盘证据。

当前默认严格实盘还要求前 70% 样本超额不低于 0、走步验证折数不少于 3。若报告显示“观察级”，含义是系统可以继续模拟盘和前向观察，但还不能把当前参数当作实盘级长期有效证据。

普通 `paper` 仍走严格实盘门禁；要按当前目标生成可审计的模拟盘/人工辅助清单，使用：

```powershell
.\scripts\aq.ps1 practical
```

严格实盘仍可能停留在 `模拟盘级` 的主要原因是：

- 参数已经冻结为候选实盘参数，见 `outputs/live_candidate.csv`。
- 冻结日为当前缓存最后行情日，冻结后新增交易日仍为 `0/60`。
- `optimized_on_current_cache=True` 仍需要由冻结后的新增行情前向验证来解除。
- `shadow_forward_audit.csv` 目前也还没有足够可评估信号，不能证明每日盘后信号在真实后续行情中稳定有效。
- 成本压力测试用于验证滑点和佣金升高后策略仍有正超额，避免只在理想交易成本下看起来有效。

系统会因此阻止普通 `paper` 生成严格实盘辅助订单；这是有意设计的安全门禁。`practical` 命令会改用实用门禁，但仍会保留订单来源、账户审计、RPA 前置检查和人工最终确认。

如果 AkShare 或缓存里最新完整行情日落后于今天，但仍在 `max_cache_age_days` 允许范围内，`practical` 会把订单执行日滚动到今天之后的下一个工作日，并在 `orders_provenance.csv` 记录 `order_date_adjusted=True` 和 `signal_age_days`。严格 `paper` 不做这个调整。

## 快速开始

```powershell
.\scripts\setup_env.ps1
.\scripts\aq.ps1 doctor
.\scripts\aq.ps1 fetch --cached-universe --max-symbols 800
.\scripts\aq.ps1 metadata --max-symbols 800 --workers 3
.\scripts\aq.ps1 workflow
.\scripts\aq.ps1 freeze-candidate
.\scripts\aq.ps1 report
.\scripts\aq.ps1 practical
```

如果网络数据源不可用，可先用样例数据验证流程：

```powershell
.\scripts\aq.ps1 fetch --sample
.\scripts\aq.ps1 research
.\scripts\aq.ps1 backtest
```

## 图形界面

Windows 下可以双击 `launch_gui.bat`，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gui.ps1
```

界面提供日常刷新、模拟盘、报告生成、可信度摘要、RPA 工单预览、候选冻结、前向观察和环境检查。侧栏按“日常 / 设置”分组，避免低分辨率下操作被截断；“策略档案”页并排展示默认基线与自适应候选的收益、基准、回撤、可信度、实用门禁、严格实盘门禁、冻结后前向天数和 Shadow 观察（严格实盘还要求至少 6 个相隔 15 天的不同信号批次，避免重复运行虚增样本）状态，并可分别打开两套报告与输出目录。

自适应配置位于 configs/adaptive_trend.yaml，报告位于 outputs_adaptive_trend/report.html。它当前是冻结观察候选，不因回测优于基线就自动晋级；只有冻结后前向验证和 Shadow 门禁全部通过，才可以改变其严格实盘定位。命令行仍是最稳定、最容易审计的运行方式。

候选研究会同时生成 strategy_research_manifest.csv，记录研究实际使用的完整交易日数据指纹和 strategy_research.csv 产物指纹。严格实盘门禁要求参数邻域稳定性、研究数据一致性和产物指纹全部通过；freeze-candidate 也会拒绝缺少这些证据的候选，并把稳定性报告指纹写入冻结清单。旧冻结清单缺少该字段时必须重新研究、生成报告并显式重新冻结。因子稳健性同时要求基准策略通过反过拟合检查、扰动通过率达标、脆弱因子场景比例受限，且组合成员删除或权重扰动不得出现脆弱场景；单个极端删因子测试不再与成员失效等价。

## 常用命令

```powershell
# 检查依赖、配置和缓存
.\scripts\aq.ps1 doctor

# 探测 AkShare 数据源，不写入正式缓存
.\scripts\aq.ps1 probe

# 刷新行情池；网络不稳时先用 50 或 100 测试
.\scripts\aq.ps1 fetch --max-symbols 800

# 使用本地缓存股票池刷新行情，适合股票列表源不稳定时
.\scripts\aq.ps1 fetch --cached-universe --max-symbols 800

# 首次建立更长历史时显式关闭增量模式；建议先在隔离配置和小股票池验证
.\scripts\aq.ps1 fetch --cached-universe --full-history --max-symbols 100 --no-metadata

# 补行业和上市日期元数据；workers 可按网络稳定性调低
.\scripts\aq.ps1 metadata --max-symbols 800 --workers 3

# 候选策略研究排名
.\scripts\aq.ps1 research

# 正式回测并生成报告
.\scripts\aq.ps1 backtest

# 研究工作流：research -> backtest -> stress -> compare -> report
.\scripts\aq.ps1 workflow

# 冻结当前策略参数，开始前向验证观察期
.\scripts\aq.ps1 freeze-candidate

# 基于当前报告输出刷新冻结后前向验证表
.\scripts\aq.ps1 candidate-audit

# 只审计候选是否满足激活条件，不切换策略
.\scripts\aq.ps1 candidate-activation-audit --candidate-config configs/hs300_point_in_time_core.yaml

# 审计全部通过后，携带精确 candidate_id 原子激活；不启用券商提交
.\scripts\aq.ps1 activate-candidate --candidate-config configs/hs300_point_in_time_core.yaml --candidate-id <CANDIDATE_ID> --confirm

激活记录保存在 `data/active_execution_strategy.json`。GUI、`run_local.cmd` 和未显式传入 `--config` 的命令会保留默认输出目录、账户与券商配置，只替换已验证候选的策略行为。记录哈希异常、包含提交授权、候选未满前向窗口或收益未达标时均失败关闭。激活后，`paper`、`daily`、`practical` 和一键运行会在订单生成前验证默认加载器身份；此时显式传入 `--config` 绕过激活记录会被拒绝。

```powershell
# 先审计停用，不改变当前策略
.\scripts\aq.ps1 deactivate-candidate --candidate-id <ACTIVE_CANDIDATE_ID>

# 精确确认后归档激活记录并恢复默认策略；不启用券商提交
.\scripts\aq.ps1 deactivate-candidate --candidate-id <ACTIVE_CANDIDATE_ID> --confirm
```

停用归档保存在 `data/active_execution_archive/`，原激活记录的哈希、候选身份和停用时间会被保留。

订单来源还会绑定独立的 `execution_epoch_id`。默认策略首次生成订单时建立纪元，候选激活使用新的 `activation_id` 作为纪元，候选停用后再次轮换默认纪元。因此即使策略配置在回退后恢复为原值，旧 `orders.csv`、RPA 工单及其签名清单也不能重新进入执行链。当前默认纪元保存在 `data/execution_strategy_epoch.json`，历史纪元仅用于审计。

受控华泰配置启用 `validation.apply_active_execution=true`，会在保留实盘输出目录、账户和券商设置的同时应用当前激活策略。研究候选与普通显式 `--config` 仍不会隐式应用激活记录。

每次 `paper`、`daily`、`practical`、一键运行或默认输出回测开始前，系统会先将上一批订单、RPA 票据、签名清单、批次计划和人工确认文件移入 `outputs/order_quarantine/<run_id>/`，并写入 `order_artifact_invalidation.csv`。策略暂停、行情过期或新周期计算失败后，旧票据不会继续留在当前执行目录；待处理的券商提交意图和账户证据不会被移动，以便恢复审计。

# 跳过耗时较长的成本压力测试；正式观察期不建议跳过
.\scripts\aq.ps1 workflow --skip-stress

# 重新生成 HTML 报告
.\scripts\aq.ps1 report

# 生成实盘就绪路线图，列出未通过门禁、剩余缺口和下一步命令
.\scripts\aq.ps1 roadmap

# 模拟盘订单；默认会检查实盘门禁
.\scripts\aq.ps1 paper

# 实用一键流程；必要时重跑 backtest/stress，检查实用门禁，生成 paper 订单、RPA 工单和报告
.\scripts\aq.ps1 practical

# 本地模拟盘最简一键流程；先显式重置本地模拟账户，再生成订单和 RPA 工单
.\scripts\aq.ps1 practical --reset-account --confirm-account-reset

# 如果已经刷新过行情，希望一键流程先刷新缓存股票池
.\scripts\aq.ps1 practical --refresh --cached-universe --max-symbols 800

# 账户、持仓、订单来源和 SQLite 状态库对账
.\scripts\aq.ps1 reconcile

# 导出当前 SQLite 账户状态，按最新完整行情日重估现金、持仓、锁定数量和权益
.\scripts\aq.ps1 account-state

# 重置本地模拟账户为现金账户；会清空本地持仓、订单、成交和旧 RPA 产物
.\scripts\aq.ps1 account-reset --confirm

# 生成账户导入模板；人工填写现金、持仓、可卖数量和成本
.\scripts\aq.ps1 account-template

# 导入人工核对后的账户状态；会覆盖本地模拟账户并清理旧的可执行订单/RPA 文件
.\scripts\aq.ps1 account-import --file .\outputs\account_import_template.csv --confirm

# 生成成交回填模板；手工/RPA 下单后填写真实成交价、数量、费用和金额
.\scripts\aq.ps1 fill-template

# 导入人工核对后的真实成交；会更新现金、持仓、成交表，并防止同一文件重复导入
.\scripts\aq.ps1 fill-import --file .\outputs\fill_import_template.csv --confirm

# 非执行 shadow 信号留痕；用于门禁未通过期间积累前向观察记录
.\scripts\aq.ps1 shadow

# 用后续真实行情评估已归档 shadow 信号
.\scripts\aq.ps1 shadow-audit

# 推荐盘后观察流程；刷新门禁证据、按需补跑压力测试、记录 shadow、评估 shadow、更新报告
.\scripts\aq.ps1 observe --quiet

# 严格日常流程；--refresh 会刷新行情、重跑 backtest，按需补跑压力测试，再进入 paper
.\scripts\aq.ps1 daily --refresh --cached-universe --max-symbols 800

# 日常流程也可以显式使用实用门禁
.\scripts\aq.ps1 daily --practical

# 只有在研究提速且接受证据过期风险时，才跳过 observe/daily 的自动压力测试维护
.\scripts\aq.ps1 observe --quiet --skip-stress
```

历史行情默认使用 `data.history_workers=3` 的有限并发，并按
`data.checkpoint_every` 持续写入 `prices_partial`。单股网络失败会按
`data.history_retries=2` 重试；主线程负责 Parquet 检查点和来源台账写入，
不会由工作线程并发修改缓存。`--full-history` 从 `backtest.start` 开始抓取，

股票行业和上市日期默认通过 BaoStock 的全市场批量接口刷新，AkShare
保留为回退源。批量元数据会写入 `stock_metadata_provenance` 来源台账；
安装数据依赖时需要 `baostock>=0.9.3`。
默认 `metadata_priced_only=true`，元数据刷新只覆盖当前有行情的精选股票。
运行清单中的 `stocks_sha256` 也只签名这些实际参与回测的股票；完整证券目录
另记为 `stock_catalog_sha256`，因此补充无行情候选不会误判既有策略证据过期。

仅应在隔离缓存通过价格、来源和存活偏差审计后再用于正式研究。


如果只是研究演练，并且明确接受门禁风险，可以临时绕过：

```powershell
.\scripts\aq.ps1 paper --allow-weak-strategy
```

## 推荐日常流程

1. 盘后刷新行情和元数据。
2. 运行回测和报告。
3. 首次确定候选参数后运行 `freeze-candidate`，之后不要改策略、风控、交易和股票池参数。
4. 后续每天优先运行 `observe --quiet`，系统会自动更新回测门禁证据、按需补跑当前配置/数据的成本压力测试、记录 shadow 信号、评估已有 shadow 信号并刷新报告。
5. 查看 `outputs/report.html` 顶部等级、`live_readiness.csv` 和“证据缺口提示”。
6. 只有冻结后新增交易日、候选参数前向超额收益、shadow 观察数量和 shadow 前向表现都通过门禁时，才生成实盘辅助订单。
7. 即使生成 RPA 工单，最终下单仍必须人工核对。

`paper` 只写入模拟盘相关文件，不覆盖正式回测证据：

- `orders.csv`
- `positions.csv`
- `trades.csv`
- `paper_equity_snapshot.csv`
- `account_reconciliation.csv`
- `paper_signal_snapshot.csv`
- `signal_log.csv`
- `orders_provenance.csv`
- `rejected_orders.csv`

正式门禁证据仍由 `backtest` / `workflow` 生成的 `run_manifest.csv`、`live_readiness.csv` 和 `report.html` 负责。系统会校验当前配置和缓存数据的哈希；如果刷新过行情但没有重新运行回测，`paper` 会拒绝生成订单。

`paper` 执行前不会只信任旧的 `live_readiness.csv`。它会从当前 `benchmark_comparison.csv`、`data_quality.csv`、`robustness_split.csv`、`walk_forward_summary.csv`、`live_candidate.json` 和 `shadow_forward_audit.csv` 重新计算实盘门禁，防止报告或 shadow 审计更新后旧门禁结果被误用。

RPA 导出默认只接受带 `orders_provenance.csv` 且来源为 `paper` 的订单，避免误把回测订单导入券商客户端。研究调试时才使用 `export-orders --allow-unverified-source`。
默认配置还要求 `account_reconciliation.csv` 全部通过；该文件会核对权益快照、现金、持仓市值、订单来源数量、执行日期、成交字段和 SQLite 状态库。任一对账项失败时，RPA 前置检查会阻止填单。
默认配置还要求 `account_state_audit.csv` 全部通过。`export-orders` 和 `rpa-fill --execute` 会先自动刷新 `account-state`，核对当前 SQLite 现金、持仓、T+1 锁定数量、快照权益和最新估值覆盖；审计失败时不会继续执行填单。

成交回填默认要求 `fill_import_template.csv` 中的成交能匹配当前 `orders.csv` 和 `orders_provenance.csv`：来源必须是强门禁通过后的 `paper` 订单，且成交日期、股票、方向和数量不能超过系统生成的订单。这样可以防止把非系统订单或手工误填成交写入本地账户状态。只有做人工修账时，才应通过自定义配置关闭 `execution.require_fill_order_match`。

`export-orders` 会同时生成 `rpa_pre_trade_checks.csv`。该清单检查订单来源、弱门禁标记、工单数量、被拦截工单、单笔金额、单日买入金额、人工最终确认和禁止自动提交等条件。`rpa-fill --execute` 会重新生成并强制检查该清单；任一项失败时，不会向券商客户端填入字段。

如果实盘门禁尚未通过，但希望每天保留候选参数的前向观察记录，使用 `shadow`，不要使用 `paper --allow-weak-strategy` 生成可执行订单文件：

```powershell
.\scripts\aq.ps1 shadow
```

`shadow` 输出 `shadow_orders.csv`、`shadow_signal_snapshot.csv`、`shadow_provenance.csv` 和 `shadow_signal_log.csv`，不会更新本地账户、不会写 `orders_provenance.csv`，也不会被 RPA 默认导出。

每次 `shadow` 还会把信号归档到 `outputs/shadow_archive`。后续有新行情后运行：

```powershell
.\scripts\aq.ps1 observe --quiet
```

系统会生成 `shadow_forward_audit.csv`，用真实后续行情计算每个 shadow 信号的组合收益、沪深 300 收益和超额收益。

默认实盘门禁要求至少 `20` 条可评估 shadow 信号，每条信号至少观察 `5` 个完整交易日，平均超额收益不低于 `0`，正超额比例不低于 `55%`，信号行情覆盖率不低于 `95%`。这些门槛用于约束“回测好看但盘后信号未经验证”的风险。

shadow 门禁只统计当前冻结候选 `candidate_id` 和当前实盘相关配置 `strategy_hash` 完全匹配的记录。更换策略、交易参数、股票池、风控或基准后，旧 shadow 观察不能继续为新配置背书。

回测、paper 和 shadow 默认只使用“完整行情日”：日行情股票覆盖率至少达到 `90%`，并且同日有基准指数数据。若缓存里出现部分股票已更新但基准或大量个股缺失的日期，系统会自动回退到最近完整行情日，防止缺失持仓价格被误当作 0 造成假回撤。

`priced_stock_coverage` 有两套解释口径：默认 `universe.scope=curated` 表示只验证精选股票池，低全市场覆盖率不会单独拦截，但报告结论只能解释为“当前精选子股票池策略”；如果改成 `universe.scope=full_market`，则 `priced_stock_coverage` 必须达到 `live_guard.min_full_market_priced_stock_coverage`，否则不能视为全市场回测证据。

默认实盘门禁还要求最大回撤不超过 `25%`、年化换手不超过 `6` 倍、`high_cost` 成本压力场景超额收益不低于 `2%`、压力场景最大回撤不超过 `25%`。`cost_stress.csv` 会记录当前配置哈希和当前股票、行情、基本面、基准数据哈希；配置或数据变更后需要重新运行 `stress` / `workflow`。`observe` 和 `daily` 默认会检测压力测试证据是否过期，并在需要时自动补跑；只有显式传入 `--skip-stress` 时才跳过。


失败的策略实验可以保留配置与输出用于复核，但应设置 `validation.research_only=true` 和 `experiment_status=rejected`。研究专用配置仍可运行回测、报告和压力测试，`freeze-candidate` 即使带 `--force` 也会拒绝，防止“一键观察所有候选”后误冻结为正式候选。当前 `ensemble_factor_capped` 与 `ensemble_redundant_three_member` 均处于此状态。

## 默认策略

默认策略为 `large_liquidity150`：

- 股票池：沪深主板、创业板、科创板；排除北交所、ST、上市未满 60 天、低成交额和不可交易股票。
- 因子：高流动性、中长期动量、趋势质量、低回撤、低波动。
- 持仓：最多 150 只，单股目标权重不超过 5%，行业上限 30%。
- 加权：按成交额平方根加权，降低单一股票集中度。
- 风险覆盖层：沪深 300 跌破长期均线或波动率升高时降低目标仓位。

## 输出文件

主要文件位于 `outputs`：

- `report.html`：中文 HTML 报告。
- `live_readiness.csv`：实盘门禁检查。
- `practical_readiness.csv`：实用门禁检查；用于 `paper --practical`、`daily --practical` 和 `practical`。
- `strategy_reliability.csv`：策略可信度与过拟合检查；用于解释“收益是否集中在近期行情”“走步折数是否足够”等问题。
- `live_candidate.csv`：当前冻结候选参数摘要。
- `live_candidate_validation.csv`：冻结后新增行情前向验证。
- `live_readiness_roadmap.csv`：实盘门禁缺口、剩余数量和下一步命令。
- `data_quality.csv`：数据质量诊断。
- `performance_metrics.csv`：收益、回撤、波动、换手、信息比率。
- `benchmark_comparison.csv`：策略与沪深 300 逐日对比。
- `robustness_split.csv`：样本内 / 样本外拆分。
- `walk_forward.csv` / `walk_forward_summary.csv`：走步验证。
- `backtest_orders.csv`：仅供研究审计的回测模拟订单，不是执行输入。
- `orders.csv`、`positions.csv`、`trades.csv`：`orders.csv` 仅由通过门禁的模拟盘流程生成；持仓和成交为本地模拟状态。
- `order_source_diagnostics.csv`：判断当前 `orders.csv` 是可执行 paper 订单还是回测/未验证订单。
- `orders_provenance.csv`：订单来源、信号日、执行日、信号哈希和配置哈希。
- `account_reconciliation.csv`：模拟盘权益、现金、持仓、订单来源和 SQLite 状态库对账。
- `account_reset_audit.csv`：本地模拟账户重置审计。
- `account_import_template.csv`：人工账户状态导入模板。
- `account_import_audit.csv`：账户导入校验结果；导入失败也会写入原因。
- `account_import_snapshot.csv` / `account_import_positions.csv`：最近一次导入后的账户快照和持仓估值。
- `account_state_audit.csv`：当前 SQLite 账户状态审计；核对现金、持仓数量、T+1 锁定数量、快照权益和估值价格覆盖。
- `account_state_snapshot.csv`：当前账户现金、记录权益、按最新行情重估权益、锁定数量和可卖数量。
- `account_state_positions.csv`：当前 SQLite 持仓、成本、锁定数量、可卖数量和最新估值。
- `account_state_missing_prices.csv`：账户状态中缺少缓存价格的持仓明细；该文件非空时 RPA 前置检查会阻止填单。
- `fill_import_template.csv`：真实成交回填模板。
- `fill_import_audit.csv`：成交回填校验结果；包含重复导入、现金、可卖数量和估值覆盖检查。
- `fill_order_match.csv`：成交与系统订单的日期、股票、方向和数量匹配明细。
- `fill_import_trades.csv`：最近一次导入的真实成交明细。
- `fill_import_snapshot.csv` / `fill_import_positions.csv`：最近一次成交回填后的账户快照和持仓估值。
- `paper_signal_snapshot.csv`：当日候选信号、目标权重和排名。
- `signal_log.csv`：每日信号哈希日志，用于冻结后前向观察留痕。
- `shadow_orders.csv` / `shadow_signal_snapshot.csv` / `shadow_signal_log.csv`：门禁未通过期间的非执行观察记录。
- `shadow_archive/`：按信号日归档的 shadow 订单、信号和来源文件。
- `shadow_forward_audit.csv`：shadow 信号后续真实行情表现。
- `rpa_pre_trade_checks.csv`：RPA 填单前的可审计风控清单。
- `rpa_orders.csv`：人工确认前的 RPA 辅助工单。

## RPA 边界

系统可以把模拟盘订单导出为 RPA 工单，也可以在配置坐标后辅助填入券商客户端字段，但默认安全规则是：

- 不点击最终提交。
- 单笔和单日金额有限制。
- 买入必须整手。
- 过期订单会被拦截。
- `paper --allow-weak-strategy` 或 `shadow` 生成的观察/研究订单不会被默认导出。
- 当前账户状态审计失败时，RPA 前置检查会阻止填单。
- `rpa_pre_trade_checks.csv` 任一项失败时，`rpa-fill --execute` 会拒绝填单。
- 最终下单必须人工核对。

```powershell
.\scripts\aq.ps1 export-orders
.\scripts\aq.ps1 rpa-preview
.\scripts\aq.ps1 rpa-fill --index 0
```

真正执行字段填入需要额外安装可选依赖并校准 `configs/rpa_template.yaml`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rpa.txt
.\scripts\aq.ps1 rpa-fill --index 0 --execute
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 自动实盘独立风控

日常一键运行现在会自动执行一次只读的自动实盘周期审计，并把阶段结果写入
`outputs/auto_live_cycle.csv` 和 `outputs/auto_live_cycle_summary.csv`。该模式不会调用券商提交接口：

```powershell
.\scripts\aq.ps1 auto-live-cycle
```

只有已接入并审计真实券商适配器、全部门禁通过，而且命令行显式允许时，周期才会提交订单；提交后会自动查询委托并生成成交候选：

```powershell
.\scripts\aq.ps1 auto-live-cycle --submit
```

华泰只读与 Canary 配置固定使用独立的 `data/huatai_live_state.sqlite3`，不得与模拟盘 `data/state.sqlite3` 共用。券商快照会先生成不写数据库的 `broker_account_import_preview.csv` 和预检审计。

`broker-refresh` 还会生成 `broker_order_preview.csv`、`broker_order_preview_signal.csv` 和 `broker_order_preview_audit.csv`。这些文件只用于人工复核，始终标记为不可执行，不会写入 `orders.csv` / `rpa_orders.csv`，也不会修改实盘账户数据库。

首次建立独立实盘账本时，先从报告复制当前 `snapshot_run_id` 和脱敏 `account_id` 运行审计；只有追加 `--confirm` 才会把临时验证数据库原子提升为 `data/huatai_live_state.sqlite3`：

```powershell
.\scripts\aq.ps1 --config configs/huatai_desktop_canary.yaml broker-account-init --snapshot-run-id <RUN_ID> --account-id <MASKED_ID>
.\scripts\aq.ps1 --config configs/huatai_desktop_canary.yaml broker-account-init --snapshot-run-id <RUN_ID> --account-id <MASKED_ID> --confirm
```

该命令拒绝覆盖已有实盘数据库，且不会自动创建账户绑定、解除急停或打开券商提交。

实盘订单来源还启用独立 HMAC 完整性链。`paper` 会把订单哈希、配置指纹、策略、执行日期和仓位保护乘数一起写入并签署 `orders_provenance.csv`；`export-orders`、RPA 预检和报告会重新验证。导出后还会生成 `rpa_orders_integrity.json`，绑定可执行工单、订单来源和当前配置；自动实盘分批前再次验签，失败时清空当前/活动批次并记录 `blocked_ticket_integrity`。华泰配置强制 `rpa.require_order_provenance_hmac: true`，密钥缺失、弱于 32 字节、来源被修改、工单被修改或签名不匹配时均拒绝继续。普通模拟配置保留哈希模式，便于不含实盘密钥的本地演练。

本地账户成交同步还需要配置 `auto_live.allow_account_sync=true` 和第二重命令行确认。当前本地账户为模拟账户时，可用该模式验证完整闭环：

```powershell
.\scripts\aq.ps1 auto-live-cycle --submit --confirm-account-sync
```

周期按“提交锁/前批恢复、订单分批、账户快照、总门禁、独立风控、交易时间窗、提交、查询、成交候选、账户同步、后交易券商账户对账”的顺序运行，任一必需阶段失败即停止后续动作。周期历史保存在 `outputs/auto_live_cycle_history.csv`。

系统会按 `auto_live.risk_limits` 自动拆分订单，卖出工单优先进入批次；每次只放行一个批次，前一批未全部进入 filled/canceled/rejected/failed 终态时，后续批次保持排队。超限或字段异常的工单不会被静默丢弃，而会阻断整个计划并写入审计文件：

- `outputs/auto_live_batch_summary.csv`：批次总数、当前批次、排队数量和阻断结论。
- `outputs/auto_live_batch_plan.csv`：逐批订单数、方向、金额、状态和关联券商运行号。
- `outputs/auto_live_batch_orders.csv`：原始 RPA 工单到批次的完整映射。
- `outputs/auto_live_current_batch.csv`：当前周期唯一允许提交的订单。
- `outputs/auto_live_active_batch.csv`：已经提交但尚未全部终态的订单，用于恢复和防重。

只生成批次计划、不调用券商：

```powershell
.\scripts\aq.ps1 auto-live-batch
```

## 无人值守自动实盘服务

服务入口默认只运行一次审计周期，不提交订单：

```powershell
.\scripts\auto_live_service.ps1
```

持续审计可使用：

```powershell
.\scripts\auto_live_service.ps1 -Loop -IntervalSeconds 60 -MaxCycles 360
```

提交模式需要同时满足配置中的 `auto_live.service.enabled=true`、`auto_live.service.allow_submit=true`，并在命令行显式传入 `-Submit`。策略、回放认证、账户、批次、风险、交易时间窗和前批终态门禁仍全部有效。

服务使用独立单实例锁和心跳；连续异常达到阈值会熔断。停止与恢复：

```powershell
.\scripts\aq.ps1 auto-live-service-stop --reason "maintenance"
.\scripts\aq.ps1 auto-live-service-resume --confirm
.\scripts\aq.ps1 auto-live-service-lock
```

Windows 计划任务安装脚本默认注册审计任务，不会提交订单：

```powershell
.\scripts\install_auto_live_task.ps1
```

注册可提交任务还必须同时使用 `-Submit -ConfirmSubmit`；这只允许服务进入提交审计，不能绕过系统门禁。

真实 command 适配器还应启用两项完整性门禁：

- `require_pinned_files=true` 与 `require_complete_pin_manifest=true`：固定桥接脚本、桥接配置及其直接导入的本地执行模块完整 SHA-256；依赖缺项、重复路径或文件被替换时均在进程启动前阻断。
- `require_hmac_integrity=true`：请求与结果使用只存在于环境变量中的 HMAC-SHA256 密钥签名，结果还必须绑定对应请求。

生成密钥但不打印密钥值：

```powershell
.\scripts\set_auto_live_hmac_key.ps1 -PersistUser
```

核对固定文件：

```powershell
.\scripts\aq.ps1 auto-live-adapter-files
```

密钥至少 32 字节，不会写入配置、JSON、CSV 或 HTML。桥接文件可信变更后必须重新计算 SHA-256、更新 `pinned_files` 并重新执行 self-test 与回放认证。

真实 command 券商桥接器还应启用 `require_adapter_replay_certification=true`。系统只读取保存的请求/结果 JSON，不发送新订单，并核对提交、查询、撤单、账户快照、逐票映射、券商委托号稳定性和成交数量边界：

```powershell
.\scripts\aq.ps1 auto-live-adapter-replay --dir outputs/broker_replay
```

结果写入 `auto_live_adapter_replay_summary.csv` 和 `auto_live_adapter_replay_audit.csv`。`certified_simulation` 只能证明模拟桥接闭环，正式门禁只接受绑定当前配置的 `certified_live`。

自动实盘提交前可单独检查当前批次的硬风险限额：

```powershell
.\scripts\aq.ps1 auto-live-risk
```

紧急停止后续提交：

```powershell
.\scripts\aq.ps1 auto-live-stop --reason "operator review"
```

复核券商端委托和提交锁后，显式恢复：

```powershell
.\scripts\aq.ps1 auto-live-resume --confirm
```

停机文件不会自动删除；恢复也不会绕过策略、账户、时间窗、前批终态或券商适配器门禁。真实提交前的当日金额会先写入 `outputs/auto_live_risk_reservations.csv`，状态不明和崩溃批次继续占用额度。

## 自动实盘事件审计链

系统为自动实盘生命周期维护只追加事件日志 auto_live_event_journal.csv，记录提交意图、提交结果、委托查询、撤单、账户快照和周期结果。每条事件包含前序哈希、载荷哈希和事件哈希；正式模板还要求使用环境变量 ASHARE_QUANT_ADAPTER_HMAC_KEY 生成 HMAC-SHA256 签名。

只读校验命令：.\scripts\aq.ps1 auto-live-journal

校验结果写入 auto_live_journal_summary.csv、auto_live_journal_audit.csv 和 auto_live_journal_errors.csv。日志无法解析、哈希链断裂、签名无效、写锁遗留或存在未解决的追加错误时，自动提交门禁会保持阻断。不要手工修改事件日志；应保留原文件并从可信备份恢复。

## 真实账户绑定

正式自动实盘除了实时读取券商账户，还必须显式证明当前本地 SQLite 状态库镜像的是同一个真实账户。完成真实账户读取和全部对账后，执行：

    .\scripts\aq.ps1 auto-live-bind-account --confirm

只读检查使用 auto-live-account-binding；解除绑定使用 auto-live-unbind-account --confirm。绑定锁定券商、稳定脱敏账户号、当前自动实盘配置和状态库路径，并使用 ASHARE_QUANT_ADAPTER_HMAC_KEY 签名。账户切换、配置变化、状态库路径变化、签名错误或手工篡改都会阻断提交。模拟账户快照不能创建真实账户绑定。

## 券商时钟同步

正式自动实盘模板要求 account_snapshot 返回带时区的 broker_time，并设置 require_broker_clock_sync=true、max_broker_clock_skew_seconds=5。系统在券商账户审计中比较本机当前时间与券商时间；字段缺失、无法解析、没有时区或绝对漂移超过阈值时，broker_clock_synchronized 失败，账户绑定与订单提交都会被阻断。

桥接模板中的 broker_time 只是协议示例。真实后端必须使用券商或柜台服务器返回时间，不能用本机时间伪装券商时间。部署机器仍应启用 Windows 时间同步并监控系统时钟。

## 自动实盘提交前恢复点

正式模板要求每次真实提交前创建恢复点。系统使用 SQLite 在线 backup API 复制账户状态，可正确包含 WAL 中已提交数据；同时固化本批订单、账户状态、券商快照、真实账户绑定、事件日志和风险审计。manifest.json 记录每个文件的 SHA-256，并使用 ASHARE_QUANT_ADAPTER_HMAC_KEY 签名。

只读验证命令为 auto-live-recovery-audit；手工创建恢复点使用 auto-live-backup。真实提交路径会在风险额度预留、submission_intent 和券商调用之前创建恢复点。创建、完整性检查或 HMAC 校验失败时，系统记录 pre_submit_recovery_failed 并立即返回，不调用券商。

恢复点默认保留最近 30 个，只会删除配置备份根目录下经过路径校验的旧恢复点。系统不会自动从旧状态恢复；恢复操作必须先停止服务、核对未决委托，再由人工按清单执行，避免把已成交订单回滚成未提交状态。

## 无人值守外部告警

正式自动实盘模板要求外部告警通道在提交前完成签名自检。告警命令使用独立 JSON 请求/结果协议，必须声明 alert 能力、返回 environment=live、绑定当前告警配置指纹，并通过固定文件 SHA-256 与 HMAC 校验。scripts/alert_bridge_template.py 仅返回 simulation，不能让正式门禁通过。

常用命令：

    .\scripts\aq.ps1 auto-live-alert-audit
    .\scripts\aq.ps1 auto-live-alert-selftest
    .\scripts\aq.ps1 auto-live-alert-test
    .\scripts\aq.ps1 auto-live-alert-retry

服务周期异常、连续失败熔断、提交前恢复失败、券商提交状态不确定和部分失败会写入 auto_live_alert_outbox.csv。发送失败保留 pending 状态并可重试；正式提交前要求发件箱无积压。告警失败不会覆盖原始交易故障，也不会自动重发订单。

## 分阶段小额投产授权

正式模板默认处于 canary 阶段：最多 1 笔，单笔、买入和总金额均不超过 2 万元。limited 默认最多 10 笔、总额不超过 20 万元；full 仍受原有独立风险限额约束。任何阶段都需要短期 HMAC 签名授权。

授权前必须完成真实账户绑定、certified_live 适配器回放、真实告警通道、自检文件固定和事件日志校验。命令：

    .\scripts\aq.ps1 auto-live-rollout-audit
    .\scripts\aq.ps1 auto-live-rollout-approve --stage canary --confirm
    .\scripts\aq.ps1 auto-live-rollout-revoke --confirm

授权绑定账户绑定 ID、脱敏账户号、当前自动实盘配置、阶段、限额和到期时间。账户切换、配置变化、文件篡改、授权到期或订单越限都会阻断。系统在总门禁检查一次，并在取得提交锁和完成独立风控后再次读取授权与订单，防止检查后替换文件或扩大批次。

从 canary 晋级 limited/full 必须修改配置、重新完成受影响认证并重新签发授权；系统不会依据回测收益或一次成功成交自动晋级。



华泰桌面只读查询会同时读取“当日委托”和“当日成交”，按合同编号聚合多笔成交，并核对累计数量、证券代码和买卖方向。只有客户端成交表明确提供“费用合计”时才作为精确费用；“发生金额”可能包含费用或现金方向，不会冒充证券成交金额；仅有佣金等分项、成交表缺行或委托/成交累计值不一致时均失败关闭。

自动实盘成交结算按券商累计回报计算增量。真实 command 适配器还必须为每笔新增成交返回 `fee_status=exact` 和可审计的 `fee_source`；仅有委托表、费用缺失或估算费用时，成交保持待核对且禁止写入账户。`auto_live_fill_progress.csv` 会逐笔记录累计成交量/金额/费用、已入账值和本次差额；重复查询生成 `already_imported`，不会重复落账。累计值回退、成交量不变但金额或费用变化、缺少稳定券商委托号时，`cumulative_fill_progress_non_regressive` 会失败关闭。每个 `auto_live:...:cumN` 事件还会在 SQLite `execution_fill_keys` 表中建立唯一键，并与现金、持仓、成交和导入批次在同一事务提交。

后交易对账还会比较每只持仓的总数量和可卖数量，并生成 `auto_live_post_trade_manifest.csv`，把本次周期、重新读取的券商快照运行号、本地账户快照、双方持仓、累计成交差额和对账审计的 SHA-256 固化在一起。缺少新券商快照或任一对账项失败时，周期不能标记为 `completed_and_synced`。清单本身的哈希还会写入 HMAC 事件链；账户或成交被重新导入时，旧清单会自动失效。
