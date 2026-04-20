from __future__ import annotations

import json
from typing import Any

from .activity import fetch_issue_activity


def parse_schedule_config(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class BoardQueryService:
    def __init__(self, db):
        self.db = db

    def list_issues(self, *, project_key: str | None = None, status: str | None = None) -> dict[str, Any]:
        sql = '''SELECT i.id, i.issue_no, i.title, i.priority, i.status, i.active_attempt_no, p.project_key,
                        ei.employee_key AS assigned_employee_key,
                        rt.template_key AS assigned_role,
                        rb.agent_id,
                        rb.session_key,
                        EXISTS (
                          SELECT 1 FROM issue_relations ir
                          JOIN issues dep ON dep.id = ir.to_issue_id
                          WHERE ir.from_issue_id = i.id AND ir.relation_type = 'blocked_by' AND dep.status != 'closed'
                        ) AS has_open_dependencies
                 FROM issues i
                 JOIN projects p ON p.id = i.project_id
                 LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
                 LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
                 LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
                 WHERE p.id IS NOT NULL'''
        params: list[Any] = []
        if project_key:
            sql += ' AND p.project_key = ?'
            params.append(project_key)
        if status:
            sql += ' AND i.status = ?'
            params.append(status)
        sql += ' ORDER BY i.issue_no DESC'
        rows = self.db.fetch_all(sql, tuple(params))
        return {
            'items': [dict(r) for r in rows],
            'total': len(rows),
        }

    def get_issue(self, *, issue_id: str) -> dict[str, Any]:
        issue = self.db.get_one(
            '''SELECT i.*, p.project_key
               FROM issues i
               LEFT JOIN projects p ON p.id = i.project_id
               WHERE i.id = ?''',
            (issue_id,),
        )
        attempts = self.db.fetch_all('SELECT * FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no', (issue_id,))
        callbacks_by_attempt: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            rows = self.db.fetch_all(
                '''SELECT id, flow_id, callback_token, phase, idempotency_key, payload_json, accepted, accepted_reason, created_at_ms
                   FROM issue_attempt_callbacks
                   WHERE attempt_id = ?
                   ORDER BY created_at_ms''',
                (attempt['id'],),
            )
            callbacks_by_attempt[str(attempt['id'])] = [dict(r) for r in rows]
        blocking = self.db.fetch_all(
            '''SELECT ir.id, ir.relation_type, ir.created_at_ms, dep.id AS related_issue_id, dep.issue_no AS related_issue_no, dep.title AS related_issue_title, dep.status AS related_issue_status
               FROM issue_relations ir
               JOIN issues dep ON dep.id = ir.to_issue_id
               WHERE ir.from_issue_id = ? AND ir.relation_type = 'blocked_by'
               ORDER BY ir.created_at_ms DESC''',
            (issue_id,),
        )
        blocked_dependents = self.db.fetch_all(
            '''SELECT ir.id, ir.relation_type, ir.created_at_ms, src.id AS related_issue_id, src.issue_no AS related_issue_no, src.title AS related_issue_title, src.status AS related_issue_status
               FROM issue_relations ir
               JOIN issues src ON src.id = ir.from_issue_id
               WHERE ir.to_issue_id = ? AND ir.relation_type = 'blocked_by'
               ORDER BY ir.created_at_ms DESC''',
            (issue_id,),
        )
        return {
            'issue': dict(issue),
            'attempts': [dict(r) for r in attempts],
            'callbacks_by_attempt': callbacks_by_attempt,
            'dependencies': {
                'blocking': [dict(r) for r in blocking],
                'blocked_dependents': [dict(r) for r in blocked_dependents],
            },
        }

    def get_attempt_timeline(self, *, attempt_id: str) -> dict[str, Any]:
        rows = self.db.fetch_all(
            'SELECT checkpoint_no, kind, summary, details_md, next_action, percent_complete, created_at_ms FROM issue_checkpoints WHERE attempt_id = ? ORDER BY checkpoint_no',
            (attempt_id,),
        )
        return {
            'items': [dict(r) for r in rows],
            'total': len(rows),
        }

    def get_agent_workload(self) -> dict[str, Any]:
        rows = self.db.fetch_all(
            '''SELECT i.id AS issue_id,
                      i.issue_no,
                      i.title,
                      i.status,
                      i.priority,
                      ei.employee_key,
                      ei.display_name,
                      rt.template_key AS role,
                      rb.agent_id,
                      rb.session_key,
                      rb.binding_key,
                      EXISTS (
                        SELECT 1 FROM issue_relations ir
                        JOIN issues dep ON dep.id = ir.to_issue_id
                        WHERE ir.from_issue_id = i.id AND ir.relation_type = 'blocked_by' AND dep.status != 'closed'
                      ) AS has_open_dependencies
               FROM issues i
               LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
               LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
               ORDER BY rb.agent_id, i.issue_no'''
        )
        grouped: dict[str, Any] = {}
        for row in rows:
            agent_id = row['agent_id'] or 'unassigned'
            grouped.setdefault(agent_id, {
                'agent_id': agent_id,
                'session_key': row['session_key'],
                'binding_key': row['binding_key'],
                'role': row['role'],
                'employee_key': row['employee_key'],
                'display_name': row['display_name'],
                'active_issue_count': 0,
                'issues': [],
            })
            if row['status'] in {'dispatching', 'running'}:
                grouped[agent_id]['active_issue_count'] += 1
            grouped[agent_id]['issues'].append({
                'issue_id': row['issue_id'],
                'issue_no': row['issue_no'],
                'title': row['title'],
                'status': row['status'],
                'priority': row['priority'],
                'has_open_dependencies': bool(row['has_open_dependencies']),
            })
        return {
            'items': list(grouped.values()),
            'total': len(grouped),
        }

    def get_board_snapshot(self) -> dict[str, Any]:
        agent_queue = self.db.fetch_all('SELECT * FROM v_agent_queue ORDER BY updated_at_ms DESC')
        human_queue = self.db.fetch_all('SELECT * FROM v_human_queue ORDER BY updated_at_ms DESC')
        scheduled_items = self.db.fetch_all(
            '''SELECT si.id, si.title, si.priority, si.route_role, si.schedule_kind, si.schedule_config_json,
                      si.enabled, si.next_run_at_ms, si.last_run_at_ms, si.last_issue_id, si.last_issue_no,
                      si.last_error, si.updated_at_ms, p.project_key, p.name AS project_name,
                      oe.employee_key AS owner_employee_key
               FROM scheduled_issues si
               JOIN projects p ON p.id = si.project_id
               LEFT JOIN employee_instances oe ON oe.id = si.owner_employee_id
               ORDER BY si.updated_at_ms DESC, si.created_at_ms DESC'''
        )
        employees = self.db.fetch_all(
            '''SELECT ei.employee_key,
                      ei.display_name,
                      ei.employment_scope,
                      p.project_key,
                      rt.template_key AS role,
                      ei.status,
                      rb.agent_id,
                      rb.session_key,
                      rb.binding_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN projects p ON p.id = ei.project_id
               LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
               ORDER BY ei.employee_key'''
        )
        projects = self.db.fetch_all(
            '''SELECT p.project_key, p.name, p.description, p.status, p.metadata_json,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id) AS total_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status = 'closed') AS closed_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status IN ('ready','dispatching','running','blocked','review','waiting_recovery_completion','waiting_children')) AS agent_queue_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status IN ('waiting_human_info','waiting_human_action','waiting_human_approval')) AS human_queue_issues
               FROM projects p
               ORDER BY p.project_key'''
        )
        dispatching_rows = self.db.fetch_all(
            "SELECT COUNT(DISTINCT issue_id) AS cnt FROM issue_attempts WHERE status IN ('dispatching', 'running')"
        )
        dispatching_count = dispatching_rows[0]['cnt'] if dispatching_rows else 0
        return {
            'project_view': [dict(r) for r in projects],
            'agent_queue': [dict(r) for r in agent_queue],
            'dispatching_count': dispatching_count,
            'human_queue': [dict(r) for r in human_queue],
            'scheduled_issues': [
                {
                    **dict(r),
                    'schedule_config': parse_schedule_config(r['schedule_config_json']) if isinstance(r['schedule_config_json'], str) else {},
                }
                for r in scheduled_items
            ],
            'employee_view': [dict(r) for r in employees],
            'agent_workload': self.get_agent_workload()['items'],
        }

    def list_lightweight_issues(self, *, closed_limit: int = 50) -> list[dict[str, Any]]:
        """Lightweight issue list for board snapshot: basic fields + last 2 attempts.
        Closed issues beyond closed_limit are excluded (load via /api/issues separately).
        No callbacks, no timelines, no activities."""
        # Non-closed: full data but no callbacks/timelines/activities
        non_closed = self.db.fetch_all(
            '''SELECT i.id, i.issue_no, i.title, i.priority, i.status, i.active_attempt_no,
                      i.created_at_ms, i.updated_at_ms, i.project_id,
                      ei.employee_key AS assigned_employee_key,
                      rt.template_key AS assigned_role,
                      rb.agent_id, rb.session_key,
                      p.project_key
               FROM issues i
               JOIN projects p ON p.id = i.project_id
               LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
               LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
               WHERE i.status != 'closed'
               ORDER BY i.issue_no DESC''')
        # Closed: basic + last 2 attempts only, limit to closed_limit
        closed_rows = self.db.fetch_all(
            '''SELECT i.id, i.issue_no, i.title, i.priority, i.status, i.active_attempt_no,
                      i.created_at_ms, i.updated_at_ms, i.project_id,
                      ei.employee_key AS assigned_employee_key,
                      rt.template_key AS assigned_role,
                      rb.agent_id, rb.session_key,
                      p.project_key
               FROM issues i
               JOIN projects p ON p.id = i.project_id
               LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
               LEFT JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
               WHERE i.status = 'closed'
               ORDER BY i.issue_no DESC
               LIMIT ?''',
            (closed_limit,))
        # Build last-2-attempts map
        attempt_rows = self.db.fetch_all(
            '''SELECT ia.issue_id, ia.id, ia.attempt_no, ia.status, ia.created_at_ms,
                      ia.result_summary, ia.failure_code, ia.failure_summary
               FROM issue_attempts ia
               WHERE ia.issue_id IN (
                   SELECT id FROM issues WHERE status != 'closed'
                   UNION ALL
                   SELECT id FROM issues WHERE status = 'closed' LIMIT ?
               ) AND ia.attempt_no > (
                   SELECT COALESCE(MAX(attempt_no), 0) - 2 FROM issue_attempts ia2
                   WHERE ia2.issue_id = ia.issue_id
               )
               ORDER BY ia.issue_id, ia.attempt_no DESC''',
            (closed_limit,))
        # Simpler approach: for each non-closed + closed (limited), get last 2 attempts
        attempt_map: dict[str, list[dict[str, Any]]] = {}
        issue_ids = [r['id'] for r in non_closed] + [r['id'] for r in closed_rows]
        if issue_ids:
            placeholders = ','.join('?' * len(issue_ids))
            att_rows = self.db.fetch_all(
                f'''SELECT ia.issue_id, ia.id, ia.attempt_no, ia.status, ia.created_at_ms,
                          ia.result_summary, ia.failure_code, ia.failure_summary
                   FROM issue_attempts ia
                   WHERE ia.issue_id IN ({placeholders})
                   ORDER BY ia.issue_id, ia.attempt_no DESC''',
                tuple(issue_ids))
            for row in att_rows:
                iid = str(row['issue_id'])
                attempt_map.setdefault(iid, [])
                if len(attempt_map[iid]) < 2:
                    attempt_map[iid].append({
                        'id': row['id'],
                        'attempt_no': row['attempt_no'],
                        'status': row['status'],
                        'created_at_ms': row['created_at_ms'],
                        'result_summary': row['result_summary'],
                        'failure_code': row['failure_code'],
                        'failure_summary': row['failure_summary'],
                    })

        def to_item(row: dict) -> dict[str, Any]:
            iid = str(row['id'])
            return {
                'issue': {
                    'id': row['id'],
                    'issue_no': row['issue_no'],
                    'title': row['title'],
                    'priority': row['priority'],
                    'status': row['status'],
                    'active_attempt_no': row['active_attempt_no'],
                    'project_key': row['project_key'],
                    'assigned_employee_key': row['assigned_employee_key'],
                    'assigned_role': row['assigned_role'],
                    'agent_id': row['agent_id'],
                    'session_key': row['session_key'],
                    'created_at_ms': row['created_at_ms'],
                    'updated_at_ms': row['updated_at_ms'],
                },
                'attempts': attempt_map.get(iid, [])[:2],
                'callbacks_by_attempt': {},
                'timelines': {},
                'activities': [],
                'relations': {'outgoing': [], 'incoming': []},
                'dependencies': {'blocking': [], 'blocked_dependents': []},
                '_lightweight': True,
            }
        return [to_item(r) for r in (list(non_closed) + list(closed_rows))]

    def get_full_issue_detail(self, *, issue_id: str) -> dict[str, Any]:
        """Full issue detail: all attempts, callbacks, timelines, activities, relations."""
        issue = self.db.get_one(
            '''SELECT i.*, p.project_key FROM issues i
               LEFT JOIN projects p ON p.id = i.project_id WHERE i.id = ?''',
            (issue_id,),
        )
        attempts = self.db.fetch_all(
            'SELECT * FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no', (issue_id,))
        callbacks_by_attempt: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            rows = self.db.fetch_all(
                '''SELECT * FROM issue_attempt_callbacks
                   WHERE attempt_id = ? ORDER BY created_at_ms''', (str(attempt['id']),))
            callbacks_by_attempt[str(attempt['id'])] = [dict(r) for r in rows]
        timelines: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            rows = self.db.fetch_all(
                'SELECT checkpoint_no, kind, summary, details_md, next_action, percent_complete, created_at_ms FROM issue_checkpoints WHERE attempt_id = ? ORDER BY checkpoint_no',
                (str(attempt['id']),))
            timelines[str(attempt['id'])] = [dict(r) for r in rows]
        outgoing = self.db.fetch_all(
            '''SELECT ir.*, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
               FROM issue_relations ir JOIN issues i ON i.id = ir.to_issue_id
               WHERE ir.from_issue_id = ? ORDER BY ir.created_at_ms DESC''', (issue_id,))
        incoming = self.db.fetch_all(
            '''SELECT ir.*, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
               FROM issue_relations ir JOIN issues i ON i.id = ir.from_issue_id
               WHERE ir.to_issue_id = ? ORDER BY ir.created_at_ms DESC''', (issue_id,))
        blocking = self.db.fetch_all(
            '''SELECT ir.*, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
               FROM issue_relations ir JOIN issues i ON i.id = ir.to_issue_id
               WHERE ir.from_issue_id = ? AND ir.relation_type = 'blocked_by' ORDER BY ir.created_at_ms DESC''', (issue_id,))
        blocked_dependents = self.db.fetch_all(
            '''SELECT ir.*, i.issue_no AS related_issue_no, i.title AS related_issue_title, i.status AS related_issue_status
               FROM issue_relations ir JOIN issues i ON i.id = ir.from_issue_id
               WHERE ir.to_issue_id = ? AND ir.relation_type = 'blocked_by' ORDER BY ir.created_at_ms DESC''', (issue_id,))
        activities = fetch_issue_activity(self.db.conn, issue_id)
        return {
            'issue': dict(issue),
            'attempts': [dict(r) for r in attempts],
            'callbacks_by_attempt': callbacks_by_attempt,
            'timelines': timelines,
            'activities': activities,
            'relations': {
                'outgoing': [dict(r) for r in outgoing],
                'incoming': [dict(r) for r in incoming],
            },
            'dependencies': {
                'blocking': [dict(r) for r in blocking],
                'blocked_dependents': [dict(r) for r in blocked_dependents],
            },
        }


    def get_issue_activity(self, *, issue_id: str) -> dict[str, Any]:
        items = fetch_issue_activity(self.db.conn, issue_id)
        return {
            'items': items,
            'total': len(items),
        }
