#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

from prometheus_client import CollectorRegistry, Gauge, generate_latest

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
DEFAULT_DB_PATH = STATE_DIR / "agent_team.db"
DEFAULT_WORKER_REPORT = STATE_DIR / "worker_report.json"
DEFAULT_DISPATCH_OBSERVER_REPORT = STATE_DIR / "dispatch_observer_report.json"
DEFAULT_SESSION_SWEEP_REPORT = STATE_DIR / "session_sweep_report.json"
DEFAULT_SESSION_REGISTRY = STATE_DIR / "session_registry.json"
DEFAULT_WORKER_ACTIONS = STATE_DIR / "worker_actions.jsonl"
DEFAULT_UI_API_URL = "http://127.0.0.1:8032/api/workflow-control"
DEFAULT_STALE_ATTEMPT_SECONDS = 30 * 60
PROCESS_PATTERNS = {
    "issue-worker": ("issue_worker_v2.py",),
    "ui-api": ("ui_api_server.py",),
    "dispatch-observer": ("dispatch_observer_v1.py",),
    "session-sweep": ("session_sweep.py",),
}
RECONCILE_KIND_MAP = {
    "dependency_released": "dependency_released",
    "parent_progressed": "parent_progressed",
    "observer_apply": "observer_apply",
    "cancel_stale": "stale_cancel",
    "defer_stale": "stale_deferred",
}
WAITING_HUMAN_STATUS_TO_TYPE = {
    "waiting_human_info": "info",
    "waiting_human_action": "action",
    "waiting_human_approval": "approval",
}


@dataclass
class ExporterConfig:
    db_path: str
    worker_report_path: str
    dispatch_observer_report_path: str
    session_sweep_report_path: str
    session_registry_path: str
    worker_actions_path: str
    ui_api_url: str
    stale_attempt_seconds: int
    listen_host: str
    listen_port: int
    env: str
    system: str
    service: str
    job: str
    instance: str


def parse_args() -> ExporterConfig:
    parser = argparse.ArgumentParser(description="Expose Agent Team metrics for Prometheus scraping.")
    parser.add_argument("--db-path", default=os.environ.get("AGENT_TEAM_DB_PATH", str(DEFAULT_DB_PATH)))
    parser.add_argument("--worker-report-path", default=os.environ.get("AGENT_TEAM_WORKER_REPORT_PATH", str(DEFAULT_WORKER_REPORT)))
    parser.add_argument("--dispatch-observer-report-path", default=os.environ.get("AGENT_TEAM_DISPATCH_OBSERVER_REPORT_PATH", str(DEFAULT_DISPATCH_OBSERVER_REPORT)))
    parser.add_argument("--session-sweep-report-path", default=os.environ.get("AGENT_TEAM_SESSION_SWEEP_REPORT_PATH", str(DEFAULT_SESSION_SWEEP_REPORT)))
    parser.add_argument("--session-registry-path", default=os.environ.get("AGENT_TEAM_SESSION_REGISTRY_PATH", str(DEFAULT_SESSION_REGISTRY)))
    parser.add_argument("--worker-actions-path", default=os.environ.get("AGENT_TEAM_WORKER_ACTIONS_PATH", str(DEFAULT_WORKER_ACTIONS)))
    parser.add_argument("--ui-api-url", default=os.environ.get("AGENT_TEAM_UI_API_URL", DEFAULT_UI_API_URL))
    parser.add_argument("--stale-attempt-seconds", type=int, default=int(os.environ.get("AGENT_TEAM_STALE_ATTEMPT_SECONDS", str(DEFAULT_STALE_ATTEMPT_SECONDS))))
    parser.add_argument("--listen-host", default=os.environ.get("AGENT_TEAM_EXPORTER_HOST", "127.0.0.1"))
    parser.add_argument("--listen-port", type=int, default=int(os.environ.get("AGENT_TEAM_EXPORTER_PORT", "19130")))
    parser.add_argument("--env", default=os.environ.get("AGENT_TEAM_EXPORTER_ENV", "local"))
    parser.add_argument("--system", default=os.environ.get("AGENT_TEAM_EXPORTER_SYSTEM", "agent-team"))
    parser.add_argument("--service", default=os.environ.get("AGENT_TEAM_EXPORTER_SERVICE", "agent-team"))
    parser.add_argument("--job", default=os.environ.get("AGENT_TEAM_EXPORTER_JOB", "agent-team-exporter"))
    parser.add_argument("--instance", default=os.environ.get("AGENT_TEAM_EXPORTER_INSTANCE") or os.uname().nodename)
    args = parser.parse_args()
    return ExporterConfig(**vars(args))


