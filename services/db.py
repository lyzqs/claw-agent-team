from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
DB_PATH = PROTO_ROOT / 'agent_team.db'


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


class AgentTeamDB:
    def __init__(self, db_path: Path = DB_PATH):
        if not db_path.exists():
            raise AgentTeamServiceError(f'DB missing: {db_path}')
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        ensure_attempt_callback_schema(self.conn)
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
