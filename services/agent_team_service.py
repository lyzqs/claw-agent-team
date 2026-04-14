from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .db import AgentTeamDB, NotFoundError, ValidationError, now_ms
from .routing_policy import route_issue
from .activity import ensure_issue_activity_table, ensure_issue_attempt_callback_table, record_issue_activity, fetch_issue_activity, record_attempt_callback_event

# Reuse the prototype adapter until a dedicated production adapter module is split out.
import sys
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
if str(PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTO_ROOT))

from execution_adapter import (  # type: ignore  # noqa: E402
    OpenClawExecutionAdapter,
    RunAbortedObserved,
    RunErrorObserved,
    TimeoutObserved,
)


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


def append_unique_artifact(existing: list[Any], artifact: Any) -> list[Any]:
    items = list(existing)
    marker = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items}
    if marker not in seen:
        items.append(artifact)
    return items


def can_use_artifact_fallback(*, acceptance_criteria_md: str | None, artifact_payload: dict[str, Any] | None) -> bool:
    text = str(acceptance_criteria_md or '')
    payload = artifact_payload if isinstance(artifact_payload, dict) else {}
    artifact_type = str(payload.get('artifact_type') or '')
    has_doc = artifact_type == 'feishu_doc' or bool(payload.get('doc_url')) or bool(payload.get('doc_token'))
    if not has_doc:
        return False
    if any(token in text for token in ['飞书文档', '产出飞书文档', '文档']):
        return True
    return False


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
        ensure_issue_activity_table(self.db.conn)
        ensure_issue_attempt_callback_table(self.db.conn)
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
        issue = self.db.get_one('SELECT id, assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
        binding = self.db.get_one('SELECT id, employee_id, binding_key, session_key FROM runtime_bindings WHERE binding_key = ?', (runtime_binding_key,))
        if issue['assigned_employee_id'] and issue['assigned_employee_id'] != binding['employee_id']:
            raise ValidationError('runtime binding employee does not match assigned employee')
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError('payload.prompt is required for real dispatch')
        effective_session_key = payload.get('session_key') if isinstance(payload.get('session_key'), str) and payload.get('session_key').strip() else binding['session_key']

        flow_id = str(payload.get('flow_id') or uid('flow'))
        callback_token = str(payload.get('callback_token') or uid('cbtok'))
        timeout_deadline_ms = int(payload.get('timeout_deadline_ms') or (now_ms() + 15 * 60 * 1000))
        ts = now_ms()
        attempt_id = uid('attempt')
        attempt_no = self._next_attempt_no(issue_id)

        callback_protocol = (
            "\n\nCallback：\n"
            f"- attempt_id: {attempt_id}\n"
            f"- callback_token: {callback_token}\n"
            f"- 文档产物：python3 /root/.openclaw/workspace-agent-team/scripts/attempt_callback_helper.py artifact-doc --attempt-id {attempt_id} --callback-token {callback_token} --doc-url <url> [--doc-token <token>] [--summary <summary>]\n"
            f"- 最终完成：python3 /root/.openclaw/workspace-agent-team/scripts/attempt_callback_helper.py terminal --attempt-id {attempt_id} --callback-token {callback_token} --status done --summary '<总结>' --next <pm|dev|qa|ops|ceo|close> --reason '<原因>'\n"
            "- 先记 callback，再发送最终 JSON。\n"
        )
        payload = dict(payload)
        payload['flow_id'] = flow_id
        payload['callback_token'] = callback_token
        payload['timeout_deadline_ms'] = timeout_deadline_ms
        payload['attempt_id'] = attempt_id
        payload['attempt_no'] = attempt_no
        payload['prompt'] = f"{prompt.rstrip()}{callback_protocol}"

        adapter = OpenClawExecutionAdapter(effective_session_key)
        dispatch = adapter.dispatch(prompt=payload['prompt'], dispatch_id=dispatch_ref)

        real_dispatch_ref = dispatch['dispatch_ref']

        self.db.conn.execute(
            '''INSERT INTO issue_attempts (
                id, issue_id, attempt_no, assigned_employee_id, runtime_binding_id,
                dispatch_kind, status, dispatch_ref, input_snapshot_json, metadata_json,
                flow_id, callback_token, callback_status, artifact_status, timeout_deadline_ms,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, 'run', 'dispatching', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                attempt_id,
                issue_id,
                attempt_no,
                binding['employee_id'],
                binding['id'],
                real_dispatch_ref,
                json.dumps(payload, ensure_ascii=False),
                json.dumps({'dispatch_lifecycle': 'accepted'}, ensure_ascii=False),
                flow_id,
                callback_token,
                'pending',
                'none',
                timeout_deadline_ms,
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
            details_md=json.dumps({'dispatch_ref': real_dispatch_ref, 'binding_key': binding['binding_key'], 'flow_id': flow_id, 'callback_token': callback_token}, ensure_ascii=False),
            next_action='Wait for callback or observe completion',
            created_by_employee_id=binding['employee_id'],
            percent_complete=10,
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            attempt_id=attempt_id,
            action_type='dispatch_execution',
            summary='Execution dispatched to runtime',
            actor_employee_id=binding['employee_id'],
            details={'dispatch_ref': real_dispatch_ref, 'binding_key': binding['binding_key'], 'session_key': effective_session_key, 'flow_id': flow_id, 'callback_token': callback_token},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'attempt_id': attempt_id,
            'attempt_no': attempt_no,
            'dispatch_ref': real_dispatch_ref,
            'runtime_binding_key': binding['binding_key'],
            'session_key': effective_session_key,
            'status': 'dispatching',
            'accepted': dispatch.get('accepted', True),
            'flow_id': flow_id,
            'callback_token': callback_token,
            'timeout_deadline_ms': timeout_deadline_ms,
        }

    def record_attempt_callback(
        self,
        *,
        attempt_id: str,
        callback_token: str,
        phase: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if phase not in {'artifact_created', 'terminal_handoff'}:
            raise ValidationError(f'unsupported callback phase: {phase}')
        attempt = self.db.get_one(
            '''SELECT id, issue_id, attempt_no, assigned_employee_id, flow_id, callback_token, callback_status,
                      artifact_status, artifact_snapshot_json, callback_payload_json, metadata_json
               FROM issue_attempts
               WHERE id = ?''',
            (attempt_id,),
        )
        if str(attempt['callback_token'] or '') != str(callback_token):
            raise ValidationError('callback token mismatch')

        ts = now_ms()
        effective_idempotency_key = idempotency_key or f'attempt:{attempt_id}:{phase}:{json.dumps(payload, ensure_ascii=False, sort_keys=True)}'
        inserted, event = record_attempt_callback_event(
            self.db.conn,
            attempt_id=attempt_id,
            flow_id=attempt['flow_id'],
            callback_token=callback_token,
            phase=phase,
            idempotency_key=effective_idempotency_key,
            payload=payload,
            accepted=True,
            accepted_reason='accepted',
            created_at_ms=ts,
        )

        if not inserted:
            return {
                'attempt_id': attempt_id,
                'issue_id': attempt['issue_id'],
                'phase': phase,
                'duplicate': True,
                'event': event,
            }

        metadata = merge_json_object(attempt['metadata_json'], {})
        callback_payload = merge_json_object(attempt['callback_payload_json'], {})
        artifact_snapshot = merge_json_object(attempt['artifact_snapshot_json'], {})
        issue_row = self.db.get_one('SELECT metadata_json FROM issues WHERE id = ?', (attempt['issue_id'],))
        issue_metadata = merge_json_object(issue_row['metadata_json'], {})

        callback_status = str(attempt['callback_status'] or 'pending')
        artifact_status = str(attempt['artifact_status'] or 'none')

        if phase == 'artifact_created':
            callback_status = 'artifact_only' if callback_status in {'pending', '', 'artifact_only'} else callback_status
            artifact_status = 'reported'
            artifact_snapshot = payload if isinstance(payload, dict) else {}
            callback_payload['artifact_callback'] = payload
            callback_payload['artifact_callback_at_ms'] = ts
            artifacts = issue_metadata.get('artifacts') if isinstance(issue_metadata.get('artifacts'), dict) else {}
            docs = artifacts.get('feishu_docs') if isinstance(artifacts.get('feishu_docs'), list) else []
            artifact_type = str(payload.get('artifact_type') or '')
            if artifact_type == 'feishu_doc' or payload.get('doc_url') or payload.get('doc_token'):
                docs = append_unique_artifact(docs, {
                    'artifact_type': artifact_type or 'feishu_doc',
                    'doc_url': payload.get('doc_url'),
                    'doc_token': payload.get('doc_token'),
                    'summary': payload.get('summary'),
                    'from_attempt_id': attempt_id,
                    'from_attempt_no': attempt['attempt_no'],
                    'created_at_ms': ts,
                    'status': 'created',
                })
                artifacts['feishu_docs'] = docs
                issue_metadata['artifacts'] = artifacts
        else:
            callback_status = 'terminal_confirmed'
            callback_payload['terminal_callback'] = payload

        self.db.conn.execute(
            '''UPDATE issue_attempts
               SET callback_status = ?, callback_received_at_ms = ?, callback_payload_json = ?,
                   artifact_status = ?, artifact_snapshot_json = ?, metadata_json = ?, updated_at_ms = ?
               WHERE id = ?''',
            (
                callback_status,
                ts,
                json.dumps(callback_payload, ensure_ascii=False),
                artifact_status,
                json.dumps(artifact_snapshot, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False),
                ts,
                attempt_id,
            ),
        )
        self.db.conn.execute(
            'UPDATE issues SET metadata_json = ?, updated_at_ms = ? WHERE id = ?',
            (json.dumps(issue_metadata, ensure_ascii=False), ts, attempt['issue_id']),
        )

        self._record_checkpoint(
            issue_id=attempt['issue_id'],
            attempt_id=attempt_id,
            kind='progress',
            summary=f'Callback received: {phase}',
            details_md=json.dumps(payload, ensure_ascii=False),
            next_action='Await further callbacks or reconcile completion',
            created_by_employee_id=attempt['assigned_employee_id'],
            percent_complete=60 if phase == 'artifact_created' else 90,
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=attempt['issue_id'],
            attempt_id=attempt_id,
            action_type='attempt_callback',
            summary=f'Attempt callback accepted: {phase}',
            actor_employee_id=attempt['assigned_employee_id'],
            details={'phase': phase, 'payload': payload, 'idempotency_key': effective_idempotency_key},
        )
        self.db.commit()
        return {
            'attempt_id': attempt_id,
            'issue_id': attempt['issue_id'],
            'phase': phase,
            'duplicate': False,
            'callback_status': callback_status,
            'artifact_status': artifact_status,
            'event': event,
        }

    def observe_execution(self, *, dispatch_ref: str, expected_text: str | None = None, expected_marker: str | None = None, timeout_seconds: int = 1, close_issue_on_success: bool = False) -> dict[str, Any]:
        row = self.db.get_one(
            '''SELECT ia.id AS attempt_id, ia.issue_id, ia.attempt_no, ia.status, ia.dispatch_ref,
                      ia.assigned_employee_id, ia.input_snapshot_json, ia.callback_status,
                      ia.callback_payload_json, ia.artifact_status, ia.artifact_snapshot_json,
                      ia.completion_mode, ia.created_at_ms, rb.binding_key, rb.session_key,
                      i.acceptance_criteria_md
               FROM issue_attempts ia
               JOIN issues i ON i.id = ia.issue_id
               LEFT JOIN runtime_bindings rb ON rb.id = ia.runtime_binding_id
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        input_snapshot = json.loads(row['input_snapshot_json']) if row['input_snapshot_json'] else {}
        effective_session_key = input_snapshot.get('session_key') if isinstance(input_snapshot.get('session_key'), str) and input_snapshot.get('session_key').strip() else row['session_key']
        callback_payload = merge_json_object(row['callback_payload_json'], {})
        result = {
            'attempt_id': row['attempt_id'],
            'issue_id': row['issue_id'],
            'attempt_no': row['attempt_no'],
            'dispatch_ref': row['dispatch_ref'],
            'status': row['status'],
            'runtime_binding_key': row['binding_key'],
            'session_key': effective_session_key,
            'callback_status': row['callback_status'],
            'artifact_status': row['artifact_status'],
        }

        terminal_callback = callback_payload.get('terminal_callback') if isinstance(callback_payload.get('terminal_callback'), dict) else None
        if terminal_callback:
            normalized_payload = dict(terminal_callback)
            suggested_next_role = str(normalized_payload.get('suggested_next_role') or default_next_role_for(input_snapshot.get('attempt_role')))
            normalized_payload['suggested_next_role'] = suggested_next_role
            normalized_payload.setdefault('artifacts', [])
            normalized_payload.setdefault('blocking_findings', [])
            normalized_payload.setdefault('status', 'done')
            normalized_payload.setdefault('risk_level', 'normal')
            normalized_payload.setdefault('needs_human', False)
            normalized_payload.setdefault('create_issue_proposal', None)
            normalized_payload.setdefault('summary', normalized_payload.get('reason') or 'Observed terminal callback')
            ts = now_ms()
            summary = str(normalized_payload.get('summary') or normalized_payload.get('reason') or 'Observed terminal callback')
            output_snapshot = {
                'source': 'callback_terminal',
                'payload': normalized_payload,
            }
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('succeeded', ts, summary, json.dumps(output_snapshot, ensure_ascii=False), 'callback_terminal', ts, row['attempt_id']),
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
                summary='Execution completed via callback',
                details_md=json.dumps(output_snapshot, ensure_ascii=False),
                next_action='Close issue' if close_issue_on_success else 'Hand off to next role / review',
                created_by_employee_id=row['assigned_employee_id'],
                percent_complete=100,
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=row['issue_id'],
                attempt_id=row['attempt_id'],
                action_type='execution_succeeded',
                summary='Execution succeeded via callback',
                actor_employee_id=row['assigned_employee_id'],
                details={'callback_payload': normalized_payload, 'issue_status': next_issue_status},
            )
            self.db.commit()
            result['status'] = 'succeeded'
            result['issue_status'] = next_issue_status
            result['wait_result'] = output_snapshot
            return result

        artifact_callback = callback_payload.get('artifact_callback') if isinstance(callback_payload.get('artifact_callback'), dict) else None
        if artifact_callback and can_use_artifact_fallback(acceptance_criteria_md=row['acceptance_criteria_md'], artifact_payload=artifact_callback):
            normalized_payload = {
                'marker': str(input_snapshot.get('marker') or ''),
                'status': 'done',
                'summary': str(artifact_callback.get('summary') or 'Artifact satisfies acceptance criteria'),
                'artifacts': [artifact_callback],
                'blocking_findings': [],
                'suggested_next_role': default_next_role_for(input_snapshot.get('attempt_role')),
                'reason': 'artifact fallback success',
                'risk_level': 'normal',
                'needs_human': False,
                'create_issue_proposal': None,
            }
            ts = now_ms()
            output_snapshot = {
                'source': 'artifact_fallback',
                'payload': normalized_payload,
            }
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('succeeded', ts, normalized_payload['summary'], json.dumps(output_snapshot, ensure_ascii=False), 'artifact_fallback', ts, row['attempt_id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, updated_at_ms = ? WHERE id = ?',
                ('review', ts, row['issue_id']),
            )
            self._record_checkpoint(
                issue_id=row['issue_id'],
                attempt_id=row['attempt_id'],
                kind='progress',
                summary='Execution completed via artifact fallback',
                details_md=json.dumps(output_snapshot, ensure_ascii=False),
                next_action='Hand off to next role / review',
                created_by_employee_id=row['assigned_employee_id'],
                percent_complete=100,
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=row['issue_id'],
                attempt_id=row['attempt_id'],
                action_type='execution_succeeded',
                summary='Execution succeeded via artifact fallback',
                actor_employee_id=row['assigned_employee_id'],
                details={'artifact_payload': artifact_callback, 'issue_status': 'review'},
            )
            self.db.commit()
            result['status'] = 'succeeded'
            result['issue_status'] = 'review'
            result['wait_result'] = output_snapshot
            return result

        if expected_text or expected_marker:
            adapter = OpenClawExecutionAdapter(effective_session_key)
            try:
                if expected_marker:
                    wait_result = adapter.wait_for_json_marker(
                        marker=expected_marker,
                        timeout_seconds=timeout_seconds,
                        min_timestamp_ms=row['created_at_ms'],
                    )
                else:
                    wait_result = adapter.wait_for_exact_text(
                        expected_text=expected_text,
                        timeout_seconds=timeout_seconds,
                        min_timestamp_ms=row['created_at_ms'],
                    )
                ts = now_ms()
                self.db.conn.execute(
                    'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                    ('succeeded', ts, 'Observed structured completion' if expected_marker else 'Observed exact completion text', json.dumps(wait_result, ensure_ascii=False), 'transcript_marker', ts, row['attempt_id']),
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
                record_issue_activity(
                    self.db.conn,
                    now_ms=ts,
                    issue_id=row['issue_id'],
                    attempt_id=row['attempt_id'],
                    action_type='execution_succeeded',
                    summary='Execution succeeded',
                    actor_employee_id=row['assigned_employee_id'],
                    details={'wait_result': wait_result, 'issue_status': next_issue_status},
                )
                self.db.commit()
                result['status'] = 'succeeded'
                result['issue_status'] = next_issue_status
                result['wait_result'] = wait_result
            except RunAbortedObserved as e:
                lifecycle = self.observe_dispatch_lifecycle_event(
                    dispatch_ref=dispatch_ref,
                    state='aborted',
                    stop_reason=e.stop_reason or 'aborted',
                    error_message=e.error_message,
                    payload={'observed_via': 'sessions.get', 'timestamp': e.timestamp},
                )
                result['status'] = 'cancelled'
                result['lifecycle_result'] = lifecycle
            except RunErrorObserved as e:
                lifecycle = self.observe_dispatch_lifecycle_event(
                    dispatch_ref=dispatch_ref,
                    state='error',
                    error_message=e.error_message or 'run errored',
                    payload={'observed_via': 'sessions.get', 'timestamp': e.timestamp},
                )
                result['status'] = 'failed'
                result['lifecycle_result'] = lifecycle
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
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            action_type='execution_cancelled',
            summary='Execution cancelled',
            actor_employee_id=attempt['assigned_employee_id'],
            details={'reason': reason, 'abort_payload': abort_payload},
        )
        self.db.commit()
        return {
            'dispatch_ref': dispatch_ref,
            'status': 'cancelled',
            'reason': reason,
            'abort_payload': abort_payload,
            'updated_at_ms': ts,
        }


    def reconcile_stale_attempt(self, *, dispatch_ref: str, reason: str = 'stale_dispatch_reconciled') -> dict[str, Any]:
        attempt = self.db.get_one(
            '''SELECT ia.id, ia.issue_id, ia.assigned_employee_id
               FROM issue_attempts ia
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        ts = now_ms()
        payload = {'reconciled': True, 'dispatch_ref': dispatch_ref, 'reason': reason}
        self.db.conn.execute(
            'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, updated_at_ms = ? WHERE id = ?',
            ('cancelled', 'stale_reconciled', reason, ts, json.dumps(payload, ensure_ascii=False), ts, attempt['id']),
        )
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
            ('ready', reason, ts, attempt['issue_id']),
        )
        self._record_checkpoint(
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            kind='system',
            summary='Stale dispatch reconciled',
            details_md=json.dumps(payload, ensure_ascii=False),
            next_action='Retry dispatch',
            created_by_employee_id=attempt['assigned_employee_id'],
            percent_complete=None,
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            action_type='execution_cancelled',
            summary='Stale dispatch reconciled back to ready',
            actor_employee_id=attempt['assigned_employee_id'],
            details=payload,
        )
        self.db.commit()
        return {
            'dispatch_ref': dispatch_ref,
            'status': 'cancelled',
            'reason': reason,
            'reconciled': True,
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

    def observe_dispatch_lifecycle_event(
        self,
        *,
        dispatch_ref: str,
        state: str,
        stop_reason: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt = self.db.get_one(
            '''SELECT ia.id, ia.issue_id, ia.assigned_employee_id, ia.status, ia.output_snapshot_json
               FROM issue_attempts ia
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        if str(attempt['status']) not in {'dispatching', 'running'}:
            return {
                'dispatch_ref': dispatch_ref,
                'ignored': True,
                'reason': f'attempt already in terminal-ish status: {attempt["status"]}',
            }
        ts = now_ms()
        system_payload = {
            'source': 'system_chat_event',
            'state': state,
            'stop_reason': stop_reason,
            'error_message': error_message,
            'payload': payload or {},
        }
        if state == 'final':
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('succeeded', ts, 'System observer saw final chat event', json.dumps(system_payload, ensure_ascii=False), 'system_chat_final', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, updated_at_ms = ? WHERE id = ?',
                ('review', ts, attempt['issue_id']),
            )
            action_type = 'execution_succeeded'
            summary = 'Execution succeeded via system chat event'
            checkpoint_summary = 'System observer saw final chat event'
        elif state == 'error':
            fail_summary = error_message or 'system_chat_error'
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('failed', 'system_chat_error', fail_summary, ts, json.dumps(system_payload, ensure_ascii=False), 'system_chat_error', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
                ('review', fail_summary, ts, attempt['issue_id']),
            )
            action_type = 'execution_failed'
            summary = 'Execution failed via system chat event'
            checkpoint_summary = 'System observer saw error chat event'
        elif state == 'aborted':
            fail_summary = stop_reason or 'system_chat_aborted'
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('cancelled', 'system_chat_aborted', fail_summary, ts, json.dumps(system_payload, ensure_ascii=False), 'system_chat_aborted', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
                ('ready', fail_summary, ts, attempt['issue_id']),
            )
            action_type = 'execution_cancelled'
            summary = 'Execution aborted via system chat event'
            checkpoint_summary = 'System observer saw aborted chat event'
        else:
            raise ValidationError(f'unsupported lifecycle state: {state}')

        self._record_checkpoint(
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            kind='system',
            summary=checkpoint_summary,
            details_md=json.dumps(system_payload, ensure_ascii=False),
            next_action='Review lifecycle event impact',
            created_by_employee_id=attempt['assigned_employee_id'],
            percent_complete=None,
        )
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=attempt['issue_id'],
            attempt_id=attempt['id'],
            action_type=action_type,
            summary=summary,
            actor_employee_id=attempt['assigned_employee_id'],
            details=system_payload,
        )
        self.db.commit()
        return {
            'dispatch_ref': dispatch_ref,
            'state': state,
            'updated_at_ms': ts,
            'issue_id': attempt['issue_id'],
            'attempt_id': attempt['id'],
            'ignored': False,
        }

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
            'create_issue_proposal': None,
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
        issue = self.db.get_one('SELECT assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
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
        issue = self.db.get_one('SELECT assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='human_enqueued',
            summary=f'Issue entered human queue ({human_type})',
            actor_employee_id=issue['assigned_employee_id'],
            details={'human_type': human_type, 'prompt': prompt, 'required_input': required_input},
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
        issue_row = self.db.get_one('SELECT assigned_employee_id, metadata_json FROM issues WHERE id = ?', (issue_id,))
        metadata = json.loads(issue_row['metadata_json']) if issue_row['metadata_json'] else {}
        if resolution == 'approve':
            new_status = 'ready'
            blocker = None
            required = None
            metadata['human_resolution_strategy'] = 'return_ready_auto'
        elif resolution == 'reject':
            new_status = 'failed'
            blocker = note or 'Rejected by human'
            required = None
            metadata['human_resolution_strategy'] = 'rejected'
        else:
            new_status = 'waiting_human_info'
            blocker = note or 'Human requested more information'
            required = note or 'Provide additional information'
            metadata['human_resolution_strategy'] = 'needs_info'
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, required_human_input = ?, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
            (new_status, blocker, required, json.dumps(metadata, ensure_ascii=False), ts, issue_id),
        )
        issue = {'assigned_employee_id': issue_row['assigned_employee_id']}
        record_issue_activity(
            self.db.conn,
            now_ms=ts,
            issue_id=issue_id,
            action_type='human_resolved',
            summary=f'Human queue resolved: {resolution}',
            actor_employee_id=issue['assigned_employee_id'],
            details={'resolution': resolution, 'note': note, 'new_status': new_status},
        )
        self.db.commit()
        return {
            'issue_id': issue_id,
            'status': new_status,
            'resolution': resolution,
            'updated_at_ms': ts,
        }

    def get_issue_activity(self, *, issue_id: str) -> dict[str, Any]:
        return {
            'items': fetch_issue_activity(self.db.conn, issue_id),
            'total': len(fetch_issue_activity(self.db.conn, issue_id)),
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
        return {
            'issue': dict(issue),
            'attempts': [dict(r) for r in attempts],
            'callbacks_by_attempt': callbacks_by_attempt,
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
                      rb.binding_key
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
                'issues': [],
            })
            grouped[agent_id]['issues'].append({
                'issue_id': row['issue_id'],
                'issue_no': row['issue_no'],
                'title': row['title'],
                'status': row['status'],
                'priority': row['priority'],
            })
        return {
            'items': list(grouped.values()),
            'total': len(grouped),
        }

    def get_board_snapshot(self) -> dict[str, Any]:
        agent_queue = self.db.fetch_all('SELECT * FROM v_agent_queue ORDER BY updated_at_ms DESC')
        human_queue = self.db.fetch_all('SELECT * FROM v_human_queue ORDER BY updated_at_ms DESC')
        employees = self.db.fetch_all(
            '''SELECT ei.employee_key,
                      ei.display_name,
                      ei.employment_scope,
                      p.project_key,
                      rt.template_key AS role,
                      ei.status,
                      rb.agent_id,
                      rb.session_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               LEFT JOIN projects p ON p.id = ei.project_id
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
            'agent_workload': self.get_agent_workload()['items'],
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
