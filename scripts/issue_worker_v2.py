#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))

from services.agent_team_service import AgentTeamService  # noqa: E402
from services.workflow_control import load_control  # noqa: E402

STATE_DIR = ROOT / 'state'
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_REGISTRY_PATH = STATE_DIR / 'session_registry.json'
REPORT_PATH = STATE_DIR / 'worker_report.json'
ACTIONS_PATH = STATE_DIR / 'worker_actions.jsonl'
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
    proposal = out.get('create_issue_proposal')
    out['create_issue_proposal'] = proposal if isinstance(proposal, dict) else None
    return out


def latest_success_handoff(svc: AgentTeamService, issue_id: str, exclude_attempt_id: str | None = None) -> dict[str, Any]:
    rows = svc.db.fetch_all(
        '''SELECT id, output_snapshot_json
           FROM issue_attempts
           WHERE issue_id = ? AND status = 'succeeded'
           ORDER BY attempt_no DESC''',
        (issue_id,),
    )
    for row in rows:
        if exclude_attempt_id and row['id'] == exclude_attempt_id:
            continue
        payload = parse_json(row['output_snapshot_json'])
        wait_result = payload.get('wait_result') if isinstance(payload.get('wait_result'), dict) else {}
        handoff = wait_result.get('payload') if isinstance(wait_result.get('payload'), dict) else {}
        if handoff:
            return handoff
    return {}



