# Grafana 本地资源观测栈（Issue #1 Dev 交付）

## 目标
为 `agent-team-grafana` 项目提供一套最小可落地的本地观测方案，满足：

1. 本地有 Grafana 看板。
2. 能看到宿主机总 CPU、内存、百分比等资源指标。
3. 能下钻到进程维度，看到哪些进程占用 CPU / 内存最高。
4. 通过 nginx 做 HTTP 反向代理，对外可访问。

## 本次 Dev 交付范围
本轮只做 Dev 角色最小必要工作，不直接替 Ops 在目标机器做最终部署。

已提供：

- `deploy/grafana/install_local_grafana_stack.sh`
  - Ubuntu 主机一键安装脚本。
  - 安装 Grafana、Prometheus、node exporter、process-exporter。
  - 下发 nginx 站点配置、Grafana provisioning、systemd unit。
- `deploy/grafana/dashboards/local-host-observability.json`
  - 预置看板，覆盖整机资源 + Top 进程。
- `deploy/grafana/prometheus/prometheus.yml`
  - 独立 Prometheus 抓取配置，监听 `127.0.0.1:19090`，避免与系统已有 Prometheus 端口冲突。
- `deploy/grafana/process-exporter/process-exporter.yml`
  - 以进程名分组输出进程级 Prometheus 指标。
- `deploy/grafana/nginx/grafana-http.conf.template`
  - nginx 反向代理模板，外部 HTTP -> 本机 Grafana `127.0.0.1:3000`。
- `deploy/grafana/grafana/grafana-server.override.conf.template`
  - Grafana root_url / admin 凭据 / 仅本地监听覆盖。
- `scripts/validate_grafana_bundle.py`
  - 对配置与看板做静态校验。

## 组件选择

### 1. Grafana
负责看板展示。

### 2. Prometheus
负责抓取并存储指标。本方案使用独立实例：

- 地址：`127.0.0.1:19090`
- 原因：避免与主机可能已有的 `9090` Prometheus 冲突。

### 3. node exporter
负责宿主机总量指标，例如：

- CPU 使用率
- 内存使用率
- 运行中进程数

### 4. process-exporter
负责进程维度指标，例如：

- `namedprocess_namegroup_cpu_seconds_total`
- `namedprocess_namegroup_memory_bytes`

当前按 `{{.Comm}}` 分组，能直接看到按进程名聚合后的 Top CPU / Top 内存。

## 看板内容
当前预置看板 `Local Host Resource & Top Processes` 包含：

- 总 CPU 使用率
- 内存使用率
- 已用内存
- 运行中进程数
- CPU 使用率趋势
- 内存使用率趋势
- Top 10 进程 CPU 占用
- Top 10 进程驻留内存

这已经覆盖题目里要求的“总 cpu，内存，百分比”和“哪个进程占用最多，占了多少”。

## 安装方式（给 Ops）

```bash
cd /root/.openclaw/workspace-agent-team
sudo ./deploy/grafana/install_local_grafana_stack.sh   --public-host grafana.example.com   --grafana-admin-password '请替换成强密码'
```

如果目标机器的 `127.0.0.1:3000` 已被其他服务占用，可以改用其他本地端口，例如：

```bash
cd /root/.openclaw/workspace-agent-team
sudo ./deploy/grafana/install_local_grafana_stack.sh \
  --public-host grafana.example.com \
  --grafana-http-port 3300 \
  --grafana-admin-password '请替换成强密码'
```

脚本会：

1. 安装 Grafana / Prometheus / node exporter / nginx。
2. 下载并安装 process-exporter。
3. 写入 provisioning、dashboard、systemd、nginx 配置。
4. 启动并校验：
   - `process-exporter` on `127.0.0.1:9256`
   - `agent-team-prometheus` on `127.0.0.1:19090`
   - `grafana-server` on `127.0.0.1:3000`（或 `--grafana-http-port` 指定的端口）
   - `nginx` on `:80`

## 最小验证

### 静态验证
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/validate_grafana_bundle.py
```

### 部署后验证
```bash
curl -fsS http://127.0.0.1:9256/metrics | head
curl -fsS http://127.0.0.1:19090/-/ready
curl -fsS http://127.0.0.1:3000/api/health   # 如有端口覆盖，替换成对应端口
curl -fsS -H 'Host: grafana.example.com' http://127.0.0.1/
```

## 角色边界说明
这轮工作里，Dev 已经完成：

- 方案实现
- 配置落盘
- 安装脚本
- 看板定义
- 静态校验脚本

但以下事项属于 Ops 收尾：

- 在目标机器上执行安装
- 检查 DNS / 外网路由 / 防火墙
- 实际暴露 HTTP 服务
- 确认服务开机自启与运行态

因此本 issue 在 Dev 完成后，建议流转给 **Ops** 落地环境部署，再交 **QA** 做验收。
