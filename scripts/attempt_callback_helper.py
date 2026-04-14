#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
CLI = ROOT / 'scripts' / 'agent_team_api_cli.py'


def run_cli(args: list[str]) -> dict:
    res = subprocess.run(['python3', str(CLI), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or res.stdout.strip() or f'callback helper failed: {args}')
    try:
        return json.loads(res.stdout)
    except Exception as e:
        raise SystemExit(f'invalid JSON from agent_team_api_cli: {e}\n{res.stdout}') from e


def parse_json_arg(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit('payload json must be an object')
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Agent Team callback helper for agent-dispatched runs')
    p.add_argument('--attempt-id', required=True)
    p.add_argument('--callback-token', required=True)
    p.add_argument('--phase', choices=['artifact_created', 'terminal_handoff'], required=True)
    p.add_argument('--payload-json', required=True, help='full JSON object payload')
    p.add_argument('--idempotency-key', default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    payload = parse_json_arg(args.payload_json)
    out = run_cli([
        'record-attempt-callback',
        '--attempt-id', args.attempt_id,
        '--callback-token', args.callback_token,
        '--phase', args.phase,
        '--payload-json', json.dumps(payload, ensure_ascii=False),
        *(['--idempotency-key', args.idempotency_key] if args.idempotency_key else []),
    ])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
