from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('AGENT_TEAM_ROOT', str(DEFAULT_ROOT))).expanduser()
STATE_DIR = Path(os.environ.get('AGENT_TEAM_STATE_DIR', str(ROOT / 'state'))).expanduser()
DATA_ROOT = Path(os.environ.get('AGENT_TEAM_DATA_ROOT', str(STATE_DIR))).expanduser()
PRIMARY_DB_PATH = Path(os.environ.get('AGENT_TEAM_DB_PATH', str(DATA_ROOT / 'agent_team.db'))).expanduser()
LEGACY_PROTO_ROOT = Path(os.environ.get('AGENT_TEAM_LEGACY_PROTO_ROOT', '/root/.openclaw/workspace/agent-team-prototype')).expanduser()
LEGACY_DB_PATH = Path(os.environ.get('AGENT_TEAM_LEGACY_DB_PATH', str(LEGACY_PROTO_ROOT / 'agent_team.db'))).expanduser()

STATE_DIR.mkdir(parents=True, exist_ok=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)

REQUIRED_DB_TABLES = {
    'projects',
    'issues',
    'issue_attempts',
    'employee_instances',
    'runtime_bindings',
}


def _is_populated_agent_team_db(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {str(row[0]) for row in rows}
        finally:
            conn.close()
    except Exception:
        return False
    return REQUIRED_DB_TABLES.issubset(tables)


@lru_cache(maxsize=1)
def resolve_db_path() -> tuple[Path, str]:
    explicit_db = os.environ.get('AGENT_TEAM_DB_PATH')
    if explicit_db:
        return PRIMARY_DB_PATH, 'env:AGENT_TEAM_DB_PATH'
    if _is_populated_agent_team_db(PRIMARY_DB_PATH):
        return PRIMARY_DB_PATH, 'state:agent_team.db'
    if _is_populated_agent_team_db(LEGACY_DB_PATH):
        return LEGACY_DB_PATH, 'legacy:agent-team-prototype'
    return PRIMARY_DB_PATH, 'default:state-agent_team.db'


def current_db_path() -> Path:
    return resolve_db_path()[0]


def current_db_source() -> str:
    return resolve_db_path()[1]


def runtime_path_snapshot() -> dict[str, str]:
    resolved, source = resolve_db_path()
    return {
        'root': str(ROOT),
        'state_dir': str(STATE_DIR),
        'data_root': str(DATA_ROOT),
        'primary_db_path': str(PRIMARY_DB_PATH),
        'legacy_db_path': str(LEGACY_DB_PATH),
        'resolved_db_path': str(resolved),
        'resolved_db_source': source,
    }
