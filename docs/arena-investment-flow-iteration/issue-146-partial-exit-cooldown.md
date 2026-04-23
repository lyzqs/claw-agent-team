# Issue #146 - partial_exit 候选增加冷却期

## 状态
- **状态**: Dev 完成 ✅
- **完成时间**: 2026-04-23

## 背景
生益科技+13.63%、中国船舶+20.32% 在多轮 AI 决策中被重复生成 partial_exit 候选，但每次均因已执行跳过，造成队列噪音增加。

## 实现方案

### 1. 构建冷却期追踪 (autopilot_state)
在 `autopilot_state` 中增加 `recentPartialExitSymbols` 字段，记录已执行 partial_exit 的 symbol 及其时间戳。

### 2. 冷却期读取 (build_exit_review_candidates)
- 从 `autopilot_state.recentPartialExitSymbols` 读取冷却期内的 symbol 集合
- 冷却时间窗口：从 `strategy.partialExitCooldownSeconds` 获取（默认 86400 秒 = 1 天）
- 超过冷却期的 symbol 自动失效

### 3. 过滤冷却中的候选 (build_exit_review_candidates)
- 遍历 exitPlaybooks 时，对 `partial_exit` 类型候选检查是否在冷却集合中
- 若在冷却中则跳过该候选，不进入 review queue

### 4. 执行后记录 (maybe_run_gateway_exit_decision)
- 当 `execute_sell` / `execute_rotation` 执行且 top.type == "partial_exit" 时
- 将 {symbol, ts} 追加到 autopilot_state.recentPartialExitSymbols
- 保留最近 50 条，按 symbol 去重

## 验收标准检查
| # | 标准 | 状态 |
|---|------|------|
| 1 | partial_exit 触发后对应 symbol 进入冷却期 | ✅ |
| 2 | 冷却期内不再生成该 symbol 的 partial_exit 候选 | ✅ |
| 3 | 冷却期满后可重新评估 | ✅ |
| 4 | 可通过 AI 决策日志验证冷却效果 | ✅ |

## 配置参数
```json
// strategy.json (可选，默认86400秒=1天)
"partialExitCooldownSeconds": 86400
```

## 关键代码
- `arena/scripts/arena_runtime.py`:
  - `build_exit_review_candidates()`: 冷却期读取 + 候选过滤 (lines 1281-1308)
  - `maybe_run_gateway_exit_decision()`: 执行后记录冷却 symbol (lines 1588-1604)
- 需传入 `autopilot_state` 参数以读取和更新冷却追踪

## commit
- `6f7b340 arena: add partial exit symbol-level cooldown + OHLC dimension aggregation for symbol charts`
