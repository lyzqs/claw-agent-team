#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService, ValidationError  # noqa: E402


def main():
    svc = AgentTeamService()
    try:
        created = svc.create_issue(
            project_key='agent-team-core',
            owner_employee_key='agent-team-core.pm',
            title='Routing policy validation issue',
            description_md='Validate constrained dynamic routing.',
            acceptance_criteria_md='Allowed route succeeds, forbidden route is rejected.',
        )
        issue_id = created['issue_id']
        triaged = svc.triage_issue(issue_id=issue_id, assign_employee_key='agent-team-core.dev')

        allowed = svc.handoff_issue(
            issue_id=issue_id,
            to_employee_key='agent-team-core.qa',
            note='Dev can hand off to QA.',
            issue_type='normal',
            risk_level='normal',
        )

        forbidden_error = None
        try:
            svc.handoff_issue(
                issue_id=issue_id,
                to_employee_key='agent-team-core.pm',
                note='QA can return to PM? should be rejected by current routing policy.',
                issue_type='normal',
                risk_level='normal',
            )
        except ValidationError as e:
            forbidden_error = str(e)

        high_risk_error = None
        svc.handoff_issue(
            issue_id=issue_id,
            to_employee_key='agent-team-core.dev',
            note='Reset issue back to dev for high-risk validation.',
            issue_type='normal',
            risk_level='normal',
        )
        try:
            svc.handoff_issue(
                issue_id=issue_id,
                to_employee_key='agent-team-core.qa',
                note='High-risk route should be rejected unless escalated.',
                issue_type='normal',
                risk_level='high',
            )
        except ValidationError as e:
            high_risk_error = str(e)

        print(json.dumps({
            'created': created,
            'triaged': triaged,
            'allowed': allowed,
            'forbidden_error': forbidden_error,
            'high_risk_error': high_risk_error,
        }, ensure_ascii=False, indent=2))
    finally:
        svc.close()


if __name__ == '__main__':
    main()
