# OpenClaw Grafana 观测规格（Issue #26 PM）

## 1. 文档目标

本文用于定义 `OpenClaw` 接入 Grafana 前的 PM 规格基线，回答以下问题：

1. OpenClaw 当前已经具备哪些可直接导出的 OTel 能力与内建指标。
2. 哪些属于 OpenClaw 的业务/运行指标，哪些系统指标应直接复用现有主机观测底座。
3. 在当前 `Prometheus + Grafana` 本地栈下，OpenClaw 应如何以最小变更面接入。
4. 改动 OpenClaw 配置时，怎样保证配置合法、可回滚、允许重启但不影响正常使用。
5. Grafana 中应该如何分组、命名与组织 OpenClaw 信息结构。

本文是 `Issue #26` 的直接交付物，供 `Issue #27` 实现使用。

## 2. 事实基础与当前已知线索

基于当前 OpenClaw 本地文档、现有 Grafana 仓库与已落地观测底座，已确认以下事实：

1. OpenClaw 已明确支持通过 `diagnostics-otel` 插件与 `diagnostics.otel` 配置导出 OTel 信号。
   - 关键配置路径包括：
     - `plugins.allow`
     - `plugins.entries.diagnostics-otel.enabled`
     - `diagnostics.enabled`
     - `diagnostics.otel.enabled`
     - `diagnostics.otel.endpoint`
     - `diagnostics.otel.protocol`
     - `diagnostics.otel.headers`
     - `diagnostics.otel.serviceName`
     - `diagnostics.otel.traces`
     - `diagnostics.otel.metrics`
     - `diagnostics.otel.logs`
     - `diagnostics.otel.sampleRate`
     - `diagnostics.otel.flushIntervalMs`

2. OpenClaw 当前文档已列出一批可直接导出的指标，至少覆盖以下信号族：
   - model usage
   - token / cost
   - run duration
   - context tokens
   - webhook / message flow
   - queue enqueue / dequeue / depth / wait
   - session state / stuck
   - run attempt

3. OpenClaw 当前 OTel 导出协议以 `OTLP/HTTP (http/protobuf)` 为主，文档明确指出 `grpc` 目前可忽略。因此实现应优先围绕 `OTLP/HTTP` 设计。

4. 当前 `agent-team-grafana` 本地观测栈已经落地的主数据源仍然是本地 `Prometheus`，其抓取对象包括：
   - `node-exporter`
   - `process-exporter`
   - `agent-team-exporter`
   - `newapi-exporter`
   - `arena-exporter`
   - `uptime-kuma-exporter`

   但尚未看到专门承接 OpenClaw `OTLP/HTTP` 指标的 collector / bridge / scrape target。

5. 现有主机观测底座已经可复用：
   - 宿主机总量指标可由 `node-exporter` 提供。
   - 进程级 CPU / 内存等指标可由 `process-exporter` 提供。

6. 当前 `process-exporter` 配置仍是按 `{{.Comm}}` 聚合进程名，这对某些 Node.js 进程可能不够精确。
   - 如果 OpenClaw 进程以通用 `node` 进程名出现，后续实现需要补一条更稳定的匹配规则，确保 OpenClaw 进程指标可单独识别，而不是与其他 Node 进程混在一起。

7. 当前 Grafana 统一蓝图已明确：
   - 单主 Grafana 入口
   - 单主 Prometheus 数据源 `prometheus-local-main`
   - 优先 pull 型采集
   - 能复用底座就不额外造轮子

8. 本 issue 当前未要求建设完整日志平台或 tracing 平台。
   - 因此本轮目标是让 Grafana 稳定看到 OpenClaw 相关指标。
   - traces / logs 的后续可视化能力只需要在方案中留好边界，不应反向扩大本轮实现范围。

## 3. OpenClaw 的系统定位与观测分层

OpenClaw 既是网关型 runtime，又是多会话、多通道、多模型调用编排系统。建议按 4 层观测：

