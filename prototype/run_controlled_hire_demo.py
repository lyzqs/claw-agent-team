#!/usr/bin/env python3
"""Controlled hire demo for Agent Team Phase 7.

Covers:
- hire_request input fields
- policy / budget / headcount checks
- approval gate
- approved -> provisioning -> active
- employee_instance creation
- runtime_binding creation
- manager relation
- one full sample flow
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
WORK_ROOT = Path('/root/.openclaw/workspace-agent-team')
DB_PATH = PROTO_ROOT / 'agent_team.db'
ARTIFACT_DIR = WORK_ROOT / 'evidence' / 'phase7'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_PATH = ARTIFACT_DIR / 'controlled_hire_demo_result.json'


def now_ms() -> int:
    return int(time.time() * 1000)


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def get_one(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f'expected row for query: {sql} {params}')
    return row


def next_request_no(conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute('SELECT COALESCE(MAX(request_no), 0) + 1 FROM hire_requests WHERE project_id = ?', (project_id,)).fetchone()
    return int(row[0])


def create_hire_request(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    requested_role_template_id: str,
    requester_employee_id: str,
    target_manager_employee_id: str,
    seat_count: int,
    justification: str,
    budget_code: str,
) -> tuple[str, int]:
    ts = now_ms()
    request_id = uid('hire')
    request_no = next_request_no(conn, project_id)
    conn.execute(
        '''INSERT INTO hire_requests (
            id, request_no, project_id, requested_role_template_id,
            requester_employee_id, target_manager_employee_id,
            seat_count, status, justification, budget_code,
            metadata_json, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)''',
        (
            request_id,
            request_no,
            project_id,
            requested_role_template_id,
            requester_employee_id,
            target_manager_employee_id,
            seat_count,
            justification,
            budget_code,
            json.dumps({'demo': 'controlled_hire'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return request_id, request_no


def update_hire_status(conn: sqlite3.Connection, request_id: str, status: str, *, note: str | None = None, approver_id: str | None = None, provisioned_employee_id: str | None = None) -> None:
    ts = now_ms()
    decision_note_expr = note
    if approver_id and provisioned_employee_id:
        conn.execute(
            'UPDATE hire_requests SET status = ?, decision_note = ?, approved_by_employee_id = ?, provisioned_employee_id = ?, updated_at_ms = ?, resolved_at_ms = ? WHERE id = ?',
            (status, decision_note_expr, approver_id, provisioned_employee_id, ts, ts if status in ('active', 'rejected', 'failed', 'cancelled') else None, request_id),
        )
    elif approver_id:
        conn.execute(
            'UPDATE hire_requests SET status = ?, decision_note = ?, approved_by_employee_id = ?, updated_at_ms = ? WHERE id = ?',
            (status, decision_note_expr, approver_id, ts, request_id),
        )
    elif provisioned_employee_id:
        conn.execute(
            'UPDATE hire_requests SET status = ?, decision_note = ?, provisioned_employee_id = ?, updated_at_ms = ?, resolved_at_ms = ? WHERE id = ?',
            (status, decision_note_expr, provisioned_employee_id, ts, ts if status in ('active', 'rejected', 'failed', 'cancelled') else None, request_id),
        )
    else:
        conn.execute(
            'UPDATE hire_requests SET status = ?, decision_note = ?, updated_at_ms = ? WHERE id = ?',
            (status, decision_note_expr, ts, request_id),
        )


def run_policy_checks(conn: sqlite3.Connection, *, project_id: str, role_template_id: str, seat_count: int, budget_code: str) -> dict:
    role = get_one(conn, 'SELECT id, template_key, scope FROM role_templates WHERE id = ?', (role_template_id,))
    active_for_role = conn.execute(
        'SELECT COUNT(*) FROM employee_instances WHERE project_id = ? AND role_template_id = ? AND status = ?',
        (project_id, role_template_id, 'active'),
    ).fetchone()[0]
    checks = {
        'role_scope_ok': role['scope'] == 'project',
        'seat_count_ok': seat_count == 1,
        'budget_code_ok': budget_code.startswith('agent-team-core-'),
        'headcount_ok': active_for_role < 3,
        'current_active_for_role': int(active_for_role),
        'role_template_key': role['template_key'],
    }
    checks['passed'] = all(v for k, v in checks.items() if k.endswith('_ok'))
    return checks


def create_employee_instance(
    conn: sqlite3.Connection,
    *,
    employee_key: str,
    display_name: str,
    project_id: str,
    role_template_id: str,
    manager_employee_id: str,
) -> str:
    ts = now_ms()
    employee_id = uid('emp')
    conn.execute(
        '''INSERT INTO employee_instances (
            id, employee_key, display_name, employment_scope,
            project_id, role_template_id, manager_employee_id,
            status, notes, metadata_json, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, 'project', ?, ?, ?, 'provisioning', ?, ?, ?, ?)''',
        (
            employee_id,
            employee_key,
            display_name,
            project_id,
            role_template_id,
            manager_employee_id,
            'Provisioned by controlled hire demo',
            json.dumps({'demo': 'controlled_hire'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return employee_id


def create_runtime_binding(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    binding_key: str,
    agent_id: str,
    session_key: str,
    workspace_path: str,
    memory_scope: str,
) -> str:
    ts = now_ms()
    runtime_id = uid('rb')
    conn.execute(
        '''INSERT INTO runtime_bindings (
            id, employee_id, runtime_type, binding_key, agent_id, session_key,
            workspace_path, memory_scope, status, is_primary, metadata_json,
            created_at_ms, updated_at_ms
        ) VALUES (?, ?, 'openclaw_session', ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?)''',
        (
            runtime_id,
            employee_id,
            binding_key,
            agent_id,
            session_key,
            workspace_path,
            memory_scope,
            json.dumps({'demo': 'controlled_hire'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return runtime_id


def activate_employee_and_runtime(conn: sqlite3.Connection, *, employee_id: str, runtime_id: str) -> None:
    ts = now_ms()
    conn.execute('UPDATE employee_instances SET status = ?, updated_at_ms = ? WHERE id = ?', ('active', ts, employee_id))
    conn.execute('UPDATE runtime_bindings SET status = ?, updated_at_ms = ? WHERE id = ?', ('active', ts, runtime_id))


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        project = get_one(conn, 'SELECT id, project_key FROM projects WHERE project_key = ?', ('agent-team-core',))
        role_dev = get_one(conn, 'SELECT id, template_key FROM role_templates WHERE template_key = ?', ('dev',))
        pm = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('agent-team-core.pm',))
        ceo = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('shared.ceo',))

        request_id, request_no = create_hire_request(
            conn,
            project_id=project['id'],
            requested_role_template_id=role_dev['id'],
            requester_employee_id=pm['id'],
            target_manager_employee_id=pm['id'],
            seat_count=1,
            justification='Need a second project-scoped dev seat for queue pressure relief and controlled scaling demo.',
            budget_code='agent-team-core-fy2026-hiring',
        )
        update_hire_status(conn, request_id, 'pending_policy_check', note='Draft validated; entering policy checks.')

        policy = run_policy_checks(
            conn,
            project_id=project['id'],
            role_template_id=role_dev['id'],
            seat_count=1,
            budget_code='agent-team-core-fy2026-hiring',
        )
        if not policy['passed']:
            update_hire_status(conn, request_id, 'failed', note=f'Policy check failed: {policy}')
            conn.commit()
            raise RuntimeError(f'Policy check failed: {policy}')

        update_hire_status(conn, request_id, 'pending_approval', note='Policy/budget/headcount checks passed; awaiting CEO approval.')
        update_hire_status(conn, request_id, 'approved', note='CEO approved controlled hire demo.', approver_id=ceo['id'])
        update_hire_status(conn, request_id, 'provisioning', note='Provisioning employee instance and runtime binding.', approver_id=ceo['id'])

        employee_key = f"agent-team-core.dev.hire{request_no}"
        employee_id = create_employee_instance(
            conn,
            employee_key=employee_key,
            display_name=f'Agent Team Core Dev Hire {request_no}',
            project_id=project['id'],
            role_template_id=role_dev['id'],
            manager_employee_id=pm['id'],
        )
        runtime_id = create_runtime_binding(
            conn,
            employee_id=employee_id,
            binding_key=f'{employee_key}.primary',
            agent_id='main',
            session_key=f'agent:main:project:agent-team-core:dev:hire{request_no}',
            workspace_path='/root/.openclaw/workspace/agent-team-prototype',
            memory_scope='project:agent-team-core',
        )
        activate_employee_and_runtime(conn, employee_id=employee_id, runtime_id=runtime_id)
        update_hire_status(conn, request_id, 'active', note='Provisioning complete; new employee and runtime binding are active.', approver_id=ceo['id'], provisioned_employee_id=employee_id)
        conn.commit()

        hire_request = dict(get_one(conn, 'SELECT * FROM hire_requests WHERE id = ?', (request_id,)))
        employee = dict(get_one(conn, 'SELECT * FROM employee_instances WHERE id = ?', (employee_id,)))
        runtime = dict(get_one(conn, 'SELECT * FROM runtime_bindings WHERE id = ?', (runtime_id,)))

        result = {
            'project_key': project['project_key'],
            'hire_request': hire_request,
            'policy_checks': policy,
            'provisioned_employee': employee,
            'runtime_binding': runtime,
            'manager_relation': {
                'employee_key': employee['employee_key'],
                'manager_employee_id': employee['manager_employee_id'],
                'manager_employee_key': pm['employee_key'],
            },
            'lifecycle': [
                'draft',
                'pending_policy_check',
                'pending_approval',
                'approved',
                'provisioning',
                'active',
            ],
        }
        ARTIFACT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
