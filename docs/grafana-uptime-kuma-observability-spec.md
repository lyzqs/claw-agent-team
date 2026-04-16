# Uptime Kuma Grafana 观测规格（Issue #14 PM）

## 1. 文档目标

本文定义 `Uptime Kuma` 接入 Grafana 前的 PM 规格基线，用于明确：

1. Uptime Kuma 当前已具备哪些监控能力与数据来源。
2. 哪些站点/服务健康信号应被纳入 Grafana。
3. 哪些信息可以直接复用，哪些需要 bridge / 聚合转换。
4. 在 Grafana 中应该如何分组、命名与组织信息结构。

本文是 `Issue #14` 的直接交付物，供 `Issue #15` 实现使用。

## 2. 事实基础与当前已知线索

基于当前环境与已知记忆，已确认以下事实：

1. 本机已实际部署并使用 Uptime Kuma。

2. 历史已确认的监控分组包括：
   - `OpenClaw`
   - `NewAPI`
   - `PaperClip`
   - `Kuma`
   - `LAN`
   - `Local`

3. 历史已确认的监控对象至少包括：
   - `192.168.1.2` 的 ESXi HTTPS
   - `192.168.1.5` 的 OpenWrt 80 端口与 `16601` 端口
   - 本机 `8788`、`9090`、`9091`、`5432`、`6379`、`11434` 等服务

4. 历史已确认的一些关键运行事实：
   - Telegram 告警当前绑定在子监控项而不是组。
   - 大部分子项使用 `maxretries=5`、`retry_interval=60`。
   - `NewAPI` 相关两条监控已被单独调成更敏感：`maxretries=3`、`retry_interval=60`。
   - OpenWrt Web 因 LuCI 重定向后返回 403，不适合直接做 HTTP 可用性检查，改成 TCP 80 端口监控更稳定。
   - Kuma 的 `socket.io` polling 在 nginx 反代下总体可正常握手，之前出现的报错更像瞬时重连提示，而不是长期反代故障。

5. 当前还没有一份面向统一 Grafana 蓝图的 Uptime Kuma 观测规格文档，因此本 issue 需要显式补齐该规格。

## 3. Uptime Kuma 的系统定位

Uptime Kuma 在本轮里不是普通业务系统，而是**合成监控与可用性观测来源**。

它的职责不是产出业务交易指标，而是回答：
- 哪些站点 / 服务现在在线或离线。
- 响应时间是否异常。
- 某条监控是否频繁抖动。
- 某些证书是否接近过期。
- 告警是否集中在某个分组或某类服务。

因此它在统一蓝图中更接近：
- L3 业务 / 合成监控层
- 运维健康层的总览入口

而不是主机资源层或业务流程层。

## 4. 核心观测对象

本轮建议把 Uptime Kuma 的观测对象分成 4 类。

### 4.1 公网/外部入口可用性

目标：判断对外暴露的入口是否在线。

示例：
- OpenClaw 外部入口
- NewAPI 外部入口
- PaperClip 外部入口
- 其他公开站点或 API

关键健康信号：
- 当前状态（up/down）
- 响应时间
- 连续失败次数
- 最近恢复时间

### 4.2 局域网基础设施可用性

目标：判断 LAN 内关键基础设施是否在线。

示例：
- ESXi HTTPS
- OpenWrt 管理入口或端口
- 内网关键网关 / 服务

关键健康信号：
- 状态
- 响应时间 / TCP 连接耗时
- 失败重试后的最终状态

### 4.3 本机关键服务可用性

目标：判断本机关键依赖是否在线。

示例：
- PostgreSQL 5432
- Redis 6379
- Prometheus 9090 / 9091
- Arena dashboard 8788
- Ollama 11434

关键健康信号：
- 状态
- 响应时间
- 抖动情况
- 分组级异常密度

### 4.4 监控系统自身健康

目标：保证 Kuma 本身是可信的。

示例：
- Kuma 自身 UI
- 反向代理链路
- socket.io / polling 可达性
- 告警通道绑定方式

关键健康信号：
- Kuma 自身在线
- 监控结果是否持续刷新
- 告警是否发送到正确粒度

## 5. 关键用户路径

本轮 Grafana 观测应优先支持以下用户路径：

1. **我现在有哪些服务挂了**
   - 按分组查看 down 项
   - 看到 down 数量、最近失败时间和持续时长

2. **哪些服务最近在抖**
   - 看最近响应时间趋势
   - 看失败/恢复频率
   - 看某类服务是否集中告警

3. **为什么同类监控结果差异大**
   - 是监控方式（HTTP/TCP）不合适
   - 是重试策略不同
   - 是本身服务波动

