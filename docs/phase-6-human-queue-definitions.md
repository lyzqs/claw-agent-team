# Phase 6：Human Queue 与人工介入机制定义

## 目标
把“需要人处理”的任务从普通 issue 流里明确分层，让 Human Queue 成为结构化介入面板，而不是杂乱的人工待办堆。

本文件一次性收口以下定义项：
- 6.1 `waiting_human_info`
- 6.2 `waiting_human_action`
- 6.3 `waiting_human_approval`
- 6.4 人工处理后的回流规则
- 6.5 `resolved_by_human`
- 6.6 `returned_to_agent_queue`
- 6.7 Human Queue 卡片必备字段

---

## 一、Human Queue 的定位
Human Queue 不是另一套真相系统，而是 **Issue 在特定人工等待状态下的投影视图**。

也就是说：
- 真相仍在 Ledger / issue
- Human Queue 只是把 `waiting_human_*` 状态投影出来
- 人工处理动作会改 issue 状态和相关字段，而不是只改卡片表面展示

---

## 二、三类 waiting_human 状态

### 6.1 `waiting_human_info`
含义：
- 系统缺信息，当前无法继续推进
- 需要人补充事实、参数、素材、上下文、选择范围

典型例子：
- 缺 API key / 文件 / 链接 / 账号信息
- 缺业务规则说明
- 缺验收标准或目标样本

进入条件：
- 经过 PREBLOCK_CHECK 后，确认无法仅靠 agent 自救
- 缺失的是“信息”，不是“动作许可”也不是“最终拍板”

退出条件：
- 人提供了足够信息
- issue 更新后可重新进入 agent queue

---

### 6.2 `waiting_human_action`
含义：
- 不是缺解释，而是需要人亲自做一个外部动作
- 系统自己做不了，且做完后可继续

典型例子：
- 人需要登录/授权
- 人需要手动点击某外部系统
- 人需要上传文件到指定位置
- 人需要线下完成某个前置步骤

进入条件：
- 问题已清楚定义
- 下一步不是“告诉系统更多内容”，而是“人去做一个动作”

退出条件：
- 人确认动作已完成
- 或外部状态被 detector 观察到已变化

---

### 6.3 `waiting_human_approval`
含义：
- 任务技术上可以继续，但需要人拍板/批准
- 重点不是信息缺失，而是治理权归属在人

典型例子：
- 是否上线/发送/删除/扩编
- 是否接受方案 A / B
- 是否批准预算/权限/变更

进入条件：
- 系统已有可执行下一步
- 但因风险、治理、预算或权限边界，必须由人明确授权

退出条件：
- 人批准 / 拒绝 / 选择方案

---

## 三、回流规则（6.4）
Human Queue 里的 issue 在人工处理后，不能断链，必须回到明确状态机。

### 回流总原则
1. 人工处理不会凭空结束 issue
2. 人工处理结果必须显式写回 issue / checkpoint
3. 回流后必须进入：
   - `ready`
   - `dispatching`
   - `running`
   - `closed`
   - `failed`
   之一，不能停留在模糊状态

### 按状态的回流
- `waiting_human_info`：补齐信息后 → `ready`
- `waiting_human_action`：动作完成后 → `ready`
- `waiting_human_approval`：批准后 → `ready` 或下一执行态；拒绝后 → `failed` / `closed` / `blocked`（依业务而定）

---

## 四、人工处理结果语义

### 6.5 `resolved_by_human`
含义：
- 问题由人直接处理完毕，不再需要 agent 继续执行

适用场景：
- 人自己完成了外部动作且这就是终局
- 人明确表示“不继续做了/我已自己处理”
- 人工介入本身就等于问题完成

建议写法：
- 作为 checkpoint kind 或 issue metadata/result flag 记录
- 不新增混乱主状态；最终 issue 可进入 `closed`

---

### 6.6 `returned_to_agent_queue`
含义：
- 人的处理只是补齐条件
- 后续仍应由 agent 继续推进

适用场景：
- 人补了信息
- 人完成了授权/上传/登录
- 人做了批准，系统现在可以继续跑

建议写法：
- 记录一个 checkpoint：`returned_to_agent_queue`
- 然后把 issue 状态切回 `ready`

---

## 五、Human Queue 卡片字段（6.7）
每张 Human Queue 卡片至少必须展示：

1. `issue_no`
2. `title`
3. `status`
4. `blocker`
5. `next_step`
6. `required_input`

建议扩展字段：
7. `severity / priority`
8. `owner / assigned role`
9. `waiting_since`
10. `last_checkpoint_summary`
11. `recommended_human_action`
12. `return_condition`

### 字段定义
- `blocker`：为什么卡住
- `next_step`：人处理完之后系统下一步会做什么
- `required_input`：当前到底需要人给什么

关键要求：
> 人看到卡片后，应能在几秒内判断：
> - 我是要补信息？
> - 我要亲自动手？
> - 还是我要拍板批准？

---

## 六、和其他层的边界
### Ledger
- 真相层
- 存 issue 当前状态与回流结果

### Human Queue
- 只读投影层
- 展示 `waiting_human_*`

### Notification Adapter
- 负责把 Human Queue 事项提醒给人

### Detector
- 可以检测“人已经完成外部动作”，从而触发回流

### Execution Adapter
- 人工阶段结束后，继续接手执行

---

## 七、推荐最小数据字段补充
当前 schema 已具备：
- `waiting_human_info`
- `waiting_human_action`
- `waiting_human_approval`
- `blocker_summary`
- `required_human_input`

建议后续补充但不是这次定义的硬前置：
- `human_resolution_type`
- `return_condition`
- `recommended_human_action`
- `resolved_by_human_at_ms`

---

## 八、验收判断
Phase 6 的定义项（6.1–6.7）可以视为完成，当且仅当：
- 三类 waiting_human 状态边界清楚
- 回流规则清楚
- `resolved_by_human` / `returned_to_agent_queue` 语义清楚
- Human Queue 卡片字段明确
- 没把 Human Queue 误做成另一套真相系统

---

## 九、下一步自然承接
在完成 6.1–6.7 后，下一步自然进入：
- 6.8 跑通一次人工补信息后回流 agent 的案例
- 6.9 跑通一次人工审批后继续执行的案例

也就是从“定义层”进入“真实业务分支验证层”。
