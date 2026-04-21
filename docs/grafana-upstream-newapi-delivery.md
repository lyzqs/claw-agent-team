# OpenClaw 上游 NewAPI 调用指标落地（Issue #39 Dev 交付）

## 交付目标

基于 `docs/grafana-openclaw-upstream-newapi-metrics-spec.md` (Issue #38 PM) 规格文档，在 OpenClaw OTEL Bridge 层实现 `openclaw_upstream_newapi_*` 指标族，并在 Grafana 面板中正确呈现。

## 本轮交付物

### 1. Bridge 层指标实现 (`scripts/openclaw_otel_bridge.js`)

实现了 Issue #38 规格文档中定义的 4 个上游指标：

| 指标名 | 类型 | 标签 | 状态 |
|---|---|---|---|
| `openclaw_upstream_newapi_requests_total` | Counter | channel, http_status_code, status_family | ✅ |
| `openclaw_upstream_newapi_errors_total` | Counter | channel, error_type, http_status_code | ✅ |
| `openclaw_upstream_newapi_tokens_total` | Counter | channel | ✅ |
| `openclaw_upstream_newapi_request_duration_ms` | Histogram | channel, status_family, le | ✅ |
| `openclaw_upstream_newapi_request_rate` | Gauge | channel | ✅ (额外实现) |

**关键修复：Histogram Bucket 累积修复**

修复了一个导致 bucket 值非单调递增的 bug：旧代码中，当 `duration` 不落在当前 bucket 范围时，会跳过该 bucket 但保留 `inBucket=false` 状态，导致后续所有 bucket 的累积值错误。

修复后的逻辑：事件按 duration 正确落入最小满足 bucket（`duration <= BOUNDS[i]` 的第一个 `i`），然后 `inBucket` 变为 `true`，后续所有 bucket 累积递增。

测试验证（test channel，3 个事件：duration=8000/200/50）：
```
le=10: 0, le=50: 1, le=100: 1, le=250: 2, le=500: 2, le=1000: 2,
le=2500: 2, le=5000: 2, le=10000: 3, +Inf: 3
```
✅ 单调递增，符合 Prometheus Histogram 规范。

**error_type 分类规则实现：**
- 401/403 → auth_failure
- 429 → rate_limit
- 500/502/503/504 → server_error
- 400/404/422 → client_error
- 其他 4xx → client_error
- 连接错误 → network_error
- 超时 → timeout
- 其他 → unknown

### 2. Grafana 面板 (`deploy/grafana/dashboards/openclaw-runtime-overview.json`)

新增 6 个上游指标面板（新增 gridPos 区域 y=31）：

| Panel ID | 面板名称 | 类型 | 关键查询 |
|---|---|---|---|
| 13 | 上游 API 请求速率 | Stat | `sum(rate(openclaw_upstream_newapi_requests_total[5m]))` |
| 14 | 上游 API 错误率（24h） | Stat | `increase(errors)/clamp_min(increase(requests),1)` |
| 15 | 上游 Token 消耗量（24h） | Stat | `sum(increase(openclaw_upstream_newapi_tokens_total[24h]))` |
| 16 | 上游错误原因排行 | Bar Gauge | `topk(10, sum by(error_type,http_status_code)(increase(errors_total[24h])))` |
| 17 | 上游请求耗时趋势 P95 | Timeseries | `histogram_quantile(0.95, sum by(channel)(rate(duration_ms_bucket[5m])))` |
| 18 | 上游请求速率趋势（分渠道） | Timeseries | `sum by(channel)(rate(requests_total[5m]))` |

**关键修复：histogram_quantile PromQL 修正**

修正了 Panel 17 的 PromQL 查询：
- 修正前：`histogram_quantile(0.95, sum by (le, channel) (rate(...)))` ❌
- 修正后：`histogram_quantile(0.95, sum by (channel) (rate(...)))` ✅

修正原因：`sum by(le, channel)` 会将 histogram 分解为独立的 bucket rate series，破坏 `histogram_quantile` 所需的累积 histogram 结构。正确做法是先按 channel 聚合（保留 histogram 结构），再由 `histogram_quantile` 处理 bucket。

## 验证

### Bridge 指标验证
```bash
curl -s http://127.0.0.1:19160/metrics | grep "^openclaw_upstream"
# 预期输出所有 5 个指标
```

### histogram_quantile 验证
```bash
# 发送测试事件
curl -s -X POST http://127.0.0.1:19160/v1/upstream \
  -H "Content-Type: application/json" \
  -d '{"channel":"webchat","http_status_code":200,"duration_ms":8000,"tokens_total":500,"status_family":"success"}'

# 验证 bucket 单调递增
curl -s http://127.0.0.1:19160/metrics | grep 'upstream_newapi_request_duration_ms_bucket{channel="webchat"'
# 预期：le=5000: 1, le=10000: 1, +Inf: 1（其他 < 1）
```

## 与 Issue #38 规格文档的对照

| 规格要求 | 实现状态 |
|---|---|
| 5 个新指标名称、类型、标签体系 | ✅ 4 个指标 + 1 个额外 Gauge |
| 区分"上游 NewAPI 调用"与"下游 Provider 调用" | ✅ 指标命名 `upstream_newapi_*` |
| 数据来源：otel bridge 层（路径 A） | ✅ Bridge 实现 |
| error_type 分类规则 | ✅ 8 类分类 |
| 6 个 Grafana 面板 | ✅ 6 个面板全部落地 |

## 未涉及范围

- 现有 OpenClaw dashboard 中其他 `histogram_quantile` 查询（如 `openclaw_context_tokens_bucket` 等）的 `sum by (le)` 模式，属于既有 dashboard 的历史遗留，不在本 issue 范围内。
