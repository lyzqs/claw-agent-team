# OpenClaw 上游 NewAPI 调用指标规格（Issue #38 PM）

## 1. 文档目标

本文定义 `OpenClaw → NewAPI` 上游 API 调用链路的指标规格，用于支撑 `Issue #39` 实现落地。

回答以下问题：
1. OpenClaw 上游调用 NewAPI 时应暴露哪些指标。
2. 指标的数据来源是 OpenClaw 自身还是 bridge 侧。
3. 标签体系如何设计，如何与 OpenClaw 到模型 Provider 的下游调用区分。
4. 错误分类方式与 Grafana 面板呈现结构。

## 2. 事实基础

### 2.1 当前已确认的事实

1. OpenClaw Gateway 已通过 `diagnostics-otel` 插件暴露以下指标族（来源：`/usr/lib/node_modules/openclaw/docs/logging.md`）：
   - `openclaw.tokens` / `openclaw.cost.usd` / `openclaw.run.duration_ms` / `openclaw.context.tokens`
   - `openclaw.webhook.*` / `openclaw.message.*` / `openclaw.queue.*` / `openclaw.session.*`

2. OpenClaw 已存在 `openclaw_otel_bridge_requests_total` 指标，已用于 `openclaw-runtime-overview.json` 中的"桥接接收请求数"面板。
   - 该指标是 OpenClaw otel bridge 向外部（如 NewAPI）发出请求时的计数，但目前只有总量，没有错误率、错误分类、token、耗时等维度。

3. OpenClaw 到 NewAPI 的调用是一种**上行（upstream）代理调用**，与 OpenClaw 到模型 Provider（OpenAI、Google 等）的下行调用属于不同方向，必须在指标命名和标签上明确区分。

4. 当前 OpenClaw 现有 dashboard 中没有"上游 API 错误率"、"错误原因排行"、"上游 token 消耗量"等面板。

### 2.2 用户核心诉求

用户明确提出：
- 在 Grafana 中能看到 OpenClaw 请求 NewAPI 的**错误率**
- 能看到**错误原因排行**（NewAPI 有时日志不完整，需要从 OpenClaw 侧反推）
- 能看到**Token 消耗量**和**请求速度**

### 2.3 指标边界划分

```
OpenClaw → 下游 → 模型 Provider（OpenAI / Google 等）
  → 已有指标：openclaw_tokens_total（按 provider/model 标签）
  → 已有面板：Token / 成本趋势、Provider / Model Token 分布

OpenClaw → 上游 → NewAPI（作为下游的 API 网关）
  → 本次新增指标（见下节）
  → 新增面板：上游 API 错误率、错误原因排行、Token 消耗量、请求速率
```

## 3. 指标规格

### 3.1 指标清单

| 指标名 | 类型 | 描述 | 关键标签 |
|---|---|---|---|
| `openclaw_upstream_newapi_requests_total` | Counter | OpenClaw 到 NewAPI 的总请求数 | `status_family`（success/error）、`http_status_code`、`channel` |
| `openclaw_upstream_newapi_errors_total` | Counter | OpenClaw 到 NewAPI 的错误请求数（status_family=error） | `error_type`（timeout/4xx/5xx/network/unknown）、`http_status_code`、`channel` |
| `openclaw_upstream_newapi_tokens_total` | Counter | OpenClaw 到 NewAPI 请求消耗的 Token 数 | `channel` |
| `openclaw_upstream_newapi_request_duration_ms` | Histogram | OpenClaw 到 NewAPI 请求的耗时 | `channel`、`status_family` |
| `openclaw_upstream_newapi_request_rate` | Gauge | OpenClaw 到 NewAPI 的实时请求速率（请求/秒） | `channel` |

### 3.2 标签设计

| 标签名 | 值域示例 | 说明 |
|---|---|---|
| `channel` | `webchat` / `feishu` / `telegram` 等 | OpenClaw 收到请求的渠道，用于横向对比 |
| `status_family` | `success` / `error` | 区分成功与失败，不含具体状态码（具体用 http_status_code） |
| `http_status_code` | `200` / `400` / `401` / `429` / `500` / `502` 等 | HTTP 状态码，用于错误原因排行 |
| `error_type` | `timeout` / `rate_limit` / `auth_failure` / `server_error` / `network_error` / `unknown` | 基于 HTTP 状态码和错误信息的语义分类，与 Grafana "错误原因排行"面板对应 |
| `token_type` | `input` / `output` / `total` | Token 类型 |

### 3.3 error_type 分类规则

