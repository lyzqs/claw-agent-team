from __future__ import annotations

import json
from typing import Any

from .activity import fetch_issue_activity, record_issue_activity
from .db import ValidationError, now_ms
from .routing_policy import route_issue

WAITING_HUMAN_STATUS_BY_TYPE = {
    'info': 'waiting_human_info',
    'action': 'waiting_human_action',
    'approval': 'waiting_human_approval',
}
WAITING_HUMAN_STATUSES = set(WAITING_HUMAN_STATUS_BY_TYPE.values())


def stringify_human_detail(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ('summary', 'detail', 'message', 'text', 'reason', 'title'):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    if isinstance(value, list):
        parts = [stringify_human_detail(item) for item in value]
        return '；'.join(part for part in parts if part)
    return str(value)


def infer_human_type(payload: dict[str, Any]) -> str:
    explicit = str(payload.get('human_type') or '').strip()
    if explicit in WAITING_HUMAN_STATUS_BY_TYPE:
        return explicit

    text = ' '.join(
        part for part in [
            stringify_human_detail(payload.get('summary')),
            stringify_human_detail(payload.get('reason')),
            stringify_human_detail(payload.get('required_human_input')),
            stringify_human_detail(payload.get('human_prompt')),
            stringify_human_detail(payload.get('blocking_findings')),
        ]
        if part
    ).lower()
    if any(marker in text for marker in ['补充信息', '更多信息', '需要信息', 'provide info', 'need info', 'clarify', '澄清']):
        return 'info'
    if any(marker in text for marker in ['批准', '审批', '确认', '拍板', 'approve', 'approval', 'review']):
        return 'approval'
    return 'action'


def derive_human_queue_request(payload: dict[str, Any]) -> dict[str, str]:
    human_type = infer_human_type(payload)
    summary = stringify_human_detail(payload.get('summary'))
    reason = stringify_human_detail(payload.get('reason'))
    findings = stringify_human_detail(payload.get('blocking_findings'))
    prompt = stringify_human_detail(payload.get('human_prompt')) or summary or findings or reason
    required_input = stringify_human_detail(payload.get('required_human_input')) or findings

    if not prompt:
        prompt = {
            'info': '该 issue 需要人工补充信息后再继续推进。',
            'action': '该 issue 需要人工执行额外动作或给出处理决定。',
            'approval': '该 issue 需要人工确认或批准后再继续推进。',
        }[human_type]
    if not required_input:
        required_input = {
            'info': reason or '请补充继续推进该 issue 所需的关键信息。',
            'action': reason or '请说明需要人工采取的动作或给出下一步处理决定。',
            'approval': reason or '请明确确认是否批准当前方案或是否结束该 issue。',
        }[human_type]

    return {
        'human_type': human_type,
        'prompt': prompt,
        'required_input': required_input,
    }


class HumanQueueService:
    def __init__(self, db):
        self.db = db

    def _employee_role(self, employee_id: str) -> str:
        row = self.db.get_one(
            '''SELECT rt.template_key AS role
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE ei.id = ?''',
            (employee_id,),
        )
        return str(row['role'])

    def _pick_employee_key(self, *, project_id: str, role: str) -> str | None:
        if role == 'ceo':
            row = self.db.conn.execute(
                '''SELECT ei.employee_key
                   FROM employee_instances ei
                   JOIN role_templates rt ON rt.id = ei.role_template_id
                   WHERE rt.template_key = 'ceo'
                   ORDER BY ei.employee_key ASC
                   LIMIT 1'''
            ).fetchone()
            return row[0] if row else None
        row = self.db.conn.execute(
            '''SELECT ei.employee_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE rt.template_key = ? AND ei.project_id = ?
               ORDER BY ei.employee_key ASC
               LIMIT 1''',
            (role, project_id),
        ).fetchone()
        return row[0] if row else None

    def _merge_human_context(self, metadata: dict[str, Any], *, resolution: str, note: str, issue_id: str, next_role: str | None, next_employee_key: str | None) -> dict[str, Any]:
        history = metadata.get('human_context_history') if isinstance(metadata.get('human_context_history'), list) else []
        item = {
            'resolution': resolution,
            'note': note.strip(),
            'issue_id': issue_id,
            'next_role': (next_role or '').strip() or None,
            'next_employee_key': (next_employee_key or '').strip() or None,
            'updated_at_ms': now_ms(),
        }
        history.append(item)
        metadata['human_context_history'] = history[-10:]
        metadata['human_context'] = item
        metadata['last_human_note'] = item['note']
        metadata['needs_human'] = False
        metadata['human_resolution'] = resolution
        return metadata

    def enqueue_human(
        self,
        *,
        issue_id: str,
        human_type: str,
        prompt: str,
        required_input: str,
    ) -> dict[str, Any]:
        if human_type not in WAITING_HUMAN_STATUS_BY_TYPE:
            raise ValidationError(f'unsupported human_type: {human_type}')
        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issues SET status = ?, blocker_summary = ?, required_human_input = ?, updated_at_ms = ? WHERE id = ?',
            (WAITING_HUMAN_STATUS_BY_TYPE[human_type], prompt, required_input, ts, issue_id),
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
            'status': WAITING_HUMAN_STATUS_BY_TYPE[human_type],
            'prompt': prompt,
            'required_input': required_input,
        }

    def resolve_human_action(
        self,
        *,
        issue_id: str,
        resolution: str,
        note: str = '',
        next_employee_key: str | None = None,
        next_role: str | None = None,
    ) -> dict[str, Any]:
        ts = now_ms()
        if resolution not in {'approve', 'reject', 'needs_info'}:
            raise ValidationError(f'unsupported resolution: {resolution}')
        issue_row = self.db.get_one(
            'SELECT project_id, assigned_employee_id, metadata_json FROM issues WHERE id = ?',
            (issue_id,),
        )
        metadata = json.loads(issue_row['metadata_json']) if issue_row['metadata_json'] else {}
        metadata = self._merge_human_context(
            metadata,
            resolution=resolution,
            note=note,
            issue_id=issue_id,
            next_role=next_role,
            next_employee_key=next_employee_key,
        )
        route_target = next_employee_key
        if resolution == 'approve' and not route_target and isinstance(next_role, str) and next_role.strip():
            route_target = self._pick_employee_key(project_id=issue_row['project_id'], role=next_role.strip())

        if resolution == 'approve' and route_target:
            current_assigned = issue_row['assigned_employee_id']
            if current_assigned is None:
                raise ValidationError('issue has no currently assigned employee')
            from_role = self._employee_role(current_assigned)
            employee = self.db.get_one(
                '''SELECT ei.id, ei.employee_key, rt.template_key AS role
                   FROM employee_instances ei
                   JOIN role_templates rt ON rt.id = ei.role_template_id
                   WHERE ei.employee_key = ?''',
                (route_target,),
            )
            to_role = str(employee['role'])
            decision = route_issue(from_role=from_role, to_role=to_role, issue_type='normal', risk_level=str(metadata.get('risk_level') or 'normal'))
            if not decision.allowed:
                raise ValidationError(decision.reason)
            metadata['human_resolution_strategy'] = 'approved_and_routed'
            metadata['human_resolution_target'] = employee['employee_key']
            metadata['prior_handoff'] = {
                'summary': note.strip() or '人工已确认并指定下一步流转。',
                'reason': note.strip() or 'human confirmed',
                'suggested_next_role': to_role,
                'needs_human': False,
                'risk_level': str(metadata.get('risk_level') or 'normal'),
            }
            self.db.conn.execute(
                'UPDATE issues SET status = ?, assigned_employee_id = ?, blocker_summary = NULL, required_human_input = NULL, metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                ('ready', employee['id'], json.dumps(metadata, ensure_ascii=False), ts, issue_id),
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=issue_id,
                action_type='human_resolved',
                summary='Human queue resolved: approve',
                actor_employee_id=current_assigned,
                details={'resolution': resolution, 'note': note, 'new_status': 'ready', 'routed_to': employee['employee_key']},
            )
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=issue_id,
                action_type='handoff',
                summary=f'Handoff {from_role} -> {to_role} after human approval',
                actor_employee_id=employee['id'],
                details={'to_employee_key': employee['employee_key'], 'note': note or 'human-approved route', 'routing_reason': decision.reason},
            )
            self.db.commit()
            return {
                'issue_id': issue_id,
                'status': 'ready',
                'resolution': resolution,
                'assigned_employee_key': employee['employee_key'],
                'updated_at_ms': ts,
            }

        current_assigned = issue_row['assigned_employee_id']
        if resolution == 'approve':
            new_status = 'review'
            blocker = note.strip() or 'Human confirmed. Continue with the next explicit agent judgment.'
            required = None
            metadata['human_resolution_strategy'] = 'approved_resume_review'
            metadata['prior_handoff'] = {
                'summary': note.strip() or '人工已确认，请基于人工确认结果继续推进。',
                'reason': note.strip() or 'human confirmed',
                'suggested_next_role': self._employee_role(current_assigned) if current_assigned else '',
                'needs_human': False,
                'risk_level': str(metadata.get('risk_level') or 'normal'),
            }
        elif resolution == 'reject':
            new_status = 'failed'
            blocker = note or 'Rejected by human'
            required = None
            metadata['human_resolution_strategy'] = 'rejected'
            metadata['prior_handoff'] = {
                'summary': note.strip() or '人工已驳回当前 issue。',
                'reason': note.strip() or 'human rejected',
                'suggested_next_role': 'close',
                'needs_human': False,
                'risk_level': str(metadata.get('risk_level') or 'normal'),
            }
        else:
            new_status = 'review'
            blocker = note or 'Human provided more information'
            required = None
            metadata['human_resolution_strategy'] = 'needs_info_resume_review'
            metadata['prior_handoff'] = {
                'summary': note.strip() or '人工已补充信息，请基于补充内容继续推进。',
                'reason': note.strip() or 'human provided additional information',
                'suggested_next_role': self._employee_role(current_assigned) if current_assigned else '',
                'needs_human': False,
                'risk_level': str(metadata.get('risk_level') or 'normal'),
            }
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

    def get_human_queue(self) -> dict[str, Any]:
        rows = self.db.fetch_all('SELECT * FROM v_human_queue ORDER BY updated_at_ms DESC')
        return {
            'items': [dict(r) for r in rows],
            'total': len(rows),
        }

    def get_issue_activity(self, *, issue_id: str) -> dict[str, Any]:
        items = fetch_issue_activity(self.db.conn, issue_id)
        return {
            'items': items,
            'total': len(items),
        }
