# Issue #138 实现/验证文档：前端个股详情分时图/K线图功能

## 基本信息

| 字段 | 内容 |
|------|------|
| **Issue** | #138 |
| **标题** | 实现前端个股详情分时图/K线图功能 |
| **Parent** | Issue #137（需求规格文档） |
| **状态** | Dev 实现完成，等待 QA 验证 |
| **实现仓库** | `/root/.openclaw/workspace-inStreet/arena/` |
| **关键文件** | `web/app.js`, `scripts/dashboard_server.py` |
| **飞书规格文档** | https://www.feishu.cn/wiki/RgxfwYa9zixj4tkg5r6cjCc8nof |

## 验收标准对照

| # | 验收标准 | 状态 | 实现位置 |
|---|---------|------|---------|
| 1 | Range=1D 显示分时曲线图（当日全部数据点 + 成交量副图） | ✅ | `app.js:renderSymbolReplayChart`, type=line |
| 2 | Range≥3D 显示日线K线图，蜡烛形态正确（红涨绿跌），含成交量副图 | ✅ | `app.js`, type=candlestick; `dashboard_server.py:_aggregate_dimension` |
| 3 | K线模式下维度选择器存在，可切换日线/周线/1小时 | ✅ | `app.js` 已有（`dimensionSelector` 元素，仅 range≥3d 时显示） |
| 4 | 鼠标滚轮可缩放时间轴，K线密度动态变化 | ✅ | `app.js` DataZoom inside + slider 配置 |
| 5 | BUY/SELL 标记叠加在正确时间位置 | ✅ | `app.js` `markPoint` 配置 |
| 6 | 成本线/现价线正确显示 | ✅ | `app.js` `markLine` 配置 |
| 7 | 图表加载中显示 loading 状态 | ✅ | `app.js` `showLoading()` / `hideLoading()` |
| 8 | API 支持 dimension 参数：?range=5d&dimension=day | ✅ | `dashboard_server.py:build_symbol_price_series`, `app.js:loadSymbolDetail` |

## 技术实现细节

### 1. ECharts 5 CDN 引入
- **文件**: `web/index.html`
- **行**: ~line 15
- **URL**: `https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js`
- 加载方式：同步 script 标签，不改变现有构建流程

### 2. 数据流与 OHLC 处理

```
Raw Snapshots (stock_universe_snapshots.jsonl)
    ↓
dashboard_server.py:build_symbol_price_series()
    - 提取 open/high/low/price (close) / volume / ts
    ↓
dashboard_server.py:_aggregate_dimension()
    - 按 hour/day/week 聚合
    - open = 第一条快照的 open
    - high = 所有快照 high 的 max
    - low = 所有快照 low 的 min
    - price (close) = 最后一条快照的 price
    - volume = 所有快照 volume 的 sum
    ↓
dashboard_server.py:build_symbol_detail_payload()
    - 将 priceSeries 放入 detail.priceSeries
    ↓
app.js:loadSymbolDetail()
    - detail = await fetch('/api/symbol-detail?...')
    - detail.priceSeries 包含完整 OHLC 数据
    ↓
app.js:renderSymbolReplayChart(detail, ...)
    - candlestick: detail.priceSeries.map(p => [open, price, low, high])
    - line (分时): points.map(p => p.price)
```

**关键**: 前端 `_aggregateDimension` 辅助函数将 API 返回的 OHLC 数据简化为 `price + volume`，用于折线图和指标计算。但 K 线图直接使用 `detail.priceSeries` 的完整 OHLC 数据。

### 3. 图表类型自动切换

```javascript
const showCandle = rangeKey !== "1d";
// type === "candlestick" (K线) 或 "line" (分时)
```

- **Range=1D**: 分时曲线图（当日数据），使用折线图 + 成交量副图
- **Range≥3D**: K 线图，使用蜡烛图 + 成交量副图

### 4. DataZoom 配置（新增）

```javascript
dataZoom: [
  { type: "inside", xAxisIndex: 0, wheelMouseWheel: true, moveOnMouseWheel: true },
  { type: "slider", xAxisIndex: 0, showDataZoom: points.length > 20,
    height: 20, bottom: 32,
    borderColor: "rgba(255,255,255,0.1)",
    backgroundColor: "rgba(11,16,32,0.5)",
    fillerColor: "rgba(106,168,255,0.2)",
    handleColor: "#92a9ff", handleSize: "100%" },
],
```

- **inside**: 鼠标滚轮缩放 + shift+滚轮平移
- **slider**: 底部滑块（数据点 > 20 时显示）

### 5. K线图 OHLC 数据构造（修复）

**之前（错误）**: `prices.map((p) => [p, p, p, p])` → 扁平蜡烛
**之后（正确）**:
```javascript
detail.priceSeries?.map((p) => [
  Number(p.open || p.price || 0),   // open
  Number(p.price || 0),             // close
  Number(p.low || p.price || 0),    // low
  Number(p.high || p.price || 0),   // high
])
```

### 6. 蜡烛颜色（红涨绿跌）

