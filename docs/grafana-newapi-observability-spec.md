# NewAPI Grafana 观测规格（Issue #10 PM）

## 1. 文档目标

本文用于定义 `NewAPI` 接入 Grafana 前的 PM 规格基线，回答以下问题：

1. NewAPI 的核心业务流程是什么。
2. 哪些业务指标和系统/运行指标值得纳入 Grafana。
3. 哪些指标已经存在，哪些需要补 exporter / bridge。
4. Grafana 中应该如何分组、命名与组织信息结构。

本文是 `Issue #10` 的直接交付物，供 `Issue #11` 实现使用。

## 2. 事实基础与当前已知线索

基于当前仓库与现有环境，已确认以下事实：

1. `NewAPI` 是一套多模型 / 多渠道的统一 API 网关与管理面，至少覆盖：
   - 用户登录与用户管理
   - Token 管理
   - Channel 管理
   - Usage / Log 查询
   - Topup / Subscription / Billing 相关流程
   - OpenAI-compatible / Responses / Claude / Gemini 等多模型代理能力

2. 代码仓库路径存在于本机：`/root/new-api`

3. 当前代码中已可确认的一批与可观测性直接相关的能力：
   - 后端日志与统计接口：
     - `/api/log/`
     - `/api/log/stat`
     - `/api/log/channel_error_stats`
   - 前端已有 dashboard 相关实现：
     - `web/src/hooks/dashboard/useDashboardData.js`
     - `web/src/components/dashboard/*`
   - 已存在渠道错误率统计面板与错误样本查看能力。

4. 历史线索显示，本机曾经围绕 NewAPI 做过“总体 + 各渠道错误率 + 错误日志样本”的 dashboard / 接口增强，并且在实际运行中确认过：
   - 服务运行正常
   - PostgreSQL 中有渠道与日志数据
   - `/api/log/` 及相关统计路径被作为关键可观测入口使用

5. 当前仓库内尚未发现一份面向 Grafana 的 NewAPI 观测规格文档，因此本 issue 仍然需要显式补齐规格，而不是直接假设已有规范。

## 3. NewAPI 核心业务流程

结合仓库代码与当前功能边界，NewAPI 的核心业务流程可抽象为 4 层。

### 3.1 接入与鉴权流程

目标：让用户或系统可以通过 token / 账号进入平台并发起调用。

关键对象：
- user
- token
- group / permission
- model visibility

关键健康信号：
- 登录是否成功
- token 是否有效
- token 是否被禁用 / 限流
- 用户可见模型是否正常返回

### 3.2 请求代理与渠道路由流程

目标：将客户端请求正确路由到目标模型/渠道并返回结果。

关键对象：
- request
- model
- channel
- upstream endpoint
- status_code / error_code

关键健康信号：
- 请求总量
- 成功量
- 错误量
- 各渠道错误率
- 渠道 bad response / upstream request failed / empty stream 等错误分布
- 模型维度与渠道维度的异常集中度

### 3.3 配额 / 计费 / 消耗流程

目标：记录调用消耗并支撑配额、计费与充值订阅能力。

关键对象：
- quota
- tokens
- topup
- subscription
- billing

关键健康信号：
- 总消耗额度
- token 消耗量
- 请求频率（RPM/TPM）
- 充值 / 订阅链路是否异常
- 用户或 token 的异常消耗模式

### 3.4 运维 / 管理流程

目标：确保渠道、模型、部署、同步与后台任务稳定运行。

关键对象：
- channel sync
- model sync
- deployment
- scheduled tasks
- error logs

关键健康信号：
- 渠道可用率
- 渠道测试结果
- 模型同步是否失败
- 后台任务是否持续运行
- 错误日志开关与错误样本是否可追溯

## 4. 主要用户路径

本轮建议重点围绕以下用户路径设计观测，而不是平均铺开所有功能：

1. **用户发起模型调用**
   - 请求进入 NewAPI
   - 选择模型 / 选择渠道
   - 上游返回成功或失败
   - 记录 quota / token 消耗

2. **管理员查看渠道健康与错误分布**
   - 查看渠道错误率
   - 查看错误码分布
   - 查看错误样本
   - 判断是否需要切换渠道或排障

3. **管理员查看整体平台运行情况**
   - 总请求量、总消耗、RPM / TPM
   - 模型使用分布
   - 用户/Token 维度异常

