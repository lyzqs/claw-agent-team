"""组合管理 — 持仓、现金流、交易记录。"""
from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Trade:
    """一次完整交易（含买入+卖出）。"""
    stock_code: str
    buy_date: date
    buy_price: float
    buy_quantity: float
    sell_date: Optional[date] = None
    sell_price: Optional[float] = None
    sell_quantity: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    holding_days: Optional[int] = None

    def close(self, sell_date: date, sell_price: float, quantity: float) -> None:
        self.sell_date = sell_date
        self.sell_price = sell_price
        self.sell_quantity = quantity
        self.pnl = (sell_price - self.buy_price) * quantity
        self.pnl_pct = (sell_price / self.buy_price - 1) * 100
        self.holding_days = (sell_date - self.buy_date).days


@dataclass
class Position:
    """当前持仓。"""
    stock_code: str
    quantity: float
    avg_price: float
    entry_date: date


@dataclass
class Portfolio:
    """回测组合账户。"""
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003      # 万三，双边收取
    slippage: float = 0.0001            # 万一跳价
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_history: list[tuple[date, float]] = field(default_factory=list)
    # 日期索引的价格表（用于市值计算）
    _close_prices: dict[str, dict[date, float]] = field(default_factory=dict)
    _current_date: Optional[date] = None

    @property
    def cash(self) -> float:
        return self.initial_cash - self._invested_cash + self._realized_pnl

    @property
    def _invested_cash(self) -> float:
        return sum(p.quantity * p.avg_price for p in self.positions.values())

    @property
    def _realized_pnl(self) -> float:
        return sum(t.pnl or 0.0 for t in self.trades)

    def total_value(self, current_date: date) -> float:
        pos_value = 0.0
        for code, pos in self.positions.items():
            price = self._close_prices.get(code, {}).get(current_date, pos.avg_price)
            pos_value += pos.quantity * price
        return self.cash + pos_value

    def update_price(self, stock_code: str, trade_date: date, close: float) -> None:
        if stock_code not in self._close_prices:
            self._close_prices[stock_code] = {}
        self._close_prices[stock_code][trade_date] = close

    def can_buy(self, stock_code: str, price: float, quantity: float) -> bool:
        cost = price * quantity * (1 + self.commission_rate + self.slippage)
        return self.cash >= cost

    def buy(self, stock_code: str, trade_date: date, price: float, quantity: float) -> bool:
        execution_price = price * (1 + self.slippage)
        cost = execution_price * quantity
        commission = cost * self.commission_rate
        total_cost = cost + commission

        if total_cost > self.cash:
            return False

        if stock_code not in self.positions:
            self.positions[stock_code] = Position(
                stock_code=stock_code,
                quantity=0.0,
                avg_price=0.0,
                entry_date=trade_date,
            )

        pos = self.positions[stock_code]
        total_qty = pos.quantity + quantity
        pos.avg_price = (pos.avg_price * pos.quantity + execution_price * quantity) / total_qty
        pos.quantity = total_qty

        return True

    def sell(
        self,
        stock_code: str,
        trade_date: date,
        price: float,
        quantity: float,
    ) -> bool:
        if stock_code not in self.positions:
            return False
        pos = self.positions[stock_code]
        if pos.quantity < quantity:
            return False

        execution_price = price * (1 - self.slippage)
        revenue = execution_price * quantity
        commission = revenue * self.commission_rate
        net_revenue = revenue - commission

        pos.quantity -= quantity
        if pos.quantity < 1e-8:
            del self.positions[stock_code]

        # Record trade
        trade = Trade(
            stock_code=stock_code,
            buy_date=pos.entry_date,
            buy_price=pos.avg_price,
            buy_quantity=quantity,
        )
        trade.close(trade_date, execution_price, quantity)
        self.trades.append(trade)

        return True

    def record_equity(self, trade_date: date) -> None:
        self._current_date = trade_date
        self.equity_history.append((trade_date, self.total_value(trade_date)))

    def compute_metrics(
        self,
        benchmark_returns: Optional[pd.Series] = None,
        risk_free_rate: float = 0.03,
    ) -> dict:
        """计算核心回测指标。"""
        if not self.equity_history:
            return {}

        eq_dates, eq_values = zip(*self.equity_history)
        eq_dates = list(eq_dates)
        eq_values = list(eq_values)
        n = len(eq_values)

        # Basic metrics
        total_return = (eq_values[-1] / eq_values[0] - 1) * 100
        initial = eq_values[0]
        final = eq_values[-1]

        # Daily returns
        daily_returns = pd.Series(eq_values).pct_change().dropna().values

        # Annualized metrics
        years = n / 252 if n > 0 else 1
        annual_return = ((final / initial) ** (1 / years) - 1) * 100 if initial > 0 else 0.0
        annual_volatility = float(pd.Series(daily_returns).std() * (252 ** 0.5)) * 100

        # Sharpe ratio
        excess_return = annual_return / 100 - risk_free_rate
        sharpe = (excess_return / (annual_volatility / 100)) if annual_volatility > 0 else 0.0

        # Max drawdown
        peak = eq_values[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        for v in eq_values:
            if v > peak:
                peak = v
            dd = (v - peak) / peak * 100
            if dd < max_dd_pct:
                max_dd_pct = dd

        # Win rate
        closed_trades = [t for t in self.trades if t.pnl is not None]
        if closed_trades:
            wins = [t for t in closed_trades if t.pnl > 0]
            total_trades = len(closed_trades)
            win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0.0
            avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
            losses = [t for t in closed_trades if t.pnl <= 0]
            avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
            profit_factor = (sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))) if losses else 0.0
        else:
            total_trades = 0
            win_rate = 0.0
            avg_win = 0.0
            avg_loss = 0.0
            profit_factor = 0.0

        # Calmar ratio
        max_dd_abs = abs(max_dd_pct)
        calmar = annual_return / max_dd_abs if max_dd_abs > 0 else 0.0

        # Max consecutive losses
        max_consecutive_losses = 0
        current_consecutive = 0
        for t in closed_trades:
            if t.pnl < 0:
                current_consecutive += 1
                max_consecutive_losses = max(max_consecutive_losses, current_consecutive)
            else:
                current_consecutive = 0

        # Avg trade duration
        if closed_trades:
            durations = [t.holding_days for t in closed_trades if t.holding_days is not None]
            avg_duration = sum(durations) / len(durations) if durations else 0.0
        else:
            avg_duration = 0.0

        return {
            "total_return_pct": round(total_return, 2),
            "annual_return_pct": round(annual_return, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "win_rate_pct": round(win_rate, 2),
            "total_trades": total_trades,
            "winning_trades": len(wins) if closed_trades else 0,
            "losing_trades": len(losses) if closed_trades else 0,
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "calmar_ratio": round(calmar, 2),
            "max_consecutive_losses": max_consecutive_losses,
            "avg_trade_duration_days": round(avg_duration, 1),
            "volatility_pct": round(annual_volatility, 2),
            "final_value": round(final, 2),
        }
