# OpenClaw 指标接入与 Grafana 面板落地（Issue #27 Dev 交付）

## 交付目标
基于 `docs/grafana-openclaw-observability-spec.md` 与统一蓝图，把 OpenClaw 的 runtime / usage / queue-session 相关指标安全接入本地 Prometheus / Grafana，并补齐最小复现与验证路径。

## 本轮交付物

### 1. OpenClaw OTLP -> Prometheus 轻量 bridge
- `scripts/openclaw_otel_bridge.js`
- `deploy/grafana/systemd/openclaw-otel-bridge.service`

实现方式：
- 监听本地 `127.0.0.1:19160`
- 接收 OpenClaw `diagnostics-otel` 发出的 `OTLP/HTTP (http/protobuf)` metrics
- 将 OTel 指标规范化为 Prometheus `/metrics`
- 内置 bridge 自监控指标：
  - `openclaw_otel_bridge_requests_total`
  - `openclaw_otel_bridge_decode_errors_total`
  - `openclaw_otel_bridge_last_export_timestamp_seconds`
  - `openclaw_otel_bridge_points_total`
- 对 OpenClaw 原始属性做低风险标签映射，保留：
  - `channel / provider / model / outcome / source / lane / state / reason / attempt / webhook / context / token_type`
- 同时支持消费 `OTLP/HTTP` traces，在 bridge 内基于 span 上的 `openclaw.sessionKey` 聚合出 agent 维度指标：
  - `openclaw_agent_message_processed_total`
  - `openclaw_agent_tokens_total`
- `agent` 标签来源于 `sessionKey` 的 `agent:<agentId>:...` 规范前缀解析，避免要求 Grafana 侧直接理解 sessionKey 原文
- 明确过滤高基数标签，避免把 `sessionKey / sessionId / chatId / messageId / error / traceId / spanId` 直接打进 Prometheus

### 2. OpenClaw 配置批量写入模板
- `deploy/grafana/openclaw-otel-config.batch.json`

用途：
- 给 Ops / QA 提供一份可直接 dry-run 或正式写入的 OpenClaw 配置 batch
- 覆盖首轮最小必要配置：
  - `plugins.entries.diagnostics-otel.enabled = true`
  - `diagnostics.enabled = true`
  - `diagnostics.otel.enabled = true`
  - `diagnostics.otel.endpoint = "http://127.0.0.1:19160"`
  - `diagnostics.otel.protocol = "http/protobuf"`
  - `diagnostics.otel.serviceName = "openclaw-gateway"`
  - `diagnostics.otel.metrics = true`
  - `diagnostics.otel.traces = true`
  - `diagnostics.otel.logs = false`
  - `diagnostics.otel.flushIntervalMs = 60000`

推荐写入前先执行：

```bash
openclaw config set --batch-file ./deploy/grafana/openclaw-otel-config.batch.json --dry-run
openclaw config validate --json
```

### 3. Prometheus 抓取接入
- 更新 `deploy/grafana/prometheus/prometheus.yml`
- 新增 / 保持：
  - `job_name: openclaw-otel-bridge`
  - 抓取地址 `127.0.0.1:19160`
- 同时补回 `uptime-kuma-exporter` 抓取项，避免 OpenClaw 接入过程中误伤现有 bundle

### 4. OpenClaw 进程识别增强
- 更新 `deploy/grafana/process-exporter/process-exporter.yml`

新增专门规则：
- `openclaw-gateway`
- `openclaw`

目的：
- 避免 OpenClaw 进程只落在通用 `node` 聚合下
- 让 Grafana 中可直接查看 OpenClaw 进程 CPU / 内存

### 5. OpenClaw dashboards
- `deploy/grafana/dashboards/openclaw-runtime-overview.json`
- `deploy/grafana/dashboards/openclaw-usage-model-message-flow.json`
- `deploy/grafana/dashboards/openclaw-queue-sessions-channels.json`
- 由 `scripts/generate_openclaw_grafana_dashboards.py` 生成

命名符合 PM 规格：
- `AT | OpenClaw | Runtime | Overview`
- `AT | OpenClaw | Usage | Model & Message Flow`
- `AT | OpenClaw | Queue | Sessions & Channels`

覆盖内容包括：
- token / cost / run duration
- message queued / processed / duration
- 新增按 `agent` 维度查看消息量变化
- 新增按 `agent` 维度查看 token 变化
- queue enqueue / dequeue / depth / wait
- session state / stuck / stuck age
- webhook received / error / duration
- provider / model / channel / lane 过滤
- OpenClaw bridge 接收量
- OpenClaw 进程 CPU / 内存

