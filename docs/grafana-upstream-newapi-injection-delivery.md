# OpenClaw 上游 NewAPI 事件自动注入落地（Issue #40 Dev 交付）

## 1. 背景

Issue #39（Dev）已在 `openclaw_otel_bridge.js` 中实现了 `POST /v1/upstream` 端点，并补上了 Grafana 面板。但 dashboard 依赖手动 curl 压测注入事件，真实流量为零。本 issue（Issue #40）负责在 NewAPI 侧自动注入真实上游事件，让 Prometheus 中的 `openclaw_upstream_newapi_requests_total` 真实 channel 分布可观测。

## 2. 架构分析

```
用户请求 → OpenClaw Gateway → NewAPI (localhost:3000) → 上游模型 Provider
                                              ↓
                              NewAPI relay handler 处理请求
                                              ↓
                              【新增】UpstreamBridgeMiddleware
                                              ↓
                              POST /v1/upstream 到 OpenClaw OTLP Bridge
```

OpenClaw Gateway 将 `openai` provider 配置为 `http://localhost:3000/v1`（NewAPI）。所有对 NewAPI 的 OpenAI 兼容请求都经过 `/v1/*` 路由，由 NewAPI relay handler 处理并转发到上游。

NewAPI 在处理每个 relay 请求后，可以从 Gin context 获取：
- Channel ID / Channel Name（来自 `Distribute()` middleware）
- 请求耗时（计时）
- HTTP 状态码（响应时）
- Token 用量（从响应体解析）

## 3. 交付物

### 3.1 新增 middleware 文件

文件：`/root/new-api/middleware/upstream_bridge.go`

实现要点：
- `UpstreamBridgeMiddleware()`: Gin middleware，拦截 `/v1/*` relay 响应，提取 channel、status、duration、token 用量
- `bufferedWriter`: 包装 `gin.ResponseWriter`，捕获响应体用于提取 `usage` 字段（不干扰响应传递）
- `resolveChannelName()`: 从 Gin context 优先取 `channel_name`，没有则通过 channel ID 查 DB；使用 5 分钟 in-process 缓存减少 DB 查询
- `fireUpstreamEvent()`: fire-and-forget goroutine POST 到 bridge `/v1/upstream`，超时 3 秒，不阻塞响应
- `InitUpstreamBridgeMiddleware()`: 可选的初始化函数，允许在 main.go 中配置 bridge URL（默认 `http://127.0.0.1:19160/v1/upstream`）

### 3.2 Middleware 注册

文件：`/root/new-api/router/relay-router.go`

在 `relayV1Router` middleware 链中新增一行：

```go
relayV1Router.Use(middleware.UpstreamBridgeMiddleware())
```

位置：`TokenAuth` 和 `ModelRequestRateLimit` 之后，确保 channel 解析逻辑能获取到 distributor middleware 设置的 context。

## 4. 数据流

1. 请求进入 `relayV1Router` middleware 链
2. `Distribute()` middleware 在 context 中设置 `channel_id` 和 `channel_name`
3. Relay handler 调用上游 Provider，生成响应
4. `UpstreamBridgeMiddleware.Next()` 返回，`blw.buf` 中包含完整响应体
5. 从 context 解析 channel name；从响应体解析 usage.token 字段
6. goroutine fire-and-forget POST 到 bridge

## 5. 已知限制与注意事项

- Middleware 在 goroutine 中发送事件，不阻塞响应传递
- 响应体超过 1MB 时跳过 usage 解析，避免大响应影响性能
- Token 用量从响应体解析，依赖 OpenAI 兼容格式；对于 streaming 响应（`data: [DONE]`）不做 token 解析（streaming body 不是 JSON）
- 5 分钟 channel 名称缓存，降低 DB 查询频率
- 建议 Ops 在部署后确认 bridge 和 NewAPI 网络互通（localhost:19160）

## 6. 部署步骤（Ops）

```bash
cd /root/new-api
git pull  # 或复制新的 middleware 和 router 文件

# 编译
go build -o new-api .

# 重启 NewAPI 服务
sudo systemctl restart new-api.service

# 验证 middleware 加载（检查日志无报错）
sudo journalctl -u new-api.service -f --lines=20

# 验证 bridge 收到事件
curl -s http://127.0.0.1:19160/metrics | grep upstream_newapi
```

## 7. 验收检查

- [ ] NewAPI 重启后无 middleware 加载报错
- [ ] 发送一个测试请求到 NewAPI（通过 OpenClaw 或直接）：`curl http://localhost:3000/v1/chat/completions ...`
- [ ] 检查 bridge metrics：`curl http://127.0.0.1:19160/metrics | grep upstream_newapi`，应有 `openclaw_upstream_newapi_requests_total{channel="..."}` 出现
- [ ] 检查 errors_total：`curl -X POST http://localhost:3000/v1/chat/completions -d '{"model":"invalid-model","messages":[{"role":"user","content":"test"}]}'` 后 bridge 中应有 `openclaw_upstream_newapi_errors_total`
- [ ] Grafana 中 `AT | OpenClaw | Runtime | Overview` 的上游面板应有数据
