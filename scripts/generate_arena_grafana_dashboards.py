#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = REPO_ROOT / 'deploy' / 'grafana' / 'dashboards'
DATASOURCE_UID = 'prometheus-local-main'
PLUGIN_VERSION = '11.0.0'


class PanelFactory:
    def __init__(self) -> None:
        self.next_id = 1

    def _panel_id(self) -> int:
        panel_id = self.next_id
        self.next_id += 1
        return panel_id

    @staticmethod
    def datasource() -> dict:
        return {'type': 'prometheus', 'uid': DATASOURCE_UID}

    def stat(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 6, h: int = 5, thresholds: list[dict] | None = None) -> dict:
        return {
            'datasource': self.datasource(),
            'fieldConfig': {
                'defaults': {
                    'color': {'mode': 'thresholds'},
                    'mappings': [],
                    'thresholds': {
                        'mode': 'absolute',
                        'steps': thresholds or [{'color': 'green', 'value': None}],
                    },
                    'unit': unit,
                },
                'overrides': [],
            },
            'gridPos': {'h': h, 'w': w, 'x': x, 'y': y},
            'id': self._panel_id(),
            'options': {
                'colorMode': 'background',
                'graphMode': 'area',
                'justifyMode': 'auto',
                'orientation': 'auto',
                'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                'textMode': 'value_and_name',
            },
            'pluginVersion': PLUGIN_VERSION,
            'targets': [
                {
                    'datasource': self.datasource(),
                    'editorMode': 'code',
                    'expr': expr,
                    'legendFormat': title,
                    'range': True,
                    'refId': 'A',
                }
            ],
            'title': title,
            'type': 'stat',
        }

    def timeseries(self, *, title: str, targets: list[dict], unit: str, x: int, y: int, w: int = 12, h: int = 8) -> dict:
        return {
            'datasource': self.datasource(),
            'fieldConfig': {
                'defaults': {
                    'color': {'mode': 'palette-classic'},
                    'mappings': [],
                    'thresholds': {'mode': 'absolute', 'steps': [{'color': 'green', 'value': None}]},
                    'unit': unit,
                },
                'overrides': [],
            },
            'gridPos': {'h': h, 'w': w, 'x': x, 'y': y},
            'id': self._panel_id(),
            'options': {
                'legend': {'calcs': [], 'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                'tooltip': {'mode': 'single', 'sort': 'none'},
            },
            'pluginVersion': PLUGIN_VERSION,
            'targets': [
                {
                    'datasource': self.datasource(),
                    'editorMode': 'code',
                    'expr': target['expr'],
                    'legendFormat': target['legend'],
                    'range': True,
                    'refId': target['refId'],
                }
                for target in targets
            ],
            'title': title,
            'type': 'timeseries',
        }

    def bargauge(self, *, title: str, expr: str, unit: str, x: int, y: int, w: int = 12, h: int = 8, legend: str = '{{setup_type}}{{blocker_type}}{{outcome}}{{playbook_type}}{{phase}}{{component}}') -> dict:
        return {
            'datasource': self.datasource(),
            'fieldConfig': {
                'defaults': {
                    'color': {'mode': 'continuous-BlYlRd' if unit in {'ms', 's'} else 'continuous-GrYlRd'},
                    'mappings': [],
                    'min': 0,
                    'thresholds': {'mode': 'absolute', 'steps': [{'color': 'green', 'value': None}]},
                    'unit': unit,
                },
                'overrides': [],
            },
            'gridPos': {'h': h, 'w': w, 'x': x, 'y': y},
            'id': self._panel_id(),
            'options': {
                'displayMode': 'gradient',
                'legend': {'displayMode': 'hidden', 'placement': 'bottom', 'showLegend': False},
                'namePlacement': 'left',
                'orientation': 'horizontal',
                'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                'showUnfilled': True,
                'sizing': 'auto',
                'valueMode': 'color',
            },
            'pluginVersion': PLUGIN_VERSION,
            'targets': [
                {
                    'datasource': self.datasource(),
                    'editorMode': 'code',
                    'expr': expr,
                    'instant': True,
                    'legendFormat': legend,
                    'range': False,
                    'refId': 'A',
                }
            ],
            'title': title,
            'type': 'bargauge',
        }

    def table(self, *, title: str, expr: str, x: int, y: int, w: int = 12, h: int = 8) -> dict:
        return {
            'datasource': self.datasource(),
            'fieldConfig': {'defaults': {}, 'overrides': []},
            'gridPos': {'h': h, 'w': w, 'x': x, 'y': y},
            'id': self._panel_id(),
            'options': {
                'cellHeight': 'sm',
                'footer': {'countRows': False, 'fields': '', 'reducer': ['sum'], 'show': False},
                'showHeader': True,
                'sortBy': [{'desc': True, 'displayName': 'Value'}],
            },
            'pluginVersion': PLUGIN_VERSION,
            'targets': [
                {
                    'datasource': self.datasource(),
                    'editorMode': 'code',
                    'expr': expr,
                    'format': 'table',
                    'instant': True,
                    'range': False,
                    'refId': 'A',
                }
            ],
            'title': title,
            'type': 'table',
        }


def variable(name: str, label: str, query: str, include_all: bool = True, multi: bool = True) -> dict:
    grafana_all = '$' + '__all'
    return {
        'current': {'selected': False, 'text': 'All', 'value': grafana_all},
        'datasource': {'type': 'prometheus', 'uid': DATASOURCE_UID},
        'definition': query,
        'hide': 0,
        'includeAll': include_all,
        'label': label,
        'multi': multi,
        'name': name,
        'options': [],
        'query': {'qryType': 1, 'query': query, 'refId': 'StandardVariableQuery'},
        'refresh': 1,
        'regex': '',
        'skipUrlSync': False,
        'sort': 1,
        'type': 'query',
    }


def base_dashboard(title: str, uid: str, tags: list[str]) -> dict:
    return {
        'annotations': {
            'list': [
                {
                    'builtIn': 1,
                    'datasource': {'type': 'grafana', 'uid': '-- Grafana --'},
                    'enable': True,
                    'hide': True,
                    'iconColor': 'rgba(0, 211, 255, 1)',
                    'name': 'Annotations & Alerts',
                    'type': 'dashboard',
                }
            ]
        },
        'editable': True,
        'fiscalYearStartMonth': 0,
        'graphTooltip': 0,
        'links': [],
        'panels': [],
        'refresh': '30s',
        'schemaVersion': 39,
        'style': 'dark',
        'tags': tags,
        'templating': {
            'list': [
                variable('project', 'project', 'label_values(arena_candidates_total, project)'),
                variable('market_state', 'market_state', 'label_values(arena_candidates_total, market_state)'),
                variable('session_label', 'session_label', 'label_values(arena_executed_trades_total, session_label)'),
                variable('setup_type', 'setup_type', 'label_values(arena_trade_tickets_total, setup_type)'),
                variable('playbook_type', 'playbook_type', 'label_values(arena_exit_playbooks_total, playbook_type)'),
                variable('job', 'job', 'label_values(arena_exporter_build_info, job)'),
                variable('instance', 'instance', 'label_values(arena_exporter_build_info, instance)'),
            ]
        },
        'time': {'from': 'now-24h', 'to': 'now'},
        'timepicker': {},
        'timezone': 'browser',
        'title': title,
        'uid': uid,
        'version': 1,
        'weekStart': '',
    }


def build_business_overview() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance",market_state=~"$market_state"'
    trade_filters = filters + ',session_label=~"$session_label"'
    setup_filters = filters + ',setup_type=~"$setup_type"'
    dashboard = base_dashboard(
        title='AT | Arena | Business | Overview',
        uid='at-arena-business-overview',
        tags=['agent-team-grafana', 'arena', 'business'],
    )
    dashboard['panels'] = [
        factory.stat(title='组合总资产', expr='max(arena_portfolio_market_value{project=~"$project",job=~"$job",instance=~"$instance"})', unit='currencyCNY', x=0, y=0),
        factory.stat(title='浮动盈亏', expr='max(arena_portfolio_unrealized_pnl{project=~"$project",job=~"$job",instance=~"$instance"})', unit='currencyCNY', x=6, y=0),
        factory.stat(title='当前持仓数', expr='max(arena_holdings_total{project=~"$project",job=~"$job",instance=~"$instance"})', unit='none', x=12, y=0),
        factory.stat(title='今日执行笔数', expr=f'sum(arena_executed_trades_total{{{trade_filters}}})', unit='none', x=18, y=0),
        factory.stat(title='候选数', expr=f'sum(arena_candidates_total{{{filters}}})', unit='none', x=0, y=5),
        factory.stat(title='Trade Tickets', expr=f'sum(arena_trade_tickets_total{{{setup_filters}}})', unit='none', x=6, y=5),
        factory.stat(title='Pending 订单', expr='sum(arena_pending_trades_total{project=~"$project",job=~"$job",instance=~"$instance",session_label=~"$session_label"})', unit='none', x=12, y=5),
        factory.stat(title='Snapshot Age', expr='max(arena_runtime_snapshot_age_seconds{project=~"$project",job=~"$job",instance=~"$instance",service_kind="runtime"})', unit='s', x=18, y=5),
        factory.timeseries(
            title='候选 / Ticket / 执行规模',
            unit='none',
            x=0,
            y=10,
            targets=[
                {'expr': f'sum(arena_candidates_total{{{filters}}})', 'legend': 'candidates', 'refId': 'A'},
                {'expr': f'sum(arena_trade_tickets_total{{{setup_filters}}})', 'legend': 'trade_tickets', 'refId': 'B'},
                {'expr': f'sum(arena_executed_trades_total{{{trade_filters}}})', 'legend': 'executed_trades', 'refId': 'C'},
            ],
        ),
        factory.timeseries(
            title='市场状态分布',
            unit='none',
            x=12,
            y=10,
            targets=[
                {'expr': 'sum by (market_state) (arena_candidates_total{project=~"$project",job=~"$job",instance=~"$instance"})', 'legend': '{{market_state}}', 'refId': 'A'},
            ],
        ),
        factory.bargauge(
            title='Setup Type 分布',
            expr=f'sort_desc(sum by (setup_type) (arena_trade_tickets_total{{{setup_filters}}}))',
            unit='short',
            x=0,
            y=18,
            legend='{{setup_type}}',
        ),
        factory.bargauge(
            title='News Score 分布',
            expr='sort_desc(sum by (score_band) (arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance"}))',
            unit='short',
            x=12,
            y=18,
            legend='{{score_band}}',
        ),
    ]
    return dashboard


def build_runtime_execution_flow() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance",market_state=~"$market_state"'
    dashboard = base_dashboard(
        title='AT | Arena | Runtime | Execution Flow',
        uid='at-arena-runtime-execution-flow',
        tags=['agent-team-grafana', 'arena', 'runtime'],
    )
    dashboard['panels'] = [
        factory.stat(title='Auto Review Queue', expr=f'sum(arena_auto_review_queue_total{{{filters},queue="auto-review"}})', unit='none', x=0, y=0),
        factory.stat(title='Dashboard Health', expr='max(arena_dashboard_http_health{project=~"$project",job=~"$job",instance=~"$instance"})', unit='none', x=6, y=0),
        factory.stat(title='Runtime Snapshot Age', expr='max(arena_runtime_snapshot_age_seconds{project=~"$project",job=~"$job",instance=~"$instance",service_kind="runtime"})', unit='s', x=12, y=0),
        factory.stat(title='Run History Age', expr='max(arena_runtime_snapshot_age_seconds{project=~"$project",job=~"$job",instance=~"$instance",service_kind="run-history"})', unit='s', x=18, y=0),
        factory.stat(title='Avg Submit→Seen', expr='max(arena_order_lifecycle_latency_seconds{project=~"$project",job=~"$job",instance=~"$instance",phase="submit_to_seen"})', unit='s', x=0, y=5),
        factory.stat(title='Avg Seen→Settled', expr='max(arena_order_lifecycle_latency_seconds{project=~"$project",job=~"$job",instance=~"$instance",phase="seen_to_settled"})', unit='s', x=6, y=5),
        factory.stat(title='P95 Submit→Settled', expr='max(arena_order_lifecycle_latency_seconds{project=~"$project",job=~"$job",instance=~"$instance",phase="submit_to_settled_p95"})', unit='s', x=12, y=5),
        factory.stat(title='Pending Trades', expr='sum(arena_pending_trades_total{project=~"$project",job=~"$job",instance=~"$instance",session_label=~"$session_label"})', unit='none', x=18, y=5),
        factory.timeseries(
            title='Runtime Events',
            unit='none',
            x=0,
            y=10,
            targets=[
                {'expr': 'sum by (event_type, status) (arena_runtime_events_total{project=~"$project",job=~"$job",instance=~"$instance"})', 'legend': '{{event_type}} / {{status}}', 'refId': 'A'},
            ],
        ),
        factory.timeseries(
            title='Runtime Loop Duration by Stage',
            unit='s',
            x=12,
            y=10,
            targets=[
                {'expr': 'arena_runtime_loop_duration_seconds{project=~"$project",job=~"$job",instance=~"$instance",stat="avg"}', 'legend': '{{stage}} avg', 'refId': 'A'},
                {'expr': 'arena_runtime_loop_duration_seconds{project=~"$project",job=~"$job",instance=~"$instance",stat="p95"}', 'legend': '{{stage}} p95', 'refId': 'B'},
            ],
        ),
        factory.bargauge(
            title='Blocker 类型分布',
            expr='sort_desc(arena_ticket_blockers_total{project=~"$project",job=~"$job",instance=~"$instance"})',
            unit='short',
            x=0,
            y=18,
            legend='{{blocker_type}}',
        ),
        factory.bargauge(
            title='运行阶段耗时',
            expr='sort_desc(arena_runtime_loop_duration_seconds{project=~"$project",job=~"$job",instance=~"$instance",stat="avg"})',
            unit='s',
            x=12,
            y=18,
            legend='{{stage}}',
        ),
    ]
    return dashboard


def build_position_holdings_exits() -> dict:
    factory = PanelFactory()
    filters = 'project=~"$project",job=~"$job",instance=~"$instance"'
    dashboard = base_dashboard(
        title='AT | Arena | Position | Holdings & Exits',
        uid='at-arena-position-holdings-exits',
        tags=['agent-team-grafana', 'arena', 'position'],
    )
    dashboard['panels'] = [
        factory.stat(title='持仓数', expr='max(arena_holdings_total{project=~"$project",job=~"$job",instance=~"$instance"})', unit='none', x=0, y=0),
        factory.stat(title='待执行退出剧本', expr='sum(arena_exit_playbooks_total{project=~"$project",job=~"$job",instance=~"$instance",playbook_type=~"$playbook_type"})', unit='none', x=6, y=0),
        factory.stat(title='Rotation Candidates', expr='sum(arena_rotation_candidates_total{project=~"$project",job=~"$job",instance=~"$instance",market_state=~"$market_state"})', unit='none', x=12, y=0),
        factory.stat(title='组合总资产', expr='max(arena_portfolio_market_value{project=~"$project",job=~"$job",instance=~"$instance"})', unit='currencyCNY', x=18, y=0),
        factory.stat(title='浮动盈亏', expr='max(arena_portfolio_unrealized_pnl{project=~"$project",job=~"$job",instance=~"$instance"})', unit='currencyCNY', x=0, y=5),
        factory.stat(title='Dashboard 健康', expr='max(arena_dashboard_http_health{project=~"$project",job=~"$job",instance=~"$instance"})', unit='none', x=6, y=5),
        factory.timeseries(
            title='执行 / Pending 走势',
            unit='none',
            x=0,
            y=10,
            targets=[
                {'expr': 'sum(arena_executed_trades_total{project=~"$project",job=~"$job",instance=~"$instance",session_label=~"$session_label"})', 'legend': 'executed', 'refId': 'A'},
                {'expr': 'sum(arena_pending_trades_total{project=~"$project",job=~"$job",instance=~"$instance",session_label=~"$session_label"})', 'legend': 'pending', 'refId': 'B'},
            ],
        ),
        factory.timeseries(
            title='持仓 / 退出剧本规模',
            unit='none',
            x=12,
            y=10,
            targets=[
                {'expr': 'max(arena_holdings_total{project=~"$project",job=~"$job",instance=~"$instance"})', 'legend': 'holdings', 'refId': 'A'},
                {'expr': 'sum(arena_exit_playbooks_total{project=~"$project",job=~"$job",instance=~"$instance",playbook_type=~"$playbook_type"})', 'legend': 'exit_playbooks', 'refId': 'B'},
            ],
        ),
        factory.bargauge(
            title='退出剧本类型分布',
            expr='sort_desc(arena_exit_playbooks_total{project=~"$project",job=~"$job",instance=~"$instance",playbook_type=~"$playbook_type"})',
            unit='short',
            x=0,
            y=18,
            legend='{{playbook_type}}',
        ),
        factory.bargauge(
            title='执行时段分布',
            expr='sort_desc(sum by (session_label) (arena_executed_trades_total{project=~"$project",job=~"$job",instance=~"$instance",session_label=~"$session_label"}))',
            unit='short',
            x=12,
            y=18,
            legend='{{session_label}}',
        ),
    ]
    return dashboard


def build_review_validation_iteration() -> dict:
    factory = PanelFactory()
    dashboard = base_dashboard(
        title='AT | Arena | Review | Validation & Iteration',
        uid='at-arena-review-validation-iteration',
        tags=['agent-team-grafana', 'arena', 'review'],
    )
    dashboard['panels'] = [
        factory.stat(title='Validation 样本数', expr='sum(arena_validation_outcomes_total{project=~"$project",job=~"$job",instance=~"$instance"})', unit='none', x=0, y=0),
        factory.stat(title='News Positive 样本', expr='sum(arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance",score_band="positive"})', unit='none', x=6, y=0),
        factory.stat(title='News Negative 样本', expr='sum(arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance",score_band="negative"})', unit='none', x=12, y=0),
        factory.stat(title='AI 决策记录', expr='sum(arena_runtime_events_total{project=~"$project",job=~"$job",instance=~"$instance",event_type="ai-decision"})', unit='none', x=18, y=0),
        factory.timeseries(
            title='Validation Outcome 分布',
            unit='none',
            x=0,
            y=5,
            targets=[
                {'expr': 'sum by (outcome) (arena_validation_outcomes_total{project=~"$project",job=~"$job",instance=~"$instance"})', 'legend': '{{outcome}}', 'refId': 'A'},
            ],
        ),
        factory.timeseries(
            title='News Score 分布',
            unit='none',
            x=12,
            y=5,
            targets=[
                {'expr': 'sum by (score_band, hard_risk) (arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance"})', 'legend': '{{score_band}} / risk={{hard_risk}}', 'refId': 'A'},
            ],
        ),
        factory.bargauge(
            title='Validation Outcomes 排行',
            expr='sort_desc(arena_validation_outcomes_total{project=~"$project",job=~"$job",instance=~"$instance"})',
            unit='short',
            x=0,
            y=13,
            legend='{{outcome}}',
        ),
        factory.bargauge(
            title='News Risk 分布',
            expr='sort_desc(sum by (hard_risk) (arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance"}))',
            unit='short',
            x=12,
            y=13,
            legend='hard_risk={{hard_risk}}',
        ),
        factory.table(
            title='验证 / 新闻样本快照',
            expr='arena_validation_outcomes_total{project=~"$project",job=~"$job",instance=~"$instance"} or arena_news_score_distribution{project=~"$project",job=~"$job",instance=~"$instance"}',
            x=0,
            y=21,
            w=24,
            h=8,
        ),
    ]
    return dashboard


def main() -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        'arena-business-overview.json': build_business_overview(),
        'arena-runtime-execution-flow.json': build_runtime_execution_flow(),
        'arena-position-holdings-exits.json': build_position_holdings_exits(),
        'arena-review-validation-iteration.json': build_review_validation_iteration(),
    }
    for name, payload in dashboards.items():
        (DASHBOARD_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
