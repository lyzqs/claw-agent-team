from __future__ import annotations

import json
from typing import Any

from .activity import record_issue_activity
from .db import now_ms


def merge_json_object(raw: str | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                base = value
        except Exception:
            base = {}
    if patch:
        base.update(patch)
    return base


class DependencyService:
    def __init__(self, db):
        self.db = db

    def reconcile_dependency_transitions(self) -> dict[str, Any]:
        ts = now_ms()
        dependency_released: list[dict[str, Any]] = []
        parent_progressed: list[dict[str, Any]] = []

        blocked_rows = self.db.fetch_all(
            '''SELECT i.id, i.assigned_employee_id
               FROM issues i
               WHERE i.status IN ('triaged', 'ready', 'review', 'blocked', 'waiting_children', 'waiting_recovery_completion')'''
        )
        for row in blocked_rows:
            open_deps = self.db.conn.execute(
                '''SELECT COUNT(*)
                   FROM issue_relations ir
                   JOIN issues dep ON dep.id = ir.to_issue_id
                   WHERE ir.from_issue_id = ? AND ir.relation_type = 'blocked_by' AND dep.status != 'closed' ''',
                (row['id'],),
            ).fetchone()[0]
            if int(open_deps or 0) == 0:
                current = self.db.get_one('SELECT status FROM issues WHERE id = ?', (row['id'],))
                if current['status'] == 'blocked':
                    self.db.conn.execute(
                        'UPDATE issues SET status = ?, blocker_summary = NULL, updated_at_ms = ? WHERE id = ?',
                        ('ready', ts, row['id']),
                    )
                    record_issue_activity(
                        self.db.conn,
                        now_ms=ts,
                        issue_id=row['id'],
                        action_type='dependency_satisfied',
                        summary='Blocked issue returned to ready after dependencies closed',
                        actor_employee_id=row['assigned_employee_id'],
                        details={'new_status': 'ready'},
                    )
                    dependency_released.append({'issue_id': row['id'], 'new_status': 'ready'})

        waiting_children_rows = self.db.fetch_all(
            '''SELECT i.id, i.assigned_employee_id, i.metadata_json
               FROM issues i
               WHERE i.status = 'waiting_children' '''
        )
        for row in waiting_children_rows:
            open_children = self.db.conn.execute(
                '''SELECT COUNT(*)
                   FROM issue_relations ir
                   JOIN issues child ON child.id = ir.to_issue_id
                   WHERE ir.from_issue_id = ? AND ir.relation_type = 'parent_of' AND child.status != 'closed' ''',
                (row['id'],),
            ).fetchone()[0]
            if int(open_children or 0) == 0:
                metadata = merge_json_object(row['metadata_json'], {})
                resume_role = metadata.get('resume_role') or 'ceo'
                self.db.conn.execute(
                    'UPDATE issues SET status = ?, blocker_summary = NULL, updated_at_ms = ? WHERE id = ?',
                    ('review', ts, row['id']),
                )
                record_issue_activity(
                    self.db.conn,
                    now_ms=ts,
                    issue_id=row['id'],
                    action_type='child_issues_completed',
                    summary='All child issues completed; parent returned to review',
                    actor_employee_id=row['assigned_employee_id'],
                    details={'new_status': 'review', 'resume_role': resume_role, 'parent_wait_strategy': metadata.get('parent_wait_strategy')},
                )
                parent_progressed.append({'issue_id': row['id'], 'new_status': 'review', 'resume_role': resume_role})

        self.db.commit()
        return {
            'dependency_released': dependency_released,
            'parent_progressed': parent_progressed,
        }
