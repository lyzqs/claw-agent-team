#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
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
REPORT_PATH = STATE_DIR / 'worker_report.json'
ACTIONS_PATH = STATE_DIR / 'worker_actions.jsonl'
EXPORT_BOARD = ROOT / 'scripts' / 'export_board_snapshot.py'
EXPORT_ISSUES = ROOT / 'scripts' / 'export_issue_details.py'

MAX_DISPATCH_PER_RUN = 3
MAX_OBSERVE_PER_RUN = 6
OBSERVE_TIMEOUT_SECONDS = 2

ROLE_LABELS = {
    'pm': 'PM',
    'dev': 'Dev',
    'qa': 'QA',
    'ops': 'Ops',
    'ceo': 'CEO',
}


def now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat() + 'Z'


def parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def append_action(payload: dict[str, Any]) -> None:
    with ACTIONS_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


def refresh_exports() -> None:
    subprocess.run(['python3', str(EXPORT_BOARD)], check=True, capture_output=True, text=True)
    subprocess.run(['python3', str(EXPORT_ISSUES)], check=True, capture_output=True, text=True)


def extract_expected_text(payload: dict[str, Any]) -> str | None:
    marker = payload.get('marker')
    if isinstance(marker, str) and marker.strip():
        return marker.strip()
    expected = payload.get('expected_text')
    if isinstance(expected, str) and expected.strip():
        return expected.strip()
    prompt = payload.get('prompt')
    if isinstance(prompt, str):
        m = re.search(r'exactly\s+([^\n]+?)\s+and\s+nothing\s+else', prompt, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('.').strip('"').strip("'")
    return None


def build_worker_payload(issue: dict[str, Any], last_attempt_payload: dict[str, Any]) -> dict[str, Any]:
    metadata = parse_json(issue.get('metadata_json'))
    role = issue.get('role') or 'agent'
    role_label = ROLE_LABELS.get(role, role.upper())
    marker = f"AUTO_DONE_{issue['issue_no']}_{uuid.uuid4().hex[:8]}"

    base_instruction = None
    for candidate in (
        metadata.get('dispatch_instruction'),
        metadata.get('worker_instruction'),
        metadata.get('prompt'),
        last_attempt_payload.get('worker_instruction'),
        last_attempt_payload.get('prompt'),
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
            f"Process this issue as the {role_label} role.\n"
            f"Title: {issue['title']}\n"
            f"Description: {description}\n"
            f"Acceptance Criteria: {acceptance}\n"
            f"Current Blocker: {blocker}\n"
            f"Required Human Input: {required_input}\n"
            "Use the canonical implementation repo at /root/.openclaw/workspace-agent-team (shortcut: ./repo in the role workspace) for real code, scripts, docs, and validation.\n"
            "Do the minimal correct work for this role using available tools if needed."
        )

    prompt = (
        f"You are acting as the {role_label} role in Agent Team.\n"
        f"Issue #{issue['issue_no']} ({issue['issue_id']}), status={issue['status']}.\n\n"
        f"{base_instruction}\n\n"
        f"When you are done, reply with exactly {marker} and nothing else."
    )
    return {
        'prompt': prompt,
        'marker': marker,
        'worker_instruction': base_instruction,
        'generated_by': 'issue_worker_v2',
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
                  rb.session_key
           FROM issues i
           LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
           LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
           WHERE i.status IN ('triaged', 'ready', 'review')
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


def main() -> int:
    control = load_control()
    report: dict[str, Any] = {
        'ran_at': now_iso(),
        'workflow_control': control,
        'mode': control.get('mode'),
        'dispatched': [],
        'observed': [],
        'skipped': [],
        'errors': [],
    }

    if control.get('mode') == 'paused':
        report['notes'] = ['worker skipped all progress because workflow_control.mode=paused']
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    svc = AgentTeamService()
    changed = False
    try:
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
                'SELECT input_snapshot_json FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no DESC LIMIT 1',
                (issue['issue_id'],),
            )
            last_payload = parse_json(last_attempt_rows[0]['input_snapshot_json']) if last_attempt_rows else {}
            payload = build_worker_payload(issue, last_payload)
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
            }
            report['dispatched'].append(item)
            append_action({'at': report['ran_at'], **item})

        observe_items = fetch_dispatching_candidates(svc)
        for attempt in observe_items[:MAX_OBSERVE_PER_RUN]:
            payload = parse_json(attempt.get('input_snapshot_json'))
            expected_text = extract_expected_text(payload)
            if not expected_text:
                report['skipped'].append({
                    'kind': 'observe',
                    'issue_id': attempt['issue_id'],
                    'issue_no': attempt['issue_no'],
                    'attempt_id': attempt['attempt_id'],
                    'dispatch_ref': attempt['dispatch_ref'],
                    'reason': 'missing_expected_text_marker',
                })
                continue
            auto_close = attempt.get('role') == 'ceo'
            try:
                out = svc.observe_execution(
                    dispatch_ref=attempt['dispatch_ref'],
                    expected_text=expected_text,
                    timeout_seconds=OBSERVE_TIMEOUT_SECONDS,
                    close_issue_on_success=auto_close,
                )
                item = {
                    'kind': 'observe',
                    'issue_id': attempt['issue_id'],
                    'issue_no': attempt['issue_no'],
                    'attempt_id': attempt['attempt_id'],
                    'dispatch_ref': attempt['dispatch_ref'],
                    'attempt_status_before': attempt['attempt_status'],
                    'expected_text': expected_text,
                    'result_status': out.get('status'),
                    'issue_status': out.get('issue_status', attempt.get('issue_status')),
                    'auto_close': auto_close,
                }
                if out.get('status') == 'succeeded':
                    changed = True
                    append_action({'at': report['ran_at'], **item})
                    issue_ctx = fetch_issue_context(svc, attempt['issue_id'])
                    metadata = parse_json(issue_ctx.get('metadata_json'))
                    next_role = decide_next_role(current_role=attempt.get('role'), metadata=metadata)
                    item['next_role_decision'] = next_role
                    if next_role and next_role != 'close' and not auto_close:
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
                    'result_status': 'observe_error',
                    'auto_close': auto_close,
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
