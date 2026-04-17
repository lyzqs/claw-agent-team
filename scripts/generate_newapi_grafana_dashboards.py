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

    def timeseries(
        self,
        *,
        title: str,
        targets: list[dict],
        unit: str,
        x: int,
        y: int,
        w: int = 12,
        h: int = 8,
    ) -> dict:
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

    def bargauge(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 12, h: int = 8) -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "continuous-BlYlRd" if unit == "ms" else "continuous-GrYlRd"},
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
                    "legendFormat": "{{model}}{{channel_name}}{{error_code}}",
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
        "current": {"selected": False, "text": "全部", "value": grafana_all},
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
                    "name": "注释与告警",
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
                variable("env", "env", 'label_values(newapi_requests_total, env)'),
                variable("project", "project", 'label_values(newapi_requests_total, project)'),
                variable("job", "job", 'label_values(newapi_requests_total, job)'),
                variable("instance", "instance", 'label_values(newapi_requests_total, instance)'),
                variable("model", "model", 'label_values(newapi_requests_total, model)'),
                variable("channel_name", "channel_name", 'label_values(newapi_requests_total, channel_name)'),
            ]
        },
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


def build_business_overview() -> dict:
    factory = PanelFactory()
    filters = 'env=~"$env",project=~"$project",job=~"$job",instance=~"$instance",model=~"$model",channel_name=~"$channel_name"'
    total_rate = f'sum(rate(newapi_requests_total{{{filters},status_family=~"success|error"}}[5m])) * 60'
    success_rate = (
        f'sum(rate(newapi_request_success_total{{{filters}}}[5m])) '
        f'/ clamp_min(sum(rate(newapi_requests_total{{{filters},status_family=~"success|error"}}[5m])), 0.0001) * 100'
    )
    error_rate = (
        f'sum(rate(newapi_request_error_total{{{filters}}}[5m])) '
        f'/ clamp_min(sum(rate(newapi_requests_total{{{filters},status_family=~"success|error"}}[5m])), 0.0001) * 100'
    )
    dashboard = base_dashboard(
        title="AT | NewAPI | 业务总览",
        uid="at-newapi-business-overview",
        tags=["agent-team-grafana", "newapi", "business"],
    )
    dashboard["panels"] = [
        factory.stat(title="请求量 / 分钟", expr=total_rate, unit="reqpm", x=0, y=0),
        factory.stat(
            title="成功率",
            expr=success_rate,
            unit="percent",
            x=6,
            y=0,
            thresholds=[{"color": "red", "value": None}, {"color": "yellow", "value": 95}, {"color": "green", "value": 99}],
        ),
        factory.stat(title="Token 消耗 / 分钟", expr=f'sum(newapi_tpm{{{filters}}})', unit="short", x=12, y=0),
        factory.stat(title="配额消耗 / 分钟", expr=f'sum(rate(newapi_quota_consumed_total{{{filters}}}[5m])) * 60', unit="short", x=18, y=0),
        factory.stat(
            title="错误率",
            expr=error_rate,
            unit="percent",
            x=0,
            y=5,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 5}],
        ),
        factory.stat(title="数据库健康", expr=f'max(newapi_db_connection_health{{job=~"$job",instance=~"$instance"}})', unit="none", x=6, y=5),
        factory.timeseries(
            title="请求趋势（成功 / 失败）",
            unit="reqps",
            x=0,
            y=10,
            targets=[
                {
                    "expr": f'sum by (status_family) (rate(newapi_requests_total{{{filters},status_family=~"success|error"}}[5m]))',
                    "legend": "{{status_family}}",
                    "refId": "A",
                }
            ],
        ),
        factory.timeseries(
            title="Token / 配额消耗趋势",
            unit="short",
            x=12,
            y=10,
            targets=[
                {"expr": f'sum(rate(newapi_tokens_consumed_total{{{filters}}}[5m])) * 60', "legend": "Token/分钟", "refId": "A"},
                {"expr": f'sum(rate(newapi_quota_consumed_total{{{filters}}}[5m])) * 60', "legend": "配额/分钟", "refId": "B"},
            ],
        ),
        factory.bargauge(
            title="模型请求速率前 10",
            expr=f'topk(10, sum by (model) (rate(newapi_requests_by_model_total{{{filters}}}[5m])))',
            unit="reqps",
            x=0,
            y=18,
        ),
        factory.bargauge(
            title="渠道错误率前 10",
            expr=f'topk(10, newapi_channel_error_rate{{job=~"$job",instance=~"$instance",env=~"$env",project=~"$project",channel_name=~"$channel_name"}}) * 100',
            unit="percent",
            x=12,
            y=18,
        ),
    ]
    return dashboard


