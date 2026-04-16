from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import AgentTeamDB, NotFoundError, ValidationError, now_ms
from .routing_policy import route_issue
from .activity import ensure_issue_activity_table, ensure_issue_attempt_callback_table, record_issue_activity
from .human_queue_service import HumanQueueService, WAITING_HUMAN_STATUSES, WAITING_HUMAN_STATUS_BY_TYPE
from .board_query_service import BoardQueryService
from .dispatch_service import DispatchService
from .dependency_service import DependencyService
from .derived_issue_service import DerivedIssueService


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


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


def default_next_role_for(role: str | None) -> str:
    mapping = {
        'pm': 'dev',
        'dev': 'qa',
        'qa': 'ceo',
        'ops': 'ceo',
        'ceo': 'close',
    }
    return mapping.get(str(role or '').strip(), 'close')


WAITING_HUMAN_STATUSES = WAITING_HUMAN_STATUSES
WAITING_HUMAN_STATUS_BY_TYPE = WAITING_HUMAN_STATUS_BY_TYPE


def append_unique_artifact(existing: list[Any], artifact: Any) -> list[Any]:
    items = list(existing)
    marker = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items}
    if marker not in seen:
        items.append(artifact)
    return items



class AgentTeamService:
    def __init__(self, db: AgentTeamDB | None = None):
        self.db = db or AgentTeamDB()
        ensure_issue_activity_table(self.db.conn)
        ensure_issue_attempt_callback_table(self.db.conn)
        self.human_queue = HumanQueueService(self.db)
        self.board_query = BoardQueryService(self.db)
        self.dispatch_service = DispatchService(self.db, self._record_checkpoint)
        self.dependency_service = DependencyService(self.db)
        self.derived_issue_service = DerivedIssueService(
            self.db,
            create_issue=self.create_issue,
            triage_issue=self.triage_issue,
        )
        self.db.commit()

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
        source_type: str = 'system',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_type not in {'user', 'system', 'detector', 'watchdog', 'human'}:
            raise ValidationError(f'unsupported source_type: {source_type}')
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)''',
            (
                issue_id,
                project['id'],
                issue_no,
                title,
                description_md,
                source_type,
                priority,
                owner['id'],
                acceptance_criteria_md,
                json.dumps(metadata or {}, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='issue_created',
            summary=f'Issue #{issue_no} created',
            actor_employee_id=owner['id'],
            details={'project_key': project_key, 'title': title, 'priority': priority, 'source_type': source_type},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'issue_no': issue_no,
            'status': 'open',
            'source_type': source_type,
            'created_at_ms': ts,
        }

    def triage_issue(self, *, issue_id: str, assign_employee_key: str) -> dict[str, Any]:
        employee = self.db.get_one('SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', (assign_employee_key,))
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, assigned_employee_id = ?, updated_at_ms = ? WHERE id = ?',
            ('triaged', employee['id'], ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='triaged',
            summary=f'Issue triaged to {employee["employee_key"]}',
            actor_employee_id=employee['id'],
            details={'assign_employee_key': employee['employee_key']},
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
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='handoff',
            summary=f'Handoff {from_role} -> {to_role}',
            actor_employee_id=employee['id'],
            details={'to_employee_key': employee['employee_key'], 'note': note, 'routing_reason': decision.reason},
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
        return self.dispatch_service.dispatch_execution(
            issue_id=issue_id,
            runtime_binding_key=runtime_binding_key,
            payload=payload,
            dispatch_ref=dispatch_ref,
        )

    def record_attempt_callback(
        self,
        *,
        attempt_id: str,
        callback_token: str,
        phase: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.dispatch_service.record_attempt_callback(
            attempt_id=attempt_id,
            callback_token=callback_token,
            phase=phase,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def observe_execution(self, *, dispatch_ref: str, expected_text: str | None = None, expected_marker: str | None = None, timeout_seconds: int = 1, close_issue_on_success: bool = False) -> dict[str, Any]:
        return self.dispatch_service.observe_execution(
            dispatch_ref=dispatch_ref,
            expected_text=expected_text,
            expected_marker=expected_marker,
            timeout_seconds=timeout_seconds,
            close_issue_on_success=close_issue_on_success,
        )

    def cancel_execution(self, *, dispatch_ref: str, reason: str = 'cancelled_by_service') -> dict[str, Any]:
        return self.dispatch_service.cancel_execution(dispatch_ref=dispatch_ref, reason=reason)

    def reconcile_stale_attempt(self, *, dispatch_ref: str, reason: str = 'stale_dispatch_reconciled') -> dict[str, Any]:
        return self.dispatch_service.reconcile_stale_attempt(dispatch_ref=dispatch_ref, reason=reason)

    def retry_execution(self, *, issue_id: str, runtime_binding_key: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        return self.dispatch_service.retry_execution(
            issue_id=issue_id,
            runtime_binding_key=runtime_binding_key,
            payload=payload,
            reason=reason,
        )

    def observe_dispatch_lifecycle_event(
        self,
        *,
        dispatch_ref: str,
        state: str,
        stop_reason: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.dispatch_service.observe_dispatch_lifecycle_event(
            dispatch_ref=dispatch_ref,
            state=state,
            stop_reason=stop_reason,
            error_message=error_message,
            payload=payload,
        )

    def apply_artifact_gate(
        self,
        *,
        issue_id: str,
        artifact_payload: dict[str, Any],
        current_role: str | None,
        summary: str | None = None,
        suggested_next_role: str | None = None,
    ) -> dict[str, Any]:
        ts = now_ms()
        issue = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        metadata = merge_json_object(issue['metadata_json'], {})
        handoff_payload = {
            'marker': '',
            'status': 'done',
            'summary': summary or str(artifact_payload.get('summary') or 'Existing artifact already satisfies acceptance'),
            'artifacts': [artifact_payload],
            'blocking_findings': [],
            'suggested_next_role': suggested_next_role or default_next_role_for(current_role),
            'reason': 'existing artifact satisfies acceptance; dispatch skipped',
            'risk_level': str(metadata.get('risk_level') or 'normal'),
            'needs_human': False,
        }
        metadata['prior_handoff'] = handoff_payload
        metadata['suggested_next_role'] = handoff_payload['suggested_next_role']
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = NULL, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
            ('review', json.dumps(metadata, ensure_ascii=False), ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='artifact_gate',
            summary='Dispatch skipped because existing artifact satisfies acceptance',
            actor_employee_id=issue['assigned_employee_id'],
            details={'artifact_payload': artifact_payload, 'handoff_payload': handoff_payload},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': 'review',
            'handoff_payload': handoff_payload,
            'updated_at_ms': ts,
        }

    def close_issue(self, *, issue_id: str, resolution: str = 'completed') -> dict[str, Any]:
        ts = now_ms()
        issue = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        open_children = self.db.conn.execute(
            '''SELECT COUNT(*)
               FROM issue_relations ir
               JOIN issues child ON child.id = ir.to_issue_id
               WHERE ir.from_issue_id = ? AND ir.relation_type = 'parent_of' AND child.status != 'closed' ''',
            (issue_id,),
        ).fetchone()[0]
        if int(open_children or 0) > 0:
            metadata = merge_json_object(issue['metadata_json'], {})
            metadata.setdefault('orchestration_parent', True)
            metadata.setdefault('parent_wait_strategy', 'wait_children_then_review')
            metadata.setdefault('resume_role', 'ceo')
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                ('waiting_children', f'waiting for {int(open_children)} child issue(s) to close', json.dumps(metadata, ensure_ascii=False), ts, issue_id),
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=issue_id,
                action_type='issue_waiting_children',
                summary='Issue moved to waiting_children instead of closing',
                actor_employee_id=issue['assigned_employee_id'],
                details={'resolution': resolution, 'open_child_count': int(open_children), 'resume_role': metadata.get('resume_role'), 'parent_wait_strategy': metadata.get('parent_wait_strategy')},
            )
            self.db.commit()
            return {
                'issue_id': issue_id,
                'status': 'waiting_children',
                'resolution': resolution,
                'open_child_count': int(open_children),
                'updated_at_ms': ts,
            }
        self.db.conn.execute(
            'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = NULL, updated_at_ms = ? WHERE id = ?',
            ('closed', ts, ts, issue_id),
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='issue_closed',
            summary='Issue closed',
            actor_employee_id=issue['assigned_employee_id'],
            details={'resolution': resolution},
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
        return self.human_queue.enqueue_human(
            issue_id=issue_id,
            human_type=human_type,
            prompt=prompt,
            required_input=required_input,
        )

    def resolve_human_action(self, *, issue_id: str, resolution: str, note: str = '') -> dict[str, Any]:
        return self.human_queue.resolve_human_action(issue_id=issue_id, resolution=resolution, note=note)

    def get_issue_activity(self, *, issue_id: str) -> dict[str, Any]:
        return self.board_query.get_issue_activity(issue_id=issue_id)

    def get_human_queue(self) -> dict[str, Any]:
        return self.human_queue.get_human_queue()

    def list_issues(self, *, project_key: str | None = None, status: str | None = None) -> dict[str, Any]:
        return self.board_query.list_issues(project_key=project_key, status=status)

    def get_issue(self, *, issue_id: str) -> dict[str, Any]:
        return self.board_query.get_issue(issue_id=issue_id)

    def get_attempt_timeline(self, *, attempt_id: str) -> dict[str, Any]:
        return self.board_query.get_attempt_timeline(attempt_id=attempt_id)

    def get_agent_workload(self) -> dict[str, Any]:
        return self.board_query.get_agent_workload()

    def get_board_snapshot(self) -> dict[str, Any]:
        return self.board_query.get_board_snapshot()

    def get_ui_snapshot(self, *, generated_at: str | None = None, source: str | None = None) -> dict[str, Any]:
        return self.board_query.get_ui_snapshot(generated_at=generated_at, source=source)

    def reconcile_dependency_transitions(self) -> dict[str, Any]:
        return self.dependency_service.reconcile_dependency_transitions()

    def create_derived_issues(
        self,
        *,
        attempt_id: str,
        proposals: list[dict[str, Any]],
        created_by_role: str | None = None,
    ) -> dict[str, Any]:
        return self.derived_issue_service.create_derived_issues(
            attempt_id=attempt_id,
            proposals=proposals,
            created_by_role=created_by_role,
        )

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
