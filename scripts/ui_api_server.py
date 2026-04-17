#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, UTC
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path('/root/.openclaw/workspace-agent-team')
STATE_DIR = ROOT / 'state'
WORKER_REPORT = STATE_DIR / 'worker_report.json'
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402
from services.config import current_db_path, current_db_source, runtime_path_snapshot  # noqa: E402
from services.workflow_control import load_control, set_mode  # noqa: E402

EXPORT_BOARD = str(ROOT / 'scripts' / 'export_board_snapshot.py')
EXPORT_ISSUES = str(ROOT / 'scripts' / 'export_issue_details.py')


MUTATING_WHEN_PAUSED_BLOCKED = {
    '/api/retry',
    '/api/human/resolve',
    '/api/human/enqueue',
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def refresh_exports() -> None:
    subprocess.run(['python3', EXPORT_BOARD], check=True, capture_output=True, text=True)
    subprocess.run(['python3', EXPORT_ISSUES], check=True, capture_output=True, text=True)


def pick_employee_key(svc: AgentTeamService, *, project_key: str, role: str) -> str:
    if role == 'ceo':
        row = svc.db.conn.execute(
            '''SELECT ei.employee_key
               FROM employee_instances ei
               JOIN role_templates rt ON rt.id = ei.role_template_id
               WHERE rt.template_key = 'ceo'
               ORDER BY ei.employee_key ASC
               LIMIT 1'''
        ).fetchone()
        if row:
            return str(row[0])
        raise RuntimeError('missing CEO employee')
    row = svc.db.conn.execute(
        '''SELECT ei.employee_key
           FROM employee_instances ei
           JOIN role_templates rt ON rt.id = ei.role_template_id
           LEFT JOIN projects p ON p.id = ei.project_id
           WHERE rt.template_key = ? AND p.project_key = ?
           ORDER BY ei.employee_key ASC
           LIMIT 1''',
        (role, project_key),
    ).fetchone()
    if row:
        return str(row[0])
    raise RuntimeError(f'missing employee for role={role} project={project_key}')


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/workflow-control':
            self._json(200, {'ok': True, 'result': load_control()})
            return
        if parsed.path == '/api/scheduled-issues':
            svc = AgentTeamService()
            try:
                out = svc.list_scheduled_issues()
                self._json(200, {'ok': True, 'result': out})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            finally:
                svc.close()
            return
        if parsed.path == '/api/human-queue':
            svc = AgentTeamService()
            try:
                out = svc.get_human_queue()
                self._json(200, {'ok': True, 'result': out})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            finally:
                svc.close()
            return
        if parsed.path == '/api/board-snapshot':
            svc = AgentTeamService()
            try:
                generated_at = now_iso()
                out = svc.get_ui_snapshot(
                    generated_at=generated_at,
                    source=current_db_source(),
                )
                out['workflow_control'] = load_control()
                if WORKER_REPORT.exists():
                    try:
                        out['worker_report'] = json.loads(WORKER_REPORT.read_text(encoding='utf-8'))
                    except Exception:
                        out['worker_report'] = None
                else:
                    out['worker_report'] = None
                out['db'] = {
                    'path': str(current_db_path()),
                    'source': current_db_source(),
                    'config': runtime_path_snapshot(),
                }
                self._json(200, {'ok': True, 'result': out})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            finally:
                svc.close()
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith('/api/'):
            self._json(404, {'error': 'not found'})
            return
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length) if length else b'{}'
        try:
            payload = json.loads(body.decode('utf-8') or '{}')
        except Exception:
            self._json(400, {'error': 'invalid json'})
            return

        if parsed.path == '/api/workflow-control':
            try:
                out = set_mode(payload['mode'], updated_by='ui', note=payload.get('note', ''))
                self._json(200, {'ok': True, 'result': out})
            except Exception as e:
                self._json(500, {'ok': False, 'error': str(e)})
            return

        control = load_control()
        if control.get('mode') == 'paused' and parsed.path in MUTATING_WHEN_PAUSED_BLOCKED:
            self._json(423, {
                'ok': False,
                'error': 'workflow is paused',
                'workflow_control': control,
            })
            return

        svc = AgentTeamService()
        try:
            if parsed.path == '/api/create-project':
                out = svc.create_project(
                    name=payload.get('name', ''),
                    project_key=payload.get('project_key') or None,
                    description=payload.get('description', ''),
                    created_by_employee_key=payload.get('created_by_employee_key', 'shared.ceo'),
                    initialize_sessions=bool(payload.get('initialize_sessions', True)),
                )
            elif parsed.path == '/api/update-project':
                out = svc.update_project(
                    project_key=payload.get('project_key', ''),
                    name=payload.get('name'),
                    description=payload.get('description'),
                )
            elif parsed.path == '/api/delete-project':
                out = svc.delete_project(
                    project_key=payload.get('project_key', ''),
                    delete_openclaw_sessions=False,
                )
            elif parsed.path == '/api/create-issue':
                project_key = payload.get('project_key', 'agent-team-core')
                route_mode = payload.get('route_mode', 'pm')
                route_role = 'ceo' if route_mode == 'ceo' else payload.get('assign_role', 'pm')
                owner_employee_key = payload.get('owner_employee_key', 'shared.ceo')
                created = svc.create_issue(
                    project_key=project_key,
                    owner_employee_key=owner_employee_key,
                    title=payload['title'],
                    description_md=payload.get('description', ''),
                    acceptance_criteria_md=payload.get('acceptance', ''),
                    priority=payload.get('priority', 'p2'),
                    source_type=payload.get('source_type', 'user'),
                    metadata={
                        'source': 'ui_create_issue',
                        'entry_mode': route_mode,
                        'requested_assign_role': route_role,
                        'dispatch_instruction': payload.get('dispatch_instruction', ''),
                        'created_via': 'agent_team_ui',
                    },
                )
                assign_employee_key = pick_employee_key(svc, project_key=project_key, role=route_role)
                triaged = svc.triage_issue(issue_id=created['issue_id'], assign_employee_key=assign_employee_key)
                out = {
                    'issue_id': created['issue_id'],
                    'issue_no': created['issue_no'],
                    'project_key': project_key,
                    'assigned_employee_key': assign_employee_key,
                    'route_role': route_role,
                    'created': created,
                    'triaged': triaged,
                }
            elif parsed.path == '/api/scheduled-issues/create':
                out = svc.create_scheduled_issue(
                    project_key=payload.get('project_key', 'agent-team-core'),
                    owner_employee_key=payload.get('owner_employee_key'),
                    title=payload['title'],
                    description_md=payload.get('description', ''),
                    acceptance_criteria_md=payload.get('acceptance', ''),
                    priority=payload.get('priority', 'p2'),
                    route_role=payload.get('route_role', 'pm'),
                    source_type=payload.get('source_type', 'system'),
                    dispatch_instruction=payload.get('dispatch_instruction', ''),
                    schedule_kind=payload.get('schedule_kind', 'daily'),
                    schedule_config=payload.get('schedule_config') or {},
                    enabled=bool(payload.get('enabled', True)),
                )
            elif parsed.path == '/api/scheduled-issues/update':
                out = svc.update_scheduled_issue(
                    scheduled_issue_id=payload['scheduled_issue_id'],
                    patch=payload,
                )
            elif parsed.path == '/api/scheduled-issues/delete':
                out = svc.delete_scheduled_issue(scheduled_issue_id=payload['scheduled_issue_id'])
            elif parsed.path == '/api/scheduled-issues/run-now':
                out = svc.run_scheduled_issue_now(scheduled_issue_id=payload['scheduled_issue_id'])
            elif parsed.path == '/api/scheduled-issues/set-enabled':
                out = svc.set_scheduled_issue_enabled(
                    scheduled_issue_id=payload['scheduled_issue_id'],
                    enabled=bool(payload.get('enabled')),
                )
            elif parsed.path == '/api/close':
                out = svc.close_issue(issue_id=payload['issue_id'], resolution=payload.get('resolution', 'completed'))
            elif parsed.path == '/api/attempt-callback':
                out = svc.record_attempt_callback(
                    attempt_id=payload['attempt_id'],
                    callback_token=payload['callback_token'],
                    phase=payload['phase'],
                    payload=payload.get('payload') or {},
                    idempotency_key=payload.get('idempotency_key'),
                )
            elif parsed.path == '/api/cancel':
                out = svc.cancel_execution(dispatch_ref=payload['dispatch_ref'], reason=payload.get('reason', 'cancelled_from_ui'))
            elif parsed.path == '/api/retry':
                out = svc.retry_execution(
                    issue_id=payload['issue_id'],
                    runtime_binding_key=payload['runtime_binding_key'],
                    payload=payload['payload'],
                    reason=payload.get('reason', 'retry_from_ui'),
                )
            elif parsed.path == '/api/human/resolve':
                out = svc.resolve_human_action(
                    issue_id=payload['issue_id'],
                    resolution=payload['resolution'],
                    note=payload.get('note', ''),
                    next_employee_key=payload.get('next_employee_key'),
                    next_role=payload.get('next_role'),
                )
            elif parsed.path == '/api/human/enqueue':
                out = svc.enqueue_human(
                    issue_id=payload['issue_id'],
                    human_type=payload['human_type'],
                    prompt=payload['prompt'],
                    required_input=payload['required_input'],
                )
            else:
                self._json(404, {'error': 'unknown api route'})
                return
            refresh_exports()
            self._json(200, {'ok': True, 'result': out})
        except Exception as e:
            self._json(500, {'ok': False, 'error': str(e)})
        finally:
            svc.close()


def main():
    server = HTTPServer(('0.0.0.0', 8032), Handler)
    print('Agent Team UI API listening on :8032')
    server.serve_forever()


if __name__ == '__main__':
    main()
