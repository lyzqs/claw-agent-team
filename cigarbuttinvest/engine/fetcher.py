"""
港股烟蒂股筛选引擎

⚠️ 占位模块 - 由 Dev (Issue #3) 实现
此文件将在 Issue #3 完成后由 Dev 替换
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class StockDataFetcher:
    """港股数据获取器"""
    
    def __init__(self):
        logger.warning("⚠️ StockDataFetcher 使用占位实现，请等待 Dev 完成 Issue #3")
    
    def fetch_hk_stocks_data(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        """
        获取港股全量数据
        
        Args:
            dry_run: 是否为试运行模式
        
        Returns:
            股票数据列表
        """
        logger.warning("占位实现：fetch_hk_stocks_data")
        
        if dry_run:
            # 试运行模式，返回少量测试数据
            return [
                {
                    "code": "00001",
                    "name": "长和",
                    "industry": "综合企业",
                    "price": 45.5,
                    "pb": 0.45,
                    "market_cap": 175_000_000_000,
                    "dividend_yield": 0.068,
                    "status": "active"
                }
            ]
        
        # TODO: 实现实际数据获取
        # 使用 akshare 或其他数据源
        logger.error("⚠️ 真实数据获取尚未实现，请先完成 Issue #3")
        return []
    
    def fetch_single_stock(self, stock_code: str) -> Dict[str, Any]:
        """
        获取单只股票数据
        
        Args:
            stock_code: 股票代码
        
        Returns:
            股票数据
        """
        logger.warning(f"占位实现：fetch_single_stock({stock_code})")
        return {}


# 全局实例
_fetcher = None


def get_fetcher() -> StockDataFetcher:
    """获取数据获取器实例"""
    global _fetcher
    if _fetcher is None:
        _fetcher = StockDataFetcher()
    return _fetcher


def fetch_hk_stocks_data(dry_run: bool = False, logger=None) -> List[Dict[str, Any]]:
    """
    获取港股全量数据
    
    Args:
        dry_run: 是否为试运行模式
        logger: 日志记录器
    
    Returns:
        股票数据列表
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    
    fetcher = get_fetcher()
    return fetcher.fetch_hk_stocks_data(dry_run=dry_run)


def fetch_single_stock(stock_code: str) -> Dict[str, Any]:
    """
    获取单只股票数据
    
    Args:
        stock_code: 股票代码
    
    Returns:
        股票数据
    """
    fetcher = get_fetcher()
    return fetcher.fetch_single_stock(stock_code)