4. **管理员处理计费 / 配额异常**
   - topup / subscription / quota 链路是否异常
   - 消耗与计费是否一致

## 5. 关键健康信号

### 5.1 顶层业务健康信号

必须优先进入 Grafana 的信号：

1. 请求成功率
2. 各渠道错误率
3. 总请求量趋势
4. token 消耗趋势
5. quota 消耗趋势
6. RPM / TPM 趋势
7. 错误码分布
8. 渠道维度错误集中情况

### 5.2 二级业务健康信号

建议纳入：

1. 模型维度请求量 / 错误量
2. 用户维度消耗异常
3. token 维度消耗异常
4. topup / subscription 成功与失败量
5. 渠道测试与同步失败数

## 6. 指标清单

以下指标按“优先级 + 指标目的 + 单位 + 标签建议”定义。

### 6.1 P0 核心业务指标

#### 1. newapi_requests_total
- 目的：观察平台总请求吞吐
- 单位：count
- 标签建议：`env`, `project`, `system`, `service`, `model`, `channel_id`, `channel_name`, `status_family`

#### 2. newapi_request_success_total
- 目的：观察成功请求量
- 单位：count
- 标签建议：`model`, `channel_id`, `channel_name`

#### 3. newapi_request_error_total
- 目的：观察失败请求量
- 单位：count
- 标签建议：`model`, `channel_id`, `channel_name`, `status_code`, `error_code`

#### 4. newapi_channel_error_rate
- 目的：直接观察各渠道错误率，便于定位故障渠道
- 单位：ratio / percent
- 标签建议：`channel_id`, `channel_name`
- 说明：已有 `/api/log/channel_error_stats` 能力，应优先复用

#### 5. newapi_tokens_consumed_total
- 目的：观察 token 消耗规模
- 单位：tokens
- 标签建议：`model`, `channel_id`, `username?`, `token_name?`

#### 6. newapi_quota_consumed_total
- 目的：观察 quota 消耗规模
- 单位：quota
- 标签建议：`model`, `channel_id`, `username?`, `token_name?`

#### 7. newapi_rpm
- 目的：观察最近一分钟请求压力
- 单位：req/min
- 标签建议：`service`

#### 8. newapi_tpm
- 目的：观察最近一分钟 token 压力
- 单位：tokens/min
- 标签建议：`service`

### 6.2 P1 重点扩展指标

#### 9. newapi_requests_by_model
- 目的：识别热门模型与异常模型
- 单位：count
- 标签建议：`model`

#### 10. newapi_errors_by_error_code
- 目的：识别 empty_stream / upstream_request_failed / bad_response_status_xxx 等问题模式
- 单位：count
- 标签建议：`error_code`, `status_code`

#### 11. newapi_channel_health_score
- 目的：提供渠道级综合健康分
- 单位：score / percent
- 标签建议：`channel_id`, `channel_name`
- 说明：若没有现成字段，可由成功率、错误率、近5分钟错误样本聚合推导

#### 12. newapi_topup_events_total
- 目的：观察充值流程活跃度与异常
- 单位：count
- 标签建议：`status`, `payment_gateway`

#### 13. newapi_subscription_events_total
- 目的：观察订阅流程活跃度与异常
- 单位：count
- 标签建议：`status`, `provider`

### 6.3 P2 运行与系统指标

#### 14. newapi_process_cpu_percent
- 目的：观察 NewAPI 进程 CPU 压力
- 单位：percent
- 标签建议：`instance`, `service`

#### 15. newapi_process_memory_bytes
- 目的：观察 NewAPI 进程内存使用
- 单位：bytes
- 标签建议：`instance`, `service`

#### 16. newapi_process_open_fds
- 目的：观察进程资源泄漏迹象
- 单位：count
- 标签建议：`instance`

#### 17. newapi_db_connection_health
- 目的：观察 PostgreSQL / SQLite 实际连接状态
- 单位：state / count
- 标签建议：`db_type`

#### 18. newapi_error_log_enabled
- 目的：确认错误日志能力是否开启
- 单位：bool / 0|1
- 标签建议：`service`

## 7. 标签规范

遵循统一蓝图，NewAPI 本轮建议基础标签如下：

