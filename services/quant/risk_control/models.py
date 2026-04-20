"""Risk Control Data Models (M9)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(Enum):
    """风控等级。"""
    PASS = "pass"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class RiskResult:
    """风控检查结果。"""
    allowed: bool
    reason: str = ""
    risk_level: str = "pass"   # 'pass' | 'warning' | 'blocked'
    # 各维度明细（可选）
    position_pct: float = 0.0    # 当前持仓占总资产比例
    drawdown_pct: float = 0.0    # 当前回撤比例
    stop_loss_triggered: bool = False
    single_trade_pct: float = 0.0


@dataclass
class RiskConfig:
    """风控配置参数。"""
    # --- 仓位限制 ---
    max_position_pct: float = 0.20       # 单股最大仓位占总资产比例
    max_total_position_pct: float = 0.80   # 所有持仓最大占总资产比例

    # --- 单笔交易限制 ---
    max_single_trade_pct: float = 0.10  # 单笔交易最大占总资产比例

    # --- 最大回撤 ---
    max_drawdown_pct: float = 0.20      # 最大回撤容忍度（超过此值触发告警/拦截）

    # --- 止损策略 ---
    stop_loss_pct: float = -0.10        # 固定止损：从入场价跌 X% 则触发止损
    trailing_stop_pct: float = 0.0       # 移动止损激活差值（0 表示不启用）
    trailing_stop_lock_pct: float = 0.0  # 移动止损锁定利润比例

    # --- 交易频率 ---
    max_trades_per_day: int = 10
    min_trade_interval_minutes: int = 5

    # --- 日志路径 ---
    risk_log_path: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        """从字典构造（兼容外部 JSON 配置）。"""
        known = {
            "max_position_pct", "max_total_position_pct",
            "max_single_trade_pct", "max_drawdown_pct",
            "stop_loss_pct", "trailing_stop_pct", "trailing_stop_lock_pct",
            "max_trades_per_day", "min_trade_interval_minutes",
            "risk_log_path",
        }
        return cls(**{k: v for k, v in d.items() if k in known})
