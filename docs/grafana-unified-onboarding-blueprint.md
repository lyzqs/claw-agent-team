# Grafana 统一接入蓝图与实施顺序（Issue #4 PM）

## 1. 文档定位

本文是 `agent-team-grafana` 项目的统一接入蓝图，用于约束后续各子 issue 的接入边界、命名方式、展示结构和实施顺序，避免各项目各自接入后出现重复建设、数据源混乱和视觉风格失控。

角色边界说明：
- 本文只定义蓝图、范围、顺序与验收口径。
- 本文不替代 Dev / Ops 的具体实现。
- `Issue #3` 继续作为总编排父 issue。
- `Issue #4` 负责输出统一蓝图。
- `Issue #5` 到 `Issue #9` 负责按系统分别落地。

当前 PM 显式判断：**当前分解粒度已经足够，不再继续新增子 issue。**
原因是：
1. 当前已经按系统拆为可独立验收单元。
2. `Issue #3` 已承担“等待子 issue 收口后统一 review”的父 issue 职责。
3. `Issue #4` 本身是单一规划交付物，不应再被拆成更碎的无效 planning issue。

## 2. 本轮纳入范围

本轮统一接入范围至少覆盖以下 5 类对象：

| 对象 | 目标 | 指标层级 | 首选采集路径 |
| --- | --- | --- | --- |
| 主机 / 系统 | 建立整套 Grafana 观测基础底座 | 平台 + 系统 | `node-exporter` + `process-exporter` + Prometheus |
| Agent Team | 观察 worker / queue / issue 流转与运行健康 | 业务 + 系统 | 应用 `/metrics` 或最小 exporter 适配 |
| NewAPI | 观察 API 请求、错误率、延迟与系统资源 | 业务 + 系统 | 应用 Prometheus 指标优先，缺失时补 exporter |
| Arena 股票竞技场 | 观察业务运行状态、关键流程吞吐与资源使用 | 业务 + 系统 | 应用 Prometheus 指标优先，缺失时补 exporter |
| Uptime Kuma | 观察站点可用性、延迟、证书与探测结果 | 运维 / 合成监控 | Uptime Kuma 指标端点或桥接 exporter |

本轮不纳入：
- 日志平台统一接入
- 分布式 tracing
- 多环境（staging / prod）治理
- 报警规则体系全面治理

这些能力如后续成为独立目标，再单独立 issue，不混入本轮最小接入闭环。

## 3. 统一观测架构蓝图

### 3.1 总体原则

1. **单 Grafana 主入口**
   - 本轮所有可视化统一进入同一套本地 Grafana。
   - 不为每个项目单独起 Grafana 实例。

2. **单主 Prometheus 数据源**
   - 统一使用本地主 Prometheus 作为主时序数据源。
   - 本轮默认数据源名称固定为：`prometheus-local-main`。

3. **先统一系统底座，再接业务指标**
   - 主机 / 系统指标是所有项目接入的公共基础。
   - 每个项目先满足“可采集、可命名、可筛选”，再谈个性化 dashboard。

4. **优先 pull，避免项目各自造轮子**
   - 优先由 Prometheus 抓取 `/metrics`。
   - 仅当项目无法直接暴露 Prometheus 指标时，才新增最小 exporter / bridge。

5. **标签治理优于 dashboard 堆砌**
   - 同一类指标优先统一 label 规范。
   - 不允许通过多个重复 dashboard 去掩盖底层指标命名不一致问题。

### 3.2 当前底座复用策略

本项目已有 `Issue #1` 形成的主机观测底座交付，可作为本轮统一底座参考：
- `deploy/grafana/install_local_grafana_stack.sh`
- `deploy/grafana/prometheus/prometheus.yml`
- `deploy/grafana/process-exporter/process-exporter.yml`
- `deploy/grafana/dashboards/local-host-observability.json`
- `docs/grafana-local-observability-stack.md`

因此后续 `Issue #9` 不从零设计 Grafana 底座，而是**以现有主机观测栈为基础，补齐统一命名、统一 folder 和统一 datasource 约束**。

## 4. Grafana 分组方案、Folder 结构与命名规范

### 4.1 Folder 结构

考虑 Grafana folder 在实际使用中更适合扁平命名与排序，本轮采用**带前缀的扁平 folder 方案**，而不是依赖多层嵌套：

1. `AT | 00 规范`
2. `AT | 10 平台 | 主机系统`
3. `AT | 20 项目 | 智能体团队`
4. `AT | 21 项目 | NewAPI`
5. `AT | 22 项目 | Arena`
6. `AT | 30 运维 | Uptime Kuma`
7. `AT | 90 总览 | 跨项目`

