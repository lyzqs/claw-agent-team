"""规则型策略模板。"""
from __future__ import annotations

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from typing import Optional, NamedTuple


class Signal(NamedTuple):
    """交易信号。"""
    stock_code: str
    action: str   # 'buy' | 'sell' | 'hold'
    reason: str = ""


class Strategy(ABC):
    """策略基类。

    支持两种模式：
    1. 预计算模式（推荐）：warm() 预计算所有技术指标，后续 on_bar() O(1) 查表。
    2. 热启动模式：warm() 预加载历史数据，on_bar() 在预热数据上实时计算。

    预计算指标存储在 _indicators[stock_code] = dict，键为指标名，值为 pd.Series。
    """

    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params
        self._history: dict[str, pd.DataFrame] = {}      # 原始历史（用于 on_bar 实时计算）
        self._indicators: dict[str, dict[str, pd.Series]] = {}  # 预计算指标

    def warm(self, stock_code: str, bars: pd.DataFrame) -> None:
        """热启动：存储原始历史，并预计算技术指标。

        子类可重写 _precompute() 自定义预计算指标。
        """
        self._history[stock_code] = bars.copy()
        self._indicators[stock_code] = self._precompute(bars)

    def _precompute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """子类可重写，预计算技术指标。默认返回空字典。"""
        return {}

    def patch_last_bar(self, stock_code: str, bar: pd.Series) -> None:
        """用当日数据覆盖 _history[stock_code] 的最后一行。"""
        if stock_code not in self._history:
            return
        hist = self._history[stock_code]
        if hist.empty:
            return
        last_idx = hist.index[-1]
        for col in ["open", "high", "low", "close", "volume"]:
            if col in bar.index:
                hist.at[last_idx, col] = bar[col]

    @abstractmethod
    def on_bar(self, stock_code: str, date_idx: int) -> Optional[Signal]:
        """每根柱调用一次。date_idx 是日期在回测窗口中的 0 基索引。"""
        raise NotImplementedError

    def signals(self, stock_codes: list[str]) -> list[Signal]:
        signals = []
        for code in stock_codes:
            sig = self.on_bar(code, 0)
            if sig is not None:
                signals.append(sig)
        return signals

    def clear(self) -> None:
        self._history.clear()
        self._indicators.clear()


class TrendFollowingStrategy(Strategy):
    """趋势跟踪策略 — 双均线金叉死叉。

    参数:
        fast_period: 快线周期 (默认 5)
        slow_period: 慢线周期 (默认 20)
    """

    def __init__(self, params: dict):
        super().__init__("trend_following", params)
        self._holdings: set[str] = set()

    def _precompute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        fast = self.params.get("fast_period", 5)
        slow = self.params.get("slow_period", 20)
        closes = df["close"]
        ma_fast = closes.rolling(window=fast, min_periods=fast).mean()
        ma_slow = closes.rolling(window=slow, min_periods=slow).mean()
        return {
            "ma_fast": ma_fast,
            "ma_slow": ma_slow,
        }

    def on_bar(self, stock_code: str, date_idx: int) -> Optional[Signal]:
        ind = self._indicators.get(stock_code, {})
        if not ind:
            return None

        ma_fast = ind["ma_fast"]
        ma_slow = ind["ma_slow"]

        if date_idx < 1 or date_idx >= len(ma_fast) or date_idx >= len(ma_slow):
            return None

        curr_fast = ma_fast.iloc[date_idx]
        curr_slow = ma_slow.iloc[date_idx]
        prev_fast = ma_fast.iloc[date_idx - 1]
        prev_slow = ma_slow.iloc[date_idx - 1]

        if pd.isna(curr_fast) or pd.isna(curr_slow):
            return None

        fast = self.params.get("fast_period", 5)
        slow = self.params.get("slow_period", 20)

        # 金叉：快线从下方穿越慢线
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            if stock_code not in self._holdings:
                return Signal(stock_code, "buy", f"ma_cross_up fast={fast} slow={slow}")

        # 死叉：快线从上方穿越慢线
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            if stock_code in self._holdings:
                self._holdings.discard(stock_code)
                return Signal(stock_code, "sell", f"ma_cross_down fast={fast} slow={slow}")

        return None


class MeanReversionStrategy(Strategy):
    """均值回归策略 — RSI 超买超卖。

    参数:
        rsi_period: RSI 周期 (默认 14)
        oversold: 超卖阈值 (默认 30)
        overbought: 超买阈值 (默认 70)
    """

    def __init__(self, params: dict):
        super().__init__("mean_reversion", params)
        self._holdings: set[str] = set()

    def _precompute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        from .indicators import rsi
        period = self.params.get("rsi_period", 14)
        closes = df["close"]
        return {
            "rsi": rsi(closes, period),
        }

    def on_bar(self, stock_code: str, date_idx: int) -> Optional[Signal]:
        ind = self._indicators.get(stock_code, {})
        if not ind:
            return None

        rsi_series = ind["rsi"]
        if date_idx < 1 or date_idx >= len(rsi_series):
            return None

        curr_rsi = rsi_series.iloc[date_idx]
        hist = self._history.get(stock_code)
        if hist is None or date_idx >= len(hist):
            return None
        curr_close = hist.iloc[date_idx]["close"]

        if pd.isna(curr_rsi):
            return None

        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)

        if curr_rsi < oversold and stock_code not in self._holdings:
            return Signal(stock_code, "buy", f"rsi={curr_rsi:.1f}<{oversold}")

        if curr_rsi > overbought and stock_code in self._holdings:
            self._holdings.discard(stock_code)
            return Signal(stock_code, "sell", f"rsi={curr_rsi:.1f}>{overbought}")

        return None
