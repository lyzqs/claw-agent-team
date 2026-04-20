"""
CigarButtInvest 定时任务调度器
每天交易日 9:30 自动运行港股烟蒂股筛选

功能：
- 交易日检测（跳过节假日和周末）
- 失败重试机制（指数退避）
- 任务状态监控
- 运行日志记录

依赖：
- engine 模块（筛选引擎，由 Dev 实现）
- reporter 模块（报告生成，由 Ops 实现）
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import time

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 交易日检测（香港联交所节假日）
from scheduler.trading_calendar import is_trading_day

# 日志配置
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(run_id: str) -> logging.Logger:
    """配置本次运行的日志"""
    log_file = LOG_DIR / f"daily_job_{run_id}.log"
    
    logger = logging.getLogger(f"cigarbuttinvest.daily_job.{run_id}")
    logger.setLevel(logging.INFO)
    
    # 清除已有 handlers
    logger.handlers = []
    
    # 文件 handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    
    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    
    # 格式化
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    
    logger.addHandler(fh)
    logger.addHandler(ch)
    
    return logger


def run_daily_screening(
    run_id: str,
    dry_run: bool = False,
    logger: Optional[logging.Logger] = None
) -> dict:
    """
    执行每日筛选任务
    
    Args:
        run_id: 运行唯一标识
        dry_run: 是否为试运行模式
        logger: 日志记录器
    
    Returns:
        运行结果 dict
    """
    if logger is None:
        logger = setup_logging(run_id)
    
    start_time = datetime.now()
    logger.info(f"=" * 60)
    logger.info(f"开始执行每日筛选 - run_id: {run_id}")
    logger.info(f"时间: {start_time.isoformat()}")
    logger.info(f"试运行模式: {dry_run}")
    logger.info(f"=" * 60)
    
    result = {
        "run_id": run_id,
        "start_time": start_time.isoformat(),
        "status": "running",
        "trading_day": is_trading_day(),
        "dry_run": dry_run,
        "steps": [],
        "errors": [],
    }
    
    try:
        # Step 1: 检查是否为交易日
        logger.info("[Step 1/5] 检查交易日...")
        if not result["trading_day"]:
            logger.info("今日非交易日，跳过筛选")
            result["status"] = "skipped"
            result["steps"].append({
                "name": "trading_day_check",
                "status": "skipped",
                "reason": "今日非交易日"
            })
            return result
        
        result["steps"].append({
            "name": "trading_day_check",
            "status": "success"
        })
        
        # Step 2: 数据获取
        logger.info("[Step 2/5] 获取港股数据...")
        result["steps"].append({
            "name": "data_fetch",
            "status": "running"
        })
        
        # 导入 engine 模块（由 Dev 实现）
        try:
            from engine.fetcher import fetch_hk_stocks_data
            from data.cache import StockDataCache
            
            cache = StockDataCache()
            stocks_data = fetch_hk_stocks_data(
                dry_run=dry_run,
                logger=logger
            )
            
            result["steps"][-1]["status"] = "success"
            result["steps"][-1]["stocks_count"] = len(stocks_data) if stocks_data else 0
            logger.info(f"成功获取 {len(stocks_data) if stocks_data else 0} 只股票数据")
            
        except ImportError as e:
            logger.warning(f"Engine 模块尚未实现: {e}")
            result["steps"][-1]["status"] = "pending"
            result["steps"][-1]["reason"] = f"Engine 模块导入失败: {e}"
            result["errors"].append({
                "step": "data_fetch",
                "error": str(e),
                "type": "import_error"
            })
            stocks_data = None
        
        # Step 3: 执行筛选
        logger.info("[Step 3/5] 执行烟蒂股筛选...")
        result["steps"].append({
            "name": "screening",
            "status": "running"
        })
        
        try:
            if stocks_data:
                from engine.screener import ScreenEngine
                
                screener = ScreenEngine()
                filtered_stocks = screener.screen(stocks_data)
                
                result["steps"][-1]["status"] = "success"
                result["steps"][-1]["filtered_count"] = len(filtered_stocks)
                logger.info(f"筛选出 {len(filtered_stocks)} 只烟蒂股")
            else:
                result["steps"][-1]["status"] = "skipped"
                result["steps"][-1]["reason"] = "无数据，跳过筛选"
                filtered_stocks = []
                
        except ImportError as e:
            logger.warning(f"筛选引擎尚未实现: {e}")
            result["steps"][-1]["status"] = "pending"
            result["steps"][-1]["reason"] = f"筛选引擎导入失败: {e}"
            filtered_stocks = []
        
        # Step 4: 生成报告
        logger.info("[Step 4/5] 生成筛选报告...")
        result["steps"].append({
            "name": "report_generation",
            "status": "running"
        })
        
        try:
            from reporter.md_report import generate_daily_report
            
            report_path = generate_daily_report(
                run_id=run_id,
                filtered_stocks=filtered_stocks,
                logger=logger
            )
            
            result["steps"][-1]["status"] = "success"
            result["steps"][-1]["report_path"] = str(report_path)
            logger.info(f"报告已生成: {report_path}")
            
        except ImportError as e:
            logger.warning(f"报告模块尚未实现: {e}")
            result["steps"][-1]["status"] = "pending"
            result["steps"][-1]["reason"] = f"报告模块导入失败: {e}"
        
        # Step 5: 保存运行记录
        logger.info("[Step 5/5] 保存运行记录...")
        result["steps"].append({
            "name": "save_run_record",
            "status": "running"
        })
        
        end_time = datetime.now()
        result["end_time"] = end_time.isoformat()
        result["duration_seconds"] = (end_time - start_time).total_seconds()
        
        # 保存到 docs/daily_runs/
        run_record_dir = PROJECT_ROOT / "docs" / "daily_runs"
        run_record_dir.mkdir(exist_ok=True)
        
        run_record_path = run_record_dir / f"run_{run_id}.json"
        with open(run_record_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        result["steps"][-1]["status"] = "success"
        result["steps"][-1]["record_path"] = str(run_record_path)
        
        # 判断最终状态
        failed_steps = [s for s in result["steps"] if s["status"] == "failed"]
        pending_steps = [s for s in result["steps"] if s["status"] == "pending"]
        
        if failed_steps:
            result["status"] = "failed"
        elif pending_steps:
            result["status"] = "partial"
        else:
            result["status"] = "success"
        
        logger.info(f"=" * 60)
        logger.info(f"任务完成 - 状态: {result['status']}")
        logger.info(f"耗时: {result['duration_seconds']:.2f} 秒")
        logger.info(f"=" * 60)
        
        return result
        
    except Exception as e:
        logger.exception(f"任务执行失败: {e}")
        result["status"] = "failed"
        result["errors"].append({
            "step": "main",
            "error": str(e),
            "type": "unexpected_error"
        })
        return result


def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 60.0,
    max_delay: float = 3600.0,
    logger: Optional[logging.Logger] = None
):
    """
    指数退避重试装饰器
    
    Args:
        func: 要重试的函数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        logger: 日志记录器
    """
    def wrapper(*args, **kwargs):
        if logger is None:
            logger = logging.getLogger("cigarbuttinvest.retry")
        
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        f"尝试 {attempt + 1}/{max_retries + 1} 失败: {e}. "
                        f"{delay:.0f} 秒后重试..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"所有重试次数已用尽: {e}")
        
        raise last_exception
    
    return wrapper


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description="港股烟蒂股每日筛选任务")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式（不实际执行筛选）"
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="运行唯一标识（默认自动生成）"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制运行（忽略交易日检查）"
    )
    
    args = parser.parse_args()
    
    # 生成或使用指定的 run_id
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 设置日志
    logger = setup_logging(run_id)
    
    # 检查交易日（除非强制运行）
    if not args.force and not is_trading_day():
        logger.info("今日非交易日，任务退出")
        return 0
    
    # 执行任务
    @retry_with_backoff(max_retries=3, base_delay=60, logger=logger)
    def run_with_retry():
        return run_daily_screening(
            run_id=run_id,
            dry_run=args.dry_run,
            logger=logger
        )
    
    result = run_with_retry()
    
    # 状态码
    status_codes = {
        "success": 0,
        "partial": 1,
        "skipped": 0,
        "failed": 2,
    }
    
    return status_codes.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
