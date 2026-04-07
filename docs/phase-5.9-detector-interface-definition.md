# Phase 5.9 Detector 接口定义

## 目标
把 **cron / heartbeat / 外部扫描器 / 被动监测器** 这类“发现问题并触发系统”的能力，从执行层和通知层中独立出来。

Detector 的职责不是执行任务，而是：
- 发现条件
- 形成结构化观察结果
- 决定是否生成 system issue 或 signal
- 把事件交给 ledger / manager / execution adapter 继续处理

---

## 一、定位
Detector 属于：**Detector / Trigger**。

它负责：
- 轮询或订阅外部状态
- 判断某个规则是否命中
- 产出检测结果（finding / signal）
- 在满足规则时生成 system issue 或 wake signal

它不负责：
- 直接执行 issue
- 直接宣告 issue 完成
- 用 detector 结果替代 execution result
- 直接把提醒当成业务完成

---

## 二、核心原则
1. **detector 只发现，不执行**
2. **detector 只触发，不闭环**
3. **detector 命中 ≠ issue 已创建成功 ≠ issue 已处理完成**
4. **同一检测命中要支持幂等去重**
5. **detector 产物必须可审计，可回放，可忽略**

---

## 三、输入模型

```json
{
  "detector_id": "heartbeat-email-checker",
  "run_id": "det_run_xxx",
  "kind": "cron|heartbeat|poller|webhook-derived",
  "scope": "project|org|external-system",
  "subject": "gmail-inbox|telegram-group|build-status|repo-watch",
  "context": {
    "project_key": "agent-team-core",
    "source": "heartbeat"
  },
  "observed_at_ms": 1770000000000,
  "payload": {
    "items": []
  }
}
```

### 字段说明
- `detector_id`：检测器定义 ID
- `run_id`：本次检测运行 ID
- `kind`：触发来源
- `scope`：组织/项目/外部系统级别
- `subject`：本次检测的对象
- `payload`：原始观察数据或归一化数据

---

## 四、输出模型

```json
{
  "detector_id": "heartbeat-email-checker",
  "run_id": "det_run_xxx",
  "status": "no_signal|signal|error|suppressed",
  "findings": [
    {
      "finding_id": "finding_xxx",
      "type": "new_item|stalled_issue|missing_ack|threshold_breach",
      "severity": "info|warn|error",
      "summary": "Detected a new actionable email",
      "dedupe_key": "email:gmail:message-123",
      "recommended_action": "create_system_issue",
      "metadata": {}
    }
  ],
  "emitted": {
    "system_issue_created": 1,
    "wake_sent": 0,
    "notifications_requested": 0
  },
  "error_code": null,
  "error_summary": null
}
```

---

## 五、推荐 finding 类型
- `new_item`
- `stalled_issue`
- `missing_ack`
- `threshold_breach`
- `state_divergence`
- `external_action_required`
- `watchdog_compensation_needed`

---

## 六、与其他层的边界
### Detector
- 发现问题
- 产出结构化 finding
- 建议下一动作
- 触发 system issue / wake signal

### Execution Adapter
- 真正派发执行
- 产生 run / completion / timeout / abort

### Notification Adapter
- 通知人或外部通道
- 返回 delivery side metadata

### Ledger / Manager
- 接住 detector finding
- 决定是否建 issue / 合并 / 忽略 / 升级

### 禁止混淆
以下都不允许：
- detector 直接把 issue 标为 closed
- detector 直接把任务执行掉却不经过 execution adapter
- detector 结果覆盖 execution result
- detector 命中就自动当作“问题已解决”

---

## 七、最小存储建议
建议新增旁路表 `detector_run` 与 `detector_finding`：

```sql
detector_run (
  id TEXT PRIMARY KEY,
  detector_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  scope TEXT,
  subject TEXT,
  status TEXT NOT NULL,
  observed_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  metadata_json TEXT
)
```

```sql
detector_finding (
  id TEXT PRIMARY KEY,
  detector_run_id TEXT NOT NULL,
  finding_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  dedupe_key TEXT,
  summary TEXT NOT NULL,
  recommended_action TEXT,
  metadata_json TEXT,
  created_at_ms INTEGER NOT NULL
)
```

用途：
- 记录 detector 命中历史
- 做幂等去重
- 做 watchdog / cron / heartbeat 的事后审计
- 避免 detector 直接污染 issue 主表

---

## 八、第一版推荐实现范围
第一版只需要支持：
1. 接口定义稳定
2. 一类最简单 detector（例如 heartbeat 扫描器）
3. `no_signal / signal / error / suppressed` 四态
4. dedupe_key 去重
5. 支持把 signal 转成 system issue

---

## 九、验收判断
5.9 可以判定完成，当且仅当：
- detector 的职责边界清楚
- 输入/输出模型清楚
- 与 execution / notification / ledger 分层清楚
- 已明确 detector 只负责发现与触发，不直接执行

---

## 十、下一步自然承接
在 5.9 完成后，后续可以自然进入：
- 6.x Human Queue 真实业务分支
- 或 8.x 恢复/补偿/可观测性

因为 detector 已经把“系统如何发现问题并触发”这层边界立住了。
