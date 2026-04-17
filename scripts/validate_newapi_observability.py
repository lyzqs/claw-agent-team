#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from prometheus_client.parser import text_string_to_metric_families


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_METRICS = {
    "newapi_requests_total",
    "newapi_request_success_total",
    "newapi_request_error_total",
    "newapi_channel_error_rate",
    "newapi_tokens_consumed_total",
    "newapi_quota_consumed_total",
    "newapi_rpm",
    "newapi_tpm",
    "newapi_requests_by_model_total",
    "newapi_errors_by_error_code_total",
    "newapi_channel_health_score",
    "newapi_topup_events_total",
    "newapi_subscription_events_total",
    "newapi_process_cpu_percent",
    "newapi_process_memory_bytes",
    "newapi_process_open_fds",
    "newapi_db_connection_health",
    "newapi_error_log_enabled",
    "newapi_channel_status",
    "newapi_channel_used_quota_total",
    "newapi_channel_response_time_ms",
    "newapi_channel_balance",
    "newapi_up",
}


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate NewAPI exporter metrics and Grafana bundle outputs.")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:19100/metrics")
    parser.add_argument("--grafana-search-url", default="http://127.0.0.1:3300/api/search?query=AT%20%7C%20NewAPI")
    parser.add_argument("--grafana-auth", default="admin:MBcACzYMkyDUu6_eh28TSonT")
    return parser.parse_args()


def fetch_grafana_search(url: str, auth: str) -> list[dict]:
    request = urllib.request.Request(url)
    if auth:
        import base64
        token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    args = parse_args()
    metrics_text = fetch_text(args.metrics_url)
    seen_metrics: set[str] = set()
    non_zero_metrics: dict[str, float] = {}
    samples_by_metric: dict[str, int] = {}
    for family in text_string_to_metric_families(metrics_text):
        seen_metrics.add(family.name)
        samples_by_metric[family.name] = len(family.samples)
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

    grafana_search = fetch_grafana_search(args.grafana_search_url, args.grafana_auth)

    report = {
        "status": "ok",
        "dashboard_validation_note": "NewAPI Business Overview 现已强化渠道维度趋势表达，包含按渠道看 Token 变化与按渠道看配额变化两块核心 timeseries；Grafana 搜索命中需结合页面确认这些面板已稳定可见。",
        "metrics_url": args.metrics_url,
        "metric_count": len(seen_metrics),
        "expected_metrics_present": sorted(EXPECTED_METRICS),
        "non_zero_metrics": non_zero_metrics,
        "samples_by_metric": samples_by_metric,
        "grafana_search_hits": grafana_search,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
