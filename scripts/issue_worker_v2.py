#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))

from services.agent_team_service import AgentTeamService, WAITING_HUMAN_STATUSES  # noqa: E402
from services.activity import record_issue_activity  # noqa: E402
from services.workflow_control import load_control  # noqa: E402

STATE_DIR = ROOT / 'state'
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_REGISTRY_PATH = STATE_DIR / 'session_registry.json'
REPORT_PATH = STATE_DIR / 'worker_report.json'
ACTIONS_PATH = STATE_DIR / 'worker_actions.jsonl'
ISSUE_CONTEXT_DIR = STATE_DIR / 'issue_context'
ISSUE_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_BOARD = ROOT / 'scripts' / 'export_board_snapshot.py'
EXPORT_ISSUES = ROOT / 'scripts' / 'export_issue_details.py'
DISPATCH_OBSERVER = ROOT / 'scripts' / 'dispatch_observer_v1.py'

MAX_DISPATCH_PER_RUN = 3
MAX_OBSERVE_PER_RUN = 6
OBSERVE_TIMEOUT_SECONDS = 8
STALE_ATTEMPT_SECONDS = 30 * 60
DISPATCH_TIMEOUT_MS = 30 * 60 * 1000

ROLE_LABELS = {
    'pm': 'PM',
    'dev': 'Dev',
    'qa': 'QA',
    'ops': 'Ops',
    'ceo': 'CEO',
}


def now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.UTC).isoformat().replace('+00:00', 'Z')


def parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def compact_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    text = re.sub(r'\n{3,}', '\n\n', text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + '...'



def normalize_handoff_payload(payload: dict[str, Any], *, marker: str, fallback_next_role: str | None = None) -> dict[str, Any]:
    out = dict(payload) if isinstance(payload, dict) else {}
    out['marker'] = str(out.get('marker') or marker)
    out['status'] = str(out.get('status') or 'done')
    out['suggested_next_role'] = str(out.get('suggested_next_role') or fallback_next_role or 'close')
    out['reason'] = str(out.get('reason') or 'role task complete')
    out['risk_level'] = str(out.get('risk_level') or 'normal')
    out['needs_human'] = bool(out.get('needs_human', False))
    out['summary'] = str(out.get('summary') or out['reason'])
    artifacts = out.get('artifacts')
    out['artifacts'] = artifacts if isinstance(artifacts, list) else []
    findings = out.get('blocking_findings')
    out['blocking_findings'] = findings if isinstance(findings, list) else []
    return out


def latest_success_handoff(svc: AgentTeamService, issue_id: str, exclude_attempt_id: str | None = None) -> dict[str, Any]:
    rows = svc.db.fetch_all(
        '''SELECT id, attempt_no, result_summary, ended_at_ms, input_snapshot_json, output_snapshot_json
           FROM issue_attempts
           WHERE issue_id = ? AND status = 'succeeded'
           ORDER BY attempt_no DESC''',
        (issue_id,),
    )
    for row in rows:
        if exclude_attempt_id and row['id'] == exclude_attempt_id:
            continue
        input_snapshot = parse_json(row['input_snapshot_json'])
        payload = parse_json(row['output_snapshot_json'])
        wait_result = payload.get('wait_result') if isinstance(payload.get('wait_result'), dict) else {}
        handoff = wait_result.get('payload') if isinstance(wait_result.get('payload'), dict) else {}
        if not handoff and isinstance(payload.get('payload'), dict):
            handoff = payload.get('payload')
        if handoff:
            enriched = dict(handoff)
            from_role = str(input_snapshot.get('attempt_role') or '').strip()
            if from_role and not enriched.get('from_role'):
                enriched['from_role'] = from_role
            enriched.setdefault('from_attempt_no', row['attempt_no'])
            if row['result_summary'] and not enriched.get('from_result_summary'):
                enriched['from_result_summary'] = row['result_summary']
            if row['ended_at_ms'] and not enriched.get('from_ended_at_ms'):
                enriched['from_ended_at_ms'] = row['ended_at_ms']
            previous_instruction = compact_text(input_snapshot.get('worker_instruction'), limit=900)
            if previous_instruction and not enriched.get('from_worker_instruction'):
                enriched['from_worker_instruction'] = previous_instruction
            return enriched
    return {}


def write_issue_context_snapshot(svc: AgentTeamService, issue_id: str) -> str:
    detail = svc.get_issue(issue_id=issue_id)
    snapshot = {
        'schema_version': 'agent-team.issue-context.v1',
        'generated_at': now_iso(),
        'issue_id': issue_id,
        'issue': detail.get('issue') or {},
        'attempts': detail.get('attempts') or [],
        'callbacks_by_attempt': detail.get('callbacks_by_attempt') or {},
        'dependencies': detail.get('dependencies') or {'blocking': [], 'blocked_dependents': []},
        'activities': svc.get_issue_activity(issue_id=issue_id).get('items') or [],
    }
    path = ISSUE_CONTEXT_DIR / f'{issue_id}.json'
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)



def latest_attempt_context(svc: AgentTeamService, issue_id: str) -> dict[str, Any]:
    row = svc.db.get_one(
        '''SELECT attempt_no, status, failure_summary, result_summary, input_snapshot_json, output_snapshot_json, completion_mode, updated_at_ms, ended_at_ms
           FROM issue_attempts
           WHERE issue_id = ?
           ORDER BY attempt_no DESC
           LIMIT 1''',
        (issue_id,),
    )
    input_snapshot = parse_json(row['input_snapshot_json'])
    output_snapshot = parse_json(row['output_snapshot_json'])
    wait_result = output_snapshot.get('wait_result') if isinstance(output_snapshot.get('wait_result'), dict) else {}
    wait_payload = wait_result.get('payload') if isinstance(wait_result.get('payload'), dict) else {}
    payload = output_snapshot.get('payload') if isinstance(output_snapshot.get('payload'), dict) else {}
    return {
        'attempt_no': row['attempt_no'],
        'status': row['status'],
        'failure_summary': row['failure_summary'],
        'result_summary': row['result_summary'],
        'completion_mode': row['completion_mode'],
        'updated_at_ms': row['updated_at_ms'],
        'ended_at_ms': row['ended_at_ms'],
        'attempt_role': input_snapshot.get('attempt_role'),
        'worker_instruction': input_snapshot.get('worker_instruction'),
        'wait_payload': wait_payload,
        'payload': payload,
    }


def should_skip_same_role_redispatch(issue: dict[str, Any], last_attempt_ctx: dict[str, Any]) -> tuple[bool, str]:
    if not last_attempt_ctx:
        return False, ''
    if str(issue.get('status') or '') != 'review':
        return False, ''
    if str(last_attempt_ctx.get('status') or '') != 'succeeded':
        return False, ''
    issue_role = str(issue.get('role') or '')
    last_role = str(last_attempt_ctx.get('attempt_role') or '')
    if not issue_role or issue_role != last_role:
        return False, ''

    terminal_payload = last_attempt_ctx.get('wait_payload') if isinstance(last_attempt_ctx.get('wait_payload'), dict) and last_attempt_ctx.get('wait_payload') else None
    if terminal_payload is None and isinstance(last_attempt_ctx.get('payload'), dict):
        terminal_payload = last_attempt_ctx.get('payload')
    if isinstance(terminal_payload, dict):
        suggested_next = str(terminal_payload.get('suggested_next_role') or '').strip()
        needs_human = bool(terminal_payload.get('needs_human')) or str(terminal_payload.get('status') or '').strip() == 'needs_human'
        if needs_human:
            return False, ''
        if suggested_next and suggested_next != issue_role:
            return False, ''

    issue_updated = int(issue.get('updated_at_ms') or 0)
    last_updated = int(last_attempt_ctx.get('updated_at_ms') or 0)
    if issue_updated > last_updated:
        return False, ''
    return True, 'same_role_succeeded_attempt_already_exists_without_new_issue_update'


