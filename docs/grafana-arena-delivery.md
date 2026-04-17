# Arena 股票竞技场指标接入与 Grafana 面板落地（Issue #13 Dev 交付）

## 交付目标
基于 `docs/grafana-arena-observability-spec.md` 与统一蓝图，把 Arena 的业务流程、持仓状态、执行链路与运行健康接入本地 Prometheus / Grafana，并提供最小验证路径。

## 本轮交付物

### 1. Arena 指标桥接 exporter
- `scripts/arena_metrics_exporter.py`
- `deploy/grafana/systemd/arena-metrics-exporter.service`

实现方式：
- 直接读取 `/root/.openclaw/workspace-inStreet/arena/data/runtime.json`
- 聚合 `data/logs/runs.jsonl`、`data/order_audit.jsonl`、`data/trade_tickets.jsonl`、`data/ai_decisions.jsonl`
- 复用本地 Arena dashboard 健康检查 `http://127.0.0.1:8788/health`
- 产出 Prometheus `/metrics`，覆盖：
  - 候选数 / trade tickets / auto review queue / executed / pending
  - 组合总资产 / 浮动盈亏 / 持仓数 / exit playbooks / rotation candidates
  - snapshot age / blocker 分布 / order lifecycle latency / validation outcome / news score 分布
  - runtime phase 耗时 / runtime events / Arena runtime 与 dashboard 进程 CPU / 内存 / dashboard http 健康

### 2. Prometheus 抓取接入
- 更新 `deploy/grafana/prometheus/prometheus.yml`
- 新增 `job_name: arena-exporter`
- 抓取地址：`127.0.0.1:19150`

### 3. Arena dashboards
- `deploy/grafana/dashboards/arena-business-overview.json`
- `deploy/grafana/dashboards/arena-runtime-execution-flow.json`
- `deploy/grafana/dashboards/arena-position-holdings-exits.json`
- `deploy/grafana/dashboards/arena-review-validation-iteration.json`
- 由 `scripts/generate_arena_grafana_dashboards.py` 生成

命名符合蓝图：
- `AT | Arena | Business | Overview`
- `AT | Arena | Runtime | Execution Flow`
- `AT | Arena | Position | Holdings & Exits`
- `AT | Arena | Review | Validation & Iteration`

### 4. Dashboard provisioning / 安装脚本更新
- `deploy/grafana/provisioning/dashboards/dashboard-provider.yaml`
  - 新增 folder `AT | 22 项目 | Arena`
- `deploy/grafana/install_local_grafana_stack.sh`
  - 安装 `arena-metrics-exporter.service`
  - 同步 Arena dashboards 到 `/var/lib/grafana/dashboards/agent-team-grafana/arena`
  - 健康检查新增 `http://127.0.0.1:19150/metrics`

### 5. 验证脚本
- `scripts/validate_grafana_bundle.py`
- `scripts/validate_arena_observability.py`

用途：
- 静态校验 Grafana bundle、Arena dashboards、systemd unit 与 Prometheus scrape job
- 校验 exporter 是否暴露预期 `arena_*` 指标
- 校验 Grafana 搜索结果里能发现 Arena dashboards

## 覆盖到的规格指标
本轮已覆盖或桥接以下核心指标：
- `arena_candidates_total`
- `arena_trade_tickets_total`
- `arena_auto_review_queue_total`
- `arena_executed_trades_total`
- `arena_pending_trades_total`
- `arena_portfolio_market_value`
- `arena_portfolio_unrealized_pnl`
- `arena_holdings_total`
- `arena_exit_playbooks_total`
- `arena_runtime_snapshot_age_seconds`
- `arena_ticket_score_distribution`
- `arena_ticket_blockers_total`
- `arena_order_lifecycle_latency_seconds`
- `arena_validation_outcomes_total`
- `arena_rotation_candidates_total`
- `arena_news_score_distribution`
- `arena_runtime_loop_duration_seconds`
- `arena_runtime_events_total`
- `arena_process_cpu_percent`
- `arena_process_memory_bytes`
- `arena_dashboard_http_health`

## 安装 / 更新方式
如果本机已有 Grafana 主栈，重新执行安装脚本即可下发最新配置：

```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/generate_arena_grafana_dashboards.py
sudo ./deploy/grafana/install_local_grafana_stack.sh \
  --public-host <your-public-host> \
  --grafana-http-port 3300 \
  --grafana-admin-password '<grafana-admin-password>'
```

脚本会：
- 安装并启动 `arena-metrics-exporter.service`
- 更新 Prometheus 抓取配置
- 同步 Arena dashboards 到 Grafana provisioning 目录
- 触发健康检查

## 最小验证

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 运行态校验
```bash
python3 scripts/validate_arena_observability.py
```

### 3. 手工检查建议
- 打开 Grafana
- 确认 folder `AT | 22 项目 | Arena` 已存在
- 确认 4 个 Arena dashboards 可见
- 确认面板中出现：
  - 组合总资产 / 浮动盈亏 / 持仓数 / 执行数 / pending
  - auto review queue / blocker 分布 / 提交到结算链路时延
  - exit playbooks / validation outcomes / news score 分布
  - Arena runtime 与 dashboard 健康指标

## 角色边界判断
本轮 Dev 已完成：
- Arena 指标桥接实现
- Prometheus 接入
- Grafana dashboards 定义
- 最小验证脚本

后续建议：
- 由 QA 基于实际页面、指标含义和运行态数据做验收
- 如需在目标主机重新安装或调整公网暴露策略，再由 Ops 配合执行
