from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import current_db_path

DB_PATH = current_db_path()


class AgentTeamServiceError(RuntimeError):
    pass


class NotFoundError(AgentTeamServiceError):
    pass


class ValidationError(AgentTeamServiceError):
    pass


def now_ms() -> int:
    import time
    return int(time.time() * 1000)


def ensure_attempt_callback_schema(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(issue_attempts)").fetchall()}
    alter_statements: list[str] = []
    if 'flow_id' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN flow_id TEXT")
    if 'callback_token' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN callback_token TEXT")
    if 'callback_status' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN callback_status TEXT")
    if 'callback_received_at_ms' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN callback_received_at_ms INTEGER")
    if 'callback_payload_json' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN callback_payload_json TEXT")
    if 'artifact_status' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN artifact_status TEXT")
    if 'artifact_snapshot_json' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN artifact_snapshot_json TEXT")
    if 'timeout_deadline_ms' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN timeout_deadline_ms INTEGER")
    if 'reconciled_at_ms' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN reconciled_at_ms INTEGER")
    if 'completion_mode' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN completion_mode TEXT")
    if 'runtime_session_key' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN runtime_session_key TEXT")
    if 'runtime_session_id' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN runtime_session_id TEXT")
    if 'runtime_session_file' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN runtime_session_file TEXT")
    if 'derived_issues_json' not in columns:
        alter_statements.append("ALTER TABLE issue_attempts ADD COLUMN derived_issues_json TEXT")
    for sql in alter_statements:
        conn.execute(sql)

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


def ensure_issue_status_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='issues'").fetchone()
    if not row or not row[0]:
        return
    sql = str(row[0])
    required_statuses = {'waiting_children', 'waiting_recovery_completion'}
    if all(status in sql for status in required_statuses):
        return

    conn.execute('ALTER TABLE issues RENAME TO issues__old_schema')
    conn.execute(
        '''CREATE TABLE issues (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          issue_no INTEGER NOT NULL,
          title TEXT NOT NULL,
          description_md TEXT,
          source_type TEXT NOT NULL DEFAULT 'user'
            CHECK (source_type IN ('user', 'system', 'detector', 'watchdog', 'human')),
          priority TEXT NOT NULL DEFAULT 'p2'
            CHECK (priority IN ('p0', 'p1', 'p2', 'p3', 'p4')),
          status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN (
              'open',
              'triaged',
              'ready',
              'dispatching',
              'running',
              'blocked',
              'waiting_human_info',
              'waiting_human_action',
              'waiting_human_approval',
              'review',
              'waiting_recovery_completion',
              'waiting_children',
              'closed',
              'failed'
            )),
          owner_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
          assigned_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
          active_attempt_no INTEGER,
          blocker_summary TEXT,
          required_human_input TEXT,
          acceptance_criteria_md TEXT,
          latest_checkpoint_at_ms INTEGER,
          closed_at_ms INTEGER,
          metadata_json TEXT,
          created_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER NOT NULL,
          UNIQUE (project_id, issue_no)
        )'''
    )
    conn.execute(
        '''INSERT INTO issues (
          id, project_id, issue_no, title, description_md, source_type, priority, status,
          owner_employee_id, assigned_employee_id, active_attempt_no, blocker_summary,
          required_human_input, acceptance_criteria_md, latest_checkpoint_at_ms,
          closed_at_ms, metadata_json, created_at_ms, updated_at_ms
        )
        SELECT
          id, project_id, issue_no, title, description_md, source_type, priority, status,
          owner_employee_id, assigned_employee_id, active_attempt_no, blocker_summary,
          required_human_input, acceptance_criteria_md, latest_checkpoint_at_ms,
          closed_at_ms, metadata_json, created_at_ms, updated_at_ms
        FROM issues__old_schema'''
    )
    conn.execute('DROP TABLE issues__old_schema')


def ensure_scheduled_issue_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_issues'").fetchone()
    recreate = False
    if row and row[0]:
        sql = str(row[0])
        if "'cron'" not in sql:
            recreate = True
    if row and recreate:
        conn.execute('ALTER TABLE scheduled_issues RENAME TO scheduled_issues__old_schema')

    conn.execute(
        '''CREATE TABLE IF NOT EXISTS scheduled_issues (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          owner_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
          title TEXT NOT NULL,
          description_md TEXT,
          acceptance_criteria_md TEXT,
          priority TEXT NOT NULL DEFAULT 'p2'
            CHECK (priority IN ('p0', 'p1', 'p2', 'p3', 'p4')),
          route_role TEXT NOT NULL
            CHECK (route_role IN ('pm', 'dev', 'qa', 'ops', 'ceo')),
          source_type TEXT NOT NULL DEFAULT 'system'
            CHECK (source_type IN ('user', 'system', 'detector', 'watchdog', 'human')),
          dispatch_instruction TEXT,
          schedule_kind TEXT NOT NULL
            CHECK (schedule_kind IN ('hourly', 'daily', 'weekly', 'monthly', 'interval', 'one_time', 'cron')),
          schedule_config_json TEXT NOT NULL,
          timezone TEXT NOT NULL DEFAULT 'UTC',
          enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
          next_run_at_ms INTEGER,
          last_run_at_ms INTEGER,
          last_issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
          last_issue_no INTEGER,
          last_error TEXT,
          metadata_json TEXT,
          created_at_ms INTEGER NOT NULL,
          updated_at_ms INTEGER NOT NULL
        )'''
    )

    if row and recreate:
        conn.execute(
            '''INSERT INTO scheduled_issues (
              id, project_id, owner_employee_id, title, description_md, acceptance_criteria_md,
              priority, route_role, source_type, dispatch_instruction, schedule_kind,
              schedule_config_json, timezone, enabled, next_run_at_ms, last_run_at_ms,
              last_issue_id, last_issue_no, last_error, metadata_json, created_at_ms, updated_at_ms
            )
            SELECT
              id, project_id, owner_employee_id, title, description_md, acceptance_criteria_md,
              priority, route_role, source_type, dispatch_instruction,
              CASE WHEN schedule_kind = 'cron' THEN 'cron' ELSE schedule_kind END,
              schedule_config_json, timezone, enabled, next_run_at_ms, last_run_at_ms,
              last_issue_id, last_issue_no, last_error, metadata_json, created_at_ms, updated_at_ms
            FROM scheduled_issues__old_schema'''
        )
        conn.execute('DROP TABLE scheduled_issues__old_schema')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_issues_due ON scheduled_issues(enabled, next_run_at_ms)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_issues_project ON scheduled_issues(project_id, updated_at_ms DESC)')
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS scheduled_issue_runs (
          id TEXT PRIMARY KEY,
          scheduled_issue_id TEXT NOT NULL REFERENCES scheduled_issues(id) ON DELETE CASCADE,
          status TEXT NOT NULL
            CHECK (status IN ('created', 'manual_created', 'failed', 'skipped')),
          issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
          issue_no INTEGER,
          error_message TEXT,
          details_json TEXT,
          created_at_ms INTEGER NOT NULL
        )'''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_issue_runs_schedule_created ON scheduled_issue_runs(scheduled_issue_id, created_at_ms DESC)')


class AgentTeamDB:
    def __init__(self, db_path: Path = DB_PATH):
        if not db_path.exists():
            raise AgentTeamServiceError(f'DB missing: {db_path}')
        self.conn = sqlite3.connect(db_path)
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.conn.row_factory = sqlite3.Row
        ensure_issue_status_schema(self.conn)
        ensure_attempt_callback_schema(self.conn)
        ensure_scheduled_issue_schema(self.conn)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def get_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            raise NotFoundError(f'No row for query: {sql} {params}')
        return row

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()
