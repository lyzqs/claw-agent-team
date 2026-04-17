# Agent Team Grafana 观测规格（Issue #16 PM）

## 1. 文档目标

本文定义 `Agent Team` 接入 Grafana 前的 PM 规格基线，用于明确：

1. Agent Team 的关键业务流程与核心观测对象。
2. 哪些业务指标与系统/运行指标应进入 Grafana。
3. 哪些指标可以由现有状态表、导出数据、日志或最小 exporter 推导。
4. Agent Team 在 Grafana 中应采用的 folder、dashboard 命名与信息结构。

本文是 `Issue #16` 的直接交付物，供 `Issue #17` 实现使用。

## 2. 事实基础与当前已知线索

基于当前仓库与现有实现，已确认以下事实：

1. Agent Team 的真实状态机与执行事实分布在以下对象中：
   - `issues`
   - `issue_attempts`
   - `issue_attempt_callbacks`
   - `issue_relations`
   - `issue_activity`
   - `issue_checkpoints`
   - `v_agent_queue`
   - `v_human_queue`

2. 当前系统已经明确支持并记录的关键流程包括：
   - issue 创建、triage、handoff、close
   - dispatch_execution
   - record_attempt_callback
   - human queue roundtrip
   - dependency reconcile
   - stale attempt reconcile
   - retry / resume
   - waiting_children / orchestration parent

3. 文档中已经显式提到的关键观测对象包括：
   - issue 流转
   - attempt 状态
   - agent queue / human queue
   - worker 健康
   - reconciliation / retry / recovery
   - multi-project queue isolation

4. 现有仓库已具备与观测直接相关的输出基础：
   - `ui/board/data.json`
   - `ui/board/issues.json`
   - `state/session_registry.json`
   - `state/worker_report.json`
   - `state/dispatch_observer_report.json`
   - `state/session_sweep_report.json`
   - `state/worker_actions.jsonl`

5. 当前尚未发现一份专门面向统一 Grafana 的 Agent Team 观测规格文档，因此本 issue 需要显式补齐该规格。

## 3. Agent Team 核心业务流程

Agent Team 不是传统单体应用，而是“多角色编排 + issue 状态机 + runtime dispatch + recovery/human queue”的系统。建议把其流程拆成 6 层。

### 3.1 Issue 生命周期

目标：让一个需求从创建走到关闭或进入等待态。

关键对象：
- issue
- status
- owner / assigned
- acceptance
- blocker / required_human_input

关键状态：
- `open`
- `triaged`
- `ready`
- `dispatching`
- `running`
- `review`
- `blocked`
- `waiting_human_*`
- `waiting_children`
- `waiting_recovery_completion`
- `closed`

### 3.2 Attempt 执行生命周期

目标：把 issue 的一次具体执行跑通并留下结果。

关键对象：
- attempt_no
- dispatch_ref
- callback_status
- completion_mode
- output_snapshot_json
- failure_code / failure_summary

关键业务意义：
- 同一个 issue 可能经历多次 attempt，attempt 是观测真实执行链路的最小单元。

### 3.3 Queue 与路由编排

目标：让 issue 进入正确的 agent queue、human queue 或 child issue 收口路径。

关键对象：
- `v_agent_queue`
- `v_human_queue`
- role routing
- parent_of / blocked_by relation
- canonical session routing

关键业务意义：
- 区分问题是在需求定义、执行、人工补充、还是编排依赖层面卡住。

### 3.4 Human Queue 往返

目标：让缺失信息或审批需求被人类补齐后返回 agent queue。

关键对象：
- `waiting_human_info`
- `waiting_human_action`
- `waiting_human_approval`
- `returned_to_agent_queue`
- `resolved_by_human`

关键业务意义：
- 这是 Agent Team 与人工协同的重要质量指标。

### 3.5 Recovery / Reconciliation / Retry

目标：在系统中断、回调丢失、dispatch stale 或运行失败时恢复正确状态。

关键对象：
- stale dispatch reconciliation
- retry_execution
- dependency transition reconcile
- orphan attempt 扫描
- recovery completion

关键业务意义：
- 这是 Agent Team 区别于普通任务看板的关键运行能力。

### 3.6 Multi-project / Session / Runtime 健康

目标：保证多项目和多角色会话不串、worker 正常推进、session 绑定正确。

关键对象：
- canonical session registry
- runtime session key / session id
- session sweep
- dispatch observer
- queue isolation

关键业务意义：
- 这是平台级稳定性与可扩展性的核心。

## 4. 关键用户路径

本轮 Grafana 观测应优先支持以下用户路径：

1. **现在系统是不是在正常推进 issue**
   - ready / dispatching / review / waiting_human / waiting_children 各有多少

2. **为什么某个 issue 没有继续流转**
   - 卡在 dispatch、卡在 human queue、卡在 child issue、还是卡在 recovery

3. **最近 attempt 执行质量怎么样**
   - 成功率、失败率、重试率、callback 完整率

4. **worker / session / runtime 是否健康**
   - worker 是否持续工作
   - session 绑定是否正确
   - stale dispatch 是否在增多

