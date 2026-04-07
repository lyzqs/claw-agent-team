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
