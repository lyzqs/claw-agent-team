"""Paper Trading Interface - M10 模拟交易接口.

连接回测引擎信号 → 风控模块 → 执行层 → 交易记录

Usage:
    from services.quant.paper_trading.interface import PaperTradingInterface

    pti = PaperTradingInterface()
    pti.receive_signal("BTC", "buy", amount=0.01)
    pti.receive_signal("BTC", "sell", amount=0.005)
    status = pti.get_portfolio()
    trades = pti.get_trades()
    pti.export_trades("trades_export.json")
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.quant.paper_trading.risk import PaperRisk, RiskConfig, RiskResult

logger = logging.getLogger("paper_trading.interface")

# Default data directory (shared with BTC trader)
DEFAULT_DATA_DIR = "/root/.openclaw/workspace/btc-quant"


@dataclass
class TradeRecord:
    """交易记录条目。"""
    timestamp: str
    action: str          # 'buy' | 'sell'
    symbol: str
    amount: float
    price: float
    total: float
    risk_check_passed: bool
    risk_reason: str = ""
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "type": self.action.upper(),
            "symbol": self.symbol,
            "amount": self.amount,
            "price": self.price,
            "total": self.total,
            "risk_check_passed": self.risk_check_passed,
            "risk_reason": self.risk_reason,
            "run_id": self.run_id,
        }


class PaperTradingInterface:
    """纸面盘交易接口。

    接收调仓信号，执行风控检查，执行交易，记录日志。
    支持从 backtest engine 或外部策略引擎接收信号。
    """

    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        risk_config: Optional[RiskConfig] = None,
        initial_cash: float = 10_000.0,
        run_id: str = "",
    ):
        self.data_dir = Path(data_dir)
        self.initial_cash = initial_cash
        self.run_id = run_id or f"paper_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize risk control
        self.risk = PaperRisk(config=risk_config, data_dir=str(self.data_dir))

        # Ensure config exists
        self._ensure_config()

        # Internal trade log
        self._trades: list[TradeRecord] = []

    def _ensure_config(self) -> None:
        """确保配置文件存在。"""
        cfg_path = self.data_dir / "config.json"
        if not cfg_path.exists():
            cfg = {
                "total_balance": self.initial_cash,
                "cash": self.initial_cash,
                "btc_holdings": 0.0,
                "btc_avg_price": 0.0,
                "futures_positions": [],
                "grid_enabled": False,
                "created_at": datetime.now().isoformat(),
            }
            cfg_path.write_text(json.dumps(cfg, indent=2))

    def _load_config(self) -> dict:
        return json.loads((self.data_dir / "config.json").read_text())

    def _save_config(self, cfg: dict) -> None:
        (self.data_dir / "config.json").write_text(json.dumps(cfg, indent=2))

    def _load_trades(self) -> list[dict]:
        path = self.data_dir / "trades.json"
        if path.exists():
            return json.loads(path.read_text())
        return []

    def _save_trades(self, trades: list[dict]) -> None:
        (self.data_dir / "trades.json").write_text(json.dumps(trades, indent=2))

    def _load_prices(self) -> dict:
        """Load current price from prediction_input.json"""
        path = self.data_dir / "prediction_input.json"
        if path.exists():
            data = json.loads(path.read_text())
            return {"current_price": data.get("current_price", 0)}
        return {"current_price": 0}

    def get_current_price(self, symbol: str = "BTC") -> float:
        """获取当前市场价格。"""
        if symbol == "BTC":
            return self._load_prices().get("current_price", 0)
        return 0.0

    def get_portfolio(self) -> dict:
        """查询当前持仓。"""
        cfg = self._load_config()
        price = self.get_current_price()
        btc_value = cfg.get("btc_holdings", 0) * price
        total = cfg.get("cash", 0) + btc_value

        return {
            "cash": cfg.get("cash", 0),
            "btc_holdings": cfg.get("btc_holdings", 0),
            "btc_avg_price": cfg.get("btc_avg_price", 0),
            "btc_value": btc_value,
            "total_value": total,
            "unrealized_pnl": btc_value - (cfg.get("btc_holdings", 0) * cfg.get("btc_avg_price", 0)),
            "grid_enabled": cfg.get("grid_enabled", False),
            "futures_positions": cfg.get("futures_positions", []),
            "run_id": self.run_id,
        }

    def receive_signal(
        self,
        symbol: str,
        action: str,          # 'buy' | 'sell'
        amount: Optional[float] = None,
        price: Optional[float] = None,
        dry_run: bool = False,
    ) -> dict:
        """接收调仓信号并执行交易。

        参数:
            symbol: 标的代码 (如 'BTC')
            action: 'buy' | 'sell' | 'hold'
            amount: 交易数量 (若为 None，则根据 current_cash 自动计算)
            price: 交易价格 (若为 None，则用当前市场价格)
            dry_run: True 则只做风控检查，不实际执行

        返回:
            dict: {success, action, reason, trade_record}
        """
        if action == "hold":
            return {"success": True, "action": "hold", "reason": "no action needed", "trade_record": None}

        price = price or self.get_current_price(symbol)
        if price <= 0:
            return {"success": False, "action": action, "reason": "无法获取当前价格", "trade_record": None}

        cfg = self._load_config()
        total_value = cfg.get("cash", 0) + cfg.get("btc_holdings", 0) * price
        current_equity = cfg.get("cash", 0)

        # === 风控检查 ===
        if amount is None:
            # 自动计算: 用30%现金买入
            if action == "buy":
                amount = (current_equity * 0.3) / price
                amount = float(int(amount * 1000)) / 1000  # 保留3位小数

        total_trade_value = amount * price

        risk_result = self.risk.check_trade(
            action=action,
            symbol=symbol,
            amount=amount,
            price=price,
            total_value=total_value,
            current_equity=current_equity,
        )

        # Log risk decision
        self.risk.log_risk_decision(
            trade_id=f"{self.run_id}_{datetime.now().timestamp()}",
            result=risk_result,
            trade_info={"symbol": symbol, "action": action, "amount": amount, "price": price},
        )

        if not risk_result.allowed:
            logger.warning(f"Risk check blocked trade: {risk_result.reason}")
            return {
                "success": False,
                "action": action,
                "reason": f"[风控拦截] {risk_result.reason}",
                "risk_level": risk_result.risk_level,
                "trade_record": None,
            }

        # === 执行交易 (dry_run 不写入) ===
        if not dry_run:
            if action == "buy":
                new_cash = cfg.get("cash", 0) - total_trade_value
                new_btc = cfg.get("btc_holdings", 0) + amount
                avg_price = cfg.get("btc_avg_price", price)
                if new_btc > 0:
                    total_cost = (cfg.get("btc_holdings", 0) * avg_price) + total_trade_value
                    cfg["btc_avg_price"] = total_cost / new_btc
                cfg["btc_holdings"] = new_btc
                cfg["cash"] = new_cash
                cfg["total_balance"] = cfg["cash"] + cfg["btc_holdings"] * price

            elif action == "sell":
                new_cash = cfg.get("cash", 0) + total_trade_value
                new_btc = cfg.get("btc_holdings", 0) - amount
                cfg["btc_holdings"] = new_btc
                cfg["cash"] = new_cash
                cfg["total_balance"] = cfg["cash"] + cfg["btc_holdings"] * price

            self._save_config(cfg)

            # 记录交易
            trade_record = TradeRecord(
                timestamp=datetime.now().isoformat(),
                action=action,
                symbol=symbol,
                amount=amount,
                price=price,
                total=total_trade_value,
                risk_check_passed=True,
                risk_reason=risk_result.reason,
                run_id=self.run_id,
            )
            self._trades.append(trade_record)

            # 追加到 trades.json
            trades_list = self._load_trades()
            trades_list.append(trade_record.to_dict())
            self._save_trades(trades_list)

            logger.info(f"[Paper Trading] {action.upper()} {amount} {symbol} @ ${price:.2f} (risk: {risk_result.reason})")
            return {
                "success": True,
                "action": action,
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": total_trade_value,
                "reason": risk_result.reason,
                "trade_record": trade_record.to_dict(),
            }
        else:
            return {
                "success": True,
                "action": action,
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": total_trade_value,
                "reason": f"[Dry Run] {risk_result.reason}",
                "trade_record": None,
            }

    def get_trades(self, symbol: Optional[str] = None) -> list[dict]:
        """查询交易记录。

        参数:
            symbol: 若指定则只返回该标的的交易
        """
        trades = self._load_trades()
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol or t.get("type","").lower() in ["buy","sell"]]
        return trades

    def export_trades(self, output_path: Optional[str] = None) -> dict:
        """导出交易记录到 JSON 文件。"""
        trades = self._load_trades()
        portfolio = self.get_portfolio()

        export = {
            "run_id": self.run_id,
            "exported_at": datetime.now().isoformat(),
            "portfolio": portfolio,
            "total_trades": len(trades),
            "buy_trades": sum(1 for t in trades if t.get("type","").upper()=="BUY"),
            "sell_trades": sum(1 for t in trades if t.get("type","").upper()=="SELL"),
            "trades": trades,
        }

        path = output_path or str(self.data_dir / f"paper_trades_{self.run_id}.json")
        Path(path).write_text(json.dumps(export, indent=2, ensure_ascii=False, default=str))
        logger.info(f"Exported {len(trades)} trades to {path}")
        return export

    def get_performance_summary(self) -> dict:
        """获取纸面盘绩效摘要。"""
        trades = self._load_trades()
        cfg = self._load_config()
        price = self.get_current_price()

        if not trades:
            return {"total_trades": 0, "message": "No trades yet"}

        first_trade = trades[0]
        last_trade = trades[-1]

        buy_count = sum(1 for t in trades if t.get("type","").upper()=="BUY")
        sell_count = sum(1 for t in trades if t.get("type","").upper()=="SELL")

        current_total = cfg.get("cash", 0) + cfg.get("btc_holdings", 0) * price
        total_return_pct = (current_total / self.initial_cash - 1) * 100 if self.initial_cash > 0 else 0

        return {
            "run_id": self.run_id,
            "started": first_trade.get("timestamp",""),
            "last_trade": last_trade.get("timestamp",""),
            "total_trades": len(trades),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "current_total": round(current_total, 2),
            "initial_cash": self.initial_cash,
            "total_return_pct": round(total_return_pct, 2),
            "cash": round(cfg.get("cash", 0), 2),
            "btc_holdings": cfg.get("btc_holdings", 0),
            "btc_avg_price": cfg.get("btc_avg_price", 0),
            "current_price": price,
        }