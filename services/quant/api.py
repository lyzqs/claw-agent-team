"""QuantDB 数据查询接口 — 供回测引擎调用。

Usage:
    from services.quant.api import QuantDB

    db = QuantDB()
    df = db.get_daily("000001.SZ", start_date="20230101", end_date="20241231")
    df = db.get_multiple(["000001.SZ", "600000.SH"], start_date="20240101")
    latest = db.get_latest_trade_date("000001.SZ")
    db.close()
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from typing import Optional, Sequence

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import DB_URL

logger = logging.getLogger("quant.api")


class QuantDB:
    """A股日线数据查询接口。"""

    def __init__(self, db_url: str = DB_URL):
        self.engine: Engine = create_engine(db_url, pool_pre_ping=True)

    def close(self):
        self.engine.dispose()

    def get_daily(
        self,
        stock_code: str,
        start_date: Optional[str | date] = None,
        end_date: Optional[str | date] = None,
        columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """获取单只股票日线数据。

        Args:
            stock_code: 股票代码，如 "000001.SZ"
            start_date: 开始日期 "YYYYMMDD" 或 date 对象
            end_date: 结束日期 "YYYYMMDD" 或 date 对象
            columns: 要返回的列名，None = 返回全部

        Returns:
            DataFrame with columns: stock_code, trade_date, open, high, low, close, volume, adjusted_close, turnover_rate
        """
        sql_columns = ", ".join(columns) if columns else "*"
        params: dict = {"code": stock_code}

        date_clause = ""
        if start_date:
            s = self._to_date_str(start_date)
            date_clause += " AND trade_date >= :start_date"
            params["start_date"] = s
        if end_date:
            e = self._to_date_str(end_date)
            date_clause += " AND trade_date <= :end_date"
            params["end_date"] = e

        sql = f"""
            SELECT {sql_columns}
            FROM stock_daily
            WHERE stock_code = :code {date_clause}
            ORDER BY trade_date ASC
        """

        df = pd.read_sql(text(sql), self.engine, params=params)
        if not df.empty and "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df

    def get_multiple(
        self,
        stock_codes: Sequence[str],
        start_date: Optional[str | date] = None,
        end_date: Optional[str | date] = None,
    ) -> pd.DataFrame:
        """批量获取多只股票的日线数据。

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame，按 stock_code + trade_date 排序
        """
        if not stock_codes:
            return pd.DataFrame()

        params: dict = {"codes": tuple(stock_codes)}
        date_clause = ""
        if start_date:
            s = self._to_date_str(start_date)
            date_clause += " AND trade_date >= :start_date"
            params["start_date"] = s
        if end_date:
            e = self._to_date_str(end_date)
            date_clause += " AND trade_date <= :end_date"
            params["end_date"] = e

        sql = f"""
            SELECT *
            FROM stock_daily
            WHERE stock_code IN :codes {date_clause}
            ORDER BY stock_code, trade_date ASC
        """

        df = pd.read_sql(text(sql), self.engine, params=params)
        if not df.empty and "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df

    def get_latest_trade_date(self, stock_code: str) -> Optional[date]:
        """获取某股票最新交易日。"""
        row = self.engine.connect().execute(
            text("SELECT MAX(trade_date) FROM stock_daily WHERE stock_code = :code"),
            {"code": stock_code},
        ).fetchone()
        if row and row[0]:
            return row[0]
        return None

    def get_latest_dates(self, stock_codes: Sequence[str]) -> dict[str, Optional[date]]:
        """批量获取多只股票的最新日期。"""
        if not stock_codes:
            return {}
        rows = self.engine.connect().execute(
            text("""
                SELECT stock_code, MAX(trade_date) as latest
                FROM stock_daily
                WHERE stock_code IN :codes
                GROUP BY stock_code
            """),
            {"codes": tuple(stock_codes)},
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_all_stock_codes(self) -> list[str]:
        """获取所有已入库的股票代码。"""
        rows = self.engine.connect().execute(
            text("SELECT DISTINCT stock_code FROM stock_daily ORDER BY stock_code")
        ).fetchall()
        return [r[0] for r in rows]

    def get_stats(self) -> dict:
        """获取数据统计信息。"""
        row = self.engine.connect().execute(text("""
            SELECT
                COUNT(DISTINCT stock_code) as stock_count,
                COUNT(*) as total_records,
                MIN(trade_date) as earliest_date,
                MAX(trade_date) as latest_date,
                COUNT(DISTINCT trade_date) as trading_days
            FROM stock_daily
        """)).fetchone()
        return {
            "stock_count": row[0] or 0,
            "total_records": row[1] or 0,
            "earliest_date": row[2],
            "latest_date": row[3],
            "trading_days": row[4] or 0,
        }

    @staticmethod
    def _to_date_str(d: str | date) -> str:
        if isinstance(d, str):
            if len(d) == 8:
                return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            return d
        return d.strftime("%Y-%m-%d")


# CLI 测试入口
if __name__ == "__main__":
    import json

    db = QuantDB()
    stats = db.get_stats()
    print(f"数据统计: {json.dumps(stats, default=str, ensure_ascii=False)}")
    codes = db.get_all_stock_codes()[:5]
    print(f"已入库股票（示例）: {codes}")
    if codes:
        df = db.get_daily(codes[0], start_date="20240101")
        print(f"股票 {codes[0]} 最近数据:\n{df.tail(3).to_string(index=False)}")
    db.close()
