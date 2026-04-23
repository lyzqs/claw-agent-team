# Issue #147 - Gateway 服务可靠性提升

## 状态
- **状态**: Dev 完成 ✅
- **完成时间**: 2026-04-23

## 背景
Issue #144 QA 调查发现 3.4% Gateway exit 失败率中有 4 例源于基础设施问题（3x timeout + 1x Connection refused）。

## PM 实现规格（飞书文档 XzGUdupvjooIykxmKWIcIEeEnzd）

AC1：Gateway timeout 场景增加重试机制
AC2：Gateway 服务不可用时有提前告警（非阻塞）
AC3：基础设施问题导致的 exit 空结果减少

关键约束：总延迟不超过 5s、异常必须记录日志。

## 实现方案

### AC1：Gateway timeout 重试机制
- `request_json_with_retry` 增加 `delay_sequence` 参数，支持自定义重试延迟序列
- `request_gateway_exit_decision` 使用 `[0.5, 1.0]` 延迟序列（0.5s 后重试，1s 后再次重试）

### AC2：非阻塞健康探测
- 新增 `_do_health_probe()`：对 `/health` 端点发 2s timeout 请求
- 若响应慢或失败，记录 `gateway-health-warn` 事件到日志
- 在 `request_gateway_exit_decision` 入口通过 daemon thread 异步调用，不阻塞主流程

### AC3：连续失败提前 fallback
- 在 `autopilot_state` 中追踪 `gatewayExitConsecutiveFailures` 计数器
- 成功时重置为 0，失败时 +1
- 若 ≥2 次连续失败：`delay_sequence=[0.001]`（几乎无延迟），快速 fallback
- 结合 AC1 重试机制，总延迟不超过 5s

## 验收标准检查
| # | 标准 | 状态 |
|---|------|------|
| 1 | Gateway timeout 场景增加重试机制 | ✅ |
| 2 | Gateway 服务不可用时有提前告警 | ✅ |
| 3 | 基础设施问题导致的 exit 空结果减少 | ✅ |

## 关键代码
- `arena/scripts/arena_runtime.py`:
  - `request_json_with_retry`: delay_sequence 参数支持
  - `_do_health_probe`: 非阻塞健康探测
  - `request_gateway_exit_decision`: AC1/AC2/AC3 全量实现

## commit
- `6e05efe arena Issue #147: add AC1/AC2/AC3 gateway reliability improvements`
