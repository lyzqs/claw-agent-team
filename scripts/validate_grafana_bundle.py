#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "deploy" / "grafana"


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def validate_dashboard(path: Path) -> dict:
    payload = json.loads(path.read_text())
    panels = payload.get("panels", [])
    titles = {panel.get("title") for panel in panels}
    required_titles = {
        "总 CPU 使用率",
        "内存使用率",
        "已用内存",
        "运行中进程数",
        "CPU 使用率趋势",
        "内存使用率趋势",
        "Top 10 进程 CPU 占用",
        "Top 10 进程驻留内存",
    }
    if not required_titles.issubset(titles):
        missing = sorted(required_titles - titles)
        raise ValueError(f"dashboard missing panels: {missing}")

    expressions = [target.get("expr", "") for panel in panels for target in panel.get("targets", [])]
    required_snippets = [
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "namedprocess_namegroup_cpu_seconds_total",
        "namedprocess_namegroup_memory_bytes",
        "topk(10",
    ]
    for snippet in required_snippets:
        if not any(snippet in expression for expression in expressions):
            raise ValueError(f"dashboard missing query snippet: {snippet}")

    for panel in panels:
        thresholds = panel.get("fieldConfig", {}).get("defaults", {}).get("thresholds")
        if thresholds is None:
            continue
        if not isinstance(thresholds, dict):
            raise ValueError(f"panel thresholds must be an object: {panel.get('title')}")
        steps = thresholds.get("steps")
        if not isinstance(steps, list):
            raise ValueError(f"panel thresholds.steps must be a list: {panel.get('title')}")

    return {
        "title": payload.get("title"),
        "panel_count": len(panels),
        "uid": payload.get("uid"),
    }


def validate_nginx_template(path: Path) -> dict:
    rendered = (
        path.read_text()
        .replace("__PUBLIC_HOST__", "grafana.example.test")
        .replace("__GRAFANA_HTTP_PORT__", "3300")
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        site_conf = temp_root / "site.conf"
        site_conf.write_text(rendered)
        root_conf = temp_root / "nginx.conf"
        root_conf.write_text(
            "events {}\n"
            "http {\n"
            f"  include {site_conf};\n"
            "}\n"
        )
        subprocess.run(
            ["nginx", "-t", "-c", str(root_conf), "-p", temp_dir],
            check=True,
            capture_output=True,
            text=True,
        )
    return {"syntax": "ok"}


def main() -> None:
    report = {
        "bundle_root": str(BUNDLE_ROOT),
        "yaml_files": {},
        "dashboard": {},
        "nginx_template": {},
        "systemd_units": {},
        "status": "ok",
    }

    yaml_paths = [
        BUNDLE_ROOT / "process-exporter" / "process-exporter.yml",
        BUNDLE_ROOT / "prometheus" / "prometheus.yml",
        BUNDLE_ROOT / "provisioning" / "datasources" / "prometheus.yaml",
        BUNDLE_ROOT / "provisioning" / "dashboards" / "dashboard-provider.yaml",
    ]
    for path in yaml_paths:
        payload = load_yaml(path)
        report["yaml_files"][str(path.relative_to(REPO_ROOT))] = {"loaded": payload is not None}

    report["dashboard"] = validate_dashboard(BUNDLE_ROOT / "dashboards" / "local-host-observability.json")
    report["nginx_template"] = validate_nginx_template(BUNDLE_ROOT / "nginx" / "grafana-http.conf.template")

    for unit_path in [
        BUNDLE_ROOT / "systemd" / "process-exporter.service",
        BUNDLE_ROOT / "systemd" / "agent-team-prometheus.service",
    ]:
        text = unit_path.read_text()
        if "ExecStart=" not in text:
            raise ValueError(f"systemd unit missing ExecStart: {unit_path}")
        report["systemd_units"][str(unit_path.relative_to(REPO_ROOT))] = {"execstart": "ok"}

    grafana_override = (BUNDLE_ROOT / "grafana" / "grafana-server.override.conf.template").read_text()
    if "__GRAFANA_HTTP_PORT__" not in grafana_override:
        raise ValueError("grafana override template missing __GRAFANA_HTTP_PORT__ placeholder")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
