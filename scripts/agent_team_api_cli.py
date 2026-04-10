#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))

from services.agent_team_service import AgentTeamService  # noqa: E402


def print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Agent Team service CLI')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('board-snapshot')

    s = sub.add_parser('list-issues')
    s.add_argument('--project-key', default=None)
    s.add_argument('--status', default=None)

    s = sub.add_parser('get-issue')
    s.add_argument('issue_id')

    s = sub.add_parser('get-attempt-timeline')
    s.add_argument('attempt_id')

    s = sub.add_parser('get-human-queue')

    s = sub.add_parser('create-issue')
    s.add_argument('--project-key', required=True)
    s.add_argument('--owner-employee-key', required=True)
    s.add_argument('--title', required=True)
    s.add_argument('--description', default='')
    s.add_argument('--acceptance', default='')
    s.add_argument('--priority', default='p2')

    s = sub.add_parser('triage-issue')
    s.add_argument('issue_id')
    s.add_argument('--assign-employee-key', required=True)

    s = sub.add_parser('handoff-issue')
    s.add_argument('issue_id')
    s.add_argument('--to-employee-key', required=True)
    s.add_argument('--note', default='')

    s = sub.add_parser('dispatch-execution')
    s.add_argument('issue_id')
    s.add_argument('--runtime-binding-key', required=True)
    s.add_argument('--payload-json', required=True)

    s = sub.add_parser('cancel-execution')
    s.add_argument('--dispatch-ref', required=True)
    s.add_argument('--reason', default='cancelled_by_cli')

    s = sub.add_parser('retry-execution')
    s.add_argument('issue_id')
    s.add_argument('--runtime-binding-key', required=True)
    s.add_argument('--payload-json', required=True)
    s.add_argument('--reason', required=True)

    s = sub.add_parser('close-issue')
    s.add_argument('issue_id')
    s.add_argument('--resolution', default='completed')

    s = sub.add_parser('enqueue-human')
    s.add_argument('issue_id')
    s.add_argument('--human-type', choices=['info', 'action', 'approval'], required=True)
    s.add_argument('--prompt', required=True)
    s.add_argument('--required-input', required=True)

    s = sub.add_parser('resolve-human')
    s.add_argument('issue_id')
    s.add_argument('--resolution', choices=['approve', 'reject', 'needs_info'], required=True)
    s.add_argument('--note', default='')

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    svc = AgentTeamService()
    try:
        if args.cmd == 'board-snapshot':
            print_json(svc.get_board_snapshot())
        elif args.cmd == 'list-issues':
            print_json(svc.list_issues(project_key=args.project_key, status=args.status))
        elif args.cmd == 'get-issue':
            print_json(svc.get_issue(issue_id=args.issue_id))
        elif args.cmd == 'get-attempt-timeline':
            print_json(svc.get_attempt_timeline(attempt_id=args.attempt_id))
        elif args.cmd == 'get-human-queue':
            print_json(svc.get_human_queue())
        elif args.cmd == 'create-issue':
            print_json(
                svc.create_issue(
                    project_key=args.project_key,
                    owner_employee_key=args.owner_employee_key,
                    title=args.title,
                    description_md=args.description,
                    acceptance_criteria_md=args.acceptance,
                    priority=args.priority,
                )
            )
        elif args.cmd == 'triage-issue':
            print_json(svc.triage_issue(issue_id=args.issue_id, assign_employee_key=args.assign_employee_key))
        elif args.cmd == 'handoff-issue':
            print_json(svc.handoff_issue(issue_id=args.issue_id, to_employee_key=args.to_employee_key, note=args.note))
        elif args.cmd == 'dispatch-execution':
            print_json(
                svc.dispatch_execution(
                    issue_id=args.issue_id,
                    runtime_binding_key=args.runtime_binding_key,
                    payload=json.loads(args.payload_json),
                )
            )
        elif args.cmd == 'cancel-execution':
            print_json(svc.cancel_execution(dispatch_ref=args.dispatch_ref, reason=args.reason))
        elif args.cmd == 'retry-execution':
            print_json(
                svc.retry_execution(
                    issue_id=args.issue_id,
                    runtime_binding_key=args.runtime_binding_key,
                    payload=json.loads(args.payload_json),
                    reason=args.reason,
                )
            )
        elif args.cmd == 'close-issue':
            print_json(svc.close_issue(issue_id=args.issue_id, resolution=args.resolution))
        elif args.cmd == 'enqueue-human':
            print_json(
                svc.enqueue_human(
                    issue_id=args.issue_id,
                    human_type=args.human_type,
                    prompt=args.prompt,
                    required_input=args.required_input,
                )
            )
        elif args.cmd == 'resolve-human':
            print_json(svc.resolve_human_action(issue_id=args.issue_id, resolution=args.resolution, note=args.note))
        else:
            raise SystemExit(f'Unknown command: {args.cmd}')
        return 0
    finally:
        svc.close()


if __name__ == '__main__':
    raise SystemExit(main())
