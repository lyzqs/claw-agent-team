"""Strategy Parameter Optimizer — Grid Search + Grafana Metrics (M8)."""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from services.quant.backtest.datafeed import DBDataFeed, CSVDataFeed, MemoryDataFeed
from services.quant.backtest.mock_data import generate_multi_stock_data
from services.quant.backtest.engine import BacktestEngine
from services.quant.backtest.strategies import TrendFollowingStrategy, MeanReversionStrategy

logger = logging.getLogger("optimizer")


STRATEGY_MAP = {
    "trend_following": TrendFollowingStrategy,
    "mean_reversion": MeanReversionStrategy,
}


@dataclass
class BacktestConfig:
    """回测配置。"""
    strategy_name: str = "trend_following"
    start_date: str = "2023-01-01"
    end_date: str = "2024-12-31"
    stock_codes: list[str] = field(default_factory=lambda: ["600519"])
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    slippage: float = 0.0001
    position_pct: float = 0.95

    # 参数网格定义: {param_name: [value1, value2, ...]}
    param_grid: dict[str, list] = field(default_factory=dict)

    # 优化目标排序: 'sharpe_ratio' | 'total_return'
    sort_by: str = "sharpe_ratio"

    # 最大并发数（保护小机 / 8GB 内存）
    max_workers: int = 2


@dataclass
class OptimizationResult:
    """单次回测结果。"""
    params: dict
    total_return: float     # 百分比
    sharpe_ratio: float
    max_drawdown: float     # 百分比（负数）
    win_rate: float
    total_trades: int
    equity_curve: list      # 归一化权益曲线
    elapsed_ms: float


