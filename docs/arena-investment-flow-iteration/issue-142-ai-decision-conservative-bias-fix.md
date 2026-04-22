# Issue #142 - AI决策规则保守偏误优化

## 变更摘要

**日期**: 2026-04-22
**类型**: 策略参数调整

## 问题背景

Decision Validation 显示分歧市场（positive_ratio=0.42, strong_ratio=0.1133）近 4 次 near-miss（watch/reject），规则可能偏保守。state score floor 在分歧市场为 17，可能过高导致候选稀少。

## Near-Miss 基线测量（变更前）

**测量时间**: 2026-04-22 18:54 UTC
**数据范围**: 最近 48 小时
**方法**: 从 ai_decisions.jsonl 解析 contentText JSON 中的 stage1Shadow 数据

**结果**:
- 48h 内 stage1Shadow 候选: 7 条
- Score 分布: score=17: 6条, score=18: 1条
- Score 15-16: 0条（低于当前 baseline 分歧 floor=17）

**Blocker 分析**:
| Blocker | 全部7条 | Score 16-17 (6条) |
|---------|---------|---------------------|
| RSI (pseudo RSI) | 7/7 (100%) | 6/6 (100%) |
| VOL (波动率) | 7/7 (100%) | 6/6 (100%) |
| SCORE (分数) | 3/7 (43%) | 3/6 (50%) |
| CLOSE (closeStrength) | 2/7 (29%) | 2/6 (33%) |

**关键发现**:
- 所有 near-miss 候选都因 **pseudo RSI** 顶格（90.9）和**波动率**超标被堵死
- 仅 3/7 (43%) 的 near-miss 候选同时有 "SCORE" 作为拦死因素
- 当前 baseline 分歧 floor=17，conservative=18，aggressive=16
- Score 16 以下（15-16）候选: 0 条 → 降低 floor 不会立即增加候选

## 策略变更

### autopilot 分歧市场 stateScoreFloor

- **变更前**: `{"分歧": 17}`
- **变更后**: `{"分歧": 16}`
- **理由**: 下调 1 分，使 score=16 的候选也能进入 AI 复核

### Rollback 信息

如需回滚，执行以下命令：
```bash
cd /root/.openclaw/workspace-inStreet/arena
sed -i 's/\"分歧\": 16/\"分歧\": 17/' config/strategy.json
```

或手动编辑 `config/strategy.json`:
```json
"autopilot": {
  "stateScoreFloor": {
    "分歧": 17  // 从 16 改回 17
  }
}
```

## 重要注意事项

1. **真正的 near-miss 瓶颈是 RSI/volatility，不是分数**: 降低 floor 不会大幅增加可执行候选，除非 RSI 阈值同步放宽
2. **需要完整市场周期验证**: 当前市场为分歧（分歧→修复→亢奋 需要数天到数周）
3. **AI 决策有保守护栏**: 即使 floor 降低，AI 仍可在 stage2 以 "reject" 或 "watch" 拒绝候选
4. **conservative/aggressive profile 未变更**: conservative分歧=18 保持不变

## 验收标准追踪

- [x] 记录优化前 near-miss 基线（7条候选，48h）
- [ ] 完整市场周期验证（待观察）
- [ ] Decision Validation 报告中 near-miss 减少（待观察）
- [x] 策略调整需有回滚机制（rollback 信息已记录）

## 相关文件

- `config/strategy.json`: autopilot.stateScoreFloor["分歧"] 17→16
- `data/runtime.json`: stateScoreFloor 同步更新
