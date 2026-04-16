#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import urllib.request
from prometheus_client.parser import text_string_to_metric_families

EXPECTED_METRICS = {
    'arena_candidates_total',
    'arena_trade_tickets_total',
    'arena_auto_review_queue_total',
    'arena_executed_trades_total',
    'arena_pending_trades_total',
    'arena_portfolio_market_value',
    'arena_portfolio_unrealized_pnl',
    'arena_holdings_total',
    'arena_exit_playbooks_total',
    'arena_runtime_snapshot_age_seconds',
    'arena_ticket_score_distribution',
    'arena_ticket_blockers_total',
    'arena_order_lifecycle_latency_seconds',
    'arena_validation_outcomes_total',
    'arena_rotation_candidates_total',
    'arena_news_score_distribution',
    'arena_runtime_loop_duration_seconds',
    'arena_runtime_events_total',
    'arena_process_cpu_percent',
    'arena_process_memory_bytes',
    'arena_dashboard_http_health',
}


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode('utf-8')


def fetch_grafana_search(url: str, auth: str) -> list[dict]:
    request = urllib.request.Request(url)
    token = base64.b64encode(auth.encode('utf-8')).decode('ascii')
    request.add_header('Authorization', f'Basic {token}')
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> None:
    parser = argparse.ArgumentParser(description='Validate Arena observability delivery.')
    parser.add_argument('--metrics-url', default='http://127.0.0.1:19150/metrics')
    parser.add_argument('--grafana-search-url', default='http://127.0.0.1:3300/api/search?query=AT%20%7C%20Arena')
    parser.add_argument('--grafana-auth', default='admin:MBcACzYMkyDUu6_eh28TSonT')
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
        raise ValueError(f'missing expected metrics: {missing}')

    grafana_hits = fetch_grafana_search(args.grafana_search_url, args.grafana_auth)
    report = {
        'status': 'ok',
        'metrics_url': args.metrics_url,
        'metric_count': len(seen_metrics),
        'expected_metrics_present': sorted(EXPECTED_METRICS),
        'non_zero_metrics': non_zero_metrics,
        'grafana_search_hits': grafana_hits,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
