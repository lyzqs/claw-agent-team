# Grafana 用户账号管理（Issue #2 Dev 交付）

## 目标
提供一个最小可复用的仓库内脚本，用来创建或更新 Grafana 本地用户，并验证该用户可以登录。

## 脚本
- `scripts/ensure_grafana_user.py`

功能：
- 读取 `/etc/systemd/system/grafana-server.service.d/agent-team-grafana.conf` 中的 Grafana 管理员账号、密码和监听端口作为默认值。
- 若目标用户不存在，则创建。
- 无论用户是否已存在，都会重置为指定密码。
- 最后使用目标账号调用 `/api/user` 验证登录成功。

## 用法
```bash
cd /root/.openclaw/workspace-agent-team
python3 scripts/ensure_grafana_user.py \
  --login <user_login> \
  --password '<new_password>'
```

可选参数：
- `--name`
- `--email`
- `--base-url`
- `--admin-user`
- `--admin-password`

## 输出
脚本会输出 JSON，例如：

```json
{
  "status": "ok",
  "action": "created",
  "login": "example",
  "user_id": 2,
  "validated": true,
  "base_url": "http://127.0.0.1:3300"
}
```

## 验证口径
只要脚本输出：
- `status = ok`
- `validated = true`

即可认为该账号已经可登录。
