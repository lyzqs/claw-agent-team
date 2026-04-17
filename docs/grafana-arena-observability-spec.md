# Arena 股票竞技场 Grafana 观测规格（Issue #12 PM）

## 1. 文档目标

本文定义 `Arena 股票竞技场` 接入 Grafana 前的 PM 规格基线，用于明确：

1. Arena 的核心业务流程与关键用户路径。
2. 应纳入 Grafana 的业务指标与系统/运行指标。
3. 哪些指标可直接复用现有数据文件 / API / runtime 输出，哪些需要补 exporter 或 bridge。
4. Arena 在 Grafana 中应采用的 folder、dashboard 命名与信息结构。

本文是 `Issue #12` 的直接交付物，供 `Issue #13` 实现使用。

## 2. 事实基础与当前已知线索

基于当前仓库与现有环境，已确认以下事实：

1. Arena 代码与文档位于：
   - `/root/.openclaw/workspace-inStreet/arena`

2. Arena 已具备较完整的运行结构，包括：
   - runtime 主循环：`arena/scripts/arena_runtime.py`
   - executor 执行器：`arena/scripts/arena_executor.py`
   - dashboard 服务：`arena/scripts/dashboard_server.py`
   - 运行与审计数据：
     - `arena/data/runtime.json`
     - `arena/data/events.jsonl`
     - `arena/data/ai_decisions.jsonl`
     - `arena/data/decisions.jsonl`
     - `arena/data/trade_tickets.jsonl`
     - `arena/data/order_audit.jsonl`
     - `arena/data/portfolio_snapshots.jsonl`
     - `arena/data/autopilot_state.json`

3. Arena 现有流程不是传统 Web CRUD，而是“扫描候选 -> 生成 ticket -> AI 审单 -> 执行 -> 持仓管理 -> 退出剧本 -> 回写复盘 -> 策略迭代”的投资闭环。

4. 文档中已经明确可观测对象至少包括：
   - 持仓状态
   - trade ticket 历史
   - 执行/拒绝/风控阻止记录
   - 样本回合 / 闭环复盘
   - 运行健康 / 新鲜度
   - 组合风险预算 / 仓位压力测试
   - 持仓动作 / 退出剧本
   - 交易窗口 / 提交时延 / 结算时延

5. Arena 已有本地 dashboard，但本 issue 目标不是继续维护 Arena 自有 dashboard，而是提炼出适合接入统一 Grafana 的业务与运行指标视图。

## 3. Arena 核心业务流程

结合现有文档，Arena 的业务主线可分为 6 个阶段。

### 3.1 候选扫描与市场快照

目标：定期扫描市场与股票池，形成候选集合。

关键输入：
- 市场快照
- 股票池快照
- portfolio
- trades

关键输出：
- stock universe snapshots
- market state
- 候选票初筛结果

### 3.2 策略打分与 trade ticket 生成

目标：将候选转化为可进入投资流程的 ticket。

关键对象：
- `trade_tickets.jsonl`
- score
- setupType
- entryReason
- invalidation
- sizing / starter / full 风险预算

关键业务意义：
- 这是从“候选观察”到“可执行机会”的关键跃迁。

### 3.3 AI 审单与执行闸门

目标：让 AI 或可见模式对 ticket 做审阅，并决定是否可进入执行。

关键对象：
- `ai_decisions.jsonl`
- autoReviewQueue
- execution timing policy
- trade window
- pending order limits
- market clock

关键业务意义：
- 区分“票不行”与“票可以但当前时机/风控不允许执行”。

### 3.4 真实执行与回写

目标：将可执行 ticket 转成真实订单，并追踪提交、留痕与结算状态。

关键对象：
- `/api/v1/arena/trade`
- `order_audit.jsonl`
- `trades`
- `trade-seen` / `trade-settled` / `trade-failed-sync`

关键业务意义：
- 这是策略是否真正形成样本、是否闭环的核心阶段。