5. **多项目有没有串队列或串会话**
   - queue isolation 是否仍成立
   - 某项目 backlog 是否异常积压

## 5. 关键健康信号

### 5.1 顶层业务健康信号

必须优先进入 Grafana 的信号：

1. 当前 open/triaged/ready/review/closed issue 数量
2. 当前 agent queue 数量
3. 当前 human queue 数量
4. 当前 dispatching/running attempt 数量
5. 最近 24h attempt 成功率
6. 最近 24h attempt 失败率
7. 最近 24h retry 数量
8. 当前 waiting_children issue 数量
9. 当前 waiting_recovery_completion issue 数量
10. 最近 reconcile 触发数量

### 5.2 二级健康信号

建议纳入：

1. 各角色 backlog 分布
2. 各项目 issue 分布
3. human queue resolution 分布
4. callback_terminal / transcript_marker / system_chat_final completion_mode 分布
5. stale dispatch 数量
6. callback 缺失 / artifact_only attempt 数量
7. 平均 issue 闭环时长
8. 平均 attempt 运行时长

## 6. 指标清单

以下按“优先级 + 指标目的 + 单位 + 标签建议”定义。

### 6.1 P0 核心业务指标

#### 1. agent_team_issues_total
- 目的：观察 issue 总量与状态分布
- 单位：count
- 标签建议：`env`, `project`, `system`, `service`, `issue_status`

#### 2. agent_team_agent_queue_total
- 目的：观察 agent queue 压力
- 单位：count
- 标签建议：`project`, `role`

#### 3. agent_team_human_queue_total
- 目的：观察 human queue 压力
- 单位：count
- 标签建议：`project`, `human_type`

#### 4. agent_team_attempts_total
- 目的：观察 attempt 数量与状态分布
- 单位：count
- 标签建议：`attempt_status`, `role`, `project`

#### 5. agent_team_attempt_success_total
- 目的：观察 attempt 成功量
- 单位：count
- 标签建议：`role`, `completion_mode`

#### 6. agent_team_attempt_failure_total
- 目的：观察 attempt 失败量
- 单位：count
- 标签建议：`role`, `failure_code`

#### 7. agent_team_attempt_running_total
- 目的：观察当前运行中 attempt 数量
- 单位：count
- 标签建议：`role`, `project`

#### 8. agent_team_waiting_children_total
- 目的：观察编排父 issue 数量
- 单位：count
- 标签建议：`project`

#### 9. agent_team_waiting_recovery_total
- 目的：观察等待恢复完成数量
- 单位：count
- 标签建议：`project`

#### 10. agent_team_issue_closed_total
- 目的：观察关闭吞吐
- 单位：count
- 标签建议：`project`, `resolution`

### 6.2 P1 关键扩展指标

#### 11. agent_team_attempt_retry_total
- 目的：观察重试规模
- 单位：count
- 标签建议：`role`, `project`

#### 12. agent_team_reconcile_events_total
- 目的：观察 stale/recovery/dependency reconcile 触发情况
- 单位：count
- 标签建议：`reconcile_type`, `project`

#### 13. agent_team_human_roundtrip_total
- 目的：观察人工往返数量
- 单位：count
- 标签建议：`human_type`, `resolution`

#### 14. agent_team_callback_completion_modes_total
- 目的：观察不同 completion_mode 分布
- 单位：count
- 标签建议：`completion_mode`

#### 15. agent_team_issue_cycle_time_seconds
- 目的：观察 issue 从创建到关闭的周期时长
- 单位：seconds
- 标签建议：`project`, `final_status`

#### 16. agent_team_attempt_runtime_seconds
- 目的：观察 attempt 执行时长
- 单位：seconds
- 标签建议：`role`, `completion_mode`

#### 17. agent_team_role_backlog_total
- 目的：观察各角色 backlog 压力
- 单位：count
- 标签建议：`role`, `issue_status`

#### 18. agent_team_project_backlog_total
- 目的：观察各项目 backlog 压力
- 单位：count
- 标签建议：`project`, `issue_status`

### 6.3 P2 系统与运行指标

#### 19. agent_team_worker_heartbeat_age_seconds
- 目的：观察 worker 最近心跳/推进是否陈旧
- 单位：seconds
- 标签建议：`instance`, `service`

#### 20. agent_team_session_registry_entries_total
- 目的：观察 canonical session 规模
- 单位：count
- 标签建议：`project`, `role`

#### 21. agent_team_stale_dispatch_total
- 目的：观察 stale dispatch 风险
- 单位：count
- 标签建议：`project`, `role`

#### 22. agent_team_queue_isolation_health
- 目的：观察多项目队列隔离是否仍成立
- 单位：0|1
- 标签建议：`service`

#### 23. agent_team_process_cpu_percent
- 目的：观察 worker / UI API 进程 CPU 压力
- 单位：percent
- 标签建议：`instance`, `service`

#### 24. agent_team_process_memory_bytes
- 目的：观察 worker / UI API 进程内存使用
- 单位：bytes
- 标签建议：`instance`, `service`

