# Arena 本地实现现状调研素材（Dev 结构化输出）

更新时间：2026-04-17 UTC

## 1. 结论快照

本地 Arena 已不是单点选股脚本，而是一套已经落到本机运行资产上的闭环投资系统，当前至少覆盖：

1. Arena API 拉取与市场时钟判断
2. 市场状态计算与候选打分
3. trade ticket 生成与可见队列
4. Eastmoney 新闻上下文 + Gateway AI 影子/主决策
5. 真实买卖执行、持仓监控、退出剧本、换仓计划
6. 本地 Dashboard、运行日志、历史快照、回放数据
7. Agent Team 仓库中的 Grafana exporter / dashboard / 校验脚本

这意味着“研究 -> 决策 -> 执行 -> 持仓 -> 退出 -> 复盘 -> 策略建议”的主链路在代码层已经基本成型，不是只有文档规格。

同时，本地实现还存在几个需要后续文档明确写出的风险点：

- `visible-review` 名称与实际执行语义已经出现漂移，代码里仍可能在交易时段触发 Gateway AI 自动决策并执行，不应简单理解为“纯手动模式”。
- `execute_rotation()` 路径存在明确代码缺陷，卖出后组装 plan 时先使用了尚未赋值的 `response_data`，换仓路径不是稳态可用。
- 运行强依赖外部环境：Arena API 凭据、本地 Gateway、Eastmoney 资讯抓取都可影响闭环质量。

## 2. 调研范围与证据基础

本次调研分两层看：

### 2.1 Arena 核心运行资产
主实现实际位于：`/root/.openclaw/workspace-inStreet/arena`

已核对的核心文件：
- `README.md`
- `docs/full-process-guide.md`
- `docs/system-design.md`
- `scripts/arena_runtime.py`
- `scripts/arena_executor.py`
- `scripts/dashboard_server.py`
- `scripts/control.sh`
- `config/strategy.json`
- `systemd/instreet-arena-loop.service`
- `systemd/instreet-arena-dashboard.service`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `data/*.json / *.jsonl`

### 2.2 Agent Team 仓库内与 Arena 相关的集成资产
位于：`/root/.openclaw/workspace-agent-team`

已核对：
- `scripts/arena_metrics_exporter.py`
- `scripts/generate_arena_grafana_dashboards.py`
- `scripts/validate_arena_observability.py`
- `docs/grafana-arena-observability-spec.md`
- `docs/grafana-arena-delivery.md`

结论：Agent Team 仓库内并不承载 Arena 交易主逻辑，而是承载 Arena 的观测接入、Grafana 交付和校验资产；真实业务闭环代码在 `workspace-inStreet/arena`。

## 3. 当前实现分层

### 3.1 L0 交易/研究主循环
主文件：`arena/scripts/arena_runtime.py`

职责：
- 拉取 `/api/v1/home`
- 拉取 `/api/v1/arena/portfolio`
- 拉取 `/api/v1/arena/trades?limit=50`
- 拉取 `/api/v1/arena/snapshots?days=30`
- 拉取 `/api/v1/arena/stocks?limit=300`
- 判断交易日、交易窗口、下一轮 sleep
- 计算市场状态
- 候选打分
- 生成 trade ticket / auto review queue / watchtower / fast-lane 实验 / 持仓监控 / 退出剧本
- 请求 Gateway 新闻影子分析与主 AI 决策
- 写回 `runtime.json` 和多类 jsonl 历史日志

### 3.2 L1 执行器
主文件：`arena/scripts/arena_executor.py`

职责：
- `review-next`
- `execute-next`
- `reject-next`
- `execute-exit-next`
- `execute-playbook`
- `execute-rotation-next`

执行前会二次做 live 校验：
- 当前是否在允许提交窗口
- 当前 pending 数量
- 是否已持有同标的
- 是否今日已下过单
- 卖出时是否还有可卖仓位

### 3.3 L2 本地 Dashboard 服务
主文件：`arena/scripts/dashboard_server.py`

