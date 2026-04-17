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
                    "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "green", "value": None}]},
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
            "targets": [{"datasource": self.datasource(), "editorMode": "code", "expr": expr, "legendFormat": title, "range": True, "refId": "A"}],
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
            "options": {"legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "single", "sort": "none"}},
            "pluginVersion": PLUGIN_VERSION,
            "targets": [
                {"datasource": self.datasource(), "editorMode": "code", "expr": target["expr"], "legendFormat": target["legend"], "range": True, "refId": target["refId"]}
                for target in targets
            ],
            "title": title,
            "type": "timeseries",
        }

    def bargauge(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 12, h: int = 8, legend: str = "{{group}}") -> dict:
        return {
            "datasource": self.datasource(),
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "continuous-GrYlRd" if unit == "percentunit" else "continuous-BlYlRd"},
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
            "targets": [{"datasource": self.datasource(), "editorMode": "code", "expr": expr, "instant": True, "legendFormat": legend, "range": False, "refId": "A"}],
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
            "targets": [{"datasource": self.datasource(), "editorMode": "code", "expr": expr, "format": "table", "instant": True, "range": False, "refId": "A"}],
            "title": title,
            "type": "table",
        }


def variable(name: str, label: str, query: str, *, include_all: bool = True, multi: bool = True) -> dict:
    return {
        "current": {"selected": False, "text": "All", "value": "$__all"},
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
                variable("group", "group", 'label_values(kuma_monitor_status, group)'),
                variable("monitor_type", "monitor_type", 'label_values(kuma_monitor_status, monitor_type)'),
                variable("monitor_name", "monitor_name", 'label_values(kuma_monitor_status, monitor_name)'),
                variable("job", "job", 'label_values(kuma_monitor_status, job)'),
                variable("instance", "instance", 'label_values(kuma_monitor_status, instance)'),
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


def build_overview() -> dict:
    factory = PanelFactory()
    group_filter = 'group=~"$group",monitor_type=~"$monitor_type",monitor_name=~"$monitor_name",job=~"$job",instance=~"$instance"'
    dash = base_dashboard("AT | Uptime Kuma | 概览", "at-uptime-kuma-synthetic-overview", ["agent-team-grafana", "uptime-kuma", "synthetic"])
    dash["panels"] = [
        factory.stat(title="在线监控数", expr='sum(kuma_monitors_up_total{group!="__all__"}) or vector(0)', unit="none", x=0, y=0),
        factory.stat(title="离线监控数", expr='sum(kuma_monitors_down_total{group!="__all__"}) or vector(0)', unit="none", x=6, y=0, thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 3}]),
        factory.stat(title="24h 平均可用率", expr='avg(kuma_group_availability_ratio{group!="__all__"}) * 100', unit="percentunit", x=12, y=0),
        factory.stat(title="平均响应时间", expr='avg(kuma_group_avg_response_time_ms{group!="__all__"})', unit="ms", x=18, y=0),
        factory.stat(title="证书剩余最短天数", expr='min(kuma_cert_expiry_days)', unit="dtdurations", x=0, y=5, thresholds=[{"color": "red", "value": None}, {"color": "yellow", "value": 14}, {"color": "green", "value": 30}]),
        factory.stat(title="Kuma 进程 CPU", expr='max(kuma_process_cpu_percent)', unit="percent", x=6, y=5),
        factory.stat(title="Kuma 进程内存", expr='max(kuma_process_memory_bytes)', unit="bytes", x=12, y=5),
        factory.stat(title="Socket Polling 健康", expr='max(kuma_socket_polling_health)', unit="none", x=18, y=5),
        factory.timeseries(
            title="分组可用率",
            unit="percentunit",
            x=0,
            y=10,
            targets=[{"expr": 'kuma_group_availability_ratio{group!="__all__"}', "legend": "{{group}}", "refId": "A"}],
        ),
        factory.bargauge(
            title="各分组离线监控数",
            expr='sort_desc(kuma_monitors_down_total{group!="__all__"})',
            unit="none",
            x=12,
            y=10,
            legend="{{group}}",
        ),
        factory.table(
            title="离线/抖动监控明细",
            expr=(
                'kuma_monitor_status{' + group_filter + '} < 1 or '
                'kuma_monitor_flap_score{' + group_filter + '} > 0'
            ),
            x=0,
            y=18,
            w=24,
            h=10,
        ),
    ]
    return dash


