#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import urllib.request
from prometheus_client.parser import text_string_to_metric_families

EXPECTED_METRICS = {
    "kuma_monitors_total",
    "kuma_monitor_status",
    "kuma_monitors_up_total",
    "kuma_monitors_down_total",
    "kuma_monitor_response_time_ms",
    "kuma_group_availability_ratio",
    "kuma_monitor_retry_policy",
    "kuma_group_alerting_scope",
    "kuma_monitor_failures_total",
    "kuma_monitor_recoveries_total",
    "kuma_group_avg_response_time_ms",
    "kuma_cert_expiry_days",
    "kuma_monitor_flap_score",
    "kuma_process_cpu_percent",
    "kuma_process_memory_bytes",
    "kuma_proxy_health",
    "kuma_socket_polling_health",
}


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def fetch_grafana_search(url: str, auth: str) -> list[dict]:
    request = urllib.request.Request(url)
    token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Uptime Kuma observability delivery.")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:19120/metrics")
    parser.add_argument("--grafana-search-url", default="http://127.0.0.1:3300/api/search?query=AT%20%7C%20Uptime-Kuma")
    parser.add_argument("--grafana-auth", default="admin:MBcACzYMkyDUu6_eh28TSonT")
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

    grafana_hits = fetch_grafana_search(args.grafana_search_url, args.grafana_auth)
    report = {
        "status": "ok",
        "metrics_url": args.metrics_url,
        "metric_count": len(seen_metrics),
        "expected_metrics_present": sorted(EXPECTED_METRICS),
        "non_zero_metrics": non_zero_metrics,
        "grafana_search_hits": grafana_hits,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