职责：
- 提供静态页面
- 提供 `/api/runtime`、`/api/summary`、`/api/events`、`/api/market-history`、`/api/portfolio-history`、`/api/decisions`、`/api/ai-decisions`、`/api/run-history`、`/api/trade-tickets`、`/api/order-audit`、`/api/symbol-catalog`、`/api/symbol-detail`、`/api/holding-changes`、`/api/review-requests`、`/api/daily-brief`、`/api/experiments`、`/health`
- 通过 `/api/runtime-control` 控制 `instreet-arena-loop.service` 的 start/stop/restart

### 3.4 L3 本地 Web 前端
主文件：`arena/web/index.html` + `arena/web/app.js`

当前前端已不是单页概览，而是多模块工作台，覆盖：
- 组合总览
- 市场状态
- 盘前简报
- 流程状态 / runtime 控制
- 新闻 / 资讯上下文
- 策略迭代看板
- 执行闸门 / AI 决策
- 持仓状态
- 预警中心 / 持仓监控
- 持仓动作 / 退出剧本
- 卖出 / 换仓流水
- 持仓 / 委托生命周期
- 风险预算 / 压力测试
- 判断验证 / 策略建议
- 样本回合 / 闭环复盘
- 闭环健康度 / Pending 监控
- 执行时机 / 提交窗口建议
- 流程 / 迭代驾驶舱
- 候选池 / 建议动作 / 观察池 / 规则实验室
- 个股详情 / 交易回放
- Fast-Lane 实验 / 样本成熟度 / Batch Review / A/B 策略对比
- 投资决策时间线 / 当天卡片时间轴 / 交易卡片 / 单笔投资链路
- 净值走势 / 市场广度走势
- 运行剖面 / 运行回放 / 运行历史 / 执行审计 / 事件日志

### 3.5 L4 观测接入（Agent Team 仓库）
主文件：
- `scripts/arena_metrics_exporter.py`
- `scripts/generate_arena_grafana_dashboards.py`
- `scripts/validate_arena_observability.py`

职责：
- 读取 Arena 本地数据文件与 dashboard 健康状态
- 暴露 `arena_*` Prometheus 指标
- 生成 4 份 Arena Grafana dashboards
- 校验 exporter / Prometheus target / Grafana dashboards 是否加载

## 4. 闭环投资流程与代码落点

| 阶段 | 当前实现情况 | 主要代码/资产 | 主要输出 |
| --- | --- | --- | --- |
| 数据抓取 | 已实现 | `arena_runtime.py::run_once` | `home/portfolio/trades/snapshots/stocks` 原始数据 |
| 交易时钟与窗口 | 已实现 | `infer_market_clock`, `assess_execution_timing_policy` | `market.clock`, `executionTimingPolicy` |
| 市场状态 | 已实现 | `compute_market_state` | 冰点/分歧/修复/亢奋 |
| 候选打分 | 已实现 | `score_candidates` | `candidates` |
| trade ticket | 已实现 | `build_trade_tickets` | `tradeTickets` |
| 自动审单队列 | 已实现 | `build_auto_review_queue`, profile matrix | `autoReviewQueue` |
| 新闻上下文 | 已实现 | Eastmoney 抓取 + `request_gateway_news_analysis` + fallback | `newsContext` |
| AI 主决策 | 已实现 | `request_gateway_ai_decision`, `execute_ai_decision` | `ai_decisions.jsonl`, execute/reject/watch |
| 持仓监控 | 已实现 | `build_holding_monitors` | `holdingMonitors`, `alerts` |
| 退出剧本 | 已实现 | `build_exit_playbooks` | `exitPlaybooks` |
| 真实执行 | 已实现 | `arena_executor.py`, `/api/v1/arena/trade` | `order_audit.jsonl`, trade records |
| 换仓计划 | 部分实现 | `rotation_prepare`, `pendingRotationPlans`, `execute_rotation` | rotation plan / sell leg |
| 决策验证 | 已实现 | `build_decision_validation_summary` | `decisionValidation` |
| 单变量策略建议 | 已实现 | `build_strategy_update_proposal` | `strategyUpdateProposal` |
| 本地 Dashboard | 已实现 | `dashboard_server.py`, `web/*` | 本地可视化工作台 |
| Grafana 对接 | 已实现（Agent Team 仓库） | `arena_metrics_exporter.py`, `generate_arena_grafana_dashboards.py` | `arena_*` 指标与 4 个 dashboards |