### 3.1 L0 观测平台层

目标：确认“观测链路本身”正常。

关键对象：
- OpenClaw OTel exporter 是否启用
- OTLP 接收端是否可用
- Prometheus 是否成功抓取 collector / bridge
- Grafana datasource 是否正常

关键意义：
- 如果 L0 失败，下面所有 OpenClaw 指标都会表现为无数据或断流。

### 3.2 L1 主机 / 进程层

目标：确认 OpenClaw 所在宿主机与进程运行稳定。

关键对象：
- 宿主机 CPU / 内存 / 磁盘 / 网络
- OpenClaw 进程 CPU / RSS / FD / 存活性
- 进程重启或异常退出迹象

关键意义：
- 这是 OpenClaw “能不能稳定跑”的底座，不应与业务信号混为一谈。

### 3.3 L2 Gateway / Runtime 层

目标：观察 OpenClaw 自身运行态与会话/队列健康。

关键对象：
- run duration
- message processed / queued
- queue enqueue / dequeue / depth / wait
- session state / stuck
- run attempt

关键意义：
- 这层决定 OpenClaw 是否在稳定处理消息、排队、会话与执行链路。

### 3.4 L3 Usage / Channel / Model 层

目标：观察 OpenClaw 对外工作量与成本/质量表现。

关键对象：
- token 输入/输出
- 成本累计
- provider / model 维度运行时长
- channel / webhook 处理结果
- message outcome 分布

关键意义：
- 这是 OpenClaw 面向用户价值的直接信号，回答“它到底处理了多少工作，成本和效率怎么样”。

## 4. 核心观测对象

本轮 OpenClaw 接入 Grafana，建议围绕以下 5 类观测对象建设。

### 4.1 Model usage 与成本信号

目标：看到 OpenClaw 实际调用模型的体量、成本与耗时。

核心问题：
- 最近模型调用量是否在上升或下降。
- 哪个 provider / model 的 token 与 cost 最高。
- run duration 是否异常升高。
- context token 规模是否膨胀。

### 4.2 Message flow 与 webhook/channel 信号

目标：看到 OpenClaw 如何接收、排队并处理消息。

核心问题：
- webhook 是否稳定收到请求。
- message processed 是否正常增长。
- outcome 是否出现异常偏移。
- 某个 channel 是否错误率上升。

### 4.3 Queue / Session / Runtime 信号

目标：看到 OpenClaw 是否有积压、卡会话、长时间 stuck。

核心问题：
- queue depth 是否持续增高。
- queue wait_ms 是否恶化。
- session stuck 是否增长。
- run attempt 是否异常集中在 retry / stuck 模式。

### 4.4 主机 / 进程运行信号

目标：看到 OpenClaw 进程本身的资源消耗。

核心问题：
- OpenClaw 进程 CPU / 内存是否异常。
- 是否与高 token / 高耗时时段有明显相关。
- 是否存在“模型指标正常但进程资源异常”的情况。

### 4.5 配置与可回滚边界

目标：把 OpenClaw 的观测接入限制在可控范围内。

核心问题：
- 配置改动是否只触及 `diagnostics` / plugin 相关子树。
- collector 是否只监听本地回环，不对外暴露。
- 回滚时能否通过关闭 OTel 导出快速恢复。
- 是否存在因为 traces / logs 扩面导致的额外风险。

## 5. 关键用户路径

本轮建议优先围绕以下用户路径设计观测，而不是一次性把所有 OTel 信号铺满。

1. **OpenClaw 启动后是否仍正常工作**
   - collector 可达
   - OTel metrics 开启
   - Prometheus 成功抓取
   - OpenClaw 正常处理消息

2. **最近模型调用、token 与 cost 是否异常**
   - token 趋势
   - cost 趋势
   - provider / model 分布
   - run duration 趋势

3. **消息与队列是否出现积压或卡住**
   - message queued / processed
   - queue depth / wait
   - session stuck
   - run attempt 分布

