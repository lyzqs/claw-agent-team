"""Paper Trading PaperRisk CLI Runner.

Usage:
    python3 -m services.quant.paper_trading.runner --status
    python3 -m services.quant.paper_trading.runner --buy 0.001
    python3 -m services.quant.paper_trading.runner --sell 0.001
    python3 -m services.quant.paper_trading.runner --export
    python3 -m services.quant.paper_trading.runner --summary
"""
import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from services.quant.paper_trading.interface import PaperTradingInterface
from services.quant.paper_trading.risk import PaperRisk


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Interface (M10)")
    parser.add_argument("--buy", type=float, help="买入数量 (BTC)")
    parser.add_argument("--sell", type=float, help="卖出数量 (BTC)")
    parser.add_argument("--amount", type=float, help="交易数量 (BTC, 用于 buy/sell)")
    parser.add_argument("--price", type=float, help="指定价格")
    parser.add_argument("--dry-run", action="store_true", help="只做风控检查，不执行")
    parser.add_argument("--status", action="store_true", help="查看账户状态")
    parser.add_argument("--summary", action="store_true", help="绩效摘要")
    parser.add_argument("--export", action="store_true", help="导出交易记录")
    parser.add_argument("--export-path", type=str, help="导出文件路径")
    parser.add_argument("--risk-check", action="store_true", help="仅做风控检查")
    parser.add_argument("--data-dir", type=str, default="/root/.openclaw/workspace/btc-quant",
                        help="数据目录")
    parser.add_argument("--run-id", type=str, default="",
                        help="运行ID (用于关联回测信号)")
    parser.add_argument("--no-risk", action="store_true", help="跳过风控检查")

    args = parser.parse_args()

    pti = PaperTradingInterface(
        data_dir=args.data_dir,
        run_id=args.run_id or "cli",
    )

    if args.status:
        portfolio = pti.get_portfolio()
        print("=" * 40)
        print("Paper Trading Account Status")
        print("=" * 40)
        print(f"Run ID:      {portfolio['run_id']}")
        print(f"Cash:        ${portfolio['cash']:.2f}")
        print(f"BTC Holdings: {portfolio['btc_holdings']:.6f} BTC")
        print(f"BTC Avg Price: ${portfolio['btc_avg_price']:.2f}")
        print(f"BTC Value:   ${portfolio['btc_value']:.2f}")
        print(f"Total:       ${portfolio['total_value']:.2f}")
        print(f"Unrealized PnL: ${portfolio['unrealized_pnl']:.2f}")
        print(f"Grid:        {'Enabled' if portfolio['grid_enabled'] else 'Disabled'}")
        print(f"Futures:     {len(portfolio['futures_positions'])} positions")
        print()

        # Risk config display
        risk = PaperRisk(data_dir=args.data_dir)
        cfg = risk.config
        print("Risk Config:")
        print(f"  Max Position %:   {cfg.max_position_pct*100:.0f}%")
        print(f"  Max Single Trade: {cfg.max_single_trade_pct*100:.0f}%")
        print(f"  Stop Loss:        {cfg.stop_loss_pct*100:.0f}%")
        print(f"  Max Trades/Day:   {cfg.max_trades_per_day}")
        print(f"  Min Interval:     {cfg.min_trade_interval_minutes}min")
        print(f"  Dry Run Mode:     {cfg.dry_run}")
        return

    if args.summary:
        summary = pti.get_performance_summary()
        print("=" * 40)
        print("Paper Trading Performance Summary")
        print("=" * 40)
        for k, v in summary.items():
            if isinstance(v, float):
                print(f"{k}: {v:.4f}")
            else:
                print(f"{k}: {v}")
        return

    if args.export:
        export = pti.export_trades(args.export_path)
        print(f"Exported {export['total_trades']} trades to {args.export_path or 'default path'}")
        return

    if args.risk_check:
        amount = args.amount or 0.001
        price = args.price or pti.get_current_price()
        action = "buy" if args.buy else "sell"
        cfg = pti._load_config()
        total_value = cfg.get("cash", 0) + cfg.get("btc_holdings", 0) * price
        risk = PaperRisk(data_dir=args.data_dir)
        result = risk.check_trade(action, "BTC", amount, price, total_value, cfg.get("cash", 0))
        print(f"Risk check [{action.upper()} {amount} BTC @ ${price:.2f}]:")
        print(f"  allowed={result.allowed} level={result.risk_level}")
        print(f"  reason={result.reason}")
        return

    # Buy / Sell actions
    if args.buy is not None or args.sell is not None:
        action = "buy" if args.buy is not None else "sell"
        amount = args.amount or args.buy or args.sell
        dry_run = args.dry_run or args.no_risk

        resp = pti.receive_signal(
            symbol="BTC",
            action=action,
            amount=amount,
            price=args.price,
            dry_run=args.no_risk,  # no_risk skips risk check
        )

        print(f"Signal [{action.upper()}] amount={amount} price={resp.get('price', 'N/A')}")
        print(f"  success={resp['success']}")
        if resp.get('trade_record'):
            tr = resp['trade_record']
            print(f"  executed: {tr['type']} {tr['amount']} @ ${tr['price']:.2f} = ${tr['total']:.2f}")
        elif resp.get('reason'):
            print(f"  reason: {resp['reason']}")
        return

    # No action specified
    parser.print_help()
    print()
    print("Examples:")
    print("  python3 -m services.quant.paper_trading.runner --status")
    print("  python3 -m services.quant.paper_trading.runner --buy 0.001")
    print("  python3 -m services.quant.paper_trading.runner --sell 0.001")
    print("  python3 -m services.quant.paper_trading.runner --risk-check --buy 0.001")
    print("  python3 -m services.quant.paper_trading.runner --export --export-path /tmp/trades.json")


if __name__ == "__main__":
    main()
