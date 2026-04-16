from __future__ import annotations

import json

from .base import RuntimeBindingContext
from .claude_code_adapter import ClaudeCodeRuntimeAdapter
from .codex_adapter import CodexRuntimeAdapter
from .hermes_adapter import HermesRuntimeAdapter
from .openclaw_adapter import OpenClawRuntimeAdapter


def build_runtime_context(binding_row) -> RuntimeBindingContext:
    metadata = {}
    raw_meta = binding_row['metadata_json'] if 'metadata_json' in binding_row.keys() else None
    if isinstance(raw_meta, str) and raw_meta.strip():
        try:
            value = json.loads(raw_meta)
            if isinstance(value, dict):
                metadata = value
        except Exception:
            metadata = {}
    return RuntimeBindingContext(
        runtime_type=str(binding_row['runtime_type']),
        binding_key=str(binding_row['binding_key']),
        session_key=str(binding_row['session_key']) if binding_row['session_key'] else None,
        agent_id=str(binding_row['agent_id']) if binding_row['agent_id'] else None,
        metadata=metadata,
    )


def get_runtime_adapter(ctx: RuntimeBindingContext):
    if ctx.runtime_type in {'openclaw_session', 'openclaw_subagent'}:
        if not ctx.session_key:
            raise RuntimeError(f'missing session_key for runtime binding {ctx.binding_key}')
        return OpenClawRuntimeAdapter(ctx.session_key)
    if ctx.runtime_type == 'hermes':
        return HermesRuntimeAdapter(ctx)
    if ctx.runtime_type == 'codex':
        return CodexRuntimeAdapter(ctx)
    if ctx.runtime_type == 'claude_code':
        return ClaudeCodeRuntimeAdapter(ctx)
    raise RuntimeError(f'unsupported runtime_type: {ctx.runtime_type}')