## 7. 标签规范

遵循统一蓝图，Agent Team 本轮建议基础标签如下：

- `env`
- `project`
- `system=agent-team`
- `service=agent-team`
- `instance`
- `job`
- `layer`

业务扩展标签建议：
- `role`
- `issue_status`
- `attempt_status`
- `completion_mode`
- `failure_code`
- `human_type`
- `reconcile_type`
- `resolution`

### 禁止直接作为 label 的高基数字段

以下字段不应长期作为 Prometheus label：
- `issue_id`
- `attempt_id`
- `dispatch_ref`
- `callback_token`
- 原始 `failure_summary`
- 原始 `details_md` / 完整 activity 内容
- session file 绝对路径

这些信息更适合作为：
- drill-down 明细
- 审计轨迹
- 异常样本入口

## 8. 采集方式建议

### 8.1 优先级

1. **优先复用现有 SQLite 状态表 / 导出数据 / state 文件**
2. **其次补最小 Prometheus exporter / bridge**
3. **最后才考虑在主业务代码中加大量原生 metrics**

### 8.2 推荐采集路径

#### 路径 A：状态表 / 导出数据采集
优先复用：
- SQLite 中的 issues / issue_attempts / issue_relations / issue_activity / issue_attempt_callbacks
- `ui/board/data.json`
- `ui/board/issues.json`
- `state/worker_report.json`
- `state/dispatch_observer_report.json`
- `state/session_registry.json`

适合先导出的指标：
- issue 数量与状态分布
- attempt 数量与状态分布
- queue 数量
- human queue 数量
- completion mode 分布
- role / project backlog

#### 路径 B：最小 exporter / bridge
如果当前没有 Prometheus 原生暴露，建议在 `Issue #17` 中补一层轻量 bridge，把状态表和 state 文件转为 Prometheus 指标。

适合 bridge 的指标：
- `agent_team_issues_total`
- `agent_team_attempts_total`
- `agent_team_agent_queue_total`
- `agent_team_human_queue_total`
- `agent_team_reconcile_events_total`
- `agent_team_issue_cycle_time_seconds`

#### 路径 C：系统层复用进程指标
worker / ui api / 相关服务的 CPU、内存等运行指标优先通过已有主机观测栈获取：
- process-exporter
- node-exporter

## 9. Grafana 信息结构

### 9.1 Folder
按统一蓝图落入：

`AT | 20 项目 | 智能体团队`

### 9.2 Dashboard 建议

#### Dashboard 1
`AT | Agent-Team | Runtime | Overview`

目标：给用户一个系统总体运行总览。

建议面板：
1. 当前 issue 总量
2. agent queue 数量
3. human queue 数量
4. 当前 running attempt 数量
5. 最近 24h success rate
6. 最近 24h failure rate
7. issue 状态分布
8. role backlog 分布

#### Dashboard 2
`AT | Agent-Team | Workflow | Flow Health`

目标：解释流转是否顺畅。

建议面板：
1. attempt 状态趋势
2. completion mode 分布
3. human roundtrip 数量
4. waiting_children 数量
5. waiting_recovery 数量
6. reconcile 触发趋势

#### Dashboard 3
`AT | Agent-Team | Ops | Recovery & Queue`

目标：观察 recovery 和 queue 风险。

建议面板：
1. stale dispatch 数量
2. retry 数量
3. human queue resolution 分布
4. queue isolation 健康值
5. worker heartbeat age
6. session registry 规模

## 10. 视觉与交互要求

1. 第一行优先展示：issue 总量、agent queue、human queue、running attempts、success rate、failure rate
2. 第二行展示流转趋势图
3. 第三行展示 backlog / completion mode / recovery 分布
4. 需要支持 `project`、`role`、`issue_status`、`completion_mode` 过滤
5. human queue 与 recovery 风险面板必须具备明显阈值色语义

## 11. 对实现侧的明确输入

`Issue #17` 在实现时应以本文为输入，至少完成以下事情：

1. 复用状态表与 state 文件生成 Prometheus 可抓取指标
2. 保持 issue_id / attempt_id / dispatch_ref 等高基数字段停留在明细层，不默认转成 label
3. 复用主机观测栈获取 worker / UI API 的系统指标
4. 在 Grafana 中建立 `AT | 20 项目 | 智能体团队` 下的 dashboard
5. 让用户能直接回答：
   - 系统现在有没有在正常推进 issue
   - 卡点是在 queue / human / recovery / children 哪一层
   - 最近 attempt 质量如何
   - worker / session / queue isolation 是否健康

## 12. PM 显式判断结论

本 issue 当前不需要继续拆分。

原因：
1. 当前 issue 已经是 PM 规格子 issue，本身就是独立验收单元。
2. 现有 Agent Team 仓库已经提供了足够丰富的状态表、导出数据和流程文档，足以形成可执行规格。
3. 继续拆分只会把同一份规格再切碎，增加编排噪音，不增加真实价值。

因此，`Issue #16` 在本文产出后即可视为完成，并建议直接关闭；随后由 `Issue #17` 进入 Dev 实现。