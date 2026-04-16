#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "deploy" / "grafana"
DASHBOARD_DIR = BUNDLE_ROOT / "dashboards"


EXPECTED_DASHBOARDS = {
    "local-host-observability.json": {
        "uid": "agent-team-local-observe",
        "snippets": [
            "node_cpu_seconds_total",
            "node_memory_MemAvailable_bytes",
            "namedprocess_namegroup_cpu_seconds_total",
            "namedprocess_namegroup_memory_bytes",
            "topk(10",
        ],
    },
    "agent-team-runtime-overview.json": {
        "uid": "at-agent-team-runtime-overview",
        "snippets": [
            "agent_team_issues_total",
            "agent_team_agent_queue_total",
            "agent_team_human_queue_total",
            "agent_team_attempt_running_total",
            "agent_team_attempt_success_total",
            "agent_team_attempt_failure_total",
            "agent_team_role_backlog_total",
            "agent_team_project_backlog_total",
        ],
    },
    "agent-team-workflow-flow-health.json": {
        "uid": "at-agent-team-workflow-flow-health",
        "snippets": [
            "agent_team_waiting_children_total",
            "agent_team_waiting_recovery_total",
            "agent_team_attempt_retry_total",
            "agent_team_stale_dispatch_total",
            "agent_team_callback_completion_modes_total",
            "agent_team_reconcile_events_total",
            "agent_team_human_roundtrip_total",
            "agent_team_attempt_failure_total",
        ],
    },
    "agent-team-ops-recovery-queue.json": {
        "uid": "at-agent-team-ops-recovery-queue",
        "snippets": [
            "agent_team_queue_isolation_health",
            "agent_team_worker_heartbeat_age_seconds",
            "agent_team_process_cpu_percent",
            "agent_team_process_memory_bytes",
            "agent_team_session_registry_entries_total",
            "agent_team_stale_dispatch_total",
            "agent_team_agent_queue_total",
            "agent_team_human_queue_total",
        ],
    },
    "arena-business-overview.json": {
        "uid": "at-arena-business-overview",
        "snippets": [
            "arena_portfolio_market_value",
            "arena_portfolio_unrealized_pnl",
            "arena_holdings_total",
            "arena_executed_trades_total",
            "arena_candidates_total",
            "arena_trade_tickets_total",
            "arena_pending_trades_total",
            "arena_runtime_snapshot_age_seconds",
        ],
    },
    "arena-runtime-execution-flow.json": {
        "uid": "at-arena-runtime-execution-flow",
        "snippets": [
            "arena_auto_review_queue_total",
            "arena_dashboard_http_health",
            "arena_runtime_snapshot_age_seconds",
            "arena_order_lifecycle_latency_seconds",
            "arena_runtime_events_total",
            "arena_runtime_loop_duration_seconds",
            "arena_ticket_blockers_total",
        ],
    },
    "arena-position-holdings-exits.json": {
        "uid": "at-arena-position-holdings-exits",
        "snippets": [
            "arena_holdings_total",
            "arena_exit_playbooks_total",
            "arena_rotation_candidates_total",
            "arena_portfolio_market_value",
            "arena_portfolio_unrealized_pnl",
            "arena_executed_trades_total",
            "arena_pending_trades_total",
        ],
    },
    "arena-review-validation-iteration.json": {
        "uid": "at-arena-review-validation-iteration",
        "snippets": [
            "arena_validation_outcomes_total",
            "arena_news_score_distribution",
            "arena_runtime_events_total",
        ],
    },
    "newapi-business-overview.json": {
        "uid": "at-newapi-business-overview",
        "snippets": [
            "newapi_requests_total",
            "newapi_request_success_total",
            "newapi_request_error_total",
            "newapi_tokens_consumed_total",
            "newapi_quota_consumed_total",
            "newapi_channel_error_rate",
        ],
    },
    "newapi-runtime-channel-health.json": {
        "uid": "at-newapi-runtime-channel-health",
        "snippets": [
            "newapi_channel_health_score",
            "newapi_channel_error_rate",
            "newapi_channel_response_time_ms",
            "newapi_errors_by_error_code_total",
            "newapi_channel_status",
        ],
    },
    "newapi-runtime-process-dependencies.json": {
        "uid": "at-newapi-runtime-process-dependencies",
        "snippets": [
            "newapi_up",
            "newapi_process_cpu_percent",
            "newapi_process_memory_bytes",
            "newapi_process_open_fds",
            "newapi_db_connection_health",
            "newapi_error_log_enabled",
        ],
    },
    "uptime-kuma-synthetic-overview.json": {
        "uid": "at-uptime-kuma-synthetic-overview",
        "snippets": [
            "kuma_monitors_up_total",
            "kuma_monitors_down_total",
            "kuma_group_availability_ratio",
            "kuma_group_avg_response_time_ms",
            "kuma_cert_expiry_days",
            "kuma_monitor_flap_score",
        ],
    },
    "uptime-kuma-synthetic-group-health.json": {
        "uid": "at-uptime-kuma-synthetic-group-health",
        "snippets": [
            "kuma_monitors_up_total",
            "kuma_monitors_down_total",
            "kuma_group_avg_response_time_ms",
            "kuma_group_availability_ratio",
            "kuma_group_alerting_scope",
            "kuma_monitor_retry_policy",
        ],
    },
    "uptime-kuma-synthetic-monitor-details.json": {
        "uid": "at-uptime-kuma-synthetic-monitor-details",
        "snippets": [
            "kuma_monitor_response_time_ms",
            "kuma_monitor_failures_total",
            "kuma_monitor_recoveries_total",
            "kuma_monitor_flap_score",
            "kuma_cert_expiry_days",
            "kuma_monitor_status",
        ],
    },
}


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def validate_dashboard(path: Path) -> dict:
    payload = json.loads(path.read_text())
    panels = payload.get("panels", [])
    if not panels:
        raise ValueError(f"dashboard has no panels: {path.name}")

    expected = EXPECTED_DASHBOARDS.get(path.name)
    if expected:
        if payload.get("uid") != expected["uid"]:
            raise ValueError(f"dashboard uid mismatch for {path.name}: {payload.get('uid')} != {expected['uid']}")
        expressions = [target.get("expr", "") for panel in panels for target in panel.get("targets", [])]
        for snippet in expected["snippets"]:
            if not any(snippet in expression for expression in expressions):
                raise ValueError(f"dashboard missing query snippet: {snippet} ({path.name})")

    for panel in panels:
        thresholds = panel.get("fieldConfig", {}).get("defaults", {}).get("thresholds")
        if thresholds is None:
            continue
        if not isinstance(thresholds, dict):
            raise ValueError(f"panel thresholds must be an object: {panel.get('title')}")
        steps = thresholds.get("steps")
        if not isinstance(steps, list):
            raise ValueError(f"panel thresholds.steps must be a list: {panel.get('title')}")

    datasource_uids = {
        target.get("datasource", {}).get("uid")
        for panel in panels
        for target in panel.get("targets", [])
        if isinstance(target.get("datasource"), dict)
    }
    datasource_uids.discard(None)
    if path.name != "local-host-observability.json" and datasource_uids != {"prometheus-local-main"}:
        raise ValueError(f"dashboard datasource uid mismatch: {path.name} -> {sorted(datasource_uids)}")
    return {"title": payload.get("title"), "panel_count": len(panels), "uid": payload.get("uid")}