4. **当前告警是不是太钝或太敏感**
   - 关注 NewAPI 等更敏感分组
   - 区分 maxretries=3 与 5 的配置效果

## 6. 关键健康信号

### 6.1 顶层健康信号

必须优先进入 Grafana 的信号：

1. 当前 monitor 总数
2. 当前 up 数量
3. 当前 down 数量
4. 当前 degraded / 波动数
5. 按分组的 down 数量
6. 平均响应时间
7. 最近 24h 可用率
8. 证书接近过期数量（若已存在对应监控能力）

### 6.2 二级健康信号

建议纳入：

1. 按 monitor type（HTTP / TCP / Ping 等）分布
2. 按 group 的错误集中度
3. 最近恢复次数
4. 最近失败次数
5. 告警敏感度差异（如 NewAPI 组 vs 其他组）

## 7. 指标清单

以下按“优先级 + 指标目的 + 单位 + 标签建议”定义。

### 7.1 P0 核心指标

#### 1. kuma_monitors_total
- 目的：观察监控总量
- 单位：count
- 标签建议：`env`, `project`, `system`, `service`, `group`, `monitor_type`

#### 2. kuma_monitor_status
- 目的：反映每个监控项当前状态
- 单位：0|1 或枚举映射
- 标签建议：`group`, `monitor_name`, `monitor_type`
- 说明：最终在 Grafana 中可聚合为 up/down 数量

#### 3. kuma_monitors_up_total
- 目的：观察当前在线监控数
- 单位：count
- 标签建议：`group`

#### 4. kuma_monitors_down_total
- 目的：观察当前离线监控数
- 单位：count
- 标签建议：`group`

#### 5. kuma_monitor_response_time_ms
- 目的：观察各监控项响应时间
- 单位：ms
- 标签建议：`group`, `monitor_name`, `monitor_type`

#### 6. kuma_group_availability_ratio
- 目的：观察分组级可用率
- 单位：ratio / percent
- 标签建议：`group`

#### 7. kuma_monitor_retry_policy
- 目的：显式化敏感度配置差异
- 单位：info / gauge
- 标签建议：`group`, `monitor_name`, `max_retries`, `retry_interval`

#### 8. kuma_group_alerting_scope
- 目的：区分告警绑定是子监控项还是分组
- 单位：info
- 标签建议：`group`, `alert_scope`

### 7.2 P1 扩展指标

#### 9. kuma_monitor_failures_total
- 目的：观察一定时间窗口内失败次数
- 单位：count
- 标签建议：`group`, `monitor_name`

#### 10. kuma_monitor_recoveries_total
- 目的：观察恢复次数
- 单位：count
- 标签建议：`group`, `monitor_name`

#### 11. kuma_group_avg_response_time_ms
- 目的：观察分组平均响应时间
- 单位：ms
- 标签建议：`group`

#### 12. kuma_cert_expiry_days
- 目的：观察证书剩余天数
- 单位：days
- 标签建议：`group`, `monitor_name`
- 说明：仅对 HTTPS/证书监控适用

#### 13. kuma_monitor_flap_score
- 目的：识别频繁抖动监控
- 单位：score / count
- 标签建议：`group`, `monitor_name`

### 7.3 P2 系统与运行指标

#### 14. kuma_process_cpu_percent
- 目的：观察 Kuma 自身 CPU 压力
- 单位：percent
- 标签建议：`instance`, `service`

#### 15. kuma_process_memory_bytes
- 目的：观察 Kuma 自身内存使用
- 单位：bytes
- 标签建议：`instance`, `service`

#### 16. kuma_proxy_health
- 目的：观察 Kuma 反向代理链路健康
- 单位：0|1
- 标签建议：`instance`, `service`

#### 17. kuma_socket_polling_health
- 目的：观察 socket/polling 是否长期异常
- 单位：0|1
- 标签建议：`instance`

## 8. 标签规范

遵循统一蓝图，Uptime Kuma 本轮建议基础标签如下：

- `env`
- `project=agent-team-grafana`
- `system=uptime-kuma`
- `service=uptime-kuma`
- `instance`
- `job`
- `layer`

业务扩展标签建议：
- `group`
- `monitor_name`
- `monitor_type`
- `alert_scope`
- `max_retries`
- `retry_interval`

### 禁止直接作为 label 的高基数字段

以下字段不应直接长期作为 Prometheus label：
- 完整 URL 查询串
- 任意原始错误全文
- socket.io 原始异常日志
- 未受控的 endpoint path

这些内容更适合：
- drill-down 明细
- 错误样本
- 运维排障日志

## 9. 采集方式建议

