#!/usr/bin/env python3
"""回测引擎 CLI 入口 + 网格搜索。

Usage examples:
  # 单次回测（模拟数据，50只股票）
  python3 -m services.quant.backtest.runner \\
    --mode mock --n-stocks 50 --start 2023-01-01 --end 2024-12-31 \\
    --strategy ma_cross --fast 5 --slow 20 \\
    --initial-cash 100000

  # 单次回测（真实数据库）
  python3 -m services.quant.backtest.runner \\
    --mode db --codes 000001.SZ,600000.SH \\
    --start 2024-01-01 --end 2025-04-18 \\
    --strategy rsi --rsi-period 14 --oversold 30 --overbought 70

  # 网格搜索（MA 参数优化）
  python3 -m services.quant.backtest.runner \\
    --mode mock --n-stocks 50 --start 2023-01-01 --end 2024-12-31 \\
    --strategy ma_cross \\
    --grid fast:5-10-15-20,slow:20-30-40-60 \\
    --output /root/.openclaw/workspace/quantitativeinvest/backtest_results/grid_search.json

  # 网格搜索（RSI 参数优化）
  python3 -m services.quant.backtest.runner \\
    --mode mock --n-stocks 50 --start 2023-01-01 --end 2024-12-31 \\
    --strategy rsi \\
    --grid rsi_period:7-10-14-21,oversold:20-25-30,overbought:65-70-75

JSON 输出格式（latest_results.json）:
{
  "run_id": "run_xxx",
  "strategy_name": "ma_cross",
  "strategy_type": "trend_following",
  "params": {"fast_period": 5, "slow_period": 20},
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "equity_curve": [{"date": "2023-01-01", "value": 100.0}, ...],
  "total_return_pct": 42.8,
  "sharpe_ratio": 1.52,
  "max_drawdown_pct": -8.5,
  "win_rate_pct": 58.2,
  ...
}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from itertools import product

# Add project root to path so 'from services.xxx' imports resolve correctly
_repo_root = str(Path(__file__).parents[3])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from services.quant.backtest.engine import BacktestEngine
from services.quant.backtest.mock_data import make_mock_datafeed
from services.quant.backtest.datafeed import DBDataFeed
from services.quant.backtest.strategies import TrendFollowingStrategy, MeanReversionStrategy


STRATEGY_MAP = {
    "ma_cross": ("trend_following", TrendFollowingStrategy),
    "rsi": ("mean_reversion", MeanReversionStrategy),
}


def parse_grid(grid_str: str) -> dict[str, list]:
    """解析网格参数字符串，如 'fast:5-10,slow:20-30-60' → {fast:[5,10], slow:[20,30,60]}"""
    result = {}
    for segment in grid_str.split(","):
        if ":" not in segment:
            continue
        key, values_str = segment.split(":", 1)
        values = [int(v) if v.isdigit() else float(v) for v in values_str.split("-")]
        result[key.strip()] = values
    return result


def grid_to_param_dicts(grid: dict[str, list]) -> list[dict]:
    """将 grid 展开为参数字典列表。"""
    keys = list(grid.keys())
    values = list(grid.values())
    result = []
    for combo in product(*values):
        result.append(dict(zip(keys, combo)))
    return result


def run_single(
    engine: BacktestEngine,
    strategy_cls,
    params: dict,
    stock_codes: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float,
) -> dict:
    """执行单次回测。"""
    strategy = strategy_cls(params)
    result = engine.run_and_write(
        stock_codes=stock_codes,
        strategy=strategy,
        start_date=start_date,
        end_date=end_date,
    )
    return result


def build_strategy_params(strategy_name: str, args: argparse.Namespace) -> dict:
    """从命令行参数构建策略参数。"""
    if strategy_name == "ma_cross":
        return {
            "fast_period": getattr(args, "fast", 5),
            "slow_period": getattr(args, "slow", 20),
        }
    elif strategy_name == "rsi":
        return {
            "rsi_period": getattr(args, "rsi_period", 14),
            "oversold": getattr(args, "oversold", 30),
            "overbought": getattr(args, "overbought", 70),
            "lookback": getattr(args, "lookback", 20),
            "bb_std": getattr(args, "bb_std", 2.0),
        }
    return {}


def main():
    parser = argparse.ArgumentParser(description="QuantitativeInvest Backtest Runner")
    parser.add_argument("--mode", choices=["mock", "db"], default="mock",
                       help="数据源模式: mock(模拟) 或 db(PostgreSQL)")
    parser.add_argument("--n-stocks", type=int, default=50,
                       help="模拟模式下的股票数量")
    parser.add_argument("--codes", type=str, default="",
                       help="数据库模式下的股票代码，逗号分隔")
    parser.add_argument("--start", type=str, default="2023-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--strategy", choices=["ma_cross", "rsi"], default="ma_cross")
    parser.add_argument("--grid", type=str, default="",
                       help="网格搜索参数，如 'fast:5-10-15,slow:20-30-60'")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--output", type=str,
                       default="/root/.openclaw/workspace/quantitativeinvest/backtest_results/latest_results.json")
    parser.add_argument("--output-grid", type=str,
                       default="/root/.openclaw/workspace/quantitativeinvest/backtest_results/grid_results.json")
    parser.add_argument("--position-pct", type=float, default=0.95,
                       help="每次买入的仓位比例（相对于现金）")
    # MA Cross 参数
    parser.add_argument("--fast", type=int, default=5,
                       help="MA快线周期 (ma_cross 策略)")
    parser.add_argument("--slow", type=int, default=20,
                       help="MA慢线周期 (ma_cross 策略)")
    # RSI 参数
    parser.add_argument("--rsi-period", type=int, default=14,
                       help="RSI周期 (rsi 策略)")
    parser.add_argument("--oversold", type=int, default=30,
                       help="RSI超卖阈值 (rsi 策略)")
    parser.add_argument("--overbought", type=int, default=70,
                       help="RSI超买阈值 (rsi 策略)")
    parser.add_argument("--lookback", type=int, default=20,
                       help="布林带回看周期 (rsi 策略)")
    parser.add_argument("--bb-std", type=float, default=2.0,
                       help="布林带标准差倍数 (rsi 策略)")
    parser.add_argument("--write-benchmark", action="store_true",
                       help="同时生成基准收益率指标")

    args = parser.parse_args()

    # 生成股票代码
    if args.mode == "mock":
        stock_codes = [f"STOCK{i:04d}" for i in range(1, args.n_stocks + 1)]
        print(f"[Mock] Generating data for {args.n_stocks} stocks, {args.start}–{args.end}")
        data_feed = make_mock_datafeed(stock_codes, args.start, args.end)
    else:
        codes_str = args.codes.strip()
        stock_codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        print(f"[DB] Loading {len(stock_codes)} stocks from PostgreSQL, {args.start}–{args.end}")
        data_feed = DBDataFeed()

    strategy_key, strategy_cls = STRATEGY_MAP[args.strategy]

    # 网格搜索
    grid = parse_grid(args.grid) if args.grid else None

    engine = BacktestEngine(
        data_feed=data_feed,
        initial_cash=args.initial_cash,
        position_pct=args.position_pct,
    )

    if grid:
        print(f"[Grid Search] {len(grid)} dimensions, expanding to {len(grid_to_param_dicts(grid))} runs")
        all_results = []
        best = None
        best_return = -999.0

        for param_dict in grid_to_param_dicts(grid):
            strategy = strategy_cls(param_dict)
            result = engine.run_and_write(
                stock_codes=stock_codes,
                strategy=strategy,
                start_date=args.start,
                end_date=args.end,
            )
            all_results.append(result)
            ret = result.get("total_return_pct", 0)
            print(f"  params={param_dict} → return={ret:.2f}% sharpe={result.get('sharpe_ratio', 0):.2f}")
            if ret > best_return:
                best_return = ret
                best = result

        # 写网格搜索结果
        Path(args.output_grid).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_grid).write_text(json.dumps(all_results, indent=2, ensure_ascii=False, default=str))
        print(f"[Grid Search] Done. Best return={best_return:.2f}%, best_params={best.get('params')}")

        # 用最优参数更新 latest_results.json
        if best:
            engine._result = best
            engine.write_results(args.output)
            print(f"[Grid Search] Best result written to {args.output}")
    else:
        # 单次回测
        strategy_params = build_strategy_params(args.strategy, args)
        strategy = strategy_cls(strategy_params)
        result = engine.run_and_write(
            stock_codes=stock_codes,
            strategy=strategy,
            start_date=args.start,
            end_date=args.end,
        )
        print(f"\n[Backtest Done] strategy={args.strategy} params={strategy_params}")
        print(f"  total_return={result.get('total_return_pct', 0):.2f}%")
        print(f"  sharpe={result.get('sharpe_ratio', 0):.2f}")
        print(f"  max_drawdown={result.get('max_drawdown_pct', 0):.2f}%")
        print(f"  win_rate={result.get('win_rate_pct', 0):.2f}%")
        print(f"  total_trades={result.get('total_trades', 0)}")
        print(f"  equity_points={len(result.get('equity_curve', []))}")

    if args.mode == "db":
        data_feed.close()


if __name__ == "__main__":
    main()