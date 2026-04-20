"""Risk Control Event Logger (M9)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("risk_control")


class RiskLogger:
    """风控事件记录器。

    每次风控拦截/告警均记录到 JSON Lines 文件，
    同时输出到标准日志。
    """

    def __init__(self, log_dir: str = ""):
        self.log_dir = Path(log_dir) if log_dir else Path("/root/.openclaw/workspace/quantitativeinvest")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "risk_events.jsonl"

    def log(self, level: str, symbol: str, action: str, reason: str,
            details: Optional[dict] = None) -> None:
        """记录一次风控事件。"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "level": level,        # 'warning' | 'blocked' | 'info'
            "symbol": symbol,
            "action": action,      # 'buy' | 'sell' | 'trade'
            "reason": reason,
            "details": details or {},
        }
        # Write JSON Lines
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Console output
        prefix = "🚨 BLOCKED" if level == "blocked" else "⚠️ WARNING"
        logger.warning(f"{prefix}: [{symbol}] {action.upper()} — {reason} | details={details}")

    def info(self, message: str, details: Optional[dict] = None) -> None:
        """记录信息性事件。"""
        self.log("info", "SYSTEM", "info", message, details)

    def get_events(self, limit: int = 100) -> list[dict]:
        """读取最近的日志事件（倒序）。"""
        if not self.log_file.exists():
            return []
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        events = [json.loads(line) for line in lines[-limit:]]
        events.reverse()
        return events

    def get_alerts(self, since: Optional[str] = None) -> list[dict]:
        """获取告警/拦截事件。"""
        events = self.get_events(limit=10000)
        return [e for e in events if e["level"] in ("warning", "blocked")]