说明：
- `AT` 代表本轮统一的 Agent Team Grafana 目录前缀。
- `00 规范` 仅放命名约定、说明型 dashboard 或基线示意，不放具体系统业务图。
- `90 总览 | 跨项目` 预留给 `Issue #3` 在子 issue 收口后的总览检查与统一汇总视图。

### 4.2 Dashboard 命名规范

统一命名格式：

`AT | <System> | <Layer> | <View>`

示例：
- `AT | Host-System | System | Overview`
- `AT | Host-System | Process | TopN`
- `AT | NewAPI | Business | Overview`
- `AT | Arena | Business | Match Health`
- `AT | Agent-Team | Runtime | Workers`
- `AT | Uptime-Kuma | Synthetic | Overview`

约束：
- 一个 dashboard 只表达一个主视角，不混合多个目标系统。
- 同一系统的业务层与系统层可拆成两个 dashboard，也可在同一 dashboard 分区展示，但命名必须能看出主视图。
- 禁止使用“临时看板”“测试面板”“新版2”这类不可维护命名。

### 4.3 Datasource 命名规范

- 主 Prometheus：`prometheus-local-main`
- 若未来引入额外数据源，命名格式：`<type>-<scope>-<name>`
- 本轮禁止各子 issue 私自引入单独 Grafana datasource 命名体系。

## 5. 视觉基线（Visual Baseline）

所有子 issue 的 dashboard 需要遵守统一视觉基线，至少包含以下规则：

### 5.1 版式基线

1. 第一行放 4 到 6 个关键 stat 面板，用于快速判断当前状态。
2. 第二行放时间序列趋势图，默认查看最近 6 小时，并兼容切换到 24 小时 / 7 天。
3. 第三行放 TopN、表格或分布图，用于下钻定位。
4. 同一 dashboard 内从上到下遵循：**结论 -> 趋势 -> 明细**。

### 5.2 单位与颜色基线

- CPU：百分比（0-100%）
- 内存 / 存储：IEC bytes
- 延迟：优先毫秒（ms）
- 吞吐：req/s、ops/s、jobs/min 等清晰单位
- 可用率：百分比

颜色基线：
- 绿色：正常
- 黄色：接近阈值
- 红色：超阈值 / 风险
- 蓝色系：中性趋势

### 5.3 阈值基线

无业务特例时默认阈值建议：
- CPU / 内存 stat：70% 警戒，85% 风险
- 错误率：按各项目业务约束定义，但必须在 dashboard 上显式可见
- 延迟：按系统 SLO 单独设定，不能只展示绝对值而没有阈值语义

### 5.4 交互基线

所有项目 dashboard 统一预留以下变量能力（如指标源支持）：
- `env`
- `instance`
- `job`
- `project`

如果某子系统没有这些 label，需要在其子 issue 中补齐或做兼容映射，而不是直接跳过。

## 6. 业务指标与系统指标的分层原则

### 6.1 分层定义

本轮统一使用四层模型：

#### L0. 观测平台层
关注 Grafana / Prometheus / exporter 自身是否健康。

示例：
- Prometheus target 抓取是否成功
- exporter 是否在线
- Grafana 数据源是否可读

#### L1. 主机 / 系统层
关注宿主机和进程运行态。

示例：
- CPU 使用率
- 内存使用率
- 磁盘 / 网络
- Top 进程 CPU / RSS
- 进程数、重启、存活性

#### L2. 服务 / Runtime 层
关注单个应用或服务本身的技术运行健康。

示例：
- 请求吞吐
- P95 / P99 延迟
- 错误率
- 队列积压
- worker 成功 / 失败

#### L3. 业务 / 合成监控层
关注业务目标与最终对外可用性。

示例：
- NewAPI 的关键调用量 / 错误率
- Arena 的比赛 / 任务流程健康
- Agent Team 的 issue 流转吞吐
- Uptime Kuma 的监控结果、响应时间、证书有效期

### 6.2 分层约束

- L1 与 L3 不得互相替代。
- 业务正常不代表系统稳定，系统稳定也不代表业务健康，必须分层展示。
- 每个项目至少要能回答两个问题：
  1. 系统是否健康运行？
  2. 业务目标是否正常完成？

## 7. 采集方式、Exporter 策略与标签规范

### 7.1 采集方式优先级

统一优先级如下：

1. **应用直接暴露 Prometheus 指标**
2. **项目侧新增最小 exporter / bridge**
3. **复用现有 exporter（如 node-exporter、process-exporter）**
4. **临时脚本导出** 仅作为过渡，不应成为长期标准

### 7.2 各系统策略

#### 主机 / 系统指标
- 使用 `node-exporter` + `process-exporter`
- 统一进入 `prometheus-local-main`
- 作为所有后续项目 dashboard 的公共基线