def pending_terminal_handoff(last_attempt_ctx: dict[str, Any], *, current_role: str | None) -> dict[str, Any]:
    if str(last_attempt_ctx.get('status') or '') != 'succeeded':
        return {}
    payload = last_attempt_ctx.get('wait_payload') if isinstance(last_attempt_ctx.get('wait_payload'), dict) and last_attempt_ctx.get('wait_payload') else None
    if payload is None and isinstance(last_attempt_ctx.get('payload'), dict):
        payload = last_attempt_ctx.get('payload')
    if not isinstance(payload, dict):
        return {}
    suggested_next = str(payload.get('suggested_next_role') or '').strip()
    if not suggested_next:
        return {}
    if suggested_next == str(current_role or ''):
        return {}
    return payload



def load_session_registry() -> dict[str, Any]:
    if not SESSION_REGISTRY_PATH.exists():
        return {}
    return json.loads(SESSION_REGISTRY_PATH.read_text(encoding='utf-8'))


def canonical_session_key(*, project_key: str, role: str) -> str:
    registry = load_session_registry()
    project_scope = 'shared' if role == 'ceo' else project_key
    agent_id = f'agent-team-{role}'
    reg_key = f'{agent_id}|{project_scope}'
    entry = registry.get(reg_key) or {}
    current = entry.get('current_session_key')
    if isinstance(current, str) and current.strip():
        return current.strip()
    if role == 'ceo':
        return 'agent:agent-team-ceo:shared'
    return f'agent:agent-team-{role}:project:{project_key}'


def append_action(payload: dict[str, Any]) -> None:
    with ACTIONS_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


def refresh_exports() -> None:
    subprocess.run(['python3', str(EXPORT_BOARD)], check=True, capture_output=True, text=True)
    subprocess.run(['python3', str(EXPORT_ISSUES)], check=True, capture_output=True, text=True)


