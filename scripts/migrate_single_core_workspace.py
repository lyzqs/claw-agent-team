#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.config import ROOT, STATE_DIR, PRIMARY_DB_PATH, LEGACY_DB_PATH

KEEP_PROJECT_KEY = 'agent-team-core'
KEEP_EMPLOYEE_KEYS = {
    'agent-team-core.pm',
    'agent-team-core.dev',
    'agent-team-core.qa',
    'agent-team-core.ops',
    'shared.ceo',
}
REMOVE_AGENT_DIRS = [
    'agent-team',
    'agent-team-dev-hire1',
    'agent-team-labs-dev',
    'agent-team-labs-ops',
    'agent-team-labs-pm',
    'agent-team-labs-qa',
]
AGENTS_ROOT = Path('/root/.openclaw/agents')
SESSION_REGISTRY_PATH = STATE_DIR / 'session_registry.json'
WORKFLOW_CONTROL_PATH = STATE_DIR / 'workflow_control.json'


@dataclass
class MigrationReport:
    backup_dir: str
    target_db: str
    source_db: str
    removed_projects: list[str]
    removed_employees: list[str]
    removed_agent_dirs: list[str]
    remaining_projects: list[str]
    remaining_employees: list[str]
    issue_counts_by_project: list[dict[str, Any]]
    session_registry_keys: list[str]


def now_tag() -> str:
    return datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def move_dir_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def role_to_keep_employee_id(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        '''SELECT ei.id, ei.employee_key, rt.template_key AS role
           FROM employee_instances ei
           JOIN role_templates rt ON rt.id = ei.role_template_id
           WHERE ei.employee_key IN (?, ?, ?, ?, ?)''',
        tuple(KEEP_EMPLOYEE_KEYS),
    ).fetchall()
    mapping: dict[str, str] = {}
    for row in rows:
        mapping[row['role']] = row['id']
    return mapping


def ensure_state_db_from_legacy(backup_dir: Path) -> None:
    if not LEGACY_DB_PATH.exists():
        raise RuntimeError(f'legacy DB missing: {LEGACY_DB_PATH}')
    PRIMARY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    copy_if_exists(PRIMARY_DB_PATH, backup_dir / 'state-agent_team.db.before')
    copy_if_exists(LEGACY_DB_PATH, backup_dir / 'legacy-agent_team.db.before')
    shutil.copy2(LEGACY_DB_PATH, PRIMARY_DB_PATH)


