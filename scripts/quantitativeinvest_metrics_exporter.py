#!/usr/bin/env python3
"""
QuantitativeInvest Backtest Metrics Exporter.
Exposes backtest result metrics in Prometheus exposition format.

Reads backtest results from JSON files and serves them at /metrics endpoint
for Prometheus scraping.

Data flow:
  Backtest engine (Issue #9) → writes result JSON →
  This exporter → Prometheus scrape → Grafana dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, Gauge, generate_latest

# Default paths
DEFAULT_DATA_DIR = '/root/.openclaw/workspace/quantitativeinvest/backtest_results'
DEFAULT_RESULTS_FILE = 'latest_results.json'
DEFAULT_LISTEN_HOST = '127.0.0.1'
DEFAULT_LISTEN_PORT = 19170
DEFAULT_ENV = 'local'
DEFAULT_INSTANCE = os.uname().nodename


@dataclass
class ExporterConfig:
    data_dir: str
    results_file: str
    listen_host: str
    listen_port: int
    env: str
    instance: str


def parse_args() -> ExporterConfig:
    parser = argparse.ArgumentParser(
        description='Expose QuantitativeInvest backtest metrics for Prometheus scraping.'
    )
    parser.add_argument(
        '--data-dir',
        default=os.environ.get('QI_EXPORTER_DATA_DIR', DEFAULT_DATA_DIR),
        help='Directory containing backtest result JSON files',
    )
    parser.add_argument(
        '--results-file',
        default=os.environ.get('QI_EXPORTER_RESULTS_FILE', DEFAULT_RESULTS_FILE),
        help='Name of the main results file to read',
    )
    parser.add_argument(
        '--listen-host',
        default=os.environ.get('QI_EXPORTER_HOST', DEFAULT_LISTEN_HOST),
    )
    parser.add_argument(
        '--listen-port',
        type=int,
        default=int(os.environ.get('QI_EXPORTER_PORT', str(DEFAULT_LISTEN_PORT))),
    )
    parser.add_argument(
        '--env',
        default=os.environ.get('QI_EXPORTER_ENV', DEFAULT_ENV),
    )
    parser.add_argument(
        '--instance',
        default=os.environ.get('QI_EXPORTER_INSTANCE') or os.uname().nodename,
    )
    args = parser.parse_args()
    return ExporterConfig(
        data_dir=args.data_dir,
        results_file=args.results_file,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        env=args.env,
        instance=args.instance,
    )


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON file, return default on error."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        pass
    return default


def load_backtest_results(data_dir: Path, results_file: str) -> list[dict]:
    """
    Load backtest results.
    Looks for:
      1. {data_dir}/{results_file}  — single combined results file
      2. {data_dir}/runs/*.json     — individual run JSON files
    Returns list of result dicts.
    """
    main_path = data_dir / results_file
    results = []

    # Try main file first
    data = load_json(main_path)
    if data is not None:
        if isinstance(data, list):
            results.extend(data)
        elif isinstance(data, dict):
            results.append(data)

    # Also scan runs/ directory
    runs_dir = data_dir / 'runs'
    if runs_dir.exists():
        for fpath in sorted(runs_dir.glob('*.json')):
            item = load_json(fpath)
            if item and isinstance(item, dict):
                results.append(item)

    return results


def _sanitize(v: Any) -> float:
    """Coerce a value to float, return 0.0 on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _params_to_label(params: dict) -> str:
    """Serialize strategy params dict into a compact label string."""
    if not params:
        return 'default'
    parts = [f'{k}={v}' for k, v in sorted(params.items())]
    return ';'.join(parts)


def _safe_str(v: Any) -> str:
    return str(v).replace('"', '').replace('\\', '')


class BacktestCollector:
    """
    Reads backtest result JSON files and converts them to Prometheus metrics.

    Expected JSON schema per backtest run:
    {
      "run_id": "run_001",
      "strategy_name": "ma_cross_002",
      "strategy_type": "trend_following",   # trend_following | mean_reversion
      "params": {"fast_period": 5, "slow_period": 20},
      "start_date": "2023-01-01",
      "end_date": "2024-12-31",
      "equity_curve": [   # daily equity values, normalized to starting=100
        {"date": "2023-01-01", "value": 100.0},
        {"date": "2023-01-02", "value": 100.5},
        ...
      ],
      "total_return_pct": 25.3,
      "sharpe_ratio": 1.45,
      "max_drawdown_pct": -12.7,
      "win_rate_pct": 58.2,
      "total_trades": 142,
      "winning_trades": 82,
      "losing_trades": 60,
      "avg_win_pct": 3.2,
      "avg_loss_pct": -2.1,
      "benchmark_return_pct": 18.5,   # benchmark (e.g. CSI 300) return for comparison
      "annual_return_pct": 12.1,
      "volatility_pct": 8.7,
      "calmar_ratio": 0.95,
      "profit_factor": 1.67,
      "max_consecutive_losses": 5,
      "avg_trade_duration_days": 8.3,
      "timestamp": "2026-04-20T10:00:00Z"
    }
    """

    def __init__(self, config: ExporterConfig) -> None:
        self.config = config
        self.base_labels = {
            'env': config.env,
            'system': 'quantitativeinvest',
            'job': 'qi-backtest-exporter',
            'instance': config.instance,
        }
        self.label_keys = list(self.base_labels.keys())

    def _strategy_labels(self, run: dict) -> dict:
        """Build label dict for strategy-scoped metrics."""
        return {
            **self.base_labels,
            'strategy_name': _safe_str(run.get('strategy_name', 'unknown')),
            'strategy_type': _safe_str(run.get('strategy_type', 'unknown')),
            'params': _params_to_label(run.get('params', {})),
            'run_id': _safe_str(run.get('run_id', 'unknown')),
        }

    def collect(self) -> bytes:
        registry = CollectorRegistry()

        # --- Define all gauges upfront ---
        info = Gauge(
            'qi_backtest_info',
            'Backtest run info (value=1 if present)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type'],
            registry=registry,
        )
        total_return = Gauge(
            'qi_total_return_pct',
            'Total return percentage',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        sharpe_ratio = Gauge(
            'qi_sharpe_ratio',
            'Sharpe ratio',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        max_drawdown = Gauge(
            'qi_max_drawdown_pct',
            'Maximum drawdown percentage (negative value)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        win_rate = Gauge(
            'qi_win_rate_pct',
            'Win rate percentage',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        total_trades = Gauge(
            'qi_total_trades',
            'Total number of trades',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        winning_trades = Gauge(
            'qi_winning_trades',
            'Number of winning trades',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        losing_trades = Gauge(
            'qi_losing_trades',
            'Number of losing trades',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        benchmark_return = Gauge(
            'qi_benchmark_return_pct',
            'Benchmark return percentage (e.g. CSI 300)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        annual_return = Gauge(
            'qi_annual_return_pct',
            'Annualized return percentage',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        volatility = Gauge(
            'qi_volatility_pct',
            'Annualized volatility percentage',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        calmar_ratio = Gauge(
            'qi_calmar_ratio',
            'Calmar ratio (annual return / max drawdown)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        profit_factor = Gauge(
            'qi_profit_factor',
            'Profit factor (gross profit / gross loss)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        avg_win = Gauge(
            'qi_avg_win_pct',
            'Average winning trade percentage',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        avg_loss = Gauge(
            'qi_avg_loss_pct',
            'Average losing trade percentage (negative value)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        max_consecutive_losses = Gauge(
            'qi_max_consecutive_losses',
            'Maximum consecutive losing trades',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )
        avg_trade_duration = Gauge(
            'qi_avg_trade_duration_days',
            'Average trade duration in days',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params'],
            registry=registry,
        )

        # Equity curve — one gauge per data point (date is encoded in timestamp)
        equity_value = Gauge(
            'qi_equity_value',
            'Equity curve value (normalized to 100 at start)',
            [*self.label_keys, 'run_id', 'strategy_name', 'strategy_type', 'params', 'date'],
            registry=registry,
        )

        data_dir = Path(self.config.data_dir)
        results = load_backtest_results(data_dir, self.config.results_file)

        if not results:
            # No backtest data found — still serve metrics with info gauge set to 0
            info.labels(**self.base_labels, run_id='none', strategy_name='none', strategy_type='none').set(0)
        else:
            for run in results:
                run_id = _safe_str(run.get('run_id', 'unknown'))
                sname = _safe_str(run.get('strategy_name', 'unknown'))
                stype = _safe_str(run.get('strategy_type', 'unknown'))
                params_str = _params_to_label(run.get('params', {}))
                labels = {**self.base_labels, 'run_id': run_id, 'strategy_name': sname, 'strategy_type': stype, 'params': params_str}

                # Set info = 1 for each present run
                info.labels(**self.base_labels, run_id=run_id, strategy_name=sname, strategy_type=stype).set(1)

                # Summary metrics
                total_return.labels(**labels).set(_sanitize(run.get('total_return_pct')))
                sharpe_ratio.labels(**labels).set(_sanitize(run.get('sharpe_ratio')))
                max_drawdown.labels(**labels).set(_sanitize(run.get('max_drawdown_pct')))
                win_rate.labels(**labels).set(_sanitize(run.get('win_rate_pct')))
                total_trades.labels(**labels).set(_sanitize(run.get('total_trades')))
                winning_trades.labels(**labels).set(_sanitize(run.get('winning_trades')))
                losing_trades.labels(**labels).set(_sanitize(run.get('losing_trades')))
                benchmark_return.labels(**labels).set(_sanitize(run.get('benchmark_return_pct')))
                annual_return.labels(**labels).set(_sanitize(run.get('annual_return_pct')))
                volatility.labels(**labels).set(_sanitize(run.get('volatility_pct')))
                calmar_ratio.labels(**labels).set(_sanitize(run.get('calmar_ratio')))
                profit_factor.labels(**labels).set(_sanitize(run.get('profit_factor')))
                avg_win.labels(**labels).set(_sanitize(run.get('avg_win_pct')))
                avg_loss.labels(**labels).set(_sanitize(run.get('avg_loss_pct')))
                max_consecutive_losses.labels(**labels).set(_sanitize(run.get('max_consecutive_losses')))
                avg_trade_duration.labels(**labels).set(_sanitize(run.get('avg_trade_duration_days')))

                # Equity curve — each data point gets a metric with date label
                for point in run.get('equity_curve') or []:
                    date_str = str(point.get('date', ''))
                    value = _sanitize(point.get('value'))
                    if date_str:
                        equity_value.labels(**self.base_labels, run_id=run_id, strategy_name=sname,
                                           strategy_type=stype, params=params_str, date=date_str).set(value)

        return generate_latest(registry)


def main() -> None:
    config = parse_args()
    collector = BacktestCollector(config)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {'/metrics', '/metrics?format=prometheus'}:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'not found')
                return
            payload = collector.collect()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    print(f'QuantitativeInvest Backtest Metrics Exporter listening on '
          f'{config.listen_host}:{config.listen_port}, data_dir={config.data_dir}')
    server.serve_forever()


if __name__ == '__main__':
    main()
