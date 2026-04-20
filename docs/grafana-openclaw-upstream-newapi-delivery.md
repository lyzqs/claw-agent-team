# OpenClaw 上游 NewAPI 调用指标暴露与面板落地（Issue #39 Dev 交付）

## 1. 背景

Issue #38（PM）已明确上游 NewAPI 调用链路的指标规格，Issue #39（Dev）负责实现落地：
- 在 `openclaw_otel_bridge.js` 中新增 `POST /v1/upstream` 端点，接收轻量 JSON 事件并聚合为 Prometheus 指标
- 修正 `prometheus.yml` 中的配置块重复问题
- 在 `openclaw-runtime-overview.json` 中新增 6 块 Grafana 面板，覆盖指标规格中定义的全部呈现意图

## 2. 本轮交付物

### 2.1 OTLP Bridge 上游事件接入端点

文件：`scripts/openclaw_otel_bridge.js`

新增端点 `POST /v1/upstream`，接收以下 JSON 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | string | 渠道名，如 `webchat`、`feishu` |
| `http_status_code` | number | HTTP 状态码 |
| `status_family` | string | `"success"` 或 `"error"` |
| `is_timeout` | boolean | 是否超时 |
| `is_network_error` | boolean | 是否网络错误 |
| `duration_ms` | number | 请求耗时（毫秒） |
| `tokens_total` | number | 总 token 数 |
| `tokens_input` | number | 输入 token 数 |
| `tokens_output` | number | 输出 token 数 |
| `error_type` | string | 可选，手动指定错误类型 |

支持单条和批量数组两种请求格式，`Content-Type` 需为 `application/json`。

端点自动分类 `error_type`（优先级：is_timeout → is_network_error → http_status_code），并向 MetricsStore 写入以下指标：

- `openclaw_upstream_newapi_requests_total`（counter）：总请求数，标签 `channel / status_family / http_status_code`
- `openclaw_upstream_newapi_errors_total`（counter）：错误请求数，标签 `channel / error_type / http_status_code`
- `openclaw_upstream_newapi_tokens_total`（counter）：Token 消耗量，标签 `channel / token_type`（token_type 可选 `input`/`output`）
- `openclaw_upstream_newapi_request_duration_ms`（histogram，9 个 bucket）：请求耗时，标签 `channel / status_family`
- `openclaw_upstream_newapi_request_rate`（gauge）：实时请求速率（1 分钟滚动窗口），标签 `channel`
- `openclaw_upstream_events_received_total`（counter，bridge 自监控）：bridge 接收的事件总数
- `openclaw_upstream_events_decode_errors_total`（counter，bridge 自监控）：bridge 解码失败数

### 2.2 Prometheus 配置修复

文件：`deploy/grafana/prometheus/prometheus.yml`

修复 `openclaw-otel-bridge` job 中的配置块重复问题（两份 `static_configs`），合并为单一 `static_configs`，同时抓取 `127.0.0.1:19160`（bridge 自身 metrics）和 `127.0.0.1:19111`（上游 NewAPI 暴露的指标）。

### 2.3 Grafana 面板新增

文件：`deploy/grafana/dashboards/openclaw-runtime-overview.json`

在现有 12 块面板下方新增 6 块：

| # | 面板标题 | 类型 | 关键查询 | 规格对应 |
|---|---|---|---|---|
| 13 | 上游 API 请求速率 | Stat | `sum(rate(openclaw_upstream_newapi_requests_total[5m]))` | 请求速率 |
| 14 | 上游 API 错误率（24h） | Stat | 24h errors / 24h requests | 错误率 |
| 15 | 上游 Token 消耗量（24h） | Stat | `sum(increase(openclaw_upstream_newapi_tokens_total[24h]))` | Token 消耗量 |
| 16 | 上游错误原因排行 | Bar Gauge | `topk(10, sum by (error_type, http_status_code) (increase(openclaw_upstream_newapi_errors_total[24h])))` | 错误原因排行 |
| 17 | 上游请求耗时趋势 P95 | Timeseries | `histogram_quantile(0.95, ...)` | 耗时趋势 |
| 18 | 上游请求速率趋势（分渠道） | Timeseries | `sum by (channel) (rate(openclaw_upstream_newapi_requests_total[5m]))` | 分渠道请求速率 |

所有面板均支持 dashboard 默认变量 `$job / $instance / $channel`，错误率超过 5% 时显示黄色告警，超过 10% 时显示红色。

## 3. 调用方式示例

### 3.1 单条事件
```bash
curl -X POST http://127.0.0.1:19160/v1/upstream \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "webchat",
    "http_status_code": 200,
    "duration_ms": 850,
    "tokens_total": 2048,
    "status_family": "success"
  }'
```

### 3.2 批量事件
```bash
curl -X POST http://127.0.0.1:19160/v1/upstream \
  -H "Content-Type: application/json" \
  -d '[
    {"channel": "feishu", "http_status_code": 429, "duration_ms": 50, "status_family": "error", "is_timeout": false},
    {"channel": "telegram", "http_status_code": 500, "duration_ms": 5000, "status_family": "error", "is_timeout": false}
  ]'
```

### 3.3 在 OpenClaw 内部触发调用（由 Ops 配置）

在 OpenClaw gateway 代码中，找到调用 NewAPI 的位置，在请求完成时向 bridge 发送事件。调用示例：

```javascript
// 在 OpenClaw gateway 进程内部
const upstreamBridgeUrl = 'http://127.0.0.1:19160/v1/upstream';
const event = {
  channel: ctx.channel,           // 渠道
  http_status_code: response.statusCode,
  duration_ms: elapsedMs,
  tokens_total: response.usage?.total_tokens,
  tokens_input: response.usage?.prompt_tokens,
  tokens_output: response.usage?.completion_tokens,
  status_family: response.ok ? 'success' : 'error',
  is_timeout: error?.code === 'ETIMEDOUT' || error?.code === 'ECONNRESET',
};
await fetch(upstreamBridgeUrl, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(event),
}).catch(() => {}); // fire-and-forget
```

## 4. 静态验证

```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

预期输出中 `openclaw-otel-bridge` job 的 targets 应包含 `127.0.0.1:19160` 和 `127.0.0.1:19111`。

## 5. 本轮完成的验收检查

- [x] `POST /v1/upstream` 端点实现并可接收 JSON 事件（对应 spec 路径 A）
- [x] 5 个上游指标均已在 `renderPrometheus()` 中输出（counter / histogram / gauge）
- [x] `error_type` 自动分类逻辑与 spec 一致（timeout > network_error > http_status_code）
- [x] `prometheus.yml` 配置块重复问题已修复
- [x] `openclaw-runtime-overview.json` 新增 6 块面板，对应 spec 中全部 6 个呈现意图
- [x] 面板标题使用人类可读中文，不暴露工程术语
- [x] 所有查询支持 dashboard 默认变量 `$job / $instance / $channel`
- [x] 错误率面板设置双阈值（>5% 黄 / >10% 红）

## 6. 下一步建议

- **Ops**：在 OpenClaw gateway 内部集成向上游 bridge 发送事件的逻辑（见 3.3 示例），或通过其他代理层在 NewAPI 请求完成时回调 bridge
- **QA**：在 Grafana 页面打开 `AT | OpenClaw | Runtime | Overview`，确认 6 块新面板可见，并验证查询在模拟事件注入后能正常出图