#### NewAPI
- 优先要求应用直接暴露 HTTP / runtime / domain 指标
- 若现有服务无 `/metrics`，允许在该子 issue 中补最小 exporter
- 不要把业务日志解析脚本直接当长期指标源

#### Arena 股票竞技场
- 优先暴露业务流程指标 + runtime 指标
- 对高基数字段保持谨慎，例如原始用户 ID、原始订单 ID、无限股票代码集合，不允许直接作为 label 泛滥写入

#### Agent Team
- 关注 workflow / queue / attempt / worker 健康
- 指标要能区分系统吞吐与失败 / 重试，不仅仅展示 CPU / 内存

#### Uptime Kuma
- 作为合成监控来源，关注站点在线状态、响应时间、告警状态、证书等
- 若其原生指标粒度不足，可补桥接 exporter，但不要因此破坏统一 label 命名

### 7.3 Label 规范

本轮统一要求的基础 label：
- `env`
- `project`
- `system`
- `service`
- `instance`
- `job`
- `layer`

推荐补充：
- `component`
- `owner`
- `queue`
- `task_type`
- `route`
- `method`
- `status_code`

规则：
1. 环境信息放 label，不放进 metric name。
2. 系统名放 label，不依赖 dashboard 标题硬编码。
3. 禁止高基数字段直接进 label，例如随机 request_id、user_id、完整 URL query。
4. 同一语义必须统一字段名，例如统一使用 `project`，不要一处叫 `project`、一处叫 `app`。

## 8. 子 issue 实施顺序、依赖关系与验收口径

### 8.1 实施顺序

建议顺序如下：

#### 阶段 A：蓝图定版
- `Issue #4` 制定统一蓝图、命名规则、分组结构、指标分层和顺序。
- 输出物：本文。

#### 阶段 B：公共底座落位
- `Issue #9` 主机总体指标与进程级指标接入 Grafana。
- 目标：把已有本地观测栈收敛成统一基础底座，并验证 `prometheus-local-main`、基础 folder、命名规则可用。

#### 阶段 C：项目侧并行接入
以下 issue 在遵守统一蓝图后可并行推进：
- `Issue #5` NewAPI
- `Issue #6` Arena 股票竞技场
- `Issue #8` Agent Team
- `Issue #7` Uptime Kuma

其中：
- `Issue #5/#6/#8` 偏“业务 + runtime”混合接入。
- `Issue #7` 偏“可用性 / 合成监控”接入。

#### 阶段 D：父 issue 汇总收口
- `Issue #3` 在子 issue 收口后恢复 review。
- 目标：检查分组是否一致、视觉基线是否统一、是否能形成完整的跨项目总览导航。

### 8.2 依赖边界

- `Issue #4` 是所有后续子 issue 的规划基线。
- `Issue #9` 是系统层底座优先项，但不是所有项目接入的硬阻塞，只要统一 datasource / label / folder 约束已确认，项目可并行推进。
- `Issue #3` 不负责替代任何一个子 issue 实现，只负责在所有子 issue 交付后做总编排收口。

### 8.3 各子 issue 验收口径

#### Issue #9 主机总体指标与进程级指标
至少满足：
- Host-System folder 已建立
- 主机总 CPU / 内存 / 进程 TopN 稳定可见
- datasource / dashboard 命名符合本文标准
- 可作为其他项目 dashboard 的参照基线

#### Issue #5 NewAPI
至少满足：
- 有一套 NewAPI 业务 / 系统 dashboard
- 能同时回答“服务是否健康”和“业务流量 / 错误是否异常”
- 指标 label 与命名遵循本文规范

#### Issue #6 Arena 股票竞技场
至少满足：
- 有一套 Arena 业务 / 系统 dashboard
- 能识别核心流程吞吐、异常与资源使用
- 高基数 label 未失控

#### Issue #8 Agent Team
至少满足：
- 有一套 Agent Team runtime / workflow dashboard
- 能看到吞吐、失败、队列 / worker 健康等关键指标
- 不仅停留在 CPU / 内存展示

#### Issue #7 Uptime Kuma
至少满足：
- 有一套 Uptime Kuma 合成监控 dashboard
- 能看到可用率、响应时间、关键告警 / 证书等信息
- 分组进入 Ops 目录，不与业务 dashboard 混放

## 9. 关闭建议

`Issue #4` 在本文落地后即可视为完成，因为它的交付目标是**统一蓝图**，不是后续实现本身。

关闭前判断标准：
1. 是否明确了本轮纳入范围。
2. 是否明确了 folder / dashboard / datasource 命名规范。
3. 是否明确了系统指标与业务指标的分层和采集策略。
4. 是否明确了 `Issue #5` 到 `Issue #9` 的实施顺序、依赖边界与验收口径。

如果上述 4 项都已满足，则当前 issue 应在 PM 完成后直接关闭，后续继续由各子 issue 独立推进。
