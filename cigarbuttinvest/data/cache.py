"""
港股烟蒂股数据缓存模块
用于缓存已获取的港股数据，减少重复请求
"""

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional


logger = logging.getLogger(__name__)


class StockDataCache:
    """港股数据缓存"""
    
    def __init__(
        self,
        cache_dir: str = None,
        expire_hours: int = 24
    ):
        """
        初始化缓存
        
        Args:
            cache_dir: 缓存目录，默认为 data/cache/
            expire_hours: 缓存过期时间（小时）
        """
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent / "data" / "cache"
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.expire_hours = expire_hours
        
        logger.debug(f"缓存目录: {self.cache_dir}")
    
    def _get_cache_key(self, stock_code: str, data_type: str = "basic") -> str:
        """生成缓存键"""
        return f"{stock_code}_{data_type}"
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / f"{cache_key}.json"
    
    def get(self, stock_code: str, data_type: str = "basic") -> Optional[Dict[str, Any]]:
        """
        获取缓存数据
        
        Args:
            stock_code: 股票代码
            data_type: 数据类型（basic, financial, price）
        
        Returns:
            缓存的数据，如果不存在或已过期则返回 None
        """
        cache_key = self._get_cache_key(stock_code, data_type)
        cache_path = self._get_cache_path(cache_key)
        
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            
            # 检查过期
            cached_time = datetime.fromisoformat(cached_data.get("_cached_at", "2000-01-01"))
            expire_time = cached_time + timedelta(hours=self.expire_hours)
            
            if datetime.now() > expire_time:
                logger.debug(f"缓存已过期: {stock_code} ({data_type})")
                return None
            
            logger.debug(f"缓存命中: {stock_code} ({data_type})")
            return cached_data.get("data")
            
        except Exception as e:
            logger.warning(f"读取缓存失败: {stock_code} - {e}")
            return None
    
    def set(
        self,
        stock_code: str,
        data: Dict[str, Any],
        data_type: str = "basic"
    ):
        """
        设置缓存数据
        
        Args:
            stock_code: 股票代码
            data: 要缓存的数据
            data_type: 数据类型
        """
        cache_key = self._get_cache_key(stock_code, data_type)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            cache_data = {
                "_cached_at": datetime.now().isoformat(),
                "_stock_code": stock_code,
                "_data_type": data_type,
                "data": data
            }
            
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"缓存已保存: {stock_code} ({data_type})")
            
        except Exception as e:
            logger.warning(f"保存缓存失败: {stock_code} - {e}")
    
    def invalidate(self, stock_code: str = None, data_type: str = None):
        """
        使缓存失效
        
        Args:
            stock_code: 股票代码，如果为 None 则清除所有
            data_type: 数据类型，如果为 None 则清除所有类型
        """
        if stock_code is None:
            # 清除所有缓存
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    cache_file.unlink()
                except Exception:
                    pass
            logger.info("已清除所有缓存")
            return
        
        if data_type is None:
            # 清除指定股票的所有缓存
            for cache_file in self.cache_dir.glob(f"{stock_code}_*.json"):
                try:
                    cache_file.unlink()
                except Exception:
                    pass
            logger.info(f"已清除股票 {stock_code} 的所有缓存")
        else:
            # 清除指定股票和类型的缓存
            cache_key = self._get_cache_key(stock_code, data_type)
            cache_path = self._get_cache_path(cache_key)
            if cache_path.exists():
                cache_path.unlink()
                logger.info(f"已清除缓存: {stock_code} ({data_type})")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            缓存统计
        """
        stats = {
            "total_files": 0,
            "total_size_bytes": 0,
            "oldest_cache": None,
            "newest_cache": None,
            "by_stock": {}
        }
        
        cache_files = list(self.cache_dir.glob("*.json"))
        stats["total_files"] = len(cache_files)
        
        oldest = None
        newest = None
        
        for cache_file in cache_files:
            stats["total_size_bytes"] += cache_file.stat().st_size
            
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                
                stock_code = cached_data.get("_stock_code", "unknown")
                cached_at = datetime.fromisoformat(cached_data.get("_cached_at", "2000-01-01"))
                
                if stock_code not in stats["by_stock"]:
                    stats["by_stock"][stock_code] = 0
                stats["by_stock"][stock_code] += 1
                
                if oldest is None or cached_at < oldest:
                    oldest = cached_at
                if newest is None or cached_at > newest:
                    newest = cached_at
                    
            except Exception:
                continue
        
        stats["oldest_cache"] = oldest.isoformat() if oldest else None
        stats["newest_cache"] = newest.isoformat() if newest else None
        
        return stats


# Ops-side: HK stock list fetcher infrastructure
def fetch_hk_stock_list() -> list:
    """
    获取港股全量标的列表
    
    这是 Ops 负责的数据获取基础设施部分
    使用 akshare 获取港股列表
    
    Returns:
        港股代码列表
    """
    try:
        import akshare as ak
        
        # 获取所有港股代码
        hk_stocks = ak.stock_hs_const_spot_em(symbol="港股")
        stock_list = hk_stocks["代码"].tolist()
        
        logger.info(f"成功获取 {len(stock_list)} 只港股代码")
        return stock_list
        
    except Exception as e:
        logger.error(f"获取港股列表失败: {e}")
        return []


def batch_fetch_stock_data(
    stock_codes: list,
    batch_size: int = 50,
    delay_between_batches: float = 1.0,
    use_cache: bool = True
) -> Dict[str, Dict[str, Any]]:
    """
    分批获取股票数据
    
    Args:
        stock_codes: 股票代码列表
        batch_size: 每批数量
        delay_between_batches: 批次间延迟（秒）
        use_cache: 是否使用缓存
    
    Returns:
        股票数据字典
    """
    import time
    
    cache = StockDataCache() if use_cache else None
    results = {}
    
    total_batches = (len(stock_codes) + batch_size - 1) // batch_size
    
    for i in range(0, len(stock_codes), batch_size):
        batch = stock_codes[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        logger.info(f"处理批次 {batch_num}/{total_batches} ({len(batch)} 只股票)")
        
        for code in batch:
            # 检查缓存
            if cache:
                cached = cache.get(code, "financial")
                if cached:
                    results[code] = cached
                    continue
            
            try:
                # 获取数据（这里会调用 engine 的 fetcher）
                from engine.fetcher import fetch_single_stock
                data = fetch_single_stock(code)
                
                if data:
                    results[code] = data
                    if cache:
                        cache.set(code, data, "financial")
                        
            except ImportError:
                # engine 模块尚未实现
                logger.warning(f"Engine 模块尚未实现，跳过: {code}")
                break
            except Exception as e:
                logger.warning(f"获取数据失败 {code}: {e}")
                continue
        
        # 批次间延迟
        if i + batch_size < len(stock_codes) and delay_between_batches > 0:
            time.sleep(delay_between_batches)
    
    return results


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    cache = StockDataCache()
    stats = cache.get_stats()
    print(f"缓存统计: {json.dumps(stats, indent=2)}")