```
http_status_code == 200  → 不记录到 errors_total（只记 requests_total）
http_status_code in [401, 403]  → error_type = "auth_failure"
http_status_code == 429  → error_type = "rate_limit"
http_status_code in [500, 502, 503, 504]  → error_type = "server_error"
http_status_code in [400, 404, 422]  → error_type = "client_error"
status == "network_error"（连接超时、DNS 失败等）→ error_type = "network_error"
status == "timeout"  → error_type = "timeout"
无法判断时 → error_type = "unknown"
```

### 3.4 数据来源

有两种实现路径，规格文档同时记录，由 Dev 在 #39 中根据实际情况选择：

**路径 A（推荐）：在 OpenClaw otel bridge 层 emit**
- 在 `openclaw_otel_bridge_requests_total` 的 emit 逻辑附近增加分桶标签（http_status_code、error_type）。
- 额外 emit `openclaw_upstream_newapi_tokens_total`（需要从 NewAPI 响应体中解析 token 字段，假设 NewAPI 返回体含 `usage` 字段）。
- 适用条件：OpenClaw bridge 层已有请求上下文（request/response），且能够解析响应体。

**路径 B（fallback）：在 NewAPI 侧通过 OpenClaw 特定的 channel/tag 标记统计**
- 在 NewAPI 增加按 `source_channel` 过滤的指标，专门记录从 OpenClaw 发来的请求。
- 不推荐：会让 OpenClaw 的指标分散在 NewAPI 侧，不利于统一 Grafana 架构。

## 4. Grafana 面板规格

### 4.1 新增面板：OpenClaw 上游 API 健康（建议放在 openclaw-runtime-overview.json 或新增 openclaw-upstream-api-health.json）

| 面板名称 | 类型 | 呈现意图 | 关键查询 |
|---|---|---|---|
| OpenClaw→NewAPI 请求速率 | Stat / Timeseries | 实时观察上游请求量是否正常 | `sum(rate(openclaw_upstream_newapi_requests_total[5m]))` |
| 上游 API 错误率（24h） | Stat | 快速感知上游健康状态 | `sum(increase(openclaw_upstream_newapi_errors_total[24h])) / clamp_min(sum(increase(openclaw_upstream_newapi_requests_total[24h])))` |
| 上游错误原因排行 | Bar Gauge（topk） | 帮助排查"为什么出错" | `topk(10, sum by (error_type, http_status_code) (increase(openclaw_upstream_newapi_errors_total[24h])))` |
| 上游 Token 消耗量（24h） | Stat | 了解 NewAPI 作为模型代理的 Token 消耗 | `sum(increase(openclaw_upstream_newapi_tokens_total[24h]))` |
| 上游请求耗时趋势 | Timeseries | 观察延迟是否恶化 | `histogram_quantile(0.95, sum by (le) (rate(openclaw_upstream_newapi_request_duration_ms_bucket[5m])))` |
| 上游请求速率趋势（分渠道） | Timeseries | 按渠道对比上游请求模式 | `sum by (channel) (rate(openclaw_upstream_newapi_requests_total[5m]))` |

### 4.2 面板标题的人类可读性要求

- 不使用工程术语如 `upstream_newapi`、`error_type`、`status_family` 作为面板标题。
- 标题优先使用自然语言，例如：
  - "上游 API 错误率" 而非 "upstream_newapi_error_rate"
  - "错误原因排行" 而非 "error_type_distribution"
- 面板副标题或描述可说明具体维度筛选方式。

## 5. 与现有指标族的区分

| 指标族 | 方向 | 数据来源 | 主要标签 | 已有面板 |
|---|---|---|---|---|
| `openclaw_tokens_total` | 下游 → 模型 Provider | OpenClaw 模型调用 | provider、model、channel | Token/成本趋势、Provider分布 |
| `openclaw_webhook_*` | OpenClaw 收到外部消息 | OpenClaw webhook 接收层 | channel、webhook | Webhook接收量分布、耗时趋势 |
| `openclaw_upstream_newapi_*` | 上游 → NewAPI | OpenClaw bridge / otel 层 | channel、http_status_code、error_type | 本次新增（见 4.1） |

## 6. 验收检查清单

- [ ] 文档明确了 5 个新指标的名称、类型、标签体系（对应 AC 第 1 条）
- [ ] 文档明确区分了"上游 NewAPI 调用"与"下游模型 Provider 调用"（对应 AC 第 2 条）
- [ ] 文档明确了数据来源路径（otel bridge 层，路径 A/B）（对应 AC 第 3 条）
- [ ] 文档定义了 error_type 分类规则与 Grafana "错误原因排行"呈现方式（对应 AC 第 4 条）
- [ ] 文档给出了 6 个 Grafana 面板的名称、类型、关键查询（对应 AC 第 5 条，可直接支撑 Dev 实现）