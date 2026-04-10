#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
CLI = str(ROOT / 'scripts' / 'agent_team_api_cli.py')


def run_cli(args):
    res = subprocess.run(['python3', CLI, *args], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def main():
    created = run_cli([
        'create-issue',
        '--project-key', 'agent-team-core',
        '--owner-employee-key', 'agent-team-core.pm',
        '--title', 'API adapter cancel live check',
        '--description', 'Validate cancel_execution reaches real adapter.abort.',
        '--acceptance', 'Dispatch should be aborted and attempt marked cancelled.',
    ])
    issue_id = created['issue_id']
    triaged = run_cli([
        'triage-issue',
        issue_id,
        '--assign-employee-key', 'agent-team-core.dev',
    ])
    dispatched = run_cli([
        'dispatch-execution',
        issue_id,
        '--runtime-binding-key', 'agent-team-core.dev.primary',
        '--payload-json', json.dumps({
            'prompt': 'Write 1000 short numbered lines in plain text, starting from 1, and do not stop early. Do not use code blocks.'
        }, ensure_ascii=False),
    ])
    cancelled = run_cli([
        'cancel-execution',
        '--dispatch-ref', dispatched['dispatch_ref'],
        '--reason', 'cancel_live_check',
    ])
    issue_after = run_cli(['get-issue', issue_id])

    print(json.dumps({
        'created': created,
        'triaged': triaged,
        'dispatched': dispatched,
        'cancelled': cancelled,
        'issue_after': issue_after,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
