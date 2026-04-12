#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
DB_PATH = Path('/root/.openclaw/workspace/agent-team-prototype/agent_team.db')
STATE_DIR = ROOT / 'state'
STATE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = STATE_DIR / 'session_sweep_report.json'
MAX_DELETE_DONE = 5
MAX_DELETE_CLOSED_RUNNING = 3


def gateway_call(method: str, params: dict) -> dict:
    out = subprocess.check_output([
        'openclaw', 'gateway', 'call', method,
        '--json', '--timeout', '60000', '--params', json.dumps(params, ensure_ascii=False)
    ], text=True)
    return json.loads(out)


def list_sessions() -> list[dict]:
    return gateway_call('sessions.list', {'limit': 200}).get('sessions', [])


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    closed_issue_ids = {row[0] for row in conn.execute("select id from issues where status='closed'").fetchall()}
    sessions = list_sessions()
    report = {
        'deleted_done': [],
        'deleted_closed_issue_running': [],
        'kept_running': [],
        'errors': [],
        'limits': {
            'max_delete_done': MAX_DELETE_DONE,
            'max_delete_closed_running': MAX_DELETE_CLOSED_RUNNING,
        }
    }

    done_deleted = 0
    closed_running_deleted = 0

    for sess in sessions:
        key = sess.get('key', '')
        status = sess.get('status')
        if ':auto:issue:' not in key and not key.startswith('agent:auto:'):
            continue

        if status == 'done' and done_deleted < MAX_DELETE_DONE:
            try:
                result = gateway_call('sessions.delete', {'key': key})
                report['deleted_done'].append({'key': key, 'result': result})
                done_deleted += 1
            except Exception as e:
                report['errors'].append({'key': key, 'action': 'delete_done', 'error': str(e)})
            continue

        hit_closed = None
        for issue_id in closed_issue_ids:
            if issue_id in key:
                hit_closed = issue_id
                break

        if status == 'running' and hit_closed and closed_running_deleted < MAX_DELETE_CLOSED_RUNNING:
            try:
                abort = gateway_call('chat.abort', {'sessionKey': key})
            except Exception as e:
                abort = {'error': str(e)}
            try:
                deleted = gateway_call('sessions.delete', {'key': key})
                report['deleted_closed_issue_running'].append({
                    'key': key,
                    'issue_id': hit_closed,
                    'abort': abort,
                    'deleted': deleted,
                })
                closed_running_deleted += 1
            except Exception as e:
                report['errors'].append({'key': key, 'issue_id': hit_closed, 'action': 'delete_closed_running', 'error': str(e), 'abort': abort})
            continue

        if status == 'running':
            report['kept_running'].append({'key': key, 'status': status, 'closed_issue': bool(hit_closed)})

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(REPORT_PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
