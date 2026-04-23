# Issue #145 - rotation_prepare 增加现金前置校验

## 状态
- **状态**: Dev 完成 ✅
- **完成时间**: 2026-04-23

## 背景
2026-04-23 rotation_prepare 信号（sh600115 换仓到宝丰能源 score=18）触发后因现金不足（22,725元）无法执行。候选票在队列中等待，造成时机延误。

## 实现方案

### 核心修复
在 `evaluate_rotation_candidate` 中增加现金前置校验：
- 若当前可用现金 < 最小买入金额（price * 100），添加 blocker "现金不足：当前可用 X 元，最小买入需要 Y 元"
- rotation_prepare 信号在触发时检查 cash 充足率

### 参数传递链路
`build_exit_playbooks(portfolio_data)` → `choose_rotation_replacement_candidates(portfolio_data)` → `evaluate_rotation_candidate(portfolio_data)`

1. `build_exit_playbooks` 增加 `portfolio_data` 参数
2. `choose_rotation_replacement_candidates` 增加 `portfolio_data` 参数并传递给 `evaluate_rotation_candidate`
3. `evaluate_rotation_candidate` 增加 `portfolio_data` 参数，使用 `portfolio_data.get("cash")` 做现金校验

## 验收标准检查
| # | 标准 | 状态 |
|---|------|------|
| 1 | rotation_prepare 信号触发时，cash 充足率 > 候选标的最低买入金额 | ✅ |
| 2 | cash 不足时，rotation 信号不在队列中显示 | ✅ |

## 关键代码
- `arena/scripts/arena_runtime.py`:
  - `evaluate_rotation_candidate`: 增加 portfolio_data 参数和现金校验 (lines 1732-1775)
  - `choose_rotation_replacement_candidates`: 增加 portfolio_data 参数传递 (line 1777)
  - `build_exit_playbooks`: 增加 portfolio_data 参数 (line 4341)
  - `build_exit_playbooks` 调用处: 传递 portfolio_data (line 6787)

## commit
- `d4a607b arena Issue #145: rotation_prepare cash pre-check`
