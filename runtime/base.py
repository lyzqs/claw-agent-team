from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class AdapterError(RuntimeError):
    pass


class TimeoutObserved(AdapterError):
    pass


class RunAbortedObserved(AdapterError):
    def __init__(self, *, stop_reason: str | None = None, error_message: str | None = None, timestamp: int | None = None):
        self.stop_reason = stop_reason
        self.error_message = error_message
        self.timestamp = timestamp
        super().__init__(error_message or stop_reason or 'run aborted')


class RunErrorObserved(AdapterError):
    def __init__(self, *, error_message: str | None = None, timestamp: int | None = None):
        self.error_message = error_message
        self.timestamp = timestamp
        super().__init__(error_message or 'run errored')


@dataclass(frozen=True)
class RuntimeBindingContext:
    runtime_type: str
    binding_key: str
    session_key: str | None
    agent_id: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RuntimeCapabilities:
    dispatch: bool = True
    observe_exact_text: bool = True
    observe_json_marker: bool = True
    abort: bool = True
    callback_protocol: bool = False
    artifact_callback: bool = False


class RuntimeAdapter(Protocol):
    @property
    def runtime_type(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    def dispatch(self, *, prompt: str, dispatch_id: str | None = None, timeout_ms: int = 30 * 60 * 1000) -> dict[str, Any]: ...
    def wait_for_exact_text(self, *, expected_text: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict[str, Any]: ...
    def wait_for_json_marker(self, *, marker: str, timeout_seconds: int = 45, limit: int = 20, min_timestamp_ms: int | None = None) -> dict[str, Any]: ...
    def abort(self, dispatch_ref: str) -> dict[str, Any]: ...