### 3.5 持仓管理、退出剧本与换仓

目标：对已入场持仓进行继续持有、减仓、止盈、卖出与换仓管理。

关键对象：
- `portfolio`
- `exitPlaybooks`
- `rotation_prepare`
- `symbolsSoldToday`
- `executedPlaybookKeys`

关键业务意义：
- Arena 不只关心买入，更关心持仓后的退出与轮换管理，这是业务闭环的重要半程。

### 3.6 复盘验证与策略迭代

目标：根据后续市场数据与交易结果判断决策质量，并生成下一轮策略建议。

关键对象：
- `build_candidate_validation_record(...)`
- `build_decision_validation_summary(...)`
- `build_strategy_update_proposal(...)`
- 样本成熟度 / 毕业闸门
- 组合风险预算

关键业务意义：
- 这决定 Arena 是否只是展示系统，还是可持续迭代的投资系统。

## 4. 关键用户路径

本轮 Grafana 观测应优先支持以下用户路径：

1. **今天有没有新的可执行机会**
   - 有多少候选
   - 有多少 trade tickets
   - 有多少进入可见审单或可执行队列

2. **为什么今天没有下单**
   - 是候选不足、score 不够、时机不对、pending 太多，还是风控挡住了

3. **当前仓位是否健康**
   - 当前持仓数量
   - 浮盈 / 浮亏
   - 可卖仓位 / 锁仓股数
   - 当前退出剧本与动作建议

4. **真实执行是否顺畅**
   - 提交后多久进入 trades
   - pending 是否积压
   - 是否出现结算或回写问题

5. **最近样本是否支持策略继续推进**
   - 执行正确 / 偏早 / 错过机会 / 待验证 的样本分布
   - 最近单变量更新建议是什么

## 5. 关键健康信号

### 5.1 顶层业务健康信号

必须优先进入 Grafana 的信号：

1. 候选数量
2. trade ticket 数量
3. 可执行队列数量
4. 今日真实执行笔数
5. 当前持仓数
6. 当前组合总资产 / 总收益 / 浮动盈亏
7. 今日 pending 订单数
8. 退出剧本待执行数
9. 最近样本验证结果分布
10. 运行快照新鲜度

### 5.2 二级业务健康信号

建议纳入：

1. 不同市场状态下的候选数与放行率
2. setupType 分布
3. score 分布
4. entryReason 命中分布
5. 退出剧本类型分布
6. rotation 候选数
7. newsScore / 风险共振 / 顺势共振分布
8. 组合风险预算占用情况

## 6. 指标清单

以下按“优先级 + 指标目的 + 单位 + 标签建议”定义。

### 6.1 P0 核心业务指标

#### 1. arena_candidates_total
- 目的：观察每轮扫描后的候选规模
- 单位：count
- 标签建议：`env`, `project`, `system`, `service`, `market_state`

#### 2. arena_trade_tickets_total
- 目的：观察可进入投资流程的票数量
- 单位：count
- 标签建议：`market_state`, `setup_type`

#### 3. arena_auto_review_queue_total
- 目的：观察进入可见审单 / 自动审单队列的 ticket 数量
- 单位：count
- 标签建议：`queue`, `market_state`

#### 4. arena_executed_trades_total
- 目的：观察真实成交数量
- 单位：count
- 标签建议：`side`, `session_label`, `market_state`

#### 5. arena_pending_trades_total
- 目的：观察 pending 订单积压情况
- 单位：count
- 标签建议：`session_label`

#### 6. arena_portfolio_market_value
- 目的：观察当前组合总市值
- 单位：currency
- 标签建议：`portfolio`

#### 7. arena_portfolio_unrealized_pnl
- 目的：观察当前浮动盈亏
- 单位：currency
- 标签建议：`portfolio`

#### 8. arena_holdings_total
- 目的：观察当前持仓数量
- 单位：count
- 标签建议：`portfolio`

