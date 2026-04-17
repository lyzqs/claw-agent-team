from __future__ import annotations

import json
from typing import Any

from .activity import record_issue_activity
from .db import now_ms


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


def proposal_dependencies(proposal: dict[str, Any]) -> list[str]:
    deps = proposal.get('depends_on_proposal_keys')
    if isinstance(deps, list):
        return [str(x).strip() for x in deps if str(x or '').strip()]
    metadata = proposal.get('metadata') if isinstance(proposal.get('metadata'), dict) else {}
    deps = metadata.get('depends_on_proposal_keys')
    if isinstance(deps, list):
        return [str(x).strip() for x in deps if str(x or '').strip()]
    return []


class DerivedIssueService:
    def __init__(self, db, *, create_issue, triage_issue):
        self.db = db
        self.create_issue = create_issue
        self.triage_issue = triage_issue

    def create_derived_issues(
        self,
        *,
        attempt_id: str,
        proposals: list[dict[str, Any]],
        created_by_role: str | None = None,
    ) -> dict[str, Any]:
        attempt = self.db.get_one(
            '''SELECT ia.id, ia.issue_id, ia.attempt_no, ia.assigned_employee_id, ia.derived_issues_json,
                      i.metadata_json, p.project_key, ei.employee_key AS assigned_employee_key
               FROM issue_attempts ia
               JOIN issues i ON i.id = ia.issue_id
               JOIN projects p ON p.id = i.project_id
               LEFT JOIN employee_instances ei ON ei.id = i.assigned_employee_id
               WHERE ia.id = ?''',
            (attempt_id,),
        )
        existing = merge_json_object(attempt['derived_issues_json'], {})
        existing_items = existing.get('items') if isinstance(existing.get('items'), list) else []
        source_issue = self.db.get_one(
            '''SELECT i.priority, i.metadata_json
               FROM issues i
               WHERE i.id = ?''',
            (attempt['issue_id'],),
        )
        issue_priority = str(source_issue['priority'] or 'p2').strip().lower()
        if issue_priority not in {'p0', 'p1', 'p2', 'p3', 'p4'}:
            issue_priority = 'p2'
        existing_by_key = {
            str(item.get('proposal_key')): item
            for item in existing_items
            if isinstance(item, dict) and isinstance(item.get('proposal_key'), str) and item.get('proposal_key').strip()
        }

        created_items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        fallback_owner = attempt['assigned_employee_key'] or 'shared.ceo'

        created_by_key: dict[str, dict[str, Any]] = {}

        for idx, proposal in enumerate(proposals, start=1):
            if not isinstance(proposal, dict):
                skipped_items.append({'index': idx, 'reason': 'proposal_not_object'})
                continue
            title = str(proposal.get('title') or '').strip()
            if not title:
                skipped_items.append({'index': idx, 'reason': 'missing_title'})
                continue
            proposal_key = str(proposal.get('proposal_key') or f'auto:{title}:{proposal.get("route_role") or "pm"}:{proposal.get("relation_type") or "related_to"}').strip()
            if proposal_key in existing_by_key:
                skipped_items.append({'index': idx, 'proposal_key': proposal_key, 'reason': 'duplicate_proposal_key', 'existing': existing_by_key[proposal_key]})
                continue

            metadata = dict(proposal.get('metadata') or {}) if isinstance(proposal.get('metadata'), dict) else {}
            metadata.update({
                'logical_source_type': 'agent',
                'created_from_issue_id': attempt['issue_id'],
                'created_from_attempt_no': attempt['attempt_no'],
                'created_from_role': created_by_role,
                'proposal_key': proposal_key,
                'source_issue_priority': issue_priority,
                'requested_priority': str(proposal.get('priority') or '').strip().lower() or None,
            })
            issue_metadata = merge_json_object(attempt['metadata_json'], {})
            issue_metadata.setdefault('resume_role', created_by_role or 'ceo')
            issue_metadata.setdefault('parent_wait_strategy', 'wait_children_then_review')
            source_type = str(proposal.get('source_type') or 'system')
            created = self.create_issue(
                project_key=str(proposal.get('project_key') or attempt['project_key']),
                owner_employee_key=str(proposal.get('owner_employee_key') or fallback_owner),
                title=title,
                description_md=str(proposal.get('description_md') or ''),
                acceptance_criteria_md=str(proposal.get('acceptance_criteria_md') or ''),
                priority=issue_priority,
                source_type=source_type if source_type in {'user', 'system', 'detector', 'watchdog', 'human'} else 'system',
                metadata=metadata,
            )
            route_role = str(proposal.get('route_role') or 'pm')
            assign_employee_key = None
            if route_role == 'ceo':
                row = self.db.conn.execute(
                    '''SELECT ei.employee_key
                       FROM employee_instances ei
                       JOIN role_templates rt ON rt.id = ei.role_template_id
                       WHERE rt.template_key = 'ceo'
                       ORDER BY ei.employee_key ASC
                       LIMIT 1'''
                ).fetchone()
                assign_employee_key = row[0] if row else None
            else:
                row = self.db.conn.execute(
                    '''SELECT ei.employee_key
                       FROM employee_instances ei
                       JOIN role_templates rt ON rt.id = ei.role_template_id
                       LEFT JOIN projects p ON p.id = ei.project_id
                       WHERE rt.template_key = ? AND p.project_key = ?
                       ORDER BY ei.employee_key ASC
                       LIMIT 1''',
                    (route_role, str(proposal.get('project_key') or attempt['project_key'])),
                ).fetchone()
                assign_employee_key = row[0] if row else None
            triaged = self.triage_issue(issue_id=created['issue_id'], assign_employee_key=assign_employee_key) if assign_employee_key else None
            relation_type = str(proposal.get('relation_type') or 'related_to')
            self.db.conn.execute(
                'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)',
                (uid('rel'), attempt['issue_id'], created['issue_id'], relation_type, attempt['assigned_employee_id'], now_ms()),
            )
            if relation_type == 'parent_of':
                issue_metadata['orchestration_parent'] = True
            self.db.conn.execute(
                'UPDATE issues SET metadata_json = ?, updated_at_ms = ? WHERE id = ?',
                (json.dumps(issue_metadata, ensure_ascii=False), now_ms(), attempt['issue_id']),
            )
            item = {
                'proposal_key': proposal_key,
                'title': title,
                'route_role': route_role,
                'relation_type': relation_type,
                'issue_id': created['issue_id'],
                'issue_no': created['issue_no'],
                'triaged_to': assign_employee_key,
            }
            created_items.append({'created': created, 'triaged': triaged, 'record': item})
            created_by_key[proposal_key] = item
            existing_by_key[proposal_key] = item
            existing_items.append(item)

        for idx, proposal in enumerate(proposals, start=1):
            if not isinstance(proposal, dict):
                continue
            proposal_key = str(proposal.get('proposal_key') or f'auto:{str(proposal.get("title") or "").strip()}:{proposal.get("route_role") or "pm"}:{proposal.get("relation_type") or "related_to"}').strip()
            current_item = created_by_key.get(proposal_key) or existing_by_key.get(proposal_key)
            if not current_item:
                continue
            current_issue_id = current_item.get('issue_id')
            for dep_key in proposal_dependencies(proposal):
                dep_item = created_by_key.get(dep_key) or existing_by_key.get(dep_key)
                dep_issue_id = dep_item.get('issue_id') if isinstance(dep_item, dict) else None
                if not dep_issue_id or dep_issue_id == current_issue_id:
                    continue
                exists = self.db.conn.execute(
                    '''SELECT 1 FROM issue_relations
                       WHERE from_issue_id = ? AND to_issue_id = ? AND relation_type = 'blocked_by'
                       LIMIT 1''',
                    (current_issue_id, dep_issue_id),
                ).fetchone()
                if exists:
                    continue
                self.db.conn.execute(
                    'INSERT INTO issue_relations (id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?)',
                    (uid('rel'), current_issue_id, dep_issue_id, 'blocked_by', attempt['assigned_employee_id'], now_ms()),
                )

        ts = now_ms()
        self.db.conn.execute(
            'UPDATE issue_attempts SET derived_issues_json = ?, updated_at_ms = ? WHERE id = ?',
            (json.dumps({'items': existing_items}, ensure_ascii=False), ts, attempt_id),
        )
        if created_items or skipped_items:
            record_issue_activity(
                self.db.conn,
                now_ms=ts,
                issue_id=attempt['issue_id'],
                attempt_id=attempt_id,
                action_type='derived_issues_processed',
                summary=f'Derived issues processed: created={len(created_items)} skipped={len(skipped_items)}',
                actor_employee_id=attempt['assigned_employee_id'],
                details={'created': created_items, 'skipped': skipped_items},
            )
        self.db.commit()
        return {
            'attempt_id': attempt_id,
            'source_issue_id': attempt['issue_id'],
            'created': created_items,
            'skipped': skipped_items,
        }
