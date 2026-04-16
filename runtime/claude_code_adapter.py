from __future__ import annotations

from .base import AdapterError, RuntimeCapabilities


class ClaudeCodeRuntimeAdapter:
    runtime_type = 'claude_code'
    provider = 'claude_code'
    capabilities = RuntimeCapabilities(
        dispatch=False,
        observe_exact_text=False,
        observe_json_marker=False,
        abort=False,
        callback_protocol=False,
        artifact_callback=False,
    )

    def __init__(self, context):
        self.context = context

    def _unimplemented(self):
        raise AdapterError('Claude Code runtime adapter is not implemented yet')

    def dispatch(self, *, prompt: str, dispatch_id: str | None = None, timeout_ms: int = 30 * 60 * 1000) -> dict:
        self._unimplemented()

    def wait_for_exact_text(self, *, expected_text: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict:
        self._unimplemented()

    def wait_for_json_marker(self, *, marker: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict:
        self._unimplemented()

    def abort(self, dispatch_ref: str) -> dict:
        self._unimplemented()
