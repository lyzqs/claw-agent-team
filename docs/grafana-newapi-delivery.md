# NewAPI 指标接入与 Grafana 面板落地（Issue #11 Dev 交付）

## 交付目标
基于 `docs/grafana-newapi-observability-spec.md` 与统一蓝图，把 NewAPI 的核心业务指标、运行指标接入本地 Prometheus / Grafana，并提供最小验证路径。

## 本轮交付物

### 1. 指标桥接 exporter
- `scripts/newapi_metrics_exporter.py`
- `deploy/grafana/systemd/newapi-metrics-exporter.service`

实现方式：
- 直接读取 `/root/new-api/one-api.db`
- 聚合 `logs / channels / top_ups / subscription_orders / options`
- 产出 Prometheus `/metrics`
- 补充 NewAPI 进程级指标：
  - `newapi_process_cpu_percent`
  - `newapi_process_memory_bytes`
  - `newapi_process_open_fds`
- 补充运行健康指标：
  - `newapi_db_connection_health`
  - `newapi_error_log_enabled`
  - `newapi_up`

### 2. Prometheus 抓取接入
- 更新 `deploy/grafana/prometheus/prometheus.yml`
- 新增 `job_name: newapi-exporter`
- 抓取地址：`127.0.0.1:19100`

### 3. Grafana 统一命名与 datasource 收敛
- `deploy/grafana/provisioning/datasources/prometheus.yaml`
  - datasource uid 调整为 `prometheus-local-main`
- `deploy/grafana/provisioning/dashboards/dashboard-provider.yaml`
  - Host-System dashboard 落到 `AT | 10 Platform | Host-System`
  - NewAPI dashboard 落到 `AT | 21 Project | NewAPI`

### 4. NewAPI dashboards
- `deploy/grafana/dashboards/newapi-business-overview.json`
- `deploy/grafana/dashboards/newapi-runtime-channel-health.json`
- `deploy/grafana/dashboards/newapi-runtime-process-dependencies.json`
- 由 `scripts/generate_newapi_grafana_dashboards.py` 生成

命名符合蓝图：
- `AT | NewAPI | Business | Overview`
- `AT | NewAPI | Runtime | Channel Health`
- `AT | NewAPI | Runtime | Process & Dependencies`

### 5. 验证脚本
- `scripts/validate_grafana_bundle.py`
- `scripts/validate_newapi_observability.py`

用途：
- 静态校验 Grafana bundle
- 校验 exporter 是否暴露了预期 `newapi_*` 指标
- 校验 Grafana 搜索结果里能发现 NewAPI dashboards

## 覆盖到的规格指标
本轮已覆盖或桥接以下核心指标：
- `newapi_requests_total`
- `newapi_request_success_total`
- `newapi_request_error_total`
- `newapi_channel_error_rate`
- `newapi_tokens_consumed_total`
- `newapi_quota_consumed_total`
- `newapi_rpm`
- `newapi_tpm`
- `newapi_requests_by_model_total`
- `newapi_errors_by_error_code_total`
- `newapi_channel_health_score`
- `newapi_topup_events_total`
- `newapi_subscription_events_total`
- `newapi_process_cpu_percent`
- `newapi_process_memory_bytes`
- `newapi_process_open_fds`
- `newapi_db_connection_health`
- `newapi_error_log_enabled`
- 以及渠道状态 / 响应时间 / 已用额度 / 余额补充指标

## 安装 / 更新方式
如果本机已有 Grafana 主栈，重新执行安装脚本即可下发最新配置：

```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/generate_newapi_grafana_dashboards.py
sudo ./deploy/grafana/install_local_grafana_stack.sh \
  --public-host <your-public-host> \
  --grafana-http-port 3300 \
  --grafana-admin-password '<grafana-admin-password>'
```

脚本会：
- 安装并启动 `newapi-metrics-exporter.service`
- 更新 Prometheus 抓取配置
- 同步 NewAPI dashboards 到 Grafana provisioning 目录
- 触发健康检查

## 最小验证

### 1. 静态校验
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 2. 运行态校验
```bash
python3 scripts/validate_newapi_observability.py
```

### 3. 手工检查建议
- 打开 Grafana
- 确认 folder `AT | 21 Project | NewAPI` 已存在
- 确认 3 个 NewAPI dashboards 可见
- 确认面板中出现：
  - 请求量 / 成功率 / 错误率
  - Token / Quota 消耗
  - 渠道错误率与健康分
  - NewAPI 进程 CPU / 内存 / FDs

## 角色边界判断
本轮 Dev 已完成：
- 指标桥接实现
- Prometheus 接入
- Grafana dashboards 定义
- 最小验证脚本

后续建议：
- 由 QA 基于实际页面与指标值做验收
- 若需要外网重新发布 / 服务重载策略确认，再由 Ops 配合处理
