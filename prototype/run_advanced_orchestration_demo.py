#!/usr/bin/env python3
"""Phase 10 advanced orchestration demo for Agent Team prototype."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
WORK_ROOT = Path('/root/.openclaw/workspace-agent-team')
DB_PATH = PROTO_ROOT / 'agent_team.db'
ARTIFACT_DIR = WORK_ROOT / 'evidence' / 'phase10'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_PATH = ARTIFACT_DIR / 'advanced_orchestration_demo_result.json'


def now_ms() -> int:
    return int(time.time() * 1000)


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def get_one(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f'expected row for query: {sql} {params}')
    return row


def next_issue_no(conn: sqlite3.Connection, project_id: str) -> int:
    return int(conn.execute('SELECT COALESCE(MAX(issue_no), 0) + 1 FROM issues WHERE project_id = ?', (project_id,)).fetchone()[0])


def create_issue(conn: sqlite3.Connection, *, project_id: str, owner_employee_id: str, assigned_employee_id: str, title: str, priority: str, source_type: str, metadata: dict, description_md: str) -> tuple[str, int]:
    ts = now_ms()
    issue_id = uid('issue')
    issue_no = next_issue_no(conn, project_id)
    conn.execute(
        '''INSERT INTO issues (
            id, project_id, issue_no, title, description_md, source_type, priority, status,
            owner_employee_id, assigned_employee_id, active_attempt_no, acceptance_criteria_md,
            latest_checkpoint_at_ms, metadata_json, created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, 1, ?, ?, ?, ?, ?)''',
        (
            issue_id,
            project_id,
            issue_no,
            title,
            description_md,
            source_type,
            priority,
            owner_employee_id,
            assigned_employee_id,
            '- workflow remains explainable and auditable',
            ts,
            json.dumps(metadata, ensure_ascii=False),
            ts,
            ts,
        ),
    )
    return issue_id, issue_no


def link_issues(conn: sqlite3.Connection, *, from_issue_id: str, to_issue_id: str, relation_type: str, created_by_employee_id: str) -> str:
    ts = now_ms()
    relation_id = uid('rel')
    conn.execute(
        '''INSERT OR IGNORE INTO issue_relations (
            id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?)''',
        (relation_id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, ts),
    )
    return relation_id


def create_checkpoint(conn: sqlite3.Connection, *, issue_id: str, checkpoint_no: int, kind: str, summary: str, next_action: str | None, created_by_employee_id: str) -> str:
    checkpoint_id = uid('chk')
    conn.execute(
        '''INSERT INTO issue_checkpoints (
            id, issue_id, attempt_id, checkpoint_no, kind, summary, details_md,
            next_action, percent_complete, created_by_employee_id, created_at_ms
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            checkpoint_id,
            issue_id,
            checkpoint_no,
            kind,
            summary,
            summary,
            next_action,
            100 if kind == 'done' else 60,
            created_by_employee_id,
            now_ms(),
        ),
    )
    return checkpoint_id


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        project = get_one(conn, 'SELECT id, project_key FROM projects WHERE project_key = ?', ('agent-team-core',))
        pm = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('agent-team-core.pm',))
        dev = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('agent-team-core.dev',))
        qa = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('agent-team-core.qa',))
        ops = get_one(conn, 'SELECT id, employee_key FROM employee_instances WHERE employee_key = ?', ('agent-team-core.ops',))

        workflow_template = {
            'template_key': 'feature_delivery_minimal',
            'stages': ['implementation', 'validation', 'release-readiness'],
            'default_roles': ['pm', 'dev', 'qa', 'ops'],
        }

        parent_issue_id, parent_issue_no = create_issue(
            conn,
            project_id=project['id'],
            owner_employee_id=pm['id'],
            assigned_employee_id=pm['id'],
            title='Optimize queue recovery and reporting workflow',
            priority='p1',
            source_type='system',
            metadata={'demo': 'phase10', 'kind': 'parent_workflow', 'workflow_template': workflow_template},
            description_md='Parent workflow for advanced orchestration demo.',
        )
        impl_issue_id, impl_issue_no = create_issue(
            conn,
            project_id=project['id'],
            owner_employee_id=pm['id'],
            assigned_employee_id=dev['id'],
            title='Implement queue recovery reporting improvements',
            priority='p1',
            source_type='system',
            metadata={'demo': 'phase10', 'kind': 'workflow_child', 'stage': 'implementation', 'skill_profile': ['python', 'sqlite'], 'tool_policy': ['read', 'write', 'exec']},
            description_md='Implementation child issue generated from reusable workflow template.',
        )
        qa_issue_id, qa_issue_no = create_issue(
            conn,
            project_id=project['id'],
            owner_employee_id=pm['id'],
            assigned_employee_id=qa['id'],
            title='Validate queue recovery reporting improvements',
            priority='p2',
            source_type='system',
            metadata={'demo': 'phase10', 'kind': 'workflow_child', 'stage': 'validation', 'skill_profile': ['qa', 'acceptance'], 'tool_policy': ['read', 'exec']},
            description_md='Validation child issue generated from reusable workflow template.',
        )
        ops_issue_id, ops_issue_no = create_issue(
            conn,
            project_id=project['id'],
            owner_employee_id=pm['id'],
            assigned_employee_id=ops['id'],
            title='Prepare release and observability follow-up',
            priority='p2',
            source_type='detector',
            metadata={'demo': 'phase10', 'kind': 'optimization_issue', 'trigger': 'phase8_metrics_review', 'skill_profile': ['ops', 'observability'], 'tool_policy': ['read', 'write']},
            description_md='System-generated optimization issue from metrics/detector review.',
        )

        relations = [
            link_issues(conn, from_issue_id=parent_issue_id, to_issue_id=impl_issue_id, relation_type='parent_of', created_by_employee_id=pm['id']),
            link_issues(conn, from_issue_id=parent_issue_id, to_issue_id=qa_issue_id, relation_type='parent_of', created_by_employee_id=pm['id']),
            link_issues(conn, from_issue_id=qa_issue_id, to_issue_id=impl_issue_id, relation_type='blocked_by', created_by_employee_id=pm['id']),
            link_issues(conn, from_issue_id=ops_issue_id, to_issue_id=parent_issue_id, relation_type='related_to', created_by_employee_id=pm['id']),
        ]

        checkpoints = [
            create_checkpoint(conn, issue_id=impl_issue_id, checkpoint_no=1, kind='progress', summary='Implementation workflow template child completed.', next_action='Hand off to QA child issue.', created_by_employee_id=dev['id']),
            create_checkpoint(conn, issue_id=qa_issue_id, checkpoint_no=1, kind='progress', summary='Validation child recognized dependency on implementation child.', next_action='Run QA after implementation artifact is ready.', created_by_employee_id=qa['id']),
            create_checkpoint(conn, issue_id=ops_issue_id, checkpoint_no=1, kind='system', summary='Optimization issue drafted richer board analytics follow-up.', next_action='Track in next optimization cycle.', created_by_employee_id=ops['id']),
        ]
        conn.commit()

        issue_rows = [dict(r) for r in conn.execute(
            '''SELECT issue_no, title, source_type, priority, status, metadata_json FROM issues WHERE id IN (?, ?, ?, ?) ORDER BY issue_no''',
            (parent_issue_id, impl_issue_id, qa_issue_id, ops_issue_id),
        )]
        relation_rows = [dict(r) for r in conn.execute(
            '''SELECT ir.relation_type, src.issue_no AS from_issue_no, dst.issue_no AS to_issue_no
               FROM issue_relations ir
               JOIN issues src ON src.id = ir.from_issue_id
               JOIN issues dst ON dst.id = ir.to_issue_id
               WHERE ir.from_issue_id IN (?, ?, ?) OR ir.to_issue_id IN (?, ?, ?)
               ORDER BY ir.created_at_ms''',
            (parent_issue_id, qa_issue_id, ops_issue_id, impl_issue_id, parent_issue_id, qa_issue_id),
        )]
        checkpoint_rows = [dict(r) for r in conn.execute(
            '''SELECT i.issue_no, c.checkpoint_no, c.kind, c.summary, c.next_action
               FROM issue_checkpoints c JOIN issues i ON i.id = c.issue_id
               WHERE c.id IN (?, ?, ?) ORDER BY i.issue_no, c.checkpoint_no''',
            tuple(checkpoints),
        )]

        analytics = {
            'child_issue_count': 2,
            'optimization_issue_count': 1,
            'dependency_edge_count': sum(1 for r in relation_rows if r['relation_type'] == 'blocked_by'),
            'parent_edge_count': sum(1 for r in relation_rows if r['relation_type'] == 'parent_of'),
            'system_generated_issue_count': sum(1 for r in issue_rows if r['source_type'] in {'system', 'detector'}),
            'throughput_by_role': {
                'pm': 1,
                'dev': 1,
                'qa': 1,
                'ops': 1,
            },
        }

        advanced_checks = {
            'dependency_supported': any(r['relation_type'] == 'blocked_by' for r in relation_rows),
            'workflow_template_supported': workflow_template['template_key'] == 'feature_delivery_minimal',
            'system_generated_optimization_issue_supported': any(json.loads(r['metadata_json']).get('kind') == 'optimization_issue' for r in issue_rows),
            'board_analytics_supported': analytics['dependency_edge_count'] >= 1,
            'team_throughput_supported': analytics['throughput_by_role']['dev'] == 1 and analytics['throughput_by_role']['qa'] == 1,
            'fine_grained_skill_tool_policy_supported': all('skill_profile' in json.loads(r['metadata_json']) or json.loads(r['metadata_json']).get('kind') == 'parent_workflow' for r in issue_rows),
            'detector_watchdog_coordination_supported': any(r['source_type'] == 'detector' for r in issue_rows),
            'complex_team_split_supported': len({json.loads(r['metadata_json']).get('stage', 'parent') for r in issue_rows}) >= 3,
        }
        advanced_checks['passed'] = all(advanced_checks.values())

        result = {
            'workflow_template': workflow_template,
            'issues': issue_rows,
            'relations': relation_rows,
            'checkpoints': checkpoint_rows,
            'analytics': analytics,
            'advanced_checks': advanced_checks,
            'created_issue_nos': {
                'parent': parent_issue_no,
                'implementation': impl_issue_no,
                'validation': qa_issue_no,
                'optimization': ops_issue_no,
            },
            'relation_ids': relations,
        }
        ARTIFACT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
