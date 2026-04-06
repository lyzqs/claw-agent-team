# Phase 5.6 Notification Adapter 定义

## 目标
把**消息提醒 / 协调通知**从权威执行里拆出去，保证系统能明确区分：
- 什么是**真正执行**
- 什么是**通知成功**
- 什么只是**触发 / 协调**

这层 adapter 的职责不是推进 issue 真相，而是把已经发生的状态，可靠地通知给人或外部通道。

---

## 一、定位
Notification Adapter 属于：**Notification / Coordination**。

它不负责：
- 执行任务
- 创建执行结果
- 伪造 issue 完成
- 直接把通知结果当作任务结果

它只负责：
- 把需要通知的内容发送到目标通道
- 记录通知是否成功
- 产出通知侧的 delivery / ack 元数据

---

## 二、核心原则
1. **通知不是执行**
2. **通知成功 ≠ 任务完成**
3. **通知失败不能回滚真实执行结果**
4. **通知可以重试，但不能制造重复业务状态**
5. **通知记录挂在 attempt / checkpoint / issue 旁路元数据上，不成为真相主状态机本体**

---

## 三、输入模型

```json
{
  "notification_id": "notif_xxx",
  "source_type": "issue|attempt|checkpoint|system",
  "source_id": "attempt_xxx",
  "event_type": "checkpoint_recorded|attempt_failed|issue_waiting_human|issue_closed",
  "channel": "telegram|feishu|webhook|email_stub",
  "target": "channel/user/thread/webhook endpoint",
  "title": "optional short title",
  "body_md": "human readable notification body",
  "metadata": {
    "project_key": "agent-team-core",
    "issue_no": 4,
    "attempt_no": 1,
    "severity": "info|warn|error"
  },
  "idempotency_key": "stable-key-for-dedup"
}
```

### 字段说明
- `notification_id`：通知事件唯一 ID
- `source_type/source_id`：通知来源对象
- `event_type`：通知语义类型
- `channel`：通知适配器目标通道
- `target`：接收对象
- `body_md`：展示给人的正文
- `idempotency_key`：用于避免重复发送

---

## 四、输出模型

```json
{
  "notification_id": "notif_xxx",
  "accepted": true,
  "channel": "telegram",
  "target": "group:-5114007576",
  "delivery_status": "sent|failed|retryable",
  "delivery_ref": "provider-message-id-or-request-id",
  "delivered_at_ms": 1770000000000,
  "error_code": null,
  "error_summary": null
}
```

### 约束
- `delivery_status=sent` 只表示通知发出成功
- 不能因此修改 issue 为 `closed/running/...`
- 若 `failed/retryable`，仅记录通知侧失败，不篡改执行结论

---

## 五、推荐事件类型
- `checkpoint_recorded`
- `attempt_dispatched`
- `attempt_failed`
- `attempt_timed_out`
- `issue_waiting_human`
- `issue_returned_to_agent_queue`
- `issue_closed`
- `system_issue_created`

---

## 六、与 Execution Adapter 的边界
### Execution Adapter 负责
- dispatch
- completion
- timeout / abort / provider-error
- ledger 真相回写

### Notification Adapter 负责
- 把这些已发生事件通知出去
- 返回 delivery 结果
- 记录通知引用号

### 禁止混淆
以下场景都不允许：
- 因为消息发出成功，就把 issue 当完成
- 因为消息发出失败，就回滚已完成的 attempt
- 用消息发送替代 dispatch/run

---

## 七、最小存储建议
可新增旁路表 `notification_delivery`：

```sql
notification_delivery (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  target TEXT NOT NULL,
  event_type TEXT NOT NULL,
  delivery_status TEXT NOT NULL,
  delivery_ref TEXT,
  idempotency_key TEXT,
  error_code TEXT,
  error_summary TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
)
```

用途：
- 审计通知发送结果
- 后续做重试/补发
- 与 issue 真相层分离

---

## 八、第一版推荐实现范围
第一版只需要支持：
1. 接口定义稳定
2. 一种实际消息通道（例如 Telegram 或 Feishu）
3. `sent / failed / retryable` 三态
4. idempotency 去重
5. 不反写 issue 真相状态

---

## 九、验收判断
5.6 可以判定完成，当且仅当：
- notification adapter 的职责边界清楚
- 输入/输出模型清楚
- 与 execution adapter 的分工清楚
- 已明确通知记录属于旁路 delivery 层，而不是真相状态机

---

## 十、下一步自然承接
5.7 可以直接在这个定义上继续：
- 选一条真实消息通道
- 做一次真实发送
- 把 delivery 结果落到 `notification_delivery` 或等价旁路记录中
