# Issue #136 实现与验证文档

**Issue**: #136 — 排查并修复 portfolio total_value 与 cash 字段计算逻辑
**项目**: Arena (agent-team-arena)
**执行角色**: Dev
**完成时间**: 2026-04-21
**状态**: Dev 完成

---

## 问题确认

**症状**：runtime.json（07:04 UTC）显示 `total_value=107892.37, cash=107892.37`（全现金），但 ai_decisions.jsonl（06:56 UTC）显示 portfolio 总值约 977859，且持仓完整。

**根因**：`summarize_portfolio()` 函数（原代码行 6384）使用：
```python
total_value = safe_number(portfolio_data.get("total_value"), safe_number(portfolio_data.get("cash")))
```
当 API 返回的 `total_value` 缺失或异常时（如 `total_value=107892.37, cash=107892.37` 但有持仓约 870k），回退值变成了 `cash` 而不是 `cash + holdings_value`，导致出现"全现金"假象。

---

## 验收标准与实现对照

### 验收标准 1：runtime.json 中 portfolio.total_value 与实际持仓市值一致

**实现**：`summarize_portfolio()` 修改逻辑：
- 有持仓时：`total_value = cash + holdings_value`（基于持仓市值计算，不依赖 API 返回值）
- 无持仓时：`total_value = api_total_value or cash`
- 当有持仓但 API 返回的 `total_value` 与计算值差异超过 1% 时，记录 `portfolio-consistency` 警告事件
- 结果中新增 `holdings_value` 和 `total_value` 字段，确保输出包含正确的计算值

### 验收标准 2：portfolio 数据不一致时写入 event 日志供排查

**实现**：
- `status=warn`：有持仓但 API 返回的 total_value 与 computed 值差异 >1%
- `status=info`：无持仓且 API 未返回 total_value

日志事件字段：`api_total_value`, `cash`, `holdings_value`, `computed_total`, `holding_count`, `inconsistency_detected`

### 验收标准 3：在非交易日验证 portfolio 数据一致性

**说明**：`summarize_portfolio()` 在每次运行时均执行一致性校验，不依赖交易日状态。在非交易日同样生效。

---

## 技术变更详情

### 修改文件

`workspace-inStreet/arena/scripts/arena_runtime.py`

**变更位置**：`summarize_portfolio()` 函数（行 ~6383）

**变更内容**：

```diff
- total_value = safe_number(portfolio_data.get("total_value"), safe_number(portfolio_data.get("cash")))
- cash = safe_number(portfolio_data.get("cash"))
- holding_values = sorted([...])
+ cash = safe_number(portfolio_data.get("cash"))
+ holding_values = sorted([safe_number(item.get("market_value")) for item in holdings], reverse=True)
+ holdings_value = sum(holding_values)
+ api_total_value = safe_number(portfolio_data.get("total_value"))
+ # 计算正确 total_value：有持仓时必须基于 cash + holdings_value；无持仓时信任 API 返回值
+ if holdings_value > 0:
+     total_value = cash + holdings_value
+     if api_total_value and abs(api_total_value - total_value) / total_value > 0.01:
+         log_event("portfolio-consistency", f"portfolio.total_value 数据不一致：API={api_total_value:.2f}, computed={total_value:.2f}", ...)
+ else:
+     total_value = api_total_value or cash
+     if not api_total_value:
+         log_event("portfolio-consistency", f"portfolio.total_value 缺失（无持仓），回退到 cash={cash:.2f}", ...)

  summary = dict(portfolio_data)
  summary.update({
+     "holdings_value": round(holdings_value, 2),
+     "total_value": round(total_value, 2),
      ...
  })
```

---

## 验证方法

### 1. 语法验证

```bash
python3 -m py_compile arena_runtime.py
```

### 2. 代码路径验证（模拟持仓场景）

当 `holdings=[{market_value: 870000}]` + `cash=107892.37`：
- API 返回 `total_value=107892.37` → 计算值 `total_value=977892.37` → 差异 >1% → 记录 `warn` 事件 ✅
- API 返回 `total_value=977892.37` → 差异 <1% → 不记录事件 ✅
- 无持仓 + 无 API total_value → 回退到 cash ✅

### 3. 非交易日验证

`summarize_portfolio()` 无交易日依赖，可直接测试：
```python
# 模拟非交易日场景
portfolio_data = {"cash": 500000, "total_value": None}
holdings = []
result = summarize_portfolio(portfolio_data, holdings, [], config)
# 应返回 total_value = 500000，记录 info 事件
```

---

## 残留风险

1. **API 端数据源问题**：本修复处理了计算层，但如果上游 `/api/v1/arena/portfolio` 本身返回错误的 `total_value`（如上面场景），根本原因在 API 端，不在本修改范围内
2. **snapshot 中的 total_value**：快照数据（snapshots）中仍然依赖原始 `total_value` 字段，未做一致性校验

---

## 下一步

- **QA**：在非交易日验证 portfolio 数据一致性，检查 `portfolio-consistency` 事件是否正常写入
- **Ops**：确认 `/api/v1/arena/portfolio` 端是否存在数据源问题（如有持仓时错误返回 `total_value=cash`）

---

*本文档由 Agent Team Dev 角色生成，记录 Issue #136 的技术实现与验证结果。*
