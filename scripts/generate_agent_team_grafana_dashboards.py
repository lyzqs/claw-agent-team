#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = REPO_ROOT / "deploy" / "grafana" / "dashboards"
DATASOURCE_UID = "prometheus-local-main"
PLUGIN_VERSION = "11.0.0"


class PanelFactory:
    def __init__(self) -> None:
        self.next_id = 1

    def _panel_id(self) -> int:
        panel_id = self.next_id
        self.next_id += 1
        return panel_id

    @staticmethod
    def datasource() -> dict:
        return {"type": "prometheus", "uid": DATASOURCE_UID}

    def stat(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 6, h: int = 5, thresholds: list[dict] | None = None) -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": thresholds or [{"color": "green", "value": None}],
                    },
                    "unit": unit,
                },
                "overrides": [],
            },
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "id": self._panel_id(),
            "options": {
                "colorMode": "background",
                "graphMode": "area",
                "justifyMode": "auto",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "value_and_name",
            },
            "pluginVersion": PLUGIN_VERSION,
            "targets": [
                {
                    "datasource": self.datasource(),
                    "editorMode": "code",
                    "expr": expr,
                    "legendFormat": title,
                    "range": True,
                    "refId": "A",
                }
            ],
            "title": title,
            "type": "stat",
        }

    def timeseries(self, *, title: str, targets: list[dict], unit: str, x: int, y: int, w: int = 12, h: int = 8) -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "mappings": [],
                    "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    "unit": unit,
                },
                "overrides": [],
            },
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "id": self._panel_id(),
            "options": {
                "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": True},
                "tooltip": {"mode": "single", "sort": "none"},
            },
            "pluginVersion": PLUGIN_VERSION,
            "targets": [
                {
                    "datasource": self.datasource(),
                    "editorMode": "code",
                    "expr": target["expr"],
                    "legendFormat": target["legend"],
                    "range": True,
                    "refId": target["refId"],
                }
                for target in targets
            ],
            "title": title,
            "type": "timeseries",
        }

    def bargauge(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 12, h: int = 8, legend: str = "{{role}}{{issue_status}}{{reconcile_type}}{{resolution}}{{component}}") -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "continuous-GrYlRd"},
                    "mappings": [],
                    "min": 0,
                    "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    "unit": unit,
                },
                "overrides": [],
            },
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "id": self._panel_id(),
            "options": {
                "displayMode": "gradient",
                "legend": {"displayMode": "hidden", "placement": "bottom", "showLegend": False},
                "namePlacement": "left",
                "orientation": "horizontal",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "showUnfilled": True,
                "sizing": "auto",
                "valueMode": "color",
            },
            "pluginVersion": PLUGIN_VERSION,
            "targets": [
                {
                    "datasource": self.datasource(),
                    "editorMode": "code",
                    "expr": expr,
                    "instant": True,
                    "legendFormat": legend,
                    "range": False,
                    "refId": "A",
                }
            ],
            "title": title,
            "type": "bargauge",
        }

    def table(self, *, title: str, expr: str, x: int, y: int, w: int = 12, h: int = 8) -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {"defaults": {}, "overrides": []},
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "id": self._panel_id(),
            "options": {
                "cellHeight": "sm",
                "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                "showHeader": True,
                "sortBy": [{"desc": True, "displayName": "Value"}],
            },
            "pluginVersion": PLUGIN_VERSION,
            "targets": [
                {
                    "datasource": self.datasource(),
                    "editorMode": "code",
                    "expr": expr,
                    "format": "table",
                    "instant": True,
                    "range": False,
                    "refId": "A",
                }
            ],
            "title": title,
            "type": "table",
        }


def variable(name: str, label: str, query: str, include_all: bool = True, multi: bool = True) -> dict:
    grafana_all = "$" + "__all"
    return {
        "current": {"selected": False, "text": "All", "value": grafana_all},
        "datasource": {"type": "prometheus", "uid": DATASOURCE_UID},
        "definition": query,
        "hide": 0,
        "includeAll": include_all,
        "label": label,
        "multi": multi,
        "name": name,
        "options": [],
        "query": {"qryType": 1, "query": query, "refId": "StandardVariableQuery"},
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "sort": 1,
        "type": "query",
    }


