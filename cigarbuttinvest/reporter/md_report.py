"""
Markdown 报告生成器
生成港股烟蒂股每日筛选报告
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
REPORT_DIR = PROJECT_ROOT / "docs" / "daily_runs"
REPORT_DIR.mkdir(exist_ok=True)


def format_currency(amount: float, currency: str = "HKD") -> str:
    """格式化货币金额"""
    if abs(amount) >= 1e9:
        return f"{currency} {amount / 1e9:.2f}B"
    elif abs(amount) >= 1e6:
        return f"{currency} {amount / 1e6:.2f}M"
    elif abs(amount) >= 1e3:
        return f"{currency} {amount / 1e3:.2f}K"
    else:
        return f"{currency} {amount:.2f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """格式化百分比"""
    return f"{value * 100:.{decimals}f}%"


def format_stock_basic_info(stock: Dict[str, Any]) -> str:
    """格式化股票基本信息"""
    lines = []
    
    if stock.get("code"):
        lines.append(f"**股票代码**: {stock['code']}")
    if stock.get("name"):
        lines.append(f"**股票名称**: {stock['name']}")
    if stock.get("industry"):
        lines.append(f"**所属行业**: {stock['industry']}")
    if stock.get("listing_date"):
        lines.append(f"**上市日期**: {stock['listing_date']}")
    
    return "\n".join(lines)


def format_nav_analysis(stock: Dict[str, Any]) -> str:
    """格式化 NAV 分析结果"""
    lines = []
    
    # T级判定
    t_level = stock.get("t_level", "N/A")
    lines.append(f"**T级**: {t_level}")
    
    # NAV 数据
    if "nav" in stock:
        nav = stock["nav"]
        if isinstance(nav, dict):
            if "t0" in nav and nav["t0"] is not None:
                lines.append(f"**T0 NAV**: {nav['t0']:.2f}")
            if "t1" in nav and nav["t1"] is not None:
                lines.append(f"**T1 NAV**: {nav['t1']:.2f}")
            if "t2" in nav and nav["t2"] is not None:
                lines.append(f"**T2 NAV**: {nav['t2']:.2f}")
    
    # 当前价格和 PB
    if stock.get("price"):
        lines.append(f"**当前股价**: {stock['price']:.3f}")
    if stock.get("pb"):
        lines.append(f"**市净率 (PB)**: {stock['pb']:.2f}")
    if stock.get("market_cap"):
        lines.append(f"**总市值**: {format_currency(stock['market_cap'])}")
    
    return "\n".join(lines)


def format_subtype_analysis(stock: Dict[str, Any]) -> str:
    """格式化子类型分析"""
    subtype = stock.get("subtype", {})
    if not subtype:
        return "未满足任何子类型条件"
    
    lines = []
    
    for stype, details in subtype.items():
        if details.get("matched"):
            lines.append(f"### {stype}")
            lines.append(f"- **判定**: ✅ 满足")
            
            if stype == "A":
                if details.get("dividend_yield"):
                    lines.append(f"- 股息率: {format_percentage(details['dividend_yield'])}")
                if details.get("pb"):
                    lines.append(f"- 市净率: {details['pb']:.2f}")
                if details.get("consecutive_years"):
                    lines.append(f"- 连续派息: {details['consecutive_years']} 年")
                    
            elif stype == "B":
                if details.get("holdings_discount"):
                    lines.append(f"- 控股折价率: {format_percentage(details['holdings_discount'])}")
                if details.get("coverage"):
                    lines.append(f"- 持股覆盖率: {format_percentage(details['coverage'])}")
                    
            elif stype == "C":
                if details.get("event_type"):
                    lines.append(f"- 事件类型: {details['event_type']}")
                if details.get("probability"):
                    lines.append(f"- 事件概率: {details['probability']}")
    
    return "\n".join(lines) if lines else "无满足的子类型"


def format_factcheck_result(stock: Dict[str, Any]) -> str:
    """格式化 Fact Check 结果"""
    factcheck = stock.get("factcheck", {})
    if not factcheck:
        return "未执行 Fact Check"
    
    lines = []
    
    # 总体评级
    rating = factcheck.get("rating", "N/A")
    rating_map = {"A": "✅ A级 - 强烈推荐", "B": "🟢 B级 - 可投资", 
                  "C": "🟡 C级 - 谨慎", "D": "🔴 D级 - 不建议"}
    lines.append(f"**Fact Check 评级**: {rating_map.get(rating, rating)}")
    
    # 警告项
    warnings = factcheck.get("warnings", [])
    if warnings:
        lines.append(f"\n**警告项** ({len(warnings)} 个):\n")
        for w in warnings:
            wtype = w.get("type", "Risk")
            lines.append(f"- {'⚠️' if wtype == 'Risk' else '📊'} [{wtype}] {w.get('item', 'N/A')}: {w.get('detail', '')}")
    
    # 否决项
    rejects = factcheck.get("rejects", [])
    if rejects:
        lines.append(f"\n**否决项** ({len(rejects)} 个):\n")
        for r in rejects:
            lines.append(f"- ❌ {r.get('item', 'N/A')}: {r.get('reason', '')}")
    
    return "\n".join(lines)


def generate_daily_report(
    run_id: str,
    filtered_stocks: List[Dict[str, Any]],
    report_date: str = None,
    logger: Optional[logging.Logger] = None
) -> Path:
    """
    生成每日筛选 Markdown 报告
    
    Args:
        run_id: 运行唯一标识
        filtered_stocks: 符合烟蒂股条件的股票列表
        report_date: 报告日期，默认为今天
        logger: 日志记录器
    
    Returns:
        报告文件路径
    """
    if logger is None:
        logger = logging.getLogger("cigarbuttinvest.reporter")
    
    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")
    
    # 生成报告文件名
    report_filename = f"烟蒂股筛选报告_{report_date}.md"
    report_path = REPORT_DIR / report_filename
    
    # 统计信息
    total_stocks = len(filtered_stocks)
    type_counts = {"A": 0, "B": 0, "C": 0}
    t_counts = {"T0": 0, "T1": 0, "T2": 0}
    
    for stock in filtered_stocks:
        subtype = stock.get("subtype", {})
        for stype in ["A", "B", "C"]:
            if subtype.get(stype, {}).get("matched"):
                type_counts[stype] += 1
        
        t_level = stock.get("t_level", "")
        if t_level in t_counts:
            t_counts[t_level] += 1
    
    # 生成 Markdown 内容
    content = f"""# 港股烟蒂股每日筛选报告

