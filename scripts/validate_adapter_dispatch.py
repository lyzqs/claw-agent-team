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
        '--title', 'API adapter dispatch live check',
        '--description', 'Validate dispatch_execution now reaches real adapter.',
        '--acceptance', 'Dispatch should create a real run and allow observation/cancel.',
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
        '--payload-json', json.dumps({'prompt': 'Reply with exactly OK_API_ADAPTER_LIVE and nothing else.'}, ensure_ascii=False),
    ])

    svc = AgentTeamService()
    try:
        observed = svc.observe_execution(
            dispatch_ref=dispatched['dispatch_ref'],
            expected_text='OK_API_ADAPTER_LIVE',
            timeout_seconds=45,
        )
    finally:
        svc.close()

    print(json.dumps({
        'created': created,
        'triaged': triaged,
        'dispatched': dispatched,
        'observed': observed,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
