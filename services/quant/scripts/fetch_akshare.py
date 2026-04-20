"""AKShare A股日线数据采集脚本。

Usage:
    # 全量历史数据采集（首次）
    python fetch_akshare.py --full

    # 增量更新（每日定时）
    python fetch_akshare.py --incremental

    # 增量更新指定股票池
    python fetch_akshare.py --incremental --stock-codes 000001.SZ,600000.SH

    # 按股票代码范围采集
    python fetch_akshare.py --full --stock-codes 000001.SZ,600000.SH

    # 采集特定交易所全部股票
    python fetch_akshare.py --full --exchange SZ --count 100

    # 采集指数成分股
    python fetch_akshare.py --full --index-components 000852.SH

Environment:
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE
    QUANT_FETCH_COUNT: 全量模式最多采集多少只股票（默认50）
    QUANT_LOOKBACK_DAYS: 增量模式最多回溯天数（默认5）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import akshare as ak
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("quant.fetcher")

# 导入 config
from .config import (
    DB_URL,
    DEFAULT_STOCK_COUNT,
    INCREMENTAL_LOOKBACK_DAYS,
    PG_DATABASE,
    PG_HOST,
    PG_PASSWORD,
    PG_PORT,
    PG_USER,
)


def get_engine() -> Engine:
    return create_engine(DB_URL, pool_pre_ping=True)


def get_all_a_stock_codes(exchange: Optional[str] = None, count: int = 50) -> list[tuple[str, str]]:
    """获取 A 股股票列表，可选按交易所过滤。

    Returns list of (stock_code, name) tuples.
    """
    try:
        stock_info_a_code_name_df = ak.stock_info_a_code_name()
        codes = stock_info_a_code_name_df["code"].tolist()
        names = stock_info_a_code_name_df["name"].tolist()
    except Exception as e:
        logger.warning("无法获取股票列表，降级使用沪深300成分: %s", e)
        try:
            index_df = ak.index_stock_cons_csindex(symbol="000852")
            codes = index_df["品种代码"].tolist()
            names = index_df["名称"].tolist()
        except Exception as e2:
            logger.error("指数成分股也无法获取: %s", e2)
            return []

    if exchange:
        if exchange.upper() == "SZ":
            codes = [c for c in codes if c.startswith(("000", "001", "002", "003"))]
        elif exchange.upper() == "SH":
            codes = [c for c in codes if c.startswith(("600", "601", "603", "605"))]

    if count > 0:
        codes = codes[:count]

    return [(c, n) for c, n in zip(codes, names)]


def fetch_daily_for_stock(
    stock_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
) -> Optional[pd.DataFrame]:
    """抓取单只股票日线数据（前复权）。

    Args:
        stock_code: 股票代码，如 "000001.SZ"
        start_date: 开始日期 "YYYYMMDD" 或 None（全量）
        end_date: 结束日期 "YYYYMMDD" 或 None（今天）
        adjust: 复权类型 "qfq"=前复权 "hfq"=后复权 "None"=不复权

    Returns DataFrame 或 None（失败时）
    """
    try:
        period = "daily"
        start_str = start_date or ""
        end_str = end_date or datetime.now().strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period=period,
            start_date=start_str,
            end_date=end_str,
            adjust=adjust,
        )

        if df is None or df.empty:
            return None

        # AKShare 返回列名（最新版本）：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        # 映射到标准列名
        col_map = {
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "turnover",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "price_change",
            "换手率": "turnover_rate",
        }
        df = df.rename(columns=col_map)

        # 标准化股票代码
        df["stock_code"] = stock_code

        # 确保日期格式
        if not pd.api.types.is_datetime64_any_dtype(df["trade_date"]):
            df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 复权收盘价使用 adjust 参数下的 close
        df["adjusted_close"] = df["close"]

        # volume 可能以万手为单位，转为手
        if df["volume"].dtype == object or df["volume"].max() > 1e8:
            # 检查是否是万手单位
            pass  # AKShare 默认是手，保持原样

        return df[
            ["stock_code", "trade_date", "open", "high", "low", "close",
             "volume", "adjusted_close", "turnover_rate"]
        ]

    except Exception as e:
        logger.debug("抓取 %s 失败: %s", stock_code, e)
        return None


def save_to_db(engine: Engine, df: pd.DataFrame) -> tuple[int, int]:
    """将 DataFrame 批量写入 PostgreSQL（upsert）。"""
    if df is None or df.empty:
        return 0, 0

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

    inserted = 0
    updated = 0

    for _, row in df.iterrows():
        result = engine.connect().execute(
            text("""
                INSERT INTO stock_daily (stock_code, trade_date, open, high, low, close, volume, adjusted_close, turnover_rate)
                VALUES (:stock_code, :trade_date, :open, :high, :low, :close, :volume, :adjusted_close, :turnover_rate)
                ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    adjusted_close = EXCLUDED.adjusted_close,
                    turnover_rate = EXCLUDED.turnover_rate
            """),
            {
                "stock_code": row["stock_code"],
                "trade_date": row["trade_date"],
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
                "adjusted_close": float(row["adjusted_close"]) if pd.notna(row["adjusted_close"]) else None,
                "turnover_rate": float(row["turnover_rate"]) if pd.notna(row["turnover_rate"]) else None,
            }
        )
        if result.rowcount == 1:
            inserted += 1
        else:
            updated += 1

    return inserted, updated


def get_latest_date(engine: Engine, stock_code: str) -> Optional[date]:
    """获取某股票最新已入库日期。"""
    row = engine.execute(
        text("SELECT MAX(trade_date) as latest FROM stock_daily WHERE stock_code = :code"),
        {"code": stock_code},
    ).fetchone()
    if row and row[0]:
        return row[0]
    return None


def fetch_incremental(
    engine: Engine,
    stock_codes: Optional[list[str]] = None,
    lookback_days: int = INCREMENTAL_LOOKBACK_DAYS,
) -> dict:
    """增量更新：只抓取最新交易日数据。"""
    all_stocks = stock_codes or []
    if not all_stocks:
        all_stocks = [s[0] for s in get_all_a_stock_codes(count=DEFAULT_STOCK_COUNT)]

    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    stats = {"stocks": 0, "records": 0, "errors": 0}
    for stock_code in all_stocks:
        stats["stocks"] += 1
        df = fetch_daily_for_stock(stock_code, start_date=start, end_date=end)
        if df is not None:
            ins, upd = save_to_db(engine, df)
            stats["records"] += ins + upd
            logger.info("  %s: %d 条（含 %d 新增, %d 更新）", stock_code, ins + upd, ins, upd)
        else:
            stats["errors"] += 1
            logger.warning("  %s: 抓取失败", stock_code)
        time.sleep(0.5)  # 避免请求过快

    return stats


def fetch_full(
    engine: Engine,
    stock_codes: Optional[list[str]] = None,
    exchange: Optional[str] = None,
    count: int = DEFAULT_STOCK_COUNT,
) -> dict:
    """全量采集：首次运行，抓取全部历史数据。"""
    all_stocks = stock_codes or []
    if not all_stocks:
        all_stocks = get_all_a_stock_codes(exchange=exchange, count=count)

    stats = {"stocks": 0, "records": 0, "errors": 0}
    for stock_code, name in all_stocks:
        stats["stocks"] += 1
        df = fetch_daily_for_stock(stock_code, adjust="qfq")
        if df is not None:
            ins, upd = save_to_db(engine, df)
            stats["records"] += ins + upd
            logger.info("  [%s] %s: %d 条（含 %d 新增, %d 更新）", stock_code, name, ins + upd, ins, upd)
        else:
            stats["errors"] += 1
            logger.warning("  [%s] %s: 抓取失败", stock_code, name)
        time.sleep(0.5)

    return stats


def main():
    parser = argparse.ArgumentParser(description="AKShare A股日线数据采集")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--full", action="store_true", help="全量历史数据采集")
    group.add_argument("--incremental", action="store_true", help="增量更新最新交易日")
    parser.add_argument("--stock-codes", type=str, help="逗号分隔的股票代码列表，如 000001.SZ,600000.SH")
    parser.add_argument("--exchange", type=str, choices=["SZ", "SH"], help="按交易所采集")
    parser.add_argument("--index-components", type=str, help="采集指数成分股，如 000852.SH")
    parser.add_argument("--count", type=int, default=DEFAULT_STOCK_COUNT, help=f"最多采集多少只（默认{DEFAULT_STOCK_COUNT}）")
    parser.add_argument("--lookback-days", type=int, default=INCREMENTAL_LOOKBACK_DAYS, help=f"增量回溯天数（默认{INCREMENTAL_LOOKBACK_DAYS}）")
    args = parser.parse_args()

    engine = get_engine()

    stock_codes = None
    if args.stock_codes:
        stock_codes = [s.strip() for s in args.stock_codes.split(",")]
    elif args.index_components:
        try:
            index_df = ak.index_stock_cons_csindex(symbol=args.index_components)
            stock_codes = index_df["品种代码"].tolist()
            logger.info("指数 %s 成分股: %d 只", args.index_components, len(stock_codes))
        except Exception as e:
            logger.error("获取指数成分股失败: %s", e)
            sys.exit(1)

    if args.full:
        stats = fetch_full(engine, stock_codes=stock_codes, exchange=args.exchange, count=args.count)
    else:
        stats = fetch_incremental(engine, stock_codes=stock_codes, lookback_days=args.lookback_days)

    logger.info("采集完成: %d 只股票, %d 条记录, %d 只失败", stats["stocks"], stats["records"], stats["errors"])
    engine.dispose()


if __name__ == "__main__":
    main()