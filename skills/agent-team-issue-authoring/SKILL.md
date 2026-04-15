---
name: agent-team-issue-authoring
description: Stable issue authoring skill for Agent Team. Use when an agent needs to decide whether to create a new issue, follow-up issue, blocker issue, governance issue, or system/watchdog issue in the Agent Team ledger, and when it must use the single official derived-issue creation path instead of burying work in chat text or writing directly through multiple paths.
---

# Agent Team Issue Authoring

Use this skill when an agent in the Agent Team system needs to create, propose, or escalate work as a new issue.

This skill is about **issue creation discipline**, not execution discipline.

## Core rule

Do **not** create a new issue just because a task failed once.

Prefer these in order:
1. same issue, new attempt
2. same issue, handoff to next role
3. same issue, send to Human Queue
4. **new issue only when the work is truly a separate unit**

A new issue should represent a distinct unit of work, ownership, or governance, not just more commentary.

---

## When to create a new issue

Create a new issue only if one of these is true:

### 1. Follow-up work is separable
Examples:
- current issue is done, but uncovered another real task
- a verification task reveals a separate remediation task
- implementation completes, but a new hardening / cleanup / rollout task is needed

### 2. A blocker needs separate ownership
Examples:
- PM needs a dedicated requirement clarification issue
- QA finds a real defect that should be tracked separately
- Ops identifies deployment infrastructure work separate from the feature itself

### 3. Governance / decision work is separate from delivery work
Examples:
- a strategic prioritization question
- budget / resource / scope conflict
- cross-project coordination
- escalation that should go to CEO as a separate governance unit

### 4. System / detector / watchdog generated work is separate
Examples:
- stale-attempt cleanup follow-up
- repeated routing anomaly
- repeated provider/runtime instability
- recurring operational warning that should be tracked as work

---

## When NOT to create a new issue

Do **not** create a new issue when:

- the current issue just needs another attempt
- the current issue simply needs handoff to another role
- the current issue needs user confirmation / missing information
- the current issue can continue after approval or clarification
- the current issue failed for transient reasons and should retry

In those cases use:
- retry
- handoff
- Human Queue
- reconciliation / stale handling

---

## Required action path from the agent

When an agent thinks a new issue is needed, it must use **one single official creation path**.

### Official rule
- Do **not** put `create_issue_proposal` or `create_issue_proposals` inside the final terminal JSON.
- Do **not** directly improvise free-form issue creation requests in chat text.
- Do **not** use multiple different creation paths.

Instead:
1. decide whether a separate issue is warranted
2. prepare one or more structured proposal objects
3. call the single official helper entry:

```bash
python3 /root/.openclaw/workspace-agent-team/scripts/attempt_callback_helper.py create-issues \
  --attempt-id <attempt_id> \
  --callback-token <callback_token> \
  --created-by-role <pm|dev|qa|ops|ceo> \
  --proposals-json '<JSON array>'
```

This helper is the **only** write path that agents should use for creating derived issues.

---

## Proposal format

Use a JSON array. Even if only one issue is needed, still pass an array with one object.

```json
[
  {
    "proposal_key": "qa-defect-login-timeout",
    "source_type": "agent",
    "project_key": "agent-team-core",
    "owner_employee_key": "shared.ceo",
    "route_role": "dev",
    "title": "Fix login timeout regression",
    "description_md": "Clear problem statement and context.",
    "acceptance_criteria_md": "What must be true to consider this done.",
    "priority": "p2",
    "issue_kind": "followup|blocker|governance|system",
    "relation_type": "parent_of|blocked_by|related_to",
    "reason": "Why this deserves a separate issue instead of a retry/handoff/human queue.",
    "metadata": {
      "created_by_agent": "agent-team-qa",
      "created_from_issue_id": "issue_xxx",
      "created_from_attempt_no": 3,
      "created_because": "qa_found_real_defect"
    }
  }
]
```

If a new issue is **not** needed, the agent should not call the helper.

---

## Field rules

### `proposal_key`
Required for deduplication.

It should be stable for the same intended derived issue inside the same source attempt.
Examples:
- `ceo-split-api-followup`
- `qa-defect-provider-timeout`
- `ops-rollout-followup-prod-alerting`

