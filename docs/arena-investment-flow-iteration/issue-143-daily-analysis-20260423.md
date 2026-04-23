# Issue #143 - 每日 Arena 投资情况分析

**分析日期**: 2026-04-23（周四，交易日）
**分析时间**: 2026-04-24 02:26 UTC

## 市场状态

- **市场状态**: 分歧
- **pseudo RSI**: 全天 90.9 顶格（超买极端值）
- **AI 决策**: 全天 watch/reject，候选均被 RSI 拦死

## 组合状态

| 指标 | 值 |
|------|-----|
| 总市值 | 974,579.72 |
| 现金 | 22,725.72 |
| 持仓市值 | 951,854.00 |
| 累计收益率 | -2.54% |
| 持仓数量 | 19 只 |

## AI 决策模式分析

### 决策统计（2026-04-23 约 8 轮决策）

| 决策 | 次数 | 原因 |
|------|------|------|
| watch | 主要 | pseudo RSI 90.9 超买，无候选 |
| reject | 若干 | pseudo RSI 超激进闸门（88） |
| execute_rotation | 2 | fallback 接管，rotation_prepare 信号触发 |

### 关键发现

1. **pseudo RSI 90.9 全天未回落**：所有候选（宝丰能源、新集能源、航发动力）均被 RSI 拦死，near-miss 候选持续在 score=17-18 但无法执行
2. **Gateway 退出决策返回空**：全天 AI 决策中 Gateway exit decisions 持续返回空结果，系统回退到确定性规则裁决（fallback）
3. **rotation_prepare 信号触发但 cash 不足**：
   - sh600115（中国东航）rotation_prepare 可执行（replacement=宝丰能源 score=18）
   - 但现金 22,725 不足买入宝丰能源
4. **partial_exit 候选重复出现**：生益科技+13.63%、中国船舶+20.32% 在多轮中触发 partial_exit 但均因"无可卖股"跳过

## 优化点识别

### 1. Gateway Exit Decision 空结果问题（高优先级）
- **现象**：Gateway 退出决策持续返回空结果，导致系统依赖 fallback 确定性规则
- **影响**：exit playbook（止损/止盈/换仓）执行效率降低
- **建议**：检查 Gateway agent 的 exit playbook 评分逻辑是否正常

### 2. rotation_prepare 现金前置校验（中优先级）
- **现象**：rotation_prepare 信号触发后因 cash 不足无法执行，候选票（宝丰能源 score=18）在队列中等待
- **影响**：有效 rotation 信号被延迟，换仓时机延误
- **建议**：在 rotation_prepare 触发前增加 cash 可用性校验，提前过滤

### 3. partial_exit 候选重复触发（中优先级）
- **现象**：生益科技+13.63%、中国船舶+20.32% 在多轮中重复生成 partial_exit 候选，但每次均因"已执行过"跳过
- **影响**：队列噪音增加，每轮无效计算
- **建议**：在 partial_exit 触发后设置冷却期（如 1 天），避免重复触发

### 4. pseudo RSI 数据陈旧（需确认）
- **现象**：全天 90.9 无变化，疑似 snapshot 数据陈旧
- **影响**：RSI 指标失真导致决策失效
- **建议**：检查 stock_universe_snapshots.jsonl 数据更新频率

## 建议派发的子 Issue

| 优先级 | 标题 | 描述 |
|--------|------|------|
| P2 | 排查 Gateway Exit Decision 返回空结果问题 | Gateway 退出决策持续返回空，导致 fallback 接管 |
| P3 | rotation_prepare 增加现金前置校验 | rotation 信号触发前提前过滤 cash 不足情况 |
| P3 | partial_exit 候选增加冷却期 | 已执行过的 partial_exit 候选设置冷却，避免重复触发 |

## 备注

- Issue #141（持仓市值修复）已修复 runtime total_value 统计口径
- Issue #142（state score floor 调整）已生效，等待完整市场周期验证
- 今日（2026-04-24，周五）分歧市场持续，关注 RSI 是否回落