def build_group_health() -> dict:
    factory = PanelFactory()
    dash = base_dashboard("AT | Uptime Kuma | 分组健康", "at-uptime-kuma-synthetic-group-health", ["agent-team-grafana", "uptime-kuma", "group-health"])
    dash["panels"] = [
        factory.bargauge(title="分组在线监控数", expr='sort_desc(kuma_monitors_up_total{group!="__all__"})', unit="none", x=0, y=0, legend="{{group}}"),
        factory.bargauge(title="分组离线监控数", expr='sort_desc(kuma_monitors_down_total{group!="__all__"})', unit="none", x=12, y=0, legend="{{group}}"),
        factory.timeseries(
            title="分组平均响应时间",
            unit="ms",
            x=0,
            y=8,
            targets=[{"expr": 'kuma_group_avg_response_time_ms{group!="__all__"}', "legend": "{{group}}", "refId": "A"}],
        ),
        factory.timeseries(
            title="分组可用率",
            unit="percentunit",
            x=12,
            y=8,
            targets=[{"expr": 'kuma_group_availability_ratio{group!="__all__"} * 100', "legend": "{{group}}", "refId": "A"}],
        ),
        factory.table(title="分组告警范围", expr='kuma_group_alerting_scope{group!="__all__"}', x=0, y=16, w=12, h=8),
        factory.table(title="监控重试策略分布", expr='kuma_monitor_retry_policy{group=~"$group",monitor_type=~"$monitor_type",monitor_name=~"$monitor_name"}', x=12, y=16, w=12, h=8),
    ]
    return dash


def build_monitor_details() -> dict:
    factory = PanelFactory()
    filter_expr = 'group=~"$group",monitor_type=~"$monitor_type",monitor_name=~"$monitor_name",job=~"$job",instance=~"$instance"'
    dash = base_dashboard("AT | Uptime Kuma | 监控明细", "at-uptime-kuma-synthetic-monitor-details", ["agent-team-grafana", "uptime-kuma", "monitor-details"])
    dash["panels"] = [
        factory.bargauge(title="响应时间 TopN", expr='topk(15, kuma_monitor_response_time_ms{' + filter_expr + '})', unit="ms", x=0, y=0, legend="{{monitor_name}}"),
        factory.bargauge(title="最近失败次数 TopN", expr='topk(15, kuma_monitor_failures_total{' + filter_expr + '})', unit="none", x=12, y=0, legend="{{monitor_name}}"),
        factory.bargauge(title="最近恢复次数 TopN", expr='topk(15, kuma_monitor_recoveries_total{' + filter_expr + '})', unit="none", x=0, y=8, legend="{{monitor_name}}"),
        factory.bargauge(title="抖动分数 TopN", expr='topk(15, kuma_monitor_flap_score{' + filter_expr + '})', unit="none", x=12, y=8, legend="{{monitor_name}}"),
        factory.table(title="证书剩余天数", expr='kuma_cert_expiry_days', x=0, y=16, w=12, h=8),
        factory.table(title="当前监控状态与重试策略", expr='kuma_monitor_status{' + filter_expr + '} or kuma_monitor_retry_policy{' + filter_expr + '}', x=12, y=16, w=12, h=8),
    ]
    return dash


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "uptime-kuma-synthetic-overview.json": build_overview(),
        "uptime-kuma-synthetic-group-health.json": build_group_health(),
        "uptime-kuma-synthetic-monitor-details.json": build_monitor_details(),
    }
    for name, payload in dashboards.items():
        (DASHBOARD_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
