# Agent Team 指标接入与 Grafana 面板落地（Issue #17 Dev 交付）

## 交付目标
基于 `docs/grafana-agent-team-observability-spec.md` 与统一蓝图，把 Agent Team 的 workflow / queue / attempt / recovery / session 健康指标接入本地 Prometheus / Grafana，并提供最小验证路径。

## 本轮交付物

### 1. Agent Team 指标桥接 exporter
- `scripts/agent_team_metrics_exporter.py`
- `deploy/grafana/systemd/agent-team-metrics-exporter.service`

实现方式：
- 直接读取 `/root/.openclaw/workspace-agent-team/state/agent_team.db`
- 复用 `state/worker_report.json`、`state/dispatch_observer_report.json`、`state/session_sweep_report.json`、`state/session_registry.json`、`state/worker_actions.jsonl`
- 调用 `http://127.0.0.1:8032/api/workflow-control` 探测 UI API 健康
- 暴露 Prometheus `/metrics`，覆盖：
  - issue / agent queue / human queue / waiting_children / waiting_recovery
  - attempts / success / failure / running / retry / completion_mode
  - reconcile events / human roundtrip / stale dispatch
  - issue cycle time / attempt runtime 聚合值
  - session registry / queue isolation / UI API 健康
  - issue worker、UI API、dispatch observer、session sweep 的 CPU / 内存 / 心跳年龄

### 2. Prometheus 抓取接入
- 更新 `deploy/grafana/prometheus/prometheus.yml`
- 新增 `job_name: agent-team-exporter`
- 抓取地址：`127.0.0.1:19130`

### 3. Grafana Host-System 蓝图对齐
- `deploy/grafana/dashboards/local-host-observability.json`
  - dashboard 标题调整为 `AT | Host-System | System | Overview`
  - datasource uid 统一使用 `prometheus-local-main`
  - 增加 `host-system` tag
- `docs/grafana-local-observability-stack.md`
- `deploy/grafana/install_local_grafana_stack.sh`
  - 输出说明同步更新为新的 Host-System dashboard 名称

### 4. Agent Team dashboards
- `deploy/grafana/dashboards/agent-team-runtime-overview.json`
- `deploy/grafana/dashboards/agent-team-workflow-flow-health.json`
- `deploy/grafana/dashboards/agent-team-ops-recovery-queue.json`
- 由 `scripts/generate_agent_team_grafana_dashboards.py` 生成

命名符合蓝图：
- `AT | Agent-Team | Runtime | Overview`
- `AT | Agent-Team | Workflow | Flow Health`
- `AT | Agent-Team | Ops | Recovery & Queue`

### 5. Dashboard provisioning / 安装脚本更新
- `deploy/grafana/provisioning/dashboards/dashboard-provider.yaml`
  - 新增 folder `AT | 20 项目 | 智能体团队`
- `deploy/grafana/install_local_grafana_stack.sh`
  - 安装 `agent-team-metrics-exporter.service`
  - 同步 Agent Team dashboards 到 `/var/lib/grafana/dashboards/agent-team-grafana/agent-team`
  - 健康检查新增 `http://127.0.0.1:19130/metrics`

### 6. 验证脚本
- `scripts/validate_grafana_bundle.py`
- `scripts/validate_agent_team_observability.py`

用途：
- 静态校验 Grafana bundle、Agent Team dashboard、systemd unit 与 Prometheus scrape job
- 校验 exporter 是否暴露预期 `agent_team_*` 指标
- 校验 Grafana 搜索结果里能发现 Agent Team dashboards

## 覆盖到的规格指标
本轮已覆盖或桥接以下核心指标：
- `agent_team_issues_total`
- `agent_team_agent_queue_total`
- `agent_team_human_queue_total`
- `agent_team_attempts_total`
- `agent_team_attempt_success_total`
- `agent_team_attempt_failure_total`
- `agent_team_attempt_running_total`
- `agent_team_waiting_children_total`
- `agent_team_waiting_recovery_total`
- `agent_team_issue_closed_total`
- `agent_team_attempt_retry_total`
- `agent_team_reconcile_events_total`
- `agent_team_human_roundtrip_total`
- `agent_team_callback_completion_modes_total`
- `agent_team_issue_cycle_time_seconds`
- `agent_team_attempt_runtime_seconds`
- `agent_team_role_backlog_total`
- `agent_team_project_backlog_total`
- `agent_team_worker_heartbeat_age_seconds`
- `agent_team_session_registry_entries_total`
- `agent_team_stale_dispatch_total`
- `agent_team_queue_isolation_health`
- `agent_team_process_cpu_percent`
- `agent_team_process_memory_bytes`
- `agent_team_ui_api_health`

## 安装 / 更新方式
如果本机已有 Grafana 主栈，重新执行安装脚本即可下发最新配置：

```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/generate_agent_team_grafana_dashboards.py
sudo ./deploy/grafana/install_local_grafana_stack.sh \
  --public-host <your-public-host> \
  --grafana-http-port 3300 \
  --grafana-admin-password '<grafana-admin-password>'
```

脚本会：
- 安装并启动 `agent-team-metrics-exporter.service`
- 更新 Prometheus 抓取配置
- 同步 Agent Team dashboards 到 Grafana provisioning 目录
- 触发健康检查

## 最小验证

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 运行态校验
```bash
python3 scripts/validate_agent_team_observability.py
```

### 3. 手工检查建议
- 打开 Grafana
- 确认 folder `AT | 20 项目 | 智能体团队` 已存在
- 确认 3 个 Agent Team dashboards 可见
- 确认 Host-System dashboard 标题已切换为 `AT | Host-System | System | Overview`
- 确认面板中出现：
  - issue / queue / human queue / running attempts
  - completion mode / reconcile / human roundtrip / stale dispatch
  - worker heartbeat / session registry / queue isolation / UI API 健康

## 角色边界判断
本轮 Dev 已完成：
- Agent Team 指标桥接实现
- Prometheus 接入
- Grafana dashboards 定义
- Host-System 蓝图命名收敛
- 最小验证脚本

后续建议：
- 由 QA 基于实际页面、指标含义和运行态数据做验收
- 如需在目标主机重新安装或调整公网暴露策略，再由 Ops 配合执行
