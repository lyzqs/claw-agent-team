#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from prometheus_client import CollectorRegistry, Gauge, generate_latest

ARENA_ROOT = Path('/root/.openclaw/workspace-inStreet/arena')
DATA_DIR = ARENA_ROOT / 'data'
RUNTIME_PATH = DATA_DIR / 'runtime.json'
RUN_HISTORY_PATH = DATA_DIR / 'logs' / 'runs.jsonl'
ORDER_AUDIT_PATH = DATA_DIR / 'order_audit.jsonl'
TRADE_TICKETS_PATH = DATA_DIR / 'trade_tickets.jsonl'
AI_DECISIONS_PATH = DATA_DIR / 'ai_decisions.jsonl'
DASHBOARD_HEALTH_URL = 'http://127.0.0.1:8788/health'
PROCESS_PATTERNS = {
    'arena-runtime': ('arena_runtime.py',),
    'arena-dashboard': ('dashboard_server.py',),
}


def _normalize_blocker_label(value) -> str:
    text = str(value or "").strip()
    return text or "未写明 blocker"


def _classify_review_blocker_type(value) -> str:
    text = _normalize_blocker_label(value)
    lowered = text.lower()
    if any(token in text for token in ["收盘后一小时", "盘前一小时", "交易窗口", "可提交窗口", "非运行时段", "交易日", "午间休市"]):
        return "timing_window"
    if "建议股数不足 100 股" in text:
        return "lot_size"
    if "待成交订单" in text or "pending" in lowered:
        return "pending_limit"
    if any(token in text for token in ["持仓数量已到上限", "当前已持有同一标的"]):
        return "position_limit"
    if any(token in text for token in ["今日自动下单次数已到上限", "今日已对该标的下过单"]):
        return "daily_limit"
    if "autopilot 未启用" in text:
        return "mode_disabled"
    if "分数" in text or "score" in lowered:
        return "score_floor"
    return "other"


@dataclass
class ExporterConfig:
    runtime_path: str
    run_history_path: str
    order_audit_path: str
    trade_tickets_path: str
    ai_decisions_path: str
    dashboard_health_url: str
    listen_host: str
    listen_port: int
    env: str
    project: str
    system: str
    service: str
    job: str
    instance: str


def parse_args() -> ExporterConfig:
    parser = argparse.ArgumentParser(description='Expose Arena metrics for Prometheus scraping.')
    parser.add_argument('--runtime-path', default=os.environ.get('ARENA_RUNTIME_PATH', str(RUNTIME_PATH)))
    parser.add_argument('--run-history-path', default=os.environ.get('ARENA_RUN_HISTORY_PATH', str(RUN_HISTORY_PATH)))
    parser.add_argument('--order-audit-path', default=os.environ.get('ARENA_ORDER_AUDIT_PATH', str(ORDER_AUDIT_PATH)))
    parser.add_argument('--trade-tickets-path', default=os.environ.get('ARENA_TRADE_TICKETS_PATH', str(TRADE_TICKETS_PATH)))
    parser.add_argument('--ai-decisions-path', default=os.environ.get('ARENA_AI_DECISIONS_PATH', str(AI_DECISIONS_PATH)))
    parser.add_argument('--dashboard-health-url', default=os.environ.get('ARENA_DASHBOARD_HEALTH_URL', DASHBOARD_HEALTH_URL))
    parser.add_argument('--listen-host', default=os.environ.get('ARENA_EXPORTER_HOST', '127.0.0.1'))
    parser.add_argument('--listen-port', type=int, default=int(os.environ.get('ARENA_EXPORTER_PORT', '19140')))
    parser.add_argument('--env', default=os.environ.get('ARENA_EXPORTER_ENV', 'local'))
    parser.add_argument('--project', default=os.environ.get('ARENA_EXPORTER_PROJECT', 'agent-team-grafana'))
    parser.add_argument('--system', default=os.environ.get('ARENA_EXPORTER_SYSTEM', 'arena'))
    parser.add_argument('--service', default=os.environ.get('ARENA_EXPORTER_SERVICE', 'instreet-arena'))
    parser.add_argument('--job', default=os.environ.get('ARENA_EXPORTER_JOB', 'arena-exporter'))
    parser.add_argument('--instance', default=os.environ.get('ARENA_EXPORTER_INSTANCE') or os.uname().nodename)
    args = parser.parse_args()
    return ExporterConfig(**vars(args))