### 9.1 优先级

1. **优先复用 Uptime Kuma 已有统计/导出能力**
2. **如原生导出不满足，再补 exporter / bridge**
3. **仅在必要时做聚合层**

### 9.2 推荐采集路径

#### 路径 A：复用 Kuma 原生能力
优先评估：
- 现有 monitor 状态
- 响应时间历史
- 可用率
- 证书信息
- group / monitor 元数据

若 Kuma 原生已有 Prometheus 或结构化 API 输出，应直接复用，不重复造轮子。

#### 路径 B：补 bridge/exporter
如果原生输出不能满足统一 Grafana 需求，建议在 `Issue #15` 中补一层轻量 bridge，把以下信息转成 Prometheus 指标：
- 分组聚合状态
- retry policy 差异
- 告警绑定粒度
- 抖动分数

#### 路径 C：系统层复用进程指标
Kuma 自身的 CPU / 内存等运行指标优先通过已有主机观测栈获取：
- process-exporter
- node-exporter

## 10. 是否需要额外 exporter / 桥接或聚合转换

PM 显式判断：**大概率需要一层轻量 bridge / 聚合转换，但不需要重型新系统。**

原因：
1. 本轮不仅要展示单 monitor 状态，还要展示 group 视角与统一蓝图下的聚合视图。
2. 告警粒度、retry policy 差异、分组可用率等信息，通常更适合经过一层聚合再进入 Grafana。
3. 这层 bridge 只需要做“结构化整理 + Prometheus 暴露”，不需要再造第二套监控系统。

### 明确边界

bridge / 聚合层应负责：
- 把 group / monitor 元信息整理成稳定标签
- 产出分组汇总指标
- 把 retry policy / alert scope 等配置性信息显式化

bridge / 聚合层不应负责：
- 替代 Uptime Kuma 的实际探测执行
- 重新实现 HTTP/TCP/Ping 检查逻辑
- 成为新的监控控制面

## 11. Grafana 信息结构

### 11.1 Folder
按统一蓝图落入：

`AT | 30 Ops | Uptime-Kuma`

### 11.2 Dashboard 建议

#### Dashboard 1
`AT | Uptime-Kuma | Synthetic | Overview`

目标：给用户一个全局服务健康总览。

建议面板：
1. 当前 up/down 总数
2. 当前 down 分组数
3. 最近 24h 平均可用率
4. 平均响应时间
5. 分组状态分布
6. down monitor 列表

#### Dashboard 2
`AT | Uptime-Kuma | Synthetic | Group Health`

目标：看各组健康差异。

建议面板：
1. 按 group 的 up/down 数量
2. 各 group 平均响应时间
3. 各 group 可用率
4. 各 group 告警范围配置
5. 各 group retry policy 分布

#### Dashboard 3
`AT | Uptime-Kuma | Synthetic | Monitor Details`

目标：查看具体 monitor 的健康细节。

建议面板：
1. monitor 响应时间 TopN
2. 最近失败次数 TopN
3. 最近恢复次数 TopN
4. flap / 抖动 monitor 列表
5. 证书即将过期列表

## 12. 视觉与交互要求

1. 第一行优先展示：up 总数、down 总数、平均可用率、平均响应时间
2. 第二行展示按 group 的趋势与分布
3. 第三行展示具体 monitor 细节与异常 TopN
4. 需要支持 `group`、`monitor_type`、`monitor_name` 过滤
5. down / 证书风险 / 高频抖动面板必须具备明显阈值色语义

## 13. 对实现侧的明确输入

`Issue #15` 在实现时应以本文为输入，至少完成以下事情：

1. 评估并复用 Kuma 原生可导出的状态与响应时间数据
2. 如原生输出不足，补轻量 bridge，把 group 汇总指标和配置性信息转换为 Prometheus 可抓取格式
3. 在 Grafana 中建立 `AT | 30 Ops | Uptime-Kuma` 下的 dashboard
4. 让用户能直接回答：
   - 现在哪些服务挂了
   - 哪些分组最不稳定
   - 哪些监控在抖
   - 某些关键服务是否需要更敏感或更稳妥的告警策略

## 14. PM 显式判断结论

本 issue 当前不需要继续拆分。

原因：
1. 当前 issue 已经是 PM 规格子 issue，本身就是独立验收单元。
2. 现有环境事实已经足够形成一份可执行的 Uptime Kuma 观测规格。
3. 继续拆分只会把规格文档再切碎，增加编排噪音，不增加真实价值。

因此，`Issue #14` 在本文产出后即可视为完成，并建议直接关闭；随后由 `Issue #15` 进入 Dev 实现。