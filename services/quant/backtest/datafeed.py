"""数据供应器 — 支持数据库、CSV、内存三种模式。"""
from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Optional, Sequence

import sys
sys.path.insert(0, str(Path(__file__).parents[2]))
from services.quant.api import QuantDB


class DataFeed:
    """回测引擎的数据抽象接口。"""

    def get_bars(
        self,
        stock_codes: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        """返回 dict[stock_code, DataFrame]，DataFrame 列：
        trade_date, open, high, low, close, volume
        """
        raise NotImplementedError


class DBDataFeed(DataFeed):
    """从 PostgreSQL (QuantDB) 读取真实数据。"""

    def __init__(self, db_url: Optional[str] = None):
        self._db = QuantDB(db_url) if db_url else QuantDB()

    def get_bars(
        self,
        stock_codes: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for code in stock_codes:
            df = self._db.get_daily(
                code,
                start_date=start_date,
                end_date=end_date,
            )
            if not df.empty:
                df = df.rename(columns={
                    "trade_date": "trade_date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                })
                df = df[["trade_date", "open", "high", "low", "close", "volume"]].copy()
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                df = df.sort_values("trade_date").reset_index(drop=True)
                result[code] = df
        return result

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CSVDataFeed(DataFeed):
    """从 CSV 文件读取数据。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def get_bars(
        self,
        stock_codes: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for code in stock_codes:
            fpath = self.data_dir / f"{code}.csv"
            if not fpath.exists():
                continue
            df = pd.read_csv(fpath, parse_dates=["trade_date"])
            df = df.rename(columns={
                "trade_date": "trade_date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            })
            df = df[["trade_date", "open", "high", "low", "close", "volume"]].copy()
            if start_date:
                df = df[df["trade_date"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["trade_date"] <= pd.to_datetime(end_date)]
            if not df.empty:
                result[code] = df.sort_values("trade_date").reset_index(drop=True)
        return result


class MemoryDataFeed(DataFeed):
    """从内存中的 dict[str, DataFrame] 提供数据。"""

    def __init__(self, data: dict[str, pd.DataFrame]):
        self._data: dict[str, pd.DataFrame] = {}
        for code, df in data.items():
            self._data[code] = df[["trade_date", "open", "high", "low", "close", "volume"]].copy()

    def get_bars(
        self,
        stock_codes: Sequence[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for code in stock_codes:
            if code not in self._data:
                continue
            df = self._data[code].copy()
            if start_date:
                df = df[df["trade_date"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["trade_date"] <= pd.to_datetime(end_date)]
            if not df.empty:
                result[code] = df.reset_index(drop=True)
        return result
