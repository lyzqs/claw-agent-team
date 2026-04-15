#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
CLI = ROOT / 'scripts' / 'agent_team_api_cli.py'


ROLE_CHOICES = ['pm', 'dev', 'qa', 'ops', 'ceo', 'close']
STATUS_CHOICES = ['done', 'blocked', 'needs_human']


def run_cli(args: list[str]) -> dict:
    res = subprocess.run(['python3', str(CLI), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(res.stderr.strip() or res.stdout.strip() or f'callback helper failed: {args}')
    try:
        return json.loads(res.stdout)
    except Exception as e:
        raise SystemExit(f'invalid JSON from agent_team_api_cli: {e}\n{res.stdout}') from e


def parse_json_object(raw: str | None, *, default: dict | None = None) -> dict:
    if raw is None or raw == '':
        return default or {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit('json payload must be an object')
    return value


def parse_json_list(raw: str | None) -> list:
    if raw is None or raw == '':
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise SystemExit('json payload must be a list')
    return value


def add_common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument('--attempt-id', required=True)
    subparser.add_argument('--callback-token', required=True)
    subparser.add_argument('--idempotency-key', default=None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Agent Team callback helper for dispatched runs')
    sub = p.add_subparsers(dest='mode', required=True)

    raw = sub.add_parser('raw', help='send a raw callback payload')
    add_common(raw)
    raw.add_argument('--phase', choices=['artifact_created', 'terminal_handoff'], required=True)
    raw.add_argument('--payload-json', required=True, help='full JSON object payload')

    artifact_doc = sub.add_parser('artifact-doc', help='record a Feishu doc artifact callback')
    add_common(artifact_doc)
    artifact_doc.add_argument('--doc-url', required=True)
    artifact_doc.add_argument('--doc-token', default='')
    artifact_doc.add_argument('--summary', default='已创建飞书文档')

    terminal = sub.add_parser('terminal', help='record a terminal handoff callback')
    add_common(terminal)
    terminal.add_argument('--marker', default='')
    terminal.add_argument('--status', choices=STATUS_CHOICES, required=True)
    terminal.add_argument('--summary', required=True)
    terminal.add_argument('--next', dest='suggested_next_role', choices=ROLE_CHOICES, required=True)
    terminal.add_argument('--reason', required=True)
    terminal.add_argument('--risk-level', default='normal')
    terminal.add_argument('--needs-human', action='store_true')
    terminal.add_argument('--artifacts-json', default='')
    terminal.add_argument('--blocking-findings-json', default='')

    create_issues = sub.add_parser('create-issues', help='create one or more derived issues from a dispatched attempt')
    add_common(create_issues)
    create_issues.add_argument('--proposals-json', required=True, help='JSON array of issue proposal objects')
    create_issues.add_argument('--created-by-role', default='')

    return p


def build_payload(args: argparse.Namespace) -> tuple[str, dict]:
    if args.mode == 'raw':
        return args.phase, parse_json_object(args.payload_json)

    if args.mode == 'artifact-doc':
        return 'artifact_created', {
            'artifact_type': 'feishu_doc',
            'doc_url': args.doc_url,
            'doc_token': args.doc_token or None,
            'summary': args.summary,
        }

    if args.mode == 'terminal':
        return 'terminal_handoff', {
            'marker': args.marker,
            'status': args.status,
            'summary': args.summary,
            'artifacts': parse_json_list(args.artifacts_json),
            'blocking_findings': parse_json_list(args.blocking_findings_json),
            'suggested_next_role': args.suggested_next_role,
            'reason': args.reason,
            'risk_level': args.risk_level,
            'needs_human': bool(args.needs_human),
        }

    raise SystemExit(f'unsupported mode: {args.mode}')


def main() -> int:
    args = build_parser().parse_args()
    if args.mode == 'create-issues':
        proposals = parse_json_list(args.proposals_json)
        out = run_cli([
            'create-derived-issues',
            '--attempt-id', args.attempt_id,
            '--proposals-json', json.dumps(proposals, ensure_ascii=False),
            *(['--created-by-role', args.created_by_role] if args.created_by_role else []),
        ])
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    phase, payload = build_payload(args)
    out = run_cli([
        'record-attempt-callback',
        '--attempt-id', args.attempt_id,
        '--callback-token', args.callback_token,
        '--phase', phase,
        '--payload-json', json.dumps(payload, ensure_ascii=False),
        *(['--idempotency-key', args.idempotency_key] if args.idempotency_key else []),
    ])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