### 6. Grafana provisioning / 安装脚本更新
- `deploy/grafana/provisioning/dashboards/dashboard-provider.yaml`
  - 新增 folder `AT | 11 平台 | OpenClaw`
  - 同时保留 `AT | 30 运维 | Uptime Kuma`，避免破坏已有目录
- `deploy/grafana/install_local_grafana_stack.sh`
  - 安装 `openclaw-otel-bridge.service`
  - 下发 OpenClaw dashboards 到 `/var/lib/grafana/dashboards/agent-team-grafana/openclaw`
  - 健康检查新增 `http://127.0.0.1:19160/metrics`
  - 同时补回 uptime-kuma dashboard / service / health check 下发，避免回归

### 7. 验证脚本
- 更新 `scripts/validate_grafana_bundle.py`
- 新增 `scripts/validate_openclaw_observability.py`

用途：
- 静态校验 Grafana bundle、OpenClaw dashboards、systemd unit、Prometheus scrape job、provider folder
- 校验 bridge `/metrics` 是否暴露预期 `openclaw_*` 指标
- 校验 Prometheus activeTargets 中 `openclaw-otel-bridge` 是否健康
- 校验 Grafana 搜索结果是否能发现 3 个 OpenClaw dashboards 且 folder 正确
- 校验当前 `openclaw.json` 是否已经具备推荐的 diagnostics-otel 配置，并在未写入时给出明确提示

## 覆盖到的规格指标
本轮桥接与面板已覆盖或接入以下核心指标：
- `openclaw_tokens_total`
- `openclaw_cost_usd_total`
- `openclaw_agent_message_processed_total`
- `openclaw_agent_tokens_total`
- `openclaw_run_duration_ms_bucket`
- `openclaw_context_tokens_bucket`
- `openclaw_message_queued_total`
- `openclaw_message_processed_total`
- `openclaw_message_duration_ms_bucket`
- `openclaw_queue_lane_enqueue_total`
- `openclaw_queue_lane_dequeue_total`
- `openclaw_queue_depth_bucket`
- `openclaw_queue_wait_ms_bucket`
- `openclaw_session_state_total`
- `openclaw_session_stuck_total`
- `openclaw_session_stuck_age_ms_bucket`
- `openclaw_run_attempt_total`
- `openclaw_webhook_received_total`
- `openclaw_webhook_error_total`
- `openclaw_webhook_duration_ms_bucket`
- `openclaw_otel_bridge_requests_total`
- `openclaw_otel_bridge_points_total`
- 以及 OpenClaw 进程 CPU / 内存（通过 `process-exporter`）

## 安装 / 更新方式
如果本机已有 Grafana 主栈，重新执行以下步骤即可：

```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/generate_openclaw_grafana_dashboards.py
openclaw config set --batch-file ./deploy/grafana/openclaw-otel-config.batch.json --dry-run
openclaw config validate --json
sudo ./deploy/grafana/install_local_grafana_stack.sh \
  --public-host <your-public-host> \
  --grafana-http-port 3300 \
  --grafana-admin-password '<grafana-admin-password>'
```

如果 dry-run 正常，再正式写入 OpenClaw 配置：

```bash
openclaw config set --batch-file ./deploy/grafana/openclaw-otel-config.batch.json
openclaw config validate --json
```

由于 agent 维度聚合依赖 `/v1/traces` 中的 `openclaw.sessionKey`，本批配置默认会启用 `diagnostics.otel.traces=true`。如果环境侧继续保留 `traces=false`，则 `openclaw_agent_message_processed_total` 与 `openclaw_agent_tokens_total` 无法稳定产出，Grafana 中的 agent 维度趋势面板会持续无数据。

之后按环境策略重载 / 重启 OpenClaw，并检查 bridge / Prometheus / Grafana 是否恢复正常。

## 最小验证

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 运行态校验
```bash
python3 scripts/validate_openclaw_observability.py
```

### 3. 本轮已完成的显式验证
本轮 Dev 已做的最小显式判断与验证：
- 确认 `openclaw config validate --json` 对当前现网配置返回 `valid: true`
- 确认当前 `/root/.openclaw/openclaw.json` 尚未存在 `diagnostics` 子树，也未启用 `diagnostics-otel`
- 因此补交付了可复用的 `openclaw-otel-config.batch.json`，并在验证脚本里把“推荐配置是否已落地”显式输出
- 静态层面补齐并校正了：
  - OpenClaw bridge systemd unit
  - Prometheus scrape job
  - Grafana provider folder
  - dashboard 生成产物
  - 验证脚本
  - 安装脚本中被 OpenClaw 改动误伤的 uptime-kuma 安装路径
