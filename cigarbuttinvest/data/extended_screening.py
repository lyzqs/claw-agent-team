"""
扩展初步筛选模块

使用简单筛选条件（PB≤0.5, 股息率≥6%）对全量港股进行初步筛选
找出更多潜在烟蒂股

⚠️ 这是基于简单指标的初步筛选，完整筛选由 engine 模块提供
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cigarbuttinvest.data.extended_screening")


@dataclass
class ScreeningCriteria:
    """筛选条件"""
    pb_max: float = 0.5
    dividend_yield_min: float = 0.06  # 6%
    market_cap_min: float = 0.0  # 无下限
    market_cap_max: float = 1e12  # 1万亿上限
    price_min: float = 0.0
    exclude_st: bool = True  # 排除ST股
    exclude_warn: bool = True  # 排除警示股
    
    def to_dict(self) -> Dict:
        return {
            "pb_max": self.pb_max,
            "dividend_yield_min": self.dividend_yield_min * 100,
            "market_cap_range": f"{self.market_cap_min/1e6:.0f}M - {self.market_cap_max/1e6:.0f}M",
            "price_min": self.price_min,
            "exclude_st": self.exclude_st,
            "exclude_warn": self.exclude_warn
        }


@dataclass
class ScreeningResult:
    """筛选结果"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_stocks: int = 0
    passed_stocks: List[Dict] = field(default_factory=list)
    failed_stocks: List[Dict] = field(default_factory=list)
    data_errors: int = 0
    data_missing: int = 0
    criteria: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        d["pass_rate"] = len(self.passed_stocks) / max(self.total_stocks, 1)
        d["pass_rate_pct"] = f"{d['pass_rate']*100:.2f}%"
        return d


def _fetch_stock_metrics(code: str) -> Optional[Dict[str, Any]]:
    """使用 yfinance 获取单只股票指标"""
    try:
        import yfinance as yf
        
        ticker = yf.Ticker(code)
        info = ticker.info
        
        return {
            "code": code,
            "name": info.get("shortName", info.get("longName", "")),
            "price": info.get("regularMarketPrice", info.get("currentPrice")),
            "market_cap": info.get("marketCap"),
            "pb": info.get("priceToBook"),
            "dividend_yield": (
                float(info["dividendYield"]) / 100  # yfinance returns percentage (3.6), convert to ratio (0.036)
                if info.get("dividendYield") is not None
                else None
            ),
            "pe": info.get("trailingPE"),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "exchange": info.get("exchange", ""),
            "quote_type": info.get("quoteType", ""),
            "market": info.get("market", ""),
        }
    except Exception as e:
        logger.debug(f"获取 {code} 指标失败: {e}")
        return None


def _filter_stock(stock: Dict, criteria: ScreeningCriteria) -> tuple[bool, List[str]]:
    """
    根据条件筛选单只股票
    
    宽松模式：只要有PB和股息率数据，就按照条件筛选
    数据缺失的不强行拒绝（可能在其他批次有数据）
    
    Returns:
        (passed, reasons)
    """
    reasons = []
    
    # 检查名称是否包含 ST/警示
    name = stock.get("name", "")
    if criteria.exclude_st and (" ST " in name or name.endswith("ST") or name.startswith("ST ")):
        reasons.append("ST股")
        return False, reasons
    if criteria.exclude_warn and (" 警示" in name or " 停牌" in name):
        reasons.append("警示/停牌")
        return False, reasons
    
    # PB 筛选
    pb = stock.get("pb")
    if pb is None:
        reasons.append("PB缺失")  # 软拒绝：记录但不直接排除
    elif pb > criteria.pb_max:
        reasons.append(f"PB>{criteria.pb_max}({pb:.2f})")
    
    # 股息率筛选
    div = stock.get("dividend_yield")
    if div is None:
        reasons.append("股息率缺失")  # 软拒绝
    elif div < criteria.dividend_yield_min:
        reasons.append(f"股息率<{criteria.dividend_yield_min*100:.0f}%({div:.2f}%)")
    
    # 市值筛选
    mc = stock.get("market_cap")
    if mc is not None:
        if mc < criteria.market_cap_min:
            reasons.append(f"市值<{criteria.market_cap_min/1e6:.0f}M")
        if mc > criteria.market_cap_max:
            reasons.append(f"市值>{criteria.market_cap_max/1e6:.0f}M")
    
    # 价格筛选
    price = stock.get("price")
    if price is not None and price < criteria.price_min:
        reasons.append(f"价格<{criteria.price_min}")
    
    # 判断是否通过
    # 硬性拒绝：有明确的 PB> 或 股息率< 原因
    hard_rejects = [r for r in reasons if any(x in r for x in ["PB>", "股息率<"])] 
    if hard_rejects:
        return False, hard_rejects
    
    # 如果 PB 和 股息率 都缺失，认为数据不足（排除）
    if pb is None and div is None:
        return False, ["PB和股息率均无数据"]
    
    # 如果有其中一项数据，按该项判断
    if pb is not None and pb <= criteria.pb_max:
        return True, [f"PB={pb:.3f}满足要求"]
    if div is not None and div >= criteria.dividend_yield_min:
        return True, [f"股息率={div:.2f}%满足要求"]
    
    # 有数据但不满足 -> 不通过
    if pb is not None or div is not None:
        return False, reasons
    
    return False, reasons


