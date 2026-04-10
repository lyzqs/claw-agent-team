#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402

CLI = str(ROOT / 'scripts' / 'agent_team_api_cli.py')


def run_cli(args):
    res = subprocess.run(['python3', CLI, *args], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def main():
    created = run_cli([
        'create-issue',
        '--project-key', 'agent-team-core',
        '--owner-employee-key', 'agent-team-core.pm',
        '--title', 'API adapter retry live check',
        '--description', 'Validate retry_execution creates a new attempt and reaches real adapter dispatch.',
        '--acceptance', 'Retry should dispatch a second real run and observe success.',
    ])
    issue_id = created['issue_id']
    triaged = run_cli([
        'triage-issue',
        issue_id,
        '--assign-employee-key', 'agent-team-core.dev',
    ])

    first_dispatch = run_cli([
        'dispatch-execution',
        issue_id,
        '--runtime-binding-key', 'agent-team-core.dev.primary',
        '--payload-json', json.dumps({
            'prompt': 'Write 1000 short numbered lines in plain text, starting from 1, and do not stop early. Do not use code blocks.'
        }, ensure_ascii=False),
    ])

    cancelled = run_cli([
        'cancel-execution',
        '--dispatch-ref', first_dispatch['dispatch_ref'],
        '--reason', 'prepare_retry_live_check',
    ])

    retried = run_cli([
        'retry-execution',
        issue_id,
        '--runtime-binding-key', 'agent-team-core.dev.primary',
        '--payload-json', json.dumps({
            'prompt': 'Reply with exactly OK_API_RETRY_LIVE and nothing else.'
        }, ensure_ascii=False),
        '--reason', 'retry_live_check',
    ])

    svc = AgentTeamService()
    try:
        observed = svc.observe_execution(
            dispatch_ref=retried['dispatch_ref'],
            expected_text='OK_API_RETRY_LIVE',
            timeout_seconds=45,
        )
    finally:
        svc.close()

    issue_after = run_cli(['get-issue', issue_id])

    print(json.dumps({
        'created': created,
        'triaged': triaged,
        'first_dispatch': first_dispatch,
        'cancelled': cancelled,
        'retried': retried,
        'observed': observed,
        'issue_after': issue_after,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