- Issue #34 follow-up 进一步补齐：
  - bridge 对 OTLP traces 的 `/v1/traces` 接收与 agent 聚合导出
  - `openclaw_agent_message_processed_total` / `openclaw_agent_tokens_total` 两个 agent 维度聚合指标
  - OpenClaw Usage 看板中的 `按 Agent 看消息量变化` / `按 Agent 看 Token 变化` 两块核心趋势面板

### 4. 手工检查建议
- 打开 Grafana
- 确认 folder `AT | 11 平台 | OpenClaw` 已存在
- 确认 3 个 OpenClaw dashboards 可见
- 确认面板中出现：
  - token / cost / run duration
  - message processed / queue wait / queue depth / stuck session
  - 按 Agent 看消息量变化 / 按 Agent 看 Token 变化
  - provider-model 分布 / webhook / lane / outcome 分布
  - OpenClaw 进程 CPU / 内存
- 确认 `validate_openclaw_observability.py` 中：
  - `openclaw-otel-bridge` target 为 `up`
  - `recommended_config_present` 全部为 `true`
  - `diagnostics.otel.traces` 明确为 `true`

## 本轮补充增强（Issue #33）

围绕第二轮“跨看板共性整改”的治理目标，本轮额外对多套 dashboard 做了统一的人类可读性修复，重点不是新增项目级业务指标，而是处理共性的难读表达与潜在误导面板：

### 1. OpenClaw 共性整改
- `Queue Enqueue 速率` / `Queue Dequeue 速率` 标题统一中文化为 `入队速率` / `出队速率`
- 将 `Queue Enqueue / Dequeue 趋势` 统一改名为 `入队 / 出队趋势`
- 将普通用户难以理解的 `Queue Depth 按 Lane` / `Queue Wait 按 Lane` 从 bargauge 快照改为两块更直观的 timeseries：
  - `各处理通道队列积压趋势`
  - `各处理通道等待时间趋势`
- 将 `消息结果分布` 改为更适合趋势阅读的 `按结果看消息量变化`
- 将 `Webhook 接收量分布` 改为 `Webhook 接收总量趋势`，并对 query 增加 `or vector(0)`，避免在低事件环境里继续制造误导性空态
- 将 `Webhook 错误数（24h）` stat 改为 `... or vector(0)`，把“当前无错误事件”明确展示为 0，而不是空
- 将 `Provider / Model Token 分布` / `Provider / Model 成本分布` 标题统一调整为 `当前 Token 最高的 Provider / Model` / `当前成本最高的 Provider / Model`，弱化实现字段口吻，保留其作为补充排行视图的定位

### 2. Agent Team 共性整改
- `完成模式分布` 从主视图分布面板改为 `完成模式变化` timeseries
- `人工处理结果分布` 从 bargauge 改为 `人工处理结果变化` timeseries
- `恢复 / 协调事件` 启用 table 图例 + multi tooltip，强化时间点横向比较可读性
- 保留 `失败码分布` 作为次级排障视图，不再让 completion_mode / resolution 继续以首页快照分布的形式抢占主视图注意力

这些改动与 #34 / #35 / #36 的项目级增强互补，完成了 PM 在第二轮清单里明确要求的“共性治理”部分。

- OpenClaw metrics bridge 实现
- Prometheus 接入
- Grafana dashboards 定义
- OpenClaw 配置 batch 模板
- 静态 / 运行态验证脚本
- 对现有 Grafana bundle 的最小回归修复

但当前机器上 **OpenClaw OTel 配置尚未真正写入并重载**，因此“持续真实产流 + 页面验收”还不能由 Dev 单方面宣布完全闭环。

其中需要特别注意：agent 维度趋势依赖 bridge 从 `/v1/traces` 聚合 `sessionKey -> agent`，所以如果 live 环境继续保持 `diagnostics.otel.traces=false`，即使 dashboard 已同步，`openclaw_agent_*` 指标也不会出现。

后续建议：
- 先由 **Ops** 或具备运行环境权限的一方按 batch 写入 OpenClaw 配置并完成服务重载
- 再由 **QA** 基于实际 Grafana 页面与运行态数据做验收

如果环境方愿意直接在本机执行 batch 写入和服务重载，本 issue 可以继续向 **QA** 流转；否则先交 **Ops** 落地配置更稳妥。
