"""QuantitativeInvest 回测引擎核心。

模块结构
==========
backtest/engine.py      — 核心回测引擎 (BacktestEngine)
backtest/datafeed.py   — 数据供应器 (DBDataFeed/CSVDataFeed/MemoryDataFeed)
backtest/portfolio.py  — 组合管理 (持仓、现金流、统计，compute_metrics 在此)
backtest/indicators.py — 技术指标 (ma/rsi/macd/bollinger_bands/atr)
backtest/strategies.py — 策略模板 (TrendFollowingStrategy/MeanReversionStrategy)
backtest/mock_data.py  — 模拟数据生成器 (用于开发测试)
backtest/runner.py     — CLI 入口 + 网格搜索
"""

from .engine import BacktestEngine
from .datafeed import DataFeed, DBDataFeed, CSVDataFeed, MemoryDataFeed
from .portfolio import Portfolio
from .indicators import ma, rsi, macd, bollinger_bands, atr, ema
from .strategies import Strategy, TrendFollowingStrategy, MeanReversionStrategy

__all__ = [
    "BacktestEngine",
    "DataFeed",
    "DBDataFeed",
    "CSVDataFeed",
    "MemoryDataFeed",
    "Portfolio",
    "Strategy",
    "TrendFollowingStrategy",
    "MeanReversionStrategy",
    "ma", "rsi", "macd", "bollinger_bands", "atr", "ema",
]