def base_dashboard(title: str, uid: str, tags: list[str]) -> dict:
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "links": [],
        "panels": [],
        "refresh": "30s",
        "schemaVersion": 39,
        "style": "dark",
        "tags": tags,
        "templating": {
            "list": [
                variable("project", "project", 'label_values(agent_team_issues_total, project)'),
                variable("role", "role", 'label_values(agent_team_role_backlog_total, role)'),
                variable("issue_status", "issue_status", 'label_values(agent_team_issues_total, issue_status)'),
                variable("completion_mode", "completion_mode", 'label_values(agent_team_callback_completion_modes_total, completion_mode)'),
                variable("job", "job", 'label_values(agent_team_exporter_build_info, job)'),
                variable("instance", "instance", 'label_values(agent_team_exporter_build_info, instance)'),
            ]
        },
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


def build_runtime_overview() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance"'
    role_filters = filters + ',role=~"$role"'
    issue_filters = filters + ',issue_status=~"$issue_status"'
    success_total = f'sum(agent_team_attempt_success_total{{{role_filters},completion_mode=~"$completion_mode"}})'
    failure_total = f'sum(agent_team_attempt_failure_total{{{role_filters}}})'
    dashboard = base_dashboard(
        title="AT | Agent Team | 运行总览",
        uid="at-agent-team-runtime-overview",
        tags=["agent-team-grafana", "agent-team", "runtime"],
    )
    dashboard["panels"] = [
        factory.stat(title="未关闭 Issue 数", expr=f'sum(agent_team_issues_total{{{filters},issue_status!="closed"}})', unit="none", x=0, y=0),
        factory.stat(title="Agent 队列", expr=f'sum(agent_team_agent_queue_total{{{role_filters}}})', unit="none", x=6, y=0),
        factory.stat(title="人工队列", expr=f'sum(agent_team_human_queue_total{{{filters}}}) or vector(0)', unit="none", x=12, y=0),
        factory.stat(title="运行中 Attempt", expr=f'sum(agent_team_attempt_running_total{{{role_filters}}})', unit="none", x=18, y=0),
        factory.stat(
            title="成功率",
            expr=f'{success_total} / clamp_min(({success_total}) + ({failure_total}), 0.0001) * 100',
            unit="percent",
            x=0,
            y=5,
            thresholds=[{"color": "red", "value": None}, {"color": "yellow", "value": 80}, {"color": "green", "value": 95}],
        ),
        factory.stat(
            title="失败率",
            expr=f'{failure_total} / clamp_min(({success_total}) + ({failure_total}), 0.0001) * 100',
            unit="percent",
            x=6,
            y=5,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 20}],
        ),
        factory.stat(title="UI API 健康", expr=f'max(agent_team_ui_api_health{{job=~"$job",instance=~"$instance"}})', unit="none", x=12, y=5),
        factory.stat(title="队列隔离健康", expr=f'min(agent_team_queue_isolation_health{{job=~"$job",instance=~"$instance",check="overall"}})', unit="none", x=18, y=5),
        factory.timeseries(
            title="Issue 状态分布",
            unit="none",
            x=0,
            y=10,
            targets=[
                {
                    "expr": f'sum by (issue_status) (agent_team_issues_total{{{issue_filters}}})',
                    "legend": "{{issue_status}}",
                    "refId": "A",
                }
            ],
        ),
        factory.timeseries(
            title="Attempt 状态分布",
            unit="none",
            x=12,
            y=10,
            targets=[
                {
                    "expr": f'sum by (attempt_status) (agent_team_attempts_total{{{role_filters}}})',
                    "legend": "{{attempt_status}}",
                    "refId": "A",
                }
            ],
        ),
        factory.bargauge(
            title="角色积压分布",
            expr=f'sort_desc(sum by (role) (agent_team_role_backlog_total{{{role_filters},issue_status=~"$issue_status"}}))',
            unit="short",
            x=0,
            y=18,
            legend="{{role}}",
        ),
        factory.bargauge(
            title="项目积压分布",
            expr='sort_desc(sum by (project) (agent_team_project_backlog_total{project=~"$project",issue_status=~"$issue_status",job=~"$job",instance=~"$instance"}))',
            unit="short",
            x=12,
            y=18,
            legend="{{project}}",
        ),
    ]
    return dashboard