## 5. 关键脚本、服务、页面、配置

### 5.1 控制脚本
`arena/scripts/control.sh`

暴露命令：
- `sync`
- `loop`
- `serve`
- `review`
- `execute-next`
- `execute-exit-next`
- `execute-rotation-next`
- `stack`
- `health`

这说明 Arena 既支持 systemd 常驻，也支持命令行人工介入。

### 5.2 systemd 服务
- `instreet-arena-loop.service`
- `instreet-arena-dashboard.service`

服务文件显示：
- 工作目录：`/root/.openclaw/workspace-inStreet`
- loop 入口：`arena_runtime.py loop --interval 300`
- dashboard 入口：`dashboard_server.py --host 0.0.0.0 --port 8788`

### 5.3 主配置
`arena/config/strategy.json`

本次实测关键配置：
- `strategyVersion = Arena-V1.11`
- `boardVersion = Board-V3.10`
- `executionMode = visible-review`
- `loopIntervalSeconds = 300`
- `dashboardPort = 8788`
- `autopilot.enabled = false`
- `aiDecision.enabled = true`
- `aiDecision.autoExecute = true`
- `aiDecision.shadowEnabled = true`
- `newsContext.aiEnabled = true`
- `executionTimingPolicy.enabled = true`
- `decisionValidation.enabled = true`

### 5.4 主要页面/API
后端 API 已覆盖轻量数据接口 + 重数据按需加载，前端 `app.js` 通过 `/api/runtime` 和 `/api/summary` 先拉轻量数据，再按需拉：
- `events`
- `market-history`
- `portfolio-history`
- `decisions`
- `ai-decisions`
- `run-history`
- `trade-tickets`
- `order-audit`
- `review-requests`
- `symbol-catalog`

说明当前前端已经是“运行台 + 回放台”，不是只读静态看板。

## 6. 关键数据流 / 文件落点

### 6.1 最新快照
- `arena/data/runtime.json`

### 6.2 过程与历史
- `arena/data/events.jsonl`
- `arena/data/logs/runs.jsonl`
- `arena/data/market_snapshots.jsonl`
- `arena/data/stock_universe_snapshots.jsonl`
- `arena/data/portfolio_snapshots.jsonl`
- `arena/data/decisions.jsonl`
- `arena/data/trade_tickets.jsonl`
- `arena/data/agent_review_requests.jsonl`
- `arena/data/order_audit.jsonl`
- `arena/data/ai_decisions.jsonl`

### 6.3 状态辅助文件
- `arena/data/autopilot_state.json`
- `arena/data/daily_brief.json`
- `arena/data/experiments.json`
- `arena/data/position_tracking.json`

### 6.4 当前数据流方向

1. Arena API -> `run_once()`
2. `run_once()` -> 生成 `market/candidate/ticket/news/holding/exit/decision` 结构
3. 结果写入 `runtime.json` + 多类 jsonl
4. `dashboard_server.py` 从这些文件读取并对外提供 API
5. `web/app.js` 消费这些 API 做多模块展示
6. Agent Team 仓库中的 `arena_metrics_exporter.py` 再读取这些文件，把 Arena 状态桥接到 Prometheus / Grafana

## 7. 当前运行态观察（本次显式核查）

基于本机可见资产，本次核查到的运行事实如下：

### 7.1 服务状态
- `instreet-arena-loop.service`：active
- `instreet-arena-dashboard.service`：active
- `http://127.0.0.1:8788/health`：返回 `{\"ok\": true}`
- `/api/runtime-control` 返回 loop 服务已安装、已启用、正在运行

### 7.2 最新 runtime 快照
来自 `arena/data/runtime.json` / `/api/runtime`：
- `generatedAt`: `2026-04-17T07:06:56.671849Z`
- `strategyVersion`: `Arena-V1.11`
- `boardVersion`: `Board-V3.10`
- `executionMode`: `visible-review`
- `marketState`: `分歧`
- `sessionLabel`: `收盘后一小时`
- `executionTimingPolicy.canSubmitNow = false`
- 当前候选数：10
- 当前 trade tickets：3
- 当前 auto review queue：3
- 当前持仓数：16
- 当前 exit playbooks：17
- 当前 alerts：11
- 当前 planned actions：2

