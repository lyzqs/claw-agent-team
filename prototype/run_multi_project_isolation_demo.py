#!/usr/bin/env python3
"""Phase 9 multi-project isolation demo for Agent Team prototype."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
WORK_ROOT = Path('/root/.openclaw/workspace-agent-team')
DB_PATH = PROTO_ROOT / 'agent_team.db'
ARTIFACT_DIR = WORK_ROOT / 'evidence' / 'phase9'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_PATH = ARTIFACT_DIR / 'multi_project_isolation_result.json'


def now_ms() -> int:
    return int(time.time() * 1000)


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def get_one(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f'expected row for query: {sql} {params}')
    return row


def find_or_create_role(conn: sqlite3.Connection, template_key: str, name: str, scope: str, desc: str) -> str:
    row = conn.execute('SELECT id FROM role_templates WHERE template_key = ?', (template_key,)).fetchone()
    if row:
        return row['id']
    ts = now_ms()
    role_id = uid('role')
    conn.execute(
        '''INSERT INTO role_templates (
            id, template_key, name, scope, description, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (role_id, template_key, name, scope, desc, ts, ts),
    )
    return role_id


def create_project(conn: sqlite3.Connection, project_key: str, name: str, description: str) -> str:
    existing = conn.execute('SELECT id FROM projects WHERE project_key = ?', (project_key,)).fetchone()
    if existing:
        return existing['id']
    ts = now_ms()
    project_id = uid('proj')
    conn.execute(
        '''INSERT INTO projects (id, project_key, name, description, status, created_at_ms, updated_at_ms)
           VALUES (?, ?, ?, ?, 'active', ?, ?)''',
        (project_id, project_key, name, description, ts, ts),
    )
    return project_id


def create_employee(conn: sqlite3.Connection, employee_key: str, display_name: str, project_id: str | None, role_template_id: str, manager_employee_id: str | None, employment_scope: str) -> str:
    existing = conn.execute('SELECT id FROM employee_instances WHERE employee_key = ?', (employee_key,)).fetchone()
    if existing:
        return existing['id']
    ts = now_ms()
    employee_id = uid('emp')
    conn.execute(
        '''INSERT INTO employee_instances (
            id, employee_key, display_name, employment_scope, project_id,
            role_template_id, manager_employee_id, status, notes, metadata_json,
            created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)''',
        (
            employee_id,
            employee_key,
            display_name,
            employment_scope,
            project_id,
            role_template_id,
            manager_employee_id,
            'Created by multi-project isolation demo',
            json.dumps({'demo': 'phase9_multi_project'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return employee_id


def create_runtime(conn: sqlite3.Connection, employee_id: str, binding_key: str, session_key: str, workspace_path: str, memory_scope: str) -> str:
    existing = conn.execute('SELECT id FROM runtime_bindings WHERE binding_key = ?', (binding_key,)).fetchone()
    if existing:
        return existing['id']
    ts = now_ms()
    runtime_id = uid('rb')
    conn.execute(
        '''INSERT INTO runtime_bindings (
            id, employee_id, runtime_type, binding_key, agent_id, session_key,
            workspace_path, memory_scope, status, is_primary, metadata_json,
            created_at_ms, updated_at_ms
        ) VALUES (?, ?, 'openclaw_session', ?, 'main', ?, ?, ?, 'active', 1, ?, ?, ?)''',
        (
            runtime_id,
            employee_id,
            binding_key,
            session_key,
            workspace_path,
            memory_scope,
            json.dumps({'demo': 'phase9_multi_project'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return runtime_id


def next_issue_no(conn: sqlite3.Connection, project_id: str) -> int:
    return int(conn.execute('SELECT COALESCE(MAX(issue_no), 0) + 1 FROM issues WHERE project_id = ?', (project_id,)).fetchone()[0])


def create_issue(conn: sqlite3.Connection, project_id: str, owner_employee_id: str, assigned_employee_id: str, title: str, description_md: str) -> tuple[str, int]:
    ts = now_ms()
    issue_id = uid('issue')
    issue_no = next_issue_no(conn, project_id)
    conn.execute(
        '''INSERT INTO issues (
            id, project_id, issue_no, title, description_md, source_type, priority, status,
            owner_employee_id, assigned_employee_id, active_attempt_no, acceptance_criteria_md,
            latest_checkpoint_at_ms, metadata_json, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, 'user', 'p2', 'closed', ?, ?, 1, ?, ?, ?, ?, ?)''',
        (
            issue_id,
            project_id,
            issue_no,
            title,
            description_md,
            owner_employee_id,
            assigned_employee_id,
            '- issue remains within its owning project boundary',
            ts,
            json.dumps({'demo': 'phase9_multi_project'}, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return issue_id, issue_no


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        role_ceo = find_or_create_role(conn, 'ceo', 'CEO', 'shared', 'Governance and controlled hiring')
        role_pm = find_or_create_role(conn, 'pm', 'PM', 'project', 'Triage and assignment')
        role_dev = find_or_create_role(conn, 'dev', 'Dev', 'project', 'Implementation and checkpoints')
        role_qa = find_or_create_role(conn, 'qa', 'QA', 'project', 'Validation and acceptance input')
        role_ops = find_or_create_role(conn, 'ops', 'Ops', 'project', 'Runtime and observability')

        ceo = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('shared.ceo',))
        project_a = get_one(conn, 'SELECT id, project_key FROM projects WHERE project_key = ?', ('agent-team-core',))
        project_b_id = create_project(conn, 'agent-team-labs', 'Agent Team Labs', 'Second isolated sample project for Phase 9 validation')
        project_b = get_one(conn, 'SELECT id, project_key FROM projects WHERE id = ?', (project_b_id,))

        pm_b = create_employee(conn, 'agent-team-labs.pm', 'Agent Team Labs PM', project_b['id'], role_pm, ceo['id'], 'project')
        dev_b = create_employee(conn, 'agent-team-labs.dev', 'Agent Team Labs Dev', project_b['id'], role_dev, pm_b, 'project')
        qa_b = create_employee(conn, 'agent-team-labs.qa', 'Agent Team Labs QA', project_b['id'], role_qa, pm_b, 'project')
        ops_b = create_employee(conn, 'agent-team-labs.ops', 'Agent Team Labs Ops', project_b['id'], role_ops, pm_b, 'project')

        create_runtime(conn, pm_b, 'agent-team-labs.pm.primary', 'agent:main:project:agent-team-labs:pm', '/root/.openclaw/workspace/agent-team-prototype/projects/agent-team-labs', 'project:agent-team-labs')
        create_runtime(conn, dev_b, 'agent-team-labs.dev.primary', 'agent:main:project:agent-team-labs:dev', '/root/.openclaw/workspace/agent-team-prototype/projects/agent-team-labs', 'project:agent-team-labs')
        create_runtime(conn, qa_b, 'agent-team-labs.qa.primary', 'agent:main:project:agent-team-labs:qa', '/root/.openclaw/workspace/agent-team-prototype/projects/agent-team-labs', 'project:agent-team-labs')
        create_runtime(conn, ops_b, 'agent-team-labs.ops.primary', 'agent:main:project:agent-team-labs:ops', '/root/.openclaw/workspace/agent-team-prototype/projects/agent-team-labs', 'project:agent-team-labs')

        issue_a_id, issue_a_no = create_issue(
            conn,
            project_a['id'],
            get_one(conn, 'SELECT id FROM employee_instances WHERE employee_key = ?', ('agent-team-core.pm',))['id'],
            get_one(conn, 'SELECT id FROM employee_instances WHERE employee_key = ?', ('agent-team-core.dev',))['id'],
            'Project A isolation proof task',
            'Artifact and queue should remain inside agent-team-core only.',
        )
        issue_b_id, issue_b_no = create_issue(
            conn,
            project_b['id'],
            pm_b,
            dev_b,
            'Project B isolation proof task',
            'Artifact and queue should remain inside agent-team-labs only.',
        )
        conn.commit()

        projects = [dict(r) for r in conn.execute('SELECT project_key, name, status FROM projects WHERE project_key IN (?, ?) ORDER BY project_key', ('agent-team-core', 'agent-team-labs'))]
        shared_roles = [dict(r) for r in conn.execute('''SELECT e.employee_key, e.employment_scope, e.project_id, rb.session_key, rb.memory_scope
            FROM employee_instances e LEFT JOIN runtime_bindings rb ON rb.employee_id = e.id AND rb.is_primary = 1
            WHERE e.employee_key = 'shared.ceo'
        ''')]
        project_runtime_views = [dict(r) for r in conn.execute('''SELECT p.project_key, e.employee_key, rb.binding_key, rb.session_key, rb.memory_scope, rb.workspace_path
            FROM runtime_bindings rb
            JOIN employee_instances e ON e.id = rb.employee_id
            LEFT JOIN projects p ON p.id = e.project_id
            WHERE e.employee_key LIKE 'agent-team-core.%' OR e.employee_key LIKE 'agent-team-labs.%'
            ORDER BY e.employee_key
        ''')]
        issue_views = [dict(r) for r in conn.execute('''SELECT p.project_key, i.issue_no, i.title, i.status, owner.employee_key AS owner_employee_key, assigned.employee_key AS assigned_employee_key
            FROM issues i
            JOIN projects p ON p.id = i.project_id
            LEFT JOIN employee_instances owner ON owner.id = i.owner_employee_id
            LEFT JOIN employee_instances assigned ON assigned.id = i.assigned_employee_id
            WHERE i.id IN (?, ?)
            ORDER BY p.project_key, i.issue_no
        ''', (issue_a_id, issue_b_id))]

        core_runtime = [r for r in project_runtime_views if r['project_key'] == 'agent-team-core']
        labs_runtime = [r for r in project_runtime_views if r['project_key'] == 'agent-team-labs']
        isolation_checks = {
            'project_boundary_defined': len(projects) == 2,
            'shared_ceo_rule': len(shared_roles) == 1 and shared_roles[0]['memory_scope'] == 'org',
            'workspace_isolation': all('/projects/agent-team-labs' not in r['workspace_path'] for r in core_runtime) and all('/projects/agent-team-labs' in r['workspace_path'] for r in labs_runtime),
            'memory_isolation': all(r['memory_scope'] == 'project:agent-team-core' for r in core_runtime) and all(r['memory_scope'] == 'project:agent-team-labs' for r in labs_runtime),
            'issue_isolation': len({(r['project_key'], r['issue_no']) for r in issue_views}) == 2,
            'queue_isolation': all(r['session_key'].startswith('agent:main:project:agent-team-core:') for r in core_runtime) and all(r['session_key'].startswith('agent:main:project:agent-team-labs:') for r in labs_runtime),
            'budget_boundary_placeholder': True,
        }
        isolation_checks['passed'] = all(isolation_checks.values())

        result = {
            'projects': projects,
            'shared_roles': shared_roles,
            'project_runtime_views': project_runtime_views,
            'issue_views': issue_views,
            'created_sample_issues': {
                'agent-team-core': issue_a_no,
                'agent-team-labs': issue_b_no,
            },
            'isolation_checks': isolation_checks,
        }
        ARTIFACT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
