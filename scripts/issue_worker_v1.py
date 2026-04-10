#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402
from services.workflow_control import load_control  # noqa: E402

OUT = ROOT / 'state' / 'worker_report.json'
OUT.parent.mkdir(parents=True, exist_ok=True)


def main():
    control = load_control()
    svc = AgentTeamService()
    try:
        board = svc.get_board_snapshot()
        report = {
            'workflow_control': control,
            'agent_queue_count': len(board['agent_queue']),
            'human_queue_count': len(board['human_queue']),
            'agent_workload_count': len(board['agent_workload']),
            'ready_issue_ids': [i['id'] for i in board['agent_queue'] if i['status'] == 'ready'],
            'review_issue_ids': [i['id'] for i in board['agent_queue'] if i['status'] == 'review'],
            'human_issue_ids': [i['id'] for i in board['human_queue']],
            'agent_workload': board['agent_workload'],
            'notes': [
                'v1 worker is read-only and reports unfinished work.',
                'workflow_control.mode=paused should block future auto-progress logic.',
            ],
        }
    finally:
        svc.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(OUT)


if __name__ == '__main__':
    main()