**报告日期**: {report_date}  
**运行ID**: {run_id}  
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**筛选标准**: 基于烟蒂股分析 Prompt v1.8

---

## 📊 筛选统计

| 指标 | 数值 |
|------|------|
| 符合条件股票数 | {total_stocks} |
| A型（高股息破净） | {type_counts['A']} |
| B型（控股折价） | {type_counts['B']} |
| C型（事件驱动） | {type_counts['C']} |
| T0级（净现金） | {t_counts['T0']} |
| T1级（现金覆盖借贷） | {t_counts['T1']} |
| T2级（流动资产覆盖） | {t_counts['T2']} |

---

## 🎯 符合条件标的详情

"""
    
    if not filtered_stocks:
        content += "> 今日无符合烟蒂股筛选条件的标的\n\n"
    else:
        for idx, stock in enumerate(filtered_stocks, 1):
            content += f"""### {idx}. {stock.get('name', 'N/A')} ({stock.get('code', 'N/A')})

#### 基本信息
{format_stock_basic_info(stock)}

#### NAV 分析
{format_nav_analysis(stock)}

#### 子类型判定
{format_subtype_analysis(stock)}

#### Fact Check
{format_factcheck_result(stock)}

---

"""
    
    # 添加投资建议
    content += f"""## 💡 投资建议

