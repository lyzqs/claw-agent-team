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

    def bargauge(self, *, title: str, expr: str, unit: str, x: int, y: int, legend: str, w: int = 12, h: int = 8) -> dict:
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


def variable(name: str, label: str, query: str) -> dict:
    grafana_all = "$" + "__all"
    return {
        "current": {"selected": False, "text": "全部", "value": grafana_all},
        "datasource": {"type": "prometheus", "uid": DATASOURCE_UID},
        "definition": query,
        "hide": 0,
        "includeAll": True,
        "label": label,
        "multi": True,
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
                variable("channel", "channel", 'label_values(openclaw_message_processed_total, channel)'),
                variable("provider", "provider", 'label_values(openclaw_tokens_total, provider)'),
                variable("model", "model", 'label_values(openclaw_tokens_total, model)'),
                variable("lane", "lane", 'label_values(openclaw_queue_depth_bucket, lane)'),
                variable("instance", "instance", 'label_values(openclaw_otel_bridge_requests_total, instance)'),
                variable("job", "job", 'label_values(openclaw_otel_bridge_requests_total, job)'),
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
    runtime_filters = 'job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"'
    queue_filters = 'job=~"$job",instance=~"$instance",lane=~"$lane"'
    dashboard = base_dashboard(
        "AT | OpenClaw | Runtime | Overview",
        "at-openclaw-runtime-overview",
        ["agent-team-grafana", "openclaw", "runtime"],
    )
    dashboard["panels"] = [
        factory.stat(title="24h Token 总量", expr=f'sum(increase(openclaw_tokens_total{{{runtime_filters}}}[24h]))', unit="short", x=0, y=0),
        factory.stat(title="24h 成本（USD）", expr=f'sum(increase(openclaw_cost_usd_total{{{runtime_filters}}}[24h]))', unit="currencyUSD", x=6, y=0),
        factory.stat(title="当前队列深度 P95", expr=f'max(histogram_quantile(0.95, sum by (le) (rate(openclaw_queue_depth_bucket{{{queue_filters}}}[5m]))))', unit="none", x=12, y=0),
        factory.stat(title="当前卡住会话数", expr='sum(increase(openclaw_session_stuck_total{job=~"$job",instance=~"$instance"}[24h]))', unit="none", x=18, y=0),
        factory.stat(title="Run Duration P95", expr=f'max(histogram_quantile(0.95, sum by (le) (rate(openclaw_run_duration_ms_bucket{{{runtime_filters}}}[5m]))))', unit="ms", x=0, y=5),
        factory.stat(title="OpenClaw Gateway CPU", expr='sum(rate(namedprocess_namegroup_cpu_seconds_total{job="process-exporter",groupname=~"openclaw|openclaw-gatewa"}[5m])) * 100', unit="percent", x=6, y=5),
        factory.stat(title="OpenClaw Gateway 内存", expr='sum(namedprocess_namegroup_memory_bytes{job="process-exporter",memtype="resident",groupname=~"openclaw|openclaw-gatewa"})', unit="bytes", x=12, y=5),
        factory.stat(title="桥接接收请求数", expr='sum(openclaw_otel_bridge_requests_total{job=~"$job",instance=~"$instance"})', unit="none", x=18, y=5),
        factory.timeseries(
            title="消息处理吞吐",
            unit="ops",
            x=0,
            y=10,
            targets=[
                {"expr": 'sum by (outcome) (rate(openclaw_message_processed_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[5m]))', "legend": '{{outcome}}', "refId": 'A'},
            ],
        ),
        factory.timeseries(
            title="队列等待时间趋势",
            unit="ms",
            x=12,
            y=10,
            targets=[
                {"expr": 'histogram_quantile(0.95, sum by (le, lane) (rate(openclaw_queue_wait_ms_bucket{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m])))', "legend": '{{lane}} P95', "refId": 'A'},
            ],
        ),
        factory.bargauge(
            title="会话状态分布",
            expr='sort_desc(sum by (state) (increase(openclaw_session_state_total{job=~"$job",instance=~"$instance"}[24h])))',
            unit="short",
            x=0,
            y=18,
            legend='{{state}}',
        ),
        factory.bargauge(
            title="队列深度（按 Lane）",
            expr='sort_desc(histogram_quantile(0.95, sum by (lane, le) (rate(openclaw_queue_depth_bucket{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))))',
            unit="none",
            x=12,
            y=18,
            legend='{{lane}}',
        ),
    ]
    return dashboard


def build_usage_model_message_flow() -> dict:
    factory = PanelFactory()
    runtime_filters = 'job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"'
    dashboard = base_dashboard(
        "AT | OpenClaw | Usage | Model & Message Flow",
        "at-openclaw-usage-model-message-flow",
        ["agent-team-grafana", "openclaw", "usage"],
    )
    dashboard["panels"] = [
        factory.stat(title="24h Message Queued", expr='sum(increase(openclaw_message_queued_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h]))', unit="none", x=0, y=0),
        factory.stat(title="24h Message Processed", expr='sum(increase(openclaw_message_processed_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h]))', unit="none", x=6, y=0),
        factory.stat(title="Webhook 错误数（24h）", expr='sum(increase(openclaw_webhook_error_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h]))', unit="none", x=12, y=0),
        factory.stat(title="Context Tokens P95", expr=f'max(histogram_quantile(0.95, sum by (le) (rate(openclaw_context_tokens_bucket{{{runtime_filters}}}[5m]))))', unit="short", x=18, y=0),
        factory.timeseries(
            title="Token / 成本趋势",
            unit="short",
            x=0,
            y=5,
            targets=[
                {"expr": 'sum(rate(openclaw_tokens_total{job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"}[5m]))', "legend": 'Token/秒', "refId": 'A'},
                {"expr": 'sum(rate(openclaw_cost_usd_total{job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"}[5m]))', "legend": '成本USD/秒', "refId": 'B'},
            ],
        ),
        factory.timeseries(
            title="消息耗时趋势",
            unit="ms",
            x=12,
            y=5,
            targets=[
                {"expr": 'histogram_quantile(0.95, sum by (le, outcome) (rate(openclaw_message_duration_ms_bucket{job=~"$job",instance=~"$instance",channel=~"$channel"}[5m])))', "legend": '{{outcome}} P95', "refId": 'A'},
            ],
        ),
        factory.bargauge(
            title="Provider / Model Token 分布",
            expr='topk(10, sum by (provider, model) (increase(openclaw_tokens_total{job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"}[24h])))',
            unit="short",
            x=0,
            y=13,
            legend='{{provider}} / {{model}}',
        ),
        factory.bargauge(
            title="Provider / Model 成本分布",
            expr='topk(10, sum by (provider, model) (increase(openclaw_cost_usd_total{job=~"$job",instance=~"$instance",channel=~"$channel",provider=~"$provider",model=~"$model"}[24h])))',
            unit="currencyUSD",
            x=12,
            y=13,
            legend='{{provider}} / {{model}}',
        ),
        factory.bargauge(
            title="消息结果分布",
            expr='sort_desc(sum by (outcome) (increase(openclaw_message_processed_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h])))',
            unit="short",
            x=0,
            y=21,
            legend='{{outcome}}',
        ),
        factory.bargauge(
            title="Webhook 接收量分布",
            expr='sort_desc(sum by (webhook) (increase(openclaw_webhook_received_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h])))',
            unit="short",
            x=12,
            y=21,
            legend='{{webhook}}',
        ),
    ]
    return dashboard


def build_queue_sessions_channels() -> dict:
    factory = PanelFactory()
    dashboard = base_dashboard(
        "AT | OpenClaw | Queue | Sessions & Channels",
        "at-openclaw-queue-sessions-channels",
        ["agent-team-grafana", "openclaw", "queue"],
    )
    dashboard["panels"] = [
        factory.stat(title="Queue Enqueue 速率", expr='sum(rate(openclaw_queue_lane_enqueue_total{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))', unit="ops", x=0, y=0),
        factory.stat(title="Queue Dequeue 速率", expr='sum(rate(openclaw_queue_lane_dequeue_total{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))', unit="ops", x=6, y=0),
        factory.stat(title="卡住会话年龄 P95", expr='histogram_quantile(0.95, sum by (le, state) (rate(openclaw_session_stuck_age_ms_bucket{job=~"$job",instance=~"$instance"}[5m])))', unit="ms", x=12, y=0),
        factory.stat(title="Run Attempt（24h）", expr='sum(increase(openclaw_run_attempt_total{job=~"$job",instance=~"$instance"}[24h]))', unit="none", x=18, y=0),
        factory.timeseries(
            title="Queue Enqueue / Dequeue 趋势",
            unit="ops",
            x=0,
            y=5,
            targets=[
                {"expr": 'sum by (lane) (rate(openclaw_queue_lane_enqueue_total{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))', "legend": '{{lane}} 入队', "refId": 'A'},
                {"expr": 'sum by (lane) (rate(openclaw_queue_lane_dequeue_total{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))', "legend": '{{lane}} 出队', "refId": 'B'},
            ],
        ),
        factory.timeseries(
            title="Webhook 耗时趋势",
            unit="ms",
            x=12,
            y=5,
            targets=[
                {"expr": 'histogram_quantile(0.95, sum by (le, webhook) (rate(openclaw_webhook_duration_ms_bucket{job=~"$job",instance=~"$instance",channel=~"$channel"}[5m])))', "legend": '{{webhook}} P95', "refId": 'A'},
            ],
        ),
        factory.bargauge(
            title="Queue Depth 按 Lane",
            expr='sort_desc(histogram_quantile(0.95, sum by (lane, le) (rate(openclaw_queue_depth_bucket{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))))',
            unit="none",
            x=0,
            y=13,
            legend='{{lane}}',
        ),
        factory.bargauge(
            title="Queue Wait 按 Lane",
            expr='sort_desc(histogram_quantile(0.95, sum by (lane, le) (rate(openclaw_queue_wait_ms_bucket{job=~"$job",instance=~"$instance",lane=~"$lane"}[5m]))))',
            unit="ms",
            x=12,
            y=13,
            legend='{{lane}}',
        ),
        factory.bargauge(
            title="卡住会话状态分布",
            expr='sort_desc(sum by (state) (increase(openclaw_session_stuck_total{job=~"$job",instance=~"$instance"}[24h])))',
            unit="short",
            x=0,
            y=21,
            legend='{{state}}',
        ),
        factory.bargauge(
            title="Channel / Outcome 异常分布",
            expr='topk(10, sum by (channel, outcome) (increase(openclaw_message_processed_total{job=~"$job",instance=~"$instance",channel=~"$channel"}[24h])))',
            unit="short",
            x=12,
            y=21,
            legend='{{channel}} / {{outcome}}',
        ),
    ]
    return dashboard


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "openclaw-runtime-overview.json": build_runtime_overview(),
        "openclaw-usage-model-message-flow.json": build_usage_model_message_flow(),
        "openclaw-queue-sessions-channels.json": build_queue_sessions_channels(),
    }
    for name, payload in dashboards.items():
        (DASHBOARD_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
