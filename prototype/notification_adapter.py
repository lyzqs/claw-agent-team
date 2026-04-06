#!/usr/bin/env python3
"""Minimal notification adapter for the Agent Team prototype.

This adapter intentionally separates delivery from execution truth:
- it shells out to `openclaw message send`
- it records only delivery-side metadata
- it supports dry-run verification to avoid accidental outward sends during prototype work
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path('/root/.openclaw/workspace-agent-team')
ARTIFACT_DIR = ROOT / 'evidence' / 'phase5'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


class NotificationAdapterError(RuntimeError):
    pass


@dataclass
class NotificationRequest:
    source_type: str
    source_id: str
    event_type: str
    channel: str
    target: str
    body_md: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    notification_id: str = field(default_factory=lambda: f'notif_{uuid.uuid4().hex[:12]}')
    idempotency_key: str | None = None
    silent: bool = False


@dataclass
class NotificationResult:
    notification_id: str
    accepted: bool
    channel: str
    target: str
    delivery_status: str
    delivery_ref: str | None
    delivered_at_ms: int | None
    error_code: str | None
    error_summary: str | None
    dry_run: bool
    raw_result: dict[str, Any]


def now_ms() -> int:
    return int(time.time() * 1000)


class OpenClawNotificationAdapter:
    def send(self, request: NotificationRequest, *, dry_run: bool = False) -> NotificationResult:
        cmd = [
            'openclaw', 'message', 'send',
            '--channel', request.channel,
            '--target', request.target,
            '--message', request.body_md,
            '--json',
        ]
        if dry_run:
            cmd.append('--dry-run')
        if request.silent:
            cmd.append('--silent')

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise NotificationAdapterError(res.stderr.strip() or res.stdout.strip() or 'message send failed')

        raw = json.loads(res.stdout)
        payload = raw.get('payload') or {}
        delivery_ref = payload.get('messageId') or payload.get('requestId') or payload.get('to')
        delivered = None if dry_run else now_ms()
        status = 'sent' if raw.get('action') == 'send' else 'failed'

        return NotificationResult(
            notification_id=request.notification_id,
            accepted=True,
            channel=request.channel,
            target=request.target,
            delivery_status=status,
            delivery_ref=delivery_ref,
            delivered_at_ms=delivered,
            error_code=None,
            error_summary=None,
            dry_run=dry_run,
            raw_result=raw,
        )


def render_issue_event(issue_no: int, title: str, status: str, summary: str) -> str:
    return (
        f'[Agent Team] Issue #{issue_no} {status}\n'
        f'Title: {title}\n'
        f'Summary: {summary}'
    )


def write_result(path: Path, result: NotificationResult, request: NotificationRequest) -> None:
    payload = {
        'request': asdict(request),
        'result': asdict(result),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    adapter = OpenClawNotificationAdapter()
    req = NotificationRequest(
        source_type='issue',
        source_id='demo-issue-5.7',
        event_type='checkpoint_recorded',
        channel='telegram',
        target='-5114007576',
        body_md=render_issue_event(
            issue_no=7,
            title='Prototype notification adapter wiring',
            status='checkpoint_recorded',
            summary='Dry-run delivery path verified through openclaw message send.',
        ),
        metadata={'phase': '5.7', 'mode': 'demo'},
        idempotency_key='phase-5.7-demo',
    )
    result = adapter.send(req, dry_run=True)
    out = ARTIFACT_DIR / 'notification_adapter_demo_result.json'
    write_result(out, result, req)
    print(out)
    print(json.dumps({'request': asdict(req), 'result': asdict(result)}, ensure_ascii=False, indent=2))
