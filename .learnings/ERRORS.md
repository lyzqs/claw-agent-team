## [ERR-20260407-001] phase10-checkpoint-kind-constraint

**Logged**: 2026-04-07T05:25:00Z
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Phase 10 orchestration demo failed because `issue_checkpoints.kind` does not allow `done`.

### Error
```
sqlite3.IntegrityError: CHECK constraint failed: kind IN ('progress', 'blocker', 'handoff', 'review', 'human_request', 'system')
```

### Context
- Operation: `python3 prototype/run_advanced_orchestration_demo.py`
- Cause: the demo used `done` as a checkpoint kind, but the schema only allows constrained enum values.

### Suggested Fix
Read schema enums before inventing checkpoint kinds; map terminal summary events to `system` or another allowed enum.

### Metadata
- Reproducible: yes
- Related Files: prototype/run_advanced_orchestration_demo.py, /root/.openclaw/workspace/agent-team-prototype/schema.sql

---

## [ERR-20260412-002] issue-worker-observe-gateway-timeout

**Logged**: 2026-04-12T09:23:00Z
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
First live run of `issue_worker_v2.py` dispatched a ready issue successfully, but observe failed because `gateway call sessions.get` timed out.

### Error
```
Gateway call failed: Error: gateway timeout after 20000ms
Gateway target: ws://127.0.0.1:18789
Source: local loopback
Config: /root/.openclaw/openclaw.json
Bind: lan
```

### Context
- Operation: `python3 /root/.openclaw/workspace-agent-team/scripts/issue_worker_v2.py`
- Result: issue #4 was auto-dispatched to `agent-team-core.dev.primary`, dispatch_ref=`dispatch_a9dc31ce2996`
- Failure point: `services.agent_team_service.observe_execution()` -> `execution_adapter.wait_for_exact_text()` -> `gateway_call('sessions.get', ...)`

### Suggested Fix
Worker observe loop should tolerate transient gateway/session polling timeouts, separate dispatch and observe result accounting, and avoid treating one polling timeout as a full worker failure.

### Metadata
- Reproducible: yes
- Related Files: scripts/issue_worker_v2.py, services/agent_team_service.py, /root/.openclaw/workspace/agent-team-prototype/execution_adapter.py

---
