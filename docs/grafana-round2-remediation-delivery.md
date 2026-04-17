# Grafana 第二轮共性整改交付（Issue #33 Dev）

## 交付目标
基于 `docs/grafana-round2-human-readable-gap-audit.md`，对现有 Grafana 看板做跨项目的统一整改，聚焦：

- 趋势型问题优先使用 timeseries 表达
- 对 lingering `No Data` 面板做明确修复或 0 值兜底
- 删除或替换普通用户难以理解的工程化表达
- 统一标题、图例、tooltip 与页面层级的人类可读性风格

本 issue 不承担项目级新增关键业务指标建设，这些已分别拆到 #34 / #35 / #36；本轮只处理跨看板的共性问题。

## 本轮实际整改范围

### 1. OpenClaw 共性整改
涉及文件：
- `scripts/generate_openclaw_grafana_dashboards.py`
- `deploy/grafana/dashboards/openclaw-runtime-overview.json`
- `deploy/grafana/dashboards/openclaw-usage-model-message-flow.json`
- `deploy/grafana/dashboards/openclaw-queue-sessions-channels.json`

已完成：
- 将 `Queue Enqueue 速率` / `Queue Dequeue 速率` 标题收敛为 `入队速率` / `出队速率`
- 将 `Queue Enqueue / Dequeue 趋势` 改为 `入队 / 出队趋势`
- 将 `Queue Depth 按 Lane` / `Queue Wait 按 Lane` 从难读的 bargauge 快照改为两块 timeseries：
  - `各处理通道队列积压趋势`
  - `各处理通道等待时间趋势`
- 将 `消息结果分布` 改为 `按结果看消息量变化` timeseries
- 将 `Webhook 接收量分布` 改为 `Webhook 接收总量趋势` timeseries，并在 query 上补 `or vector(0)`，避免低事件环境下继续出现误导性空态
- 将 `Webhook 错误数（24h）` stat 增加 `or vector(0)`，把“当前无错误事件”稳定呈现为 0
- 将 `Provider / Model Token 分布` / `Provider / Model 成本分布` 标题改为：
  - `当前 Token 最高的 Provider / Model`
  - `当前成本最高的 Provider / Model`
  使其更像排行榜补充视图，而不是主结论面板

### 2. Agent Team 共性整改
涉及文件：
- `scripts/generate_agent_team_grafana_dashboards.py`
- `deploy/grafana/dashboards/agent-team-workflow-flow-health.json`

已完成：
- 将 `完成模式分布` 改为 `完成模式变化` timeseries
- 将 `人工处理结果分布` 改为 `人工处理结果变化` timeseries
- `恢复 / 协调事件` 开启 table 图例与 multi tooltip，增强多序列横向比较能力
- 保留 `失败码分布` 作为次级排障视图，但不再让 `completion_mode` / `resolution` 继续以首页快照分布形式占据主视图核心位置

## 本轮显式不做的事
- 不重复建设 #34 OpenClaw agent 维度趋势
- 不重复建设 #35 NewAPI channel 维度 token 趋势
- 不重复建设 #36 Agent Team project 维度 issue 趋势
- 不对主机系统做大范围风格重构，只保留其作为合理的 TopN 排行视图
- 不无差别删除所有 bargauge，而是把它们收敛到“当前时点排行/补充排障”语义下使用

## 验证证据

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 面板级显式核对
本轮 Dev 已核对生成后的 JSON，确认以下共性整改已落地：

#### OpenClaw Queue / Sessions / Channels
- `各处理通道队列积压趋势` -> timeseries
- `各处理通道等待时间趋势` -> timeseries
- `入队 / 出队趋势` -> timeseries
- `近24h 各渠道处理结果` -> bargauge（保留为次级排行）

#### OpenClaw Usage
- `按结果看消息量变化` -> timeseries
- `Webhook 接收总量趋势` -> timeseries
- `Webhook 错误数（24h）` -> stat + `or vector(0)`
- `当前 Token 最高的 Provider / Model` -> bargauge（补充排行）
- `当前成本最高的 Provider / Model` -> bargauge（补充排行）

#### Agent Team Workflow
- `完成模式变化` -> timeseries
- `人工处理结果变化` -> timeseries
- `恢复 / 协调事件` -> timeseries + table legend + multi tooltip
- `失败码分布` -> bargauge（保留为次级排障视图）

## 角色边界判断
本轮 Dev 已完成：
- 第二轮共性整改中属于跨看板的人类可读性修复
- 趋势化表达替换
- 0 值兜底修复，避免核心面板 lingering `No Data`
- 难懂工程维度命名收敛
- 最小静态验证与面板级显式核对

后续建议：
- 由 QA 基于实际 Grafana 页面确认这些共性整改是否真正减少误导面板、提升阅读直觉
- 若某些环境仍有长期空态，再由 QA/Ops 根据真实运行环境继续判定是“应删除”还是“应补链路”
