#!/usr/bin/env python3
import json
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
ROOT = THIS_FILE.parents[1]
sys.path.insert(0, str(ROOT))

from services.config import ROOT as CONFIG_ROOT  # noqa: E402
from services.agent_team_service import AgentTeamService  # noqa: E402

OUT = CONFIG_ROOT / 'ui' / 'board' / 'issues.json'
OUT.parent.mkdir(parents=True, exist_ok=True)


def main():
    svc = AgentTeamService()
    try:
        issues = svc.list_issues()['items']
        details = []
        for item in issues:
            detail = svc.get_issue(issue_id=item['id'])
            attempts = detail['attempts']
            callbacks_by_attempt = detail.get('callbacks_by_attempt') or {}
            timelines = {}
            for attempt in attempts:
                timelines[attempt['id']] = svc.get_attempt_timeline(attempt_id=attempt['id'])['items']
            outgoing = svc.db.fetch_all(
                '''SELECT ir.id, ir.relation_type, ir.created_at_ms, i.id AS related_issue_id, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
                   FROM issue_relations ir
                   JOIN issues i ON i.id = ir.to_issue_id
                   WHERE ir.from_issue_id = ?
                   ORDER BY ir.created_at_ms DESC''',
                (item['id'],),
            )
            incoming = svc.db.fetch_all(
                '''SELECT ir.id, ir.relation_type, ir.created_at_ms, i.id AS related_issue_id, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
                   FROM issue_relations ir
                   JOIN issues i ON i.id = ir.from_issue_id
                   WHERE ir.to_issue_id = ?
                   ORDER BY ir.created_at_ms DESC''',
                (item['id'],),
            )
            details.append({
                'issue': detail['issue'],
                'attempts': attempts,
                'callbacks_by_attempt': callbacks_by_attempt,
                'timelines': timelines,
                'activities': svc.get_issue_activity(issue_id=item['id'])['items'],
                'relations': {
                    'outgoing': [dict(r) for r in outgoing],
                    'incoming': [dict(r) for r in incoming],
                },
                'dependencies': detail.get('dependencies') or {'blocking': [], 'blocked_dependents': []},
            })
    finally:
        svc.close()
    OUT.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print(OUT)


if __name__ == '__main__':
    main()
