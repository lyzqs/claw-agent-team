#!/usr/bin/env python3
"""Run two minimal Human Queue roundtrip demos on the Agent Team prototype.

Cases:
1. waiting_human_info -> returned_to_agent_queue -> real execution succeeds
2. waiting_human_approval -> returned_to_agent_queue -> real execution succeeds
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
WORK_ROOT = Path('/root/.openclaw/workspace-agent-team')
ARTIFACT_DIR = WORK_ROOT / 'evidence' / 'phase6'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_PATH = ARTIFACT_DIR / 'human_queue_roundtrip_demo_result.json'

sys.path.insert(0, str(PROTO_ROOT))

from execution_adapter import DB_PATH, Ledger, OpenClawExecutionAdapter, now_ms, uid  # type: ignore  # noqa: E402


def set_waiting_human(ledger: Ledger, issue_id: str, *, status: str, blocker: str, required_input: str) -> None:
    ts = now_ms()
    ledger.conn.execute(
        'UPDATE issues SET status = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
        (status, blocker, required_input, ts, issue_id),
    )


def return_to_agent_queue(ledger: Ledger, issue_id: str) -> None:
    ts = now_ms()
    ledger.conn.execute(
        'UPDATE issues SET status = ?, blocker_summary = NULL, required_human_input = NULL, updated_at_ms = ? WHERE id = ?',
        ('ready', ts, issue_id),
    )


def fetch_issue_summary(ledger: Ledger, issue_id: str) -> dict[str, Any]:
    issue = ledger.get_one(
        'SELECT issue_no, title, status, active_attempt_no, blocker_summary, required_human_input FROM issues WHERE id = ?',
        (issue_id,),
    )
    checkpoints = [
        dict(r)
        for r in ledger.conn.execute(
            'SELECT checkpoint_no, kind, summary, next_action FROM issue_checkpoints WHERE issue_id = ? ORDER BY checkpoint_no',
            (issue_id,),
        ).fetchall()
    ]
    return {
        'issue': dict(issue),
        'checkpoints': checkpoints,
    }


def run_execution_after_return(
    ledger: Ledger,
    *,
    issue_id: str,
    assigned_employee_id: str,
    runtime_binding_id: str,
    session_key: str,
    created_by_employee_id: str,
    marker_prefix: str,
    checkpoint_summary: str,
) -> dict[str, Any]:
    dispatch_id = uid('dispatch')
    marker = f'{marker_prefix}_{dispatch_id}'
    prompt = f'Reply with exactly {marker} and nothing else.'
    adapter = OpenClawExecutionAdapter(session_key)
    dispatch = adapter.dispatch(prompt=prompt, dispatch_id=dispatch_id)
    attempt_id, attempt_no = ledger.create_attempt(
        issue_id=issue_id,
        assigned_employee_id=assigned_employee_id,
        runtime_binding_id=runtime_binding_id,
        dispatch_ref=dispatch['dispatch_ref'],
        input_snapshot={'prompt': prompt, 'marker': marker, 'session_key': session_key},
    )
    ledger.mark_running(issue_id, attempt_id)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=attempt_id,
        created_by_employee_id=created_by_employee_id,
        kind='handoff',
        summary=checkpoint_summary,
        details_md=f'dispatch_ref={dispatch["dispatch_ref"]}',
        next_action='Wait for exact marker from execution adapter',
        percent_complete=70,
    )
    ledger.commit()

    wait_result = adapter.wait_for_exact_text(expected_text=marker, timeout_seconds=45)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=attempt_id,
        created_by_employee_id=created_by_employee_id,
        kind='progress',
        summary='Execution resumed successfully after human queue roundtrip',
        details_md=f'assistant returned exact marker: {marker}',
        next_action='Close issue',
        percent_complete=95,
    )
    ledger.mark_succeeded(
        issue_id=issue_id,
        attempt_id=attempt_id,
        result_summary='Human queue roundtrip demo completed successfully',
        output_snapshot={'assistant_text': wait_result['assistant_text'], 'timestamp': wait_result['timestamp']},
    )
    ledger.commit()
    return {
        'dispatch': dispatch,
        'attempt_no': attempt_no,
        'wait_result': wait_result,
    }


def info_roundtrip_case(ledger: Ledger, project_id: str, pm_id: str, dev_id: str, dev_runtime_id: str, dev_session_key: str) -> dict[str, Any]:
    issue_id, issue_no = ledger.create_issue(
        project_id=project_id,
        owner_employee_id=pm_id,
        title='Human info roundtrip demo',
        description_md='Demonstrate waiting_human_info -> returned_to_agent_queue -> resumed execution.',
        acceptance_criteria_md='Issue should wait for missing info, receive it, return to ready, then complete through the real execution adapter.',
        metadata={'demo': 'human_info_roundtrip'},
    )
    ledger.triage_issue(issue_id, pm_id)
    ledger.assign_issue(issue_id, dev_id)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=pm_id,
        kind='human_request',
        summary='Need missing acceptance input from human',
        details_md='Please provide the exact canonical sample phrase for validation.',
        next_action='Wait for human info',
        percent_complete=20,
    )
    set_waiting_human(
        ledger,
        issue_id,
        status='waiting_human_info',
        blocker='Missing canonical sample phrase for validation.',
        required_input='Provide one exact accepted sample phrase.',
    )
    ledger.commit()

    before_return = fetch_issue_summary(ledger, issue_id)

    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=pm_id,
        kind='review',
        summary='Human provided the missing info',
        details_md='Canonical sample phrase supplied: 君の未来。',
        next_action='Return to agent queue',
        percent_complete=45,
    )
    return_to_agent_queue(ledger, issue_id)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=pm_id,
        kind='handoff',
        summary='returned_to_agent_queue after info supplied',
        details_md='Issue can resume normal execution.',
        next_action='Dispatch to dev runtime',
        percent_complete=55,
    )
    ledger.commit()

    execution = run_execution_after_return(
        ledger,
        issue_id=issue_id,
        assigned_employee_id=dev_id,
        runtime_binding_id=dev_runtime_id,
        session_key=dev_session_key,
        created_by_employee_id=dev_id,
        marker_prefix='OK_HUMAN_INFO',
        checkpoint_summary='Dispatch after waiting_human_info resolution',
    )
    after_close = fetch_issue_summary(ledger, issue_id)
    return {
        'issue_id': issue_id,
        'issue_no': issue_no,
        'waiting_state_snapshot': before_return,
        'execution': execution,
        'final_snapshot': after_close,
    }


def approval_roundtrip_case(ledger: Ledger, project_id: str, ceo_id: str, dev_id: str, dev_runtime_id: str, dev_session_key: str) -> dict[str, Any]:
    issue_id, issue_no = ledger.create_issue(
        project_id=project_id,
        owner_employee_id=ceo_id,
        title='Human approval roundtrip demo',
        description_md='Demonstrate waiting_human_approval -> returned_to_agent_queue -> resumed execution.',
        acceptance_criteria_md='Issue should wait for approval, receive it, return to ready, then complete through the real execution adapter.',
        metadata={'demo': 'human_approval_roundtrip'},
    )
    ledger.triage_issue(issue_id, ceo_id)
    ledger.assign_issue(issue_id, dev_id)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=ceo_id,
        kind='human_request',
        summary='Need explicit human approval before continuing',
        details_md='Approve outbound publish simulation before dispatching the next step.',
        next_action='Wait for human approval',
        percent_complete=20,
    )
    set_waiting_human(
        ledger,
        issue_id,
        status='waiting_human_approval',
        blocker='Publish simulation requires explicit approval.',
        required_input='Approve or reject the outbound publish step.',
    )
    ledger.commit()

    before_return = fetch_issue_summary(ledger, issue_id)

    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=ceo_id,
        kind='review',
        summary='Human approved the requested action',
        details_md='Approval granted for outbound publish simulation.',
        next_action='Return to agent queue',
        percent_complete=45,
    )
    return_to_agent_queue(ledger, issue_id)
    ledger.record_checkpoint(
        issue_id=issue_id,
        attempt_id=None,
        created_by_employee_id=ceo_id,
        kind='handoff',
        summary='returned_to_agent_queue after approval granted',
        details_md='Issue can resume normal execution.',
        next_action='Dispatch to dev runtime',
        percent_complete=55,
    )
    ledger.commit()

    execution = run_execution_after_return(
        ledger,
        issue_id=issue_id,
        assigned_employee_id=dev_id,
        runtime_binding_id=dev_runtime_id,
        session_key=dev_session_key,
        created_by_employee_id=dev_id,
        marker_prefix='OK_HUMAN_APPROVAL',
        checkpoint_summary='Dispatch after waiting_human_approval resolution',
    )
    after_close = fetch_issue_summary(ledger, issue_id)
    return {
        'issue_id': issue_id,
        'issue_no': issue_no,
        'waiting_state_snapshot': before_return,
        'execution': execution,
        'final_snapshot': after_close,
    }


def main() -> None:
    ledger = Ledger(DB_PATH)
    try:
        project = ledger.get_one('SELECT id, project_key FROM projects WHERE project_key = ?', ('agent-team-core',))
        pm = ledger.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', ('agent-team-core.pm',))
        dev = ledger.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', ('agent-team-core.dev',))
        ceo = ledger.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', ('shared.ceo',))
        dev_runtime = ledger.get_one('SELECT id, session_key FROM runtime_bindings WHERE binding_key = ?', ('agent-team-core.dev.primary',))

        info_case = info_roundtrip_case(
            ledger,
            project_id=project['id'],
            pm_id=pm['id'],
            dev_id=dev['id'],
            dev_runtime_id=dev_runtime['id'],
            dev_session_key=dev_runtime['session_key'],
        )
        approval_case = approval_roundtrip_case(
            ledger,
            project_id=project['id'],
            ceo_id=ceo['id'],
            dev_id=dev['id'],
            dev_runtime_id=dev_runtime['id'],
            dev_session_key=dev_runtime['session_key'],
        )

        result = {
            'project_key': project['project_key'],
            'cases': {
                'human_info_roundtrip': info_case,
                'human_approval_roundtrip': approval_case,
            },
            'agent_queue_count': ledger.conn.execute('SELECT COUNT(*) FROM v_agent_queue').fetchone()[0],
            'human_queue_count': ledger.conn.execute('SELECT COUNT(*) FROM v_human_queue').fetchone()[0],
        }
        ARTIFACT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
