#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from prometheus_client import CollectorRegistry, Gauge, generate_latest

NEWAPI_SERVICE_PATH = Path("/etc/systemd/system/new-api.service")


@dataclass
class ExporterConfig:
    db_path: str
    sql_dsn: str
    listen_host: str
    listen_port: int
    env: str
    project: str
    system: str
    service: str
    job: str
    instance: str
    target_base_url: str


def parse_systemd_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line.startswith('Environment="') or not line.endswith('"'):
            continue
        payload = line[len('Environment="'):-1]
        if '=' not in payload:
            continue
        key, value = payload.split('=', 1)
        values[key] = value
    return values


def parse_args() -> ExporterConfig:
    service_env = parse_systemd_env(NEWAPI_SERVICE_PATH)
    parser = argparse.ArgumentParser(description="Expose NewAPI metrics for Prometheus scraping.")
    parser.add_argument("--db-path", default=os.environ.get("NEWAPI_DB_PATH", "/root/new-api/one-api.db"))
    parser.add_argument("--sql-dsn", default=os.environ.get("NEWAPI_SQL_DSN") or service_env.get("LOG_SQL_DSN") or service_env.get("SQL_DSN") or "")
    parser.add_argument("--listen-host", default=os.environ.get("NEWAPI_EXPORTER_HOST", "127.0.0.1"))
    parser.add_argument("--listen-port", type=int, default=int(os.environ.get("NEWAPI_EXPORTER_PORT", "19100")))
    parser.add_argument("--env", default=os.environ.get("NEWAPI_EXPORTER_ENV", "local"))
    parser.add_argument("--project", default=os.environ.get("NEWAPI_EXPORTER_PROJECT", "agent-team-grafana"))
    parser.add_argument("--system", default=os.environ.get("NEWAPI_EXPORTER_SYSTEM", "newapi"))
    parser.add_argument("--service", default=os.environ.get("NEWAPI_EXPORTER_SERVICE", "new-api"))
    parser.add_argument("--job", default=os.environ.get("NEWAPI_EXPORTER_JOB", "newapi-exporter"))
    parser.add_argument("--instance", default=os.environ.get("NEWAPI_EXPORTER_INSTANCE") or os.uname().nodename)
    parser.add_argument("--target-base-url", default=os.environ.get("NEWAPI_TARGET_BASE_URL", "http://127.0.0.1:3000"))
    args = parser.parse_args()
    return ExporterConfig(**vars(args))


class DatabaseClient:
    def fetch_rows(self, query: str) -> list[dict]:
        raise NotImplementedError

    def fetch_value(self, query: str, default: float = 0.0) -> float:
        rows = self.fetch_rows(query)
        if not rows:
            return default
        row = rows[0]
        if not row:
            return default
        return float(next(iter(row.values())) or default)