4. **是不是 OpenClaw 自身资源不足导致问题**
   - 宿主机 CPU / 内存
   - OpenClaw 进程 CPU / 内存
   - 是否与 queue / run duration 呈正相关

5. **某个 channel / webhook 是否在出问题**
   - webhook received / error / duration
   - message outcome 分布
   - channel 维度异常集中

## 6. 关键健康信号

### 6.1 顶层业务 / 运行健康信号

必须优先进入 Grafana 的信号：

1. 最近 24h token 总量
2. 最近 24h cost 总量
3. run duration 趋势
4. message processed 速率
5. queue depth 当前值与趋势
6. queue wait_ms 趋势
7. session stuck 数量
8. session state 分布
9. run attempt 计数
10. OpenClaw 进程 CPU / 内存

### 6.2 二级补充信号

建议纳入：

1. provider / model 维度 token 分布
2. provider / model 维度 cost 分布
3. context token 分布
4. webhook received / error / duration
5. queue enqueue / dequeue 速率
6. stuck session age 分布
7. datasource / collector scrape health

## 7. 指标清单

以下指标按“优先级 + 指标目的 + 单位 + 标签建议”定义。

### 7.1 P0 核心指标

#### 1. `openclaw.tokens`
- 目的：观察模型 token 使用量
- 单位：counter
- 关键属性：`openclaw.token`, `openclaw.channel`, `openclaw.provider`, `openclaw.model`
- 说明：这是 OpenClaw 成本与吞吐分析的核心指标之一

#### 2. `openclaw.cost.usd`
- 目的：观察模型调用成本
- 单位：counter (USD)
- 关键属性：`openclaw.channel`, `openclaw.provider`, `openclaw.model`
- 说明：应与 token 趋势配合展示

#### 3. `openclaw.run.duration_ms`
- 目的：观察单次 run 耗时分布
- 单位：histogram (ms)
- 关键属性：`openclaw.channel`, `openclaw.provider`, `openclaw.model`
- 说明：用于发现整体处理变慢或特定模型变慢

#### 4. `openclaw.context.tokens`
- 目的：观察上下文规模
- 单位：histogram (tokens)
- 关键属性：`openclaw.context`, `openclaw.channel`, `openclaw.provider`, `openclaw.model`
- 说明：用于发现提示词膨胀、缓存命中变化等现象

#### 5. `openclaw.message.queued`
- 目的：观察进入消息处理队列的体量
- 单位：counter
- 关键属性：`openclaw.channel`, `openclaw.source`

#### 6. `openclaw.message.processed`
- 目的：观察已处理消息量与结果分布
- 单位：counter
- 关键属性：`openclaw.channel`, `openclaw.outcome`

#### 7. `openclaw.message.duration_ms`
- 目的：观察消息处理耗时
- 单位：histogram (ms)
- 关键属性：`openclaw.channel`, `openclaw.outcome`

#### 8. `openclaw.queue.depth`
- 目的：观察队列积压程度
- 单位：histogram
- 关键属性：`openclaw.lane` 或 `openclaw.channel=heartbeat`
- 说明：这是定位积压和异步执行延迟的关键指标

#### 9. `openclaw.queue.wait_ms`
- 目的：观察队列等待时长
- 单位：histogram (ms)
- 关键属性：`openclaw.lane`

#### 10. `openclaw.session.state`
- 目的：观察会话状态切换与分布
- 单位：counter
- 关键属性：`openclaw.state`, `openclaw.reason`

#### 11. `openclaw.session.stuck`
- 目的：观察卡住会话数量
- 单位：counter
- 关键属性：`openclaw.state`

#### 12. `openclaw.run.attempt`
- 目的：观察执行尝试量
- 单位：counter
- 关键属性：`openclaw.attempt`

### 7.2 P1 重点扩展指标

#### 13. `openclaw.webhook.received`
- 目的：观察 webhook 接收量
- 单位：counter
- 关键属性：`openclaw.channel`, `openclaw.webhook`
- 说明：仅在有 webhook 型 channel 时有意义

