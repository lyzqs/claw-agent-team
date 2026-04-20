# Issue #7 实现与验证文档

**Issue**: #7 — 补强 Arena 流程观测，统一 review queue 口径并显式展示窗口/阻塞原因
**项目**: Arena (agent-team-arena)
**执行角色**: Dev
**完成时间**: 2026-04-17
**状态**: Dev 完成

---

## 验收标准与实现对照

### 验收标准 1：统一 review queue 口径

**要求**：统一或显式标注 runtime、events、dashboard/exporter 中 review queue / auto review 相关指标与字段的统计口径，避免同一时刻出现难以解释的认知偏差。

**实现**：

- **`arena_runtime.py`**（行 5438-5514）：新增 `build_review_queue_summary()` 函数，返回结构化对象包含：
  - `scopeLabel` — 当前队列范围（如 `"eligible"` / `"blocked"` / `"all"` 等）
  - `totalCount`、`eligibleCount`、`blockedCount`、`reviewedCount`、`omittedEligibleCount` — 五种互斥口径
  - `topBlockers` — 计数最高的 blocker 标签列表

- **`arena_runtime.py` run_summary 集成**（行 6889-6919）：在 `run_summary` 中新增显式字段：
  - `queueTotalCount`、`queueEligibleCount`、`queueBlockedCount`、`queueReviewedCount`、`queueOmittedEligibleCount`
  - `reviewQueueScopeLabel`、`windowMode`、`windowModeLabel`、`windowCanSubmit`、`windowBlocker`
  - `topBlockerLabel`、`topBlockerCount`

- **`arena_metrics_exporter.py`**（行 273-278）：将 `arena_auto_review_queue_total` 按 `queue_scope` 标签拆分为 5 种：
  - `queue_scope=all`（总数）
  - `queue_scope=eligible`（可送审）
  - `queue_scope=blocked`（被阻塞）
  - `queue_scope=reviewed`（已审核）
  - `queue_scope=omitted_eligible`（被忽略的可送审）

- **`arena_metrics_exporter.py`** `normalize_blocker_label` + `classify_review_blocker_type`：将 blocker 归类为 timing_window / lot_size / pending_limit / position_limit / daily_limit / mode_disabled / score_floor / other 八类

✅ 验收标准 1 已满足

### 验收标准 2：显式展示执行窗口和阻塞原因

**要求**：在合适的输出面（如 runtime、dashboard、summary 或相关观测层）清晰展示当前执行窗口、是否允许提交，以及主要阻塞原因。

**实现**：

- **`arena_runtime.py`** `build_review_queue_summary()` 包含：
  - `windowMode` — 如 `"post_close_1h"`、`"pre_open_1h"`、`"trading"` 等
  - `windowModeLabel` — 可读标签（如 "收盘后 1 小时观察窗口"）
  - `windowCanSubmit` — boolean，是否允许提交
  - `windowBlocker` — 主要阻塞原因文字说明
  - `blockerTypeSummary` — 归类后的 blocker 类型统计

- **`arena_runtime.py`** `run_summary` 集成同上（5 项显式字段）

- **`arena_runtime.py`** `runtime["process"]["externalDependencies"]`（行 6768）：外部依赖健康状态

- **`arena_metrics_exporter.py`** `ticket_blockers_total`（行 263）：按 blocker 类型（8 类）的 Prometheus Gauge

✅ 验收标准 2 已满足

### 验收标准 3：外部依赖可见性

**要求**：如存在关键外部依赖影响可执行性，至少在输出面中做到可见或可解释。

**实现**：

- **`arena_runtime.py`** `runtime_dependency_statuses()`（行 318-336）：返回新闻数据可用性、AI 决策结果可用性、外部 API 可达性状态

- `runtime["process"]["externalDependencies"]` 已集成到：
  - `build_review_queue_summary()` 的 `externalDependencies` 字段（行 5511）
  - `run_summary` 的 `externalDependencies` 字段（行 6768, 6857）
  - decisions JSONL 输出

