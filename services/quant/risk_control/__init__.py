"""Risk Control Module (M9) — 风控模块。

独立的风控引擎，可与回测引擎 / 模拟交易 / 实盘接口联动。

使用方式:
    from services.quant.risk_control import RiskController, RiskLevel

    risk = RiskController()
    result = risk.check(
        symbol="AAPL",
        action="buy",
        price=150.0,
        quantity=100,
        current_price=150.0,
        portfolio_value=100000.0,
        cash=50000.0,
        holdings={"AAPL": {"qty": 50, "avg_price": 145.0}},
        equity_history=[("2024-01-01", 100000), ...],   # (date_str, equity)
        config={"max_drawdown_pct": 0.20, ...}
    )
    if not result.allowed:
        print(f"风控拦截: {result.reason}")
"""
from .controller import RiskController, RiskLevel
from .models import RiskResult, RiskConfig
from .logger import RiskLogger

__all__ = [
    "RiskController",
    "RiskLevel",
    "RiskResult",
    "RiskConfig",
    "RiskLogger",
]
