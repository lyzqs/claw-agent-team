# Issue #152 - 每日arena股票投资情况分析（2026-04-28 周二）

## 状态
- **状态**: Dev 分析完成 + 关键 Bug 修复 ✅
- **分析时间**: 2026-04-28
- **最近完整运行时数据**: 2026-04-23

## 数据概览

### 市场状态（2026-04-28 最新快照，07:56 UTC）
| 指标 | 2026-04-28 | 2026-04-23 | 变化 |
|------|-------------|-------------|------|
| positive_ratio | 52% | 27% | ↑ 显著改善 |
| strong_ratio | 15.67% | 8.33% | ↑ 大幅提升 |
| median_change | +0.07% | -0.53% | ↑ 转正 |
| top10_avg_change | +6.87% | +6.27% | ↑ |
| market_state | 分歧 | 分歧 | 相同 |

### 最新组合（2026-04-23 cycle，未更新）
| 指标 | 值 |
|------|-----|
| 现金 | 22,725.72 元 |
| 总市值 | 974,579.72 元 |
| 持仓股票 | 19 只 |
| 总浮亏 | -29,133.00 元 (-2.99%) |

### 运行时事件（2026-04-28）
| 事件类型 | 数量 | 说明 |
|----------|------|------|
| exit-playbooks | 116 | 生成退出剧本事件 |
| runtime | 60 | 策略循环（全部失败！） |
| scoring | 116 | 候选股评分 |
| market-clock | 116 | 交易日识别 |
| daily-brief | 116 | 每日简报 |
| news | 116 | 新闻抓取 |
| alerts | 116 | 预警生成 |
| fetch | 118 | Arena 数据抓取 |
| **http-retry** | **31** | **News Analysis 请求重试** |

---

## 🚨 关键 Bug 发现与修复

### Bug #1: `name 'cash' is not defined` 导致策略循环全量失败
**严重程度**: P0 - 阻断性 Bug

**问题描述**: 2026-04-28 00:32 以来，所有 60 次策略循环（`run_once()`）全部失败，错误：`name 'cash' is not defined`

**错误堆栈**:
```
策略循环失败: name 'cash' is not defined
```

**根因定位**: `run_once()` 函数第 6801 行：
```python
if holdings:
    computed_total = cash + holdings_value  # ← cash 未定义！
    _portfolio_data["total_value"] = round(computed_total, 2)
```

`cash` 和 `holdings_value` 是 `summarize_portfolio()` 函数的局部变量，从未在 `run_once()` 作用域中定义。

**为什么之前不报错**: 2026-04-23 之前，Arena API 不返回 `holdings` 数据，`holdings` 列表为空，`if holdings:` 分支从未执行。2026-04-24 起 API 开始返回持仓数据，触发此代码路径并崩溃。

**已修复**: 删除重复且错误的代码块（arena/scripts/arena_runtime.py lines 6799-6802），`summarize_portfolio()` 内部已正确计算 `total_value = cash + holdings_value`。Commit c533cd7。

### Bug #2: API 认证问题
**影响**: 1 次 HTTP 404（"Agent 尚未参赛"），1 次 HTTP 401（"Invalid API key"）
**说明**: API 认证凭据可能过期或 Arena 账号状态异常，需 Ops 排查

---

## 运行时统计

| 指标 | 数值 |
|------|------|
| 策略循环总次数 | 60 |
| 成功次数 | 0 |
| 失败次数 | 60 |
| 失败率 | 100% |
| 失败原因 | `name 'cash' is not defined`（Bug #1） |
| News Analysis 重试率 | 31/60 = 51.7% |
| 订单执行（2026-04-27至今） | 0 |

---

## 无需派发新 Issue 的优化点

1. **Gateway retry 51.7%**: Issue #147 已覆盖（health probe + retry）
2. **cash bug**: 已修复（commit c533cd7）
3. **API 认证**: 属于 Ops 排查范围，非 Dev 优化
4. **市场改善**: positive_ratio 从 27% 升至 52%，分歧状态改善，系统无需操作

## 建议

1. **立即**: Ops 确认 API 认证状态（401/404 错误）
2. **立即**: 验证 c533cd7 修复生效（策略循环恢复正常）
3. **下一交易日**: 验证 Issue #145 cash 预检生效
4. **下一交易日**: 验证 Issue #147 Gateway reliability 生效

## commit
- `c533cd7 arena: fix 'cash' is not defined crash in run_once (line 6800 typo)`