class SQLiteClient(DatabaseClient):
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def fetch_rows(self, query: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


class PostgresClient(DatabaseClient):
    def __init__(self, dsn: str) -> None:
        parsed = urlparse(dsn)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError(f"Unsupported SQL_DSN scheme: {parsed.scheme}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = str(parsed.port or 5432)
        self.user = parsed.username or "postgres"
        self.password = parsed.password or ""
        self.dbname = (parsed.path or "/postgres").lstrip("/")

    def fetch_rows(self, query: str) -> list[dict]:
        wrapped = f"select coalesce(json_agg(t), '[]'::json)::text from ({query}) t"
        env = os.environ.copy()
        if self.password:
            env["PGPASSWORD"] = self.password
        result = subprocess.run(
            [
                "psql",
                "-h",
                self.host,
                "-p",
                self.port,
                "-U",
                self.user,
                "-d",
                self.dbname,
                "-At",
                "-c",
                wrapped,
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        payload = result.stdout.strip() or "[]"
        return json.loads(payload)


class NewAPICollector:
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
        self.pid = self._find_newapi_pid()
        self.db = self._build_db_client()

    def _build_db_client(self) -> DatabaseClient:
        if self.config.sql_dsn:
            return PostgresClient(self.config.sql_dsn)
        return SQLiteClient(self.config.db_path)

    def _find_newapi_pid(self) -> int | None:
        proc_root = Path("/proc")
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text().strip() == "new-api":
                    return int(entry.name)
            except OSError:
                continue
        return None

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

    def _process_open_fds(self) -> float:
        if self.pid is None:
            return 0.0
        try:
            return float(len(list(Path(f"/proc/{self.pid}/fd").iterdir())))
        except OSError:
            return 0.0

    def _http_health(self) -> float:
        try:
            with urlopen(f"{self.config.target_base_url}/api/status", timeout=5) as response:
                return 1.0 if response.status == 200 else 0.0
        except Exception:
            return 0.0

    def _error_code_from_other(self, other: str | None) -> str:
        if not other:
            return "unknown"
        for pattern in (r'"status"\s*:\s*"([^"]+)"', r'"end_reason"\s*:\s*"([^"]+)"'):
            match = re.search(pattern, other)
            if match:
                return match.group(1)
        return "unknown"

    def collect(self) -> bytes:
        registry = CollectorRegistry()
        info = Gauge("newapi_exporter_build_info", "Exporter build info", [*self.base_labels.keys(), "layer"], registry=registry)
        info.labels(**self.base_labels, layer="L0").set(1)

        request_total = Gauge("newapi_requests_total", "Request count in rolling 1h window", [*self.base_labels.keys(), "layer", "model", "channel_id", "channel_name", "status_family"], registry=registry)
        request_success = Gauge("newapi_request_success_total", "Successful request count in rolling 1h window", [*self.base_labels.keys(), "layer", "model", "channel_id", "channel_name"], registry=registry)
        request_error = Gauge("newapi_request_error_total", "Error request count in rolling 1h window", [*self.base_labels.keys(), "layer", "model", "channel_id", "channel_name", "status_code", "error_code"], registry=registry)
        requests_by_model = Gauge("newapi_requests_by_model_total", "Request count by model in rolling 1h window", [*self.base_labels.keys(), "layer", "model"], registry=registry)
        tokens_consumed = Gauge("newapi_tokens_consumed_total", "Token consumption in rolling 1h window", [*self.base_labels.keys(), "layer", "model", "channel_id", "channel_name", "token_name"], registry=registry)
        quota_consumed = Gauge("newapi_quota_consumed_total", "Quota consumption in rolling 1h window", [*self.base_labels.keys(), "layer", "model", "channel_id", "channel_name", "token_name"], registry=registry)
        rpm = Gauge("newapi_rpm", "Requests per minute in the last minute", [*self.base_labels.keys(), "layer"], registry=registry)
        tpm = Gauge("newapi_tpm", "Tokens per minute in the last minute", [*self.base_labels.keys(), "layer"], registry=registry)
        channel_error_rate = Gauge("newapi_channel_error_rate", "Error rate per channel in rolling 1h window", [*self.base_labels.keys(), "layer", "channel_id", "channel_name"], registry=registry)
        errors_by_error_code = Gauge("newapi_errors_by_error_code_total", "Error count by derived error code in rolling 1h window", [*self.base_labels.keys(), "layer", "error_code", "status_code"], registry=registry)
        channel_health = Gauge("newapi_channel_health_score", "Derived channel health score", [*self.base_labels.keys(), "layer", "channel_id", "channel_name"], registry=registry)
        topup_events = Gauge("newapi_topup_events_total", "Topup events in rolling 7d window", [*self.base_labels.keys(), "layer", "status", "payment_gateway"], registry=registry)
        subscription_events = Gauge("newapi_subscription_events_total", "Subscription events in rolling 7d window", [*self.base_labels.keys(), "layer", "status", "provider"], registry=registry)
        process_cpu = Gauge("newapi_process_cpu_percent", "NewAPI process CPU percent", [*self.base_labels.keys(), "layer"], registry=registry)
        process_mem = Gauge("newapi_process_memory_bytes", "NewAPI process RSS memory bytes", [*self.base_labels.keys(), "layer"], registry=registry)
        process_fds = Gauge("newapi_process_open_fds", "NewAPI process open file descriptors", [*self.base_labels.keys(), "layer"], registry=registry)
        db_health = Gauge("newapi_db_connection_health", "Database connectivity health", [*self.base_labels.keys(), "layer", "db_type"], registry=registry)
        errlog_enabled = Gauge("newapi_error_log_enabled", "Whether error log export is enabled", [*self.base_labels.keys(), "layer"], registry=registry)
        up = Gauge("newapi_up", "Whether exporter can reach NewAPI status endpoint", [*self.base_labels.keys(), "layer"], registry=registry)
        channel_status = Gauge("newapi_channel_status", "Channel status", [*self.base_labels.keys(), "layer", "channel_id", "channel_name", "group_name"], registry=registry)
        channel_used_quota = Gauge("newapi_channel_used_quota_total", "Used quota by channel", [*self.base_labels.keys(), "layer", "channel_id", "channel_name", "group_name"], registry=registry)
        channel_response_ms = Gauge("newapi_channel_response_time_ms", "Recorded channel response time", [*self.base_labels.keys(), "layer", "channel_id", "channel_name", "group_name"], registry=registry)
        channel_balance = Gauge("newapi_channel_balance", "Recorded channel balance", [*self.base_labels.keys(), "layer", "channel_id", "channel_name", "group_name"], registry=registry)
        latest_log = Gauge("newapi_latest_log_timestamp", "Latest log timestamp", [*self.base_labels.keys(), "layer"], registry=registry)
        earliest_log = Gauge("newapi_earliest_log_timestamp", "Earliest log timestamp", [*self.base_labels.keys(), "layer"], registry=registry)
        total_logs = Gauge("newapi_logs_total", "Total log rows", [*self.base_labels.keys(), "layer"], registry=registry)

        now = int(time.time())
        one_hour_ago = now - 3600
        one_minute_ago = now - 60
        seven_days_ago = now - 7 * 24 * 3600

        default_model = "unknown"
        default_channel_id = "0"
        default_channel_name = "unknown"
        default_token_name = "unknown"
        request_total.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name, status_family="success").set(0)
        request_total.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name, status_family="error").set(0)
        request_success.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name).set(0)
        request_error.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name, status_code="0", error_code="unknown").set(0)
        requests_by_model.labels(**self.base_labels, layer="L3", model=default_model).set(0)
        tokens_consumed.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name, token_name=default_token_name).set(0)
        quota_consumed.labels(**self.base_labels, layer="L3", model=default_model, channel_id=default_channel_id, channel_name=default_channel_name, token_name=default_token_name).set(0)
        rpm.labels(**self.base_labels, layer="L3").set(0)
        tpm.labels(**self.base_labels, layer="L3").set(0)
        channel_error_rate.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name).set(0)
        errors_by_error_code.labels(**self.base_labels, layer="L2", error_code="unknown", status_code="0").set(0)
        channel_health.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name).set(0)
        topup_events.labels(**self.base_labels, layer="L3", status="none", payment_gateway="none").set(0)
        subscription_events.labels(**self.base_labels, layer="L3", status="none", provider="none").set(0)
        channel_status.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name, group_name="default").set(0)
        channel_used_quota.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name, group_name="default").set(0)
        channel_response_ms.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name, group_name="default").set(0)
        channel_balance.labels(**self.base_labels, layer="L2", channel_id=default_channel_id, channel_name=default_channel_name, group_name="default").set(0)

        db_type = "postgres" if self.config.sql_dsn else "sqlite"
        db_health.labels(**self.base_labels, layer="L1", db_type=db_type).set(1)
        errlog_enabled.labels(**self.base_labels, layer="L1").set(0)
        up.labels(**self.base_labels, layer="L0").set(self._http_health())
        process_cpu.labels(**self.base_labels, layer="L1").set(self._process_cpu_percent())
        process_mem.labels(**self.base_labels, layer="L1").set(self._process_memory_bytes())
        process_fds.labels(**self.base_labels, layer="L1").set(self._process_open_fds())

        try:
            logs = self.db.fetch_rows(
                f"""
                select
                    coalesce(nullif(l.model_name, ''), 'unknown') as model_name,
                    coalesce(l.channel_id, 0) as channel_id,
                    coalesce(c.name, 'channel-' || l.channel_id, 'unknown') as channel_name,
                    coalesce(nullif(l.token_name, ''), 'unknown') as token_name,
                    coalesce(l.type, 0) as log_type,
                    coalesce(l.quota, 0) as quota,
                    coalesce(l.prompt_tokens, 0) as prompt_tokens,
                    coalesce(l.completion_tokens, 0) as completion_tokens,
                    coalesce(l.created_at, 0) as created_at,
                    coalesce(l.other, '') as other
                from logs l
                left join channels c on c.id = l.channel_id
                where l.created_at >= {one_hour_ago} and l.type in (2, 5)
                """
            )
            last_minute_rows = [row for row in logs if int(row["created_at"] or 0) >= one_minute_ago]
            rpm.labels(**self.base_labels, layer="L3").set(float(len(last_minute_rows)))
            tpm.labels(**self.base_labels, layer="L3").set(float(sum((row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0) for row in last_minute_rows)))

            aggregate: dict[tuple[str, str, str, str], dict[str, float]] = {}
            by_model: dict[str, float] = {}
            channel_rollup: dict[tuple[str, str], dict[str, float]] = {}
            error_rollup: dict[str, float] = {}
            for row in logs:
                model = str(row["model_name"] or "unknown")
                channel_id = str(row["channel_id"] or 0)
                channel_name = str(row["channel_name"] or f"channel-{channel_id}")
                token_name = str(row["token_name"] or "unknown")
                key = (model, channel_id, channel_name, token_name)
                item = aggregate.setdefault(key, {"total": 0.0, "success": 0.0, "error": 0.0, "tokens": 0.0, "quota": 0.0})
                item["total"] += 1.0
                item["tokens"] += float((row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0))
                item["quota"] += float(row["quota"] or 0)
                if int(row["log_type"] or 0) == 2:
                    item["success"] += 1.0
                elif int(row["log_type"] or 0) == 5:
                    item["error"] += 1.0
                    error_code = self._error_code_from_other(str(row["other"] or ""))
                    error_rollup[error_code] = error_rollup.get(error_code, 0.0) + 1.0
                by_model[model] = by_model.get(model, 0.0) + 1.0
                channel_item = channel_rollup.setdefault((channel_id, channel_name), {"total": 0.0, "error": 0.0})
                channel_item["total"] += 1.0
                if int(row["log_type"] or 0) == 5:
                    channel_item["error"] += 1.0

            for (model, channel_id, channel_name, token_name), values in aggregate.items():
                request_total.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name, status_family="success").set(values["success"])
                request_total.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name, status_family="error").set(values["error"])
                request_success.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name).set(values["success"])
                request_error.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name, status_code="0", error_code="all").set(values["error"])
                tokens_consumed.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name, token_name=token_name).set(values["tokens"])
                quota_consumed.labels(**self.base_labels, layer="L3", model=model, channel_id=channel_id, channel_name=channel_name, token_name=token_name).set(values["quota"])
            for model, count in by_model.items():
                requests_by_model.labels(**self.base_labels, layer="L3", model=model).set(count)
            for (channel_id, channel_name), values in channel_rollup.items():
                rate = values["error"] / values["total"] if values["total"] else 0.0
                channel_error_rate.labels(**self.base_labels, layer="L2", channel_id=channel_id, channel_name=channel_name).set(rate)
                channel_health.labels(**self.base_labels, layer="L2", channel_id=channel_id, channel_name=channel_name).set(max(0.0, 1.0 - rate))
            for error_code, count in error_rollup.items():
                errors_by_error_code.labels(**self.base_labels, layer="L2", error_code=error_code, status_code="0").set(count)

            channels = self.db.fetch_rows("select id, coalesce(name, 'unknown') as name, coalesce(status, 0) as status, coalesce(\"group\", 'default') as group_name, coalesce(used_quota, 0) as used_quota, coalesce(response_time, 0) as response_time, coalesce(balance, 0) as balance from channels")
            for row in channels:
                channel_id = str(row["id"])
                labels = {**self.base_labels, "layer": "L2", "channel_id": channel_id, "channel_name": str(row["name"]), "group_name": str(row["group_name"])}
                channel_status.labels(**labels).set(float(row["status"] or 0))
                channel_used_quota.labels(**labels).set(float(row["used_quota"] or 0))
                channel_response_ms.labels(**labels).set(float(row["response_time"] or 0))
                channel_balance.labels(**labels).set(float(row["balance"] or 0))

            topups = self.db.fetch_rows(f"select coalesce(status, 'unknown') as status, coalesce(payment_method, 'unknown') as payment_method, count(*) as event_count from top_ups where create_time >= {seven_days_ago} group by status, payment_method")
            for row in topups:
                topup_events.labels(**self.base_labels, layer="L3", status=str(row["status"]), payment_gateway=str(row["payment_method"])).set(float(row["event_count"] or 0))

            subscriptions = self.db.fetch_rows(f"select coalesce(status, 'unknown') as status, coalesce(payment_method, 'unknown') as payment_method, count(*) as event_count from subscription_orders where create_time >= {seven_days_ago} group by status, payment_method")
            for row in subscriptions:
                subscription_events.labels(**self.base_labels, layer="L3", status=str(row["status"]), provider=str(row["payment_method"])).set(float(row["event_count"] or 0))

            option_rows = self.db.fetch_rows("select key, value from options where key in ('ErrorLogEnabled', 'LogConsumeEnabled', 'DataExportEnabled')")
            options = {str(row["key"]): str(row["value"]) for row in option_rows}
            errlog_enabled.labels(**self.base_labels, layer="L1").set(1.0 if options.get("ErrorLogEnabled", "false").lower() == "true" else 0.0)

            window = self.db.fetch_rows("select max(created_at) as latest_ts, min(created_at) as earliest_ts, count(*) as total_count from logs")
            if window:
                latest_log.labels(**self.base_labels, layer="L3").set(float(window[0]["latest_ts"] or 0))
                earliest_log.labels(**self.base_labels, layer="L3").set(float(window[0]["earliest_ts"] or 0))
                total_logs.labels(**self.base_labels, layer="L3").set(float(window[0]["total_count"] or 0))
        except subprocess.CalledProcessError as exc:
            db_health.labels(**self.base_labels, layer="L1", db_type=db_type).set(0)
            raise RuntimeError(f"psql query failed: {exc.stderr.strip() or exc.stdout.strip()}") from exc
        except Exception:
            db_health.labels(**self.base_labels, layer="L1", db_type=db_type).set(0)
            raise

        return generate_latest(registry)


if __name__ == "__main__":
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    config = parse_args()
    collector = NewAPICollector(config)

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
