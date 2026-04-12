#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402


def main():
    svc = AgentTeamService()
    try:
        created = svc.create_issue(
            project_key='agent-team-core',
            owner_employee_key='shared.ceo',
            title='Canonical session routing verification',
            description_md='Verify new dispatch lands in canonical role-project session.',
            acceptance_criteria_md='The first dispatch should target the canonical PM project session.',
            priority='p2',
            metadata={
                'dispatch_instruction_pm': 'As PM, do the minimum review and return JSON with suggested_next_role=close.',
                'suggested_next_role': 'pm',
                'risk_level': 'normal',
                'source': 'canonical_routing_check'
            },
        )
        triaged = svc.triage_issue(issue_id=created['issue_id'], assign_employee_key='agent-team-core.pm')
        print(json.dumps({'created': created, 'triaged': triaged}, ensure_ascii=False, indent=2))
    finally:
        svc.close()


if __name__ == '__main__':
    main()
