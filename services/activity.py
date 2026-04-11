from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def ensure_issue_activity_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS issue_activities (
            id TEXT PRIMARY KEY,
            issue_id TEXT NOT NULL,
            attempt_id TEXT,
            actor_employee_id TEXT,
            actor_role TEXT,
            actor_agent_id TEXT,
            action_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            details_json TEXT,
            created_at_ms INTEGER NOT NULL
        )'''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_issue_activities_issue_created ON issue_activities(issue_id, created_at_ms)')


def actor_meta(conn: sqlite3.Connection, employee_id: str | None) -> tuple[str | None, str | None]:
    if not employee_id:
        return None, None
    row = conn.execute(
        '''SELECT rt.template_key AS role, rb.agent_id
           FROM employee_instances ei
           JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
           WHERE ei.id = ?''',
        (employee_id,),
    ).fetchone()
    if not row:
        return None, None
    return row['role'], row['agent_id']


def record_issue_activity(
    conn: sqlite3.Connection,
    *,
    now_ms: int,
    issue_id: str,
    action_type: str,
    summary: str,
    attempt_id: str | None = None,
    actor_employee_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    role, agent_id = actor_meta(conn, actor_employee_id)
    conn.execute(
        '''INSERT INTO issue_activities (
            id, issue_id, attempt_id, actor_employee_id, actor_role, actor_agent_id,
            action_type, summary, details_json, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            uid('act'),
            issue_id,
            attempt_id,
            actor_employee_id,
            role,
            agent_id,
            action_type,
            summary,
            json.dumps(details or {}, ensure_ascii=False),
            now_ms,
        ),
    )


def fetch_issue_activity(conn: sqlite3.Connection, issue_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''SELECT action_type, summary, actor_role, actor_agent_id, details_json, created_at_ms
           FROM issue_activities
           WHERE issue_id = ?
           ORDER BY created_at_ms''',
        (issue_id,),
    ).fetchall()
    items = []
    for row in rows:
        try:
            details = json.loads(row['details_json'] or '{}')
        except Exception:
            details = {'raw': row['details_json']}
        items.append({
            'action_type': row['action_type'],
            'summary': row['summary'],
            'actor_role': row['actor_role'],
            'actor_agent_id': row['actor_agent_id'],
            'details': details,
            'created_at_ms': row['created_at_ms'],
        })
    return items
