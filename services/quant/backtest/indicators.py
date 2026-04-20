"""技术指标库 — 纯 NumPy 实现，无额外依赖。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Tuple


def ma(closes: pd.Series, period: int) -> pd.Series:
    """简单移动平均 (SMA)。"""
    return closes.rolling(window=period, min_periods=period).mean()


def ema(closes: pd.Series, period: int) -> pd.Series:
    """指数移动平均 (EMA)。"""
    return closes.ewm(span=period, adjust=False).mean()


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指数 (RSI)。

    使用 EWM 计算平均涨跌幅，公式：
        RSI = 100 - 100 / (1 + RS)
        RS = avg_gain / avg_loss

    注意：loss=0 的行保持为 0（而非 NaN），否则 rolling 会全部 NaN。
    """
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    # clip 之后再 negate，确保损失为正（而非负数）
    loss = (-delta.clip(upper=0.0)).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    # avg_loss=0 → RS→∞ → RSI=100
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    rsi_val = rsi_val.fillna(value=100.0)
    return rsi_val.clip(0.0, 100.0)


def macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (Moving Average Convergence Divergence)。

    Returns:
        (macd_line, signal_line, histogram)
    """
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """布林带 (Bollinger Bands)。

    Returns:
        (upper_band, middle_band, lower_band)
    """
    middle = ma(closes, period)
    std = closes.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def atr(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range (ATR)。"""
    high_low = highs - lows
    high_close = (highs - closes.shift(1)).abs()
    low_close = (lows - closes.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()