#### 9. arena_exit_playbooks_total
- 目的：观察待执行退出剧本规模
- 单位：count
- 标签建议：`playbook_type`

#### 10. arena_runtime_snapshot_age_seconds
- 目的：观察 runtime 快照是否过旧
- 单位：seconds
- 标签建议：`service`

### 6.2 P1 关键扩展指标

#### 11. arena_ticket_score_distribution
- 目的：观察候选评分结构
- 单位：count
- 标签建议：`score_band`, `setup_type`

#### 12. arena_ticket_blockers_total
- 目的：区分“没有下单”的原因
- 单位：count
- 标签建议：`blocker_type`
- blocker 示例：`timing_window`, `pending_limit`, `daily_order_limit`, `position_limit`, `market_closed`

#### 13. arena_order_lifecycle_latency_seconds
- 目的：观察提交 -> 留痕 -> 结算链路是否卡顿
- 单位：seconds
- 标签建议：`phase`
- phase 示例：`submit_to_seen`, `seen_to_settled`, `submit_to_settled`

#### 14. arena_validation_outcomes_total
- 目的：观察样本验证结果
- 单位：count
- 标签建议：`outcome`
- outcome 示例：`correct`, `too_early`, `missed_opportunity`, `pending_validation`

#### 15. arena_rotation_candidates_total
- 目的：观察换仓准备规模
- 单位：count
- 标签建议：`market_state`

#### 16. arena_news_score_distribution
- 目的：观察新闻影子评分分布
- 单位：count
- 标签建议：`score_band`, `hard_risk`

### 6.3 P2 运行与系统指标

#### 17. arena_runtime_loop_duration_seconds
- 目的：观察每轮 runtime 执行耗时
- 单位：seconds
- 标签建议：`stage`

#### 18. arena_runtime_events_total
- 目的：观察 runtime / executor 事件流数量
- 单位：count
- 标签建议：`event_type`, `status`

#### 19. arena_process_cpu_percent
- 目的：观察 arena 进程 CPU 压力
- 单位：percent
- 标签建议：`instance`, `service`

#### 20. arena_process_memory_bytes
- 目的：观察 arena 进程内存使用
- 单位：bytes
- 标签建议：`instance`, `service`

#### 21. arena_dashboard_http_health
- 目的：观察本地 dashboard 服务可用性
- 单位：bool / 0|1
- 标签建议：`instance`, `service`

## 7. 标签规范

遵循统一蓝图，Arena 本轮建议基础标签如下：

- `env`
- `project=agent-team-grafana`
- `system=arena`
- `service=instreet-arena`
- `instance`
- `job`
- `layer`

业务扩展标签建议：
- `market_state`
- `session_label`
- `setup_type`
- `playbook_type`
- `blocker_type`
- `validation_outcome`
- `score_band`
- `hard_risk`
- `side`

### 禁止直接作为 label 的高基数字段

以下字段禁止直接长期作为 Prometheus label：
- `ticketId`
- `playbookId`
- `symbol`（如需用 symbol，必须先限制范围或只用于低频明细，不默认上升为全量时序 label）
- 原始 `entryReason` 全文
- `request_id`
- 原始新闻摘要全文

这些字段更适合作为：
- 明细样本
- drill-down 卡片
- 审计日志 / 回放入口

## 8. 采集方式建议

### 8.1 优先级

1. **优先复用现有 runtime / jsonl / 本地 API 输出**
2. **其次补轻量 exporter / bridge**
3. **最后才考虑直接读取前端 dashboard 数据结构**

### 8.2 推荐采集路径

#### 路径 A：runtime 快照与本地 API
优先评估复用：
- `arena/data/runtime.json`
- `arena/data/portfolio_snapshots.jsonl`
- `arena/data/trade_tickets.jsonl`
- `arena/data/order_audit.jsonl`
- `arena/scripts/dashboard_server.py` 暴露的本地 API

