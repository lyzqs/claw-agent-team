# Phase 4.6 Board 载体决策（第一版）

## 结论
第一版 Board 载体选择：**轻量 Web UI**。

不是 Bitable 优先，原因很直接：

1. **当前已有稳定本地投影产物**
   - SQLite Ledger → `board_snapshot.json`
   - 已能稳定产出 `project_view / agent_queue / human_queue / employee_view`

2. **Board 当前职责是展示，不是真相层**
   - 现阶段最重要的是把投影结果稳定展示出来
   - 不应该为了“先接 Bitable”把展示层和真相层重新耦合

3. **轻量 Web UI 更贴近总清单当前目标**
   - 4.6 要求是“选择第一版载体”，不是一步做到最终运营面板
   - 轻量 Web UI 可以最小成本验证：
     - 视图结构是否清晰
     - 队列划分是否可读
     - 后续是否需要交互

4. **Bitable 更适合作为后续投影目标，而不是第一落点**
   - Bitable 适合共享、筛选、协作查看
   - 但它天然更像外部展示/运营视图
   - 在第一版阶段，应该先把本地展示层跑顺，再决定是否补 Bitable 投影

## 本次决策对应的最小落地
- 保留现有 SQLite ledger 真相层
- 保留 `render_board_snapshot.py` 的单向投影
- 新增一个本地静态 Board 页面，直接渲染当前 snapshot
- 继续保持：**Board 只读，不反写 Ledger**

## 验收判断
4.6 可视为完成，当且仅当：
- 已明确第一版载体为轻量 Web UI
- 有实际可打开/可渲染的最小 Board 页面
- 页面数据来自 ledger snapshot，而不是手写假数据
- 不引入 Board 反写真相层

## 后续自然承接
这个决定为后续两条线留下空间：
- Phase 5/6 继续补 notification adapter / human queue 真实分支
- 之后如果需要共享展示，再加 Bitable 投影
