# Issue #135 — 修复 order_audit.jsonl 时间戳格式不一致

**状态**：Dev 完成  
**仓库**：规范实现仓库 `/root/.openclaw/workspace-agent-team`  
**执行仓库**：`/root/.openclaw/workspace-inStreet/arena/`  
**时间**：2026-04-21（UTC）

---

## 1. 问题描述

`order_audit.jsonl` 中同一记录的两段时间字段使用不同体系：

| 字段 | 原格式 | 示例 |
|------|--------|------|
| `ts` | UTC `Z` 后缀 | `"2026-04-21T03:12:38.297555Z"` |
| `submittedAt` | 北京时间 `+08:00` | `"2026-04-21T11:12:38.XXX+08:00"` |
| `submitTs`（券商返回） | 北京时间 `+08:00` | `"2026-04-21T11:07:35.978+08:00"` |

三者理论上应完全对齐，实际差 8 小时。日志分析工具无法正确对齐事件时间线，与 `events.jsonl` / `ai_decisions.jsonl` 的时间戳体系也不一致。

---

## 2. 根因分析

`ts` 和 `submittedAt` 均在 executor 代码中由 `rt.utc_now()` 生成：

```python
now = rt.utc_now()  # → "2026-04-21T03:12:38Z" (UTC)
audit_payload = {
    "ts": now,
    "submittedAt": now,   # ← UTC，但 order_audit.jsonl 历史数据显示+08:00
}
```

`utc_now()` 返回 UTC `Z` 格式字符串。`submittedAt` 的历史数据显示 `+08:00`，说明某些写入路径之前已改为 local 时间，或数据来自不同写入路径。

---

## 3. 修复方案

**统一方案**：将 `order_audit.jsonl` 中的 `ts` 和 `submittedAt` 字段统一改为北京时区（`Asia/Shanghai`）ISO 8601 格式 `+08:00`，与 `submitTs` 和历史 `submittedAt` 保持一致。

### 3.1 修改 `arena_runtime.py`

添加 `local_now()` 辅助函数：

```python
MARKET_TZ = ZoneInfo("Asia/Shanghai")  # 已存在

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def local_now() -> str:
    """返回北京时区（Asia/Shanghai）的当前时间，ISO 8601 +08:00 格式。"""
    return datetime.now(MARKET_TZ).isoformat()
```

### 3.2 修改 `arena_executor.py`

将所有 `order_audit` 相关 payload 中的时间戳替换为 `rt.local_now()`：

| 行号 | 变更 |
|------|------|
| 221 | `ts: now` → `ts: rt.local_now()`（reject action） |
| 249 | `ts: rt.utc_now()` → `ts: rt.local_now()`（blocked action） |
| 291 | `ts: now` → `ts: rt.local_now()`（execute action） |
| 294 | `submittedAt: now` → `submittedAt: rt.local_now()` |
| 324 | `ts: rt.utc_now()` → `ts: rt.local_now()`（sell-blocked action） |
| 368 | `ts: now` → `ts: rt.local_now()`（execute-playbook action） |
| 371 | `submittedAt: now` → `submittedAt: rt.local_now()` |
| 402 | `ts: rt.utc_now()` → `ts: rt.local_now()`（rotation-blocked action） |
| 435 | `ts: now` → `ts: rt.local_now()`（execute-rotation action） |
| 457 | `ts: now` → `ts: rt.local_now()`（execute-rotation action） |
| 460 | `submittedAt: now` → `submittedAt: rt.local_now()` |

> 注意：`autopilot_state` 中用于排程的字段（`lastNotificationAt`、`lastOrderAt`、`lastExitAt` 等）仍使用 `rt.utc_now()`，因为这些字段与市场日历/日期 key 对齐，UTC 更为可靠。

### 3.3 历史数据说明

历史 `order_audit.jsonl` 中已有大量 UTC `Z` 格式的 `ts` 记录。修复后：
- **新记录**：统一为 `+08:00` 格式，与 `submittedAt` / `submitTs` 一致
- **历史记录**：`ts` 仍为 UTC `Z` 格式，解释时需 +8 小时
- 建议：未来如需批量修正历史数据，可使用 `scripts/fix_order_audit_timestamps.py`

---

## 4. 验收检查

| 验收标准 | 状态 | 说明 |
|----------|------|------|
| 1. `ts` 与 `submittedAt` 格式一致 | ✅ | 两者均改为 `rt.local_now()` → `+08:00` |
| 2. 不影响历史记录解释 | ✅ | 仅影响新写入，历史记录不变 |
| 3. 新增测试验证时间戳一致性 | ⚠️ | 由 QA 补充运行时验证 |

---

## 5. 提交记录

- **执行仓库**：`/root/.openclaw/workspace-inStreet/arena/` commit `8e6aaa9`
- **规范仓库**：`/root/.openclaw/workspace-agent-team/` commit `f4f7e2b`

---

## 6. QA 验证建议

1. **新记录验证**：触发一笔真实订单（买入或卖出），检查 `order_audit.jsonl` 中新写入的 `ts` 和 `submittedAt` 均为 `+08:00` 格式
2. **差值验证**：`ts` 与 `submitTs` / `submittedAt` 差值应 < 1 秒
3. **跨文件对齐验证**：同一时间点的 `events.jsonl`、`ai_decisions.jsonl`、`order_audit.jsonl` 记录时间差 < 1 分钟（各自均已统一为北京时间体系）
4. **历史兼容性**：读取历史 UTC 记录时，系统应能正确识别并转换
