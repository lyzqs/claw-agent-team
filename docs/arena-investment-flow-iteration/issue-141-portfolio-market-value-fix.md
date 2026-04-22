# Issue #141 - 修复持仓市值数据不更新问题

## 变更摘要

**日期**: 2026-04-22
**类型**: Bug 修复（数据统计逻辑）
**影响**: portfolio.total_value, return_rate

## 问题背景

portfolio.total_value 仅显示现金余额 8,138 元，holdings_value 显示持仓成本 970,212 元，market_value 实际为 0，导致组合显示 -99% 亏损。这是数据统计 bug，不是真实亏损。

**症状**:
- portfolio.total_value = 8,138 (仅现金，stale)
- holdings_value = 970,212 (持仓市值，正确)
- return_rate = -99.19% (错误，因为 total_value 被 stale 值拖累)
- 所有 18 只持仓的 cost/currentPrice 实际有值，前端显示 null 是另一个渲染问题

**根因**: `summarize_portfolio` 读取 `portfolio_data.get("total_value")` 时拿到了陈旧的 cash-only 值（8,138），而不是 computed 的 cash + holdings_value（978,350）。

## 修复内容

### 修复 1: summarize_portfolio 调用前强制修正 total_value

在 `run_once()` 中调用 `summarize_portfolio` 前，先用 computed 值覆盖：

```python
_portfolio_data = dict(portfolio_data)
if holdings:
    computed_total = cash + holdings_value
    _portfolio_data["total_value"] = round(computed_total, 2)
portfolio_summary = summarize_portfolio(_portfolio_data, holdings, snapshots_list, config)
```

### 修复 2: summarize_portfolio 中同步修正 return_rate

在 `summarize_portfolio` 中用 corrected total_value 计算 return_rate：

```python
total_invested = safe_number(portfolio_data.get("total_invested"))
if total_invested and total_invested > 0:
    computed_return_rate = (total_value - total_invested) / total_invested
else:
    computed_return_rate = safe_number(portfolio_data.get("return_rate"))
# ...
summary["return_rate"] = round(computed_return_rate, 4)
```

## 修复后预期值

| 字段 | 修复前（stale） | 修复后（computed） |
|------|---------------|-------------------|
| portfolio.total_value | 8,138.44 | 978,350.44 |
| return_rate | -99.19% | -2.17% |
| cash_ratio | ~100% | ~0.83% |

## 验证方式

1. 等待下一次 arena_runtime cycle（约 5 分钟）
2. 检查 `data/runtime.json` 中 `portfolio.total_value` 是否为 978,350.44
3. 检查 `portfolio.return_rate` 是否约为 -2.17%
4. 检查 Grafana arena 面板 portfolio_total_value 指标

## 相关文件

- `scripts/arena_runtime.py`: summarize_portfolio + run_once 修改
- `data/runtime.json`: 将在下次 cycle 时被修正

## 注意

- 持仓的 cost/currentPrice 字段在 runtime.json 中**已正确填充**（avg_cost=4.66, current_price=4.58 等），问题在于 portfolio 层的 total_value 计算
- 前端显示 null 可能是另一个渲染/序列化问题，需另行排查