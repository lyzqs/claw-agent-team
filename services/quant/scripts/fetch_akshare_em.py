"""AKShare A股日线数据采集脚本（使用 curl_cffi 直连 Eastmoney API）。

Usage:
    # 全量历史数据采集（首次）
    python3 fetch_akshare_em.py --full

    # 增量更新（每日定时）
    python3 fetch_akshare_em.py --incremental

    # 采集特定股票池
    python3 fetch_akshare_em.py --full --stock-codes 000001.SZ,600000.SH

Environment:
    PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE
    QUANT_FETCH_COUNT: 全量模式最多采集多少只股票（默认50）
    QUANT_LOOKBACK_DAYS: 增量模式最多回溯天数（默认5）
    FETCH_RETRY: 单只股票最大重试次数（默认3）
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import psycopg2
from curl_cffi import requests as creq
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ——— Logging ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("quant.em_fetcher")

# ——— Eastmoney API ———
EM_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def stock_code_to_secid(code: str) -> str:
    """将股票代码转换为 Eastmoney secid。
    
    Examples:
        000001.SZ -> 0.000001
        600000.SH -> 1.600000
        000651.SZ -> 0.000651
    """
    code = code.strip().upper()
    suffix = ""
    if "." in code:
        code, suffix = code.split(".", 1)
    code = code.lstrip("0") or "0"
    if suffix == "SH" or code.startswith(("6", "5", "9")):
        return f"1.{code.zfill(6)}"
    return f"0.{code.zfill(6)}"


def fetch_daily_em(
    stock_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "1",  # 1=qfq, 2=hfq, 0=no adjust
    max_retries: int = 3,
) -> Optional[list[dict]]:
    """使用 curl_cffi 直连 Eastmoney API 抓取日线数据。

    Returns list of dict rows or None（失败时）
    """
    secid = stock_code_to_secid(stock_code)
    start = start_date or "19700101"
    end = end_date or datetime.now().strftime("%Y%m%d")

    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": adjust,
        "secid": secid,
        "beg": start,
        "end": end,
        "lmt": "1000000",
    }

    for attempt in range(max_retries):
        try:
            r = creq.get(
                EM_BASE, params=params,
                impersonate="chrome", timeout=20,
            )
            if r.status_code != 200:
                time.sleep(2)
                continue

            import json
            data = r.json()
            if not data or "data" not in data or not data["data"]:
                return None

            klines = data["data"].get("klines", [])
            if not klines:
                return None

            # kline format: date,open,close,high,low,volume,amount,amp,pct,chg,turnover
            records = []
            for line in klines:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                records.append({
                    "stock_code": stock_code,
                    "trade_date": parts[0],
                    "open": float(parts[1]) if parts[1] else None,
                    "high": float(parts[3]) if parts[3] else None,
                    "low": float(parts[4]) if parts[4] else None,
                    "close": float(parts[2]) if parts[2] else None,
                    "volume": int(float(parts[5])) if parts[5] else None,
                    "amount": float(parts[6]) if len(parts) > 6 and parts[6] else None,
                    "turnover_rate": float(parts[10]) if len(parts) > 10 and parts[10] else None,
                    "adjusted_close": float(parts[2]) if parts[2] else None,
                })
            return records

        except Exception as e:
            logger.debug("Attempt %d/%d for %s: %s", attempt+1, max_retries, stock_code, e)
            if attempt < max_retries - 1:
                time.sleep(3)
    return None


# ——— Database helpers ———
def get_pg_conn():
    import os
    from services.quant.config import PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DATABASE
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        database=PG_DATABASE,
        connect_timeout=10,
    )


def get_engine() -> Engine:
    from services.quant.config import DB_URL
    return create_engine(DB_URL, pool_pre_ping=True, pool_size=1, max_overflow=3)


def save_to_db(conn, rows: list[dict]) -> tuple[int, int]:
    """Upsert list of rows into stock_daily using ON CONFLICT."""
    if not rows:
        return 0, 0
    inserted = updated = 0
    cur = conn.cursor()
    for row in rows:
        try:
            cur.execute("""
                INSERT INTO stock_daily (stock_code, trade_date, open, high, low, close, volume, adjusted_close, turnover_rate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, volume = EXCLUDED.volume,
                    adjusted_close = EXCLUDED.adjusted_close, turnover_rate = EXCLUDED.turnover_rate
            """, (
                row["stock_code"], row["trade_date"],
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["adjusted_close"], row.get("turnover_rate"),
            ))
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            logger.debug("DB insert error: %s", e)
    conn.commit()
    cur.close()
    return inserted, updated


# ——— Stock list ———
def get_all_a_stock_codes(exchange: Optional[str] = None, count: int = 50) -> list[tuple[str, str]]:
    """获取 A 股股票列表（使用 akshare stock_info_a_code_name）。"""
    try:
        import akshare as ak
        stock_info_df = ak.stock_info_a_code_name()
        codes = stock_info_df["code"].tolist()
        names = stock_info_df["name"].tolist()
    except Exception as e:
        logger.warning("无法获取股票列表: %s", e)
        return []

    if exchange:
        if exchange.upper() == "SZ":
            codes = [c for c in codes if re.match(r"^0[0-5]", c)]
        elif exchange.upper() == "SH":
            codes = [c for c in codes if re.match(r"^6[0-9]", c)]

    codes = codes[:count]
    names = names[:len(codes)]
    return list(zip(codes, names))


# ——— Main ———
def fetch_full(
    conn,
    stock_codes: Optional[list[str]] = None,
    exchange: Optional[str] = None,
    count: int = 50,
) -> dict:
    """全量采集：首次运行。"""
    all_stocks = stock_codes or []
    if not all_stocks:
        all_stocks = get_all_a_stock_codes(exchange=exchange, count=count)

    stats = {"stocks": 0, "records": 0, "errors": 0}
    for entry in all_stocks:
        code = entry[0] if isinstance(entry, tuple) else entry
        name = entry[1] if isinstance(entry, tuple) else ""
        stats["stocks"] += 1
        rows = fetch_daily_em(code, adjust="1")
        if rows:
            ins, upd = save_to_db(conn, rows)
            stats["records"] += ins + upd
            logger.info("  [%s] %s: %d 条（+%d/-%d）", code, name, ins+upd, ins, upd)
        else:
            stats["errors"] += 1
            logger.warning("  [%s] %s: 抓取失败", code, name)
        time.sleep(0.3)
    return stats


def fetch_incremental(
    conn,
    stock_codes: Optional[list[str]] = None,
    lookback_days: int = 5,
) -> dict:
    """增量更新：只抓取最新交易日数据。"""
    all_stocks = stock_codes or []
    if not all_stocks:
        all_stocks = [c for c, _ in get_all_a_stock_codes(count=50)]

    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    stats = {"stocks": 0, "records": 0, "errors": 0}
    for code in all_stocks:
        stats["stocks"] += 1
        rows = fetch_daily_em(code, start_date=start, end_date=end, adjust="1")
        if rows:
            ins, upd = save_to_db(conn, rows)
            stats["records"] += ins + upd
            logger.info("  %s: %d 条", code, ins+upd)
        else:
            stats["errors"] += 1
            logger.warning("  %s: 失败", code)
        time.sleep(0.3)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Eastmoney A股日线数据采集（curl_cffi）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--full", action="store_true", help="全量历史数据采集")
    group.add_argument("--incremental", action="store_true", help="增量更新最新交易日")
    parser.add_argument("--stock-codes", type=str, help="逗号分隔的股票代码，如 000001.SZ,600000.SH")
    parser.add_argument("--exchange", type=str, choices=["SZ", "SH"], help="按交易所采集")
    parser.add_argument("--count", type=int, default=50, help="最多采集多少只（默认50）")
    parser.add_argument("--lookback-days", type=int, default=5, help="增量回溯天数（默认5）")
    args = parser.parse_args()

    conn = get_pg_conn()
    stock_codes = [s.strip() for s in args.stock_codes.split(",")] if args.stock_codes else None

    if args.full:
        stats = fetch_full(conn, stock_codes=stock_codes, exchange=args.exchange, count=args.count)
    else:
        stats = fetch_incremental(conn, stock_codes=stock_codes, lookback_days=args.lookback_days)

    logger.info("采集完成: %d 只股票, %d 条记录, %d 只失败",
                stats["stocks"], stats["records"], stats["errors"])
    conn.close()


if __name__ == "__main__":
    main()
