#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402

DB_PATH = Path('/root/.openclaw/workspace/agent-team-prototype/agent_team.db')


def wait_worker_cycle(seconds: int = 35):
    time.sleep(seconds)


def latest_attempt(conn, issue_no: int):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        '''select ia.attempt_no, ia.status, ia.dispatch_ref, ia.input_snapshot_json
           from issue_attempts ia join issues i on i.id=ia.issue_id
           where i.issue_no=? order by ia.attempt_no desc limit 1''',
        (issue_no,),
    ).fetchone()
    return dict(row) if row else None


def main():
    svc = AgentTeamService()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        issue_no = 20
        issue_id = conn.execute('select id from issues where issue_no=?', (issue_no,)).fetchone()['id']

        # stage 1: dev
        svc.handoff_issue(issue_id=issue_id, to_employee_key='agent-team-core.dev', note='canonical stage validation: PM -> Dev')
        subprocess.check_call(['systemctl', 'restart', 'agent-team-worker.service'])
        wait_worker_cycle(35)
        attempt_dev = latest_attempt(conn, issue_no)

        # stage 2: qa
        svc.handoff_issue(issue_id=issue_id, to_employee_key='agent-team-core.qa', note='canonical stage validation: Dev -> QA')
        subprocess.check_call(['systemctl', 'restart', 'agent-team-worker.service'])
        wait_worker_cycle(35)
        attempt_qa = latest_attempt(conn, issue_no)

        # stage 3: ceo
        svc.handoff_issue(issue_id=issue_id, to_employee_key='shared.ceo', note='canonical stage validation: QA -> CEO')
        subprocess.check_call(['systemctl', 'restart', 'agent-team-worker.service'])
        wait_worker_cycle(35)
        attempt_ceo = latest_attempt(conn, issue_no)

        print(json.dumps({
            'issue_no': issue_no,
            'dev': attempt_dev,
            'qa': attempt_qa,
            'ceo': attempt_ceo,
        }, ensure_ascii=False, indent=2))
    finally:
        svc.close()
        conn.close()


if __name__ == '__main__':
    main()