def drain_dispatch_lifecycle_events() -> dict[str, Any]:
    res = subprocess.run(
        ['python3', str(DISPATCH_OBSERVER)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip() or 'dispatch observer failed')
    path = (res.stdout or '').strip().splitlines()[-1].strip()
    if not path:
        raise RuntimeError('dispatch observer returned no report path')
    report_path = Path(path)
    if not report_path.exists():
        raise RuntimeError(f'dispatch observer report missing: {report_path}')
    return json.loads(report_path.read_text(encoding='utf-8'))


def extract_expected_text(payload: dict[str, Any]) -> str | None:
    expected = payload.get('expected_text')
    if isinstance(expected, str) and expected.strip():
        return expected.strip()
    prompt = payload.get('prompt')
    if isinstance(prompt, str):
        m = re.search(r'exactly\s+([^\n]+?)\s+and\s+nothing\s+else', prompt, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('.').strip('"').strip("'")
    return None


def extract_expected_marker(payload: dict[str, Any]) -> str | None:
    marker = payload.get('marker')
    if isinstance(marker, str) and marker.strip():
        return marker.strip()
    return None


def extract_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(extract_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get('text'), str):
            return value['text']
        if 'content' in value:
            return extract_text(value.get('content'))
        if 'message' in value:
            return extract_text(value.get('message'))
        return ''
    return str(value)


def parse_timestamp_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            if raw.endswith('Z'):
                raw = raw[:-1] + '+00:00'
            return int(datetime.fromisoformat(raw).timestamp() * 1000)
        except Exception:
            return None
    return None


def parse_event_timestamp_ms(event: dict[str, Any]) -> int | None:
    ts = parse_timestamp_ms(event.get('timestamp'))
    if ts is not None:
        return ts
    data = event.get('data') if isinstance(event.get('data'), dict) else {}
    ts = parse_timestamp_ms(data.get('timestamp'))
    if ts is not None:
        return ts
    message = event.get('message') if isinstance(event.get('message'), dict) else {}
    return parse_timestamp_ms(message.get('timestamp'))


def resolve_session_record(session_key: str) -> dict[str, Any]:
    parts = session_key.split(':')
    if len(parts) < 2 or parts[0] != 'agent':
        return {}
    agent_id = parts[1]
    sessions_path = Path('/root/.openclaw/agents') / agent_id / 'sessions' / 'sessions.json'
    if not sessions_path.exists():
        return {}
    try:
        data = json.loads(sessions_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    entry = data.get(session_key)
    return entry if isinstance(entry, dict) else {}


def load_recent_session_events(session_file: str, *, max_lines: int = 400) -> list[dict[str, Any]]:
    path = Path(session_file)
    if not path.exists():
        return []
    data = b''
    lines: list[bytes] = []
    with path.open('rb') as f:
        f.seek(0, 2)
        remaining = f.tell()
        while remaining > 0 and len(lines) <= max_lines:
            chunk = min(16384, remaining)
            remaining -= chunk
            f.seek(remaining)
            data = f.read(chunk) + data
            lines = data.splitlines()
    out: list[dict[str, Any]] = []
    for raw in lines[-max_lines:]:
        try:
            item = json.loads(raw.decode('utf-8', errors='replace'))
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def has_recent_runtime_activity(*, session_key: str, since_ms: int, lookback_seconds: int = 300, marker: str | None = None, dispatch_ref: str | None = None, session_id: str | None = None, session_file: str | None = None) -> tuple[bool, dict[str, Any]]:
    info: dict[str, Any] = {
        'session_key': session_key,
        'since_ms': since_ms,
        'lookback_seconds': lookback_seconds,
        'marker': marker,
        'dispatch_ref': dispatch_ref,
        'session_id': session_id,
        'session_file': session_file,
    }
    now_ms_local = int(time.time() * 1000)
    threshold_ms = now_ms_local - lookback_seconds * 1000
    session_record = resolve_session_record(session_key) if session_key else {}
    if session_record:
        info['registry_session_id'] = session_record.get('sessionId')
        info['registry_session_file'] = session_record.get('sessionFile')
        info['session_updated_at'] = session_record.get('updatedAt')
    effective_session_file = session_file or (session_record.get('sessionFile') if isinstance(session_record.get('sessionFile'), str) else '')

    if effective_session_file:
        events = load_recent_session_events(effective_session_file)
        anchor_ts = None
        anchor_source = None
        for event in events:
            ts = parse_event_timestamp_ms(event)
            if ts is None or ts < since_ms:
                continue
            data = event.get('data') if isinstance(event.get('data'), dict) else {}
            msg = event.get('message') if isinstance(event.get('message'), dict) else {}
            role = msg.get('role')
            text = extract_text(msg.get('content'))
            if dispatch_ref and isinstance(data.get('runId'), str) and data.get('runId') == dispatch_ref:
                if anchor_ts is None or ts < anchor_ts:
                    anchor_ts = ts
                    anchor_source = 'runId'
            if marker and event.get('type') == 'message' and role == 'user' and marker in text:
                if anchor_ts is None or ts < anchor_ts:
                    anchor_ts = ts
                    anchor_source = 'marker'

        newest_ts = None
        newest_kind = None
        newest_text = None
        activity_count = 0
        related_run_event_count = 0
        superseded_by_new_user = False
        for event in events:
            ts = parse_event_timestamp_ms(event)
            if ts is None or ts < since_ms or ts < threshold_ms:
                continue
            if anchor_ts is not None and ts < anchor_ts:
                continue
            event_type = str(event.get('type') or '')
            data = event.get('data') if isinstance(event.get('data'), dict) else {}
            msg = event.get('message') if isinstance(event.get('message'), dict) else {}
            role = str(msg.get('role') or '')
            text = extract_text(msg.get('content'))
            custom_type = str(event.get('customType') or '')
            if anchor_ts is not None and event_type == 'message' and role == 'user' and (not marker or marker not in text):
                superseded_by_new_user = True
                break
            relevant = False
            kind = None
            snippet = ''
            if dispatch_ref and isinstance(data.get('runId'), str) and data.get('runId') == dispatch_ref:
                relevant = True
                kind = f'custom:{custom_type or event_type}'
                snippet = extract_text(data)[:200]
                related_run_event_count += 1
            elif event_type == 'message' and role in {'assistant', 'toolResult'}:
                relevant = True
                kind = f'message:{role}'
                snippet = text[:200]
            elif event_type == 'custom' and custom_type in {'openclaw:prompt-error', 'openclaw:bootstrap-context:full'}:
                relevant = True
                kind = f'custom:{custom_type}'
                snippet = extract_text(data)[:200]
            if not relevant:
                continue
            activity_count += 1
            if newest_ts is None or ts > newest_ts:
                newest_ts = ts
                newest_kind = kind
                newest_text = snippet

        info.update({
            'source': 'session_file',
            'anchor_ts': anchor_ts,
            'anchor_source': anchor_source,
            'newest_ts': newest_ts,
            'newest_kind': newest_kind,
            'newest_text': newest_text,
            'activity_count': activity_count,
            'related_run_event_count': related_run_event_count,
            'superseded_by_new_user': superseded_by_new_user,
        })
        return bool(newest_ts is not None) and not superseded_by_new_user, info

    try:
        cmd = [
            'openclaw', 'gateway', 'call', 'sessions.get',
            '--json',
            '--timeout', '20000',
            '--params', json.dumps({'sessionKey': session_key, 'limit': 50}, ensure_ascii=False),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return False, {**info, 'check_error': res.stderr.strip() or res.stdout.strip()}
        data = json.loads(res.stdout)
        messages = data.get('messages') or []
        anchor_ts = None
        for message in messages:
            ts = message.get('timestamp')
            if not isinstance(ts, (int, float)):
                continue
            ts = int(ts)
            if ts < since_ms:
                continue
            role = message.get('role')
            text = extract_text(message.get('content'))
            if marker and role == 'user' and marker in text:
                if anchor_ts is None or ts < anchor_ts:
                    anchor_ts = ts
        newest_ts = None
        newest_role = None
        newest_text = None
        activity_count = 0
        superseded_by_new_user = False
        for message in messages:
            ts = message.get('timestamp')
            if not isinstance(ts, (int, float)):
                continue
            ts = int(ts)
            if ts < since_ms or ts < threshold_ms:
                continue
            if anchor_ts is not None and ts < anchor_ts:
                continue
            role = message.get('role')
            text = extract_text(message.get('content'))
            if anchor_ts is not None and role == 'user' and (not marker or marker not in text):
                superseded_by_new_user = True
                break
            if role not in {'assistant', 'tool'}:
                continue
            activity_count += 1
            if newest_ts is None or ts > newest_ts:
                newest_ts = ts
                newest_role = role
                newest_text = text[:200]
        info.update({
            'source': 'gateway_sessions_get',
            'anchor_ts': anchor_ts,
            'newest_ts': newest_ts,
            'newest_role': newest_role,
            'newest_text': newest_text,
            'activity_count': activity_count,
            'superseded_by_new_user': superseded_by_new_user,
        })
        return bool(newest_ts is not None) and not superseded_by_new_user, info
    except Exception as e:
        return False, {**info, 'check_error': str(e)}


def build_worker_payload(issue: dict[str, Any], last_attempt_payload: dict[str, Any], *, session_key: str, issue_context_path: str | None = None) -> dict[str, Any]:
    metadata = parse_json(issue.get('metadata_json'))
    role = issue.get('role') or 'agent'
    role_label = ROLE_LABELS.get(role, role.upper())
    marker = f"AUTO_DONE_{issue['issue_no']}_{uuid.uuid4().hex[:8]}"
    flow_id = f"flow_{uuid.uuid4().hex[:12]}"
    callback_token = f"cbtok_{uuid.uuid4().hex[:12]}"

    role_worker_instruction = metadata.get(f'worker_instruction_{role}')
    role_dispatch_instruction = metadata.get(f'dispatch_instruction_{role}')
    current_worker_instruction = metadata.get('worker_instruction')
    current_dispatch_instruction = metadata.get('dispatch_instruction')
    last_attempt_role = last_attempt_payload.get('attempt_role') if isinstance(last_attempt_payload.get('attempt_role'), str) else None
    reuse_last_instruction = last_attempt_role == role
    explicit_instruction = None
    for candidate in (
        role_dispatch_instruction,
        role_worker_instruction,
        current_dispatch_instruction,
        current_worker_instruction,
        metadata.get('prompt'),
        last_attempt_payload.get('worker_instruction') if (not current_worker_instruction and reuse_last_instruction) else None,
        last_attempt_payload.get('prompt') if (not current_dispatch_instruction and reuse_last_instruction) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            explicit_instruction = candidate.strip()
            break

    description = issue.get('description_md') or 'No description.'
    acceptance = issue.get('acceptance_criteria_md') or 'No explicit acceptance criteria.'
    blocker = issue.get('blocker_summary') or 'None.'
    required_input = issue.get('required_human_input') or 'None.'
    project_name = str(issue.get('project_name') or issue.get('project_key') or '未知项目')
    project_description = str(issue.get('project_description') or metadata.get('project_context_md') or '').strip()
    issue_context_block = (
        f"请以 {role_label} 角色处理这个 issue。\n"
        f"项目：{project_name} ({issue.get('project_key') or ''})\n"
        + (f"项目背景：{project_description}\n" if project_description else '')
        + f"标题：{issue['title']}\n"
        + f"描述：{description}\n"
        + f"验收标准：{acceptance}\n"
        + f"当前阻塞：{blocker}\n"
        + f"当前缺失的人类输入：{required_input}\n"
    )
    base_instruction = (
        issue_context_block
        + (f"附加要求：{explicit_instruction}\n" if explicit_instruction else '')
        + "真实代码、脚本、文档、验证请使用规范实现仓库 /root/.openclaw/workspace-agent-team（角色工作区里可用 ./repo 快捷入口）。\n"
        + "只做当前角色最小必要且正确的工作，不要越过本角色职责边界。"
    )

    issue_context_summary = ''
    if issue_context_path:
        issue_context_summary = (
            "完整上下文文件：\n"
            f"- JSON 文件路径：{issue_context_path}\n"
            "- 文件中包含当前 issue 状态、历史 attempts、callbacks、activities、dependencies。\n"
            "- 如果你需要完整了解上一个角色做了什么、当前为什么停在这里、还依赖什么，请先阅读这个 JSON。\n\n"
        )

    prior_handoff = metadata.get('prior_handoff') if isinstance(metadata.get('prior_handoff'), dict) else {}
    prior_summary = ''
    if prior_handoff:
        artifacts_text = compact_text(json.dumps(prior_handoff.get('artifacts') or [], ensure_ascii=False), limit=500) if isinstance(prior_handoff.get('artifacts'), list) and prior_handoff.get('artifacts') else ''
        findings_text = compact_text(json.dumps(prior_handoff.get('blocking_findings') or [], ensure_ascii=False), limit=500) if isinstance(prior_handoff.get('blocking_findings'), list) and prior_handoff.get('blocking_findings') else ''
        prior_summary = (
            "上一角色交接（这是下游继续推进的直接依据，不要忽略）：\n"
            f"- from_role: {prior_handoff.get('from_role') or ''}\n"
            f"- from_attempt_no: {prior_handoff.get('from_attempt_no') or ''}\n"
            f"- summary: {prior_handoff.get('summary') or prior_handoff.get('reason') or ''}\n"
            f"- reason: {prior_handoff.get('reason') or ''}\n"
            f"- suggested_next_role: {prior_handoff.get('suggested_next_role') or ''}\n"
            + (f"- artifacts: {artifacts_text}\n" if artifacts_text else '')
            + (f"- blocking_findings: {findings_text}\n" if findings_text else '')
            + (f"- previous_role_instruction: {compact_text(prior_handoff.get('from_worker_instruction'), limit=700)}\n" if prior_handoff.get('from_worker_instruction') else '')
            + "\n"
        )

    previous_role_context = ''
    previous_role = str(last_attempt_payload.get('attempt_role') or '').strip()
    if previous_role and previous_role != role:
        previous_instruction = compact_text(last_attempt_payload.get('worker_instruction'), limit=1000)
        if previous_instruction:
            previous_role_context = (
                "上一角色收到的任务上下文摘要（帮助你理解这次交接是从什么背景来的）：\n"
                f"- from_role: {previous_role}\n"
                f"{previous_instruction}\n\n"
            )

    human_context = metadata.get('human_context') if isinstance(metadata.get('human_context'), dict) else {}
    human_summary = ''
    if human_context:
        human_summary = (
            "人工最新反馈（必须显式处理，不要忽略）：\n"
            f"- resolution: {human_context.get('resolution') or ''}\n"
            f"- note: {human_context.get('note') or ''}\n"
            f"- next_role: {human_context.get('next_role') or ''}\n"
            f"- next_employee_key: {human_context.get('next_employee_key') or ''}\n\n"
        )

    retry_context = metadata.get('retry_context') if isinstance(metadata.get('retry_context'), dict) else {}
    retry_summary = ''
    if retry_context and retry_context.get('attempt_role') == role:
        retry_summary = (
            "重试上下文：\n"
            f"- previous attempt_no: {retry_context.get('attempt_no') or ''}\n"
            f"- previous status: {retry_context.get('status') or ''}\n"
            f"- previous failure_summary: {retry_context.get('failure_summary') or ''}\n"
            + (f"- previous completion_mode: {retry_context.get('completion_mode')}\n" if retry_context.get('completion_mode') else '')
            + "- 尽量延续已有工作，不要把它当成全新 issue\n\n"
        )

    recovery_summary = ''
    if retry_context and retry_context.get('completion_mode') == 'system_chat_final':
        recovery_summary = (
            "补偿提示：\n"
            "- 系统观察到上一轮 run 已结束，但没有收到 terminal callback，也没有观察到最终 JSON / marker。\n"
            "- 本轮请基于上一轮已完成的工作做一次显式收口。\n"
            "- 如果任务已完成，请立即补发 terminal callback，并返回最终 JSON。\n"
            "- 如果任务未完成，请明确说明阻塞原因，并给出 suggested_next_role。\n\n"
        )

    existing_artifacts = metadata.get('artifacts') if isinstance(metadata.get('artifacts'), dict) else {}
    existing_feishu_docs = existing_artifacts.get('feishu_docs') if isinstance(existing_artifacts.get('feishu_docs'), list) else []
    artifact_summary = ''
    if existing_feishu_docs:
        lines = []
        seen_refs: set[str] = set()
        for item in existing_feishu_docs:
            if not isinstance(item, dict):
                continue
            ref = str(item.get('doc_url') or item.get('doc_token') or '').strip()
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            lines.append(
                f"- 文档: {ref}"
                + (f" | summary: {item.get('summary')}" if item.get('summary') else '')
            )
            if len(lines) >= 5:
                break
        if lines:
            artifact_summary = (
                "已有中间产出（不可跳过，必须显式处理）：\n"
                + "\n".join(lines)
                + "\n- 你必须基于这些已有中间产出进行本角色的一轮显式判断与回复。\n"
                + "- 不要因为已有产物就省略本角色对话；请明确判断是继续推进、收尾关闭，还是提出阻塞。\n\n"
            )

    role_boundary_rules = {
        'ceo': '你是治理与分派角色，默认不要亲自调研、实现、测试、部署。优先做判断、分派、升级、关闭。',
        'pm': '你负责需求澄清、任务拆分、路由与组织，不负责主体实现。',
        'dev': '你负责实现与最小技术验证，不负责治理拍板和业务定调。',
        'qa': '你负责验收、验证、找风险，不负责主体实现。',
        'ops': '你负责部署、环境、运行态与发布保障，不负责需求定义与业务验收。',
    }
    decomposition_strategy = ''
    if role in {'ceo', 'pm'}:
        decomposition_strategy = (
            "拆分策略（CEO / PM 必须优先考虑，而不是默认一个 issue 流转到底）：\n"
            "1. 如果当前 issue 同时包含多个阶段、多个交付物、跨角色依赖、或者能拆成多个独立验收目标，优先拆成多个小 issue。\n"
            "2. 优先把大 issue 变成父 issue / 编排 issue，再创建多个子 issue，并通过 `parent_of` / `blocked_by` 表达顺序和依赖。\n"
            "3. 需求澄清、实现、部署、验收、补文档、后续治理，只要能独立验收，就尽量拆开，不要硬塞进一个 issue。\n"
            "4. 如果你判断应该拆分，请优先一次创建多个 proposal，而不是只给下游一句模糊 handoff。\n\n"
        )

    prompt = (
        f"你现在以 Agent Team 的 {role_label} 角色工作。Issue #{issue['issue_no']}。\n\n"
        f"{issue_context_summary}"
        f"{prior_summary}"
        f"{previous_role_context}"
        f"{human_summary}"
        f"{retry_summary}"
        f"{recovery_summary}"
        f"{artifact_summary}"
        f"角色边界：{role_boundary_rules.get(role, '请只做当前角色边界内的工作。')}\n\n"
        f"{decomposition_strategy}"
        f"任务：\n{base_instruction}\n\n"
        "要求：\n"
        "1. 只做当前角色最小必要的工作。\n"
        "2. 不要做无关探索。\n"
        "3. 即使已有中间产出，也必须做本角色的一轮显式判断，不要静默跳过。\n"
        "4. 如果验收已满足，请明确给出关闭或继续流转建议。\n"
        "5. 如果创建了飞书文档等外部产物，请先记录 artifact callback。\n"
        "6. 如需创建新的独立 issue，只能使用 skill `agent-team-issue-authoring`，并通过唯一入口 `python3 /root/.openclaw/workspace-agent-team/scripts/attempt_callback_helper.py create-issues --attempt-id <attempt_id> --callback-token <callback_token> --created-by-role <role> --proposals-json \"[...]\"`。不要在最终 JSON 中夹带 issue proposal。\n"
        "7. `--proposals-json` 必须传 JSON 数组，即使只创建 1 个 issue 也要传数组；每个 proposal 都应包含稳定的 `proposal_key` 用于去重。\n"
        "8. proposal 推荐字段：proposal_key, title, description_md, acceptance_criteria_md, priority, route_role, relation_type(parent_of|blocked_by|related_to), metadata。\n"
        "9. 如果你是 CEO 或 PM，且判断这是一个大 issue，请优先拆成多个小 issue，并让当前 issue 负责编排 / 等待子 issue 收口。\n"
        "10. 最终回复必须是单个 JSON 对象，不要带 markdown、代码块或额外说明。\n\n"
        "最终 JSON 只需要包含这些字段：\n"
        f"marker={marker}\n"
        "status, summary, artifacts, blocking_findings, suggested_next_role, reason, risk_level, needs_human\n\n"
        "系统会在消息末尾追加本轮可直接执行的 callback 命令，请按追加后的具体命令调用。"
    )

    return {
        'prompt': prompt,
        'marker': marker,
        'expected_text': marker,
        'session_key': session_key,
        'worker_instruction': base_instruction,
        'attempt_role': role,
        'generated_by': 'issue_worker_v2',
        'flow_id': flow_id,
        'callback_token': callback_token,
        'timeout_deadline_ms': int(time.time() * 1000) + DISPATCH_TIMEOUT_MS,
        'issue_context_path': issue_context_path,
    }


def fetch_ready_candidates(svc: AgentTeamService) -> list[dict[str, Any]]:
    rows = svc.db.fetch_all(
        '''SELECT i.id AS issue_id,
                  i.issue_no,
                  i.status,
                  i.title,
                  i.description_md,
                  i.acceptance_criteria_md,
                  i.blocker_summary,
                  i.required_human_input,
                  i.metadata_json,
                  ei.employee_key,
                  ei.id AS assigned_employee_id,
                  rt.template_key AS role,
                  rb.binding_key,
                  rb.session_key,
                  rb.agent_id,
                  p.project_key,
                  p.name AS project_name,
                  p.description AS project_description,
                  EXISTS (
                    SELECT 1
                    FROM issue_relations ir
                    JOIN issues dep ON dep.id = ir.to_issue_id
                    WHERE ir.from_issue_id = i.id AND ir.relation_type = 'blocked_by' AND dep.status != 'closed'
                  ) AS has_open_dependencies,
                  EXISTS (
                    SELECT 1
                    FROM issue_attempts ia2
                    LEFT JOIN runtime_bindings rb2 ON rb2.id = ia2.runtime_binding_id
                    WHERE ia2.status IN ('dispatching','running')
                      AND rb2.agent_id = rb.agent_id
                      AND ia2.issue_id != i.id
                  ) AS agent_has_active_issue
           FROM issues i
           LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
           LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
           LEFT JOIN projects p ON p.id = ei.project_id
           WHERE i.status IN ('triaged', 'ready', 'review', 'waiting_recovery_completion', 'waiting_children')
             AND NOT EXISTS (SELECT 1 FROM issue_attempts ia WHERE ia.issue_id = i.id AND ia.status IN ('dispatching','running'))
           ORDER BY CASE i.priority
                      WHEN 'p0' THEN 0
                      WHEN 'p1' THEN 1
                      WHEN 'p2' THEN 2
                      WHEN 'p3' THEN 3
                      WHEN 'p4' THEN 4
                      ELSE 9
                    END ASC,
                    i.updated_at_ms ASC,
                    i.issue_no ASC'''
    )
    return [dict(r) for r in rows]


def fetch_dispatching_candidates(svc: AgentTeamService) -> list[dict[str, Any]]:
    rows = svc.db.fetch_all(
        '''SELECT ia.id AS attempt_id,
                  ia.issue_id,
                  ia.attempt_no,
                  ia.status AS attempt_status,
                  ia.dispatch_ref,
                  ia.input_snapshot_json,
                  ia.created_at_ms,
                  ia.updated_at_ms,
                  ia.runtime_session_key,
                  ia.runtime_session_id,
                  ia.runtime_session_file,
                  i.issue_no,
                  i.title,
                  i.status AS issue_status,
                  rt.template_key AS role,
                  rb.binding_key
           FROM issue_attempts ia
           JOIN issues i ON i.id = ia.issue_id
           LEFT JOIN runtime_bindings rb ON rb.id = ia.runtime_binding_id
           LEFT JOIN employee_instances ei ON ei.id = ia.assigned_employee_id
           LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
           WHERE ia.status IN ('dispatching', 'running')
             AND ia.attempt_no = (
               SELECT MAX(ia2.attempt_no)
               FROM issue_attempts ia2
               WHERE ia2.issue_id = ia.issue_id AND ia2.status IN ('dispatching', 'running')
             )
           ORDER BY ia.updated_at_ms ASC'''
    )
    return [dict(r) for r in rows]


def fetch_issue_context(svc: AgentTeamService, issue_id: str) -> dict[str, Any]:
    row = svc.db.get_one(
        '''SELECT i.id AS issue_id,
                  i.issue_no,
                  i.status,
                  i.title,
                  i.metadata_json,
                  p.project_key,
                  ei.employee_key AS assigned_employee_key,
                  rt.template_key AS assigned_role
           FROM issues i
           JOIN projects p ON p.id = i.project_id
           LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
           LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
           WHERE i.id = ?''',
        (issue_id,),
    )
    return dict(row)


def pick_target_employee_key(svc: AgentTeamService, *, project_key: str, role: str) -> str | None:
    if role == 'ceo':
        row = svc.db.conn.execute(
            '''SELECT ei.employee_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE rt.template_key = 'ceo'
               ORDER BY ei.employee_key ASC
               LIMIT 1'''
        ).fetchone()
        return row[0] if row else None
    row = svc.db.conn.execute(
        '''SELECT ei.employee_key
           FROM employee_instances ei
           JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN projects p ON p.id = ei.project_id
           WHERE rt.template_key = ? AND p.project_key = ?
           ORDER BY ei.employee_key ASC
           LIMIT 1''',
        (role, project_key),
    ).fetchone()
    return row[0] if row else None



def decide_next_role(*, current_role: str | None, metadata: dict[str, Any]) -> str | None:
    if bool(metadata.get('needs_human')):
        return 'human_queue'

    suggested = metadata.get('suggested_next_role')
    if isinstance(suggested, str) and suggested.strip():
        return suggested.strip()

    issue_type = str(metadata.get('issue_type') or 'normal')
    risk_level = str(metadata.get('risk_level') or 'normal')
    prior_handoff = metadata.get('prior_handoff') if isinstance(metadata.get('prior_handoff'), dict) else {}
    summary_text = ' '.join(
        str(x or '') for x in [
            prior_handoff.get('summary'),
            prior_handoff.get('reason'),
            metadata.get('dispatch_instruction'),
            metadata.get(f'worker_instruction_{current_role}') if current_role else '',
        ]
    )
    requires_ops = bool(metadata.get('requires_ops')) or issue_type in {'production_change', 'release'}
    if not requires_ops and any(token in summary_text.lower() for token in ['ops', 'deploy', 'deployment', 'nginx', 'systemd']):
        requires_ops = True
    if not requires_ops and any(token in summary_text for token in ['部署', '发布', '运行态', '环境', 'nginx', '外网访问']):
        requires_ops = True

    if current_role == 'pm':
        return 'dev'
    if current_role == 'dev':
        return 'ops' if requires_ops else 'qa'
    if current_role == 'qa':
        if requires_ops:
            return 'ops'
        return 'ceo' if risk_level in {'normal', 'high'} else 'ceo'
    if current_role == 'ops':
        return 'ceo'
    if current_role == 'ceo':
        return 'close'
    return None


def enqueue_route_failure_compensation(
    svc: AgentTeamService,
    *,
    issue_id: str,
    from_role: str | None,
    next_role: str | None,
    reason: str,
    missing_target: bool = False,
) -> dict[str, Any]:
    from_label = ROLE_LABELS.get(str(from_role or ''), str(from_role or '未知角色').upper())
    to_label = ROLE_LABELS.get(str(next_role or ''), str(next_role or '未知角色').upper())
    detail = str(reason or '').strip()
    lowered = detail.lower()

    if missing_target:
        human_type = 'action'
        prompt = f'系统原本想把这条 issue 从 {from_label} 流转到 {to_label}，但当前项目里没有可用的 {to_label} 角色实例。'
        required_input = f'请补齐 {to_label} 角色实例，或者明确改派给哪个角色继续推进。'
    elif 'route not allowed' in lowered or 'high risk routes must' in lowered or 'require ceo' in lowered:
        human_type = 'approval'
        prompt = f'系统原本想把这条 issue 从 {from_label} 流转到 {to_label}，但被当前路由规则拦住了。'
        required_input = f'原因：{detail or "当前路由规则不允许该流转"}。请确认应该改派哪个角色、是否升级 CEO，或是否需要调整路由规则。'
    else:
        human_type = 'action'
        prompt = f'系统在尝试把这条 issue 从 {from_label} 流转到 {to_label} 时失败。'
        required_input = detail or '请确认下一步应该如何继续推进。'

    return svc.enqueue_human(
        issue_id=issue_id,
        human_type=human_type,
        prompt=prompt,
        required_input=required_input,
    )


def record_compensation_activity(
    svc: AgentTeamService,
    *,
    issue_id: str,
    attempt_id: str | None,
    assigned_employee_id: str | None,
    summary: str,
    details: dict[str, Any],
) -> None:
    record_issue_activity(
        svc.db.conn,
        now_ms=int(time.time() * 1000),
        issue_id=issue_id,
        attempt_id=attempt_id,
        action_type='system_compensation',
        summary=summary,
        actor_employee_id=assigned_employee_id,
        details=details,
    )


def extract_reusable_artifact_for_issue(issue: dict[str, Any]) -> dict[str, Any] | None:
    metadata = parse_json(issue.get('metadata_json'))
    artifacts = metadata.get('artifacts') if isinstance(metadata.get('artifacts'), dict) else {}
    docs = artifacts.get('feishu_docs') if isinstance(artifacts.get('feishu_docs'), list) else []
    acceptance = str(issue.get('acceptance_criteria_md') or '')
    if any(token in acceptance for token in ['飞书文档', '产出飞书文档', '文档']):
        for item in docs:
            if not isinstance(item, dict):
                continue
            if item.get('doc_url') or item.get('doc_token'):
                return item
    return None


def main() -> int:
    control = load_control()
    report: dict[str, Any] = {
        'ran_at': now_iso(),
        'workflow_control': control,
        'mode': control.get('mode'),
        'dispatched': [],
        'observed': [],
        'skipped': [],
        'cancelled': [],
        'errors': [],
    }

    if control.get('mode') == 'paused':
        report['notes'] = ['worker skipped all progress because workflow_control.mode=paused']
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    svc = AgentTeamService()
    changed = False
    try:
        try:
            lifecycle_report = drain_dispatch_lifecycle_events()
            report['observer'] = {
                'ok': lifecycle_report.get('ok', False),
                'observed_count': len(lifecycle_report.get('observed') or []),
                'applied_count': len(lifecycle_report.get('applied') or []),
                'timed_out': bool(lifecycle_report.get('timedOut')),
            }
            if lifecycle_report.get('applied'):
                changed = True
                for applied in lifecycle_report.get('applied') or []:
                    append_action({'at': report['ran_at'], 'kind': 'observer_apply', **applied})
        except Exception as e:
            report['errors'].append({'message': f'dispatch observer drain failed: {e}'})

        try:
            dep_report = svc.reconcile_dependency_transitions()
            report['dependency_reconcile'] = dep_report
            if (dep_report.get('dependency_released') or dep_report.get('parent_progressed')):
                changed = True
                for item in dep_report.get('dependency_released') or []:
                    append_action({'at': report['ran_at'], 'kind': 'dependency_released', **item})
                for item in dep_report.get('parent_progressed') or []:
                    append_action({'at': report['ran_at'], 'kind': 'parent_progressed', **item})
        except Exception as e:
            report['errors'].append({'message': f'dependency reconcile failed: {e}'})

        ready_items = fetch_ready_candidates(svc)
        dispatched_this_run = 0
        occupied_agent_ids = {
            str(item.get('agent_id') or '').strip()
            for item in ready_items
            if item.get('agent_has_active_issue') and str(item.get('agent_id') or '').strip()
        }
        for issue in ready_items:
            if dispatched_this_run >= MAX_DISPATCH_PER_RUN:
                break
            last_attempt_rows = svc.db.fetch_all(
                'SELECT id, input_snapshot_json FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no DESC LIMIT 1',
                (issue['issue_id'],),
            )
            last_attempt_id = last_attempt_rows[0]['id'] if last_attempt_rows else None
            last_payload = parse_json(last_attempt_rows[0]['input_snapshot_json']) if last_attempt_rows else {}
            metadata = parse_json(issue.get('metadata_json'))
            metadata['prior_handoff'] = latest_success_handoff(svc, issue['issue_id'], exclude_attempt_id=last_attempt_id)
            last_attempt_ctx = latest_attempt_context(svc, issue['issue_id']) if last_attempt_id else {}
            if last_attempt_ctx:
                metadata['retry_context'] = last_attempt_ctx
            pending_handoff = pending_terminal_handoff(last_attempt_ctx, current_role=issue.get('role'))
            if pending_handoff:
                metadata['prior_handoff'] = pending_handoff
                if isinstance(pending_handoff.get('suggested_next_role'), str) and pending_handoff.get('suggested_next_role').strip():
                    metadata['suggested_next_role'] = pending_handoff.get('suggested_next_role').strip()
                if isinstance(pending_handoff.get('risk_level'), str) and pending_handoff.get('risk_level').strip():
                    metadata['risk_level'] = pending_handoff.get('risk_level').strip()
                metadata['needs_human'] = bool(pending_handoff.get('needs_human')) or str(pending_handoff.get('status') or '').strip() == 'needs_human'
                next_role = decide_next_role(current_role=issue.get('role'), metadata=metadata)
                item = {
                    'kind': 'pending_terminal_handoff',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'from_role': issue.get('role'),
                    'next_role_decision': next_role,
                    'last_attempt_no': last_attempt_ctx.get('attempt_no'),
                }
                if next_role == 'close':
                    closed = svc.close_issue(issue_id=issue['issue_id'], resolution='completed')
                    item['close'] = {'resolution': closed.get('resolution'), 'status': closed.get('status')}
                    changed = True
                elif next_role and next_role not in {'close', 'human_queue'}:
                    target_employee_key = pick_target_employee_key(svc, project_key=issue['project_key'], role=next_role)
                    if target_employee_key:
                        try:
                            route_out = svc.handoff_issue(
                                issue_id=issue['issue_id'],
                                to_employee_key=target_employee_key,
                                note=f'auto route after {issue.get("role") or "unknown"} terminal callback',
                                issue_type=str(metadata.get('issue_type') or 'normal'),
                                risk_level=str(metadata.get('risk_level') or 'normal'),
                                handoff_payload=pending_handoff,
                            )
                            item['route'] = {
                                'to_role': next_role,
                                'target_employee_key': target_employee_key,
                                'routing_reason': route_out.get('routing_reason'),
                            }
                            changed = True
                        except Exception as e:
                            human_out = enqueue_route_failure_compensation(
                                svc,
                                issue_id=issue['issue_id'],
                                from_role=issue.get('role'),
                                next_role=next_role,
                                reason=str(e),
                            )
                            item['route_error'] = str(e)
                            item['human_queue'] = human_out
                            changed = True
                            record_compensation_activity(
                                svc,
                                issue_id=issue['issue_id'],
                                attempt_id=last_attempt_id,
                                assigned_employee_id=issue.get('assigned_employee_id'),
                                summary='System compensation: route failure moved to human queue',
                                details={'from_role': issue.get('role'), 'to_role': next_role, 'reason': str(e), 'human_queue': human_out},
                            )
                    else:
                        human_out = enqueue_route_failure_compensation(
                            svc,
                            issue_id=issue['issue_id'],
                            from_role=issue.get('role'),
                            next_role=next_role,
                            reason=f'missing employee for role={next_role} project={issue["project_key"]}',
                            missing_target=True,
                        )
                        item['route_error'] = f'missing employee for role={next_role} project={issue["project_key"]}'
                        item['human_queue'] = human_out
                        changed = True
                        record_compensation_activity(
                            svc,
                            issue_id=issue['issue_id'],
                            attempt_id=last_attempt_id,
                            assigned_employee_id=issue.get('assigned_employee_id'),
                            summary='System compensation: missing route target moved to human queue',
                            details={'from_role': issue.get('role'), 'to_role': next_role, 'reason': item['route_error'], 'human_queue': human_out},
                        )
                else:
                    human_out = svc.enqueue_human(
                        issue_id=issue['issue_id'],
                        human_type='action',
                        prompt='系统没有从这条 issue 的最新完成结果里推导出下一步该交给谁。',
                        required_input='请明确下一步应该流转给哪个角色，或者直接关闭这条 issue。',
                    )
                    item['reason'] = 'no_next_role_decision'
                    item['human_queue'] = human_out
                    changed = True
                    report['skipped'].append(item)
                    record_compensation_activity(
                        svc,
                        issue_id=issue['issue_id'],
                        attempt_id=last_attempt_id,
                        assigned_employee_id=issue.get('assigned_employee_id'),
                        summary='System compensation: missing next role moved to human queue',
                        details=item,
                    )
                append_action({'at': report['ran_at'], **item})
                if changed:
                    report['observed'].append(item)
                continue
            if issue.get('has_open_dependencies'):
                report['skipped'].append({
                    'kind': 'dispatch',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'reason': 'blocked_by_open_dependency',
                })
                continue
            current_agent_id = str(issue.get('agent_id') or '').strip()
            if issue.get('agent_has_active_issue') or (current_agent_id and current_agent_id in occupied_agent_ids):
                report['skipped'].append({
                    'kind': 'dispatch',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'reason': 'agent_has_active_issue',
                    'agent_id': issue.get('agent_id'),
                })
                continue
            if not issue.get('binding_key'):
                human_out = svc.enqueue_human(
                    issue_id=issue['issue_id'],
                    human_type='action',
                    prompt='这条 issue 当前缺少可用的主运行时绑定，系统无法继续自动派发。',
                    required_input='请修复该角色实例的 primary runtime binding，或明确改派到其他可执行角色。',
                )
                changed = True
                item = {
                    'kind': 'dispatch',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'reason': 'missing_primary_runtime_binding',
                    'human_queue': human_out,
                }
                report['skipped'].append(item)
                record_compensation_activity(
                    svc,
                    issue_id=issue['issue_id'],
                    attempt_id=None,
                    assigned_employee_id=issue.get('assigned_employee_id'),
                    summary='System compensation: missing runtime binding moved to human queue',
                    details=item,
                )
                continue

            skip_duplicate, duplicate_reason = should_skip_same_role_redispatch(issue, last_attempt_ctx)
            if skip_duplicate:
                report['skipped'].append({
                    'kind': 'dispatch',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'reason': duplicate_reason,
                    'role': issue.get('role'),
                    'last_attempt_no': last_attempt_ctx.get('attempt_no'),
                })
                append_action({'at': report['ran_at'], 'kind': 'skip_duplicate_dispatch', 'issue_id': issue['issue_id'], 'issue_no': issue['issue_no'], 'role': issue.get('role'), 'reason': duplicate_reason})
                continue
            issue['metadata_json'] = json.dumps(metadata, ensure_ascii=False)
            issue_context_path = write_issue_context_snapshot(svc, issue['issue_id'])
            payload = build_worker_payload(issue, last_payload, session_key=str(issue.get('session_key') or ''), issue_context_path=issue_context_path)
            out = svc.dispatch_execution(
                issue_id=issue['issue_id'],
                runtime_binding_key=issue['binding_key'],
                payload=payload,
            )
            changed = True
            item = {
                'kind': 'dispatch',
                'issue_id': issue['issue_id'],
                'issue_no': issue['issue_no'],
                'status_before': issue['status'],
                'runtime_binding_key': issue['binding_key'],
                'dispatch_ref': out['dispatch_ref'],
                'attempt_no': out['attempt_no'],
                'role': issue.get('role'),
                'artifact_hint': bool(extract_reusable_artifact_for_issue(issue)),
            }
            report['dispatched'].append(item)
            if current_agent_id:
                occupied_agent_ids.add(current_agent_id)
            append_action({'at': report['ran_at'], **item})
            dispatched_this_run += 1

        observe_items = fetch_dispatching_candidates(svc)
        for attempt in observe_items[:MAX_OBSERVE_PER_RUN]:
            age_seconds = max(0, int(time.time() - ((attempt.get('updated_at_ms') or attempt.get('created_at_ms') or 0) / 1000)))
            payload = parse_json(attempt.get('input_snapshot_json'))
            expected_marker = extract_expected_marker(payload)
            expected_text = extract_expected_text(payload)
            if not expected_marker and not expected_text:
                report['skipped'].append({
                    'kind': 'observe',
                    'issue_id': attempt['issue_id'],
                    'issue_no': attempt['issue_no'],
                    'attempt_id': attempt['attempt_id'],
                    'dispatch_ref': attempt['dispatch_ref'],
                    'reason': 'missing_expected_completion_signature',
                })
                continue
            should_auto_close = False
            try:
                out = svc.observe_execution(
                    dispatch_ref=attempt['dispatch_ref'],
                    expected_text=expected_text,
                    expected_marker=expected_marker,
                    timeout_seconds=OBSERVE_TIMEOUT_SECONDS,
                    close_issue_on_success=should_auto_close,
                )
                item = {
                    'kind': 'observe',
                    'issue_id': attempt['issue_id'],
                    'issue_no': attempt['issue_no'],
                    'attempt_id': attempt['attempt_id'],
                    'dispatch_ref': attempt['dispatch_ref'],
                    'attempt_status_before': attempt['attempt_status'],
                    'expected_text': expected_text,
                    'expected_marker': expected_marker,
                    'age_seconds': age_seconds,
                    'result_status': out.get('status'),
                    'issue_status': out.get('issue_status', attempt.get('issue_status')),
                    'auto_close': should_auto_close,
                }
                if out.get('status') == 'succeeded':
                    changed = True
                    append_action({'at': report['ran_at'], **item})
                    issue_ctx = fetch_issue_context(svc, attempt['issue_id'])
                    metadata = parse_json(issue_ctx.get('metadata_json'))
                    raw_wait_payload = ((out.get('wait_result') or {}).get('payload') or {}) if isinstance(out.get('wait_result'), dict) else {}
                    wait_payload = normalize_handoff_payload(raw_wait_payload, marker=expected_marker or expected_text or '', fallback_next_role=attempt.get('role'))
                    metadata['prior_handoff'] = wait_payload
                    if isinstance(wait_payload.get('suggested_next_role'), str) and wait_payload.get('suggested_next_role').strip():
                        metadata['suggested_next_role'] = wait_payload.get('suggested_next_role').strip()
                    if isinstance(wait_payload.get('risk_level'), str) and wait_payload.get('risk_level').strip():
                        metadata['risk_level'] = wait_payload.get('risk_level').strip()
                    metadata['needs_human'] = bool(wait_payload.get('needs_human')) or str(wait_payload.get('status') or '').strip() == 'needs_human'
                    item['agent_suggestion'] = wait_payload
                    next_role = None if out.get('issue_status') in WAITING_HUMAN_STATUSES else decide_next_role(current_role=attempt.get('role'), metadata=metadata)
                    item['next_role_decision'] = next_role or ('human_queue_wait' if out.get('issue_status') in WAITING_HUMAN_STATUSES else None)
                    if next_role == 'close':
                        closed = svc.close_issue(issue_id=attempt['issue_id'], resolution='completed')
                        close_item = {
                            'kind': 'close',
                            'issue_id': attempt['issue_id'],
                            'issue_no': attempt['issue_no'],
                            'from_role': attempt.get('role'),
                            'resolution': closed.get('resolution'),
                            'summary': 'auto closed after success',
                        }
                        item['close'] = close_item
                        item['issue_status'] = 'closed'
                        append_action({'at': report['ran_at'], **close_item})
                    elif next_role and next_role not in {'close', 'human_queue'}:
                        target_employee_key = pick_target_employee_key(svc, project_key=issue_ctx['project_key'], role=next_role)
                        if target_employee_key:
                            try:
                                route_out = svc.handoff_issue(
                                    issue_id=attempt['issue_id'],
                                    to_employee_key=target_employee_key,
                                    note=f'auto route after {attempt.get("role") or "unknown"} success',
                                    issue_type=str(metadata.get('issue_type') or 'normal'),
                                    risk_level=str(metadata.get('risk_level') or 'normal'),
                                    handoff_payload=out.get('payload') if isinstance(out.get('payload'), dict) else None,
                                )
                                route_item = {
                                    'kind': 'route',
                                    'issue_id': attempt['issue_id'],
                                    'issue_no': attempt['issue_no'],
                                    'from_role': attempt.get('role'),
                                    'to_role': next_role,
                                    'target_employee_key': target_employee_key,
                                    'routing_reason': route_out.get('routing_reason'),
                                }
                                item['route'] = route_item
                                append_action({'at': report['ran_at'], **route_item})
                            except Exception as e:
                                human_out = enqueue_route_failure_compensation(
                                    svc,
                                    issue_id=attempt['issue_id'],
                                    from_role=attempt.get('role'),
                                    next_role=next_role,
                                    reason=str(e),
                                )
                                item['route_error'] = str(e)
                                item['human_queue'] = human_out
                                changed = True
                                record_compensation_activity(
                                    svc,
                                    issue_id=attempt['issue_id'],
                                    attempt_id=attempt['attempt_id'],
                                    assigned_employee_id=attempt.get('assigned_employee_id'),
                                    summary='System compensation: route failure moved to human queue',
                                    details={'from_role': attempt.get('role'), 'to_role': next_role, 'reason': str(e), 'human_queue': human_out},
                                )
                        else:
                            human_out = enqueue_route_failure_compensation(
                                svc,
                                issue_id=attempt['issue_id'],
                                from_role=attempt.get('role'),
                                next_role=next_role,
                                reason=f'missing employee for role={next_role} project={issue_ctx["project_key"]}',
                                missing_target=True,
                            )
                            item['route_error'] = f'missing employee for role={next_role} project={issue_ctx["project_key"]}'
                            item['human_queue'] = human_out
                            changed = True
                            record_compensation_activity(
                                svc,
                                issue_id=attempt['issue_id'],
                                attempt_id=attempt['attempt_id'],
                                assigned_employee_id=attempt.get('assigned_employee_id'),
                                summary='System compensation: missing route target moved to human queue',
                                details={'from_role': attempt.get('role'), 'to_role': next_role, 'reason': item['route_error'], 'human_queue': human_out},
                            )
                elif out.get('observe_timeout'):
                    item['observe_timeout'] = out['observe_timeout']
                    if age_seconds >= STALE_ATTEMPT_SECONDS:
                        session_key = payload.get('session_key') if isinstance(payload.get('session_key'), str) else ''
                        active, activity_info = has_recent_runtime_activity(
                            session_key=session_key or str(attempt.get('runtime_session_key') or ''),
                            session_id=str(attempt.get('runtime_session_id') or '') or None,
                            session_file=str(attempt.get('runtime_session_file') or '') or None,
                            since_ms=int(attempt.get('created_at_ms') or 0),
                            marker=expected_marker or expected_text,
                            dispatch_ref=attempt.get('dispatch_ref'),
                        ) if (session_key or attempt.get('runtime_session_key')) else (False, {'check_error': 'missing session_key'})
                        item['recent_activity'] = activity_info
                        if active:
                            item['stale_deferred'] = True
                            report['observed'].append(item)
                            append_action({'at': report['ran_at'], 'kind': 'defer_stale', 'issue_id': attempt['issue_id'], 'issue_no': attempt['issue_no'], 'attempt_id': attempt['attempt_id'], 'dispatch_ref': attempt['dispatch_ref'], 'activity': activity_info})
                            continue
                        try:
                            cancel_out = svc.cancel_execution(
                                dispatch_ref=attempt['dispatch_ref'],
                                reason=f'auto_cancel_stale_attempt_after_{age_seconds}s',
                            )
                        except Exception as e:
                            if 'aborted: False' in str(e) or 'runIds' in str(e):
                                cancel_out = svc.reconcile_stale_attempt(
                                    dispatch_ref=attempt['dispatch_ref'],
                                    reason=f'auto_reconcile_stale_attempt_after_{age_seconds}s',
                                )
                            else:
                                report['errors'].append({
                                    'message': f'stale cancel failed for {attempt["dispatch_ref"]}: {e}',
                                })
                                report['observed'].append(item)
                                continue
                        changed = True
                        cancel_item = {
                            'kind': 'cancel_stale',
                            'issue_id': attempt['issue_id'],
                            'issue_no': attempt['issue_no'],
                            'attempt_id': attempt['attempt_id'],
                            'dispatch_ref': attempt['dispatch_ref'],
                            'attempt_status_before': attempt['attempt_status'],
                            'age_seconds': age_seconds,
                            'result_status': cancel_out.get('status'),
                            'reconciled': cancel_out.get('reconciled', False),
                        }
                        item['stale_cancel'] = cancel_item
                        report['cancelled'].append(cancel_item)
                        append_action({'at': report['ran_at'], **cancel_item})
                report['observed'].append(item)
            except Exception as e:
                report['observed'].append({
                    'kind': 'observe',
                    'issue_id': attempt['issue_id'],
                    'issue_no': attempt['issue_no'],
                    'attempt_id': attempt['attempt_id'],
                    'dispatch_ref': attempt['dispatch_ref'],
                    'attempt_status_before': attempt['attempt_status'],
                    'expected_text': expected_text,
                    'expected_marker': expected_marker,
                    'age_seconds': age_seconds,
                    'result_status': 'observe_error',
                    'auto_close': should_auto_close,
                    'error': str(e),
                })
    except Exception as e:
        report['errors'].append({
            'message': str(e),
            'traceback': traceback.format_exc(),
        })
    finally:
        svc.close()

    if changed:
        try:
            refresh_exports()
            report['exports_refreshed'] = True
        except Exception as e:
            report['errors'].append({'message': f'export refresh failed: {e}'})

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(REPORT_PATH)
    return 0 if not report['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
