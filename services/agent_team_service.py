from __future__ import annotations

import calendar
import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from .db import AgentTeamDB, NotFoundError, ValidationError, now_ms
from .routing_policy import route_issue
from .activity import ensure_issue_activity_table, ensure_issue_attempt_callback_table, record_issue_activity
from .human_queue_service import HumanQueueService, WAITING_HUMAN_STATUSES, WAITING_HUMAN_STATUS_BY_TYPE
from .board_query_service import BoardQueryService
from .dispatch_service import DispatchService
from .dependency_service import DependencyService
from .derived_issue_service import DerivedIssueService
from .config import STATE_DIR


PROJECT_ROLE_ORDER = ('pm', 'dev', 'qa', 'ops')
ROLE_AGENT_IDS = {
    'ceo': 'agent-team-ceo',
    'pm': 'agent-team-pm',
    'dev': 'agent-team-dev',
    'qa': 'agent-team-qa',
    'ops': 'agent-team-ops',
}
ROLE_DISPLAY_NAMES = {
    'ceo': 'CEO',
    'pm': 'PM',
    'dev': 'Dev',
    'qa': 'QA',
    'ops': 'Ops',
}
ROLE_WORKSPACE_FALLBACKS = {
    'ceo': '/root/.openclaw/workspace-agent-team-ceo',
    'pm': '/root/.openclaw/workspace-agent-team-pm',
    'dev': '/root/.openclaw/workspace-agent-team-dev',
    'qa': '/root/.openclaw/workspace-agent-team-qa',
    'ops': '/root/.openclaw/workspace-agent-team-ops',
}
ROLE_BOOTSTRAP_HINTS = {
    'ceo': '你负责治理、升级、取舍和最终拍板，默认不要亲自下场做主体实现。',
    'pm': '你负责需求梳理、任务拆分、流转设计和跨角色协调，默认不要越界去做主体实现。',
    'dev': '你负责实现、技术方案和最小必要验证，默认不要替 PM 做需求定调，也不要替 QA 做最终验收。',
    'qa': '你负责验证、验收、风险识别和质量把关，默认不要替 Dev 完成主体实现。',
    'ops': '你负责部署、环境、运行态、观测和发布保障，默认不要替 PM 或 Dev 做需求和实现决策。',
}
SESSION_REGISTRY_PATH = STATE_DIR / 'session_registry.json'


def slugify_project_key(value: str) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower()).strip('-')
    return re.sub(r'-{2,}', '-', normalized)


