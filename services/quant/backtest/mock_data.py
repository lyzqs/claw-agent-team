"""模拟数据生成器 — 用于开发和测试回测引擎（不依赖真实数据库）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[2]))
from .datafeed import MemoryDataFeed


def generate_stock_data(
    stock_code: str,
    start_date: date,
    end_date: date,
    start_price: float = 100.0,
    annual_return: float = 0.12,
    annual_volatility: float = 0.25,
    seed: int = 42,
) -> pd.DataFrame:
    """生成模拟股票日线数据（几何布朗运动）。

    Args:
        stock_code: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        start_price: 起始价格
        annual_return: 年化收益率
        annual_volatility: 年化波动率
        seed: 随机种子
    """
    np.random.seed(seed)

    # 生成交易日（跳过周末）
    all_days = pd.date_range(start_date, end_date, freq="D")
    trading_days = all_days[all_days.weekday < 5]  # Mon-Fri

    n = len(trading_days)
    dt = 1 / 252
    annual_drift = annual_return * dt
    annual_vol = annual_volatility * np.sqrt(dt)

    # GBM 模拟
    log_returns = np.random.normal(annual_drift, annual_vol, n)
    prices = start_price * np.exp(np.cumsum(log_returns))

    # 生成 OHLCV
    highs = prices * (1 + np.abs(np.random.normal(0, 0.01, n)))
    lows = prices * (1 - np.abs(np.random.normal(0, 0.01, n)))
    opens = prices * (1 + np.random.normal(0, 0.005, n))
    volumes = np.random.lognormal(15, 0.5, n).astype(int)

    df = pd.DataFrame({
        "trade_date": trading_days,
        "open": np.round(opens, 2),
        "high": np.round(highs, 2),
        "low": np.round(np.minimum(lows, prices), 2),
        "close": np.round(prices, 2),
        "volume": volumes,
    })
    return df


def generate_multi_stock_data(
    stock_codes: list[str],
    start_date: date,
    end_date: date,
    base_start_price: float = 100.0,
    annual_return_range: tuple[float, float] = (-0.05, 0.30),
    annual_vol_range: tuple[float, float] = (0.15, 0.40),
    seed_base: int = 42,
) -> dict[str, pd.DataFrame]:
    """生成多只股票的数据。"""
    result = {}
    n = len(stock_codes)
    ret_range = annual_return_range
    vol_range = annual_vol_range

    for i, code in enumerate(stock_codes):
        ret = np.random.default_rng(seed_base + i).uniform(ret_range[0], ret_range[1])
        vol = np.random.default_rng(seed_base + i + 1000).uniform(vol_range[0], vol_range[1])
        df = generate_stock_data(
            code, start_date, end_date,
            start_price=base_start_price * (1 + i * 0.1),
            annual_return=ret,
            annual_volatility=vol,
            seed=seed_base + i,
        )
        result[code] = df
    return result


def make_mock_datafeed(
    stock_codes: list[str],
    start_date: str = "2023-01-01",
    end_date: str = "2024-12-31",
) -> MemoryDataFeed:
    """创建带模拟数据的 MemoryDataFeed。"""
    data = generate_multi_stock_data(
        stock_codes,
        start_date=pd.to_datetime(start_date).date(),
        end_date=pd.to_datetime(end_date).date(),
    )
    return MemoryDataFeed(data)


if __name__ == "__main__":
    # CLI 测试
    codes = ["TEST001", "TEST002", "TEST003"]
    feed = make_mock_datafeed(codes, "2023-01-01", "2024-12-31")
    bars = feed.get_bars(codes)
    for code, df in bars.items():
        print(f"{code}: {len(df)} bars, first={df['trade_date'].iloc[0].date()}, last={df['trade_date'].iloc[-1].date()}")