"""规则型策略模板。"""
from __future__ import annotations

import pandas as pd
from abc import ABC, abstractmethod
from typing import Optional, NamedTuple


class Signal(NamedTuple):
    """交易信号。"""
    stock_code: str
    action: str   # 'buy' | 'sell' | 'hold'
    reason: str = ""


class Strategy(ABC):
    """策略基类。

    热启动模式：
        warm(code, df)  →  将 stock 的全部历史 DataFrame 存入 _history[code]
        patch_last_bar() → 在逐日循环中，用当天收盘价覆盖最后一行
                           （避免 concat 产生重复行）
        on_bar(code)     → 在预热后的 _history 上运算，读取最后一行作为「今日」

    这样 on_bar() 可以正确读取完整历史进行技术指标计算，同时「今日」
    的值是最新的当天值（由 patch_last_bar 注入）。
    """

    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params
        self._history: dict[str, pd.DataFrame] = {}

    def warm(self, stock_code: str, bars: pd.DataFrame) -> None:
        """用历史数据（截止到回测起始日之前）热启动策略。"""
        self._history[stock_code] = bars.copy()

    def patch_last_bar(self, stock_code: str, bar: pd.Series) -> None:
        """用当日数据覆盖 _history[stock_code] 的最后一行。

        在 run() 逐日循环中，每处理一天调用一次。
        这比 concat 效率高，且避免行重复导致 rolling 计算错误。
        """
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
    def on_bar(self, stock_code: str) -> Optional[Signal]:
        """每根柱调用一次（基于 patch_last_bar 后的 _history）。"""
        raise NotImplementedError

    def signals(self, stock_codes: list[str]) -> list[Signal]:
        signals = []
        for code in stock_codes:
            sig = self.on_bar(code)
            if sig is not None:
                signals.append(sig)
        return signals

    def clear(self) -> None:
        self._history.clear()


class TrendFollowingStrategy(Strategy):
    """趋势跟踪策略 — 双均线金叉死叉。

    参数:
        fast_period: 快线周期 (默认 5)
        slow_period: 慢线周期 (默认 20)
        position_size: 每只股票的仓位比例 (默认 0.95)
    """

    def __init__(self, params: dict):
        super().__init__("trend_following", params)
        self._holdings: set[str] = set()

    def on_bar(self, stock_code: str) -> Optional[Signal]:
        hist = self._history.get(stock_code)
        if hist is None or len(hist) < max(self.params.get("slow_period", 20), 2):
            return None

        fast = self.params.get("fast_period", 5)
        slow = self.params.get("slow_period", 20)
        closes = hist["close"]

        ma_fast = closes.rolling(window=fast).mean()
        ma_slow = closes.rolling(window=slow).mean()

        curr_fast = ma_fast.iloc[-1]
        curr_slow = ma_slow.iloc[-1]
        prev_fast = ma_fast.iloc[-2]
        prev_slow = ma_slow.iloc[-2]

        if pd.isna(curr_fast) or pd.isna(curr_slow):
            return None

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
    """均值回归策略 — RSI 超买超卖 + 布林带。

    参数:
        rsi_period: RSI 周期 (默认 14)
        oversold: 超卖阈值 (默认 30)
        overbought: 超买阈值 (默认 70)
        lookback: 布林带回看周期 (默认 20)
        bb_std: 布林带标准差倍数 (默认 2.0)
    """

    def __init__(self, params: dict):
        super().__init__("mean_reversion", params)
        self._holdings: set[str] = set()

    def on_bar(self, stock_code: str) -> Optional[Signal]:
        hist = self._history.get(stock_code)
        min_bars = max(self.params.get("rsi_period", 14), self.params.get("lookback", 20))
        if hist is None or len(hist) < min_bars:
            return None

        closes = hist["close"]
        rsi_period = self.params.get("rsi_period", 14)
        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)
        lookback = self.params.get("lookback", 20)
        bb_std = self.params.get("bb_std", 2.0)

        # RSI（使用 indicators 中的实现）
        from .indicators import rsi as calc_rsi
        rsi_val = calc_rsi(closes, rsi_period)

        # 布林带
        bb_mid = closes.rolling(window=lookback).mean()
        bb_std_val = closes.rolling(window=lookback).std()
        bb_upper = bb_mid + bb_std * bb_std_val
        bb_lower = bb_mid - bb_std * bb_std_val

        curr_rsi = rsi_val.iloc[-1]
        curr_close = closes.iloc[-1]
        curr_bb_lower = bb_lower.iloc[-1]
        curr_bb_upper = bb_upper.iloc[-1]

        if pd.isna(curr_rsi) or pd.isna(curr_bb_lower):
            return None

        # RSI < oversold 且价格触及布林带下轨 → 买入
        if curr_rsi < oversold and curr_close <= curr_bb_lower:
            if stock_code not in self._holdings:
                return Signal(stock_code, "buy",
                             f"rsi={curr_rsi:.1f}<{oversold} bb_lower_hit")

        # RSI > overbought 且价格触及布林带上轨 → 卖出
        if curr_rsi > overbought and curr_close >= curr_bb_upper:
            if stock_code in self._holdings:
                self._holdings.discard(stock_code)
                return Signal(stock_code, "sell",
                             f"rsi={curr_rsi:.1f}>{overbought} bb_upper_hit")

        # 中性退出（RSI 回归 40-60 区间）
        if stock_code in self._holdings:
            if 40 < curr_rsi < 60:
                self._holdings.discard(stock_code)
                return Signal(stock_code, "sell", f"rsi={curr_rsi:.1f} mean_reversion_exit")

        return None