def load_session_registry() -> dict[str, Any]:
    if not SESSION_REGISTRY_PATH.exists():
        return {}
    try:
        value = json.loads(SESSION_REGISTRY_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def save_session_registry(payload: dict[str, Any]) -> None:
    SESSION_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_REGISTRY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def gateway_call(method: str, params: dict[str, Any], *, timeout_ms: int = 40000) -> dict[str, Any]:
    cmd = [
        'openclaw', 'gateway', 'call', method,
        '--json',
        '--timeout', str(timeout_ms),
        '--params', json.dumps(params, ensure_ascii=False),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=max(30, timeout_ms // 1000 + 15),
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or f'gateway call failed: {method}')
    raw = (res.stdout or '').strip()
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def build_project_bootstrap_message(*, role: str, project_name: str, project_key: str, description: str) -> str:
    context = str(description or '').strip() or '暂无补充项目描述，后续请以本项目 issue 与补充上下文为准。'
    role_name = ROLE_DISPLAY_NAMES.get(role, role.upper())
    role_hint = ROLE_BOOTSTRAP_HINTS.get(role, '请只做当前角色边界内的工作。')
    return (
        '这是一条 Agent Team 项目会话初始化消息，不是开始处理 issue。\n'
        f'项目名称：{project_name}\n'
        f'项目 Key：{project_key}\n'
        f'当前角色：{role_name}\n'
        f'项目背景：{context}\n'
        f'角色说明：{role_hint}\n'
        '协作约束：固定 5 个角色 agent，不新增 agent 目录；CEO 保持共享；你当前是该项目的固定角色实例。\n'
        '后续这个项目的 issue 会默认进入这个 session，请把以上内容当作项目长期上下文。\n'
        '收到后只回复：PROJECT_CONTEXT_READY'
    )


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def merge_json_object(raw: str | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                base = value
        except Exception:
            base = {}
    if patch:
        base.update(patch)
    return base


def parse_schedule_config(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def dt_from_ms(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts) / 1000, tz=UTC)


def _next_month_anchor(dt: datetime, day: int, hour: int, minute: int) -> datetime:
    year = dt.year
    month = dt.month
    while True:
        days = calendar.monthrange(year, month)[1]
        safe_day = min(max(day, 1), days)
        candidate = datetime(year, month, safe_day, hour, minute, tzinfo=UTC)
        if candidate > dt:
            return candidate
        month += 1
        if month > 12:
            year += 1
            month = 1


CRON_MONTH_ALIASES = {
    'jan': 1,
    'feb': 2,
    'mar': 3,
    'apr': 4,
    'may': 5,
    'jun': 6,
    'jul': 7,
    'aug': 8,
    'sep': 9,
    'oct': 10,
    'nov': 11,
    'dec': 12,
}

CRON_DOW_ALIASES = {
    'sun': 0,
    'mon': 1,
    'tue': 2,
    'wed': 3,
    'thu': 4,
    'fri': 5,
    'sat': 6,
}


def _parse_cron_value(token: str, *, minimum: int, maximum: int, field_name: str, aliases: dict[str, int] | None = None) -> int:
    raw = str(token or '').strip().lower()
    if not raw:
        raise ValidationError(f'cron {field_name} contains an empty value')
    if aliases and raw in aliases:
        return aliases[raw]
    try:
        value = int(raw)
    except ValueError as e:
        raise ValidationError(f'cron {field_name} contains invalid value: {token}') from e
    if value < minimum or value > maximum:
        raise ValidationError(f'cron {field_name} out of range: {token}')
    return value


def _expand_cron_field(
    raw: str,
    *,
    minimum: int,
    maximum: int,
    field_name: str,
    aliases: dict[str, int] | None = None,
    allow_question: bool = False,
) -> tuple[set[int], bool]:
    text = str(raw or '').strip().lower()
    if not text:
        raise ValidationError(f'cron {field_name} is required')
    if allow_question and text == '?':
        text = '*'

    all_values = set(range(minimum, maximum + 1))
    values: set[int] = set()
    for part in text.split(','):
        piece = part.strip().lower()
        if not piece:
            raise ValidationError(f'cron {field_name} contains an empty segment')
        if allow_question and piece == '?':
            piece = '*'

        step = 1
        base = piece
        if '/' in piece:
            base, step_raw = piece.split('/', 1)
            try:
                step = int(step_raw)
            except ValueError as e:
                raise ValidationError(f'cron {field_name} contains invalid step: {piece}') from e
            if step <= 0:
                raise ValidationError(f'cron {field_name} step must be > 0: {piece}')

        if base == '*':
            start = minimum
            end = maximum
        elif '-' in base:
            start_raw, end_raw = base.split('-', 1)
            start = _parse_cron_value(start_raw, minimum=minimum, maximum=maximum, field_name=field_name, aliases=aliases)
            end = _parse_cron_value(end_raw, minimum=minimum, maximum=maximum, field_name=field_name, aliases=aliases)
            if end < start:
                raise ValidationError(f'cron {field_name} range must be ascending: {piece}')
        else:
            start = _parse_cron_value(base, minimum=minimum, maximum=maximum, field_name=field_name, aliases=aliases)
            end = maximum if '/' in piece else start

        for value in range(start, end + 1, step):
            values.add(value)

    return values, values == all_values


def _parse_cron_expression(expr: str) -> dict[str, Any]:
    parts = [part for part in str(expr or '').split() if part]
    if len(parts) == 5:
        second_values = {0}
        second_all = True
        minute_raw, hour_raw, dom_raw, month_raw, dow_raw = parts
    elif len(parts) == 6:
        second_raw, minute_raw, hour_raw, dom_raw, month_raw, dow_raw = parts
        second_values, second_all = _expand_cron_field(
            second_raw,
            minimum=0,
            maximum=59,
            field_name='second',
        )
        if second_values != {0}:
            raise ValidationError('scheduled issue cron only supports second=0')
    else:
        raise ValidationError('cron expression must have 5 fields, or 6 fields with second=0')

    minute_values, minute_all = _expand_cron_field(minute_raw, minimum=0, maximum=59, field_name='minute')
    hour_values, hour_all = _expand_cron_field(hour_raw, minimum=0, maximum=23, field_name='hour')
    dom_values, dom_all = _expand_cron_field(dom_raw, minimum=1, maximum=31, field_name='day_of_month', allow_question=True)
    month_values, month_all = _expand_cron_field(
        month_raw,
        minimum=1,
        maximum=12,
        field_name='month',
        aliases=CRON_MONTH_ALIASES,
    )
    dow_values, dow_all = _expand_cron_field(
        dow_raw,
        minimum=0,
        maximum=7,
        field_name='day_of_week',
        aliases=CRON_DOW_ALIASES,
        allow_question=True,
    )
    return {
        'seconds': second_values,
        'seconds_all': second_all,
        'minutes': minute_values,
        'minutes_all': minute_all,
        'hours': hour_values,
        'hours_all': hour_all,
        'days_of_month': dom_values,
        'days_of_month_all': dom_all,
        'months': month_values,
        'months_all': month_all,
        'days_of_week': dow_values,
        'days_of_week_all': dow_all,
    }


def _cron_matches(candidate: datetime, spec: dict[str, Any]) -> bool:
    cron_dow = (candidate.weekday() + 1) % 7
    dom_match = candidate.day in spec['days_of_month']
    dow_match = cron_dow in spec['days_of_week'] or (cron_dow == 0 and 7 in spec['days_of_week'])

    if spec['days_of_month_all'] and spec['days_of_week_all']:
        day_match = True
    elif spec['days_of_month_all']:
        day_match = dow_match
    elif spec['days_of_week_all']:
        day_match = dom_match
    else:
        day_match = dom_match or dow_match

    return (
        candidate.second in spec['seconds']
        and candidate.minute in spec['minutes']
        and candidate.hour in spec['hours']
        and candidate.month in spec['months']
        and day_match
    )


def _next_cron_run(expr: str, *, now_dt: datetime) -> datetime:
    spec = _parse_cron_expression(expr)
    candidate = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366 * 5)
    while candidate <= deadline:
        if _cron_matches(candidate, spec):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValidationError(f'cron expression has no next occurrence within 5 years: {expr}')


def compute_next_scheduled_run(*, schedule_kind: str, schedule_config: dict[str, Any], now_dt: datetime, last_run_at_ms: int | None = None) -> datetime | None:
    kind = str(schedule_kind or '').strip()
    if kind == 'hourly':
        minute = max(0, min(int(schedule_config.get('minute', 0) or 0), 59))
        candidate = now_dt.replace(minute=minute, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate += timedelta(hours=1)
        return candidate
    if kind == 'daily':
        hour = max(0, min(int(schedule_config.get('hour', 9) or 9), 23))
        minute = max(0, min(int(schedule_config.get('minute', 0) or 0), 59))
        candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate += timedelta(days=1)
        return candidate
    if kind == 'weekly':
        weekday = max(0, min(int(schedule_config.get('weekday', 0) or 0), 6))
        hour = max(0, min(int(schedule_config.get('hour', 9) or 9), 23))
        minute = max(0, min(int(schedule_config.get('minute', 0) or 0), 59))
        days_ahead = (weekday - now_dt.weekday()) % 7
        candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
        if candidate <= now_dt:
            candidate += timedelta(days=7)
        return candidate
    if kind == 'monthly':
        day = max(1, min(int(schedule_config.get('day', 1) or 1), 31))
        hour = max(0, min(int(schedule_config.get('hour', 9) or 9), 23))
        minute = max(0, min(int(schedule_config.get('minute', 0) or 0), 59))
        return _next_month_anchor(now_dt, day, hour, minute)
    if kind == 'interval':
        every_minutes = max(1, int(schedule_config.get('every_minutes', 60) or 60))
        anchor_dt = dt_from_ms(last_run_at_ms) or now_dt
        candidate = anchor_dt + timedelta(minutes=every_minutes)
        if candidate <= now_dt:
            candidate = now_dt + timedelta(minutes=every_minutes)
        return candidate.replace(second=0, microsecond=0)
    if kind == 'one_time':
        run_at_ms = schedule_config.get('run_at_ms')
        if run_at_ms is None:
            return None
        candidate = dt_from_ms(int(run_at_ms))
        return candidate if candidate and candidate > now_dt else None
    if kind == 'cron':
        expr = str(schedule_config.get('expr') or '').strip()
        if not expr:
            return None
        return _next_cron_run(expr, now_dt=now_dt)
    raise ValidationError(f'unsupported schedule kind: {schedule_kind}')


def default_next_role_for(role: str | None) -> str:
    mapping = {
        'pm': 'dev',
        'dev': 'qa',
        'qa': 'ceo',
        'ops': 'ceo',
        'ceo': 'close',
    }
    return mapping.get(str(role or '').strip(), 'close')


WAITING_HUMAN_STATUSES = WAITING_HUMAN_STATUSES
WAITING_HUMAN_STATUS_BY_TYPE = WAITING_HUMAN_STATUS_BY_TYPE


def append_unique_artifact(existing: list[Any], artifact: Any) -> list[Any]:
    items = list(existing)
    marker = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items}
    if marker not in seen:
        items.append(artifact)
    return items



class AgentTeamService:
    def __init__(self, db: AgentTeamDB | None = None):
        self.db = db or AgentTeamDB()
        ensure_issue_activity_table(self.db.conn)
        ensure_issue_attempt_callback_table(self.db.conn)
        self.human_queue = HumanQueueService(self.db)
        self.board_query = BoardQueryService(self.db)
        self.dispatch_service = DispatchService(self.db, self._record_checkpoint)
        self.dependency_service = DependencyService(self.db)
        self.derived_issue_service = DerivedIssueService(
            self.db,
            create_issue=self.create_issue,
            triage_issue=self.triage_issue,
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _next_issue_no(self, project_id: str) -> int:
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(issue_no), 0) + 1 FROM issues WHERE project_id = ?',
            (project_id,),
        ).fetchone()
        return int(row[0])

    def _next_attempt_no(self, issue_id: str) -> int:
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM issue_attempts WHERE issue_id = ?',
            (issue_id,),
        ).fetchone()
        return int(row[0])

    def _employee_role(self, employee_id: str) -> str:
        row = self.db.get_one(
            '''SELECT rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.id = ?''',
            (employee_id,),
        )
        return str(row['role'])

    def _role_template_row(self, template_key: str):
        return self.db.get_one('SELECT * FROM role_templates WHERE template_key = ?', (template_key,))

    def _runtime_seed_for_role(self, role: str, role_template_row) -> dict[str, Any]:
        row = self.db.conn.execute(
            '''SELECT rb.agent_id, rb.model, rb.workspace_path, rb.tool_policy_json, rb.skills_profile_json, rb.metadata_json
               FROM runtime_bindings rb
               JOIN employee_instances ei ON ei.id = rb.employee_id
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE rt.template_key = ? AND rb.is_primary = 1
               ORDER BY CASE rb.status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                        rb.updated_at_ms DESC,
                        rb.created_at_ms DESC
               LIMIT 1''',
            (role,),
        ).fetchone()
        seed = dict(row) if row else {}
        seed['agent_id'] = str(seed.get('agent_id') or ROLE_AGENT_IDS[role])
        seed['workspace_path'] = str(seed.get('workspace_path') or ROLE_WORKSPACE_FALLBACKS[role])
        seed['model'] = seed.get('model') or role_template_row['default_model']
        seed['tool_policy_json'] = seed.get('tool_policy_json') or role_template_row['default_tool_policy_json']
        seed['skills_profile_json'] = seed.get('skills_profile_json') or role_template_row['default_skill_profile_json']
        return seed

    def _ensure_openclaw_project_session(
        self,
        *,
        project_key: str,
        project_name: str,
        description: str,
        role: str,
        agent_id: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        session_key = f'agent:{agent_id}:project:{project_key}'
        params: dict[str, Any] = {
            'key': session_key,
            'agentId': agent_id,
            'label': f'{project_name} · {ROLE_DISPLAY_NAMES.get(role, role.upper())}',
            'message': build_project_bootstrap_message(
                role=role,
                project_name=project_name,
                project_key=project_key,
                description=description,
            ),
        }
        if model:
            params['model'] = model
        payload = gateway_call('sessions.create', params, timeout_ms=60000)
        entry = payload.get('entry') if isinstance(payload.get('entry'), dict) else {}
        return {
            'agent_id': agent_id,
            'session_key': str(payload.get('key') or session_key),
            'session_id': str(payload.get('sessionId') or entry.get('sessionId') or ''),
            'session_file': str(entry.get('sessionFile') or ''),
            'label': str(entry.get('label') or params['label']),
            'run_started': bool(payload.get('runStarted')),
            'run_error': payload.get('runError'),
        }

    def _upsert_project_session_registry(self, *, project_key: str, role_sessions: dict[str, dict[str, Any]]) -> None:
        registry = load_session_registry()
        ts = now_ms()
        for role, session_meta in role_sessions.items():
            agent_id = str(session_meta.get('agent_id') or ROLE_AGENT_IDS[role])
            registry_key = f'{agent_id}|{project_key}'
            existing = registry.get(registry_key) if isinstance(registry.get(registry_key), dict) else {}
            next_entry = {
                'logical_key': f'agent:{agent_id}:project:{project_key}',
                'current_session_key': str(session_meta.get('session_key') or f'agent:{agent_id}:project:{project_key}'),
                'role': role,
                'project_key': project_key,
                'generation': int(existing.get('generation') or 1),
                'status': 'active',
                'updated_at_ms': ts,
            }
            if session_meta.get('session_id'):
                next_entry['session_id'] = session_meta['session_id']
            if session_meta.get('session_file'):
                next_entry['session_file'] = session_meta['session_file']
            registry[registry_key] = next_entry

        ceo_key = 'agent-team-ceo|shared'
        existing_ceo = registry.get(ceo_key) if isinstance(registry.get(ceo_key), dict) else {}
        registry[ceo_key] = {
            'logical_key': str(existing_ceo.get('logical_key') or 'agent:agent-team-ceo:shared'),
            'current_session_key': str(existing_ceo.get('current_session_key') or existing_ceo.get('logical_key') or 'agent:agent-team-ceo:shared'),
            'role': 'ceo',
            'project_key': 'shared',
            'generation': int(existing_ceo.get('generation') or 1),
            'status': str(existing_ceo.get('status') or 'active'),
            'updated_at_ms': int(existing_ceo.get('updated_at_ms') or ts),
        }
        if existing_ceo.get('session_id'):
            registry[ceo_key]['session_id'] = existing_ceo['session_id']
        if existing_ceo.get('session_file'):
            registry[ceo_key]['session_file'] = existing_ceo['session_file']
        save_session_registry(registry)

    def create_project(
        self,
        *,
        name: str,
        description: str = '',
        project_key: str | None = None,
        created_by_employee_key: str = 'shared.ceo',
        initialize_sessions: bool = True,
    ) -> dict[str, Any]:
        project_name = str(name or '').strip()
        if not project_name:
            raise ValidationError('project name is required')
        project_description = str(description or '').strip()
        normalized_key = slugify_project_key(project_key or project_name)
        if not normalized_key:
            raise ValidationError('project_key is required when the project name cannot be converted into a stable key')
        if self.db.conn.execute('SELECT 1 FROM projects WHERE project_key = ?', (normalized_key,)).fetchone():
            raise ValidationError(f'project already exists: {normalized_key}')

        creator = self.db.get_one(
            'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?',
            (created_by_employee_key or 'shared.ceo',),
        )
        shared_ceo = self.db.get_one(
            'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?',
            ('shared.ceo',),
        )
        role_templates = {role: self._role_template_row(role) for role in PROJECT_ROLE_ORDER}

        runtime_blueprints: list[dict[str, Any]] = []
        role_sessions: dict[str, dict[str, Any]] = {}
        for role in PROJECT_ROLE_ORDER:
            employee_key = f'{normalized_key}.{role}'
            binding_key = f'{employee_key}.primary'
            if self.db.conn.execute('SELECT 1 FROM employee_instances WHERE employee_key = ?', (employee_key,)).fetchone():
                raise ValidationError(f'employee already exists: {employee_key}')
            if self.db.conn.execute('SELECT 1 FROM runtime_bindings WHERE binding_key = ?', (binding_key,)).fetchone():
                raise ValidationError(f'runtime binding already exists: {binding_key}')

            seed = self._runtime_seed_for_role(role, role_templates[role])
            session_meta = {
                'agent_id': seed['agent_id'],
                'session_key': f"agent:{seed['agent_id']}:project:{normalized_key}",
                'session_id': '',
                'session_file': '',
                'label': f'{project_name} · {ROLE_DISPLAY_NAMES.get(role, role.upper())}',
                'run_started': False,
                'run_error': None,
            }
            if initialize_sessions:
                try:
                    session_meta = self._ensure_openclaw_project_session(
                        project_key=normalized_key,
                        project_name=project_name,
                        description=project_description,
                        role=role,
                        agent_id=str(seed['agent_id']),
                        model=str(seed['model']) if seed.get('model') else None,
                    )
                except Exception as exc:
                    raise ValidationError(f'failed to initialize {role.upper()} session: {exc}') from exc
            role_sessions[role] = session_meta
            runtime_blueprints.append({
                'role': role,
                'employee_key': employee_key,
                'display_name': f'{project_name} {ROLE_DISPLAY_NAMES[role]}',
                'role_template_id': role_templates[role]['id'],
                'binding_key': binding_key,
                'agent_id': str(seed['agent_id']),
                'session_key': str(session_meta['session_key']),
                'workspace_path': str(seed['workspace_path']),
                'model': seed.get('model'),
                'tool_policy_json': seed.get('tool_policy_json'),
                'skills_profile_json': seed.get('skills_profile_json'),
                'seed_metadata_json': seed.get('metadata_json'),
                'session_meta': session_meta,
            })

        ts = now_ms()
        project_id = uid('proj')
        project_metadata = {
            'project_context_md': project_description,
            'created_by_employee_key': creator['employee_key'],
            'auto_created_roles': list(PROJECT_ROLE_ORDER),
            'created_via': 'agent_team_service.create_project',
            'initialized_session_keys': {role: meta['session_key'] for role, meta in role_sessions.items()},
        }

        employee_rows: list[dict[str, Any]] = []
        runtime_rows: list[dict[str, Any]] = []
        employee_ids: dict[str, str] = {}
        try:
            self.db.conn.execute(
                '''INSERT INTO projects (
                    id, project_key, name, description, status, metadata_json, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)''',
                (
                    project_id,
                    normalized_key,
                    project_name,
                    project_description,
                    json.dumps(project_metadata, ensure_ascii=False),
                    ts,
                    ts,
                ),
            )

            for blueprint in runtime_blueprints:
                role = blueprint['role']
                employee_id = uid('emp')
                manager_employee_id = shared_ceo['id'] if role == 'pm' else employee_ids['pm']
                employee_metadata = {
                    'project_key': normalized_key,
                    'project_name': project_name,
                    'project_context_md': project_description,
                    'role': role,
                    'auto_created': True,
                }
                self.db.conn.execute(
                    '''INSERT INTO employee_instances (
                        id, employee_key, display_name, employment_scope, project_id,
                        role_template_id, manager_employee_id, status, notes, metadata_json,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, 'project', ?, ?, ?, 'active', ?, ?, ?, ?)''',
                    (
                        employee_id,
                        blueprint['employee_key'],
                        blueprint['display_name'],
                        project_id,
                        blueprint['role_template_id'],
                        manager_employee_id,
                        'Auto-created during project provisioning',
                        json.dumps(employee_metadata, ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                employee_ids[role] = employee_id
                blueprint['employee_id'] = employee_id
                employee_rows.append({
                    'role': role,
                    'employee_key': blueprint['employee_key'],
                    'display_name': blueprint['display_name'],
                    'manager_employee_id': manager_employee_id,
                })

            for blueprint in runtime_blueprints:
                runtime_id = uid('rb')
                session_meta = blueprint['session_meta']
                runtime_metadata = merge_json_object(blueprint.get('seed_metadata_json'), {
                    'project_key': normalized_key,
                    'project_name': project_name,
                    'project_context_md': project_description,
                    'role': blueprint['role'],
                    'auto_created': True,
                    'session_id': session_meta.get('session_id') or None,
                    'session_file': session_meta.get('session_file') or None,
                    'session_label': session_meta.get('label') or None,
                    'session_bootstrap_run_started': bool(session_meta.get('run_started')),
                    'session_bootstrap_error': session_meta.get('run_error'),
                })
                self.db.conn.execute(
                    '''INSERT INTO runtime_bindings (
                        id, employee_id, runtime_type, binding_key, agent_id, session_key, model,
                        workspace_path, memory_scope, tool_policy_json, skills_profile_json,
                        status, is_primary, metadata_json, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, 'openclaw_session', ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)''',
                    (
                        runtime_id,
                        blueprint['employee_id'],
                        blueprint['binding_key'],
                        blueprint['agent_id'],
                        blueprint['session_key'],
                        blueprint.get('model'),
                        blueprint['workspace_path'],
                        f'project:{normalized_key}',
                        blueprint.get('tool_policy_json'),
                        blueprint.get('skills_profile_json'),
                        json.dumps(runtime_metadata, ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                runtime_rows.append({
                    'role': blueprint['role'],
                    'binding_key': blueprint['binding_key'],
                    'agent_id': blueprint['agent_id'],
                    'session_key': blueprint['session_key'],
                    'workspace_path': blueprint['workspace_path'],
                })

            self._upsert_project_session_registry(project_key=normalized_key, role_sessions=role_sessions)
            self.db.commit()
        except Exception:
            self.db.conn.rollback()
            raise

        return {
            'project_id': project_id,
            'project_key': normalized_key,
            'name': project_name,
            'description': project_description,
            'created_at_ms': ts,
            'created_by_employee_key': creator['employee_key'],
            'shared_ceo_employee_key': shared_ceo['employee_key'],
            'employees': employee_rows,
            'runtime_bindings': runtime_rows,
            'initialized_sessions': role_sessions,
        }

    def update_project(
        self,
        *,
        project_key: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        project = self.db.get_one(
            'SELECT id, project_key, name, description, metadata_json FROM projects WHERE project_key = ?',
            (project_key,),
        )
        next_name = str(name).strip() if name is not None else str(project['name'] or '')
        next_description = str(description).strip() if description is not None else str(project['description'] or '')
        if not next_name:
            raise ValidationError('project name is required')

        metadata = merge_json_object(project['metadata_json'], None)
        metadata['project_context_md'] = next_description
        ts = now_ms()
        self.db.conn.execute(
            '''UPDATE projects
               SET name = ?, description = ?, metadata_json = ?, updated_at_ms = ?
               WHERE id = ?''',
            (
                next_name,
                next_description,
                json.dumps(metadata, ensure_ascii=False),
                ts,
                project['id'],
            ),
        )
        self.db.conn.execute(
            '''UPDATE employee_instances
               SET display_name = CASE
                   WHEN employee_key = ? THEN ?
                   WHEN employee_key = ? THEN ?
                   WHEN employee_key = ? THEN ?
                   WHEN employee_key = ? THEN ?
                   ELSE display_name
               END,
                   metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.project_name', ?, '$.project_context_md', ?),
                   updated_at_ms = ?
               WHERE project_id = ?''',
            (
                f'{project_key}.pm', f'{next_name} PM',
                f'{project_key}.dev', f'{next_name} Dev',
                f'{project_key}.qa', f'{next_name} QA',
                f'{project_key}.ops', f'{next_name} Ops',
                next_name,
                next_description,
                ts,
                project['id'],
            ),
        )
        self.db.conn.execute(
            '''UPDATE runtime_bindings
               SET metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.project_name', ?, '$.project_context_md', ?),
                   updated_at_ms = ?
               WHERE employee_id IN (SELECT id FROM employee_instances WHERE project_id = ?)''',
            (
                next_name,
                next_description,
                ts,
                project['id'],
            ),
        )
        self.db.commit()
        return {
            'project_key': project_key,
            'name': next_name,
            'description': next_description,
            'updated_at_ms': ts,
        }

    def delete_project(
        self,
        *,
        project_key: str,
        delete_openclaw_sessions: bool = False,
    ) -> dict[str, Any]:
        if delete_openclaw_sessions:
            raise ValidationError('delete_project never deletes OpenClaw sessions in this workflow')
        if str(project_key or '').strip() == 'agent-team-core':
            raise ValidationError('agent-team-core is the canonical base project and cannot be deleted from the UI')

        project = self.db.get_one(
            'SELECT id, project_key, name, description, metadata_json FROM projects WHERE project_key = ?',
            (project_key,),
        )
        employee_rows = self.db.fetch_all(
            '''SELECT ei.id, ei.employee_key, rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.project_id = ?
               ORDER BY ei.employee_key''',
            (project['id'],),
        )
        employee_ids = [str(row['id']) for row in employee_rows]
        employee_keys = [str(row['employee_key']) for row in employee_rows]
        runtime_rows = self.db.fetch_all(
            'SELECT id, binding_key, session_key, agent_id FROM runtime_bindings WHERE employee_id IN (%s) ORDER BY binding_key' % ','.join('?' for _ in employee_ids),
            tuple(employee_ids),
        ) if employee_ids else []

        ts = now_ms()
        registry = load_session_registry()
        deleted_registry_keys: list[str] = []
        for role in PROJECT_ROLE_ORDER:
            registry_key = f'{ROLE_AGENT_IDS[role]}|{project_key}'
            if registry_key in registry:
                deleted_registry_keys.append(registry_key)
                del registry[registry_key]

        try:
            self.db.conn.execute('DELETE FROM issues WHERE project_id = ?', (project['id'],))
            if employee_ids:
                placeholders = ','.join('?' for _ in employee_ids)
                self.db.conn.execute(f'DELETE FROM runtime_bindings WHERE employee_id IN ({placeholders})', tuple(employee_ids))
                self.db.conn.execute(f'DELETE FROM issue_activities WHERE actor_employee_id IN ({placeholders})', tuple(employee_ids))
                self.db.conn.execute(f'DELETE FROM issue_checkpoints WHERE created_by_employee_id IN ({placeholders})', tuple(employee_ids))
                self.db.conn.execute(f'DELETE FROM issue_relations WHERE created_by_employee_id IN ({placeholders})', tuple(employee_ids))
                self.db.conn.execute(f'DELETE FROM employee_instances WHERE id IN ({placeholders})', tuple(employee_ids))
            self.db.conn.execute('DELETE FROM projects WHERE id = ?', (project['id'],))
            save_session_registry(registry)
            self.db.commit()
        except Exception:
            self.db.conn.rollback()
            raise

        return {
            'project_key': project['project_key'],
            'name': project['name'],
            'deleted_at_ms': ts,
            'deleted_employees': employee_keys,
            'deleted_runtime_bindings': [str(row['binding_key']) for row in runtime_rows],
            'preserved_session_keys': [str(row['session_key']) for row in runtime_rows if row['session_key']],
            'deleted_session_registry_keys': deleted_registry_keys,
            'openclaw_sessions_deleted': False,
        }

    def create_issue(
        self,
        *,
        project_key: str,
        owner_employee_key: str,
        title: str,
        description_md: str = '',
        acceptance_criteria_md: str = '',
        priority: str = 'p2',
        source_type: str = 'system',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_type not in {'user', 'system', 'detector', 'watchdog', 'human'}:
            raise ValidationError(f'unsupported source_type: {source_type}')
        project = self.db.get_one('SELECT id FROM projects WHERE project_key = ?', (project_key,))
        owner = self.db.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', (owner_employee_key,))
        ts = now_ms()
        issue_id = uid('issue')
        issue_no = self._next_issue_no(project['id'])
        self.db.conn.execute(
            '''INSERT INTO issues (
                id, project_id, issue_no, title, description_md, source_type, priority,
                status, owner_employee_id, acceptance_criteria_md, metadata_json,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)''',
            (
                issue_id,
                project['id'],
                issue_no,
                title,
                description_md,
                source_type,
                priority,
                owner['id'],
                acceptance_criteria_md,
                json.dumps(metadata or {}, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='issue_created',
            summary=f'Issue #{issue_no} created',
            actor_employee_id=owner['id'],
            details={'project_key': project_key, 'title': title, 'priority': priority, 'source_type': source_type},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'issue_no': issue_no,
            'status': 'open',
            'source_type': source_type,
            'created_at_ms': ts,
        }

    def triage_issue(self, *, issue_id: str, assign_employee_key: str) -> dict[str, Any]:
        employee = self.db.get_one('SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', (assign_employee_key,))
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, assigned_employee_id = ?, updated_at_ms = ? WHERE id = ?',
            ('triaged', employee['id'], ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='triaged',
            summary=f'Issue triaged to {employee["employee_key"]}',
            actor_employee_id=employee['id'],
            details={'assign_employee_key': employee['employee_key']},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'triaged',
            'assign_employee_key': employee['employee_key'],
            'updated_at_ms': ts,
        }

    def handoff_issue(
        self,
        *,
        issue_id: str,
        to_employee_key: str,
        note: str = '',
        issue_type: str = 'normal',
        risk_level: str = 'normal',
        handoff_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        issue = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        if issue['assigned_employee_id'] is None:
            raise ValidationError('issue has no assigned employee to route from')
        from_role = self._employee_role(issue['assigned_employee_id'])

        employee = self.db.get_one(
            '''SELECT ei.id, ei.employee_key, rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.employee_key = ?''',
            (to_employee_key,),
        )
        to_role = str(employee['role'])
        decision = route_issue(from_role=from_role, to_role=to_role, issue_type=issue_type, risk_level=risk_level)
        if not decision.allowed:
            raise ValidationError(decision.reason)

        ts = now_ms()
        metadata = merge_json_object(issue['metadata_json'], {})
        normalized_handoff = dict(handoff_payload) if isinstance(handoff_payload, dict) else {}
        if note and not normalized_handoff.get('summary'):
            normalized_handoff['summary'] = note
        normalized_handoff.setdefault('reason', note or f'handoff {from_role} -> {to_role}')
        normalized_handoff.setdefault('suggested_next_role', to_role)
        normalized_handoff.setdefault('from_role', from_role)
        metadata['prior_handoff'] = normalized_handoff
        metadata['suggested_next_role'] = normalized_handoff.get('suggested_next_role') or to_role
        self.db.conn.execute(
            'UPDATE issues SET status = ?, assigned_employee_id = ?, blocker_summary = NULL, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
            ('ready', employee['id'], json.dumps(metadata, ensure_ascii=False), ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='handoff',
            summary=f'Handoff {from_role} -> {to_role}',
            actor_employee_id=employee['id'],
            details={'to_employee_key': employee['employee_key'], 'note': note, 'routing_reason': decision.reason},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'ready',
            'assigned_employee_key': employee['employee_key'],
            'from_role': from_role,
            'to_role': to_role,
            'routing_reason': decision.reason,
            'updated_at_ms': ts,
        }

    def dispatch_execution(
        self,
        *,
        issue_id: str,
        runtime_binding_key: str,
        payload: dict[str, Any],
        dispatch_ref: str | None = None,
    ) -> dict[str, Any]:
        return self.dispatch_service.dispatch_execution(
            issue_id=issue_id,
            runtime_binding_key=runtime_binding_key,
            payload=payload,
            dispatch_ref=dispatch_ref,
        )

    def record_attempt_callback(
        self,
        *,
        attempt_id: str,
        callback_token: str,
        phase: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.dispatch_service.record_attempt_callback(
            attempt_id=attempt_id,
            callback_token=callback_token,
            phase=phase,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def observe_execution(self, *, dispatch_ref: str, expected_text: str | None = None, expected_marker: str | None = None, timeout_seconds: int = 1, close_issue_on_success: bool = False) -> dict[str, Any]:
        return self.dispatch_service.observe_execution(
            dispatch_ref=dispatch_ref,
            expected_text=expected_text,
            expected_marker=expected_marker,
            timeout_seconds=timeout_seconds,
            close_issue_on_success=close_issue_on_success,
        )

    def cancel_execution(self, *, dispatch_ref: str, reason: str = 'cancelled_by_service') -> dict[str, Any]:
        return self.dispatch_service.cancel_execution(dispatch_ref=dispatch_ref, reason=reason)

    def reconcile_stale_attempt(self, *, dispatch_ref: str, reason: str = 'stale_dispatch_reconciled') -> dict[str, Any]:
        return self.dispatch_service.reconcile_stale_attempt(dispatch_ref=dispatch_ref, reason=reason)

    def retry_execution(self, *, issue_id: str, runtime_binding_key: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        return self.dispatch_service.retry_execution(
            issue_id=issue_id,
            runtime_binding_key=runtime_binding_key,
            payload=payload,
            reason=reason,
        )

    def observe_dispatch_lifecycle_event(
        self,
        *,
        dispatch_ref: str,
        state: str,
        stop_reason: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.dispatch_service.observe_dispatch_lifecycle_event(
            dispatch_ref=dispatch_ref,
            state=state,
            stop_reason=stop_reason,
            error_message=error_message,
            payload=payload,
        )

    def apply_artifact_gate(
        self,
        *,
        issue_id: str,
        artifact_payload: dict[str, Any],
        current_role: str | None,
        summary: str | None = None,
        suggested_next_role: str | None = None,
    ) -> dict[str, Any]:
        ts = now_ms()
        issue = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        metadata = merge_json_object(issue['metadata_json'], {})
        handoff_payload = {
            'marker': '',
            'status': 'done',
            'summary': summary or str(artifact_payload.get('summary') or 'Existing artifact already satisfies acceptance'),
            'artifacts': [artifact_payload],
            'blocking_findings': [],
            'suggested_next_role': suggested_next_role or default_next_role_for(current_role),
            'reason': 'existing artifact satisfies acceptance; dispatch skipped',
            'risk_level': str(metadata.get('risk_level') or 'normal'),
            'needs_human': False,
        }
        metadata['prior_handoff'] = handoff_payload
        metadata['suggested_next_role'] = handoff_payload['suggested_next_role']
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = NULL, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
            ('review', json.dumps(metadata, ensure_ascii=False), ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='artifact_gate',
            summary='Dispatch skipped because existing artifact satisfies acceptance',
            actor_employee_id=issue['assigned_employee_id'],
            details={'artifact_payload': artifact_payload, 'handoff_payload': handoff_payload},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'review',
            'handoff_payload': handoff_payload,
            'updated_at_ms': ts,
        }

    def close_issue(self, *, issue_id: str, resolution: str = 'completed') -> dict[str, Any]:
        ts = now_ms()
        issue = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        open_children = self.db.conn.execute(
            '''SELECT COUNT(*)
               FROM issue_relations ir
               JOIN issues child ON child.id = ir.to_issue_id
               WHERE ir.from_issue_id = ? AND ir.relation_type = 'parent_of' AND child.status != 'closed' ''',
            (issue_id,),
        ).fetchone()[0]
        if int(open_children or 0) > 0:
            metadata = merge_json_object(issue['metadata_json'], {})
            metadata.setdefault('orchestration_parent', True)
            metadata.setdefault('parent_wait_strategy', 'wait_children_then_review')
            metadata.setdefault('resume_role', 'ceo')
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                ('waiting_children', f'waiting for {int(open_children)} child issue(s) to close', json.dumps(metadata, ensure_ascii=False), ts, issue_id),
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=issue_id,
                action_type='issue_waiting_children',
                summary='Issue moved to waiting_children instead of closing',
                actor_employee_id=issue['assigned_employee_id'],
                details={'resolution': resolution, 'open_child_count': int(open_children), 'resume_role': metadata.get('resume_role'), 'parent_wait_strategy': metadata.get('parent_wait_strategy')},
            )
            self.db.commit()
            return {
                'issue_id': issue_id,
                'status': 'waiting_children',
                'resolution': resolution,
                'open_child_count': int(open_children),
                'updated_at_ms': ts,
            }
        self.db.conn.execute(
            'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = NULL, updated_at_ms = ? WHERE id = ?',
            ('closed', ts, ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='issue_closed',
            summary='Issue closed',
            actor_employee_id=issue['assigned_employee_id'],
            details={'resolution': resolution},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'closed',
            'resolution': resolution,
            'closed_at_ms': ts,
        }

    def enqueue_human(
        self,
        *,
        issue_id: str,
        human_type: str,
        prompt: str,
        required_input: str,
    ) -> dict[str, Any]:
        return self.human_queue.enqueue_human(
            issue_id=issue_id,
            human_type=human_type,
            prompt=prompt,
            required_input=required_input,
        )

    def resolve_human_action(
        self,
        *,
        issue_id: str,
        resolution: str,
        note: str = '',
        next_employee_key: str | None = None,
        next_role: str | None = None,
    ) -> dict[str, Any]:
        return self.human_queue.resolve_human_action(
            issue_id=issue_id,
            resolution=resolution,
            note=note,
            next_employee_key=next_employee_key,
            next_role=next_role,
        )

    def get_issue_activity(self, *, issue_id: str) -> dict[str, Any]:
        return self.board_query.get_issue_activity(issue_id=issue_id)

    def get_human_queue(self) -> dict[str, Any]:
        return self.human_queue.get_human_queue()

    def list_issues(self, *, project_key: str | None = None, status: str | None = None) -> dict[str, Any]:
        return self.board_query.list_issues(project_key=project_key, status=status)

    def get_issue(self, *, issue_id: str) -> dict[str, Any]:
        return self.board_query.get_issue(issue_id=issue_id)

    def get_attempt_timeline(self, *, attempt_id: str) -> dict[str, Any]:
        return self.board_query.get_attempt_timeline(attempt_id=attempt_id)

    def get_agent_workload(self) -> dict[str, Any]:
        return self.board_query.get_agent_workload()

    def get_board_snapshot(self) -> dict[str, Any]:
        return self.board_query.get_board_snapshot()

    def get_ui_snapshot(self, *, generated_at: str | None = None, source: str | None = None) -> dict[str, Any]:
        return self.board_query.get_ui_snapshot(generated_at=generated_at, source=source)

    def reconcile_dependency_transitions(self) -> dict[str, Any]:
        return self.dependency_service.reconcile_dependency_transitions()

    def create_derived_issues(
        self,
        *,
        attempt_id: str,
        proposals: list[dict[str, Any]],
        created_by_role: str | None = None,
    ) -> dict[str, Any]:
        return self.derived_issue_service.create_derived_issues(
            attempt_id=attempt_id,
            proposals=proposals,
            created_by_role=created_by_role,
        )

    def _project_id_by_key(self, project_key: str) -> str:
        row = self.db.get_one('SELECT id FROM projects WHERE project_key = ?', (project_key,))
        return str(row['id'])

    def _employee_id_by_key(self, employee_key: str) -> str:
        row = self.db.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', (employee_key,))
        return str(row['id'])

    def _pick_employee_key_for_role(self, *, project_key: str, role: str) -> str:
        if role == 'ceo':
            row = self.db.conn.execute(
                '''SELECT ei.employee_key
                   FROM employee_instances ei
                   JOIN role_templates rt ON rt.id = ei.role_template_id
                   WHERE rt.template_key = 'ceo'
                   ORDER BY ei.employee_key ASC
                   LIMIT 1'''
            ).fetchone()
            if row:
                return str(row[0])
            raise ValidationError('missing CEO employee')
        row = self.db.conn.execute(
            '''SELECT ei.employee_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN projects p ON p.id = ei.project_id
               WHERE rt.template_key = ? AND p.project_key = ?
               ORDER BY ei.employee_key ASC
               LIMIT 1''',
            (role, project_key),
        ).fetchone()
        if row:
            return str(row[0])
        raise ValidationError(f'missing employee for role={role} project={project_key}')

    def _default_owner_employee_key(self, project_key: str) -> str:
        row = self.db.conn.execute(
            '''SELECT ei.employee_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN projects p ON p.id = ei.project_id
               WHERE (rt.template_key = 'pm' AND p.project_key = ?)
                  OR rt.template_key = 'ceo'
               ORDER BY CASE rt.template_key WHEN 'pm' THEN 0 ELSE 1 END, ei.employee_key ASC
               LIMIT 1''',
            (project_key,),
        ).fetchone()
        if not row:
            raise ValidationError(f'missing default owner for project={project_key}')
        return str(row[0])

    def _normalize_schedule_config(self, *, schedule_kind: str, schedule_config: dict[str, Any]) -> dict[str, Any]:
        kind = str(schedule_kind or '').strip()
        cfg = dict(schedule_config or {})
        if kind == 'hourly':
            return {'minute': max(0, min(int(cfg.get('minute', 0) or 0), 59))}
        if kind == 'daily':
            return {
                'hour': max(0, min(int(cfg.get('hour', 9) or 9), 23)),
                'minute': max(0, min(int(cfg.get('minute', 0) or 0), 59)),
            }
        if kind == 'weekly':
            return {
                'weekday': max(0, min(int(cfg.get('weekday', 0) or 0), 6)),
                'hour': max(0, min(int(cfg.get('hour', 9) or 9), 23)),
                'minute': max(0, min(int(cfg.get('minute', 0) or 0), 59)),
            }
        if kind == 'monthly':
            return {
                'day': max(1, min(int(cfg.get('day', 1) or 1), 31)),
                'hour': max(0, min(int(cfg.get('hour', 9) or 9), 23)),
                'minute': max(0, min(int(cfg.get('minute', 0) or 0), 59)),
            }
        if kind == 'interval':
            return {'every_minutes': max(1, int(cfg.get('every_minutes', 60) or 60))}
        if kind == 'one_time':
            run_at_ms = cfg.get('run_at_ms')
            if run_at_ms is None:
                raise ValidationError('one_time schedule requires run_at_ms')
            return {'run_at_ms': int(run_at_ms)}
        if kind == 'cron':
            expr = str(cfg.get('expr') or '').strip()
            if not expr:
                raise ValidationError('cron schedule requires expr')
            return {'expr': expr}
        raise ValidationError(f'unsupported schedule kind: {schedule_kind}')

    def list_scheduled_issues(self, *, project_key: str | None = None) -> dict[str, Any]:
        sql = '''SELECT si.*, p.project_key, p.name AS project_name, oe.employee_key AS owner_employee_key
                 FROM scheduled_issues si
                 JOIN projects p ON p.id = si.project_id
                 LEFT JOIN employee_instances oe ON oe.id = si.owner_employee_id'''
        params: list[Any] = []
        if project_key:
            sql += ' WHERE p.project_key = ?'
            params.append(project_key)
        sql += ' ORDER BY si.updated_at_ms DESC, si.created_at_ms DESC'
        rows = self.db.fetch_all(sql, tuple(params))
        items = []
        for row in rows:
            item = dict(row)
            item['schedule_config'] = parse_schedule_config(item.get('schedule_config_json'))
            item['recent_runs'] = [
                dict(r)
                for r in self.db.fetch_all(
                    '''SELECT id, status, issue_id, issue_no, error_message, details_json, created_at_ms
                       FROM scheduled_issue_runs
                       WHERE scheduled_issue_id = ?
                       ORDER BY created_at_ms DESC
                       LIMIT 10''',
                    (item['id'],),
                )
            ]
            items.append(item)
        return {'items': items, 'total': len(items)}

    def create_scheduled_issue(
        self,
        *,
        project_key: str,
        title: str,
        description_md: str = '',
        acceptance_criteria_md: str = '',
        priority: str = 'p2',
        route_role: str = 'pm',
        source_type: str = 'system',
        dispatch_instruction: str = '',
        schedule_kind: str,
        schedule_config: dict[str, Any],
        owner_employee_key: str | None = None,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = self._project_id_by_key(project_key)
        owner_key = owner_employee_key or self._default_owner_employee_key(project_key)
        owner_id = self._employee_id_by_key(owner_key)
        normalized_cfg = self._normalize_schedule_config(schedule_kind=schedule_kind, schedule_config=schedule_config)
        now_dt = datetime.now(UTC)
        next_run = compute_next_scheduled_run(
            schedule_kind=schedule_kind,
            schedule_config=normalized_cfg,
            now_dt=now_dt,
            last_run_at_ms=None,
        ) if enabled else None
        ts = now_ms()
        schedule_id = uid('sched')
        meta = dict(metadata or {})
        meta.setdefault('created_via', 'agent_team_ui')
        self.db.conn.execute(
            '''INSERT INTO scheduled_issues (
                id, project_id, owner_employee_id, title, description_md, acceptance_criteria_md,
                priority, route_role, source_type, dispatch_instruction, schedule_kind,
                schedule_config_json, timezone, enabled, next_run_at_ms, metadata_json,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UTC', ?, ?, ?, ?, ?)''',
            (
                schedule_id,
                project_id,
                owner_id,
                title,
                description_md,
                acceptance_criteria_md,
                priority,
                route_role,
                source_type,
                dispatch_instruction,
                schedule_kind,
                json.dumps(normalized_cfg, ensure_ascii=False),
                1 if enabled else 0,
                int(next_run.timestamp() * 1000) if next_run else None,
                json.dumps(meta, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        self.db.commit()
        return {
            'scheduled_issue_id': schedule_id,
            'project_key': project_key,
            'owner_employee_key': owner_key,
            'next_run_at_ms': int(next_run.timestamp() * 1000) if next_run else None,
            'enabled': enabled,
        }

    def update_scheduled_issue(self, *, scheduled_issue_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        row = self.db.get_one('SELECT * FROM scheduled_issues WHERE id = ?', (scheduled_issue_id,))
        current = dict(row)
        current_project_key = str(self.db.get_one('SELECT project_key FROM projects WHERE id = ?', (current['project_id'],))['project_key'])
        project_key = str(patch.get('project_key') or current_project_key)
        project_id = self._project_id_by_key(project_key)
        owner_key = patch.get('owner_employee_key') or (
            self.db.get_one('SELECT employee_key FROM employee_instances WHERE id = ?', (current['owner_employee_id'],))['employee_key']
            if current.get('owner_employee_id') else self._default_owner_employee_key(project_key)
        )
        owner_id = self._employee_id_by_key(str(owner_key))
        schedule_kind = str(patch.get('schedule_kind') or current['schedule_kind'])
        schedule_config = self._normalize_schedule_config(
            schedule_kind=schedule_kind,
            schedule_config=patch.get('schedule_config') or parse_schedule_config(current.get('schedule_config_json')),
        )
        enabled = bool(current['enabled']) if 'enabled' not in patch else bool(patch.get('enabled'))
        last_run_at_ms = patch.get('last_run_at_ms', current.get('last_run_at_ms'))
        next_run = compute_next_scheduled_run(
            schedule_kind=schedule_kind,
            schedule_config=schedule_config,
            now_dt=datetime.now(UTC),
            last_run_at_ms=last_run_at_ms,
        ) if enabled else None
        metadata = merge_json_object(current.get('metadata_json'), patch.get('metadata') if isinstance(patch.get('metadata'), dict) else None)
        ts = now_ms()
        self.db.conn.execute(
            '''UPDATE scheduled_issues
               SET project_id = ?, owner_employee_id = ?, title = ?, description_md = ?, acceptance_criteria_md = ?,
                   priority = ?, route_role = ?, source_type = ?, dispatch_instruction = ?,
                   schedule_kind = ?, schedule_config_json = ?, enabled = ?, next_run_at_ms = ?,
                   metadata_json = ?, updated_at_ms = ?
             WHERE id = ?''',
            (
                project_id,
                owner_id,
                patch.get('title', current['title']),
                patch.get('description', patch.get('description_md', current.get('description_md') or '')),
                patch.get('acceptance', patch.get('acceptance_criteria_md', current.get('acceptance_criteria_md') or '')),
                patch.get('priority', current['priority']),
                patch.get('route_role', current['route_role']),
                patch.get('source_type', current['source_type']),
                patch.get('dispatch_instruction', current.get('dispatch_instruction') or ''),
                schedule_kind,
                json.dumps(schedule_config, ensure_ascii=False),
                1 if enabled else 0,
                int(next_run.timestamp() * 1000) if next_run else None,
                json.dumps(metadata, ensure_ascii=False),
                ts,
                scheduled_issue_id,
            ),
        )
        self.db.commit()
        return {
            'scheduled_issue_id': scheduled_issue_id,
            'project_key': project_key,
            'enabled': enabled,
            'next_run_at_ms': int(next_run.timestamp() * 1000) if next_run else None,
        }

    def delete_scheduled_issue(self, *, scheduled_issue_id: str) -> dict[str, Any]:
        row = self.db.get_one('SELECT id, title FROM scheduled_issues WHERE id = ?', (scheduled_issue_id,))
        self.db.conn.execute('DELETE FROM scheduled_issues WHERE id = ?', (scheduled_issue_id,))
        self.db.commit()
        return {'scheduled_issue_id': row['id'], 'title': row['title'], 'deleted': True}

    def _create_issue_from_schedule(self, row: dict[str, Any] | Any, *, manual: bool = False, now_ts: int | None = None) -> dict[str, Any]:
        record = dict(row)
        ts = int(now_ts or now_ms())
        project_row = self.db.get_one('SELECT project_key FROM projects WHERE id = ?', (record['project_id'],))
        project_key = str(project_row['project_key'])
        owner_row = self.db.get_one('SELECT employee_key FROM employee_instances WHERE id = ?', (record['owner_employee_id'],))
        owner_employee_key = str(owner_row['employee_key'])
        metadata = merge_json_object(
            record.get('metadata_json'),
            {
                'source': 'scheduled_issue',
                'scheduled_issue_id': record['id'],
                'created_via': 'scheduled_issue_runner',
                'dispatch_instruction': record.get('dispatch_instruction') or '',
            },
        )
        created = self.create_issue(
            project_key=project_key,
            owner_employee_key=owner_employee_key,
            title=str(record['title']),
            description_md=str(record.get('description_md') or ''),
            acceptance_criteria_md=str(record.get('acceptance_criteria_md') or ''),
            priority=str(record['priority']),
            source_type=str(record['source_type']),
            metadata=metadata,
        )
        assign_employee_key = self._pick_employee_key_for_role(project_key=project_key, role=str(record['route_role']))
        triaged = self.triage_issue(issue_id=created['issue_id'], assign_employee_key=assign_employee_key)
        run_id = uid('schedrun')
        self.db.conn.execute(
            '''INSERT INTO scheduled_issue_runs (
                id, scheduled_issue_id, status, issue_id, issue_no, error_message, details_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                run_id,
                record['id'],
                'manual_created' if manual else 'created',
                created['issue_id'],
                created['issue_no'],
                None,
                json.dumps({'assigned_employee_key': assign_employee_key, 'triaged': triaged}, ensure_ascii=False),
                ts,
            ),
        )
        schedule_config = parse_schedule_config(record.get('schedule_config_json'))
        next_run = compute_next_scheduled_run(
            schedule_kind=str(record['schedule_kind']),
            schedule_config=schedule_config,
            now_dt=datetime.now(UTC),
            last_run_at_ms=ts,
        ) if bool(record['enabled']) and str(record['schedule_kind']) != 'one_time' else None
        enabled = bool(record['enabled']) and str(record['schedule_kind']) != 'one_time'
        self.db.conn.execute(
            '''UPDATE scheduled_issues
               SET last_run_at_ms = ?, last_issue_id = ?, last_issue_no = ?, last_error = NULL,
                   enabled = ?, next_run_at_ms = ?, updated_at_ms = ?
             WHERE id = ?''',
            (
                ts,
                created['issue_id'],
                created['issue_no'],
                1 if enabled else 0,
                int(next_run.timestamp() * 1000) if next_run else None,
                ts,
                record['id'],
            ),
        )
        self.db.commit()
        return {
            'scheduled_issue_id': record['id'],
            'run_id': run_id,
            'created_issue': created,
            'assigned_employee_key': assign_employee_key,
            'next_run_at_ms': int(next_run.timestamp() * 1000) if next_run else None,
            'enabled': enabled,
        }

    def run_due_scheduled_issues(self, *, now_ts: int | None = None, limit: int = 20) -> dict[str, Any]:
        ts = int(now_ts or now_ms())
        rows = self.db.fetch_all(
            '''SELECT * FROM scheduled_issues
               WHERE enabled = 1 AND next_run_at_ms IS NOT NULL AND next_run_at_ms <= ?
               ORDER BY next_run_at_ms ASC, created_at_ms ASC
               LIMIT ?''',
            (ts, limit),
        )
        created_items = []
        failed_items = []
        for row in rows:
            try:
                created_items.append(self._create_issue_from_schedule(row, manual=False, now_ts=ts))
            except Exception as e:
                run_id = uid('schedrun')
                self.db.conn.execute(
                    '''INSERT INTO scheduled_issue_runs (
                        id, scheduled_issue_id, status, issue_id, issue_no, error_message, details_json, created_at_ms
                    ) VALUES (?, ?, 'failed', NULL, NULL, ?, ?, ?)''',
                    (run_id, row['id'], str(e), json.dumps({'scheduled_issue_id': row['id']}, ensure_ascii=False), ts),
                )
                self.db.conn.execute(
                    'UPDATE scheduled_issues SET last_error = ?, updated_at_ms = ? WHERE id = ?',
                    (str(e), ts, row['id']),
                )
                self.db.commit()
                failed_items.append({'scheduled_issue_id': row['id'], 'error': str(e), 'run_id': run_id})
        return {'created': created_items, 'failed': failed_items, 'total_due': len(rows)}

    def run_scheduled_issue_now(self, *, scheduled_issue_id: str) -> dict[str, Any]:
        row = self.db.get_one('SELECT * FROM scheduled_issues WHERE id = ?', (scheduled_issue_id,))
        return self._create_issue_from_schedule(row, manual=True)

    def set_scheduled_issue_enabled(self, *, scheduled_issue_id: str, enabled: bool) -> dict[str, Any]:
        row = self.db.get_one('SELECT * FROM scheduled_issues WHERE id = ?', (scheduled_issue_id,))
        schedule_config = parse_schedule_config(row['schedule_config_json'])
        next_run = compute_next_scheduled_run(
            schedule_kind=str(row['schedule_kind']),
            schedule_config=schedule_config,
            now_dt=datetime.now(UTC),
            last_run_at_ms=row['last_run_at_ms'],
        ) if enabled else None
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE scheduled_issues SET enabled = ?, next_run_at_ms = ?, updated_at_ms = ? WHERE id = ?',
            (1 if enabled else 0, int(next_run.timestamp() * 1000) if next_run else None, ts, scheduled_issue_id),
        )
        self.db.commit()
        return {
            'scheduled_issue_id': scheduled_issue_id,
            'enabled': enabled,
            'next_run_at_ms': int(next_run.timestamp() * 1000) if next_run else None,
        }

    def _record_checkpoint(
        self,
        *,
        issue_id: str,
        attempt_id: str,
        kind: str,
        summary: str,
        details_md: str,
        next_action: str,
        created_by_employee_id: str | None,
        percent_complete: int | None,
    ) -> None:
        checkpoint_id = uid('ckpt')
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(checkpoint_no), 0) + 1 FROM issue_checkpoints WHERE attempt_id = ?',
            (attempt_id,),
        ).fetchone()
        checkpoint_no = int(row[0])
        self.db.conn.execute(
            '''INSERT INTO issue_checkpoints (
                id, issue_id, attempt_id, checkpoint_no, kind, summary, details_md,
                next_action, percent_complete, created_by_employee_id, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                checkpoint_id,
                issue_id,
                attempt_id,
                checkpoint_no,
                kind,
                summary,
                details_md,
                next_action,
                percent_complete,
                created_by_employee_id,
                now_ms(),
            ),
        )


def demo() -> None:
    service = AgentTeamService()
    try:
        result = service.list_issues(project_key='agent-team-core')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        service.close()


if __name__ == '__main__':
    demo()
