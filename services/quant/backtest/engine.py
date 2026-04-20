"""回测引擎核心 — 事件驱动型日线回测。"""
from __future__ import annotations

import json
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional

from .datafeed import DataFeed
from .portfolio import Portfolio
from .strategies import Strategy

logger = logging.getLogger("backtest.engine")


class BacktestEngine:
    """事件驱动型日线回测引擎。

    流程：
        1. load_data()    — 加载数据
        2. warm_strategy() — 用历史数据预热策略（仅在循环前调用一次）
        3. run()          — 逐日推进，生成信号，执行交易
        4. write_results() — 输出 JSON
    """

    def __init__(
        self,
        data_feed: DataFeed,
        initial_cash: float = 100_000.0,
        commission_rate: float = 0.0003,
        slippage: float = 0.0001,
        position_pct: float = 0.95,
    ):
        self.data_feed = data_feed
        self.initial_cash = initial_cash
        self.position_pct = position_pct
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.portfolio: Optional[Portfolio] = None
        self.strategy: Optional[Strategy] = None
        self.stock_codes: list[str] = []
        self.start_date: Optional[str] = None
        self.end_date: Optional[str] = None
        self._all_dates: list[pd.Timestamp] = []
        self._bars: dict[str, pd.DataFrame] = {}
        self._result: Optional[dict] = None

    def load_data(
        self,
        stock_codes: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        """加载数据到内存。"""
        self.stock_codes = stock_codes
        self.start_date = start_date
        self.end_date = end_date
        self._bars = self.data_feed.get_bars(stock_codes, start_date, end_date)
        logger.info(
            f"Loaded data for {len(self._bars)}/{len(stock_codes)} stocks, "
            f"dates {start_date}–{end_date}"
        )

        # 合并所有日期索引
        all_dates: set[pd.Timestamp] = set()
        for df in self._bars.values():
            all_dates.update(df["trade_date"].tolist())
        self._all_dates = sorted(all_dates)
        logger.info(f"Total trading days: {len(self._all_dates)}")

    def warm_strategy(self, strategy: Strategy) -> None:
        """用全部历史数据热启动策略。

        调用 strategy.warm(code, df) 让策略预计算技术指标（MA/RSI 等），
        后续 run() 中 on_bar() 通过 date_idx 查表实现 O(1) 指标获取。
        """
        self.strategy = strategy
        for code, df in self._bars.items():
            strategy.warm(code, df.copy())
        logger.info(
            f"Strategy '{strategy.name}' warmed: "
            f"{len(self._all_dates)} trading days × {len(self._bars)} stocks"
        )

    def run(self) -> dict:
        """运行回测，逐日推进。返回结果字典。"""
        if not self._bars or not self.strategy:
            raise ValueError("Must call load_data() and warm_strategy() before run()")

        self.portfolio = Portfolio(
            initial_cash=self.initial_cash,
            commission_rate=self.commission_rate,
            slippage=self.slippage,
        )
        self.portfolio.record_equity(self._all_dates[0])

        holding: dict[str, float] = {}  # 当前持仓量
        strategy = self.strategy
        bars = self._bars
        all_dates = self._all_dates

        # 预计算指标在 warm_strategy 中已完成，on_bar() 通过 date_idx 查表
        for date_idx, date_ts in enumerate(all_dates):
            trade_date = date_ts.date() if hasattr(date_ts, "date") else date_ts

            for code in self.stock_codes:
                df = bars.get(code)
                if df is None or df.empty:
                    continue
                row_mask = df["trade_date"] == date_ts
                if not row_mask.any():
                    continue
                bar = df.loc[row_mask].iloc[0]

                # 通过 date_idx 查预计算指标，O(1)
                signal = strategy.on_bar(code, date_idx)
                if signal is None:
                    continue

                stock_close = float(bar["close"])

                if signal.action == "buy":
                    if holding.get(code, 0.0) > 0:
                        continue
                    alloc = self.portfolio.cash * self.position_pct
                    if alloc <= 0:
                        continue
                    qty = float(int(alloc / stock_close / 100) * 100)  # 按手取整
                    if qty <= 0:
                        continue
                    if self.portfolio.buy(code, trade_date, stock_close, qty):
                        holding[code] = qty

                elif signal.action == "sell":
                    if holding.get(code, 0.0) <= 0:
                        continue
                    qty = holding[code]
                    if self.portfolio.sell(code, trade_date, stock_close, qty):
                        holding[code] = 0.0

            # 更新收盘价 + 记录权益
            for code, df in bars.items():
                row_mask = df["trade_date"] == date_ts
                if row_mask.any():
                    self.portfolio.update_price(code, trade_date, float(df.loc[row_mask, "close"].iloc[0]))
            self.portfolio.record_equity(trade_date)

        # 回测结束时，平掉所有剩余仓位（计入 closed_trades 以便统计）
        last_date = all_dates[-1].date() if hasattr(all_dates[-1], "date") else all_dates[-1]
        for code, qty in list(holding.items()):
            if qty <= 0:
                continue
            df = bars.get(code)
            if df is None or df.empty:
                continue
            row_mask = df["trade_date"] == all_dates[-1]
            if not row_mask.any():
                continue
            price = float(df.loc[row_mask, "close"].iloc[0])
            self.portfolio.sell(code, last_date, price, qty)
            holding[code] = 0.0

        # === 计算指标 ===
        metrics = self.portfolio.compute_metrics()
        metrics["run_id"] = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        metrics["strategy_name"] = self.strategy.name
        metrics["strategy_type"] = self.strategy.name
        metrics["params"] = self.strategy.params
        metrics["start_date"] = self.start_date
        metrics["end_date"] = self.end_date
        metrics["stock_count"] = len(self._bars)
        metrics["trading_days"] = len(self._all_dates)

        # 权益曲线（归一化到初始=100）
        equity_curve = [
            {"date": str(d), "value": round(v, 4)}
            for d, v in self.portfolio.equity_history
        ]
        if equity_curve:
            base = equity_curve[0]["value"]
            for item in equity_curve:
                item["value"] = round(item["value"] / base * 100, 4)
        metrics["equity_curve"] = equity_curve

        # 基准收益率
        if equity_curve:
            first_code = next(iter(self._bars.keys()))
            first_df = self._bars[first_code]
            if not first_df.empty:
                first_close = float(first_df["close"].iloc[0])
                last_close = float(first_df["close"].iloc[-1])
                benchmark_return = (last_close / first_close - 1) * 100
                metrics["benchmark_return_pct"] = round(benchmark_return, 2)

        return metrics

    def write_results(self, output_path: str | Path) -> None:
        """将结果写入 JSON 文件（供 QI Metrics Exporter 读取）。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._result is None:
            raise ValueError("run() must be called before write_results()")
        output_path.write_text(
            json.dumps([self._result], indent=2, ensure_ascii=False, default=str)
        )
        logger.info(f"Results written to {output_path}")

    def run_and_write(
        self,
        stock_codes: list[str],
        strategy,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        output_path: str | Path = "/root/.openclaw/workspace/quantitativeinvest/backtest_results/latest_results.json",
    ) -> dict:
        """一键运行并写入结果。"""
        self.load_data(stock_codes, start_date, end_date)
        self.warm_strategy(strategy)
        self._result = self.run()
        self.write_results(output_path)
        return self._result
