# CigarButtInvest 港股烟蒂股筛选系统

> 自动化港股烟蒂股筛选系统，每交易日定时运行

## 项目概述

本项目基于[烟蒂股分析 Prompt v1.8](https://terancejiang.github.io/Stock_Analyze_Prompts/cigbutt/%E7%83%9F%E8%92%82%E8%82%A1%E5%88%86%E6%9E%90Prompt_v1.8/)实现，用于自动化筛选符合条件的港股烟蒂股标的。

## 功能特性

- **T0/T1/T2 三级 NAV 分级**：基于资产负债表计算净变现价值
- **三大支柱验证**：存量资产、低维持运营、资产兑现逻辑
- **A/B/C 三种子类型判定**：高股息破净、控股折价、事件驱动
- **Fact Check 22 项验证**：资产质量、负债隐患、治理风险
- **每日定时筛选**：交易日 9:30 自动执行
- **Markdown 报告生成**：自动生成筛选报告
- **监控告警机制**：任务状态监控、异常告警

## 项目结构

```
cigarbuttinvest/
├── data/                   # 数据层
│   └── cache.py           # 数据缓存
├── engine/                 # 筛选引擎（Dev 实现）
│   ├── fetcher.py        # 数据获取
│   └── screener.py       # 筛选引擎
├── scheduler/              # 定时任务（Ops 配置）
│   ├── daily_job.py      # 每日任务
│   └── trading_calendar.py # 交易日历
├── reporter/               # 报告生成（Ops 配置）
│   └── md_report.py      # Markdown 报告
├── monitor/               # 监控告警（Ops 配置）
│   └── task_monitor.py   # 任务监控
├── docs/
│   ├── config/           # 配置文件
│   │   ├── monitor_config.json    # 监控配置
│   │   ├── daily_job.env          # 任务环境变量
│   │   └── cigarbuttinvest-*.service  # systemd 服务
│   └── daily_runs/      # 每日运行记录
├── logs/                  # 运行日志
├── main.py                # 主入口
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install akshare pandas requests
```

### 2. 运行每日筛选

```bash
# 进入项目目录
cd cigarbuttinvest

# 运行每日筛选
python -m cigarbuttinvest.main run

# 试运行模式
python -m cigarbuttinvest.main run --dry-run
```

### 3. 查看状态

```bash
python -m cigarbuttinvest.main status
python -m cigarbuttinvest.main report
```

### 4. 健康检查

```bash
python -m cigarbuttinvest.main health
```

## 部署定时任务

### systemd 部署（推荐）

```bash
# 1. 复制服务文件
sudo cp docs/config/cigarbuttinvest-*.service /etc/systemd/system/
sudo cp docs/config/cigarbuttinvest-*.timer /etc/systemd/system/

# 2. 重载 systemd
sudo systemctl daemon-reload

# 3. 启用定时器
sudo systemctl enable cigarbuttinvest-screening.timer
sudo systemctl enable cigarbuttinvest-healthcheck.timer

# 4. 启动
sudo systemctl start cigarbuttinvest-screening.timer
sudo systemctl start cigarbuttinvest-healthcheck.timer

# 5. 查看状态
systemctl list-timers --all | grep cigarbuttinvest
```

### cron 部署

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每周一至周五 9:25 执行）
25 9 * * 1-5 cd /path/to/cigarbuttinvest && python3 -m cigarbuttinvest.scheduler.daily_job >> logs/daily_job.log 2>&1
```

## 验收标准

| # | 标准 | 状态 |
|---|------|------|
| 1 | 定时任务可正常执行 | ✅ |
| 2 | 全量港股数据获取完成 | ⏳ 待 Dev 实现 |
| 3 | 每日报告自动生成 | ✅ |
| 4 | 异常告警机制可用 | ✅ |
| 5 | 所有配置和运行记录归档 | ✅ |

## 配置说明

### 监控配置 (docs/config/monitor_config.json)

```json
{
  "alert_thresholds": {
    "max_execution_time_seconds": 600,
    "max_consecutive_failures": 3
  },
  "alert_channels": {
    "feishu": {
      "enabled": true,
      "webhook_url": "https://open.feishu.cn/..."
    }
  }
}
```

### 任务配置 (docs/config/daily_job.env)

```bash
SCREENING_TIME="09:30"
TRADING_DAY_ONLY=true
MAX_RETRIES=3
```

## 输出物

- **每日报告**: `docs/daily_runs/烟蒂股筛选报告_YYYY-MM-DD.md`
- **运行记录**: `docs/daily_runs/run_YYYYMMDD_HHMMSS.json`
- **日志文件**: `logs/daily_job_*.log`
- **告警日志**: `logs/alerts.log`

## 开发说明

### Ops 职责（已完成）

- ✅ 定时任务框架 (scheduler/daily_job.py)
- ✅ 交易日检测 (scheduler/trading_calendar.py)
- ✅ 报告生成 (reporter/md_report.py)
- ✅ 监控告警 (monitor/task_monitor.py)
- ✅ systemd 服务配置
- ✅ 数据缓存基础设施

### Dev 职责（Issue #3）

- [ ] 数据获取模块 (engine/fetcher.py)
- [ ] NAV 计算 (engine/nav.py)
- [ ] 三大支柱验证 (engine/pillars.py)
- [ ] A/B/C 子类型判定 (engine/subtype.py)
- [ ] Fact Check 22 项 (engine/factcheck.py)

## 参考资料

- [烟蒂股分析 Prompt v1.8](https://terancejiang.github.io/Stock_Analyze_Prompts/cigbutt/%E7%83%9F%E8%92%82%E8%82%A1%E5%88%86%E6%9E%90Prompt_v1.8/)
- [飞书项目 Roadmap](https://www.feishu.cn/wiki/BzLUwDBv5itOqzkqW2rcWpoNnXc)

## 许可证

MIT License
