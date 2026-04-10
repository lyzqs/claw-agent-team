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


def run_agent(agent_id: str, message: str):
    res = subprocess.run(['openclaw', 'agent', '--agent', agent_id, '--message', message, '--json'], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def main():
    created = run_cli([
        'create-issue',
        '--project-key', 'agent-team-core',
        '--owner-employee-key', 'agent-team-core.pm',
        '--title', 'Minimal PM-Dev-QA-CEO chain live check',
        '--description', 'PM creates, Dev executes, QA reviews, CEO approves and closes.',
        '--acceptance', 'A real issue should flow through PM -> Dev -> QA -> CEO using real OpenClaw agents.',
    ])
    issue_id = created['issue_id']

    pm_agent = run_agent('agent-team-pm', 'Reply with exactly PM_QA_CHAIN_READY and nothing else.')
    triaged = run_cli([
        'triage-issue', issue_id,
        '--assign-employee-key', 'agent-team-core.dev',
    ])

    dev_agent = run_agent('agent-team-dev', 'Reply with exactly DEV_QA_CHAIN_READY and nothing else.')
    dispatched = run_cli([
        'dispatch-execution', issue_id,
        '--runtime-binding-key', 'agent-team-core.dev.primary',
        '--payload-json', json.dumps({'prompt': 'Reply with exactly DEV_QA_CHAIN_DONE and nothing else.'}, ensure_ascii=False),
    ])

    svc = AgentTeamService()
    try:
        observed = svc.observe_execution(
            dispatch_ref=dispatched['dispatch_ref'],
            expected_text='DEV_QA_CHAIN_DONE',
            timeout_seconds=45,
            close_issue_on_success=False,
        )
    finally:
        svc.close()

    handoff_qa = run_cli([
        'handoff-issue', issue_id,
        '--to-employee-key', 'agent-team-core.qa',
        '--note', 'Dev completed execution; awaiting QA review.',
    ])
    qa_agent = run_agent('agent-team-qa', 'Reply with exactly QA_CHAIN_APPROVED and nothing else.')

    handoff_ceo = run_cli([
        'handoff-issue', issue_id,
        '--to-employee-key', 'shared.ceo',
        '--note', 'QA approved the result; awaiting CEO approval.',
    ])
    ceo_agent = run_agent('agent-team-ceo', 'Reply with exactly CEO_QA_CHAIN_APPROVED and nothing else.')

    closed = run_cli([
        'close-issue', issue_id,
        '--resolution', 'completed',
    ])
    issue_after = run_cli(['get-issue', issue_id])

    print(json.dumps({
        'created': created,
        'pm_agent': pm_agent,
        'triaged': triaged,
        'dev_agent': dev_agent,
        'dispatched': dispatched,
        'observed': observed,
        'handoff_qa': handoff_qa,
        'qa_agent': qa_agent,
        'handoff_ceo': handoff_ceo,
        'ceo_agent': ceo_agent,
        'closed': closed,
        'issue_after': issue_after,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
