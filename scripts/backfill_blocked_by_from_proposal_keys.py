#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

DB = Path('/root/.openclaw/workspace-agent-team/state/agent_team.db')


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def parse_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT i.id, i.issue_no, i.metadata_json
               FROM issues i
               WHERE i.source_type = 'system' '''
        ).fetchall()
        by_key = {}
        for row in rows:
            meta = parse_json(row['metadata_json'])
            proposal_key = str(meta.get('proposal_key') or '').strip()
            if proposal_key:
                by_key[proposal_key] = dict(row)

        inserted = []
        for row in rows:
            meta = parse_json(row['metadata_json'])
            dep_keys = meta.get('depends_on_proposal_keys') if isinstance(meta.get('depends_on_proposal_keys'), list) else []
            dep_keys = [str(x).strip() for x in dep_keys if str(x or '').strip()]
            for dep_key in dep_keys:
                dep = by_key.get(dep_key)
                if not dep or dep['id'] == row['id']:
                    continue
                exists = conn.execute(
                    '''SELECT 1 FROM issue_relations
                       WHERE from_issue_id = ? AND to_issue_id = ? AND relation_type = 'blocked_by'
                       LIMIT 1''',
                    (row['id'], dep['id']),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, CAST(strftime("%s","now") AS INTEGER) * 1000)',
                    (uid('rel'), row['id'], dep['id'], 'blocked_by', None),
                )
                inserted.append({'issue_no': row['issue_no'], 'depends_on_issue_no': dep['issue_no'], 'proposal_dep_key': dep_key})
        conn.commit()
        print(json.dumps({'ok': True, 'inserted': inserted}, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