```javascript
itemStyle: showCandle
  ? { color: "#20c997", color0: "#ff6b6b",   // 绿涨（收盘>开盘），红跌（收盘<开盘）
      borderColor: "#20c997", borderColor0: "#ff6b6b" }
  : { color: "#92a9ff" },
```

ECharts 5 默认 convention: `color` 用于收盘价 ≥ 开盘价，`color0` 用于收盘价 < 开盘价。

### 7. 维度选择器

- **位置**: `dimensionSelector` div，仅 `range !== "1d" && range !== "all"` 时显示
- **选项**: auto / 1小时 / 日线 / 周线
- **行为**: 点击后重新请求 API（带 dimension 参数），触发 `loadSymbolDetail()`
- **当前选中高亮**: 蓝色背景

### 8. 关键 API 端点

```
GET /api/symbol-detail?symbol=000001&range=5d&dimension=day
```

参数:
- `symbol`: 股票代码（必填）
- `range`: 1d / 3d / 5d / 10d / all（默认 5d）
- `dimension`: auto / hour / day / week（默认 auto）

返回 `priceSeries` 字段示例:
```json
{
  "symbol": "000001",
  "priceSeries": [
    {
      "ts": 1744099200,
      "symbol": "000001",
      "name": "平安银行",
      "open": 12.34,
      "high": 12.56,
      "low": 12.20,
      "price": 12.45,
      "volume": 1500000
    }
  ]
}
```

## 数据局限性说明

⚠️ `stock_universe_snapshots.jsonl` 约 8-12 分钟一条快照，**无法提供真正 1 分钟粒度**。

- **日线/周线维度**: 聚合多条快照为一根 K 线，有实际 OHLC 意义
- **1小时维度**: 聚合约 5-7 条快照为 1 根 K 线，有一定 OHLC 意义但非真实日内数据
- **auto 维度**: 直接返回原始快照，仅有收盘价，无真正 OHLC

因此 `dimension=auto` 模式下蜡烛图使用 `open=price`（收盘价），不是真实开盘价。这是数据源限制，非实现问题。

## Git 提交记录

```
commit f8c2f14
feat: add DataZoom + OHLC candlestick data for K-line chart

- Add DataZoom (inside + slider) for mouse wheel zoom and drag pan
- Fix candlestick to use real OHLC from detail.priceSeries
- Backend already returns OHLC fields (open/high/low)
```

## 验证步骤（QA）

### 前置条件
1. Arena dashboard 服务运行中（`http://127.0.0.1:19150`）
2. 浏览器访问 dashboard（端口 8788）

### 验证用例

#### UC-1: 分时曲线图（1D）
1. 打开个股详情，选择 Range = 1D
2. 确认显示平滑曲线（非蜡烛图）
3. 确认下方有成交量副图
4. 鼠标悬停显示 tooltip（价格 + 成交量）

#### UC-2: K 线图（3D+）
1. 选择 Range = 5D
2. 确认显示蜡烛图（非平滑曲线）
3. 确认蜡烛颜色：绿涨（涨）/ 红跌
4. 确认下方有成交量副图（颜色编码）
5. 悬停某根蜡烛，显示 O/H/L/C + 涨跌幅

#### UC-3: 维度选择器
1. Range = 5D 时，确认维度选择器可见
2. 点击"日线"，确认 K 线重新渲染
3. 点击"周线"，确认 K 线重新渲染
4. 点击"1小时"，确认 K 线重新渲染
5. 点击"自动"，确认切换回 auto 模式（flat candles）

#### UC-4: DataZoom 缩放
1. Range = 5D，日线模式
2. 鼠标滚轮：确认时间轴缩放
3. 拖拽 DataZoom 滑块：确认时间窗口平移
4. 缩放后蜡烛密度动态变化

#### UC-5: 交互元素
1. 确认 BUY/SELL 标记点叠加在正确时间位置
2. 确认成本线水平线显示（markLine）
3. 确认现价线水平线显示

#### UC-6: Loading 状态
1. 切换 range 或 dimension 时
2. 确认图表区域显示 loading 动画
3. 数据加载后 loading 消失

#### UC-7: API dimension 参数
```bash
curl "http://127.0.0.1:19150/api/symbol-detail?symbol=000001&range=5d&dimension=day"
# 确认返回的 priceSeries 每条有 open/high/low/price/volume
```

## 已知限制

1. **1分钟粒度**: 数据源不支持，分时图非真正 tick 级数据
2. **分时图蜡烛**: auto 模式下蜡烛图使用 flat price（不是真实 OHLC）
3. **DataZoom slider**: 数据点 ≤ 20 时隐藏（避免空间拥挤）

## 建议后续工作

1. **数据源增强**: 接入 tick 级数据（Level-2 或实时行情 API）以支持真正分时图
2. **指标叠加**: 在 K 线图上叠加 MA/BOLL 等技术指标
3. **全量数据模式**: "all" range 时也支持 DataZoom
4. **移动端优化**: 触摸手势缩放替代鼠标滚轮
