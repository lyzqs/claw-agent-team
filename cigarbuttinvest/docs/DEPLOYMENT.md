# CigarButtInvest 部署指南

本指南说明如何将 CigarButtInvest 港股烟蒂股筛选系统部署到生产环境。

## 前置要求

- Python 3.10+
- systemd (Linux)
- 网络连接（获取港股数据）

## 依赖安装

```bash
pip install akshare pandas requests
```

## 目录结构

建议的部署目录结构：

```
/opt/cigarbuttinvest/
├── data/           # 数据缓存
├── docs/           # 文档和配置
│   ├── config/     # 配置文件
│   └── daily_runs/ # 运行记录
├── logs/           # 日志
└── cigarbuttinvest/ # 源代码
    ├── engine/
    ├── scheduler/
    ├── reporter/
    └── monitor/
```

## 部署步骤

### 1. 创建部署目录

```bash
sudo mkdir -p /opt/cigarbuttinvest/{data,docs/config,docs/daily_runs,logs}
sudo chown -R $USER:$USER /opt/cigarbuttinvest
```

### 2. 复制代码

```bash
cp -r /root/.openclaw/workspace-agent-team/cigarbuttinvest/* /opt/cigarbuttinvest/
```

### 3. 配置服务

编辑 `/opt/cigarbuttinvest/docs/config/monitor_config.json`：

```json
{
  "alert_thresholds": {
    "max_execution_time_seconds": 600,
    "max_consecutive_failures": 3
  },
  "alert_channels": {
    "log": {"enabled": true},
    "feishu": {
      "enabled": true,
      "webhook_url": "你的飞书机器人 webhook URL"
    }
  }
}
```

### 4. 安装 systemd 服务

```bash
# 复制服务文件
sudo cp /opt/cigarbuttinvest/docs/config/cigarbuttinvest-*.service /etc/systemd/system/
sudo cp /opt/cigarbuttinvest/docs/config/cigarbuttinvest-*.timer /etc/systemd/system/

# 重载 systemd
sudo systemctl daemon-reload

# 启用定时器
sudo systemctl enable cigarbuttinvest-screening.timer
sudo systemctl enable cigarbuttinvest-healthcheck.timer

# 启动
sudo systemctl start cigarbuttinvest-screening.timer
sudo systemctl start cigarbuttinvest-healthcheck.timer

# 检查状态
systemctl list-timers --all | grep cigarbuttinvest
```

### 5. 测试运行

```bash
# 手动运行一次
cd /opt/cigarbuttinvest
python3 -m cigarbuttinvest.main run --dry-run

# 检查日志
tail -f logs/daily_job_*.log
```

## 监控

### 查看服务状态

```bash
systemctl status cigarbuttinvest-screening.service
systemctl status cigarbuttinvest-healthcheck.service
```

### 查看日志

```bash
# 查看筛选任务日志
journalctl -u cigarbuttinvest-screening.service -f

# 查看健康检查日志
journalctl -u cigarbuttinvest-healthcheck.service -f

# 查看应用日志
tail -f /opt/cigarbuttinvest/logs/daily_job_*.log
```

### 触发立即执行

```bash
# 立即执行一次筛选
sudo systemctl start cigarbuttinvest-screening.service

# 立即执行健康检查
sudo systemctl start cigarbuttinvest-healthcheck.service
```

## 故障排除

### 任务未执行

1. 检查定时器状态：`systemctl list-timers --all | grep cigarbuttinvest`
2. 查看日志：`journalctl -u cigarbuttinvest-screening.timer`
3. 手动触发：`sudo systemctl start cigarbuttinvest-screening.service`

### 数据获取失败

1. 检查网络连接
2. 验证 akshare 依赖：`pip show akshare`
3. 查看任务日志中的具体错误

### 报告未生成

1. 检查运行记录：`ls -la /opt/cigarbuttinvest/docs/daily_runs/`
2. 查看是否有错误：`grep ERROR /opt/cigarbuttinvest/logs/daily_job_*.log`

## 更新

### 代码更新

```bash
# 停止服务
sudo systemctl stop cigarbuttinvest-screening.timer
sudo systemctl stop cigarbuttinvest-healthcheck.timer

# 备份当前代码
cp -r /opt/cigarbuttinvest/cigarbuttinvest /opt/cigarbuttinvest/cigarbuttinvest.bak

# 更新代码
cp -r /path/to/new/cigarbuttinvest/* /opt/cigarbuttinvest/

# 重新启动
sudo systemctl start cigarbuttinvest-screening.timer
sudo systemctl start cigarbuttinvest-healthcheck.timer
```

### 配置更新

编辑 `/opt/cigarbuttinvest/docs/config/` 下的配置文件，然后重启服务：

```bash
sudo systemctl restart cigarbuttinvest-screening.service
sudo systemctl restart cigarbuttinvest-healthcheck.service
```

## 卸载

```bash
# 停止并禁用服务
sudo systemctl stop cigarbuttinvest-screening.timer
sudo systemctl stop cigarbuttinvest-healthcheck.timer
sudo systemctl disable cigarbuttinvest-screening.timer
sudo systemctl disable cigarbuttinvest-healthcheck.timer

# 删除服务文件
sudo rm /etc/systemd/system/cigarbuttinvest-*.service
sudo rm /etc/systemd/system/cigarbuttinvest-*.timer

# 重载 systemd
sudo systemctl daemon-reload
sudo systemctl reset-failed

# 删除目录
sudo rm -rf /opt/cigarbuttinvest
```

## 安全建议

1. **最小权限**：使用专用用户运行，避免 root
2. **网络安全**：限制对外访问，仅允许必要的数据源
3. **日志审计**：定期审查日志，监控异常行为
4. **备份**：定期备份配置和运行记录

## 联系支持

如有问题，请查看日志或联系项目维护者。
