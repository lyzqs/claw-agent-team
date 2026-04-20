"""Paper Trading PaperRisk Control Module (M10 - 模拟交易接口风控层).

Basic risk management for paper trading:
- Position size limits
- Single trade size limits
- Stop-loss thresholds
- Daily trade count limits
- Duplicate trade prevention
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from services.quant.config import RISK_CONFIG


@dataclass
class RiskConfig:
    """风控配置参数。"""
    # Position limits
    max_position_pct: float = 0.3          # 单只标的最多占总资产 30%
    max_single_trade_pct: float = 0.1    # 单笔交易最多占总资产 10%
    max_total_position_pct: float = 0.8    # 所有持仓最多占总资产 80%

    # Stop-loss
    stop_loss_pct: float = -0.10          # 止损线: 从买入价跌10%则不允许加仓/建议止损
    max_loss_per_trade_pct: float = -0.05 # 单笔最大亏损: 超过5%则强制止损

    # Trade frequency
    max_trades_per_day: int = 10           # 单日最大交易次数
    min_trade_interval_minutes: int = 5   # 最小交易间隔(分钟)

    # Risk mode
    dry_run: bool = False                  # True=只记录不执行

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})


@dataclass
class RiskResult:
    """风控检查结果。"""
    allowed: bool
    reason: str = ""
    risk_level: str = "normal"  # normal | warning | blocked
    adjustments: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.allowed


class PaperRisk:
    """Paper trading risk control engine.

    在执行每笔交易前调用 check_trade() 进行风控检查。
    检查通过返回 RiskResult(allowed=True)，否则返回 allowed=False。
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        data_dir: str = "/root/.openclaw/workspace/btc-quant",
    ):
        if config is None:
            config = RiskConfig.from_dict(RISK_CONFIG)
        self.config = config
        self.data_dir = Path(data_dir)
        self._today_trades: list[datetime] = []

    def load_state(self) -> dict:
        """加载当前组合状态。"""
        cfg_path = self.data_dir / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text())
        return {}

    def load_trades(self) -> list[dict]:
        """加载交易记录。"""
        trades_path = self.data_dir / "trades.json"
        if trades_path.exists():
            return json.loads(trades_path.read_text())
        return []

    def _today_trades_count(self) -> int:
        """今天已执行的交易次数。"""
        today = date.today().isoformat()
        return sum(
            1 for t in self.load_trades()
            if t.get("timestamp", "").startswith(today)
        )

    def _last_trade_time(self) -> Optional[datetime]:
        """最近一笔交易时间。"""
        trades = self.load_trades()
        if not trades:
            return None
        latest = max(trades, key=lambda t: t.get("timestamp", ""))
        return datetime.fromisoformat(latest["timestamp"])

    def check_trade(
        self,
        action: str,           # 'buy' | 'sell'
        symbol: str,
        amount: float,
        price: float,
        total_value: float,
        current_equity: float,
    ) -> RiskResult:
        """执行风控检查。

        参数:
            action: 交易方向
            symbol: 标的代码
            amount: 交易数量
            price: 交易价格
            total_value: 总资产(现金+持仓)
            current_equity: 当前现金
        """
        cfg = self.config
        today_count = self._today_trades_count()
        last_time = self._last_trade_time()
        portfolio = self.load_state()

        # === 1. Dry run 模式 ===
        if cfg.dry_run:
            return RiskResult(
                allowed=True,
                reason="dry_run模式: 只记录不执行",
                risk_level="normal",
                adjustments={"dry_run": True},
            )

        # === 2. 日内交易次数限制 ===
        if today_count >= cfg.max_trades_per_day:
            return RiskResult(
                allowed=False,
                reason=f"日内交易次数超限: {today_count}/{cfg.max_trades_per_day}",
                risk_level="blocked",
            )

        # === 3. 最小交易间隔 ===
        if last_time:
            elapsed_minutes = (datetime.now() - last_time).total_seconds() / 60
            if elapsed_minutes < cfg.min_trade_interval_minutes:
                return RiskResult(
                    allowed=False,
                    reason=f"交易间隔不足: {elapsed_minutes:.1f}min < {cfg.min_trade_interval_minutes}min",
                    risk_level="blocked",
                )

        # === 4. 单笔交易金额上限 (相对于总资产) ===
        total_trade_value = amount * price
        trade_value_pct = total_trade_value / total_value if total_value > 0 else 0
        if trade_value_pct > cfg.max_single_trade_pct:
            return RiskResult(
                allowed=False,
                reason=f"单笔交易超限: {trade_value_pct*100:.1f}% > {cfg.max_single_trade_pct*100:.0f}%",
                risk_level="blocked",
            )

        # === 5. 现金充足性 (买入时) ===
        if action == "buy":
            if amount * price > current_equity:
                return RiskResult(
                    allowed=False,
                    reason=f"现金不足: 需${amount*price:.2f}，剩余${current_equity:.2f}",
                    risk_level="blocked",
                )

            # 估算买入后总持仓占比
            current_holdings = portfolio.get("btc_holdings", 0.0) * price
            new_holdings = (portfolio.get("btc_holdings", 0.0) + amount) * price
            position_pct = new_holdings / total_value if total_value > 0 else 0
            if position_pct > cfg.max_position_pct:
                return RiskResult(
                    allowed=False,
                    reason=f"单标的持仓超限: {position_pct*100:.1f}% > {cfg.max_position_pct*100:.0f}%",
                    risk_level="blocked",
                )

        # === 6. 持仓充足性 (卖出时) ===
        if action == "sell":
            holdings = portfolio.get("btc_holdings", 0.0)
            if amount > holdings:
                return RiskResult(
                    allowed=False,
                    reason=f"BTC不足: 需{amount}，持有{holdings}",
                    risk_level="blocked",
                )

            # 卖出后现金占比检查
            new_cash = current_equity + amount * price
            new_position_pct = (holdings - amount) * price / total_value if total_value > 0 else 0
            if new_position_pct > cfg.max_total_position_pct:
                return RiskResult(
                    allowed=False,
                    reason=f"总持仓超限: {new_position_pct*100:.1f}% > {cfg.max_total_position_pct*100:.0f}%",
                    risk_level="blocked",
                )

        # === 7. 止损检查 (持仓亏损超过阈值时禁止加仓) ===
        if action == "buy":
            holdings = portfolio.get("btc_holdings", 0.0)
            avg_price = portfolio.get("btc_avg_price", price)
            if holdings > 0 and avg_price > 0:
                loss_pct = (price - avg_price) / avg_price
                if loss_pct < cfg.stop_loss_pct:
                    return RiskResult(
                        allowed=False,
                        reason=f"触发止损线: 当前价${price:.2f} < 均价${avg_price:.2f} ({loss_pct*100:.1f}%), 禁止加仓",
                        risk_level="warning",
                    )

        return RiskResult(allowed=True, reason="风控通过", risk_level="normal")

    def log_risk_decision(
        self,
        trade_id: str,
        result: RiskResult,
        trade_info: dict,
    ) -> None:
        """记录风控决策到日志。"""
        log_path = self.data_dir / "risk_log.jsonl"
        entry = {
            "timestamp": datetime.now().isoformat(),
            "trade_id": trade_id,
            "allowed": result.allowed,
            "reason": result.reason,
            "risk_level": result.risk_level,
            "trade": trade_info,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