"""
    
    if not filtered_stocks:
        content += "> 今日无强烈推荐标的，建议关注市场动态。\n"
    else:
        # 按 Fact Check 评级排序
        top_stocks = sorted(
            [s for s in filtered_stocks if s.get("factcheck", {}).get("rating") in ["A", "B"]],
            key=lambda x: {"A": 0, "B": 1}.get(x.get("factcheck", {}).get("rating", "C"), 2)
        )[:5]
        
        if top_stocks:
            content += "**今日重点关注**:\n\n"
            for stock in top_stocks:
                rating = stock.get("factcheck", {}).get("rating", "C")
                content += f"- **{stock.get('name', 'N/A')}** ({stock.get('code', 'N/A')}) - "
                content += f"T{stock.get('t_level', 'N')[-1] if stock.get('t_level') else '?'}级 - "
                content += f"Fact Check {rating}级\n"
            content += "\n"
    
    # 添加免责声明
    content += """## ⚠️ 免责声明

本报告仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。

请在做出投资决策前：
1. 自行进行更深入的调研
2. 评估个人风险承受能力
3. 考虑资产配置需求
4. 必要时咨询专业投资顾问

---

*报告由 CigarButtInvest 自动化筛选系统生成*
*筛选标准参考: https://terancejiang.github.io/Stock_Analyze_Prompts/*
"""
    
    # 写入文件
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    logger.info(f"报告已生成: {report_path}")
    
    return report_path


def generate_summary_report(
    start_date: str,
    end_date: str,
    daily_results: List[Dict[str, Any]]
) -> Path:
    """
    生成周期汇总报告（如周报、月报）
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        daily_results: 每日结果列表
    
    Returns:
        汇总报告路径
    """
    summary_filename = f"烟蒂股筛选汇总_{start_date}_{end_date}.md"
    summary_path = REPORT_DIR / summary_filename
    
    total_runs = len(daily_results)
    total_stocks_found = sum(len(r.get("filtered_stocks", [])) for r in daily_results)
    
    # 统计每日结果
    successful_runs = [r for r in daily_results if r.get("status") == "success"]
    
    content = f"""# 烟蒂股筛选汇总报告

**汇总周期**: {start_date} 至 {end_date}  
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📈 汇总统计

| 指标 | 数值 |
|------|------|
| 运行总次数 | {total_runs} |
| 成功运行 | {len(successful_runs)} |
| 发现烟蒂股总数 | {total_stocks_found} |
| 日均发现数 | {total_stocks_found / max(total_runs, 1):.1f} |

---

## 📅 每日详情

| 日期 | 状态 | 筛选数量 |
|------|------|----------|
"""
    
    for result in daily_results:
        date = result.get("run_id", "N/A")[:8]  # 取日期部分
        status = result.get("status", "unknown")
        count = len(result.get("filtered_stocks", []))
        content += f"| {date} | {status} | {count} |\n"
    
    content += "\n---\n\n*汇总报告由 CigarButtInvest 自动化筛选系统生成*\n"
    
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    return summary_path


if __name__ == "__main__":
    # 测试
    test_stocks = [
        {
            "code": "00001",
            "name": "长和",
            "industry": "综合企业",
            "price": 45.5,
            "pb": 0.45,
            "market_cap": 175_000_000_000,
            "t_level": "T1",
            "nav": {"t0": 60.5, "t1": 55.2, "t2": 48.3},
            "subtype": {
                "A": {"matched": True, "dividend_yield": 0.068, "pb": 0.45, "consecutive_years": 12}
            },
            "factcheck": {
                "rating": "A",
                "warnings": [],
                "rejects": []
            }
        },
        {
            "code": "00005",
            "name": "汇丰控股",
            "industry": "银行",
            "price": 62.0,
            "pb": 0.65,
            "market_cap": 1_200_000_000_000,
            "t_level": "T2",
            "nav": {"t0": None, "t1": 70.5, "t2": 58.2},
            "subtype": {},
            "factcheck": {
                "rating": "B",
                "warnings": [{"type": "Risk", "item": "商誉占比", "detail": "商誉占总资产 18%"}],
                "rejects": []
            }
        }
    ]
    
    report_path = generate_daily_report(
        run_id="test_20260420",
        filtered_stocks=test_stocks,
        report_date="2026-04-20"
    )
    
    print(f"测试报告已生成: {report_path}")
