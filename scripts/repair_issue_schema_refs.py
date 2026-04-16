#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path('/root/.openclaw/workspace-agent-team/state/agent_team.db')


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute('BEGIN')

        conn.execute('DROP VIEW IF EXISTS v_agent_queue')
        conn.execute('DROP VIEW IF EXISTS v_human_queue')

        conn.execute('ALTER TABLE issue_attempts RENAME TO issue_attempts__old_schema')
        conn.execute('ALTER TABLE issue_checkpoints RENAME TO issue_checkpoints__old_schema')
        conn.execute('ALTER TABLE issue_relations RENAME TO issue_relations__old_schema')

        conn.execute(
            '''CREATE TABLE issue_attempts (
              id TEXT PRIMARY KEY,
              issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
              attempt_no INTEGER NOT NULL,
              assigned_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
              runtime_binding_id TEXT REFERENCES runtime_bindings(id) ON DELETE SET NULL,
              dispatch_kind TEXT NOT NULL DEFAULT 'run'
                CHECK (dispatch_kind IN ('spawn', 'run', 'manual', 'system')),
              status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN (
                  'queued',
                  'dispatching',
                  'running',
                  'succeeded',
                  'failed',
                  'cancelled',
                  'timed_out',
                  'abandoned'
                )),
              dispatch_ref TEXT,
              started_at_ms INTEGER,
              last_heartbeat_at_ms INTEGER,
              ended_at_ms INTEGER,
              failure_code TEXT,
              failure_summary TEXT,
              result_summary TEXT,
              input_snapshot_json TEXT,
              output_snapshot_json TEXT,
              metadata_json TEXT,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL,
              flow_id TEXT,
              callback_token TEXT,
              callback_status TEXT,
              callback_received_at_ms INTEGER,
              callback_payload_json TEXT,
              artifact_status TEXT,
              artifact_snapshot_json TEXT,
              timeout_deadline_ms INTEGER,
              reconciled_at_ms INTEGER,
              completion_mode TEXT,
              runtime_session_key TEXT,
              runtime_session_id TEXT,
              runtime_session_file TEXT,
              derived_issues_json TEXT,
              UNIQUE (issue_id, attempt_no)
            )'''
        )
        conn.execute(
            '''INSERT INTO issue_attempts (
              id, issue_id, attempt_no, assigned_employee_id, runtime_binding_id,
              dispatch_kind, status, dispatch_ref, started_at_ms, last_heartbeat_at_ms,
              ended_at_ms, failure_code, failure_summary, result_summary,
              input_snapshot_json, output_snapshot_json, metadata_json,
              created_at_ms, updated_at_ms, flow_id, callback_token, callback_status,
              callback_received_at_ms, callback_payload_json, artifact_status,
              artifact_snapshot_json, timeout_deadline_ms, reconciled_at_ms,
              completion_mode, runtime_session_key, runtime_session_id,
              runtime_session_file, derived_issues_json
            )
            SELECT
              id, issue_id, attempt_no, assigned_employee_id, runtime_binding_id,
              dispatch_kind, status, dispatch_ref, started_at_ms, last_heartbeat_at_ms,
              ended_at_ms, failure_code, failure_summary, result_summary,
              input_snapshot_json, output_snapshot_json, metadata_json,
              created_at_ms, updated_at_ms, flow_id, callback_token, callback_status,
              callback_received_at_ms, callback_payload_json, artifact_status,
              artifact_snapshot_json, timeout_deadline_ms, reconciled_at_ms,
              completion_mode, runtime_session_key, runtime_session_id,
              runtime_session_file, derived_issues_json
            FROM issue_attempts__old_schema'''
        )

        conn.execute(
            '''CREATE TABLE issue_checkpoints (
              id TEXT PRIMARY KEY,
              issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
              attempt_id TEXT REFERENCES issue_attempts(id) ON DELETE CASCADE,
              checkpoint_no INTEGER NOT NULL,
              kind TEXT NOT NULL DEFAULT 'progress'
                CHECK (kind IN ('progress', 'blocker', 'handoff', 'review', 'human_request', 'system')),
              summary TEXT NOT NULL,
              details_md TEXT,
              next_action TEXT,
              percent_complete INTEGER CHECK (percent_complete BETWEEN 0 AND 100),
              created_by_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
              created_at_ms INTEGER NOT NULL,
              UNIQUE (attempt_id, checkpoint_no)
            )'''
        )
        conn.execute(
            '''INSERT INTO issue_checkpoints (
              id, issue_id, attempt_id, checkpoint_no, kind, summary, details_md,
              next_action, percent_complete, created_by_employee_id, created_at_ms
            )
            SELECT
              id, issue_id, attempt_id, checkpoint_no, kind, summary, details_md,
              next_action, percent_complete, created_by_employee_id, created_at_ms
            FROM issue_checkpoints__old_schema'''
        )

        conn.execute(
            '''CREATE TABLE issue_relations (
              id TEXT PRIMARY KEY,
              from_issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
              to_issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
              relation_type TEXT NOT NULL
                CHECK (relation_type IN ('blocked_by', 'duplicate_of', 'parent_of', 'related_to')),
              created_by_employee_id TEXT REFERENCES employee_instances(id) ON DELETE SET NULL,
              created_at_ms INTEGER NOT NULL,
              CHECK (from_issue_id <> to_issue_id),
              UNIQUE (from_issue_id, to_issue_id, relation_type)
            )'''
        )
        conn.execute(
            '''INSERT INTO issue_relations (
              id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms
            )
            SELECT
              id, from_issue_id, to_issue_id, relation_type, created_by_employee_id, created_at_ms
            FROM issue_relations__old_schema'''
        )

        conn.execute(
            '''CREATE VIEW v_agent_queue AS
            SELECT
              i.id,
              i.project_id,
              i.issue_no,
              i.title,
              i.priority,
              i.status,
              i.assigned_employee_id,
              i.active_attempt_no,
              i.updated_at_ms
            FROM issues i
            WHERE i.status IN ('ready', 'dispatching', 'running', 'blocked', 'review', 'waiting_recovery_completion', 'waiting_children')'''
        )
        conn.execute(
            '''CREATE VIEW v_human_queue AS
            SELECT
              i.id,
              i.project_id,
              i.issue_no,
              i.title,
              i.priority,
              i.status,
              i.blocker_summary,
              i.required_human_input,
              i.updated_at_ms
            FROM issues i
            WHERE i.status IN (
              'waiting_human_info',
              'waiting_human_action',
              'waiting_human_approval'
            )'''
        )

        conn.execute('DROP TABLE issue_attempts__old_schema')
        conn.execute('DROP TABLE issue_checkpoints__old_schema')
        conn.execute('DROP TABLE issue_relations__old_schema')

        conn.commit()
        conn.execute('PRAGMA foreign_keys = ON')
        print(DB_PATH)
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
