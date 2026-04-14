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


def ensure_issue_attempt_callback_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS issue_attempt_callbacks (
            id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL,
            flow_id TEXT,
            callback_token TEXT NOT NULL,
            phase TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_json TEXT,
            accepted INTEGER NOT NULL DEFAULT 1,
            accepted_reason TEXT,
            created_at_ms INTEGER NOT NULL
        )'''
    )
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_issue_attempt_callbacks_idempotency ON issue_attempt_callbacks(idempotency_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_issue_attempt_callbacks_attempt_created ON issue_attempt_callbacks(attempt_id, created_at_ms)')


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


def record_attempt_callback_event(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    flow_id: str | None,
    callback_token: str,
    phase: str,
    idempotency_key: str,
    payload: dict[str, Any] | None,
    accepted: bool,
    accepted_reason: str | None,
    created_at_ms: int,
) -> tuple[bool, dict[str, Any]]:
    ensure_issue_attempt_callback_table(conn)
    existing = conn.execute(
        '''SELECT id, attempt_id, flow_id, callback_token, phase, idempotency_key, payload_json, accepted, accepted_reason, created_at_ms
           FROM issue_attempt_callbacks
           WHERE idempotency_key = ?''',
        (idempotency_key,),
    ).fetchone()
    if existing:
        return False, {
            'id': existing['id'],
            'attempt_id': existing['attempt_id'],
            'flow_id': existing['flow_id'],
            'callback_token': existing['callback_token'],
            'phase': existing['phase'],
            'idempotency_key': existing['idempotency_key'],
            'payload_json': existing['payload_json'],
            'accepted': bool(existing['accepted']),
            'accepted_reason': existing['accepted_reason'],
            'created_at_ms': existing['created_at_ms'],
        }
    callback_id = uid('cb')
    conn.execute(
        '''INSERT INTO issue_attempt_callbacks (
            id, attempt_id, flow_id, callback_token, phase, idempotency_key,
            payload_json, accepted, accepted_reason, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            callback_id,
            attempt_id,
            flow_id,
            callback_token,
            phase,
            idempotency_key,
            json.dumps(payload or {}, ensure_ascii=False),
            1 if accepted else 0,
            accepted_reason,
            created_at_ms,
        ),
    )
    return True, {
        'id': callback_id,
        'attempt_id': attempt_id,
        'flow_id': flow_id,
        'callback_token': callback_token,
        'phase': phase,
        'idempotency_key': idempotency_key,
        'payload_json': json.dumps(payload or {}, ensure_ascii=False),
        'accepted': accepted,
        'accepted_reason': accepted_reason,
        'created_at_ms': created_at_ms,
    }


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