#### 14. `openclaw.webhook.error`
- 目的：观察 webhook 错误量
- 单位：counter
- 关键属性：`openclaw.channel`, `openclaw.webhook`

#### 15. `openclaw.webhook.duration_ms`
- 目的：观察 webhook 处理延迟
- 单位：histogram (ms)
- 关键属性：`openclaw.channel`, `openclaw.webhook`

#### 16. `openclaw.queue.lane.enqueue`
- 目的：观察各 lane 入队速率
- 单位：counter
- 关键属性：`openclaw.lane`

#### 17. `openclaw.queue.lane.dequeue`
- 目的：观察各 lane 出队速率
- 单位：counter
- 关键属性：`openclaw.lane`

#### 18. `openclaw.session.stuck_age_ms`
- 目的：观察 stuck 会话年龄分布
- 单位：histogram (ms)
- 关键属性：`openclaw.state`

### 7.3 本轮不作为 Grafana 主验收目标的信号

OpenClaw 还支持 traces / logs 导出，但本轮不把它们作为 Grafana 主验收目标：

1. traces 需要单独的 tracing backend 才能真正发挥价值。
2. logs 需要统一日志后端，否则会增加部署面与风险。
3. 当前验收聚焦“Grafana 能看到 OpenClaw 相关指标”，因此 traces / logs 只保留接入边界说明，不纳入首轮必须项。

## 8. 标签规范

遵循统一蓝图，OpenClaw 本轮建议基础标签如下：

- `env`
- `project=agent-team-grafana`
- `system=openclaw`
- `service=openclaw-gateway`
- `instance`
- `job`
- `layer`

业务扩展标签建议：
- `channel`
- `provider`
- `model`
- `outcome`
- `lane`
- `state`
- `reason`
- `attempt`
- `token_type`

### 8.1 OTel 属性到 Prometheus label 的映射原则

需要注意，OpenClaw 文档中列出的属性名多为 OTel 风格，例如：
- `openclaw.channel`
- `openclaw.provider`
- `openclaw.model`

经过 collector / exporter 转成 Prometheus 后，标签名很可能会被归一化，例如点号转下划线。实现侧必须：

1. 维护一份确定性的属性映射表。
2. 以 collector 最终实际暴露的 label 名为 dashboard 变量绑定依据。
3. 避免出现“文档写一套、PromQL 查询另一套”的命名漂移。

### 8.2 禁止直接作为 label 的高基数字段

以下字段不应长期作为 Prometheus label：
- `sessionKey`
- `sessionId`
- `chatId`
- `messageId`
- 完整 webhook path
- 原始 error 文本
- 原始日志内容
- 完整 trace/span id

这些内容更适合：
- 明细日志
- trace drill-down
- 故障样本
- 临时排障查询

## 9. 采集方式建议

### 9.1 优先级

1. **优先复用 OpenClaw 内建 OTel metrics**
2. **其次新增一层轻量 collector / bridge，把 OTLP 指标转成 Prometheus 可抓取格式**
3. **系统层继续复用 node-exporter / process-exporter**
4. **不建议为了首轮接入去改 OpenClaw 核心代码自行发明 Prometheus exporter**

### 9.2 推荐采集路径

#### 路径 A：OpenClaw 内建 OTel metrics -> 轻量 collector -> Prometheus

这是本轮推荐主路径。

建议链路：

`OpenClaw diagnostics.otel (OTLP/HTTP) -> 本地 collector / bridge -> Prometheus scrape -> Grafana`

原因：
1. OpenClaw 已经能发 OTel，无需侵入式改应用。
2. 当前 Grafana 栈仍以 Prometheus pull 为主，需要一个桥接点。
3. 这条链路最符合“最小变更面 + 不影响正常使用”的目标。

#### 路径 B：主机 / 进程指标复用现有底座

OpenClaw 相关系统指标应继续复用：
- `node-exporter`
- `process-exporter`

