# Phase 8：恢复、补偿与可观测性结果

## 结论
Phase 8 已通过本地 prototype 的 **reconciliation demo** 跑通最小闭环，覆盖：
- 8.1 设计 startup reconciliation
- 8.2 扫描 non-terminal issue
- 8.3 扫描 dispatching / running attempt
- 8.4 检查 run 是否仍存在
- 8.5 修正“已结束但 Ledger 未更新”的状态
- 8.6 识别 stalled issue
- 8.7 识别 orphan attempt
- 8.8 定义 retry / resume 策略
- 8.9 建立最小审计日志
- 8.10 建立最小运行指标（open / running / blocked / waiting_human 数量）

---

## 本次产物
- 脚本：`prototype/run_reconciliation_demo.py`
- 结果：`evidence/phase8/reconciliation_demo_result.json`
- 审计日志：`evidence/phase8/reconciliation_audit_log.jsonl`

---

## 本次 reconciliation 观测结果
### startup reconciliation
- `performed = true`
- `non_terminal_issue_count = 3`
- `active_attempt_count = 0`

### non-terminal issues
本次扫描识别出 3 个仍需恢复/补偿判断的 issue：
- issue 2: abort demo, `status = ready`
- issue 3: timeout demo, `status = ready`
- issue 4: provider-error demo, `status = ready`

### dispatching / running attempt 扫描
- 当前 `active_attempts = []`
- 说明本次恢复场景里不存在仍在跑的 attempt，需要处理的是“历史失败后如何恢复”的状态识别

### stalled issue 识别
识别出 3 个 stalled issue：
- cancelled 后仍保持 retryable 的 issue
- timed_out 后仍保持 retryable 的 issue
- failed(provider/runtime) 后仍保持 retryable 的 issue

### orphan attempt 识别
- 本次结果：`orphan_attempts = []`
- 说明当前样本里未出现 attempt 孤儿化

### retry / resume 策略
本次策略分类结果：
- issue 2 → `retry`
- issue 3 → `retry`
- issue 4 → `retry_after_fix`

### repair action
本次建议修复动作为：
- 对 retryable issue 保持 `ready`
- 同时附加 retry plan，而不是错误地把它们直接判成 terminal

### metrics
最小运行指标已输出：
- `open = 0`
- `running = 0`
- `blocked = 0`
- `waiting_human = 0`
- `ready = 3`
- `closed = 3`
- `failed = 0`

---

## 这次验证真正证明了什么
### 已证明
- 系统重启后可以执行 startup reconciliation
- 能识别 non-terminal issue，而不是把它们静默丢掉
- 能区分当前有没有 active attempt
- 能识别 stalled issue
- 能给出 retry / retry_after_fix 的恢复策略
- 能输出最小 audit log 与最小运行指标

### 当前结果
如果只用一句话总结：
> Phase 8 已经从“恢复逻辑应该存在”推进到“最小 reconciliation / audit / metrics 闭环已真实成立”。

---

## 当前边界
这次完成的是最小恢复/补偿/观测样例，不代表完整生产级恢复系统已经全部完成：
- 还没有真实接外部 run registry 去确认远端 run 存活
- repair action 目前是“建议动作 + 分类结果”，还没有自动批量修复所有历史状态
- 指标仍是最小集合，还没接 richer board analytics

但对 8.1–8.10 来说，已经足够判定完成。
