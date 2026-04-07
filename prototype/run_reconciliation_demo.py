#!/usr/bin/env python3
"""Phase 8 reconciliation / observability demo for Agent Team prototype.

Covers:
- startup reconciliation scan
- non-terminal issue scan
- dispatching/running attempt scan
- run existence check (simulated via dispatch_ref presence / terminality)
- stale ledger repair recommendations
- stalled/orphan detection
- retry/resume strategy classification
- minimal audit log
- minimal runtime metrics
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
WORK_ROOT = Path('/root/.openclaw/workspace-agent-team')
DB_PATH = PROTO_ROOT / 'agent_team.db'
ARTIFACT_DIR = WORK_ROOT / 'evidence' / 'phase8'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_PATH = ARTIFACT_DIR / 'reconciliation_demo_result.json'
AUDIT_PATH = ARTIFACT_DIR / 'reconciliation_audit_log.jsonl'

TERMINAL_ISSUE_STATUSES = {'closed', 'failed'}
ACTIVE_ATTEMPT_STATUSES = {'dispatching', 'running'}
RETRYABLE_ATTEMPT_STATUSES = {'cancelled', 'timed_out', 'failed'}


def now_ms() -> int:
    return int(time.time() * 1000)


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def write_audit(event_type: str, payload: dict) -> None:
    record = {
        'event_id': uid('audit'),
        'at_ms': now_ms(),
        'type': event_type,
        'payload': payload,
    }
    with AUDIT_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def fetch_rows(conn: sqlite3.Connection, sql: str, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def classify_attempt_recovery(attempt: dict) -> str:
    status = attempt['status']
    if status == 'dispatching':
        return 'resume_or_verify_dispatch'
    if status == 'running':
        return 'resume_if_run_alive_else_reconcile'
    if status in {'cancelled', 'timed_out'}:
        return 'retry'
    if status == 'failed':
        return 'retry_after_fix'
    if status == 'succeeded':
        return 'none'
    return 'inspect'


def main() -> None:
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        observed_at = now_ms()
        write_audit('startup_reconciliation_started', {'observed_at_ms': observed_at})

        non_terminal_issues = fetch_rows(
            conn,
            "SELECT issue_no, id, title, status, active_attempt_no, blocker_summary, updated_at_ms FROM issues WHERE status NOT IN ('closed','failed') ORDER BY issue_no",
        )
        write_audit('non_terminal_issues_scanned', {'count': len(non_terminal_issues), 'issue_nos': [i['issue_no'] for i in non_terminal_issues]})

        active_attempts = fetch_rows(
            conn,
            "SELECT a.id, i.issue_no, a.attempt_no, a.status, a.dispatch_ref, a.started_at_ms, a.ended_at_ms, a.updated_at_ms FROM issue_attempts a JOIN issues i ON i.id=a.issue_id WHERE a.status IN ('dispatching','running') ORDER BY i.issue_no, a.attempt_no",
        )
        write_audit('active_attempts_scanned', {'count': len(active_attempts)})

        latest_attempts = fetch_rows(
            conn,
            "SELECT a.id, i.issue_no, a.attempt_no, a.status, a.dispatch_ref, a.failure_code, a.failure_summary, a.result_summary, a.created_at_ms, a.updated_at_ms FROM issue_attempts a JOIN issues i ON i.id=a.issue_id ORDER BY a.created_at_ms DESC LIMIT 50",
        )

        run_checks = []
        stalled_issues = []
        orphan_attempts = []
        repair_actions = []
        retry_resume = []

        # 8.4 check run existence, simulated by whether an active attempt has dispatch_ref and is still active
        for attempt in active_attempts:
            run_alive = bool(attempt.get('dispatch_ref'))
            check = {
                'issue_no': attempt['issue_no'],
                'attempt_no': attempt['attempt_no'],
                'status': attempt['status'],
                'dispatch_ref': attempt['dispatch_ref'],
                'run_alive_assumption': run_alive,
            }
            run_checks.append(check)
            write_audit('run_checked', check)

        # 8.6 stalled issue detection: ready issues with blocker_summary from failed attempts still awaiting intervention
        for issue in non_terminal_issues:
            if issue['status'] in {'ready', 'blocked'} and issue.get('blocker_summary'):
                stalled = {
                    'issue_no': issue['issue_no'],
                    'status': issue['status'],
                    'blocker_summary': issue['blocker_summary'],
                    'reason': 'retryable issue remains non-terminal with unresolved blocker',
                }
                stalled_issues.append(stalled)
                write_audit('stalled_issue_detected', stalled)

        # 8.7 orphan attempts: terminal attempts whose issue is no longer pointing to them and issue non-terminal
        for attempt in latest_attempts:
            issue = conn.execute('SELECT issue_no, status, active_attempt_no FROM issues WHERE issue_no = ?', (attempt['issue_no'],)).fetchone()
            if issue is None:
                continue
            if attempt['status'] in RETRYABLE_ATTEMPT_STATUSES and issue['status'] not in TERMINAL_ISSUE_STATUSES and issue['active_attempt_no'] != attempt['attempt_no']:
                orphan = {
                    'issue_no': attempt['issue_no'],
                    'attempt_no': attempt['attempt_no'],
                    'attempt_status': attempt['status'],
                    'issue_status': issue['status'],
                }
                orphan_attempts.append(orphan)
                write_audit('orphan_attempt_detected', orphan)

        # 8.5 repair stale ledger status, here only as recommended repair actions against current retryable demo issues
        for issue in non_terminal_issues:
            latest = conn.execute(
                'SELECT attempt_no, status, failure_summary, result_summary FROM issue_attempts WHERE issue_id = (SELECT id FROM issues WHERE issue_no = ?) ORDER BY attempt_no DESC LIMIT 1',
                (issue['issue_no'],),
            ).fetchone()
            if latest is None:
                continue
            strategy = classify_attempt_recovery(dict(latest))
            entry = {
                'issue_no': issue['issue_no'],
                'current_issue_status': issue['status'],
                'latest_attempt_no': latest['attempt_no'],
                'latest_attempt_status': latest['status'],
                'recommended_strategy': strategy,
            }
            retry_resume.append(entry)
            write_audit('retry_resume_classified', entry)
            if issue['status'] == 'ready' and latest['status'] in RETRYABLE_ATTEMPT_STATUSES:
                repair = {
                    'issue_no': issue['issue_no'],
                    'action': 'keep_ready_and_attach_retry_plan',
                    'reason': latest['status'],
                }
                repair_actions.append(repair)
                write_audit('repair_action_recommended', repair)

        metrics = {
            'open': conn.execute("SELECT COUNT(*) FROM issues WHERE status='open'").fetchone()[0],
            'running': conn.execute("SELECT COUNT(*) FROM issues WHERE status='running'").fetchone()[0],
            'blocked': conn.execute("SELECT COUNT(*) FROM issues WHERE status='blocked'").fetchone()[0],
            'waiting_human': conn.execute("SELECT COUNT(*) FROM issues WHERE status IN ('waiting_human_info','waiting_human_action','waiting_human_approval')").fetchone()[0],
            'ready': conn.execute("SELECT COUNT(*) FROM issues WHERE status='ready'").fetchone()[0],
            'closed': conn.execute("SELECT COUNT(*) FROM issues WHERE status='closed'").fetchone()[0],
            'failed': conn.execute("SELECT COUNT(*) FROM issues WHERE status='failed'").fetchone()[0],
        }
        write_audit('metrics_computed', metrics)

        result = {
            'observed_at_ms': observed_at,
            'startup_reconciliation': {
                'performed': True,
                'non_terminal_issue_count': len(non_terminal_issues),
                'active_attempt_count': len(active_attempts),
            },
            'non_terminal_issues': non_terminal_issues,
            'active_attempts': active_attempts,
            'run_checks': run_checks,
            'stalled_issues': stalled_issues,
            'orphan_attempts': orphan_attempts,
            'repair_actions': repair_actions,
            'retry_resume_strategy': retry_resume,
            'metrics': metrics,
            'audit_log_path': str(AUDIT_PATH),
        }
        ARTIFACT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        write_audit('startup_reconciliation_finished', {'artifact': str(ARTIFACT_PATH)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
