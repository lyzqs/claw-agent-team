# Uptime Kuma 指标接入与 Grafana 面板落地（Issue #15 Dev 交付）

## 交付目标
基于 `docs/grafana-uptime-kuma-observability-spec.md`，把 Uptime Kuma 的关键合成监控指标桥接到本地 Prometheus / Grafana，并提供最小验证证据。

## 本轮交付物

### 1. Uptime Kuma exporter
- `scripts/uptime_kuma_metrics_exporter.py`
- `deploy/grafana/systemd/uptime-kuma-metrics-exporter.service`

实现方式：
- 直接读取 `/opt/uptime-kuma/data/kuma.db`
- 复用 `monitor / heartbeat / monitor_group / notification / domain_expiry` 数据
- 输出分组、监控项、响应时间、失败/恢复、重试策略、证书剩余天数等 Prometheus 指标
- 额外补充 Kuma 自身运行指标：
  - `kuma_process_cpu_percent`
  - `kuma_process_memory_bytes`
  - `kuma_proxy_health`
  - `kuma_socket_polling_health`

### 2. Prometheus 抓取接入
- 更新 `deploy/grafana/prometheus/prometheus.yml`
- 新增 `job_name: uptime-kuma-exporter`
- 抓取地址：`127.0.0.1:19120`

### 3. Grafana dashboards
- `deploy/grafana/dashboards/uptime-kuma-synthetic-overview.json`
- `deploy/grafana/dashboards/uptime-kuma-synthetic-group-health.json`
- `deploy/grafana/dashboards/uptime-kuma-synthetic-monitor-details.json`
- 由 `scripts/generate_uptime_kuma_grafana_dashboards.py` 生成

命名符合蓝图：
- `AT | Uptime-Kuma | Synthetic | Overview`
- `AT | Uptime-Kuma | Synthetic | Group Health`
- `AT | Uptime-Kuma | Synthetic | Monitor Details`

Folder：
- `AT | 30 Ops | Uptime-Kuma`

### 4. Grafana / provisioning 收敛
- `deploy/grafana/provisioning/dashboards/dashboard-provider.yaml`
  - 新增 Uptime Kuma folder provider
- `deploy/grafana/install_local_grafana_stack.sh`
  - 新增 uptime-kuma dashboard 下发与 exporter 服务安装

### 5. 验证脚本
- `scripts/validate_grafana_bundle.py`
- `scripts/validate_uptime_kuma_observability.py`

用途：
- 校验 Grafana bundle 中是否包含 Uptime Kuma dashboard / folder / systemd / Prometheus scrape job
- 校验 exporter 是否暴露了预期 `kuma_*` 指标
- 校验 Grafana 搜索结果里能发现 Uptime Kuma dashboards

## 覆盖到的规格指标
本轮已覆盖或桥接以下核心指标：
- `kuma_monitors_total`
- `kuma_monitor_status`
- `kuma_monitors_up_total`
- `kuma_monitors_down_total`
- `kuma_monitor_response_time_ms`
- `kuma_group_availability_ratio`
- `kuma_monitor_retry_policy`
- `kuma_group_alerting_scope`
- `kuma_monitor_failures_total`
- `kuma_monitor_recoveries_total`
- `kuma_group_avg_response_time_ms`
- `kuma_cert_expiry_days`
- `kuma_monitor_flap_score`
- `kuma_process_cpu_percent`
- `kuma_process_memory_bytes`
- `kuma_proxy_health`
- `kuma_socket_polling_health`

## 最小验证

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 运行态校验
```bash
python3 scripts/validate_uptime_kuma_observability.py
```

### 3. 手工检查建议
- 打开 Grafana
- 确认 folder `AT | 30 Ops | Uptime-Kuma` 已存在
- 确认 3 个 Uptime Kuma dashboards 可见
- 确认能够回答：
  - 当前哪些监控 down
  - 哪些组最不稳定
  - 哪些监控在抖
  - 哪些证书接近到期

## 角色边界判断
本轮 Dev 已完成：
- Uptime Kuma 指标桥接实现
- Prometheus 接入
- Grafana dashboard 定义
- 最小验证脚本

后续建议：
- 由 QA 以页面与指标内容为主做验收
- 若需要通过 nginx / 外网统一暴露 Grafana，则由 Ops 负责环境收尾
