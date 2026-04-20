# Issue #6 实现与验证文档

**Issue**: #6 — 修复 Arena 换仓执行路径并完成最小可验证闭环
**项目**: Arena (agent-team-arena)
**执行角色**: Dev
**完成时间**: 2026-04-20
**状态**: Dev 完成

---

## 验收标准与实现对照

### 验收标准 1：修复 `execute_rotation()` 代码缺陷

**要求**：避免卖出后组装 plan 路径因变量使用顺序问题而抛错。

**缺陷确认**：`execute_rotation()` 函数（约 arena_executor.py 行 391-476）中，`sellTradeId` 组装时引用了 `response_data`，但该变量在 `plan = {...}` 组装后才定义，导致 `NameError`。

**修复**：将 `response_data = (response or {}).get("data") or {}` 前移到 `post_json` 之后、`now = rt.utc_now()` 之前（第 431 行），确保 `plan["sellTradeId"]` 和 `audit_payload["tradeId"]` 引用时 `response_data` 已定义。

**验证**：语法验证通过 (`python3 -m py_compile`)。

✅ 验收标准 1 已满足

### 验收标准 2：对换仓执行路径完成最小必要验证

**要求**：至少能证明核心流程不会在当前缺陷点中断。

**验证方法**：

| 验证项 | 方法 | 结果 |
|---|---|---|
| 语法完整性 | `py_compile` | ✅ 通过 |
| `execute_rotation` 执行路径（代码审查） | 从 `post_json` → `response_data` → `plan` → `autopilot_state` → `audit_payload` → `record_audit` 全链路无 NameError | ✅ 通过 |
| `execute_rotation` 与其他执行函数一致性 | 对比 `execute_playbook`、`execute_ticket`，确认变量声明顺序统一 | ✅ 一致 |
| `response_data` 声明位置 | 修复后 `response_data` 声明在行 431，`plan` 组装在行 433-444，符合先声明后使用原则 | ✅ 通过 |

### 验收标准 3：明确记录验证覆盖范围与残留风险

**验证覆盖范围**：
- `execute_rotation()` 函数内 `sellTradeId` 字段正确赋值
- `audit_payload` 中 `tradeId` 字段正确赋值
- `autopilot_state` 的 `pendingRotationPlans` 正确追加
- 状态写入 `AUTOPILOT_STATE_PATH`

**未覆盖范围**：
- 真实 API 调用（需市场开盘、可交易标的）
- 买入腿（buy leg）执行 —— 换仓卖出后买入 replacement 的完整两腿流程
- `live_validate_playbook()` 内部校验逻辑
- 并发/重复执行换仓的幂等性
- 卖出未成功但 plan 已写入 autopilot_state 的回滚逻辑

**残留风险**：
- 最小验证仅覆盖代码路径，无法保证真实交易环境下的行为
- 买入腿属于独立后续步骤，不在本轮 fix 范围内
- 幂等性未验证（同一 playbook 重复执行可能导致 duplicate orders）

### 验收标准 4：迭代目录文档

**实现**：本文档创建于 `docs/arena-investment-flow-iteration/issue-6-rotation-execution-fix.md`

---

## 技术变更详情

### 修改文件

`workspace-inStreet/arena/scripts/arena_executor.py`

**变更类型**：Bug fix（变量声明顺序）

**变更内容**：

```diff
     response = post_json(creds["base_url"], "/api/v1/arena/trade", creds["api_key"], order_payload)
+    response_data = (response or {}).get("data") or {}

     now = rt.utc_now()
     plan = {
         ...
         "sellTradeId": response_data.get("trade_id") or response.get("trade_id"),
         ...
     }
     ...
     autopilot_state["pendingRotationPlans"] = [...]
     rt.write_json(AUTOPILOT_STATE_PATH, autopilot_state)

-    response_data = (response or {}).get("data") or {}
     audit_payload = {
```

### 执行路径分析

`execute_rotation()` 完整路径（修复后）：

```
load_runtime → select_rotation_playbook → live_validate_playbook
  → [blockers? return blocked payload]
  → compute sell_shares
  → [sell_shares < 100? raise RuntimeError]
  → build order_payload
  → post_json(sell order) ─────────────────┐
  → response_data extraction ◄──────────────┘
  → now = utc_now
  → build plan (with sellTradeId from response_data) ← FIXED
  → update autopilot_state (pendingRotationPlans, etc.)
  → write_json(AUTOPILOT_STATE_PATH)
  → build audit_payload (with tradeId from response_data)
  → record_audit
  → log_event
  → return audit_payload
```

---

## 下一步

- **QA**：在模拟/测试环境中运行 `execute_rotation` 命令，验证完整流程
- **PM**：确认买入腿执行路径是否需要独立 issue
- **Ops**：确认 autopilot_state 回滚机制是否需要加固

---

*本文档由 Agent Team Dev 角色生成，记录 Issue #6 的技术实现与验证结果。*
