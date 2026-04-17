from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .activity import record_issue_activity, record_attempt_callback_event
from .db import ValidationError, now_ms
from .human_queue_service import (
    WAITING_HUMAN_STATUSES,
    WAITING_HUMAN_STATUS_BY_TYPE,
    derive_human_queue_request,
)
from runtime.base import RunAbortedObserved, RunErrorObserved, TimeoutObserved
from runtime.openclaw_adapter import resolve_session_snapshot
from runtime.registry import build_runtime_context, get_runtime_adapter


def uid(prefix: str) -> str:
    import uuid
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


def is_retryable_system_error(message: str | None) -> bool:
    text = str(message or '').strip().lower()
    if not text:
        return False
    retryable_tokens = [
        '503',
        'overloaded',
        'temporarily unavailable',
        'timeout',
        'timed out',
        'rate limit',
        'too many requests',
        'econnreset',
        'socket hang up',
        'gateway timeout',
        'service unavailable',
    ]
    return any(token in text for token in retryable_tokens)


def resolve_session_snapshot(session_key: str) -> dict[str, Any]:
    parts = session_key.split(':')
    if len(parts) < 2 or parts[0] != 'agent':
        return {}
    agent_id = parts[1]
    sessions_path = Path('/root/.openclaw/agents') / agent_id / 'sessions' / 'sessions.json'
    if not sessions_path.exists():
        return {}
    try:
        data = json.loads(sessions_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    entry = data.get(session_key)
    return entry if isinstance(entry, dict) else {}


class DispatchService:
    def __init__(self, db, record_checkpoint):
        self.db = db
        self._record_checkpoint = record_checkpoint

    def _next_attempt_no(self, issue_id: str) -> int:
        row = self.db.conn.execute(
            'SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM issue_attempts WHERE issue_id = ?',
            (issue_id,),
        ).fetchone()
        return int(row[0])

    def dispatch_execution(
        self,
        *,
        issue_id: str,
        runtime_binding_key: str,
        payload: dict[str, Any],
        dispatch_ref: str | None = None,
    ) -> dict[str, Any]:
        issue = self.db.get_one('SELECT id, assigned_employee_id FROM issues WHERE id = ?', (issue_id,))
        binding = self.db.get_one('SELECT id, employee_id, binding_key, session_key, runtime_type, agent_id, metadata_json FROM runtime_bindings WHERE binding_key = ?', (runtime_binding_key,))
        if issue['assigned_employee_id'] and issue['assigned_employee_id'] != binding['employee_id']:
            raise ValidationError('runtime binding employee does not match assigned employee')
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValidationError('payload.prompt is required for real dispatch')
        effective_session_key = payload.get('session_key') if isinstance(payload.get('session_key'), str) and payload.get('session_key').strip() else binding['session_key']

        flow_id = str(payload.get('flow_id') or uid('flow'))
        callback_token = str(payload.get('callback_token') or uid('cbtok'))
        timeout_deadline_ms = int(payload.get('timeout_deadline_ms') or (now_ms() + 30 * 60 * 1000))
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

        runtime_ctx = build_runtime_context(binding)
        adapter = get_runtime_adapter(runtime_ctx)
        dispatch = adapter.dispatch(prompt=payload['prompt'], dispatch_id=dispatch_ref)

        real_dispatch_ref = dispatch['dispatch_ref']
        session_snapshot = resolve_session_snapshot(effective_session_key)
        runtime_session_id = session_snapshot.get('sessionId') if isinstance(session_snapshot.get('sessionId'), str) else None
        runtime_session_file = session_snapshot.get('sessionFile') if isinstance(session_snapshot.get('sessionFile'), str) else None

        self.db.conn.execute(
            '''INSERT INTO issue_attempts (
                id, issue_id, attempt_no, assigned_employee_id, runtime_binding_id,
                dispatch_kind, status, dispatch_ref, input_snapshot_json, metadata_json,
                flow_id, callback_token, callback_status, artifact_status, timeout_deadline_ms,
                runtime_session_key, runtime_session_id, runtime_session_file,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, 'run', 'dispatching', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
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
                effective_session_key,
                runtime_session_id,
                runtime_session_file,
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
            details_md=json.dumps({'dispatch_ref': real_dispatch_ref, 'binding_key': binding['binding_key'], 'flow_id': flow_id, 'callback_token': callback_token, 'session_key': effective_session_key, 'session_id': runtime_session_id, 'session_file': runtime_session_file}, ensure_ascii=False),
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
            details={'dispatch_ref': real_dispatch_ref, 'binding_key': binding['binding_key'], 'session_key': effective_session_key, 'session_id': runtime_session_id, 'session_file': runtime_session_file, 'flow_id': flow_id, 'callback_token': callback_token},
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
            'runtime_session_id': runtime_session_id,
            'runtime_session_file': runtime_session_file,
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

        if phase == 'terminal_handoff':
            normalized_payload = dict(payload) if isinstance(payload, dict) else {}
            suggested_next_role = str(normalized_payload.get('suggested_next_role') or default_next_role_for(metadata.get('attempt_role')))
            normalized_payload['suggested_next_role'] = suggested_next_role
            normalized_payload.setdefault('artifacts', [])
            normalized_payload.setdefault('blocking_findings', [])
            normalized_payload.setdefault('status', 'done')
            normalized_payload.setdefault('risk_level', 'normal')
            normalized_payload.setdefault('needs_human', False)
            normalized_payload.pop('create_issue_proposal', None)
            normalized_payload.pop('create_issue_proposals', None)
            normalized_payload.setdefault('summary', normalized_payload.get('reason') or 'Terminal callback accepted')
            issue_current = self.db.get_one('SELECT status, blocker_summary, required_human_input FROM issues WHERE id = ?', (attempt['issue_id'],))
            if normalized_payload.get('needs_human') or str(normalized_payload.get('status') or '').strip() == 'needs_human':
                human_request = derive_human_queue_request(normalized_payload)
                next_issue_status = WAITING_HUMAN_STATUS_BY_TYPE[human_request['human_type']]
                blocker_summary = human_request['prompt']
                required_human_input = human_request['required_input']
                closed_at = None
            else:
                next_issue_status = 'review' if str(issue_current['status'] or '') not in WAITING_HUMAN_STATUSES else str(issue_current['status'])
                blocker_summary = None if next_issue_status == 'review' else issue_current['blocker_summary']
                required_human_input = None if next_issue_status == 'review' else issue_current['required_human_input']
                closed_at = None
            output_snapshot = {
                'source': 'callback_terminal',
                'payload': normalized_payload,
            }
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, ended_at_ms = ?, result_summary = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('succeeded', ts, str(normalized_payload.get('summary') or normalized_payload.get('reason') or 'Terminal callback accepted'), json.dumps(output_snapshot, ensure_ascii=False), 'callback_terminal', ts, attempt_id),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
                (next_issue_status, closed_at, blocker_summary, required_human_input, ts, attempt['issue_id']),
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
                      i.status AS issue_status, i.blocker_summary, i.required_human_input,
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
            normalized_payload.pop('create_issue_proposal', None)
            normalized_payload.pop('create_issue_proposals', None)
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
            human_request = None
            if normalized_payload.get('needs_human') or str(normalized_payload.get('status') or '').strip() == 'needs_human':
                human_request = derive_human_queue_request(normalized_payload)
                next_issue_status = WAITING_HUMAN_STATUS_BY_TYPE[human_request['human_type']]
                blocker_summary = human_request['prompt']
                required_human_input = human_request['required_input']
                closed_at = None
            else:
                next_issue_status = 'closed' if close_issue_on_success else ('review' if str(row['issue_status'] or '') not in WAITING_HUMAN_STATUSES else str(row['issue_status']))
                blocker_summary = None if next_issue_status == 'review' else row['blocker_summary']
                required_human_input = None if next_issue_status == 'review' else row['required_human_input']
                closed_at = ts if close_issue_on_success and next_issue_status == 'closed' else None
            self.db.conn.execute(
                'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
                (next_issue_status, closed_at, blocker_summary, required_human_input, ts, row['issue_id']),
            )
            self._record_checkpoint(
                issue_id=row['issue_id'],
                attempt_id=row['attempt_id'],
                kind='progress',
                summary='Execution completed via callback',
                details_md=json.dumps(output_snapshot, ensure_ascii=False),
                next_action='Await human resolution' if human_request else ('Close issue' if close_issue_on_success else 'Hand off to next role / review'),
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
            if human_request:
                result['human_queue'] = human_request
            result['wait_result'] = output_snapshot
            return result

        artifact_callback = callback_payload.get('artifact_callback') if isinstance(callback_payload.get('artifact_callback'), dict) else None
        if artifact_callback:
            result['artifact_callback'] = artifact_callback
            result['artifact_ready'] = bool(
                artifact_callback.get('artifact_type') == 'feishu_doc'
                or artifact_callback.get('doc_url')
                or artifact_callback.get('doc_token')
            )

        if expected_text or expected_marker:
            runtime_ctx = build_runtime_context({
                'runtime_type': 'openclaw_session',
                'binding_key': row['binding_key'] or 'derived-openclaw',
                'session_key': effective_session_key,
                'agent_id': None,
                'metadata_json': '{}',
            })
            adapter = get_runtime_adapter(runtime_ctx)
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
                observed_payload = wait_result.get('payload') if isinstance(wait_result.get('payload'), dict) else {}
                human_request = None
                if observed_payload and (observed_payload.get('needs_human') or str(observed_payload.get('status') or '').strip() == 'needs_human'):
                    human_request = derive_human_queue_request(observed_payload)
                    next_issue_status = WAITING_HUMAN_STATUS_BY_TYPE[human_request['human_type']]
                    blocker_summary = human_request['prompt']
                    required_human_input = human_request['required_input']
                    closed_at = None
                else:
                    next_issue_status = 'closed' if close_issue_on_success else ('review' if str(row['issue_status'] or '') not in WAITING_HUMAN_STATUSES else str(row['issue_status']))
                    blocker_summary = None if next_issue_status == 'review' else row['blocker_summary']
                    required_human_input = None if next_issue_status == 'review' else row['required_human_input']
                    closed_at = ts if close_issue_on_success and next_issue_status == 'closed' else None
                self.db.conn.execute(
                    'UPDATE issues SET status = ?, closed_at_ms = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
                    (next_issue_status, closed_at, blocker_summary, required_human_input, ts, row['issue_id']),
                )
                self._record_checkpoint(
                    issue_id=row['issue_id'],
                    attempt_id=row['attempt_id'],
                    kind='progress',
                    summary='Execution observed completion',
                    details_md=json.dumps(wait_result, ensure_ascii=False),
                    next_action='Await human resolution' if human_request else ('Close issue' if close_issue_on_success else 'Hand off to next role / review'),
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
                if human_request:
                    result['human_queue'] = human_request
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
            '''SELECT ia.id, ia.issue_id, ia.assigned_employee_id, rb.session_key, rb.runtime_type, rb.binding_key, rb.agent_id, rb.metadata_json
               FROM issue_attempts ia
               LEFT JOIN runtime_bindings rb ON rb.id = ia.runtime_binding_id
               WHERE ia.dispatch_ref = ?''',
            (dispatch_ref,),
        )
        runtime_ctx = build_runtime_context(attempt)
        adapter = get_runtime_adapter(runtime_ctx)
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
            fail_summary = 'system_chat_final_missing_business_completion'
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('cancelled', 'missing_business_completion', fail_summary, ts, json.dumps(system_payload, ensure_ascii=False), 'system_chat_final', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, updated_at_ms = ? WHERE id = ?',
                ('waiting_recovery_completion', fail_summary, ts, attempt['issue_id']),
            )
            action_type = 'execution_cancelled'
            summary = 'Execution ended without terminal callback or JSON completion'
            checkpoint_summary = 'System observer saw final chat event without business completion'
        elif state == 'error':
            fail_summary = error_message or 'system_chat_error'
            retryable = is_retryable_system_error(fail_summary)
            issue_row = self.db.get_one('SELECT metadata_json FROM issues WHERE id = ?', (attempt['issue_id'],))
            issue_meta = merge_json_object(issue_row['metadata_json'], {})
            if retryable:
                retry_state = dict(issue_meta.get('retry_state') or {}) if isinstance(issue_meta.get('retry_state'), dict) else {}
                retry_state.update({
                    'priority_boost': 'immediate',
                    'last_retryable_failure_at_ms': ts,
                    'last_retryable_failure_reason': fail_summary,
                    'last_retryable_failure_code': 'system_chat_error',
                    'retry_count': int(retry_state.get('retry_count') or 0) + 1,
                })
                issue_meta['retry_state'] = retry_state
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('failed', 'system_chat_error', fail_summary, ts, json.dumps(system_payload, ensure_ascii=False), 'system_chat_error', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                ('ready' if retryable else 'review', fail_summary, json.dumps(issue_meta, ensure_ascii=False), ts, attempt['issue_id']),
            )
            action_type = 'execution_failed'
            summary = 'Execution failed via system chat event'
            checkpoint_summary = 'System observer saw error chat event'
        elif state == 'aborted':
            fail_summary = stop_reason or 'system_chat_aborted'
            issue_row = self.db.get_one('SELECT metadata_json FROM issues WHERE id = ?', (attempt['issue_id'],))
            issue_meta = merge_json_object(issue_row['metadata_json'], {})
            retry_state = dict(issue_meta.get('retry_state') or {}) if isinstance(issue_meta.get('retry_state'), dict) else {}
            retry_state.update({
                'priority_boost': 'immediate',
                'last_retryable_failure_at_ms': ts,
                'last_retryable_failure_reason': fail_summary,
                'last_retryable_failure_code': 'system_chat_aborted',
                'retry_count': int(retry_state.get('retry_count') or 0) + 1,
            })
            issue_meta['retry_state'] = retry_state
            self.db.conn.execute(
                'UPDATE issue_attempts SET status = ?, failure_code = ?, failure_summary = ?, ended_at_ms = ?, output_snapshot_json = ?, completion_mode = ?, updated_at_ms = ? WHERE id = ?',
                ('cancelled', 'system_chat_aborted', fail_summary, ts, json.dumps(system_payload, ensure_ascii=False), 'system_chat_aborted', ts, attempt['id']),
            )
            self.db.conn.execute(
                'UPDATE issues SET status = ?, blocker_summary = ?, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                ('ready', fail_summary, json.dumps(issue_meta, ensure_ascii=False), ts, attempt['issue_id']),
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