适合先导出的指标：
- trade tickets 总量
- queue 数量
- holdings 数量
- exit playbooks 数量
- runtime snapshot age
- 组合净值 / 浮盈亏

#### 路径 B：jsonl -> Prometheus bridge
对于样本验证、事件流、订单生命周期指标，建议在 `Issue #13` 中补轻量 bridge，把 jsonl / runtime 计算结果转换为 Prometheus 指标。

适合 bridge 的指标：
- `arena_ticket_blockers_total`
- `arena_order_lifecycle_latency_seconds`
- `arena_validation_outcomes_total`
- `arena_news_score_distribution`

#### 路径 C：系统层复用进程指标
运行指标优先通过已有主机观测栈获取：
- process-exporter
- node-exporter

这样无需在 Arena 内重复造系统指标轮子。

## 9. Grafana 信息结构

### 9.1 Folder
按统一蓝图落入：

`AT | 22 项目 | Arena`

### 9.2 Dashboard 建议

#### Dashboard 1
`AT | Arena | Business | Overview`

目标：给用户一个当前策略运行与组合状态总览。

建议面板：
1. 当前组合总资产
2. 当前浮动盈亏
3. 当前持仓数
4. 今日真实执行笔数
5. 候选数 / trade tickets 数
6. pending 订单数
7. 候选与执行趋势
8. 市场状态分布

#### Dashboard 2
`AT | Arena | Runtime | Execution Flow`

目标：解释为什么能下单或为什么没下单。

建议面板：
1. auto review queue 数量
2. blocker 类型分布
3. 提交 -> 留痕 -> 结算时延
4. runtime snapshot age
5. loop duration
6. runtime / executor 事件流趋势

#### Dashboard 3
`AT | Arena | Position | Holdings & Exits`

目标：观察持仓、退出剧本与风险预算。

建议面板：
1. 当前持仓卡数量
2. 待执行退出剧本数
3. rotation 准备数量
4. 可卖仓位 / 锁仓压力
5. 风险预算占用情况
6. 持仓浮盈亏区间分布

#### Dashboard 4（可选）
`AT | Arena | Review | Validation & Iteration`

目标：观察样本验证质量与策略迭代反馈。

建议面板：
1. validation outcome 分布
2. 最近样本成熟度
3. newsScore 分布
4. 最近策略更新建议摘要

## 10. 视觉与交互要求

1. 第一行优先展示：组合总资产、浮盈亏、持仓数、今日执行数、pending 数、snapshot age
2. 第二行展示趋势图
3. 第三行展示 blocker / validation / playbook / 风险预算分布
4. 需要支持 `market_state`、`session_label`、`setup_type`、`playbook_type` 过滤
5. 风险类面板必须带阈值色语义

## 11. 对实现侧的明确输入

`Issue #13` 在实现时应以本文为输入，至少完成以下事情：

1. 复用 Arena 的 runtime / jsonl / 本地 API 数据形成 Prometheus 可抓取指标
2. 保持高基数字段在明细层，不默认上升到 Prometheus label
3. 复用主机观测栈获取 Arena 进程级系统指标
4. 在 Grafana 中建立 `AT | 22 项目 | Arena` 下的 dashboard
5. 至少让用户能在 Grafana 中回答：
   - 今天有没有值得做的票
   - 为什么当前没有执行
   - 当前持仓是否健康
   - 最近样本是否支持继续推进策略

## 12. PM 显式判断结论

本 issue 当前不需要继续拆分。

原因：
1. 当前 issue 已经是 PM 规格子 issue，本身就是独立验收单元。
2. Arena 仓库与文档已经提供了足够强的业务线索，能够形成一份可执行规格。
3. 继续拆分只会把规格说明再切碎，增加编排噪音，不增加真实价值。

因此，`Issue #12` 在本文产出后即可视为完成，并建议直接关闭；随后由 `Issue #13` 进入 Dev 实现。