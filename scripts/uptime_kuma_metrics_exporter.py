#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from prometheus_client import CollectorRegistry, Gauge, generate_latest

KUMA_DB_PATH = Path("/opt/uptime-kuma/data/kuma.db")
PROCESS_NAME = "uptime-kuma"


@dataclass
class ExporterConfig:
    db_path: str
    listen_host: str
    listen_port: int
    env: str
    project: str
    system: str
    service: str
    job: str
    instance: str
    target_base_url: str


def parse_args() -> ExporterConfig:
    parser = argparse.ArgumentParser(description="Expose Uptime Kuma metrics for Prometheus scraping.")
    parser.add_argument("--db-path", default=os.environ.get("KUMA_DB_PATH", str(KUMA_DB_PATH)))
    parser.add_argument("--listen-host", default=os.environ.get("KUMA_EXPORTER_HOST", "127.0.0.1"))
    parser.add_argument("--listen-port", type=int, default=int(os.environ.get("KUMA_EXPORTER_PORT", "19110")))
    parser.add_argument("--env", default=os.environ.get("KUMA_EXPORTER_ENV", "local"))
    parser.add_argument("--project", default=os.environ.get("KUMA_EXPORTER_PROJECT", "agent-team-grafana"))
    parser.add_argument("--system", default=os.environ.get("KUMA_EXPORTER_SYSTEM", "uptime-kuma"))
    parser.add_argument("--service", default=os.environ.get("KUMA_EXPORTER_SERVICE", "uptime-kuma"))
    parser.add_argument("--job", default=os.environ.get("KUMA_EXPORTER_JOB", "uptime-kuma-exporter"))
    parser.add_argument("--instance", default=os.environ.get("KUMA_EXPORTER_INSTANCE") or os.uname().nodename)
    parser.add_argument("--target-base-url", default=os.environ.get("KUMA_TARGET_BASE_URL", "http://127.0.0.1:3001"))
    args = parser.parse_args()
    return ExporterConfig(**vars(args))


