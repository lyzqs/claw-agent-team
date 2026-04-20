"""Strategy Parameter Optimizer (M8) — 策略参数优化接口。

使用方式:
    from services.quant.optimizer import Optimizer, BacktestConfig

    cfg = BacktestConfig(
        strategy_name="trend_following",
        start_date="2023-01-01",
        end_date="2024-12-31",
        stock_codes=["600519", "000858"],
        initial_cash=100_000,
        param_grid={
            "fast_period": [5, 10, 15, 20],
            "slow_period": [30, 60, 90],
        },
        sort_by="sharpe_ratio",  # 'sharpe_ratio' | 'total_return'
    )

    opt = Optimizer(cfg)
    results = opt.run()
    opt.print_results()
    opt.apply_best()
"""
from .optimizer import Optimizer, BacktestConfig

__all__ = ["Optimizer", "BacktestConfig"]
