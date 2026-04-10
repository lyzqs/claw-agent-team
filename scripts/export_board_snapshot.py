#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
sys.path.insert(0, str(ROOT))
from services.agent_team_service import AgentTeamService  # noqa: E402

OUT = ROOT / 'ui' / 'board' / 'data.json'
OUT.parent.mkdir(parents=True, exist_ok=True)


def main():
    svc = AgentTeamService()
    try:
        snapshot = svc.get_board_snapshot()
    finally:
        svc.close()
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(OUT)


if __name__ == '__main__':
    main()
