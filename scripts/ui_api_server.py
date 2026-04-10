#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402

EXPORT_BOARD = str(ROOT / 'scripts' / 'export_board_snapshot.py')
EXPORT_ISSUES = str(ROOT / 'scripts' / 'export_issue_details.py')


def refresh_exports() -> None:
    subprocess.run(['python3', EXPORT_BOARD], check=True, capture_output=True, text=True)
    subprocess.run(['python3', EXPORT_ISSUES], check=True, capture_output=True, text=True)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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

        svc = AgentTeamService()
        try:
            if parsed.path == '/api/close':
                out = svc.close_issue(issue_id=payload['issue_id'], resolution=payload.get('resolution', 'completed'))
            elif parsed.path == '/api/cancel':
                out = svc.cancel_execution(dispatch_ref=payload['dispatch_ref'], reason=payload.get('reason', 'cancelled_from_ui'))
            elif parsed.path == '/api/retry':
                out = svc.retry_execution(
                    issue_id=payload['issue_id'],
                    runtime_binding_key=payload['runtime_binding_key'],
                    payload=payload['payload'],
                    reason=payload.get('reason', 'retry_from_ui'),
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
