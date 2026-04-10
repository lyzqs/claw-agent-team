from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .db import AgentTeamDB, NotFoundError, ValidationError, now_ms
from .routing_policy import route_issue

# Reuse the prototype adapter until a dedicated production adapter module is split out.
import sys
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
if str(PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTO_ROOT))

from execution_adapter import OpenClawExecutionAdapter, TimeoutObserved  # type: ignore  # noqa: E402


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


@dataclass
class IssueRecord:
    issue_id: str
    issue_no: int
    status: str
    title: str
    assigned_employee_id: str | None
    owner_employee_id: str | None
    active_attempt_no: int | None


@dataclass
class AttemptRecord:
    attempt_id: str
    attempt_no: int
    issue_id: str
    status: str
    dispatch_ref: str | None
    runtime_binding_id: str | None
    assigned_employee_id: str | None


class AgentTeamService:
    def __init__(self, db: AgentTeamDB | None = None):
        self.db = db or AgentTeamDB()

    def close(self) -> None:
        self.db.close()

    def _next_issue_no(self, project_id: str) -> int:
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(issue_no), 0) + 1 FROM issues WHERE project_id = ?',
            (project_id,),
        ).fetchone()
        return int(row[0])

    def _next_attempt_no(self, issue_id: str) -> int:
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM issue_attempts WHERE issue_id = ?',
            (issue_id,),
        ).fetchone()
        return int(row[0])

    def _employee_role(self, employee_id: str) -> str:
        row = self.db.get_one(
            '''SELECT rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.id = ?''',
            (employee_id,),
        )
        return str(row['role'])

    def create_issue(
        self,
        *,
        project_key: str,
        owner_employee_key: str,
        title: str,
        description_md: str = '',
        acceptance_criteria_md: str = '',
        priority: str = 'p2',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.db.get_one('SELECT id FROM projects WHERE project_key = ?', (project_key,))
        owner = self.db.get_one('SELECT id FROM employee_instances WHERE employee_key = ?', (owner_employee_key,))
        ts = now_ms()
        issue_id = uid('issue')
        issue_no = self._next_issue_no(project['id'])
        self.db.conn.execute(
            '''INSERT INTO issues (
                id, project_id, issue_no, title, description_md, source_type, priority,
                status, owner_employee_id, acceptance_criteria_md, metadata_json,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, 'system', ?, 'open', ?, ?, ?, ?, ?)''',
            (
                issue_id,
                project['id'],
                issue_no,
                title,
                description_md,
                priority,
                owner['id'],
                acceptance_criteria_md,
                json.dumps(metadata or {}, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'issue_no': issue_no,
            'status': 'open',
            'created_at_ms': ts,
        }

    def triage_issue(self, *, issue_id: str, assign_employee_key: str) -> dict[str, Any]:
        employee = self.db.get_one('SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', (assign_employee_key,))
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, assigned_employee_id = ?, updated_at_ms = ? WHERE id = ?',
            ('triaged', employee['id'], ts, issue_id),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'triaged',
            'assign_employee_key': employee['employee_key'],
            'updated_at_ms': ts,
        }

    def handoff_issue(
        self,
        *,
        issue_id: str,
        to_employee_key: str,
        note: str = '',
        issue_type: str = 'normal',
        risk_level: str = 'normal',
    ) -> dict[str, Any]:
        issue = self.db.get_one('SELECT assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
        if issue['assigned_employee_id'] is None:
            raise ValidationError('issue has no assigned employee to route from')
        from_role = self._employee_role(issue['assigned_employee_id'])

        employee = self.db.get_one(
            '''SELECT ei.id, ei.employee_key, rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.employee_key = ?''',
            (to_employee_key,),
        )
        to_role = str(employee['role'])
        decision = route_issue(from_role=from_role, to_role=to_role, issue_type=issue_type, risk_level=risk_level)
        if not decision.allowed:
            raise ValidationError(decision.reason)

        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, assigned_employee_id = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
            ('review', employee['id'], note or None, ts, issue_id),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'review',
            'assigned_employee_key': employee['employee_key'],
            'from_role': from_role,
            'to_role': to_role,
            'routing_reason': decision.reason,
            'updated_at_ms': ts,
        }

    def dispatch_execution(
        self,
        *,
        issue_id: str,
        runtime_binding_key: str,
        payload: dict[str, Any],
        dispatch_ref: str | None = None,
    ) -> dict[str, Any]:
        issue = self.db.get_one('SELECT id, assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
        binding = self.db.get_one('SELECT id, employee_id, binding_key, session_key FROM runtime_bindings WHERE binding_key = ?', (runtime_binding_key,))
        if issue['assigned_employee_id'] and issue['assigned_employee_id'] != binding['employee_id']:
            raise ValidationError('runtime binding employee does not match assigned employee')
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError('payload.prompt is required for real dispatch')

        adapter = OpenClawExecutionAdapter(binding['session_key'])
        dispatch = adapter.dispatch(prompt=prompt, dispatch_id=dispatch_ref)

        ts = now_ms()
        attempt_id = uid('attempt')
        attempt_no = self._next_attempt_no(issue_id)
        real_dispatch_ref = dispatch['dispatch_ref']

        self.db.conn.execute(
            '''INSERT INTO issue_attempts (
                id, issue_id, attempt_no, assigned_employee_id, runtime_binding_id,
                dispatch_kind, status, dispatch_ref, input_snapshot_json, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, 'run', 'dispatching', ?, ?, ?, ?)''',
            (
                attempt_id,
                issue_id,
                attempt_no,
                binding['employee_id'],
                binding['id'],
                real_dispatch_ref,
                json.dumps(payload, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        self.db.conn.execute(
            'UPDATE issues SET status = ?, active_attempt_no = ?, assigned_employee_id = ?, updated_at_ms = ? WHERE id = ?',
            ('dispatching', attempt_no, binding['employee_id'], ts, issue_id),
        )
        self._record_checkpoint(
            issue_id=issue_id,
            attempt_id=attempt_id,
            kind='handoff',
            summary='Execution dispatched',
            details_md=json.dumps({'dispatch_ref': real_dispatch_ref, 'binding_key': binding['binding_key']}, ensure_ascii=False),
            next_action='Observe execution',
            created_by_employee_id=binding['employee_id'],
            percent_complete=10,
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'attempt_id': attempt_id,
            'attempt_no': attempt_no,
            'dispatch_ref': real_dispatch_ref,
            'runtime_binding_key': binding['binding_key'],
            'session_key': binding['session_key'],
            'status': 'dispatching',
            'accepted': dispatch.get('accepted', True),
        }

    def observe_execution(self, *, dispatch_ref: str, expected_text: str | None = None, timeout_seconds: int = 1, close_issue_on_success: bool = False) -> dict[str, Any]:
        row = self.db.get_one(
            '''SELECT ia.id AS attempt_id, ia.issue_id, ia.attempt_no, ia.status, ia.dispatch_ref,
                      ia.assigned_employee_id, rb.binding_key, rb.session_key
               FROM issue_attempts ia
               LEFT JOIN runtime_bindings rb ON rb.id = ia.runtime_binding_id
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        result = {
            'attempt_id': row['attempt_id'],
            'issue_id': row['issue_id'],
            'attempt_no': row['attempt_no'],
            'dispatch_ref': row['dispatch_ref'],
            'status': row['status'],
            'runtime_binding_key': row['binding_key'],
            'session_key': row['session_key'],
        }
        if expected_text:
            adapter = OpenClawExecutionAdapter(row['session_key'])
            try:
                wait_result = adapter.wait_for_exact_text(expected_text=expected_text, timeout_seconds=timeout_seconds)
                ts = now_ms()
                self.db.conn.execute(
                    'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, updated_at_ms = ? WHERE id = ?',
                    ('succeeded', ts, 'Observed exact completion text', json.dumps(wait_result, ensure_ascii=False), ts, row['attempt_id']),
                )
                next_issue_status = 'closed' if close_issue_on_success else 'review'
                closed_at = ts if close_issue_on_success else None
                self.db.conn.execute(
                    'UPDATE issues SET status = ?, closed_at_ms = ?, updated_at_ms = ? WHERE id = ?',
                    (next_issue_status, closed_at, ts, row['issue_id']),
                )
                self._record_checkpoint(
                    issue_id=row['issue_id'],
                    attempt_id=row['attempt_id'],
                    kind='progress',
                    summary='Execution observed completion',
                    details_md=json.dumps(wait_result, ensure_ascii=False),
                    next_action='Close issue' if close_issue_on_success else 'Hand off to next role / review',
                    created_by_employee_id=row['assigned_employee_id'],
                    percent_complete=100,
                )
                self.db.commit()
                result['status'] = 'succeeded'
                result['issue_status'] = next_issue_status
                result['wait_result'] = wait_result
            except TimeoutObserved as e:
                result['observe_timeout'] = str(e)
        return result

    def cancel_execution(self, *, dispatch_ref: str, reason: str = 'cancelled_by_service') -> dict[str, Any]:
        attempt = self.db.get_one(
            '''SELECT ia.id, ia.issue_id, ia.assigned_employee_id, rb.session_key
               FROM issue_attempts ia
               LEFT JOIN runtime_bindings rb ON rb.id = ia.runtime_binding_id
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        adapter = OpenClawExecutionAdapter(attempt['session_key'])
        abort_payload = adapter.abort(dispatch_ref)
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, updated_at_ms = ? WHERE id = ?',
            ('cancelled', 'cancelled', reason, ts, json.dumps(abort_payload, ensure_ascii=False), ts, attempt['id']),
        )
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
            ('ready', reason, ts, attempt['issue_id']),
        )
        self._record_checkpoint(
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            kind='system',
            summary='Execution cancelled',
            details_md=json.dumps(abort_payload, ensure_ascii=False),
            next_action='Retry or close issue',
            created_by_employee_id=attempt['assigned_employee_id'],
            percent_complete=None,
        )
        self.db.commit()
        return {
            'dispatch_ref': dispatch_ref,
            'status': 'cancelled',
            'reason': reason,
            'abort_payload': abort_payload,
            'updated_at_ms': ts,
        }

    def retry_execution(self, *, issue_id: str, runtime_binding_key: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
            (f'Retry requested: {reason}', ts, issue_id),
        )
        self.db.commit()
        return self.dispatch_execution(issue_id=issue_id, runtime_binding_key=runtime_binding_key, payload=payload)

    def close_issue(self, *, issue_id: str, resolution: str = 'completed') -> dict[str, Any]:
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = NULL, updated_at_ms = ? WHERE id = ?',
            ('closed', ts, ts, issue_id),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'closed',
            'resolution': resolution,
            'closed_at_ms': ts,
        }

    def enqueue_human(
        self,
        *,
        issue_id: str,
        human_type: str,
        prompt: str,
        required_input: str,
    ) -> dict[str, Any]:
        status_map = {
            'info': 'waiting_human_info',
            'action': 'waiting_human_action',
            'approval': 'waiting_human_approval',
        }
        if human_type not in status_map:
            raise ValidationError(f'unsupported human_type: {human_type}')
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
            (status_map[human_type], prompt, required_input, ts, issue_id),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': status_map[human_type],
            'prompt': prompt,
            'required_input': required_input,
        }

    def resolve_human_action(self, *, issue_id: str, resolution: str, note: str = '') -> dict[str, Any]:
        ts = now_ms()
        if resolution not in {'approve', 'reject', 'needs_info'}:
            raise ValidationError(f'unsupported resolution: {resolution}')
        if resolution == 'approve':
            new_status = 'ready'
            blocker = None
            required = None
        elif resolution == 'reject':
            new_status = 'failed'
            blocker = note or 'Rejected by human'
            required = None
        else:
            new_status = 'waiting_human_info'
            blocker = note or 'Human requested more information'
            required = note or 'Provide additional information'
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
            (new_status, blocker, required, ts, issue_id),
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': new_status,
            'resolution': resolution,
            'updated_at_ms': ts,
        }

    def get_human_queue(self) -> dict[str, Any]:
        rows = self.db.fetch_all('SELECT * FROM v_human_queue ORDER BY updated_at_ms DESC')
        return {
            'items': [dict(r) for r in rows],
            'total': len(rows),
        }

    def list_issues(self, *, project_key: str | None = None, status: str | None = None) -> dict[str, Any]:
        sql = 'SELECT i.id, i.issue_no, i.title, i.priority, i.status, i.active_attempt_no, p.project_key FROM issues i JOIN projects p ON p.id = i.project_id WHERE 1=1'
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
        issue = self.db.get_one('SELECT * FROM issues WHERE id = ?', (issue_id,))
        attempts = self.db.fetch_all('SELECT * FROM issue_attempts WHERE issue_id = ? ORDER BY attempt_no', (issue_id,))
        return {
            'issue': dict(issue),
            'attempts': [dict(r) for r in attempts],
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

    def get_board_snapshot(self) -> dict[str, Any]:
        agent_queue = self.db.fetch_all('SELECT * FROM v_agent_queue ORDER BY updated_at_ms DESC')
        human_queue = self.db.fetch_all('SELECT * FROM v_human_queue ORDER BY updated_at_ms DESC')
        employees = self.db.fetch_all(
            '''SELECT ei.employee_key, rt.template_key AS role, ei.status, rb.session_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN runtime_bindings rb ON rb.employee_id = ei.id AND rb.is_primary = 1
               ORDER BY ei.employee_key'''
        )
        projects = self.db.fetch_all(
            '''SELECT p.project_key, p.name, p.status,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id) AS total_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status = 'closed') AS closed_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status IN ('ready','dispatching','running','blocked','review')) AS agent_queue_issues,
                      (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id AND i.status IN ('waiting_human_info','waiting_human_action','waiting_human_approval')) AS human_queue_issues
               FROM projects p
               ORDER BY p.project_key'''
        )
        return {
            'project_view': [dict(r) for r in projects],
            'agent_queue': [dict(r) for r in agent_queue],
            'human_queue': [dict(r) for r in human_queue],
            'employee_view': [dict(r) for r in employees],
        }

    def _record_checkpoint(
        self,
        *,
        issue_id: str,
        attempt_id: str,
        kind: str,
        summary: str,
        details_md: str,
        next_action: str,
        created_by_employee_id: str | None,
        percent_complete: int | None,
    ) -> None:
        checkpoint_id = uid('ckpt')
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(checkpoint_no), 0) + 1 FROM issue_checkpoints WHERE attempt_id = ?',
            (attempt_id,),
        ).fetchone()
        checkpoint_no = int(row[0])
        self.db.conn.execute(
            '''INSERT INTO issue_checkpoints (
                id, issue_id, attempt_id, checkpoint_no, kind, summary, details_md,
                next_action, percent_complete, created_by_employee_id, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                checkpoint_id,
                issue_id,
                attempt_id,
                checkpoint_no,
                kind,
                summary,
                details_md,
                next_action,
                percent_complete,
                created_by_employee_id,
                now_ms(),
            ),
        )


def demo() -> None:
    service = AgentTeamService()
    try:
        result = service.list_issues(project_key='agent-team-core')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        service.close()


if __name__ == '__main__':
    demo()
