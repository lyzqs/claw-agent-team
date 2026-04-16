from __future__ import annotations

import json
import sys
from pathlib import Path

from .base import RunAbortedObserved, RunErrorObserved, TimeoutObserved

PROTO_ROOT = Path('/root/.openclaw/workspace/agent-team-prototype')
if str(PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTO_ROOT))

from execution_adapter import (  # type: ignore  # noqa: E402
    OpenClawExecutionAdapter,
    RunAbortedObserved as ProtoRunAbortedObserved,
    RunErrorObserved as ProtoRunErrorObserved,
    TimeoutObserved as ProtoTimeoutObserved,
)


def resolve_session_snapshot(session_key: str) -> dict[str, Any]:
    parts = session_key.split(':')
    if len(parts) < 2 or parts[0] != 'agent':
        return {}
    agent_id = parts[1]
    sessions_path = Path('/root/.openclaw/agents') / agent_id / 'sessions' / 'sessions.json'
    if not sessions_path.exists():
        return {}
    try:
        data = json.loads(sessions_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    entry = data.get(session_key)
    return entry if isinstance(entry, dict) else {}


class OpenClawRuntimeAdapter:
    def __init__(self, session_key: str):
        self.session_key = session_key
        self._adapter = OpenClawExecutionAdapter(session_key)

    def dispatch(self, *, prompt: str, dispatch_id: str | None = None, timeout_ms: int = 30 * 60 * 1000) -> dict[str, Any]:
        return self._adapter.dispatch(prompt=prompt, dispatch_id=dispatch_id, timeout_ms=timeout_ms)

    def wait_for_exact_text(self, *, expected_text: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict[str, Any]:
        try:
            return self._adapter.wait_for_exact_text(
                expected_text=expected_text,
                timeout_seconds=timeout_seconds,
                limit=limit,
                min_timestamp_ms=min_timestamp_ms,
            )
        except ProtoRunAbortedObserved as e:
            raise RunAbortedObserved(stop_reason=e.stop_reason, error_message=e.error_message, timestamp=e.timestamp) from e
        except ProtoRunErrorObserved as e:
            raise RunErrorObserved(error_message=e.error_message, timestamp=e.timestamp) from e
        except ProtoTimeoutObserved as e:
            raise TimeoutObserved(str(e)) from e

    def wait_for_json_marker(self, *, marker: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict[str, Any]:
        try:
            return self._adapter.wait_for_json_marker(
                marker=marker,
                timeout_seconds=timeout_seconds,
                limit=limit,
                min_timestamp_ms=min_timestamp_ms,
            )
        except ProtoRunAbortedObserved as e:
            raise RunAbortedObserved(stop_reason=e.stop_reason, error_message=e.error_message, timestamp=e.timestamp) from e
        except ProtoRunErrorObserved as e:
            raise RunErrorObserved(error_message=e.error_message, timestamp=e.timestamp) from e
        except ProtoTimeoutObserved as e:
            raise TimeoutObserved(str(e)) from e

    def abort(self, dispatch_ref: str) -> dict[str, Any]:
        return self._adapter.abort(dispatch_ref)