If the agent retries the same derived-issue creation, it should reuse the same `proposal_key`.

### `source_type`
Use these values consistently:

- `user` — created directly by the human user
- `agent` — created by PM/Dev/QA/Ops/CEO as a new work item
- `system` — created by system orchestration logic
- `detector` — created by detector/analyzer logic
- `watchdog` — created by watchdog/recovery logic
- `human` — created as a human-originated operational item inside the system

If current backend does not yet accept `agent`, degrade to `system` and preserve real origin in metadata:

```json
{
  "source_type": "system",
  "metadata": {
    "logical_source_type": "agent"
  }
}
```

### `owner_employee_key`
Default owner guidance:

- governance issues -> `shared.ceo`
- normal delivery issues -> project PM if known
- system/watchdog issues -> `shared.ceo` unless another owner is clearly better

### `route_role`
Default routing guidance:

- `pm` for most new business/delivery work
- `ceo` for governance, prioritization, budget, or conflict resolution
- rarely direct to `dev` / `qa` / `ops` unless that is obviously correct and bypassing PM is intentional

### `title`
Good titles are:
- short
- action-oriented
- one real problem per issue

Bad:
- “need to discuss something”
- “more work”
- “follow-up”

Good:
- “Clarify export retention requirements”
- “Fix provider-error path ledger writeback mismatch”
- “Decide rollout priority for agent-team-labs backlog”

### `description_md`
Should include:
1. what happened
2. why it matters
3. what this new issue is for
4. why current issue should not absorb it

### `acceptance_criteria_md`
Must be concrete enough that another role can close it later.

---

## Creation policy by role

### PM
PM may create:
- clarification issues
- decomposition/follow-up issues
- governance escalation issues

PM should usually route new issues to:
- PM (if more decomposition needed)
- CEO (if governance/priority conflict)
- Dev (only when the task is already cleanly specified)

### Dev
Dev may create:
- blocker issue
- follow-up implementation issue
- technical debt issue
- defect issue only if it is clearly separate from current issue

Dev should usually route to:
- QA for verification-related follow-up
- PM if scope/requirements changed
- CEO if risk or governance level is high

### QA
QA may create:
- defect issue
- regression issue
- release risk issue

QA should usually route to:
- Dev for concrete fixes
- CEO for release/governance risk
- PM if requirement ambiguity is the real blocker

### Ops
Ops may create:
- rollout issue
- infrastructure issue
- operational risk issue
- monitoring/hardening issue

### CEO
CEO may create:
- governance issue
- cross-project coordination issue
- prioritization / resourcing issue

---

## Agent-to-system behavior

Important: the agent should usually **propose** issue creation, and then call the single official helper.

Recommended control pattern:
1. agent decides a separate issue is needed
2. agent builds one or more structured proposals
3. agent calls `attempt_callback_helper.py create-issues`
4. system/API performs actual `create_issue + triage + relation`
5. activity log records origin and linkage

This keeps issue creation auditable and prevents noisy issue spam.

---

## Required linkage metadata

Whenever a new issue is created from an old one, include linkage metadata if available:

```json
{
  "created_by_agent": "agent-team-qa",
  "created_from_issue_id": "issue_abc123",
  "created_from_attempt_no": 4,
  "created_from_role": "qa",
  "created_because": "verification_found_real_defect"
}
```

This is critical for later observability and UI graphing.

---

## Decision checklist before proposing a new issue

Ask these questions in order:

1. Can this be solved by retrying the current issue?
2. Can this be solved by handing off the current issue?
3. Can this be solved by sending the current issue to Human Queue?
4. Is this genuinely separate work with different acceptance / owner / route?

Only if 4 is yes should a new issue be proposed.

---

## Anti-patterns

Avoid these:

- Creating a new issue after every failed attempt
- Creating vague “follow-up” issues without concrete acceptance
- Creating governance issues for normal delivery work
- Creating delivery issues when Human Queue is the correct answer
- Omitting linkage metadata
- Sending everything directly to CEO by default
- Mixing final terminal JSON with derived-issue creation side effects
- Using more than one official issue-creation write path

---

## Recommended next implementation step

Keep a single official write path for derived issues. Skills should guide the agent’s judgment and proposal structure, while the helper/API performs the actual creation in a centralized, auditable, idempotent way.
