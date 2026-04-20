"""QuantDB Prometheus 指标导出器。

暴露量化系统数据管道的关键指标：
- quant_stocks_total: 已覆盖股票数
- quant_records_total: 总记录数
- quant_earliest_date_unix: 最早记录日期
- quant_latest_date_unix: 最新记录日期
- quant_last_fetch_timestamp: 最后采集时间

Usage:
    python3 quant_exporter.py
    # 暴露 http://localhost:19101/metrics

需要添加到 prometheus.yml:
  - job_name: 'quant-exporter'
    static_configs:
      - targets: ['localhost:19111']
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
    start_http_server,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("quant.exporter")

# ——— Metrics ———
REGISTRY = CollectorRegistry()

QUANT_STOCKS_TOTAL = Gauge(
    "quant_stocks_total",
    "Number of stocks with data in the pipeline",
    registry=REGISTRY,
)
QUANT_RECORDS_TOTAL = Gauge(
    "quant_records_total",
    "Total number of records in the pipeline",
    registry=REGISTRY,
)
QUANT_LATEST_DATE = Gauge(
    "quant_latest_date_unix",
    "Latest trade date in the pipeline (Unix timestamp)",
    registry=REGISTRY,
)
QUANT_EARLIEST_DATE = Gauge(
    "quant_earliest_date_unix",
    "Earliest trade date in the pipeline (Unix timestamp)",
    registry=REGISTRY,
)
QUANT_FETCH_RECORDS = Counter(
    "quant_fetch_records_total",
    "Records fetched by the pipeline",
    ["stock_code", "status"],
    registry=REGISTRY,
)
QUANT_FETCH_ERRORS = Counter(
    "quant_fetch_errors_total",
    "Fetch errors",
    registry=REGISTRY,
)
QUANT_FETCH_LAST_SUCCESS = Gauge(
    "quant_fetch_last_success_timestamp",
    "Timestamp of last successful fetch",
    registry=REGISTRY,
)
QUANT_FETCH_DURATION = Gauge(
    "quant_fetch_duration_seconds",
    "Duration of last fetch run",
    registry=REGISTRY,
)

# ——— DB query ———
def update_metrics():
    """从数据库拉取最新指标。"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="127.0.0.1", port=5432,
            user="postgres", password="quantdb2026",
            database="quantdb",
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT stock_code), COUNT(*),
                   EXTRACT(EPOCH FROM MIN(trade_date))::bigint,
                   EXTRACT(EPOCH FROM MAX(trade_date))::bigint
            FROM stock_daily
        """)
        row = cur.fetchone()
        if row:
            QUANT_STOCKS_TOTAL.set(row[0] or 0)
            QUANT_RECORDS_TOTAL.set(row[1] or 0)
            QUANT_EARLIEST_DATE.set(row[2] or 0)
            QUANT_LATEST_DATE.set(row[3] or 0)
            logger.info(
                f"Metrics updated: {row[0]} stocks, {row[1]} records, "
                f"{datetime.fromtimestamp(row[2]) if row[2] else 'N/A'} ~ "
                f"{datetime.fromtimestamp(row[3]) if row[3] else 'N/A'}"
            )
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to update metrics from DB: {e}")


def metrics_app(environ, start_response):
    """轻量 WSGI 指标端点。"""
    if environ["PATH_INFO"] == "/metrics":
        update_metrics()
        output = generate_latest(REGISTRY)
        start_response("200 OK", [
            ("Content-Type", "text/plain; version=0.0.4; charset=utf-8"),
            ("Content-Length", str(len(output))),
        ])
        return [output]
    elif environ["PATH_INFO"] == "/health":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"OK"]
    else:
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]


def run_server(port: int = 19111):
    """运行 HTTP 服务器。"""
    import socketserver
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(4096).strip()
            if b"GET /metrics" in data or b"GET /health" in data:
                path = b"/metrics" if b"metrics" in data else b"/health"
                status = b"200 OK"
                content_type = b"text/plain; version=0.0.4" if b"metrics" in data else b"text/plain"
                if b"metrics" in data:
                    update_metrics()
                    content = generate_latest(REGISTRY)
                else:
                    content = b"OK"
                response = (
                    b"HTTP/1.1 " + status + b"\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
                    b"Content-Length: " + str(len(content)).encode() + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n" + content
                )
                self.request.sendall(response)

    logger.info(f"Starting quant exporter on port {port}")
    server = socketserver.TCPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="QuantDB Prometheus Metrics Exporter")
    parser.add_argument("--port", type=int, default=19111, help="HTTP port (default: 19111)")
    args = parser.parse_args()
    run_server(port=args.port)
