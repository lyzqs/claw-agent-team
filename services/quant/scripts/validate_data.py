"""QuantDB 数据质量验证与数据清洗工具。

验证规则（对应 PM 规范第 4 节）：
1. 停牌检测：volume=0 AND amount=0
2. 价格异常：close=0 OR close<low OR close>high
3. 缺失值：任一 OHLCV 字段为空

Usage:
    python3 validate_data.py --check-quality
    python3 validate_data.py --fix-suspensions
    python3 validate_data.py --report
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

sys.path.insert(0, ".")
from services.quant.api import QuantDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("quant.validator")


def check_missing_values(db: QuantDB) -> list:
    """检查缺失值（任一 OHLCV 字段为空）。"""
    rows = db.engine.connect().execute(
        __import__("sqlalchemy").text("""
            SELECT stock_code, trade_date::text, open, high, low, close, volume
            FROM stock_daily
            WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL
        """)
    ).fetchall()
    return rows


def check_price_anomalies(db: QuantDB) -> list:
    """检查价格异常：close<low 或 close>high。"""
    rows = db.engine.connect().execute(
        __import__("sqlalchemy").text("""
            SELECT stock_code, trade_date::text, open, high, low, close
            FROM stock_daily
            WHERE close IS NOT NULL AND low IS NOT NULL AND high IS NOT NULL
              AND (close < low OR close > high)
        """)
    ).fetchall()
    return rows


def check_suspended(db: QuantDB) -> list:
    """检查停牌数据：volume=0 AND amount=0 或 amount IS NULL。"""
    rows = db.engine.connect().execute(
        __import__("sqlalchemy").text("""
            SELECT stock_code, trade_date::text, open, high, low, close, volume, amount
            FROM stock_daily
            WHERE (volume = 0 OR volume IS NULL) 
              AND (amount IS NULL OR amount = 0)
            ORDER BY stock_code, trade_date
        """)
    ).fetchall()
    return rows


def check_zero_prices(db: QuantDB) -> list:
    """检查价格为0的记录（需删除）。"""
    rows = db.engine.connect().execute(
        __import__("sqlalchemy").text("""
            SELECT stock_code, trade_date::text, open, high, low, close
            FROM stock_daily
            WHERE close = 0 OR close IS NULL
        """)
    ).fetchall()
    return rows


def report(db: QuantDB):
    """生成数据质量报告。"""
    logger.info("=== 数据质量验证报告 ===\n")

    stats = db.get_stats()
    logger.info(f"覆盖股票数: {stats['stock_count']}")
    logger.info(f"总记录数: {stats['total_records']}")
    logger.info(f"最早日期: {stats['earliest_date']}")
    logger.info(f"最新日期: {stats['latest_date']}")
    logger.info(f"交易天数: {stats['trading_days']}")

    # 2年覆盖检查
    if stats["earliest_date"]:
        days_covered = (stats["latest_date"] - stats["earliest_date"]).days
        logger.info(f"日期跨度: {days_covered} 天 (~{days_covered/365:.1f} 年)")
        if days_covered >= 730:
            logger.info("  ✅ 历史数据覆盖 ≥2 年")
        else:
            logger.warning(f"  ⚠️ 历史数据覆盖不足2年（当前 {days_covered/365:.1f} 年）")
    logger.info("")

    # 各维度检查
    missing = check_missing_values(db)
    logger.info(f"缺失值（OHLCV空）: {len(missing)} 条 {'✅ 无' if len(missing)==0 else '❌ 有异常'}")

    zero_prices = check_zero_prices(db)
    logger.info(f"价格为0记录: {len(zero_prices)} 条 {'✅ 无' if len(zero_prices)==0 else '❌ 有异常'}")
    if zero_prices:
        for r in zero_prices[:5]:
            logger.warning(f"    {r[0]} {r[1]} close={r[5]}")

    anomalies = check_price_anomalies(db)
    logger.info(f"价格异常（close<low/close>high）: {len(anomalies)} 条 {'✅ 无' if len(anomalies)==0 else '❌ 有异常'}")
    if anomalies:
        for r in anomalies[:5]:
            logger.warning(f"    {r[0]} {r[1]} open={r[2]} high={r[3]} low={r[4]} close={r[5]}")

    suspended = check_suspended(db)
    logger.info(f"疑似停牌（volume=0/amount=0）: {len(suspended)} 条")
    if suspended:
        for r in suspended[:5]:
            logger.info(f"    {r[0]} {r[1]} vol={r[6]} amt={r[7]}")

    logger.info("\n=== 结论 ===")
    issues = len(missing) + len(zero_prices) + len(anomalies)
    if issues == 0:
        logger.info("✅ 所有数据质量检查通过，无异常记录")
    else:
        logger.warning(f"❌ 发现 {issues} 条异常数据")


def fix_suspensions(db: QuantDB):
    """标记或清理停牌数据。"""
    cur = db.engine.connect()
    # 将停牌记录的 volume 设为 NULL（表示无交易）
    result = cur.execute(
        __import__("sqlalchemy").text("""
            UPDATE stock_daily
            SET volume = NULL, amount = NULL
            WHERE (volume = 0 OR volume IS NULL)
              AND (amount IS NULL OR amount = 0)
        """)
    )
    logger.info(f"已清理 {result.rowcount} 条停牌记录（volume/amount 置 NULL）")
    db.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="QuantDB 数据质量验证")
    parser.add_argument("--check-quality", action="store_true", help="检查数据质量")
    parser.add_argument("--fix-suspensions", action="store_true", help="清理停牌数据")
    parser.add_argument("--report", action="store_true", help="生成报告")
    args = parser.parse_args()

    if args.check_quality or args.report or not any(vars(args).values()):
        db = QuantDB()
        report(db)
        db.close()

    if args.fix_suspensions:
        db = QuantDB()
        fix_suspensions(db)
        db.close()


if __name__ == "__main__":
    main()