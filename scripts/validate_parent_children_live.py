#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))

from services.agent_team_service import AgentTeamService  # noqa: E402


def main() -> int:
    svc = AgentTeamService()
    try:
        created_parent = svc.create_issue(
            project_key='agent-team-core',
            owner_employee_key='agent-team-core.pm',
            title='Parent/children live validation parent',
            description_md='Validate waiting_children and parent resume behavior.',
            acceptance_criteria_md='Parent should wait for children, then return to review.',
            priority='p2',
            source_type='system',
            metadata={'validation': 'parent_children_live'},
        )
        parent_issue_id = created_parent['issue_id']
        svc.triage_issue(issue_id=parent_issue_id, assign_employee_key='agent-team-core.pm')

        child1 = svc.create_issue(
            project_key='agent-team-core',
            owner_employee_key='agent-team-core.pm',
            title='Parent/children live validation child 1',
            description_md='child 1',
            acceptance_criteria_md='close child 1',
            priority='p2',
            source_type='system',
            metadata={'validation': 'parent_children_live', 'child_index': 1},
        )
        child2 = svc.create_issue(
            project_key='agent-team-core',
            owner_employee_key='agent-team-core.pm',
            title='Parent/children live validation child 2',
            description_md='child 2',
            acceptance_criteria_md='close child 2',
            priority='p2',
            source_type='system',
            metadata={'validation': 'parent_children_live', 'child_index': 2},
        )
        svc.triage_issue(issue_id=child1['issue_id'], assign_employee_key='agent-team-core.dev')
        svc.triage_issue(issue_id=child2['issue_id'], assign_employee_key='agent-team-core.qa')

        parent = svc.db.get_one('SELECT assigned_employee_id FROM issues WHERE id = ?', (parent_issue_id,))
        now_ms = svc.db.conn.execute('SELECT CAST(strftime(\'%s\',\'now\') AS INTEGER) * 1000').fetchone()[0]
        svc.db.conn.execute(
            'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)',
            (f"rel_parent_child_1_{child1['issue_id']}", parent_issue_id, child1['issue_id'], 'parent_of', parent['assigned_employee_id'], now_ms),
        )
        svc.db.conn.execute(
            'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)',
            (f"rel_parent_child_2_{child2['issue_id']}", parent_issue_id, child2['issue_id'], 'parent_of', parent['assigned_employee_id'], now_ms + 1),
        )
        svc.db.conn.commit()

        waiting = svc.close_issue(issue_id=parent_issue_id, resolution='completed')
        after_wait = svc.get_issue(issue_id=parent_issue_id)['issue']['status']

        svc.close_issue(issue_id=child1['issue_id'], resolution='completed')
        mid = svc.reconcile_dependency_transitions()
        mid_status = svc.get_issue(issue_id=parent_issue_id)['issue']['status']

        svc.close_issue(issue_id=child2['issue_id'], resolution='completed')
        done = svc.reconcile_dependency_transitions()
        final = svc.get_issue(issue_id=parent_issue_id)['issue']

        report = {
            'parent_issue_no': created_parent['issue_no'],
            'child_issue_nos': [child1['issue_no'], child2['issue_no']],
            'close_parent_result': waiting,
            'status_after_parent_close': after_wait,
            'status_after_one_child_closed': mid_status,
            'mid_reconcile': mid,
            'final_reconcile': done,
            'final_parent_status': final['status'],
            'final_parent_metadata': final['metadata_json'],
        }
        for issue_id in [parent_issue_id, child1['issue_id'], child2['issue_id']]:
            svc.db.conn.execute('DELETE FROM issue_activities WHERE issue_id = ?', (issue_id,))
            svc.db.conn.execute('DELETE FROM issue_relations WHERE from_issue_id = ? OR to_issue_id = ?', (issue_id, issue_id))
            svc.db.conn.execute('DELETE FROM issues WHERE id = ?', (issue_id,))
        svc.db.conn.commit()
        report['cleanup'] = 'validation issues deleted'
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        svc.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