class Optimizer:
    """策略参数网格搜索优化器。

    流程:
        1. build_grid() — 从 param_grid 生成所有参数组合
        2. run()        — 逐一回测，结果写入 JSON + Prometheus
        3. print_results() — 按夏普/收益率排序打印
        4. apply_best()  — 将最优参数保存为 JSON，供后续回测使用
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self._results: list[OptimizationResult] = []
        self._best: Optional[OptimizationResult] = None
        self._output_dir = Path("/root/.openclaw/workspace/quantitativeinvest/optimizer")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._metrics_file = self._output_dir / "metrics.json"

    def build_grid(self) -> list[dict]:
        """从 param_grid 生成参数组合列表。"""
        keys = list(self.config.param_grid.keys())
        values = list(self.config.param_grid.values())
        combos = []
        for combo in itertools.product(*values):
            combos.append(dict(zip(keys, combo)))
        return combos

    def _run_single(self, params: dict) -> OptimizationResult:
        """运行单次回测。"""
        t0 = time.time()

        strategy_cls = STRATEGY_MAP.get(self.config.strategy_name, TrendFollowingStrategy)
        strategy = strategy_cls(params)

        # 优先使用真实数据库，无数据时使用模拟数据
        db_feed = DBDataFeed()
        bars = db_feed.get_bars(
            self.config.stock_codes,
            self.config.start_date,
            self.config.end_date,
        )
        if not bars or all(len(v) == 0 for v in bars.values()):
            logger.warning(
                "[Optimizer] DB returns no data — using mock data for "
                f"{self.config.stock_codes} ({self.config.start_date}–{self.config.end_date})"
            )
            bars = generate_multi_stock_data(
                self.config.stock_codes,
                self.config.start_date,
                self.config.end_date,
            )

        memory_feed = MemoryDataFeed(bars)

        engine = BacktestEngine(
            data_feed=memory_feed,
            initial_cash=self.config.initial_cash,
            commission_rate=self.config.commission_rate,
            slippage=self.config.slippage,
            position_pct=self.config.position_pct,
        )
        engine.stock_codes = self.config.stock_codes
        engine.load_data(
            self.config.stock_codes,
            self.config.start_date,
            self.config.end_date,
        )
        engine.warm_strategy(strategy)
        metrics = engine.run()

        elapsed_ms = (time.time() - t0) * 1000
        return OptimizationResult(
            params=params,
            total_return=metrics.get("total_return_pct", 0.0),
            sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
            max_drawdown=metrics.get("max_drawdown_pct", 0.0),
            win_rate=metrics.get("win_rate", 0.0),
            total_trades=metrics.get("total_trades", 0),
            equity_curve=metrics.get("equity_curve", []),
            elapsed_ms=elapsed_ms,
        )

    def run(self) -> list[OptimizationResult]:
        """运行网格搜索，返回所有结果（未排序）。"""
        grid = self.build_grid()
        n = len(grid)

        if n == 0:
            logger.warning("param_grid 为空，跳过优化")
            return []

        # 硬件保护：限制并发数
        max_workers = min(self.config.max_workers, n)
        logger.info(f"[Optimizer] 开始网格搜索: {n} 组参数, max_workers={max_workers}")

        # 限制总组数避免溢出（100 组硬上限）
        MAX_COMBOS = 100
        if n > MAX_COMBOS:
            logger.warning(f"参数组合 {n} > {MAX_COMBOS} 硬上限，自动截断")
            grid = grid[:MAX_COMBOS]

        self._results = []
        for i, params in enumerate(grid):
            result = self._run_single(params)
            self._results.append(result)

            # 写入 Prometheus 指标
            self._write_metrics(i + 1, n, result)

            pct = (i + 1) / n * 100
            logger.info(
                f"[Optimizer] [{i+1}/{n}] {pct:.0f}% "
                f"params={params} "
                f"return={result.total_return:.2f}% "
                f"sharpe={result.sharpe_ratio:.3f} "
                f"dd={result.max_drawdown:.2f}% "
                f"({result.elapsed_ms:.0f}ms)"
            )

        return self._results

    def _write_metrics(self, done: int, total: int, result: OptimizationResult) -> None:
        """写入 Prometheus 格式指标（供 Grafana 读取）。"""
        prefix = f"/root/.openclaw/workspace/quantitativeinvest/metrics/optimizer"
        Path(prefix).mkdir(parents=True, exist_ok=True)

        labels = ",".join(f'{k}="{v}"' for k, v in result.params.items())
        param_count = done

        metrics_lines = [
            f"# HELP optimizer_combinations_total Total param combos tested",
            f"# TYPE optimizer_combinations_total gauge",
            f"optimizer_combinations_total {total}",
            f"# HELP optimizer_done Current completed combos",
            f"# TYPE optimizer_done gauge",
            f"optimizer_done {done}",
            f"# HELP optimizer_sharpe_ratio Sharpe ratio for current combo",
            f"# TYPE optimizer_sharpe_ratio gauge",
            f"optimizer_sharpe_ratio{{{labels}}} {result.sharpe_ratio}",
            f"# HELP optimizer_return_pct Return % for current combo",
            f"# TYPE optimizer_return_pct gauge",
            f"optimizer_return_pct{{{labels}}} {result.total_return}",
            f"# HELP optimizer_max_drawdown_pct Max drawdown % for current combo",
            f"# TYPE optimizer_max_drawdown_pct gauge",
            f"optimizer_max_drawdown_pct{{{labels}}} {result.max_drawdown}",
        ]

        metrics_file = Path(prefix) / f"combo_{param_count}.prom"
        metrics_file.write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")

        # 汇总文件
        summary = {
            "combinations_total": total,
            "done": done,
            "current": result.params,
            "current_sharpe": result.sharpe_ratio,
            "current_return": result.total_return,
        }
        Path(prefix + "/latest.json").write_text(
            __import__("json").dumps(summary), encoding="utf-8"
        )

    def print_results(self, top_n: int = 20) -> None:
        """按 sort_by 排序打印 top N 结果。"""
        if not self._results:
            print("No results yet — run run() first")
            return

        sort_key = "sharpe_ratio" if self.config.sort_by == "sharpe_ratio" else "total_return"
        sorted_results = sorted(
            self._results,
            key=lambda r: getattr(r, sort_key),
            reverse=(sort_key == "total_return"),
        )

        print(f"\n{'='*90}")
        print(f"  Optimization Results — {len(self._results)} combos — sorted by {sort_key}")
        print(f"{'='*90}")
        print(f"{'Rank':<5} {'Sharpe':>8} {'Return%':>9} {'MaxDD%':>8} {'Trades':>7} {'WinRate%':>9}  Params")
        print(f"{'-'*90}")

        for rank, r in enumerate(sorted_results[:top_n], 1):
            tag = " ⭐" if rank == 1 else ""
            print(
                f"{rank:<5} {r.sharpe_ratio:>8.3f} {r.total_return:>9.2f}% "
                f"{r.max_drawdown:>8.2f}% {r.total_trades:>7} "
                f"{r.win_rate:>9.2f}%  {r.params}{tag}"
            )

        print(f"{'-'*90}")
        best = self.best_result
        print(
            f"Best: sharpe={best.sharpe_ratio:.3f} return={best.total_return:.2f}% "
            f"dd={best.max_drawdown:.2f}%  {best.params}"
        )

    @property
    def best_result(self) -> Optional[OptimizationResult]:
        """返回最优结果（按 sort_by 排序第一个）。"""
        if not self._results:
            return None
        if self._best is None:
            sort_key = "sharpe_ratio" if self.config.sort_by == "sharpe_ratio" else "total_return"
            self._best = max(self._results, key=lambda r: getattr(r, sort_key))
        return self._best

    def apply_best(self, output_path: Optional[str] = None) -> dict:
        """将最优参数保存为 JSON，供回测直接加载。"""
        best = self.best_result
        if best is None:
            raise ValueError("No results — run run() first")

        payload = {
            "strategy_name": self.config.strategy_name,
            "best_params": best.params,
            "metrics": {
                "total_return_pct": best.total_return,
                "sharpe_ratio": best.sharpe_ratio,
                "max_drawdown_pct": best.max_drawdown,
                "win_rate": best.win_rate,
                "total_trades": best.total_trades,
            },
            "applied_at": datetime.now().isoformat(),
        }

        path = output_path or str(self._output_dir / "best_params.json")
        Path(path).write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        logger.info(f"[Optimizer] Best params saved to {path}")
        return payload

    def export_all(self, output_path: Optional[str] = None) -> list[dict]:
        """导出所有结果为 JSON（供前端/分析使用）。"""
        path = output_path or str(self._output_dir / "all_results.json")
        data = [
            {
                "params": r.params,
                "total_return_pct": r.total_return,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown_pct": r.max_drawdown,
                "win_rate": r.win_rate,
                "total_trades": r.total_trades,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in self._results
        ]
        Path(path).write_text(__import__("json").dumps(data, indent=2), encoding="utf-8")
        logger.info(f"[Optimizer] All results exported to {path}")
        return data