def build_workflow_flow_health() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance"'
    role_filters = filters + ',role=~"$role"'
    dashboard = base_dashboard(
        title="AT | Agent Team | 流转健康",
        uid="at-agent-team-workflow-flow-health",
        tags=["agent-team-grafana", "agent-team", "workflow"],
    )
    dashboard["panels"] = [
        factory.stat(title="等待子 Issue", expr=f'sum(agent_team_waiting_children_total{{{filters}}})', unit="none", x=0, y=0),
        factory.stat(title="等待恢复完成", expr=f'sum(agent_team_waiting_recovery_total{{{filters}}}) or vector(0)', unit="none", x=6, y=0),
        factory.stat(title="重试次数", expr=f'sum(agent_team_attempt_retry_total{{{role_filters}}})', unit="none", x=12, y=0),
        factory.stat(title="陈旧派发", expr=f'sum(agent_team_stale_dispatch_total{{{role_filters}}}) or vector(0)', unit="none", x=18, y=0),
        factory.stat(title="平均闭环时长", expr=f'avg(agent_team_issue_cycle_time_seconds{{{filters},final_status="closed",stat="avg"}})', unit="s", x=0, y=5),
        factory.stat(title="P95 Attempt 时长", expr=f'max(agent_team_attempt_runtime_seconds{{{role_filters},completion_mode=~"$completion_mode",stat="p95"}})', unit="s", x=6, y=5),
        factory.stat(title="人工往返次数", expr=f'sum(agent_team_human_roundtrip_total{{{filters},resolution!="enqueued"}})', unit="none", x=12, y=5),
        factory.stat(title="已关闭 Issue", expr=f'sum(agent_team_issue_closed_total{{{filters}}})', unit="none", x=18, y=5),
        factory.timeseries(
            title="完成模式分布",
            unit="none",
            x=0,
            y=10,
            targets=[
                {
                    "expr": f'sum by (completion_mode) (agent_team_callback_completion_modes_total{{{role_filters},completion_mode=~"$completion_mode"}})',
                    "legend": "{{completion_mode}}",
                    "refId": "A",
                }
            ],
        ),
        factory.timeseries(
            title="恢复 / 协调事件",
            unit="none",
            x=12,
            y=10,
            targets=[
                {
                    "expr": f'sum by (reconcile_type) (agent_team_reconcile_events_total{{{filters}}})',
                    "legend": "{{reconcile_type}}",
                    "refId": "A",
                }
            ],
        ),
        factory.bargauge(
            title="人工处理结果分布",
            expr='sort_desc(sum by (resolution) (agent_team_human_roundtrip_total{project=~"$project",job=~"$job",instance=~"$instance",resolution!="enqueued"}))',
            unit="short",
            x=0,
            y=18,
            legend="{{resolution}}",
        ),
        factory.bargauge(
            title="失败码分布",
            expr=f'sort_desc(sum by (failure_code) (agent_team_attempt_failure_total{{{role_filters}}}))',
            unit="short",
            x=12,
            y=18,
            legend="{{failure_code}}",
        ),
    ]
    return dashboard