def build_runtime_channel_health() -> dict:
    factory = PanelFactory()
    filters = 'env=~"$env",project=~"$project",job=~"$job",instance=~"$instance",channel_name=~"$channel_name"'
    dashboard = base_dashboard(
        title="AT | NewAPI | 运行健康 | 渠道状态",
        uid="at-newapi-runtime-channel-health",
        tags=["agent-team-grafana", "newapi", "runtime"],
    )
    dashboard["panels"] = [
        factory.stat(title="平均渠道健康分", expr=f'avg(newapi_channel_health_score{{{filters}}})', unit="percentunit", x=0, y=0),
        factory.stat(title="最高错误率渠道", expr=f'max(newapi_channel_error_rate{{{filters}}}) * 100', unit="percent", x=6, y=0),
        factory.stat(title="已启用渠道数", expr=f'sum(newapi_channel_status{{{filters}}} > 0)', unit="none", x=12, y=0),
        factory.stat(title="平均响应时间", expr=f'avg(newapi_channel_response_time_ms{{{filters}}})', unit="ms", x=18, y=0),
        factory.timeseries(
            title="渠道错误率趋势",
            unit="percent",
            x=0,
            y=5,
            targets=[
                {
                    "expr": f'topk(8, newapi_channel_error_rate{{{filters}}}) * 100',
                    "legend": "{{channel_name}}",
                    "refId": "A",
                }
            ],
        ),
        factory.bargauge(
            title="渠道健康分排行",
            expr=f'sort_desc(newapi_channel_health_score{{{filters}}})',
            unit="percentunit",
            x=12,
            y=5,
        ),
        factory.bargauge(
            title="渠道响应时间排行",
            expr=f'sort_desc(newapi_channel_response_time_ms{{{filters}}})',
            unit="ms",
            x=0,
            y=13,
        ),
        factory.bargauge(
            title="错误码分布（24h）",
            expr=f'topk(10, sum by (error_code) (increase(newapi_errors_by_error_code_total{{job=~"$job",instance=~"$instance",env=~"$env",project=~"$project"}}[24h])))',
            unit="short",
            x=12,
            y=13,
        ),
        factory.table(
            title="渠道状态表",
            expr=(
                'newapi_channel_status{' + filters + '} or '
                'newapi_channel_response_time_ms{' + filters + '} or '
                'newapi_channel_balance{' + filters + '}'
            ),
            x=0,
            y=21,
            w=24,
            h=8,
        ),
    ]
    return dashboard


def build_runtime_process_dependencies() -> dict:
    factory = PanelFactory()
    filters = 'env=~"$env",project=~"$project",job=~"$job",instance=~"$instance"'
    dashboard = base_dashboard(
        title="AT | NewAPI | 运行健康 | 进程与依赖",
        uid="at-newapi-runtime-process-dependencies",
        tags=["agent-team-grafana", "newapi", "runtime", "process"],
    )
    dashboard["panels"] = [
        factory.stat(title="导出器 / 服务健康", expr=f'max(newapi_up{{{filters}}})', unit="none", x=0, y=0),
        factory.stat(title="CPU 使用率", expr=f'max(newapi_process_cpu_percent{{{filters}}})', unit="percent", x=6, y=0),
        factory.stat(title="内存占用", expr=f'max(newapi_process_memory_bytes{{{filters}}})', unit="bytes", x=12, y=0),
        factory.stat(title="打开文件句柄数", expr=f'max(newapi_process_open_fds{{{filters}}})', unit="none", x=18, y=0),
        factory.stat(title="数据库连接健康", expr=f'max(newapi_db_connection_health{{{filters}}})', unit="none", x=0, y=5),
        factory.stat(title="错误日志开关", expr=f'max(newapi_error_log_enabled{{{filters}}})', unit="none", x=6, y=5),
        factory.timeseries(
            title="NewAPI 进程 CPU 趋势",
            unit="percent",
            x=0,
            y=10,
            targets=[{"expr": f'max(newapi_process_cpu_percent{{{filters}}})', "legend": "CPU", "refId": "A"}],
        ),
        factory.timeseries(
            title="NewAPI 进程内存趋势",
            unit="bytes",
            x=12,
            y=10,
            targets=[{"expr": f'max(newapi_process_memory_bytes{{{filters}}})', "legend": "常驻内存", "refId": "A"}],
        ),
        factory.timeseries(
            title="NewAPI 打开文件句柄趋势",
            unit="none",
            x=0,
            y=18,
            targets=[{"expr": f'max(newapi_process_open_fds{{{filters}}})', "legend": "打开文件句柄", "refId": "A"}],
        ),
        factory.bargauge(
            title="渠道累计已用额度",
            expr='sort_desc(newapi_channel_used_quota_total{env=~"$env",project=~"$project",job=~"$job",instance=~"$instance",channel_name=~"$channel_name"})',
            unit="short",
            x=12,
            y=18,
        ),
    ]
    return dashboard


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "newapi-business-overview.json": build_business_overview(),
        "newapi-runtime-channel-health.json": build_runtime_channel_health(),
        "newapi-runtime-process-dependencies.json": build_runtime_process_dependencies(),
    }
    for name, payload in dashboards.items():
        (DASHBOARD_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
