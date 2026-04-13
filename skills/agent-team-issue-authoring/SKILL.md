---
name: agent-team-issue-authoring
description: Stable issue authoring skill for Agent Team. Use when an agent needs to decide whether to create a new issue, follow-up issue, blocker issue, governance issue, or system/watchdog issue in the Agent Team ledger, and when it must produce a clean structured issue-creation proposal instead of burying work in chat text.
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

## Required output format from the agent

When an agent thinks a new issue is needed, it should **not directly improvise free-form text**.
Instead, it should output a single structured proposal object.

Use this JSON shape:

```json
{
  "create_issue": true,
  "source_type": "agent",
  "project_key": "agent-team-core",
  "owner_employee_key": "shared.ceo",
  "route_role": "pm",
  "title": "Short issue title",
  "description_md": "Clear problem statement and context.",
  "acceptance_criteria_md": "What must be true to consider this done.",
  "priority": "p2",
  "issue_kind": "followup|blocker|governance|system",
  "reason": "Why this deserves a separate issue instead of a retry/handoff/human queue.",
  "dispatch_instruction": "Optional first-role instruction.",
  "metadata": {
    "created_by_agent": "agent-team-dev",
    "created_from_issue_id": "issue_xxx",
    "created_from_attempt_no": 3,
    "created_because": "qa_found_real_defect"
  }
}
```

If a new issue is **not** needed, the agent should not emit this object.

---

## Field rules

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

Important: the agent should usually **propose** issue creation, not directly create it by hidden side effects.

Recommended control pattern:
1. agent emits structured `create_issue` proposal
2. orchestrator validates it
3. system/API performs actual `create_issue`
4. activity log records origin and linkage

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

## Good examples

### Example: QA finds a separate defect

```json
{
  "create_issue": true,
  "source_type": "agent",
  "project_key": "agent-team-core",
  "owner_employee_key": "shared.ceo",
  "route_role": "dev",
  "title": "Fix mismatch in provider-error ledger writeback",
  "description_md": "QA verified the canonical provider-error routing path, but found the resulting ledger writeback does not match the expected failure summary. This is separate from the original routing verification issue and should be tracked as its own defect.",
  "acceptance_criteria_md": "A provider-error attempt writes the expected failure code and failure summary into the ledger, and QA can verify the corrected path.",
  "priority": "p1",
  "issue_kind": "blocker",
  "reason": "This is a separate defect and should not be hidden inside the verification issue.",
  "dispatch_instruction": "Reproduce the mismatch and correct the ledger writeback path.",
  "metadata": {
    "created_by_agent": "agent-team-qa",
    "created_from_issue_id": "issue_xyz",
    "created_from_attempt_no": 4,
    "created_because": "qa_found_real_defect"
  }
}
```

### Example: CEO governance escalation

```json
{
  "create_issue": true,
  "source_type": "agent",
  "project_key": "agent-team-core",
  "owner_employee_key": "shared.ceo",
  "route_role": "ceo",
  "title": "Decide whether PM may bypass CEO for low-risk user clarifications",
  "description_md": "During Agent Team productization, a governance ambiguity was found: it is unclear when PM/QA/Ops may directly ask the user for clarification versus escalating through CEO. This is a governance question, not a delivery issue.",
  "acceptance_criteria_md": "A clear policy exists for governance vs direct clarification routing, and it is reflected in UI and worker behavior.",
  "priority": "p2",
  "issue_kind": "governance",
  "reason": "This requires a policy decision, not just another execution attempt.",
  "dispatch_instruction": "Make a policy decision and record the allowed routing rule.",
  "metadata": {
    "created_by_agent": "agent-team-pm",
    "created_from_issue_id": "issue_abc",
    "created_because": "governance_ambiguity"
  }
}
```

---

## Anti-patterns

Avoid these:

- Creating a new issue after every failed attempt
- Creating vague “follow-up” issues without concrete acceptance
- Creating governance issues for normal delivery work
- Creating delivery issues when Human Queue is the correct answer
- Omitting linkage metadata
- Sending everything directly to CEO by default

---

## Recommended next implementation step

If the runtime supports it, add an orchestrator-side handler for agent issue proposals so agents emit structured proposals and the system performs the actual create/triage path.