def safe_json_loads(raw: str | None) -> dict | list:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, (dict, list)) else {}
    except Exception:
        return {}


def read_json_file(path: str, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def age_seconds_from_epoch(epoch_seconds: float | None, now_seconds: float) -> float:
    if epoch_seconds is None:
        return 0.0
    return max(now_seconds - epoch_seconds, 0.0)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float((ordered[lower] * (1 - weight)) + (ordered[upper] * weight))


def infer_human_type_from_status(status: str | None) -> str:
    return WAITING_HUMAN_STATUS_TO_TYPE.get(str(status or "").strip(), "unknown")


class AgentTeamCollector:
    def __init__(self, config: ExporterConfig) -> None:
        self.config = config
        self.base_labels = {
            "env": config.env,
            "system": config.system,
            "service": config.service,
            "job": config.job,
            "instance": config.instance,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _matching_pids(self, *patterns: str) -> list[int]:
        matches: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
            except OSError:
                continue
            if all(pattern in cmdline for pattern in patterns):
                matches.append(int(entry.name))
        return matches

    def _process_cpu_percent(self, pid: int) -> float:
        try:
            parts = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
            utime = int(parts[13])
            stime = int(parts[14])
            starttime = int(parts[21])
            clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            uptime_seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
            process_seconds = uptime_seconds - (starttime / clk_tck)
            if process_seconds <= 0:
                return 0.0
            return max(((utime + stime) / clk_tck) / process_seconds * 100.0, 0.0)
        except Exception:
            return 0.0

    def _process_memory_bytes(self, pid: int) -> float:
        try:
            resident_pages = int(Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()[1])
            return float(resident_pages * os.sysconf("SC_PAGE_SIZE"))
        except Exception:
            return 0.0

    def _ui_api_health(self) -> float:
        try:
            with urlopen(self.config.ui_api_url, timeout=5) as response:
                return 1.0 if response.status == 200 else 0.0
        except Exception:
            return 0.0

    def collect(self) -> bytes:
        registry = CollectorRegistry()
        base_keys = list(self.base_labels.keys())

        build_info = Gauge("agent_team_exporter_build_info", "Exporter build info", [*base_keys, "layer"], registry=registry)
        issues_total = Gauge("agent_team_issues_total", "Current issue count by status", [*base_keys, "layer", "project", "issue_status"], registry=registry)
        agent_queue_total = Gauge("agent_team_agent_queue_total", "Current agent queue size", [*base_keys, "layer", "project", "role"], registry=registry)
        human_queue_total = Gauge("agent_team_human_queue_total", "Current human queue size", [*base_keys, "layer", "project", "human_type"], registry=registry)
        attempts_total = Gauge("agent_team_attempts_total", "Attempt count by status", [*base_keys, "layer", "project", "role", "attempt_status"], registry=registry)
        attempt_success_total = Gauge("agent_team_attempt_success_total", "Succeeded attempt count", [*base_keys, "layer", "project", "role", "completion_mode"], registry=registry)
        attempt_failure_total = Gauge("agent_team_attempt_failure_total", "Failed attempt count", [*base_keys, "layer", "project", "role", "failure_code"], registry=registry)
        attempt_running_total = Gauge("agent_team_attempt_running_total", "Current running attempt count", [*base_keys, "layer", "project", "role"], registry=registry)
        waiting_children_total = Gauge("agent_team_waiting_children_total", "Current waiting_children issue count", [*base_keys, "layer", "project"], registry=registry)
        waiting_recovery_total = Gauge("agent_team_waiting_recovery_total", "Current waiting_recovery_completion issue count", [*base_keys, "layer", "project"], registry=registry)
        issue_closed_total = Gauge("agent_team_issue_closed_total", "Issue closed event count", [*base_keys, "layer", "project", "resolution"], registry=registry)
        attempt_retry_total = Gauge("agent_team_attempt_retry_total", "Retry attempt count", [*base_keys, "layer", "project", "role"], registry=registry)
        reconcile_events_total = Gauge("agent_team_reconcile_events_total", "Reconcile event count", [*base_keys, "layer", "project", "reconcile_type"], registry=registry)
        human_roundtrip_total = Gauge("agent_team_human_roundtrip_total", "Human queue roundtrip count", [*base_keys, "layer", "project", "human_type", "resolution"], registry=registry)
        callback_completion_modes_total = Gauge("agent_team_callback_completion_modes_total", "Completion mode distribution", [*base_keys, "layer", "project", "role", "completion_mode"], registry=registry)
        issue_cycle_time_seconds = Gauge("agent_team_issue_cycle_time_seconds", "Issue cycle time aggregate", [*base_keys, "layer", "project", "final_status", "stat"], registry=registry)
        attempt_runtime_seconds = Gauge("agent_team_attempt_runtime_seconds", "Attempt runtime aggregate", [*base_keys, "layer", "project", "role", "completion_mode", "stat"], registry=registry)
        role_backlog_total = Gauge("agent_team_role_backlog_total", "Open backlog by role and status", [*base_keys, "layer", "project", "role", "issue_status"], registry=registry)
        project_backlog_total = Gauge("agent_team_project_backlog_total", "Open backlog by project and status", [*base_keys, "layer", "project", "issue_status"], registry=registry)
        worker_heartbeat_age_seconds = Gauge("agent_team_worker_heartbeat_age_seconds", "Heartbeat/report age in seconds", [*base_keys, "layer", "component"], registry=registry)
        session_registry_entries_total = Gauge("agent_team_session_registry_entries_total", "Session registry entry count", [*base_keys, "layer", "project", "role", "entry_status"], registry=registry)
        stale_dispatch_total = Gauge("agent_team_stale_dispatch_total", "Current stale dispatch count", [*base_keys, "layer", "project", "role"], registry=registry)
        queue_isolation_health = Gauge("agent_team_queue_isolation_health", "Queue isolation health checks", [*base_keys, "layer", "check"], registry=registry)
        process_cpu_percent = Gauge("agent_team_process_cpu_percent", "Agent Team process CPU percent", [*base_keys, "layer", "component"], registry=registry)
        process_memory_bytes = Gauge("agent_team_process_memory_bytes", "Agent Team process memory bytes", [*base_keys, "layer", "component"], registry=registry)
        ui_api_health = Gauge("agent_team_ui_api_health", "Agent Team UI API health", [*base_keys, "layer"], registry=registry)

        build_info.labels(**self.base_labels, layer="L0").set(1)
        ui_api_health.labels(**self.base_labels, layer="L1").set(self._ui_api_health())

        now_seconds = time.time()
        worker_report = read_json_file(self.config.worker_report_path, {})
        dispatch_observer_report = read_json_file(self.config.dispatch_observer_report_path, {})
        session_registry = read_json_file(self.config.session_registry_path, {})

        worker_heartbeat_age_seconds.labels(**self.base_labels, layer="L1", component="issue-worker").set(
            age_seconds_from_epoch(parse_iso_to_epoch(worker_report.get("ran_at")), now_seconds)
        )
        finished_at = dispatch_observer_report.get("finishedAt")
        dispatch_epoch = (float(finished_at) / 1000.0) if isinstance(finished_at, (int, float)) else None
        worker_heartbeat_age_seconds.labels(**self.base_labels, layer="L1", component="dispatch-observer").set(
            age_seconds_from_epoch(dispatch_epoch, now_seconds)
        )
        try:
            session_sweep_mtime = Path(self.config.session_sweep_report_path).stat().st_mtime
        except OSError:
            session_sweep_mtime = None
        worker_heartbeat_age_seconds.labels(**self.base_labels, layer="L1", component="session-sweep").set(
            age_seconds_from_epoch(session_sweep_mtime, now_seconds)
        )

        for component, patterns in PROCESS_PATTERNS.items():
            pids = self._matching_pids(*patterns)
            process_cpu_percent.labels(**self.base_labels, layer="L1", component=component).set(
                sum(self._process_cpu_percent(pid) for pid in pids)
            )
            process_memory_bytes.labels(**self.base_labels, layer="L1", component=component).set(
                sum(self._process_memory_bytes(pid) for pid in pids)
            )

        conn = self._connect()
        try:
            issue_rows = conn.execute(
                """
                select i.id,
                       i.status,
                       i.created_at_ms,
                       i.closed_at_ms,
                       p.project_key,
                       coalesce(rt.template_key, 'unassigned') as role
                from issues i
                join projects p on p.id = i.project_id
                left join employee_instances ei on ei.id = i.assigned_employee_id
                left join role_templates rt on rt.id = ei.role_template_id
                """
            ).fetchall()
            issue_project_map = {str(row["id"]): str(row["project_key"]) for row in issue_rows}

            issue_status_counts: Counter[tuple[str, str]] = Counter()
            agent_queue_counts: Counter[tuple[str, str]] = Counter()
            human_queue_counts: Counter[tuple[str, str]] = Counter()
            waiting_children_counts: Counter[str] = Counter()
            waiting_recovery_counts: Counter[str] = Counter()
            role_backlog_counts: Counter[tuple[str, str, str]] = Counter()
            project_backlog_counts: Counter[tuple[str, str]] = Counter()
            issue_cycle_buckets: defaultdict[tuple[str, str], list[float]] = defaultdict(list)

            for row in issue_rows:
                project = str(row["project_key"])
                status = str(row["status"])
                role = str(row["role"] or "unassigned")
                issue_status_counts[(project, status)] += 1
                if status in {"ready", "dispatching", "running", "blocked", "review", "waiting_recovery_completion", "waiting_children"}:
                    agent_queue_counts[(project, role)] += 1
                human_type = infer_human_type_from_status(status)
                if human_type != "unknown":
                    human_queue_counts[(project, human_type)] += 1
                if status == "waiting_children":
                    waiting_children_counts[project] += 1
                if status == "waiting_recovery_completion":
                    waiting_recovery_counts[project] += 1
                if status != "closed":
                    role_backlog_counts[(project, role, status)] += 1
                    project_backlog_counts[(project, status)] += 1
                if row["closed_at_ms"]:
                    duration = max((int(row["closed_at_ms"]) - int(row["created_at_ms"])) / 1000.0, 0.0)
                    issue_cycle_buckets[(project, status)].append(duration)

            for (project, status), count in sorted(issue_status_counts.items()):
                issues_total.labels(**self.base_labels, layer="L3", project=project, issue_status=status).set(float(count))
            for (project, role), count in sorted(agent_queue_counts.items()):
                agent_queue_total.labels(**self.base_labels, layer="L3", project=project, role=role).set(float(count))
            for (project, human_type), count in sorted(human_queue_counts.items()):
                human_queue_total.labels(**self.base_labels, layer="L3", project=project, human_type=human_type).set(float(count))
            for project, count in sorted(waiting_children_counts.items()):
                waiting_children_total.labels(**self.base_labels, layer="L3", project=project).set(float(count))
            for project, count in sorted(waiting_recovery_counts.items()):
                waiting_recovery_total.labels(**self.base_labels, layer="L3", project=project).set(float(count))
            for (project, role, status), count in sorted(role_backlog_counts.items()):
                role_backlog_total.labels(**self.base_labels, layer="L3", project=project, role=role, issue_status=status).set(float(count))
            for (project, status), count in sorted(project_backlog_counts.items()):
                project_backlog_total.labels(**self.base_labels, layer="L3", project=project, issue_status=status).set(float(count))
            for (project, final_status), values in sorted(issue_cycle_buckets.items()):
                issue_cycle_time_seconds.labels(**self.base_labels, layer="L3", project=project, final_status=final_status, stat="avg").set(float(statistics.fmean(values)))
                issue_cycle_time_seconds.labels(**self.base_labels, layer="L3", project=project, final_status=final_status, stat="p95").set(percentile(values, 0.95))

            attempt_rows = conn.execute(
                """
                select ia.status,
                       ia.attempt_no,
                       ia.failure_code,
                       ia.completion_mode,
                       ia.created_at_ms,
                       ia.started_at_ms,
                       ia.ended_at_ms,
                       p.project_key,
                       coalesce(rt.template_key, 'unassigned') as role
                from issue_attempts ia
                join issues i on i.id = ia.issue_id
                join projects p on p.id = i.project_id
                left join employee_instances ei on ei.id = ia.assigned_employee_id
                left join role_templates rt on rt.id = ei.role_template_id
                """
            ).fetchall()

            attempt_status_counts: Counter[tuple[str, str, str]] = Counter()
            attempt_success_counts: Counter[tuple[str, str, str]] = Counter()
            attempt_failure_counts: Counter[tuple[str, str, str]] = Counter()
            attempt_running_counts: Counter[tuple[str, str]] = Counter()
            retry_counts: Counter[tuple[str, str]] = Counter()
            completion_mode_counts: Counter[tuple[str, str, str]] = Counter()
            attempt_runtime_buckets: defaultdict[tuple[str, str, str], list[float]] = defaultdict(list)
            stale_dispatch_counts: Counter[tuple[str, str]] = Counter()

            for row in attempt_rows:
                project = str(row["project_key"])
                role = str(row["role"] or "unassigned")
                status = str(row["status"])
                attempt_status_counts[(project, role, status)] += 1
                completion_mode = str(row["completion_mode"] or "unknown")
                if status == "succeeded":
                    attempt_success_counts[(project, role, completion_mode)] += 1
                elif status in {"failed", "cancelled", "timed_out", "abandoned"}:
                    failure_code = str(row["failure_code"] or status or "unknown")
                    attempt_failure_counts[(project, role, failure_code)] += 1
                if status in {"dispatching", "running"}:
                    attempt_running_counts[(project, role)] += 1
                if int(row["attempt_no"] or 0) > 1:
                    retry_counts[(project, role)] += 1
                completion_mode_counts[(project, role, completion_mode)] += 1
                if row["ended_at_ms"]:
                    start_ms = int(row["started_at_ms"] or row["created_at_ms"] or row["ended_at_ms"])
                    duration = max((int(row["ended_at_ms"]) - start_ms) / 1000.0, 0.0)
                    attempt_runtime_buckets[(project, role, completion_mode)].append(duration)
                if status == "dispatching":
                    updated_age = max(now_seconds - (int(row["created_at_ms"] or 0) / 1000.0), 0.0)
                    if updated_age >= self.config.stale_attempt_seconds:
                        stale_dispatch_counts[(project, role)] += 1

            for (project, role, status), count in sorted(attempt_status_counts.items()):
                attempts_total.labels(**self.base_labels, layer="L3", project=project, role=role, attempt_status=status).set(float(count))
            for (project, role, completion_mode), count in sorted(attempt_success_counts.items()):
                attempt_success_total.labels(**self.base_labels, layer="L3", project=project, role=role, completion_mode=completion_mode).set(float(count))
            for (project, role, failure_code), count in sorted(attempt_failure_counts.items()):
                attempt_failure_total.labels(**self.base_labels, layer="L3", project=project, role=role, failure_code=failure_code).set(float(count))
            for (project, role), count in sorted(attempt_running_counts.items()):
                attempt_running_total.labels(**self.base_labels, layer="L3", project=project, role=role).set(float(count))
            for (project, role), count in sorted(retry_counts.items()):
                attempt_retry_total.labels(**self.base_labels, layer="L3", project=project, role=role).set(float(count))
            for (project, role, completion_mode), count in sorted(completion_mode_counts.items()):
                callback_completion_modes_total.labels(**self.base_labels, layer="L3", project=project, role=role, completion_mode=completion_mode).set(float(count))
            for (project, role, completion_mode), values in sorted(attempt_runtime_buckets.items()):
                attempt_runtime_seconds.labels(**self.base_labels, layer="L3", project=project, role=role, completion_mode=completion_mode, stat="avg").set(float(statistics.fmean(values)))
                attempt_runtime_seconds.labels(**self.base_labels, layer="L3", project=project, role=role, completion_mode=completion_mode, stat="p95").set(percentile(values, 0.95))
            for (project, role), count in sorted(stale_dispatch_counts.items()):
                stale_dispatch_total.labels(**self.base_labels, layer="L2", project=project, role=role).set(float(count))

            close_rows = conn.execute(
                """
                select p.project_key,
                       coalesce(json_extract(a.details_json, '$.resolution'), 'unknown') as resolution,
                       count(*) as total
                from issue_activities a
                join issues i on i.id = a.issue_id
                join projects p on p.id = i.project_id
                where a.action_type = 'issue_closed'
                group by p.project_key, resolution
                """
            ).fetchall()
            for row in close_rows:
                issue_closed_total.labels(
                    **self.base_labels,
                    layer="L3",
                    project=str(row["project_key"]),
                    resolution=str(row["resolution"] or "unknown"),
                ).set(float(row["total"] or 0.0))

            activity_rows = conn.execute(
                """
                select a.issue_id, p.project_key, a.action_type, a.details_json, a.created_at_ms
                from issue_activities a
                join issues i on i.id = a.issue_id
                join projects p on p.id = i.project_id
                where a.action_type in ('human_enqueued', 'human_resolved')
                order by a.issue_id asc, a.created_at_ms asc
                """
            ).fetchall()
            roundtrip_counts: Counter[tuple[str, str, str]] = Counter()
            latest_human_type_by_issue: dict[str, str] = {}
            for row in activity_rows:
                issue_id = str(row["issue_id"])
                project = str(row["project_key"])
                details = safe_json_loads(row["details_json"])
                if not isinstance(details, dict):
                    details = {}
                if row["action_type"] == "human_enqueued":
                    human_type = str(details.get("human_type") or "unknown")
                    latest_human_type_by_issue[issue_id] = human_type
                    roundtrip_counts[(project, human_type, "enqueued")] += 1
                else:
                    resolution = str(details.get("resolution") or "unknown")
                    human_type = latest_human_type_by_issue.get(issue_id) or infer_human_type_from_status(details.get("new_status"))
                    roundtrip_counts[(project, human_type, resolution)] += 1
            for (project, human_type, resolution), count in sorted(roundtrip_counts.items()):
                human_roundtrip_total.labels(
                    **self.base_labels,
                    layer="L3",
                    project=project,
                    human_type=human_type,
                    resolution=resolution,
                ).set(float(count))
        finally:
            conn.close()

        registry_counts: Counter[tuple[str, str, str]] = Counter()
        session_keys: list[tuple[str, str, str]] = []
        project_tagged_ok = True
        unique_session_keys_ok = True
        binding_match_ok = True
        if isinstance(session_registry, dict):
            for key, payload in session_registry.items():
                if not isinstance(payload, dict):
                    continue
                project = str(payload.get("project_key") or "shared")
                role = str(payload.get("role") or "unknown")
                entry_status = str(payload.get("status") or "unknown")
                registry_counts[(project, role, entry_status)] += 1
                current_session_key = str(payload.get("current_session_key") or "")
                if project != "shared":
                    if f":project:{project}" not in current_session_key:
                        project_tagged_ok = False
                    session_keys.append((project, role, current_session_key))
        seen_session_keys: dict[str, tuple[str, str]] = {}
        for project, role, session_key in session_keys:
            if not session_key:
                binding_match_ok = False
                continue
            prior = seen_session_keys.get(session_key)
            if prior and prior != (project, role):
                unique_session_keys_ok = False
            else:
                seen_session_keys[session_key] = (project, role)
        for (project, role, entry_status), count in sorted(registry_counts.items()):
            session_registry_entries_total.labels(
                **self.base_labels,
                layer="L2",
                project=project,
                role=role,
                entry_status=entry_status,
            ).set(float(count))
        queue_isolation_health.labels(**self.base_labels, layer="L2", check="project_tagged").set(1.0 if project_tagged_ok else 0.0)
        queue_isolation_health.labels(**self.base_labels, layer="L2", check="unique_session_key").set(1.0 if unique_session_keys_ok else 0.0)
        queue_isolation_health.labels(**self.base_labels, layer="L2", check="binding_match").set(1.0 if binding_match_ok else 0.0)
        overall_ok = project_tagged_ok and unique_session_keys_ok and binding_match_ok
        queue_isolation_health.labels(**self.base_labels, layer="L2", check="overall").set(1.0 if overall_ok else 0.0)

        reconcile_counts: Counter[tuple[str, str]] = Counter()
        try:
            for raw_line in Path(self.config.worker_actions_path).read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                kind = RECONCILE_KIND_MAP.get(str(payload.get("kind") or ""))
                if not kind:
                    continue
                issue_id = str(payload.get("issue_id") or "")
                project = issue_project_map.get(issue_id, "shared")
                reconcile_counts[(project, kind)] += 1
        except Exception:
            pass
        for (project, reconcile_type), count in sorted(reconcile_counts.items()):
            reconcile_events_total.labels(
                **self.base_labels,
                layer="L2",
                project=project,
                reconcile_type=reconcile_type,
            ).set(float(count))

        return generate_latest(registry)


if __name__ == "__main__":
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    config = parse_args()
    collector = AgentTeamCollector(config)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/metrics", "/metrics?format=prometheus"}:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            payload = collector.collect()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    server.serve_forever()
