#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from services.config import ROOT, STATE_DIR

CONTROL_PATH = STATE_DIR / 'workflow_control.json'

DEFAULT = {
    'mode': 'running',
    'updated_at': None,
    'updated_by': 'system',
    'note': '',
}


def load_control() -> dict:
    if not CONTROL_PATH.exists():
        save_control(DEFAULT)
    return json.loads(CONTROL_PATH.read_text(encoding='utf-8'))


def save_control(payload: dict) -> None:
    CONTROL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def set_mode(mode: str, updated_by: str = 'system', note: str = '') -> dict:
    if mode not in {'running', 'paused'}:
        raise ValueError(f'unsupported mode: {mode}')
    import datetime as _dt
    data = load_control()
    data['mode'] = mode
    data['updated_at'] = _dt.datetime.utcnow().isoformat() + 'Z'
    data['updated_by'] = updated_by
    data['note'] = note
    save_control(data)
    return data


if __name__ == '__main__':
    print(json.dumps(load_control(), ensure_ascii=False, indent=2))