实现注意：
- 若 OpenClaw 进程无法通过现有 `{{.Comm}}` 规则稳定区分，需要补专门命名规则，保证 Grafana 中能单独看到 OpenClaw 进程，而不是只看到通用 `node` 进程。

#### 路径 C：traces / logs 延后

traces / logs 本轮不作为必须交付。

如果后续需要：
- traces 可接 Tempo 或其他 OTLP backend
- logs 可接 Loki 或其他日志平台

但这些都不应阻塞 `Issue #27` 的最小指标接入闭环。

## 10. 是否需要额外 collector / bridge 或聚合转换

PM 显式判断：**需要一层轻量 collector / bridge，但不需要重型新系统，也不需要改 OpenClaw 核心业务逻辑。**

原因：
1. OpenClaw 当前导出的是 OTLP/HTTP，不是 Prometheus 原生 `/metrics` 端点。
2. 当前本地观测栈没有现成的 OpenClaw OTLP 接收端。
3. 为了接入 Grafana 而直接改 OpenClaw 代码增加另一套 Prometheus 指标，性价比低且风险更高。
4. 轻量 collector / bridge 既能承接本轮 metrics，也给未来 traces / logs 扩展预留空间。

### 10.1 这层 collector / bridge 应负责什么

应该负责：
- 接收 OpenClaw 发出的 OTLP/HTTP telemetry
- 将 metrics 转为 Prometheus 可抓取目标
- 保留稳定的 resource / attribute 维度
- 对外只暴露本地回环监听

不应负责：
- 重新实现 OpenClaw 业务逻辑
- 额外生成与 OpenClaw 原生语义不一致的新指标体系
- 在本轮同时承担完整 tracing / logging 平台角色

## 11. OpenClaw 配置变更、合法性校验与回滚约束

本轮实现必须遵守以下安全约束。

### 11.1 变更范围约束

只允许触及与观测接入直接相关的配置：
- `plugins.allow`
- `plugins.entries.diagnostics-otel`
- `diagnostics`
- 如有必要的 collector 本地配置与 Prometheus scrape 配置

不应顺手改动：
- 模型路由
- 会话调度
- channel 行为
- ACP/cron 等无关配置

### 11.2 推荐的首轮启用策略

首轮建议：
- `diagnostics.enabled = true`
- `diagnostics.otel.enabled = true`
- `diagnostics.otel.metrics = true`
- `diagnostics.otel.traces = false`
- `diagnostics.otel.logs = false`
- `diagnostics.otel.protocol = "http/protobuf"`
- `diagnostics.otel.serviceName = "openclaw-gateway"`

原因：
1. metrics 是本轮验收主目标。
2. traces / logs 会扩大部署面和排障面。
3. 先拿到稳定指标，再考虑追加 traces / logs，更符合“不能影响正常使用”的要求。

### 11.3 collector / endpoint 约束

collector / bridge 应：
- 优先监听 `127.0.0.1`
- 不直接暴露到公网
- 与现有 Prometheus 一样采用本地 pull 模式接入

### 11.4 合法性校验与重启验证

实现侧至少应完成：

1. 修改前先读取并确认相关 schema / config 子树。
2. 准备 collector / bridge 并先本地验证可监听。
3. 写入 OpenClaw OTel 相关配置。
4. 做配置合法性检查。
5. 重启 OpenClaw。
6. 验证 OpenClaw 正常启动、日志无明显 OTel 配置错误、Prometheus scrape target 正常、Grafana 可见指标。

### 11.5 回滚策略

若接入后出现异常，应能快速回滚：

1. 关闭 `diagnostics.otel.enabled` 或恢复修改前配置。
2. 保留 `diagnostics.enabled` 与否按原配置恢复。
3. 下线 collector / bridge 的 Prometheus scrape job。
4. 重启 OpenClaw 后确认服务恢复。

回滚目标不是“保留观测能力”，而是“优先恢复 OpenClaw 正常工作”。