def main() -> None:
    report = {"bundle_root": str(BUNDLE_ROOT), "yaml_files": {}, "dashboards": {}, "systemd_units": {}, "status": "ok"}

    yaml_paths = [
        BUNDLE_ROOT / "process-exporter" / "process-exporter.yml",
        BUNDLE_ROOT / "prometheus" / "prometheus.yml",
        BUNDLE_ROOT / "provisioning" / "datasources" / "prometheus.yaml",
        BUNDLE_ROOT / "provisioning" / "dashboards" / "dashboard-provider.yaml",
    ]
    for path in yaml_paths:
        payload = load_yaml(path)
        report["yaml_files"][str(path.relative_to(REPO_ROOT))] = {"loaded": payload is not None}

    for dashboard_path in sorted(DASHBOARD_DIR.glob("*.json")):
        report["dashboards"][dashboard_path.name] = validate_dashboard(dashboard_path)

    for unit_path in [
        BUNDLE_ROOT / "systemd" / "process-exporter.service",
        BUNDLE_ROOT / "systemd" / "agent-team-prometheus.service",
        BUNDLE_ROOT / "systemd" / "agent-team-metrics-exporter.service",
        BUNDLE_ROOT / "systemd" / "arena-metrics-exporter.service",
        BUNDLE_ROOT / "systemd" / "newapi-metrics-exporter.service",
        BUNDLE_ROOT / "systemd" / "uptime-kuma-metrics-exporter.service",
    ]:
        text = unit_path.read_text()
        if "ExecStart=" not in text:
            raise ValueError(f"systemd unit missing ExecStart: {unit_path}")
        report["systemd_units"][str(unit_path.relative_to(REPO_ROOT))] = {"execstart": "ok"}

    prometheus_yaml = load_yaml(BUNDLE_ROOT / "prometheus" / "prometheus.yml")
    jobs = {item["job_name"] for item in prometheus_yaml.get("scrape_configs", [])}
    for required_job in {"agent-team-exporter", "newapi-exporter", "arena-exporter", "uptime-kuma-exporter"}:
        if required_job not in jobs:
            raise ValueError(f"prometheus scrape config missing {required_job} job")

    datasource_yaml = load_yaml(BUNDLE_ROOT / "provisioning" / "datasources" / "prometheus.yaml")
    datasource_uids = {item["uid"] for item in datasource_yaml.get("datasources", [])}
    for required_uid in {"prometheus-local-main", "prometheus-local"}:
        if required_uid not in datasource_uids:
            raise ValueError(f"datasource provisioning missing {required_uid} uid")

    providers_yaml = load_yaml(BUNDLE_ROOT / "provisioning" / "dashboards" / "dashboard-provider.yaml")
    folders = {item["folder"] for item in providers_yaml.get("providers", [])}
    for folder in {"AT | 10 Platform | Host-System", "AT | 20 Project | Agent-Team", "AT | 21 Project | NewAPI", "AT | 22 Project | Arena", "AT | 30 Ops | Uptime-Kuma"}:
        if folder not in folders:
            raise ValueError(f"dashboard provider missing folder: {folder}")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
