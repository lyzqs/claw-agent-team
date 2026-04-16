#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OVERRIDE_PATH = Path("/etc/systemd/system/grafana-server.service.d/agent-team-grafana.conf")


def parse_override_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line.startswith('Environment="') or not line.endswith('"'):
            continue
        payload = line[len('Environment="'):-1]
        if '=' not in payload:
            continue
        key, value = payload.split('=', 1)
        values[key] = value
    return values


def build_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def api_request(
    *,
    base_url: str,
    method: str,
    path: str,
    username: str,
    password: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": build_auth_header(username, password),
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read().decode("utf-8")
            return response.status, json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        data = error.read().decode("utf-8")
        parsed = {}
        if data:
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                parsed = {"raw": data}
        return error.code, parsed


def resolve_defaults() -> tuple[str, str, str]:
    override_env = parse_override_env(OVERRIDE_PATH)
    admin_user = os.environ.get("GRAFANA_ADMIN_USER") or override_env.get("GF_SECURITY_ADMIN_USER") or "admin"
    admin_password = os.environ.get("GRAFANA_ADMIN_PASSWORD") or override_env.get("GF_SECURITY_ADMIN_PASSWORD") or ""
    port = os.environ.get("GRAFANA_HTTP_PORT") or override_env.get("GF_SERVER_HTTP_PORT") or "3000"
    base_url = os.environ.get("GRAFANA_BASE_URL") or f"http://127.0.0.1:{port}"
    return admin_user, admin_password, base_url


def lookup_user(*, base_url: str, admin_user: str, admin_password: str, login: str) -> dict | None:
    status, payload = api_request(
        base_url=base_url,
        method="GET",
        path=f"/api/users/lookup?loginOrEmail={urllib.parse.quote(login)}",
        username=admin_user,
        password=admin_password,
    )
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"lookup user failed: HTTP {status} {payload}")
    return payload


def create_or_update_user(*, base_url: str, admin_user: str, admin_password: str, login: str, password: str, name: str, email: str) -> dict:
    existing_user = lookup_user(
        base_url=base_url,
        admin_user=admin_user,
        admin_password=admin_password,
        login=login,
    )
    if existing_user is None:
        status, payload = api_request(
            base_url=base_url,
            method="POST",
            path="/api/admin/users",
            username=admin_user,
            password=admin_password,
            payload={
                "name": name,
                "email": email,
                "login": login,
                "password": password,
            },
        )
        if status != 200:
            raise RuntimeError(f"create user failed: HTTP {status} {payload}")
        user_id = int(payload["id"])
        action = "created"
    else:
        user_id = int(existing_user["id"])
        action = "updated"

    status, payload = api_request(
        base_url=base_url,
        method="PUT",
        path=f"/api/admin/users/{user_id}/password",
        username=admin_user,
        password=admin_password,
        payload={"password": password},
    )
    if status != 200:
        raise RuntimeError(f"update password failed: HTTP {status} {payload}")

    status, payload = api_request(
        base_url=base_url,
        method="GET",
        path="/api/user",
        username=login,
        password=password,
    )
    if status != 200:
        raise RuntimeError(f"login validation failed: HTTP {status} {payload}")

    return {
        "status": "ok",
        "action": action,
        "login": login,
        "user_id": user_id,
        "validated": True,
        "base_url": base_url,
        "name": payload.get("name"),
        "email": payload.get("email"),
        "is_grafana_admin": payload.get("isGrafanaAdmin"),
    }


def parse_args() -> argparse.Namespace:
    default_admin_user, default_admin_password, default_base_url = resolve_defaults()
    parser = argparse.ArgumentParser(description="Ensure a Grafana user exists and can log in.")
    parser.add_argument("--base-url", default=default_base_url)
    parser.add_argument("--admin-user", default=default_admin_user)
    parser.add_argument("--admin-password", default=default_admin_password)
    parser.add_argument("--login", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name")
    parser.add_argument("--email")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.admin_password:
        raise SystemExit("Missing admin password. Pass --admin-password or set it in the grafana override env file.")

    result = create_or_update_user(
        base_url=args.base_url,
        admin_user=args.admin_user,
        admin_password=args.admin_password,
        login=args.login,
        password=args.password,
        name=args.name or args.login,
        email=args.email or f"{args.login}@local",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
