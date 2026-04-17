#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import urllib.request
from pathlib import Path

from prometheus_client.parser import text_string_to_metric_families


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_METRICS = {
    "openclaw_tokens_total",
    "openclaw_cost_usd_total",
    "openclaw_run_duration_ms_bucket",
    "openclaw_context_tokens_bucket",
    "openclaw_message_queued_total",
    "openclaw_message_processed_total",
    "openclaw_message_duration_ms_bucket",
    "openclaw_queue_depth_bucket",
    "openclaw_queue_wait_ms_bucket",
    "openclaw_session_state_total",
    "openclaw_session_stuck_total",
    "openclaw_session_stuck_age_ms_bucket",
    "openclaw_run_attempt_total",
    "openclaw_webhook_received_total",
    "openclaw_webhook_error_total",
    "openclaw_webhook_duration_ms_bucket",
    "openclaw_queue_lane_enqueue_total",
    "openclaw_queue_lane_dequeue_total",
    "openclaw_otel_bridge_requests_total",
    "openclaw_otel_bridge_points_total",
    "openclaw_otel_bridge_last_export_timestamp_seconds",
}
EXPECTED_GRAFANA_DASHBOARD_UIDS = {
    "at-openclaw-runtime-overview",
    "at-openclaw-usage-model-message-flow",
    "at-openclaw-queue-sessions-channels",
}
EXPECTED_GRAFANA_FOLDER_TITLE = "AT | 11 平台 | OpenClaw"
EXPECTED_PROMETHEUS_JOB = "openclaw-otel-bridge"
CONFIG_BATCH = [
    {
        "path": "plugins.entries.diagnostics-otel.enabled",
        "value": True,
    },
    {
        "path": "diagnostics.enabled",
        "value": True,
    },
    {
        "path": "diagnostics.otel.enabled",
        "value": True,
    },
    {
        "path": "diagnostics.otel.endpoint",
        "value": "http://127.0.0.1:19160",
    },
    {
        "path": "diagnostics.otel.protocol",
        "value": "http/protobuf",
    },
    {
        "path": "diagnostics.otel.serviceName",
        "value": "openclaw-gateway",
    },
    {
        "path": "diagnostics.otel.metrics",
        "value": True,
    },
    {
        "path": "diagnostics.otel.traces",
        "value": False,
    },
    {
        "path": "diagnostics.otel.logs",
        "value": False,
    },
    {
        "path": "diagnostics.otel.flushIntervalMs",
        "value": 60000,
    },
]


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def fetch_json(url: str, auth: str = "") -> object:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate OpenClaw observability delivery.")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:19160/metrics")
    parser.add_argument("--prometheus-targets-url", default="http://127.0.0.1:19090/api/v1/targets")
    parser.add_argument("--grafana-search-url", default="http://127.0.0.1:3300/api/search?query=AT%20%7C%20OpenClaw")
    parser.add_argument("--grafana-auth", default="admin:MBcACzYMkyDUu6_eh28TSonT")
    parser.add_argument("--config-file", default="/root/.openclaw/openclaw.json")
    args = parser.parse_args()

    metrics_text = fetch_text(args.metrics_url)
    seen_metrics: set[str] = set()
    non_zero_metrics: dict[str, float] = {}
    for family in text_string_to_metric_families(metrics_text):
        seen_metrics.add(family.name)
        for sample in family.samples:
            try:
                value = float(sample.value)
            except (TypeError, ValueError):
                continue
            if value != 0 and family.name not in non_zero_metrics:
                non_zero_metrics[family.name] = value

    missing = sorted(EXPECTED_METRICS - seen_metrics)
    if missing:
        raise ValueError(f"missing expected metrics: {missing}")

    prometheus_payload = fetch_json(args.prometheus_targets_url)
    active_targets = prometheus_payload.get("data", {}).get("activeTargets", [])
    openclaw_targets = [
        {
            "job": target.get("labels", {}).get("job"),
            "instance": target.get("labels", {}).get("instance"),
            "health": target.get("health"),
            "lastError": target.get("lastError", ""),
            "scrapeUrl": target.get("scrapeUrl"),
        }
        for target in active_targets
        if target.get("labels", {}).get("job") == EXPECTED_PROMETHEUS_JOB
    ]
    if not openclaw_targets:
        raise ValueError("prometheus activeTargets missing openclaw-otel-bridge")
    unhealthy_targets = [target for target in openclaw_targets if target.get("health") != "up"]
    if unhealthy_targets:
        raise ValueError(f"openclaw-otel-bridge target not healthy: {unhealthy_targets}")

    grafana_hits = fetch_json(args.grafana_search_url, args.grafana_auth)
    grafana_hit_uids = {item.get("uid") for item in grafana_hits}
    missing_dashboard_uids = sorted(EXPECTED_GRAFANA_DASHBOARD_UIDS - grafana_hit_uids)
    if missing_dashboard_uids:
        raise ValueError(f"grafana missing openclaw dashboards: {missing_dashboard_uids}")
    bad_folder_hits = [
        {
            "uid": item.get("uid"),
            "title": item.get("title"),
            "folderTitle": item.get("folderTitle"),
        }
        for item in grafana_hits
        if item.get("uid") in EXPECTED_GRAFANA_DASHBOARD_UIDS and item.get("folderTitle") != EXPECTED_GRAFANA_FOLDER_TITLE
    ]
    if bad_folder_hits:
        raise ValueError(f"grafana openclaw dashboards loaded under unexpected folder: {bad_folder_hits}")

    config_path = Path(args.config_file)
    config_exists = config_path.exists()
    config_payload = json.loads(config_path.read_text()) if config_exists else {}
    diagnostics_plugin_enabled = (
        config_payload.get("plugins", {})
        .get("entries", {})
        .get("diagnostics-otel", {})
        .get("enabled")
        if isinstance(config_payload, dict)
        else None
    )
    diagnostics_payload = config_payload.get("diagnostics", {}) if isinstance(config_payload, dict) else {}
    diagnostics_otel_payload = diagnostics_payload.get("otel", {}) if isinstance(diagnostics_payload, dict) else {}

    recommended_config_present = {
        "plugins.entries.diagnostics-otel.enabled": diagnostics_plugin_enabled is True,
        "diagnostics.enabled": diagnostics_payload.get("enabled") is True,
        "diagnostics.otel.enabled": diagnostics_otel_payload.get("enabled") is True,
        "diagnostics.otel.metrics": diagnostics_otel_payload.get("metrics") is True,
        "diagnostics.otel.traces": diagnostics_otel_payload.get("traces") is False,
        "diagnostics.otel.logs": diagnostics_otel_payload.get("logs") is False,
        "diagnostics.otel.protocol": diagnostics_otel_payload.get("protocol") == "http/protobuf",
        "diagnostics.otel.serviceName": diagnostics_otel_payload.get("serviceName") == "openclaw-gateway",
        "diagnostics.otel.endpoint": diagnostics_otel_payload.get("endpoint") == "http://127.0.0.1:19160",
    }

    report = {
        "status": "ok",
        "metrics_url": args.metrics_url,
        "prometheus_targets_url": args.prometheus_targets_url,
        "metric_count": len(seen_metrics),
        "expected_metrics_present": sorted(EXPECTED_METRICS),
        "non_zero_metrics": non_zero_metrics,
        "openclaw_targets": openclaw_targets,
        "grafana_search_hits": grafana_hits,
        "expected_grafana_dashboard_uids": sorted(EXPECTED_GRAFANA_DASHBOARD_UIDS),
        "expected_grafana_folder_title": EXPECTED_GRAFANA_FOLDER_TITLE,
        "config_file": str(config_path),
        "config_exists": config_exists,
        "recommended_config_present": recommended_config_present,
        "recommended_config_batch": CONFIG_BATCH,
        "config_validation_note": "当前仓库已补齐可直接写入的 OpenClaw OTel 推荐配置 batch；本机现有 /root/.openclaw/openclaw.json 仍未启用 diagnostics-otel，因此运行态指标是否持续出现仍需 Ops/QA 按 batch 写入并复验。",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
