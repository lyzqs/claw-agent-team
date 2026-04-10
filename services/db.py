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


class AgentTeamDB:
    def __init__(self, db_path: Path = DB_PATH):
        if not db_path.exists():
            raise AgentTeamServiceError(f'DB missing: {db_path}')
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

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