class KumaCollector:
    def __init__(self, config: ExporterConfig) -> None:
        self.config = config
        self.base_labels = {
            "env": config.env,
            "project": config.project,
            "system": config.system,
            "service": config.service,
            "job": config.job,
            "instance": config.instance,
        }
        self.pid = self._find_pid()

    def _find_pid(self) -> int | None:
        proc_root = Path("/proc")
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text().strip() == PROCESS_NAME:
                    return int(entry.name)
            except OSError:
                continue
        return None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _process_cpu_percent(self) -> float:
        if self.pid is None:
            return 0.0
        stat_path = Path(f"/proc/{self.pid}/stat")
        try:
            parts = stat_path.read_text().split()
            utime = int(parts[13])
            stime = int(parts[14])
            starttime = int(parts[21])
            clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
            process_seconds = uptime_seconds - (starttime / clk_tck)
            if process_seconds <= 0:
                return 0.0
            cpu_seconds = (utime + stime) / clk_tck
            return max(cpu_seconds / process_seconds * 100.0, 0.0)
        except (OSError, IndexError, ValueError):
            return 0.0

    def _process_memory_bytes(self) -> float:
        if self.pid is None:
            return 0.0
        try:
            resident_pages = int(Path(f"/proc/{self.pid}/statm").read_text().split()[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return float(resident_pages * page_size)
        except (OSError, IndexError, ValueError):
            return 0.0

    def _http_health(self) -> float:
        try:
            with urlopen(f"{self.config.target_base_url}/", timeout=5) as response:
                return 1.0 if response.status == 200 else 0.0
        except Exception:
            return 0.0

    def _socket_polling_health(self) -> float:
        try:
            with urlopen(f"{self.config.target_base_url}/metrics", timeout=5) as response:
                return 1.0 if response.status in {200, 401} else 0.0
        except Exception:
            return 0.0

    def collect(self) -> bytes:
        registry = CollectorRegistry()
        info = Gauge("kuma_exporter_build_info", "Exporter build info", [*self.base_labels.keys(), "layer"], registry=registry)
        info.labels(**self.base_labels, layer="L0").set(1)

        monitors_total = Gauge("kuma_monitors_total", "Total monitors", [*self.base_labels.keys(), "layer", "group", "monitor_type"], registry=registry)
        monitor_status = Gauge("kuma_monitor_status", "Current monitor status", [*self.base_labels.keys(), "layer", "group", "monitor_name", "monitor_type"], registry=registry)
        monitors_up_total = Gauge("kuma_monitors_up_total", "Current up monitor count", [*self.base_labels.keys(), "layer", "group"], registry=registry)
        monitors_down_total = Gauge("kuma_monitors_down_total", "Current down monitor count", [*self.base_labels.keys(), "layer", "group"], registry=registry)
        response_ms = Gauge("kuma_monitor_response_time_ms", "Monitor response time", [*self.base_labels.keys(), "layer", "group", "monitor_name", "monitor_type"], registry=registry)
        group_availability = Gauge("kuma_group_availability_ratio", "Availability ratio by group", [*self.base_labels.keys(), "layer", "group"], registry=registry)
        retry_policy = Gauge("kuma_monitor_retry_policy", "Monitor retry policy", [*self.base_labels.keys(), "layer", "group", "monitor_name", "max_retries", "retry_interval"], registry=registry)
        group_alert_scope = Gauge("kuma_group_alerting_scope", "Group alerting scope info", [*self.base_labels.keys(), "layer", "group", "alert_scope"], registry=registry)
        failures_total = Gauge("kuma_monitor_failures_total", "Recent monitor failures", [*self.base_labels.keys(), "layer", "group", "monitor_name", "monitor_type"], registry=registry)
        recoveries_total = Gauge("kuma_monitor_recoveries_total", "Recent monitor recoveries", [*self.base_labels.keys(), "layer", "group", "monitor_name", "monitor_type"], registry=registry)
        group_avg_response = Gauge("kuma_group_avg_response_time_ms", "Average response time by group", [*self.base_labels.keys(), "layer", "group"], registry=registry)
        cert_expiry_days = Gauge("kuma_cert_expiry_days", "Domain expiry days", [*self.base_labels.keys(), "layer", "group", "monitor_name"], registry=registry)
        flap_score = Gauge("kuma_monitor_flap_score", "Flap score per monitor in last 24h", [*self.base_labels.keys(), "layer", "group", "monitor_name", "monitor_type"], registry=registry)
        process_cpu = Gauge("kuma_process_cpu_percent", "Uptime Kuma process CPU percent", [*self.base_labels.keys(), "layer"], registry=registry)
        process_mem = Gauge("kuma_process_memory_bytes", "Uptime Kuma process RSS memory bytes", [*self.base_labels.keys(), "layer"], registry=registry)
        proxy_health = Gauge("kuma_proxy_health", "Reverse proxy health", [*self.base_labels.keys(), "layer"], registry=registry)
        socket_health = Gauge("kuma_socket_polling_health", "Socket polling health", [*self.base_labels.keys(), "layer"], registry=registry)

        process_cpu.labels(**self.base_labels, layer="L1").set(self._process_cpu_percent())
        process_mem.labels(**self.base_labels, layer="L1").set(self._process_memory_bytes())
        proxy_health.labels(**self.base_labels, layer="L1").set(self._http_health())
        socket_health.labels(**self.base_labels, layer="L1").set(self._socket_polling_health())
        group_alert_scope.labels(**self.base_labels, layer="L3", group="__all__", alert_scope="monitor-level").set(1)

        conn = self._connect()
        try:
            monitor_rows = conn.execute(
                """
                select m.id, m.name, m.type, m.maxretries, m.retry_interval, m.parent,
                       case when p.name is not null then p.name else '__ungrouped__' end as group_name
                from monitor m
                left join monitor p on p.id = m.parent and p.type = 'group'
                where m.active = 1
                order by m.id
                """
            ).fetchall()
            groups = {}
            for row in monitor_rows:
                if row["type"] == "group":
                    groups[row["id"]] = row["name"]

            now = int(time.time())
            day_ago = now - 24 * 3600

            status_map = {}
            response_map = {}
            failure_map = {}
            recovery_map = {}
            flap_map = {}
            heartbeat_rows = conn.execute(
                """
                select monitor_id, status, time, ping, retries
                from heartbeat
                where strftime('%s', time) >= ?
                order by time desc
                """,
                (day_ago,),
            ).fetchall()
            seen_monitor = set()
            transition_track: dict[int, list[int]] = {}
            for row in heartbeat_rows:
                monitor_id = row["monitor_id"]
                transition_track.setdefault(monitor_id, []).append(int(row["status"]))
                if monitor_id not in seen_monitor:
                    seen_monitor.add(monitor_id)
                    status_map[monitor_id] = int(row["status"])
                    response_map[monitor_id] = float(row["ping"] or 0)
                if int(row["status"]) == 0:
                    failure_map[monitor_id] = failure_map.get(monitor_id, 0) + 1
                if int(row["status"]) == 1:
                    recovery_map[monitor_id] = recovery_map.get(monitor_id, 0) + 1

            for monitor_id, states in transition_track.items():
                transitions = 0
                prev = None
                for state in reversed(states):
                    if prev is not None and state != prev:
                        transitions += 1
                    prev = state
                flap_map[monitor_id] = transitions

            group_up = {}
            group_down = {}
            group_response_samples: dict[str, list[float]] = {}
            group_counts: dict[tuple[str, str], int] = {}

            for row in monitor_rows:
                monitor_id = row["id"]
                monitor_name = row["name"]
                monitor_type = row["type"]
                group_name = row["group_name"]
                group_counts[(group_name, monitor_type)] = group_counts.get((group_name, monitor_type), 0) + 1
                if monitor_type == "group":
                    continue
                status_value = status_map.get(monitor_id, 0)
                response_value = response_map.get(monitor_id, 0.0)
                status_labels = {**self.base_labels, "layer": "L3", "group": group_name, "monitor_name": monitor_name, "monitor_type": monitor_type}
                monitor_status.labels(**status_labels).set(float(status_value))
                response_ms.labels(**status_labels).set(response_value)
                failures_total.labels(**status_labels).set(float(failure_map.get(monitor_id, 0)))
                recoveries_total.labels(**status_labels).set(float(recovery_map.get(monitor_id, 0)))
                flap_score.labels(**status_labels).set(float(flap_map.get(monitor_id, 0)))
                retry_policy.labels(**self.base_labels, layer="L3", group=group_name, monitor_name=monitor_name, max_retries=str(row["maxretries"]), retry_interval=str(row["retry_interval"])).set(1)
                if status_value == 1:
                    group_up[group_name] = group_up.get(group_name, 0) + 1
                else:
                    group_down[group_name] = group_down.get(group_name, 0) + 1
                if response_value > 0:
                    group_response_samples.setdefault(group_name, []).append(response_value)

            all_groups = {row["name"] for row in monitor_rows if row["type"] == "group"} | set(group_up) | set(group_down)
            for group_name in sorted(all_groups):
                up_count = group_up.get(group_name, 0)
                down_count = group_down.get(group_name, 0)
                total = up_count + down_count
                monitors_up_total.labels(**self.base_labels, layer="L3", group=group_name).set(float(up_count))
                monitors_down_total.labels(**self.base_labels, layer="L3", group=group_name).set(float(down_count))
                group_availability.labels(**self.base_labels, layer="L3", group=group_name).set((up_count / total) if total else 1.0)
                samples = group_response_samples.get(group_name, [])
                group_avg_response.labels(**self.base_labels, layer="L3", group=group_name).set(float(statistics.fmean(samples)) if samples else 0.0)
                group_alert_scope.labels(**self.base_labels, layer="L3", group=group_name, alert_scope="monitor-level").set(1)

            total_monitors = sum(count for (_, _), count in group_counts.items())
            monitors_total.labels(**self.base_labels, layer="L3", group="__all__", monitor_type="all").set(float(total_monitors))
            for (group_name, monitor_type), count in group_counts.items():
                monitors_total.labels(**self.base_labels, layer="L3", group=group_name, monitor_type=monitor_type).set(float(count))

            domain_rows = conn.execute("select domain, expiry from domain_expiry").fetchall()
            for row in domain_rows:
                expiry_text = row["expiry"]
                try:
                    expiry_ts = time.mktime(time.strptime(expiry_text, "%Y-%m-%d %H:%M:%S.%f")) if "." in expiry_text else time.mktime(time.strptime(expiry_text, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                days_left = max((expiry_ts - time.time()) / 86400.0, 0.0)
                cert_expiry_days.labels(**self.base_labels, layer="L3", group="External", monitor_name=row["domain"]).set(days_left)
        finally:
            conn.close()

        return generate_latest(registry)


if __name__ == "__main__":
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    config = parse_args()
    collector = KumaCollector(config)

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