def build_ops_recovery_queue() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance"'
    dashboard = base_dashboard(
        title="AT | Agent Team | 恢复与队列",
        uid="at-agent-team-ops-recovery-queue",
        tags=["agent-team-grafana", "agent-team", "ops"],
    )
    dashboard["panels"] = [
        factory.stat(title="队列隔离检查", expr='min(agent_team_queue_isolation_health{job=~"$job",instance=~"$instance",check!="overall"})', unit="none", x=0, y=0),
        factory.stat(title="Worker 心跳延迟", expr='max(agent_team_worker_heartbeat_age_seconds{job=~"$job",instance=~"$instance",component="issue-worker"})', unit="s", x=6, y=0, thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 60}, {"color": "red", "value": 300}]),
        factory.stat(title="UI API CPU", expr='max(agent_team_process_cpu_percent{job=~"$job",instance=~"$instance",component="ui-api"})', unit="percent", x=12, y=0),
        factory.stat(title="Worker 内存", expr='max(agent_team_process_memory_bytes{job=~"$job",instance=~"$instance",component="issue-worker"})', unit="bytes", x=18, y=0),
        factory.stat(title="活跃会话条目", expr=f'sum(agent_team_session_registry_entries_total{{{filters}}})', unit="none", x=0, y=5),
        factory.stat(title="观察器延迟", expr='max(agent_team_worker_heartbeat_age_seconds{job=~"$job",instance=~"$instance",component="dispatch-observer"})', unit="s", x=6, y=5, thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 60}, {"color": "red", "value": 300}]),
        factory.stat(title="清扫器延迟", expr='max(agent_team_worker_heartbeat_age_seconds{job=~"$job",instance=~"$instance",component="session-sweep"})', unit="s", x=12, y=5, thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 300}, {"color": "red", "value": 900}]),
        factory.stat(title="UI API 可用性", expr='max(agent_team_ui_api_health{job=~"$job",instance=~"$instance"})', unit="none", x=18, y=5),
        factory.timeseries(
            title="各组件心跳延迟",
            unit="s",
            x=0,
            y=10,
            targets=[
                {
                    "expr": 'agent_team_worker_heartbeat_age_seconds{job=~"$job",instance=~"$instance"}',
                    "legend": "{{component}}",
                    "refId": "A",
                }
            ],
        ),
        factory.timeseries(
            title="Agent Team 进程 CPU",
            unit="percent",
            x=12,
            y=10,
            targets=[
                {
                    "expr": 'agent_team_process_cpu_percent{job=~"$job",instance=~"$instance"}',
                    "legend": "{{component}}",
                    "refId": "A",
                }
            ],
        ),
        factory.timeseries(
            title="Agent Team 进程内存",
            unit="bytes",
            x=0,
            y=18,
            targets=[
                {
                    "expr": 'agent_team_process_memory_bytes{job=~"$job",instance=~"$instance"}',
                    "legend": "{{component}}",
                    "refId": "A",
                }
            ],
        ),
        factory.bargauge(
            title="各角色会话条目",
            expr=f'sort_desc(sum by (role) (agent_team_session_registry_entries_total{{{filters}}}))',
            unit="short",
            x=12,
            y=18,
            legend="{{role}}",
        ),
        factory.bargauge(
            title="队列隔离检查项",
            expr='sort_desc(agent_team_queue_isolation_health{job=~"$job",instance=~"$instance"})',
            unit="none",
            x=0,
            y=26,
            legend="{{check}}",
        ),
        factory.table(
            title="队列风险快照",
            expr=(
                'agent_team_agent_queue_total{project=~"$project",job=~"$job",instance=~"$instance",role=~"$role"} '
                'or agent_team_human_queue_total{project=~"$project",job=~"$job",instance=~"$instance"} '
                'or agent_team_stale_dispatch_total{project=~"$project",job=~"$job",instance=~"$instance",role=~"$role"}'
            ),
            x=12,
            y=26,
            w=12,
            h=8,
        ),
    ]
    return dashboard


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "agent-team-runtime-overview.json": build_runtime_overview(),
        "agent-team-workflow-flow-health.json": build_workflow_flow_health(),
        "agent-team-ops-recovery-queue.json": build_ops_recovery_queue(),
    }
    for name, payload in dashboards.items():
        (DASHBOARD_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
