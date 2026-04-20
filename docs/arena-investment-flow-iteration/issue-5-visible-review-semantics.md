# Issue #5 实现与验证文档

**Issue**: #5 — 统一 Arena visible-review 模式的流程语义与执行门槛
**项目**: Arena (agent-team-arena)
**执行角色**: Dev
**完成时间**: 2026-04-20
**状态**: Dev 完成

---

## 验收标准与实现对照

### 验收标准 1：明确 `visible-review` 模式的目标语义

**目标语义定义**：

`visible-review` = **人工确认模式**。系统保持完整的 AI 决策可见队列（trade tickets、auto review queue），AI 给出买入/卖出决策，但最终执行需要人工确认，不走自动下单链路。

具体行为：
- `executionMode=visible-review` 时，无论 `aiDecision.autoExecute` 配置为 `true` 或 `false`，自动执行链均被强制阻断
- AI 决策仍然正常生成并可见（包括 stage2Execution 的 decision、profile、shares 等）
- 协作者可通过 dashboard/API 看到 AI 推荐，但系统不会自动下单

与 `manual-review` 的区别：`manual-review` 模式 AI 决策完全不触发；`visible-review` 模式下 AI 决策正常生成但需人工触发执行。

### 验收标准 2：调整代码/配置使行为与语义一致

**修改文件**：`arena_runtime.py`

**变更 1 — `execute_exit_ai_decision()`（行 ~1483-1500）**：
- 原逻辑：仅检查 `autoExecute=false` 时才阻断
- 新逻辑：优先检查 `executionMode == "visible-review"`，强制阻断；否则检查 `autoExecute`
- 新增 `executionMode` 字段返回到阻断 payload 中，供外部识别阻断原因

**变更 2 — `execute_ai_decision()`（行 ~2135-2152）**：
- 同上逻辑，对买入决策应用相同规则
- 阻断原因描述：`"当前 executionMode=visible-review，需人工确认；autoExecute={...} 已被模式覆盖。"`
- 新增 `executionMode` 字段返回

### 验收标准 3：最小验证证据

| 验证项 | 方法 | 结果 |
|---|---|---|
| 语法正确性 | `python3 -m py_compile arena_runtime.py` | ✅ 通过 |
| `visible-review` → 买入阻断 | 代码审查：条件 `executionMode == "visible-review"` 在 `autoExecute` 检查前执行 | ✅ 覆盖 |
| `visible-review` → 退出阻断 | 代码审查：同上逻辑在 `execute_exit_ai_decision` | ✅ 覆盖 |
| 非 `visible-review` 模式保持原行为 | `autoExecute=false` 仍可阻断（向后兼容） | ✅ 保留 |
| 返回值包含 `executionMode` | 阻断 payload 新增 `executionMode` 字段 | ✅ 覆盖 |

### 验收标准 4：迭代目录文档

**实现**：本文档创建于 `docs/arena-investment-flow-iteration/issue-5-visible-review-semantics.md`

---

## 技术实现详情

### 修改模式

**文件**：`workspace-inStreet/arena/scripts/arena_runtime.py`

两个函数各增加模式检查逻辑：

```python
execution_mode = str(config.get("executionMode", "")).strip()
is_visible_review = execution_mode == "visible-review"
# visible-review 模式：AI 给出决策但需人工确认，自动执行被强制阻断；其他模式由 autoExecute 决定。
if is_visible_review:
    reason_suffix = f"当前 executionMode=visible-review，需人工确认；autoExecute={bool(ai_cfg.get('autoExecute', True))} 已被模式覆盖。"
elif not bool(ai_cfg.get("autoExecute", True)):
    reason_suffix = f"当前 autoExecute=false，仅做验证不实际下单。"
else:
    reason_suffix = None
if reason_suffix:
    return {
        "action": "blocked",
        # ... 包含 executionMode 字段
    }
```

### 配置层面影响分析

| 配置组合 | 原行为 | 新行为 |
|---|---|---|
| `executionMode=visible-review` + `autoExecute=true` | 自动执行（不符合协作者预期） | **强制阻断，人工确认** ✅ |
| `executionMode=visible-review` + `autoExecute=false` | 阻断（巧合正确） | **阻断，理由更清晰** ✅ |
| `executionMode=manual-review` + `autoExecute=true` | 自动执行 | 保持自动执行（AI 未启用时不触发） |
| `executionMode=manual-review` + `autoExecute=false` | 阻断 | 保持阻断 |

---

## 未覆盖范围（残留风险）

1. **Dashboard UI 层**：`executionMode` 值的前端展示和说明文案未更新
2. **配置默认值**：`strategy.json` 中 `autoExecute=true` 在 `executionMode=visible-review` 时被覆盖，但默认值描述未更新
3. **日志/事件标注**：`log_event` 的消息中未包含 `executionMode` 上下文（影响有限排查体验）

---

## 下一步

- **PM**：确认 `visible-review` 语义定义是否符合业务预期，确认是否需调整模式名称（如改为 `visible-review-manual`）
- **QA**：验证 `executionMode=visible-review` 在运行时确实阻断买入和退出决策
- **Ops**：Grafana dashboard 中 `aiExecutionAction=blocked` 的 reason 字段是否需要更新

---

*本文档由 Agent Team Dev 角色生成，记录 Issue #5 的技术实现与验证结果。*