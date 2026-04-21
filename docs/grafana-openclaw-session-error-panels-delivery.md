# Issue #42 Dev 交付：OpenClaw 内部 Session 异常终止错误率与消息级异常面板

## 1. 背景

Issue #42（PM）要求将 Issue #39 产出的"上游 API 错误率（24h）"面板替换为 OpenClaw **内部** session 异常指标面板。

## 2. 数据来源分析

### 可用指标

| 指标名 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `openclaw_session_stuck_total` | gauge | `state=processing` | **当前**卡住（stuck）会话数，跨 pid 聚合后约 998 |
| `openclaw_session_state_total` | counter | `state`, `reason` | Session 状态转换累计计数。状态仅含 `idle`/`processing`；原因含 `message_completed`、`message_start`、`run_completed`、`run_started` |
| `openclaw_message_processed_total` | counter | `outcome`, `channel` | 消息处理结果。仅含 `outcome=completed`，无 `outcome=error/failed` |

### 指标链路

OpenClaw Gateway (`diagnostics.otel`) → OTLP HTTP/protobuf → OTLP Bridge (`:19160/v1/metrics`) → Prometheus (`:19090`)

Gateway 配置 `diagnostics.otel.endpoint: http://127.0.0.1:19160`，每 60s 推送一次。

## 3. 本轮交付物

### 3.1 面板替换

在 `openclaw-runtime-overview.json` 中对 3 个面板进行替换/新增：

#### Panel 14: `Session 异常状态率（24h）`（替换原"上游 API 错误率"）

- **类型**: Stat
- **表达式**: `sum(openclaw_session_stuck_total{job=~"$job",instance=~"$instance"})`
- **说明**: 显示当前卡住会话数（gauge），阈值：黄 > 500，红 > 1000
- **语义**: `openclaw_session_stuck_total` 是 gauge，表示当前正在运行的卡住会话数，而非累计异常终止数

#### Panel 16: `异常终止原因排行`（替换原"上游错误原因排行"）

- **类型**: Bar Gauge
- **表达式**: `topk(10, sum by (reason, state) (increase(openclaw_session_state_total{job=~"$job",instance=~"$instance"}[24h])))`
- **说明**: 展示 session 状态转换的 reason 分布（按 state 分组），展示 24h 内各 state/reason 组合的转换次数

#### Panel 19: `Message 异常率趋势`（新增）

- **类型**: Timeseries
- **表达式**: `sum by (outcome, channel) (rate(openclaw_message_processed_total{job=~"$job",instance=~"$instance"}[5m]))`
- **说明**: 按 outcome 和 channel 分组展示消息处理速率

### 3.2 Dashboard Push

已通过 Grafana API (`POST /api/dashboards/db`) 推送至 live Grafana，Dashboard UID: `at-openclaw-runtime-overview`。

## 4. Prometheus 直接查询验证

```
# Panel 14 - 当前卡住会话数
sum(openclaw_session_stuck_total{job="openclaw-otel-bridge"}) = 998

# Panel 16 - Session 状态分布（24h increase）
topk(5, sum by (reason, state) (increase(openclaw_session_state_total{job="openclaw-otel-bridge"}[24h])))
  reason=message_start, state=processing: 732
  reason=message_completed, state=idle: 728
  reason=run_started, state=processing: 700
  reason=run_completed, state=idle: 696

# Panel 19 - Message 处理速率
sum by (outcome, channel) (rate(openclaw_message_processed_total{job="openclaw-otel-bridge"}[5m]))
  channel=webchat, outcome=completed: ~0.007 req/s
  channel=telegram, outcome=completed: 0
```

## 5. Blocking Findings

### 5.1 Gateway 未暴露 `state=abnormal` 标签

**现象**: `openclaw_session_state_total` 仅含 `state=idle` 和 `state=processing`，无 `state=abnormal`、`state=error`、`state=timeout`。

**影响**: 无法计算"真正的 Session 异常终止率"（需 `state=abnormal`）。当前 Panel 14 退化为"卡住会话数"，Panel 16 退化为"Session 状态转换 reason 分布"（而非异常终止原因）。

**根因**: OpenClaw Gateway 内部 session 异常判断逻辑存在，但未通过 OTel metric 的 `state` 标签暴露为 `abnormal`/`error`/`timeout`。

**建议**: 由 OpenClaw 侧（Ops/Dev）修改 Gateway 代码，在 session 异常终止时 emit `state=abnormal` 标签。

### 5.2 Gateway 未暴露 `outcome=error/failed` 标签

**现象**: `openclaw_message_processed_total` 仅含 `outcome=completed`，无 `outcome=error`、`outcome=failed`。

**影响**: 无法计算"Message 异常率"。Panel 19 只能展示 `completed` 速率，无法展示 error/failed 比率。

**建议**: 由 OpenClaw 侧（Ops/Dev）修改 Gateway 代码，在消息处理异常时 emit `outcome=error` 标签。

### 5.3 Summary

| 验收标准 | 状态 | 说明 |
|---|---|---|
| "上游 API 错误率" 已替换 | ✅ | Panel 14 替换为 Session 异常状态率 |
| "异常终止原因排行" 新增 | ✅ | Panel 16 替换为 Session 状态 reason 分布 |
| "Message 异常率趋势" 新增 | ✅ | Panel 19，展示 outcome 趋势 |
| 面板有数据无 No Data | ✅ | Panel 14: 998; Panel 16: 4 个 reason; Panel 19: completed rate |
| 面板中文标题 | ✅ | 全部中文 |
| 展示 session 异常终止率（true abnormal） | ⚠️ 阻塞 | Gateway 未暴露 `state=abnormal`，面板只能展示卡住会话数 |
| 展示异常终止原因 | ⚠️ 阻塞 | Gateway 未暴露异常 state，面板只能展示所有 state 的 reason |
| 展示 Message 异常率 | ⚠️ 阻塞 | Gateway 未暴露 `outcome=error/failed`，面板只能展示 completed rate |

**建议**: Issue #42 标记为部分完成（3 块面板已落地并可渲染），但 3 个 blocking findings 需在后续 issue 中由 OpenClaw 侧处理后方可完全满足验收标准。

## 6. 下一步

- **建议新 Issue（Ops/Dev）**: 在 OpenClaw Gateway 内部 session 异常判断处 emit `state=abnormal` 和 `outcome=error/failed` OTel metric labels
- **建议新 Issue（Ops/Dev）**: 在 `openclaw_message_processed_total` 中增加 `outcome=error/failed` 标签
- **QA**: 在 Grafana 页面确认 3 块新面板可见、可渲染