def run_extended_screening(
    stock_list: List[Dict],
    criteria: ScreeningCriteria = None,
    max_workers: int = 10,
    batch_size: int = 100,
    dry_run: bool = False,
    sample_size: int = 500,  # 试运行采样数量
    logger = None
) -> ScreeningResult:
    """
    运行扩展初步筛选
    
    Args:
        stock_list: 股票列表
        criteria: 筛选条件，默认使用 PB≤0.5, 股息率≥6%
        max_workers: 并发线程数
        batch_size: 每批处理数量
        dry_run: 试运行模式
        sample_size: 试运行采样数量
        logger: 日志记录器
    
    Returns:
        ScreeningResult
    """
    if logger is None:
        logger = logging.getLogger("cigarbuttinvest.extended_screening")
    
    if criteria is None:
        criteria = ScreeningCriteria()
    
    result = ScreeningResult(
        criteria=criteria.to_dict(),
        total_stocks=len(stock_list)
    )
    
    logger.info(f"开始扩展筛选: {len(stock_list)} 只股票")
    logger.info(f"筛选条件: PB≤{criteria.pb_max}, 股息率≥{criteria.dividend_yield_min*100:.0f}%")
    
    if dry_run:
        stock_list = stock_list[:sample_size]
        logger.info(f"试运行模式: 仅处理前 {sample_size} 只")
    
    start_time = time.time()
    
    # 分批处理
    total_batches = (len(stock_list) + batch_size - 1) // batch_size
    all_passed = []
    all_failed = []
    
    for batch_idx in range(total_batches):
        batch_start = batch_idx * batch_size
        batch_end = min(batch_start + batch_size, len(stock_list))
        batch = stock_list[batch_start:batch_end]
        
        logger.info(f"处理批次 {batch_idx + 1}/{total_batches} ({len(batch)} 只)")
        
        # 并发获取数据
        fetched = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for stock in batch:
                code = stock.get("code_yf", f"{stock.get('code')}.HK")
                futures[executor.submit(_fetch_stock_metrics, code)] = stock
            
            for future in as_completed(futures):
                stock = futures[future]
                code = stock.get("code", "")
                try:
                    data = future.result(timeout=15)
                    if data:
                        # 补充原始信息
                        data["_original_code"] = code
                        data["_original_name"] = stock.get("name", data.get("name", ""))
                        fetched[code] = data
                except Exception as e:
                    logger.debug(f"获取 {code} 失败: {e}")
                    result.data_errors += 1
        
        # 对获取成功的股票进行筛选
        for code, stock_data in fetched.items():
            passed, reasons = _filter_stock(stock_data, criteria)
            stock_data["_filter_reasons"] = reasons
            
            if passed:
                all_passed.append(stock_data)
            else:
                all_failed.append(stock_data)
        
        # 批次间延迟
        if batch_idx < total_batches - 1:
            time.sleep(0.5)
    
    result.passed_stocks = all_passed
    result.failed_stocks = all_failed
    
    # 按 PB 排序（烟蒂股优先低PB）
    result.passed_stocks.sort(key=lambda x: x.get("pb") or 9999)
    
    end_time = time.time()
    duration = end_time - start_time
    
    logger.info(f"筛选完成:")
    logger.info(f"  总计: {result.total_stocks} 只")
    logger.info(f"  通过: {len(result.passed_stocks)} 只 ({len(result.passed_stocks)/max(result.total_stocks,1)*100:.1f}%)")
    logger.info(f"  失败: {len(result.failed_stocks)} 只")
    logger.info(f"  数据错误: {result.data_errors} 只")
    logger.info(f"  耗时: {duration:.1f} 秒")
    
    return result