## 12. Grafana 信息结构

### 12.1 Folder 建议

建议在统一蓝图下新增：

`AT | 11 平台 | OpenClaw`

原因：
1. OpenClaw 更接近平台 / gateway 观测对象，而不是单一业务项目。
2. 它与主机系统层有关联，但仍然有独立的 runtime / usage 视图，不宜塞回纯主机目录。

### 12.2 Dashboard 建议

#### Dashboard 1
`AT | OpenClaw | Runtime | Overview`

目标：给用户一个 OpenClaw 总体运行总览。

建议面板：
1. 最近 24h token 总量
2. 最近 24h cost 总量
3. 当前 queue depth
4. 当前 stuck session 数量
5. run duration P95
6. OpenClaw 进程 CPU / 内存
7. session state 分布
8. queue wait 趋势

#### Dashboard 2
`AT | OpenClaw | Usage | Model & Message Flow`

目标：看模型调用与消息流量质量。

建议面板：
1. provider / model token 分布
2. provider / model cost 分布
3. run duration 趋势
4. message processed by outcome
5. message queued 趋势
6. context token 分布

#### Dashboard 3
`AT | OpenClaw | Queue | Sessions & Channels`

目标：定位 queue/session/channel 侧异常。

建议面板：
1. queue enqueue / dequeue 速率
2. queue depth by lane
3. queue wait by lane
4. session stuck 趋势
5. webhook received / error / duration
6. channel / outcome 异常分布

## 13. 视觉与交互要求

1. 第一行优先展示：token、cost、queue depth、stuck sessions、P95 run duration、OpenClaw 进程 CPU / 内存。
2. 第二行展示趋势图：run duration、message flow、queue wait。
3. 第三行展示分布和 TopN：provider/model、lane、channel、outcome。
4. 需要支持 `provider`、`model`、`channel`、`lane` 过滤。
5. queue depth、queue wait、stuck session 相关面板必须具备明显阈值色语义。
6. 若 webhook 信号在当前环境为空，应明确展示“当前环境未启用该类 channel”而不是简单出现误导性的 No Data。

## 14. 对实现侧的明确输入

`Issue #27` 在实现时应以本文为输入，至少完成以下事情：

1. 新增一层轻量 OTLP 接收与 Prometheus 暴露组件，承接 OpenClaw metrics。
2. 在现有 Prometheus 配置中增加 OpenClaw collector / bridge scrape job。
3. 只启用首轮必要的 OpenClaw metrics 导出，不把 traces / logs 扩到本轮必需范围。
4. 复用 node-exporter / process-exporter 获取宿主机和 OpenClaw 进程系统指标。
5. 如果现有 `process-exporter` 规则无法稳定识别 OpenClaw 进程，补齐专门匹配规则。
6. 在 Grafana 中建立 `AT | 11 平台 | OpenClaw` 下的 dashboard。
7. 让用户能直接回答：
   - OpenClaw 现在有没有正常处理工作。
   - queue / session 是否在积压或卡住。
   - 哪个 provider / model 的 token、cost、耗时最高。
   - 问题更像是 OpenClaw 自身资源不足，还是 message / channel / model 侧异常。
8. 提供配置合法性检查、重启验证、回滚验证的证据或步骤。

## 15. PM 显式判断结论

本 issue 当前不需要继续拆分。

原因：
1. 当前 issue 已经是 PM 规格子 issue，本身就是独立验收单元。
2. 现有 OpenClaw 文档与当前 Grafana 底座事实，已经足够形成一份可执行的接入规格。
3. 继续拆分只会把同一份规格再切碎，增加编排噪音，不增加真实价值。
4. 在本轮规格中，关于 collector / bridge、process-exporter 精确识别、以及 traces / logs 延后策略，已经把实现边界说明清楚，足以支撑后续 Dev 落地。

因此，`Issue #26` 在本文产出后即可视为完成，并建议直接关闭；随后由 `Issue #27` 进入 Dev 实现。
