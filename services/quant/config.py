"""QuantDB config — holds DB and AKShare settings."""
from __future__ import annotations

import os

# PostgreSQL connection
PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "quantdb2026")
PG_DATABASE = os.environ.get("PG_DATABASE", "quantdb")

DB_URL = f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"

# AKShare settings
# 默认抓取多少只股票的历史数据（0 = 全部）
DEFAULT_STOCK_COUNT = int(os.environ.get("QUANT_FETCH_COUNT", "50"))
# 增量更新时最多回溯天数
INCREMENTAL_LOOKBACK_DAYS = int(os.environ.get("QUANT_LOOKBACK_DAYS", "5"))

# 日志
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