- **`arena_metrics_exporter.py`** 中各运行时健康数据均通过 `/health` endpoint 采集

✅ 验收标准 3 已满足

### 验收标准 4：迭代目录文档

**要求**：在 Arena 投资流程优化迭代目录下新增实现/验证文档。

**实现**：

- 本文档创建于 `docs/arena-investment-flow-iteration/issue-7-review-queue-window-observability.md`
- Issue #2 调研文档：`docs/arena-local-implementation-survey-material.md`

✅ 验收标准 4 已满足

---

## 技术实现细节

### 核心函数

| 函数 | 文件 | 行号 | 说明 |
|---|---|---|---|
| `normalize_blocker_label()` | arena_runtime.py | 286 | 将 blocker 值规范化为字符串 |
| `classify_review_blocker_type()` | arena_runtime.py | 291 | 将 blocker 归类为 8 种类型之一 |
| `top_counter_entries()` | arena_runtime.py | 311 | 从 Counter 提取 top-N 条目 |
| `runtime_dependency_statuses()` | arena_runtime.py | 318 | 返回外部依赖健康状态列表 |
| `build_review_queue_summary()` | arena_runtime.py | 5438 | 构建 review queue 结构化摘要 |
| `_normalize_blocker_label()` | arena_metrics_exporter.py | ~31 | exporter 版规范化函数 |
| `_classify_review_blocker_type()` | arena_metrics_exporter.py | ~38 | exporter 版 blocker 分类函数 |

### 修改文件

| 文件 | 修改类型 | 关键变更 |
|---|---|---|
| `workspace-inStreet/arena/scripts/arena_runtime.py` | 修改 | 新增 4 个辅助函数 + `build_review_queue_summary()` + 集成到 runtime strategy 和 run_summary |
| `workspace-agent-team/scripts/arena_metrics_exporter.py` | 修改 | `arena_auto_review_queue_total` 拆分为 5 个 queue_scope 标签 + 修复 blocker 分类逻辑 |

### 指标变更

**Prometheus 指标**：`arena_auto_review_queue_total`

- 旧：单一指标，无 queue_scope 标签
- 新：按 `queue_scope={all,eligible,blocked,reviewed,omitted_eligible}` 拆分
- 向后兼容：保留原有指标名称，不破坏已有 Prometheus 面板

**Prometheus 指标**：`arena_ticket_blockers_total`

- 新增按 blocker 类型（8 类）的标签维度

---

## 验证方法

### 1. 语法验证

```bash
python3 -m py_compile /root/.openclaw/workspace-inStreet/arena/scripts/arena_runtime.py
python3 -m py_compile /root/.openclaw/workspace-agent-team/scripts/arena_metrics_exporter.py
```

### 2. Runtime JSON 验证

检查 `http://127.0.0.1:8788/api/summary` 响应中是否包含 `reviewQueueScopeLabel`、`windowMode`、`windowCanSubmit`、`windowBlocker` 字段。

### 3. Prometheus 指标验证

```bash
curl http://127.0.0.1:19150/metrics | grep arena_auto_review_queue_total
```

应看到 `queue_scope` 标签出现在指标中。

### 4. Grafana Dashboard

确认 Grafana dashboard 中 `arena_auto_review_queue_total` 面板按 `queue_scope` 标签筛选或分组显示。

---

## 已识别遗留问题

1. **Broken reference（已修复）**：`arena_metrics_exporter.py` 第 248 行原引用 `rt_classify_review_blocker_type(blocker)`（未定义函数），已替换为本地 `_classify_review_blocker_type(blocker)`

---

## 下一步

- **QA**：验证运行时指标输出是否符合验收标准
- **PM**：确认文档内容是否满足业务理解需求
- **Ops**：Grafana dashboard 按新 queue_scope 标签调整（如需要）

---

*本文档由 Agent Team Dev 角色生成，记录 Issue #7 的技术实现与验证结果。*