def cleanup_database() -> dict[str, Any]:
    conn = sqlite3.connect(PRIMARY_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        keep_project = conn.execute('SELECT id, project_key FROM projects WHERE project_key = ?', (KEEP_PROJECT_KEY,)).fetchone()
        if keep_project is None:
            raise RuntimeError(f'keep project not found: {KEEP_PROJECT_KEY}')
        keep_project_id = keep_project['id']

        role_keep_ids = role_to_keep_employee_id(conn)
        keep_employee_ids = {
            row['id']
            for row in conn.execute(
                'SELECT id FROM employee_instances WHERE employee_key IN (?, ?, ?, ?, ?)',
                tuple(KEEP_EMPLOYEE_KEYS),
            ).fetchall()
        }

        removed_projects = [
            row['project_key']
            for row in conn.execute('SELECT project_key FROM projects WHERE project_key != ? ORDER BY project_key', (KEEP_PROJECT_KEY,)).fetchall()
        ]
        removed_employees = [
            row['employee_key']
            for row in conn.execute(
                'SELECT employee_key FROM employee_instances WHERE employee_key NOT IN (?, ?, ?, ?, ?) ORDER BY employee_key',
                tuple(KEEP_EMPLOYEE_KEYS),
            ).fetchall()
        ]

        deleted_issue_rows = conn.execute(
            '''SELECT i.id, i.issue_no, p.project_key
               FROM issues i
               JOIN projects p ON p.id = i.project_id
               WHERE i.project_id != ?''',
            (keep_project_id,),
        ).fetchall()
        deleted_issue_ids = [row['id'] for row in deleted_issue_rows]
        deleted_attempt_ids: list[str] = []
        if deleted_issue_ids:
            placeholders = ','.join('?' for _ in deleted_issue_ids)
            deleted_attempt_ids = [
                row['id']
                for row in conn.execute(
                    f'SELECT id FROM issue_attempts WHERE issue_id IN ({placeholders})',
                    tuple(deleted_issue_ids),
                ).fetchall()
            ]
            conn.execute(
                f'DELETE FROM issue_activities WHERE issue_id IN ({placeholders})',
                tuple(deleted_issue_ids),
            )
            if deleted_attempt_ids:
                attempt_placeholders = ','.join('?' for _ in deleted_attempt_ids)
                conn.execute(
                    f'DELETE FROM issue_attempt_callbacks WHERE attempt_id IN ({attempt_placeholders})',
                    tuple(deleted_attempt_ids),
                )
            conn.execute(
                f'DELETE FROM issues WHERE id IN ({placeholders})',
                tuple(deleted_issue_ids),
            )

        employee_meta = {
            row['id']: {'role': row['role'], 'employee_key': row['employee_key']}
            for row in conn.execute(
                '''SELECT ei.id, ei.employee_key, rt.template_key AS role
                   FROM employee_instances ei
                   JOIN role_templates rt ON rt.id = ei.role_template_id'''
            ).fetchall()
        }

        core_issue_rows = conn.execute(
            'SELECT id, owner_employee_id, assigned_employee_id FROM issues WHERE project_id = ?',
            (keep_project_id,),
        ).fetchall()
        for row in core_issue_rows:
            owner_id = row['owner_employee_id']
            assigned_id = row['assigned_employee_id']
            new_owner = owner_id
            new_assigned = assigned_id
            if owner_id and owner_id not in keep_employee_ids:
                role = employee_meta.get(owner_id, {}).get('role')
                new_owner = role_keep_ids.get(role, role_keep_ids.get('pm'))
            if assigned_id and assigned_id not in keep_employee_ids:
                role = employee_meta.get(assigned_id, {}).get('role')
                new_assigned = role_keep_ids.get(role, role_keep_ids.get('pm'))
            if new_owner != owner_id or new_assigned != assigned_id:
                conn.execute(
                    'UPDATE issues SET owner_employee_id = ?, assigned_employee_id = ? WHERE id = ?',
                    (new_owner, new_assigned, row['id']),
                )

        conn.execute('DELETE FROM hire_requests')

        conn.execute(
            'DELETE FROM employee_instances WHERE employee_key NOT IN (?, ?, ?, ?, ?)',
            tuple(KEEP_EMPLOYEE_KEYS),
        )
        conn.execute('DELETE FROM projects WHERE project_key != ?', (KEEP_PROJECT_KEY,))

        conn.execute('DELETE FROM issue_activities WHERE issue_id NOT IN (SELECT id FROM issues)')
        conn.execute('DELETE FROM issue_attempt_callbacks WHERE attempt_id NOT IN (SELECT id FROM issue_attempts)')

        conn.commit()
        conn.execute('VACUUM')
        conn.commit()

        remaining_projects = [
            row['project_key']
            for row in conn.execute('SELECT project_key FROM projects ORDER BY project_key').fetchall()
        ]
        remaining_employees = [
            row['employee_key']
            for row in conn.execute('SELECT employee_key FROM employee_instances ORDER BY employee_key').fetchall()
        ]
        issue_counts_by_project = [
            dict(row)
            for row in conn.execute(
                '''SELECT p.project_key, COUNT(*) AS issue_count
                   FROM issues i
                   JOIN projects p ON p.id = i.project_id
                   GROUP BY p.project_key
                   ORDER BY p.project_key'''
            ).fetchall()
        ]
        return {
            'removed_projects': removed_projects,
            'removed_employees': removed_employees,
            'remaining_projects': remaining_projects,
            'remaining_employees': remaining_employees,
            'issue_counts_by_project': issue_counts_by_project,
        }
    finally:
        conn.close()


def cleanup_session_registry(backup_dir: Path) -> list[str]:
    copy_if_exists(SESSION_REGISTRY_PATH, backup_dir / 'session_registry.json.before')
    if not SESSION_REGISTRY_PATH.exists():
        return []
    data = json.loads(SESSION_REGISTRY_PATH.read_text(encoding='utf-8'))
    filtered = {
        key: value
        for key, value in data.items()
        if key in {
            'agent-team-pm|agent-team-core',
            'agent-team-dev|agent-team-core',
            'agent-team-qa|agent-team-core',
            'agent-team-ops|agent-team-core',
            'agent-team-ceo|shared',
        }
    }
    SESSION_REGISTRY_PATH.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding='utf-8')
    return sorted(filtered.keys())


def cleanup_agent_dirs(backup_dir: Path) -> list[str]:
    removed: list[str] = []
    agents_backup = backup_dir / 'agents-removed'
    for name in REMOVE_AGENT_DIRS:
        src = AGENTS_ROOT / name
        dst = agents_backup / name
        if move_dir_if_exists(src, dst):
            removed.append(name)
    return removed


def backup_misc_state(backup_dir: Path) -> None:
    copy_if_exists(WORKFLOW_CONTROL_PATH, backup_dir / 'workflow_control.json.before')


def main() -> int:
    backup_dir = STATE_DIR / 'backups' / f'migrate-single-core-{now_tag()}'
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_misc_state(backup_dir)
    ensure_state_db_from_legacy(backup_dir)
    db_report = cleanup_database()
    kept_registry_keys = cleanup_session_registry(backup_dir)
    removed_agent_dirs = cleanup_agent_dirs(backup_dir)

    report = MigrationReport(
        backup_dir=str(backup_dir),
        target_db=str(PRIMARY_DB_PATH),
        source_db=str(LEGACY_DB_PATH),
        removed_projects=db_report['removed_projects'],
        removed_employees=db_report['removed_employees'],
        removed_agent_dirs=removed_agent_dirs,
        remaining_projects=db_report['remaining_projects'],
        remaining_employees=db_report['remaining_employees'],
        issue_counts_by_project=db_report['issue_counts_by_project'],
        session_registry_keys=kept_registry_keys,
    )
    report_path = backup_dir / 'migration_report.json'
    report_path.write_text(json.dumps(report.__dict__, ensure_ascii=False, indent=2), encoding='utf-8')
    print(report_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