- `env`
- `project=agent-team-grafana`
- `system=newapi`
- `service=new-api`
- `instance`
- `job`
- `layer`

业务扩展标签建议：
- `model`
- `channel_id`
- `channel_name`
- `status_code`
- `error_code`
- `token_name`
- `username`
- `payment_gateway`

### 禁止直接作为 label 的高基数字段

以下字段禁止直接长期作为 Prometheus label：
- `request_id`
- 原始错误全文
- 完整 upstream URL
- 用户输入 prompt
- 任意长 token 值

这些信息适合作为：
- 错误样本明细
- 日志跳转入口
- drill-down 详情，而不是时序 label

## 8. 采集方式建议

### 8.1 优先级

1. **优先复用现有 API / 后端统计能力**
2. **其次补最小 Prometheus exporter / bridge**
3. **最后才考虑直接读日志文件**

### 8.2 推荐采集路径

#### 路径 A：复用已有统计 API
可直接评估复用：
- `/api/log/stat`
- `/api/log/channel_error_stats`
- dashboard 现有数据接口

适合先导出的指标：
- quota
- rpm
- tpm
- channel error rate
- error code distribution

#### 路径 B：补充 Prometheus exporter / bridge
如果现有 API 不是 Prometheus 格式，则建议在 `Issue #11` 中补一层轻量 bridge，把核心统计转换成 Prometheus 指标。

适合 bridge 的指标：
- `newapi_channel_error_rate`
- `newapi_errors_by_error_code`
- `newapi_tokens_consumed_total`
- `newapi_quota_consumed_total`

#### 路径 C：系统层复用进程指标
运行指标优先通过已有主机观测栈获取：
- process-exporter
- node-exporter

这样不需要在 NewAPI 内再重复造系统指标轮子。

## 9. Grafana 信息结构

### 9.1 Folder
按统一蓝图落入：

`AT | 21 项目 | NewAPI`

### 9.2 Dashboard 建议

#### Dashboard 1
`AT | NewAPI | Business | Overview`

目标：给运营/管理员一个总览页。

建议面板：
1. 总请求量
2. 成功率
3. 总 token 消耗
4. 总 quota 消耗
5. RPM
6. TPM
7. 按模型请求量趋势
8. 按渠道错误率排行

#### Dashboard 2
`AT | NewAPI | Runtime | Channel Health`

目标：定位渠道与错误问题。

建议面板：
1. 各渠道错误率 TopN
2. 各错误码分布
3. channel error samples 数量趋势
4. bad_response / upstream_request_failed / empty_stream 细分趋势
5. 渠道健康分或告警列表

#### Dashboard 3（可选）
`AT | NewAPI | Runtime | Process & Dependencies`

目标：观察 NewAPI 进程及依赖。

建议面板：
1. new-api 进程 CPU
2. new-api 进程内存
3. DB 连接健康
4. 错误日志开关状态
5. 关键依赖可达性

## 10. 视觉与交互要求

1. 第一行优先展示：成功率、请求量、错误率、RPM、TPM、token 消耗
2. 第二行展示趋势图
3. 第三行展示 TopN 渠道 / 错误码 / 模型分布
4. 需要支持 `instance`、`model`、`channel_id`、`channel_name` 过滤
5. 错误率与错误码面板必须带阈值色语义

## 11. 对实现侧的明确输入

`Issue #11` 在实现时应以本文为输入，至少完成以下事情：

1. 复用或桥接现有 `/api/log/stat` 与 `/api/log/channel_error_stats` 统计能力
2. 把高价值业务指标转换为 Prometheus 可抓取形式
3. 复用主机进程观测栈获取 new-api 的系统/运行指标
4. 在 Grafana 中建立 `AT | 21 项目 | NewAPI` 下的 dashboard
5. 保留错误样本 drill-down 能力，但不要把高基数原始字段做成 Prometheus label

## 12. PM 显式判断结论

本 issue 当前不需要继续拆分。

原因：
1. 当前 issue 已经是 PM 规格子 issue，本身就是独立验收单元。
2. 现有仓库与历史线索已经足够形成一份可执行的规格说明。
3. 继续拆分只会把同一份规格再切碎，增加编排噪音，不增加真实价值。

因此，`Issue #10` 在本文产出后即可视为完成，并建议直接关闭；随后由 `Issue #11` 进入 Dev 实现。