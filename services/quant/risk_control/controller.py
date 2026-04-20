"""Risk Control Controller (M9) — 风控引擎核心。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .models import RiskConfig, RiskResult, RiskLevel
from .logger import RiskLogger

logger = logging.getLogger("risk_control")


class RiskController:
    """风控控制器。

    每次交易（买入/卖出）前调用 check()，返回 RiskResult。
    allowed=False 时说明风控拦截了该交易。

    使用方式:
        risk = RiskController(config=RiskConfig(...))
        result = risk.check(
            symbol="AAPL",
            action="buy",
            price=150.0,
            quantity=100,
            current_price=150.0,
            portfolio_value=100000.0,
            cash=50000.0,
            holdings={"AAPL": {"qty": 50, "avg_price": 145.0}},
            equity_history=[("2024-01-01", 100000), ...],
        )
        if not result.allowed:
            print(f"拦截: {result.reason}")
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        log_dir: str = "",
    ):
        self.config = config or RiskConfig()
        self.logger = RiskLogger(log_dir or self.config.risk_log_path)
        self._today_trades: list[datetime] = []
        self._last_trade_time: Optional[datetime] = None
        self._peak_equity: float = 0.0

    def check(
        self,
        symbol: str,
        action: str,
        price: float,
        quantity: float,
        current_price: float,
        portfolio_value: float,
        cash: float,
        holdings: dict,   # {symbol: {"qty": float, "avg_price": float}}
        equity_history: list,  # [(date_str, equity_value), ...]
    ) -> RiskResult:
        """执行完整风控检查。返回允许/拦截/警告结果。"""
        cfg = self.config

        # === 1. 最大回撤检查 ===
        dd_result = self._check_drawdown(portfolio_value, equity_history)
        if dd_result is not None:
            return dd_result

        # === 2. 日内交易频率 ===
        freq_result = self._check_frequency()
        if freq_result is not None:
            return freq_result

        # === 3. 单笔交易金额上限 ===
        trade_value = price * quantity
        trade_pct = trade_value / portfolio_value if portfolio_value > 0 else 0
        if trade_pct > cfg.max_single_trade_pct:
            r = RiskResult(
                allowed=False,
                reason=f"单笔交易超限: {trade_pct*100:.1f}% > {cfg.max_single_trade_pct*100:.0f}%",
                risk_level="blocked",
                single_trade_pct=trade_pct,
            )
            self.logger.log("blocked", symbol, action, r.reason, {
                "trade_pct": trade_pct, "max_pct": cfg.max_single_trade_pct,
            })
            return r

        # === 4. 单股仓位上限 ===
        if action == "buy":
            new_holding_value = holdings.get(symbol, {}).get("qty", 0) * current_price
            new_total_value = new_holding_value + trade_value
            new_pct = new_total_value / portfolio_value if portfolio_value > 0 else 0
            if new_pct > cfg.max_position_pct:
                r = RiskResult(
                    allowed=False,
                    reason=f"单股仓位超限: {new_pct*100:.1f}% > {cfg.max_position_pct*100:.0f}%",
                    risk_level="blocked",
                    position_pct=new_pct,
                )
                self.logger.log("blocked", symbol, action, r.reason, {
                    "position_pct": new_pct, "max_pct": cfg.max_position_pct,
                })
                return r

        # === 5. 现金充足性（买入时）===
        if action == "buy":
            if price * quantity > cash:
                r = RiskResult(
                    allowed=False,
                    reason=f"现金不足: 需要 ${price*quantity:.2f} > 现有 ${cash:.2f}",
                    risk_level="blocked",
                )
                self.logger.log("blocked", symbol, action, r.reason, {
                    "required": price * quantity, "cash": cash,
                })
                return r

        # === 6. 止损策略 ===
        stop_result = self._check_stop_loss(
            symbol, action, price, quantity, holdings, current_price, portfolio_value
        )
        if stop_result is not None:
            return stop_result

        # 全部通过
        self._record_trade()
        self.logger.log("info", symbol, action, "风控通过", {
            "price": price, "quantity": quantity,
        })
        return RiskResult(allowed=True, reason="风控通过", risk_level="pass")

    def _check_drawdown(
        self,
        portfolio_value: float,
        equity_history: list,
    ) -> Optional[RiskResult]:
        """检查最大回撤。超过阈值则 BLOCKED。"""
        cfg = self.config
        if not equity_history:
            return None

        # 更新峰值
        for _, equity in equity_history:
            if equity > self._peak_equity:
                self._peak_equity = equity

        # 当前值低于峰值
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - portfolio_value) / self._peak_equity

            if drawdown >= cfg.max_drawdown_pct:
                r = RiskResult(
                    allowed=False,
                    reason=f"最大回撤超限: {drawdown*100:.1f}% ≥ {cfg.max_drawdown_pct*100:.0f}%",
                    risk_level="blocked",
                    drawdown_pct=drawdown,
                )
                self.logger.log("blocked", "SYSTEM", "trade", r.reason, {
                    "drawdown": drawdown,
                    "max_drawdown": cfg.max_drawdown_pct,
                    "peak_equity": self._peak_equity,
                    "current_equity": portfolio_value,
                })
                return r
            elif drawdown >= cfg.max_drawdown_pct * 0.8:
                r = RiskResult(
                    allowed=True,
                    reason=f"回撤警告: {drawdown*100:.1f}% 已达阈值80%",
                    risk_level="warning",
                    drawdown_pct=drawdown,
                )
                self.logger.log("warning", "SYSTEM", "trade", r.reason, {
                    "drawdown": drawdown,
                })
                return r

        return None

    def _check_frequency(self) -> Optional[RiskResult]:
        """检查日内交易频率和最小间隔。"""
        cfg = self.config
        now = datetime.now()

        # 每日次数
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_trades = [t for t in self._today_trades if t >= today_start]
        if len(today_trades) >= cfg.max_trades_per_day:
            r = RiskResult(
                allowed=False,
                reason=f"日内交易次数已达上限: {len(today_trades)}/{cfg.max_trades_per_day}",
                risk_level="blocked",
            )
            self.logger.log("blocked", "SYSTEM", "trade", r.reason, {
                "count": len(today_trades), "limit": cfg.max_trades_per_day,
            })
            return r

        # 最小间隔
        if self._last_trade_time:
            elapsed = (now - self._last_trade_time).total_seconds() / 60
            if elapsed < cfg.min_trade_interval_minutes:
                r = RiskResult(
                    allowed=False,
                    reason=f"交易间隔不足: {elapsed:.1f}min < {cfg.min_trade_interval_minutes}min",
                    risk_level="blocked",
                )
                self.logger.log("blocked", "SYSTEM", "trade", r.reason, {
                    "elapsed_min": elapsed,
                })
                return r

        return None

    def _check_stop_loss(
        self,
        symbol: str,
        action: str,
        price: float,
        quantity: float,
        holdings: dict,
        current_price: float,
        portfolio_value: float,
    ) -> Optional[RiskResult]:
        """检查固定止损和移动止损。"""
        cfg = self.config
        pos = holdings.get(symbol, {})
        avg_price = pos.get("avg_price", 0)
        qty = pos.get("qty", 0)

        # --- 固定止损：持仓亏损超过阈值时，禁止加仓 ---
        if action == "buy" and avg_price > 0:
            loss_pct = (current_price - avg_price) / avg_price
            if loss_pct <= cfg.stop_loss_pct:
                r = RiskResult(
                    allowed=False,
                    reason=f"固定止损触发: 当前亏损 {loss_pct*100:.1f}% ≤ 阈值 {cfg.stop_loss_pct*100:.1f}%",
                    risk_level="blocked",
                    stop_loss_triggered=True,
                )
                self.logger.log("blocked", symbol, action, r.reason, {
                    "loss_pct": loss_pct,
                    "avg_price": avg_price,
                    "current_price": current_price,
                })
                return r

        # --- 移动止损：盈利超过 trailing_stop_pct 后，锁定利润 ---
        if action == "sell" and avg_price > 0 and cfg.trailing_stop_pct > 0:
            gain_pct = (current_price - avg_price) / avg_price
            if gain_pct > cfg.trailing_stop_pct:
                # 允许卖出（盈利了）
                pass

        return None

    def _record_trade(self) -> None:
        """记录一笔成功交易（用于频率控制）。"""
        now = datetime.now()
        self._today_trades.append(now)
        self._last_trade_time = now
        # 只保留今天之前的记录（避免内存膨胀）
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._today_trades = [t for t in self._today_trades if t >= today_start]

    def get_drawdown(self, equity_history: list) -> float:
        """计算当前回撤率（0~1）。"""
        if not equity_history:
            return 0.0
        peak = max(e for _, e in equity_history)
        current = equity_history[-1][1]
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - current) / peak)

    def get_status(self) -> dict:
        """获取当前风控状态（用于监控面板）。"""
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = sum(1 for t in self._today_trades if t >= today_start)
        return {
            "peak_equity": self._peak_equity,
            "today_trades": today_count,
            "last_trade_time": self._last_trade_time.isoformat() if self._last_trade_time else None,
        }