### 7.3 本轮 process phases
已落盘阶段：
- `fetch`
- `market-clock`
- `daily-brief`
- `market-state`
- `scoring`
- `news`
- `alerts`
- `exit-playbooks`
- `actions`

说明主循环不是黑盒，阶段耗时和阶段状态都可回放。

### 7.4 当前审单/执行态
来自 `review` 区：
- trade summary: `35` 笔历史订单，其中 `34` executed，`1` failed，`0` pending
- AI 主决策本轮跳过，原因是当前窗口只允许观察，不允许真实提交
- exit AI 决策本轮跳过，原因是当前没有可执行卖出/换仓剧本

### 7.5 当前 `review-next` 实测
调用：`python3 arena/scripts/arena_executor.py review-next --runtime-file arena/data/runtime.json`

结果表明：
- 能读取下一张待看的 ticket
- 当前返回的是 `万达电影` ticket
- 该 ticket 已带上 `featureSnapshot`、`entryReason`、`invalidation`、`executionPlan`、`newsContext`、`agentBlockers`、`aiProfile`
- 当前 blocker 是：`当前处于收盘后一小时，只允许观察 / 回写跟踪，不允许真实提交。`

这说明人工 review 接口可用，且 ticket 信息已较完整。

## 8. 已实现 / 未实现 / 存疑或风险

## 8.1 已实现

### A. 主闭环已实现
- 数据抓取、市场状态、候选打分、ticket 化、AI 决策、真实下单、持仓监控、退出剧本、验证建议都已有代码落点。

### B. 本地运行资产已实现
- loop 服务、dashboard 服务、本地前端、dashboard API、json/jsonl 历史数据全部存在且处于可读状态。

### C. 研究与执行不是脱节的
- 不是“只会生成报告不执行”，也不是“只会下单不留痕”，而是两者同时存在。

### D. 复盘与策略建议已落地
- `decisionValidation` 与 `strategyUpdateProposal` 已在 runtime 中真实生成，不是空壳字段。

### E. Grafana 接入资产已实现
- Agent Team 仓库中已有 exporter、dashboard 生成器、校验脚本、交付文档。

## 8.2 未实现或仅部分实现

### A. 全自动无人值守并未完全收口为“纯自动模式”
- 当前配置 `executionMode=visible-review`，并非独立的纯自动生产模式命名。
- `autopilot.enabled=false`，因此旧 autopilot/event 路径并未作为主工作流启用。

### B. 换仓能力不是稳态成熟功能
- `rotation_prepare`、`pendingRotationPlans`、`execute_rotation` 已有代码，但从文档表述和代码质量看，换仓仍更像阶段性能力，不如 `full_exit / partial_exit` 成熟。

### C. 新闻与 AI 依赖外部服务
- 新闻抓取依赖 Eastmoney 页面结构。
- 主决策依赖 OpenClaw Gateway Chat Completions。
- 虽有 fallback，但 fallback 更多是保底而非等效替代。

## 8.3 存疑 / 风险点（建议后续文档明确写出）

### 风险 1：`visible-review` 与真实执行语义存在漂移
事实依据：
- `aiDecision.enabled=true`
- `aiDecision.autoExecute=true`
- `maybe_run_gateway_ai_decision()` 只检查 `canSubmitNow` 与 `eligible`，并不会因为 `executionMode=visible-review` 或 `autopilot.enabled=false` 而直接停掉 AI 自动执行链。
- `evaluate_ticket_review_gate()` 只有在 `executionMode == agent-auto-review` 时才检查 `autopilot.enabled`。

含义：
- 当前代码语义更接近“可见队列 + AI 自动裁决/可执行”，而不是严格的“人工 review 后才执行”。
- 后续写飞书文档时，不能仅沿用旧的“visible-review = 手动模式”说法。

### 风险 2：换仓执行路径有明确代码缺陷
事实依据：`arena/scripts/arena_executor.py` 的 `execute_rotation()` 在构造 `plan` 时先引用了 `response_data.get(...)`，但 `response_data` 直到更后面才赋值。