def latest_attempt_context(svc: AgentTeamService, issue_id: str) -> dict[str, Any]:
    row = svc.db.get_one(
        '''SELECT attempt_no, status, failure_summary, result_summary, input_snapshot_json, output_snapshot_json, completion_mode
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
    return {
        'attempt_no': row['attempt_no'],
        'status': row['status'],
        'failure_summary': row['failure_summary'],
        'result_summary': row['result_summary'],
        'completion_mode': row['completion_mode'],
        'attempt_role': input_snapshot.get('attempt_role'),
        'worker_instruction': input_snapshot.get('worker_instruction'),
        'wait_payload': wait_payload,
    }



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


def has_recent_runtime_activity(*, session_key: str, since_ms: int, lookback_seconds: int = 300) -> tuple[bool, dict[str, Any]]:
    try:
        cmd = [
            'openclaw', 'gateway', 'call', 'sessions.get',
            '--json',
            '--timeout', '20000',
            '--params', json.dumps({'sessionKey': session_key, 'limit': 50}, ensure_ascii=False),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return False, {'check_error': res.stderr.strip() or res.stdout.strip()}
        data = json.loads(res.stdout)
        messages = data.get('messages') or []
        now_ms_local = int(time.time() * 1000)
        threshold_ms = now_ms_local - lookback_seconds * 1000
        newest_ts = None
        newest_role = None
        newest_text = None
        for message in messages:
            ts = message.get('timestamp')
            if not isinstance(ts, (int, float)):
                continue
            ts = int(ts)
            if ts < since_ms or ts < threshold_ms:
                continue
            role = message.get('role')
            if role not in {'assistant', 'tool'}:
                continue
            if newest_ts is None or ts > newest_ts:
                newest_ts = ts
                newest_role = role
                newest_text = extract_text(message)[:200] if isinstance(message, dict) else None
        return bool(newest_ts is not None), {
            'newest_ts': newest_ts,
            'newest_role': newest_role,
            'newest_text': newest_text,
            'lookback_seconds': lookback_seconds,
        }
    except Exception as e:
        return False, {'check_error': str(e)}


def build_worker_payload(issue: dict[str, Any], last_attempt_payload: dict[str, Any], *, session_key: str) -> dict[str, Any]:
    metadata = parse_json(issue.get('metadata_json'))
    role = issue.get('role') or 'agent'
    role_label = ROLE_LABELS.get(role, role.upper())
    marker = f"AUTO_DONE_{issue['issue_no']}_{uuid.uuid4().hex[:8]}"
    flow_id = f"flow_{uuid.uuid4().hex[:12]}"
    callback_token = f"cbtok_{uuid.uuid4().hex[:12]}"

    base_instruction = None
    role_worker_instruction = metadata.get(f'worker_instruction_{role}')
    role_dispatch_instruction = metadata.get(f'dispatch_instruction_{role}')
    current_worker_instruction = metadata.get('worker_instruction')
    current_dispatch_instruction = metadata.get('dispatch_instruction')
    last_attempt_role = last_attempt_payload.get('attempt_role') if isinstance(last_attempt_payload.get('attempt_role'), str) else None
    reuse_last_instruction = last_attempt_role == role
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
            base_instruction = candidate.strip()
            break

    if not base_instruction:
        description = issue.get('description_md') or 'No description.'
        acceptance = issue.get('acceptance_criteria_md') or 'No explicit acceptance criteria.'
        blocker = issue.get('blocker_summary') or 'None.'
        required_input = issue.get('required_human_input') or 'None.'
        base_instruction = (
            f"请以 {role_label} 角色处理这个 issue。\n"
            f"标题：{issue['title']}\n"
            f"描述：{description}\n"
            f"验收标准：{acceptance}\n"
            f"当前阻塞：{blocker}\n"
            f"当前缺失的人类输入：{required_input}\n"
            "真实代码、脚本、文档、验证请使用规范实现仓库 /root/.openclaw/workspace-agent-team（角色工作区里可用 ./repo 快捷入口）。\n"
            "只做当前角色最小必要且正确的工作，不要越过本角色职责边界。"
        )

    prior_handoff = metadata.get('prior_handoff') if isinstance(metadata.get('prior_handoff'), dict) else {}
    prior_summary = ''
    if prior_handoff:
        prior_summary = (
            "上一角色交接：\n"
            f"- summary: {prior_handoff.get('summary') or prior_handoff.get('reason') or ''}\n"
            f"- suggested_next_role: {prior_handoff.get('suggested_next_role') or ''}\n\n"
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

    prompt = (
        f"你现在以 Agent Team 的 {role_label} 角色工作。Issue #{issue['issue_no']}。\n\n"
        f"{prior_summary}"
        f"{retry_summary}"
        f"{recovery_summary}"
        f"{artifact_summary}"
        f"角色边界：{role_boundary_rules.get(role, '请只做当前角色边界内的工作。')}\n\n"
        f"任务：\n{base_instruction}\n\n"
        "要求：\n"
        "1. 只做当前角色最小必要的工作。\n"
        "2. 不要做无关探索。\n"
        "3. 即使已有中间产出，也必须做本角色的一轮显式判断，不要静默跳过。\n"
        "4. 如果验收已满足，请明确给出关闭或继续流转建议。\n"
        "5. 如果创建了飞书文档等外部产物，请先记录 artifact callback。\n"
        "6. 最终回复必须是单个 JSON 对象，不要带 markdown、代码块或额外说明。\n\n"
        "最终 JSON 只需要包含这些字段：\n"
        f"marker={marker}\n"
        "status, summary, artifacts, blocking_findings, suggested_next_role, reason, risk_level, needs_human, create_issue_proposal\n\n"
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
                  rt.template_key AS role,
                  rb.binding_key,
                  rb.session_key,
                  p.project_key
           FROM issues i
           LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
           LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
           LEFT JOIN projects p ON p.id = ei.project_id
           WHERE i.status IN ('triaged', 'ready', 'review', 'waiting_recovery_completion')
             AND NOT EXISTS (SELECT 1 FROM issue_attempts ia WHERE ia.issue_id = i.id AND ia.status IN ('dispatching','running'))
           ORDER BY i.updated_at_ms ASC'''
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



def create_issue_from_proposal(svc: AgentTeamService, *, proposal: dict[str, Any], fallback_project_key: str, fallback_owner_employee_key: str, current_issue_id: str, current_attempt_no: int | None, created_by_role: str | None) -> dict[str, Any]:
    project_key = proposal.get('project_key') or fallback_project_key
    owner_employee_key = proposal.get('owner_employee_key') or fallback_owner_employee_key
    route_role = proposal.get('route_role') or 'pm'
    relation_type = proposal.get('relation_type') or 'related_to'
    source_type = proposal.get('source_type') or 'system'
    metadata = dict(proposal.get('metadata') or {}) if isinstance(proposal.get('metadata'), dict) else {}
    metadata.update({
        'logical_source_type': 'agent',
        'created_from_issue_id': current_issue_id,
        'created_from_attempt_no': current_attempt_no,
        'created_from_role': created_by_role,
    })
    created = svc.create_issue(
        project_key=project_key,
        owner_employee_key=owner_employee_key,
        title=proposal['title'],
        description_md=proposal.get('description_md', ''),
        acceptance_criteria_md=proposal.get('acceptance_criteria_md', ''),
        priority=proposal.get('priority', 'p2'),
        source_type=source_type if source_type in {'user', 'system', 'detector', 'watchdog', 'human'} else 'system',
        metadata=metadata,
    )
    assign_employee_key = pick_target_employee_key(svc, project_key=project_key, role=route_role)
    triaged = svc.triage_issue(issue_id=created['issue_id'], assign_employee_key=assign_employee_key)
    creator_row = svc.db.conn.execute('SELECT assigned_employee_id FROM issues WHERE id = ?', (current_issue_id,)).fetchone()
    svc.db.conn.execute(
        'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)',
        (f"rel_{uuid.uuid4().hex[:12]}", current_issue_id, created['issue_id'], relation_type, creator_row[0] if creator_row else None, int(time.time() * 1000)),
    )
    svc.db.commit()
    return {
        'created': created,
        'triaged': triaged,
        'assign_employee_key': assign_employee_key,
        'route_role': route_role,
        'relation_type': relation_type,
    }


def decide_next_role(*, current_role: str | None, metadata: dict[str, Any]) -> str | None:
    suggested = metadata.get('suggested_next_role')
    if isinstance(suggested, str) and suggested.strip():
        return suggested.strip()
    issue_type = str(metadata.get('issue_type') or 'normal')
    risk_level = str(metadata.get('risk_level') or 'normal')
    requires_ops = bool(metadata.get('requires_ops')) or issue_type in {'production_change', 'release'}
    if current_role == 'pm':
        return 'dev'
    if current_role == 'dev':
        return 'qa'
    if current_role == 'qa':
        if requires_ops:
            return 'ops'
        return 'ceo' if risk_level in {'normal', 'high'} else 'ceo'
    if current_role == 'ops':
        return 'ceo'
    if current_role == 'ceo':
        return 'close'
    return None


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

        ready_items = fetch_ready_candidates(svc)
        for issue in ready_items[:MAX_DISPATCH_PER_RUN]:
            if not issue.get('binding_key'):
                report['skipped'].append({
                    'kind': 'dispatch',
                    'issue_id': issue['issue_id'],
                    'issue_no': issue['issue_no'],
                    'reason': 'missing_primary_runtime_binding',
                })
                continue
            last_attempt_rows = svc.db.fetch_all(
                'SELECT id, input_snapshot_json FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no DESC LIMIT 1',
                (issue['issue_id'],),
            )
            last_attempt_id = last_attempt_rows[0]['id'] if last_attempt_rows else None
            last_payload = parse_json(last_attempt_rows[0]['input_snapshot_json']) if last_attempt_rows else {}
            metadata = parse_json(issue.get('metadata_json'))
            metadata['prior_handoff'] = latest_success_handoff(svc, issue['issue_id'], exclude_attempt_id=last_attempt_id)
            if last_attempt_id:
                metadata['retry_context'] = latest_attempt_context(svc, issue['issue_id'])
            issue['metadata_json'] = json.dumps(metadata, ensure_ascii=False)
            session_key = canonical_session_key(project_key=issue.get('project_key') or 'shared', role=issue.get('role') or 'dev')
            payload = build_worker_payload(issue, last_payload, session_key=session_key)
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
            append_action({'at': report['ran_at'], **item})

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
                    item['agent_suggestion'] = wait_payload
                    if wait_payload.get('create_issue_proposal'):
                        try:
                            created_issue = create_issue_from_proposal(
                                svc,
                                proposal=wait_payload['create_issue_proposal'],
                                fallback_project_key=issue_ctx['project_key'],
                                fallback_owner_employee_key=issue_ctx.get('assigned_employee_key') or 'shared.ceo',
                                current_issue_id=attempt['issue_id'],
                                current_attempt_no=attempt.get('attempt_no'),
                                created_by_role=attempt.get('role'),
                            )
                            item['created_issue'] = created_issue
                            append_action({'at': report['ran_at'], 'kind': 'create_issue', 'source_issue_id': attempt['issue_id'], 'source_issue_no': attempt['issue_no'], 'created_issue_no': created_issue['created']['issue_no'], 'route_role': created_issue['route_role']})
                        except Exception as e:
                            item['create_issue_error'] = str(e)
                    next_role = decide_next_role(current_role=attempt.get('role'), metadata=metadata)
                    item['next_role_decision'] = next_role
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
                    elif next_role and next_role != 'close':
                        target_employee_key = pick_target_employee_key(svc, project_key=issue_ctx['project_key'], role=next_role)
                        if target_employee_key:
                            route_out = svc.handoff_issue(
                                issue_id=attempt['issue_id'],
                                to_employee_key=target_employee_key,
                                note=f'auto route after {attempt.get("role") or "unknown"} success',
                                issue_type=str(metadata.get('issue_type') or 'normal'),
                                risk_level=str(metadata.get('risk_level') or 'normal'),
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
                        else:
                            item['route_error'] = f'missing employee for role={next_role} project={issue_ctx["project_key"]}'
                elif out.get('observe_timeout'):
                    item['observe_timeout'] = out['observe_timeout']
                    if age_seconds >= STALE_ATTEMPT_SECONDS:
                        session_key = payload.get('session_key') if isinstance(payload.get('session_key'), str) else ''
                        active, activity_info = has_recent_runtime_activity(
                            session_key=session_key,
                            since_ms=int(attempt.get('created_at_ms') or 0),
                        ) if session_key else (False, {'check_error': 'missing session_key'})
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