def save_screening_result(result: ScreeningResult, output_dir: str = None) -> List[Path]:
    """
    保存筛选结果
    
    Returns:
        保存的文件路径列表
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "docs" / "results" / "expanded"
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = []
    
    # 1. 保存完整结果
    result_file = output_dir / f"extended_screening_result_{date_str}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    files.append(result_file)
    logger.info(f"结果已保存: {result_file}")
    
    # 2. 保存通过的股票列表
    passed_file = output_dir / f"passed_stocks_{date_str}.json"
    with open(passed_file, "w", encoding="utf-8") as f:
        json.dump(result.passed_stocks, f, ensure_ascii=False, indent=2)
    files.append(passed_file)
    
    # 3. 生成 Markdown 报告
    md_file = output_dir / f"expanded_screening_report_{date_str}.md"
    generate_md_report(result, md_file)
    files.append(md_file)
    
    # 4. 保存最新结果（覆盖）
    latest_file = output_dir / "latest_result.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    
    return files


def generate_md_report(result: ScreeningResult, output_path: Path):
    """生成 Markdown 报告"""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        f"# 扩展初步筛选报告",
        f"",
        f"**生成时间**: {date_str}",
        f"**筛选条件**: PB≤{result.criteria.get('pb_max', 'N/A')}, 股息率≥{result.criteria.get('dividend_yield_min', 'N/A')}%",
        f"",
        f"## 统计概览",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 处理股票总数 | {result.total_stocks} |",
        f"| 通过筛选 | {len(result.passed_stocks)} |",
        f"| 未通过 | {len(result.failed_stocks)} |",
        f"| 数据获取失败 | {result.data_errors} |",
        f"| 通过率 | {result.to_dict().get('pass_rate_pct', 'N/A')} |",
        f"",
    ]
    
    # 通过的股票列表
    lines.append("## 通过筛选的股票")
    lines.append("")
    
    if result.passed_stocks:
        lines.append("| 股票代码 | 名称 | PB | 股息率 | 市值 |")
        lines.append("|------|------|------|------|------|")
        
        for stock in result.passed_stocks[:100]:  # 最多100行
            code = stock.get("_original_code", stock.get("code", ""))
            name = stock.get("_original_name", stock.get("name", "N/A"))[:20]
            pb = stock.get("pb", "N/A")
            pb_str = f"{pb:.2f}" if isinstance(pb, (int, float)) else str(pb)
            div = stock.get("dividend_yield", "N/A")
            div_str = f"{div*100:.2f}%" if isinstance(div, (int, float)) and div else "N/A"
            mc = stock.get("market_cap", 0)
            mc_str = f"{mc/1e9:.1f}B" if mc and mc > 1e9 else (f"{mc/1e6:.1f}M" if mc and mc > 1e6 else "N/A")
            
            lines.append(f"| {code} | {name} | {pb_str} | {div_str} | {mc_str} |")
    else:
        lines.append("> 暂无符合筛选条件的股票")
    
    lines.append("")
    lines.append("---\n*本报告由 CigarButtInvest 扩展筛选系统自动生成*\n")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    # 加载股票列表
    list_file = Path(__file__).parent.parent / "docs" / "stock_lists" / "full_hk_stock_list.json"
    with open(list_file) as f:
        stocks = json.load(f)
    
    print(f"加载 {len(stocks)} 只股票")
    
    # 试运行
    result = run_extended_screening(stocks, dry_run=True, sample_size=50)
    
    print(f"筛选结果: {len(result.passed_stocks)} 只通过")
    
    # 保存
    files = save_screening_result(result)
    print(f"已保存到: {files}")