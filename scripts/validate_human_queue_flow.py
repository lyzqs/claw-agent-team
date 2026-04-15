#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402


CLI = str(ROOT / 'scripts' / 'agent_team_api_cli.py')
EXPORT_BOARD = str(ROOT / 'scripts' / 'export_board_snapshot.py')
EXPORT_ISSUES = str(ROOT / 'scripts' / 'export_issue_details.py')


def run_cli(args):
    res = subprocess.run(['python3', CLI, *args], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def refresh_exports():
    subprocess.run(['python3', EXPORT_BOARD], check=True, capture_output=True, text=True)
    subprocess.run(['python3', EXPORT_ISSUES], check=True, capture_output=True, text=True)


def main():
    svc = AgentTeamService()
    try:
        target = next((x for x in svc.list_issues()['items'] if x['issue_no'] == 36), None)
        if not target:
            raise SystemExit('issue #36 not found')
        issue_id = target['id']
        detail_before = svc.get_issue(issue_id=issue_id)
        before = {
            'issue_status': detail_before['issue']['status'],
            'human_queue_total': svc.get_human_queue()['total'],
            'last_attempt_no': detail_before['attempts'][-1]['attempt_no'],
        }
        live_attempts = [a for a in detail_before['attempts'] if a['status'] in ('dispatching', 'running')]
        if live_attempts:
            last_attempt = live_attempts[-1]
            observed = svc.observe_execution(
                dispatch_ref=last_attempt['dispatch_ref'],
                timeout_seconds=1,
                close_issue_on_success=False,
            )
        else:
            last_attempt = detail_before['attempts'][-1]
            observed = {'status': 'noop', 'reason': 'no live attempts', 'last_attempt_status': last_attempt['status']}
        detail_after = svc.get_issue(issue_id=issue_id)
        human_queue = svc.get_human_queue()
    finally:
        svc.close()

    refresh_exports()

    print(json.dumps({
        'before': before,
        'observed': observed,
        'after': {
            'issue_status': detail_after['issue']['status'],
            'blocker_summary': detail_after['issue']['blocker_summary'],
            'required_human_input': detail_after['issue']['required_human_input'],
            'human_queue_total': human_queue['total'],
            'human_queue_items': human_queue['items'],
            'attempt_count': len(detail_after['attempts']),
            'last_attempt_no': detail_after['attempts'][-1]['attempt_no'],
        }
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
