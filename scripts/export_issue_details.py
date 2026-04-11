#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402

OUT = ROOT / 'ui' / 'board' / 'issues.json'
OUT.parent.mkdir(parents=True, exist_ok=True)


def main():
    svc = AgentTeamService()
    try:
        issues = svc.list_issues()['items']
        details = []
        for item in issues:
            detail = svc.get_issue(issue_id=item['id'])
            attempts = detail['attempts']
            timelines = {}
            for attempt in attempts:
                timelines[attempt['id']] = svc.get_attempt_timeline(attempt_id=attempt['id'])['items']
            details.append({
                'issue': detail['issue'],
                'attempts': attempts,
                'timelines': timelines,
                'activities': svc.get_issue_activity(issue_id=item['id'])['items'],
            })
    finally:
        svc.close()
    OUT.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print(OUT)


if __name__ == '__main__':
    main()