含义：
- 一旦真正走到 `execute_rotation`，该路径大概率会在卖出请求成功后因局部变量未定义而抛错。
- 这应被视为明确技术缺口，而不是仅“待观察”。

### 风险 3：当前运行高度依赖外部环境与本地凭据
- Arena API 凭据来自 `.openclaw/instreet-forum.json`
- AI 需要可用 Gateway 配置
- 新闻上下文需要 Eastmoney 页面可抓取

含义：
- 本地代码完整不代表随时可稳定复跑。
- 交付文档应把这些依赖写成前置条件，而不是默认总可用。

### 风险 4：运行快照与服务存活并非同义
当前观测到：
- loop 服务 active
- 但 `runtime.json` 停留在最近一次完整扫描时间
- `events.jsonl` 在更晚时间写入了 `loop-sleep` 事件

含义：
- 后续文档需说明 Arena 在非运行窗口会进入长睡眠，不能用“runtime 快照时间旧”直接判断服务挂掉。

## 9. 可直接给 PM / 文档整理层复用的摘要素材

可直接复用的文案骨架如下：

### 9.1 Arena 当前本地实现范围
Arena 本地已具备从市场扫描、候选打分、trade ticket、AI 决策、真实执行、持仓监控、退出剧本，到复盘验证和策略建议的完整实现骨架。核心业务逻辑位于 `/root/.openclaw/workspace-inStreet/arena`，而 `/root/.openclaw/workspace-agent-team` 中补充了 Arena 的 Grafana exporter、dashboard 生成与校验资产。

### 9.2 Arena 主要模块
- Runtime：`arena/scripts/arena_runtime.py`
- Executor：`arena/scripts/arena_executor.py`
- Dashboard Server：`arena/scripts/dashboard_server.py`
- Web UI：`arena/web/index.html` + `arena/web/app.js`
- Control Script：`arena/scripts/control.sh`
- Systemd：`instreet-arena-loop.service`、`instreet-arena-dashboard.service`
- Config：`arena/config/strategy.json`
- Observability：`workspace-agent-team/scripts/arena_metrics_exporter.py` 等

### 9.3 Arena 关键数据文件
- `runtime.json`
- `events.jsonl`
- `market_snapshots.jsonl`
- `stock_universe_snapshots.jsonl`
- `portfolio_snapshots.jsonl`
- `decisions.jsonl`
- `trade_tickets.jsonl`
- `order_audit.jsonl`
- `ai_decisions.jsonl`
- `autopilot_state.json`
- `daily_brief.json`
- `experiments.json`
- `position_tracking.json`

### 9.4 Arena 当前实测状态
截至本次核查，本机 loop 与 dashboard 服务都处于 active，dashboard 健康接口可访问，最新 runtime 快照版本为 `Arena-V1.11 / Board-V3.10`。当前市场状态为“分歧”，处于“收盘后一小时”的 observe-only 窗口，因此本轮 AI 决策跳过真实提交，但候选、ticket、持仓监控、退出剧本和历史回放数据都已正常存在。

### 9.5 应明确写出的当前缺口
- `visible-review` 与自动执行语义存在漂移，不能简单等同于纯人工模式。
- 换仓执行路径存在代码缺陷，需要后续修复。
- 运行依赖 Arena API / Gateway / Eastmoney 等外部条件。
- 非运行窗口会长睡眠，快照时间旧不等于服务故障。

## 10. Dev 显式判断

本 issue 的 Dev 侧验收已满足：

1. 已基于本地仓库与可见运行资产梳理 Arena 当前实现范围，覆盖了核心流程、主要模块/页面/脚本、关键数据流与服务依赖。
2. 已输出一份可直接被后续飞书文档整理引用的结构化素材。
3. 已明确区分已实现、未实现/部分实现、以及存疑/风险点，没有停留在泛泛描述。

建议后续流转：`pm`

原因：当前最合理的下一步不是再让 Dev 扩展调研，而是由 PM/文档整理角色消费本文件，继续完成飞书文档成稿或推动已阻塞的文档发布子任务。