def load_json(path: str, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def read_jsonl(path: str, limit: int | None = None) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding='utf-8').splitlines()
    if limit is not None:
        lines = lines[-limit:]
    items: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    return items


def parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return __import__('datetime').datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def age_seconds(ts: float | None, now: float) -> float:
    if ts is None:
        return 0.0
    return max(now - ts, 0.0)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


class ArenaCollector:
    def __init__(self, config: ExporterConfig) -> None:
        self.config = config
        self.base_labels = {
            'env': config.env,
            'project': config.project,
            'system': config.system,
            'service': config.service,
            'job': config.job,
            'instance': config.instance,
        }

    def _matching_pids(self, *patterns: str) -> list[int]:
        matches: list[int] = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / 'cmdline').read_bytes().replace(b'\x00', b' ').decode('utf-8', errors='ignore')
            except OSError:
                continue
            if all(pattern in cmdline for pattern in patterns):
                matches.append(int(entry.name))
        return matches

    def _process_cpu_percent(self, pid: int) -> float:
        try:
            parts = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8').split()
            utime = int(parts[13])
            stime = int(parts[14])
            starttime = int(parts[21])
            clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
            uptime_seconds = float(Path('/proc/uptime').read_text(encoding='utf-8').split()[0])
            process_seconds = uptime_seconds - (starttime / clk_tck)
            if process_seconds <= 0:
                return 0.0
            return max(((utime + stime) / clk_tck) / process_seconds * 100.0, 0.0)
        except Exception:
            return 0.0

    def _process_memory_bytes(self, pid: int) -> float:
        try:
            resident_pages = int(Path(f'/proc/{pid}/statm').read_text(encoding='utf-8').split()[1])
            return float(resident_pages * os.sysconf('SC_PAGE_SIZE'))
        except Exception:
            return 0.0

    def _dashboard_http_health(self) -> float:
        try:
            with urlopen(self.config.dashboard_health_url, timeout=5) as response:
                return 1.0 if response.status == 200 else 0.0
        except Exception:
            return 0.0

    def collect(self) -> bytes:
        registry = CollectorRegistry()
        base_keys = list(self.base_labels.keys())

        build_info = Gauge('arena_exporter_build_info', 'Exporter build info', [*base_keys, 'layer'], registry=registry)
        candidates_total = Gauge('arena_candidates_total', 'Arena candidates count', [*base_keys, 'layer', 'market_state'], registry=registry)
        trade_tickets_total = Gauge('arena_trade_tickets_total', 'Arena trade ticket count', [*base_keys, 'layer', 'market_state', 'setup_type'], registry=registry)
        auto_review_queue_total = Gauge('arena_auto_review_queue_total', 'Arena review queue counts by queue_scope (all/eligible/blocked/reviewed/omitted_eligible)', [*base_keys, 'layer', 'market_state', 'queue_scope'], registry=registry)
        executed_trades_total = Gauge('arena_executed_trades_total', 'Arena executed trades count', [*base_keys, 'layer', 'side', 'session_label', 'market_state'], registry=registry)
        pending_trades_total = Gauge('arena_pending_trades_total', 'Arena pending trades count', [*base_keys, 'layer', 'session_label'], registry=registry)
        portfolio_market_value = Gauge('arena_portfolio_market_value', 'Arena portfolio total market value', [*base_keys, 'layer', 'portfolio'], registry=registry)
        portfolio_unrealized_pnl = Gauge('arena_portfolio_unrealized_pnl', 'Arena portfolio unrealized pnl', [*base_keys, 'layer', 'portfolio'], registry=registry)
        holdings_total = Gauge('arena_holdings_total', 'Arena holding count', [*base_keys, 'layer', 'portfolio'], registry=registry)
        exit_playbooks_total = Gauge('arena_exit_playbooks_total', 'Arena exit playbook count', [*base_keys, 'layer', 'playbook_type'], registry=registry)
        runtime_snapshot_age_seconds = Gauge('arena_runtime_snapshot_age_seconds', 'Arena runtime snapshot age', [*base_keys, 'layer', 'service_kind'], registry=registry)
        ticket_score_distribution = Gauge('arena_ticket_score_distribution', 'Arena ticket score distribution', [*base_keys, 'layer', 'market_state', 'setup_type', 'score_band'], registry=registry)
        ticket_blockers_total = Gauge('arena_ticket_blockers_total', 'Arena ticket blockers', [*base_keys, 'layer', 'blocker_type'], registry=registry)
        order_lifecycle_latency_seconds = Gauge('arena_order_lifecycle_latency_seconds', 'Arena order lifecycle latency', [*base_keys, 'layer', 'phase'], registry=registry)
        validation_outcomes_total = Gauge('arena_validation_outcomes_total', 'Arena validation outcomes', [*base_keys, 'layer', 'outcome'], registry=registry)
        rotation_candidates_total = Gauge('arena_rotation_candidates_total', 'Arena rotation candidates count', [*base_keys, 'layer', 'market_state'], registry=registry)
        news_score_distribution = Gauge('arena_news_score_distribution', 'Arena news score distribution', [*base_keys, 'layer', 'score_band', 'hard_risk'], registry=registry)
        runtime_loop_duration_seconds = Gauge('arena_runtime_loop_duration_seconds', 'Arena runtime loop duration summary', [*base_keys, 'layer', 'stage', 'stat'], registry=registry)
        runtime_events_total = Gauge('arena_runtime_events_total', 'Arena runtime events total', [*base_keys, 'layer', 'event_type', 'status'], registry=registry)
        process_cpu_percent = Gauge('arena_process_cpu_percent', 'Arena process cpu percent', [*base_keys, 'layer', 'component'], registry=registry)
        process_memory_bytes = Gauge('arena_process_memory_bytes', 'Arena process memory bytes', [*base_keys, 'layer', 'component'], registry=registry)
        dashboard_http_health = Gauge('arena_dashboard_http_health', 'Arena dashboard http health', [*base_keys, 'layer'], registry=registry)

        build_info.labels(**self.base_labels, layer='L0').set(1)
        dashboard_http_health.labels(**self.base_labels, layer='L2').set(self._dashboard_http_health())

        runtime = load_json(self.config.runtime_path, {})
        strategy = runtime.get('strategy') or {}
        portfolio = runtime.get('portfolio') or {}
        market_state = str(((runtime.get('market') or {}).get('state') or {}).get('name') or 'unknown')
        market_session_label = str(((runtime.get('market') or {}).get('clock') or {}).get('sessionLabel') or 'unknown')
        generated_at = parse_ts(runtime.get('generatedAt'))
        now = time.time()
        runtime_snapshot_age_seconds.labels(**self.base_labels, layer='L2', service_kind='runtime').set(age_seconds(generated_at, now))

        run_history = read_jsonl(self.config.run_history_path, 200)
        if run_history:
            last_run_ts = parse_ts(run_history[-1].get('ts'))
            runtime_snapshot_age_seconds.labels(**self.base_labels, layer='L2', service_kind='run-history').set(age_seconds(last_run_ts, now))

        candidates_total.labels(**self.base_labels, layer='L3', market_state=market_state).set(float(len(strategy.get('candidates') or [])))

        trade_ticket_counts: Counter[tuple[str, str]] = Counter()
        score_bands: Counter[tuple[str, str, str]] = Counter()
        blocker_counts: Counter[str] = Counter()
        news_bands: Counter[tuple[str, str]] = Counter()
        for ticket in strategy.get('tradeTickets') or []:
            setup_type = str(ticket.get('setupType') or 'unknown')
            trade_ticket_counts[(market_state, setup_type)] += 1
            score = int(ticket.get('score') or 0)
            if score >= 19:
                score_band = '19-20'
            elif score >= 17:
                score_band = '17-18'
            elif score >= 15:
                score_band = '15-16'
            else:
                score_band = '<15'
            score_bands[(market_state, setup_type, score_band)] += 1
            for blocker in ticket.get('agentBlockers') or []:
                blocker_type = _classify_review_blocker_type(blocker)
                blocker_counts[blocker_type] += 1
            news_context = ticket.get('newsContext') or {}
            score_value = float(news_context.get('aiNewsScore') or 0.0)
            if score_value >= 0.5:
                news_band = 'positive'
            elif score_value <= -0.3:
                news_band = 'negative'
            else:
                news_band = 'neutral'
            news_bands[(news_band, 'true' if news_context.get('aiHardRisk') else 'false')] += 1
        for (state, setup_type), count in sorted(trade_ticket_counts.items()):
            trade_tickets_total.labels(**self.base_labels, layer='L3', market_state=state, setup_type=setup_type).set(float(count))
        for (state, setup_type, score_band), count in sorted(score_bands.items()):
            ticket_score_distribution.labels(**self.base_labels, layer='L3', market_state=state, setup_type=setup_type, score_band=score_band).set(float(count))
        for blocker_type, count in sorted(blocker_counts.items()):
            ticket_blockers_total.labels(**self.base_labels, layer='L3', blocker_type=blocker_type).set(float(count))
        for (score_band, hard_risk), count in sorted(news_bands.items()):
            news_score_distribution.labels(**self.base_labels, layer='L3', score_band=score_band, hard_risk=hard_risk).set(float(count))

        review_queue_summary = strategy.get('reviewQueueSummary') or {}
        queue_total = float(review_queue_summary.get('totalCount', len(strategy.get('autoReviewQueue') or [])) or 0)
        queue_eligible = float(review_queue_summary.get('eligibleCount', 0) or 0)
        queue_blocked = float(review_queue_summary.get('blockedCount', 0) or 0)
        queue_reviewed = float(review_queue_summary.get('reviewedCount', 0) or 0)
        queue_omitted = float(review_queue_summary.get('omittedEligibleCount', 0) or 0)
        auto_review_queue_total.labels(**self.base_labels, layer='L3', market_state=market_state, queue_scope='all').set(queue_total)
        auto_review_queue_total.labels(**self.base_labels, layer='L3', market_state=market_state, queue_scope='eligible').set(queue_eligible)
        auto_review_queue_total.labels(**self.base_labels, layer='L3', market_state=market_state, queue_scope='blocked').set(queue_blocked)
        auto_review_queue_total.labels(**self.base_labels, layer='L3', market_state=market_state, queue_scope='reviewed').set(queue_reviewed)
        auto_review_queue_total.labels(**self.base_labels, layer='L3', market_state=market_state, queue_scope='omitted_eligible').set(queue_omitted)
        rotation_candidates_total.labels(**self.base_labels, layer='L3', market_state=market_state).set(float(len(strategy.get('rotationPlans') or [])))

        portfolio_market_value.labels(**self.base_labels, layer='L3', portfolio='main').set(float(portfolio.get('total_value') or 0.0))
        unrealized = sum(float(item.get('profit_loss') or 0.0) for item in runtime.get('holdings') or [])
        portfolio_unrealized_pnl.labels(**self.base_labels, layer='L3', portfolio='main').set(unrealized)
        holdings_total.labels(**self.base_labels, layer='L3', portfolio='main').set(float(portfolio.get('holding_count') or len(runtime.get('holdings') or [])))

        playbook_counts: Counter[str] = Counter()
        for playbook in strategy.get('exitPlaybooks') or []:
            playbook_counts[str(playbook.get('type') or 'unknown')] += 1
        for playbook_type, count in sorted(playbook_counts.items()):
            exit_playbooks_total.labels(**self.base_labels, layer='L3', playbook_type=playbook_type).set(float(count))

        trades = ((runtime.get('feeds') or {}).get('trades') or [])
        executed_counts: Counter[tuple[str, str, str]] = Counter()
        pending_count = 0
        for trade in trades:
            status = str(trade.get('status') or '').lower()
            side = str(trade.get('action') or 'unknown').lower()
            if status == 'pending':
                pending_count += 1
            elif status == 'executed':
                executed_counts[(side, market_session_label, market_state)] += 1
        pending_trades_total.labels(**self.base_labels, layer='L3', session_label=market_session_label).set(float(pending_count))
        for (side, session_label, state), count in sorted(executed_counts.items()):
            executed_trades_total.labels(**self.base_labels, layer='L3', side=side, session_label=session_label, market_state=state).set(float(count))

        audit_rows = read_jsonl(self.config.order_audit_path, 500)
        phase_values: defaultdict[str, list[float]] = defaultdict(list)
        audit_counts: Counter[tuple[str, str]] = Counter()
        for row in audit_rows:
            action = str(row.get('action') or 'unknown')
            audit_counts[('order-audit', action)] += 1
            for phase in ['submitToSeenSeconds', 'seenToResolutionSeconds', 'submitToResolutionSeconds']:
                value = row.get(phase)
                if value is not None:
                    mapped = {
                        'submitToSeenSeconds': 'submit_to_seen',
                        'seenToResolutionSeconds': 'seen_to_settled',
                        'submitToResolutionSeconds': 'submit_to_settled',
                    }[phase]
                    phase_values[mapped].append(float(value))
        for phase, values in sorted(phase_values.items()):
            order_lifecycle_latency_seconds.labels(**self.base_labels, layer='L3', phase=phase).set(float(statistics.fmean(values)))
            order_lifecycle_latency_seconds.labels(**self.base_labels, layer='L3', phase=f'{phase}_p95').set(percentile(values, 0.95))
        for (event_type, status), count in sorted(audit_counts.items()):
            runtime_events_total.labels(**self.base_labels, layer='L2', event_type=event_type, status=status).set(float(count))

        validation = (strategy.get('decisionValidation') or {}).get('records') or []
        validation_counts: Counter[str] = Counter()
        for record in validation:
            validation_counts[str(record.get('status') or 'unknown')] += 1
        for outcome, count in sorted(validation_counts.items()):
            validation_outcomes_total.labels(**self.base_labels, layer='L3', outcome=outcome).set(float(count))

        ai_rows = read_jsonl(self.config.ai_decisions_path, 200)
        ai_counts: Counter[tuple[str, str]] = Counter()
        for row in ai_rows:
            status = str(((row.get('execution') or {}).get('action')) or 'unknown')
            ai_counts[('ai-decision', status)] += 1
        for (event_type, status), count in sorted(ai_counts.items()):
            runtime_events_total.labels(**self.base_labels, layer='L2', event_type=event_type, status=status).set(float(count))

        run_counts: Counter[tuple[str, str]] = Counter()
        phase_duration_buckets: defaultdict[str, list[float]] = defaultdict(list)
        for row in run_history:
            run_counts[('run-history', str(row.get('status') or 'unknown'))] += 1
        process = runtime.get('process') or {}
        for phase in process.get('phases') or []:
            phase_name = str(phase.get('name') or 'unknown')
            run_counts[('runtime-phase', str(phase.get('status') or 'unknown'))] += 1
            started_at = parse_ts(phase.get('startedAt'))
            finished_at = parse_ts(phase.get('finishedAt'))
            if started_at is not None and finished_at is not None and finished_at >= started_at:
                phase_duration_buckets[phase_name].append(finished_at - started_at)
        for (event_type, status), count in sorted(run_counts.items()):
            runtime_events_total.labels(**self.base_labels, layer='L2', event_type=event_type, status=status).set(float(count))
        for stage, values in sorted(phase_duration_buckets.items()):
            runtime_loop_duration_seconds.labels(**self.base_labels, layer='L2', stage=stage, stat='avg').set(float(statistics.fmean(values)))
            runtime_loop_duration_seconds.labels(**self.base_labels, layer='L2', stage=stage, stat='p95').set(percentile(values, 0.95))

        for component, patterns in PROCESS_PATTERNS.items():
            pids = self._matching_pids(*patterns)
            process_cpu_percent.labels(**self.base_labels, layer='L1', component=component).set(sum(self._process_cpu_percent(pid) for pid in pids))
            process_memory_bytes.labels(**self.base_labels, layer='L1', component=component).set(sum(self._process_memory_bytes(pid) for pid in pids))

        return generate_latest(registry)


if __name__ == '__main__':
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    config = parse_args()
    collector = ArenaCollector(config)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {'/metrics', '/metrics?format=prometheus'}:
                self.send_response(404)
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
    server.serve_forever()
