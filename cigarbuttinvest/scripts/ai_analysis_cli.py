#!/usr/bin/env python3
"""
AI 辅助筛选 CLI 入口

提供独立的 AI 分析功能入口，支持：
- 单只股票分析
- 批量分析（从通过初步筛选的股票中选）
- 与 Pipeline 集成分析

Usage:
    # 单只股票分析
    python scripts/ai_analysis_cli.py --code 0083.HK --name "信和置业"

    # 批量分析
    python scripts/ai_analysis_cli.py --batch 0083.HK 0267.HK 1800.HK

    # Pipeline 模式（初步筛选 + AI 分析）
    python scripts/ai_analysis_cli.py --pipeline --codes-from-previous

    # 通过管道输入股票列表
    echo "0083.HK,SINO LAND\n0267.HK,CITIC" | python scripts/ai_analysis_cli.py --stdin
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ai_analysis_cli")


# ==============================================================================
# 主逻辑
# ==============================================================================

def analyze_single(code: str, name: str = "", data_file: str = "") -> dict:
    """分析单只股票"""
    from engine.ai_analyzer import AIStockAnalyzer, AnalysisConfig, AIAnalysisResult
    
    analyzer = AIStockAnalyzer(config=AnalysisConfig(timeout_seconds=300))
    
    stock_data = None
    if data_file and os.path.exists(data_file):
        with open(data_file) as f:
            all_data = json.load(f)
            for s in all_data:
                if s.get("code") == code:
                    stock_data = s
                    break
    
    logger.info(f"Starting AI analysis for {code} {name or ''}...")
    result = analyzer.analyze_stock(code, name, stock_data)
    
    return result.to_dict()


def analyze_batch(codes: list, names: list = None) -> list:
    """批量分析多只股票"""
    from engine.ai_analyzer import AIStockAnalyzer, AnalysisConfig
    
    stocks = []
    for i, code in enumerate(codes):
        name = (names[i] if names and i < len(names) else "")
        stocks.append({"code": code, "name": name})
    
    def progress(current, total, result):
        status = "OK" if not result.error else "ERR"
        logger.info(f"  [{current}/{total}] {status} {result.code} → {result.rating}")
    
    analyzer = AIStockAnalyzer(config=AnalysisConfig(timeout_seconds=300))
    results = analyzer.analyze_stocks_batch(stocks, progress_callback=progress)
    
    return [r.to_dict() for r in results]


def analyze_pipeline_mode(codes: list = None, output_dir: str = "") -> dict:
    """Pipeline 模式：初步筛选 + AI 分析"""
    from engine.pipeline import CigarButtPipeline, PipelineConfig
    
    # 从 data/ 加载候选股票
    data_dir = PROJECT_ROOT / "docs" / "results" / "expanded"
    candidate_file = data_dir / "passed_stocks_latest.json"
    
    stocks = []
    if candidate_file.exists():
        with open(candidate_file) as f:
            stocks = json.load(f)
        logger.info(f"Loaded {len(stocks)} candidates from {candidate_file}")
    
    if codes:
        # 过滤指定代码
        stocks = [s for s in stocks if s.get("code") in codes]
        logger.info(f"Filtered to {len(stocks)} specified stocks")
    
    if not stocks:
        logger.warning("No stocks available for pipeline analysis")
        return {"error": "No stocks available"}
    
    # 运行 Pipeline
    pipeline = CigarButtPipeline(PipelineConfig(
        use_ai=True,
        output_dir=output_dir or str(PROJECT_ROOT / "docs" / "results")
    ))
    
    def ai_progress(current, total, result):
        logger.info(f"  AI [{current}/{total}] {result.code} → {result.rating}")
    
    report = pipeline.run(stocks, use_ai=True, ai_callback=ai_progress)
    
    # 保存报告
    files = pipeline.save_report(report)
    logger.info(f"Report saved: {[str(f) for f in files]}")
    
    return report.to_dict()


def load_from_stdin() -> list:
    """从 stdin 读取股票列表（每行：code,name）"""
    lines = sys.stdin.read().strip().split("\n")
    stocks = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else ""
        stocks.append({"code": code, "name": name})
    return stocks


def main():
    parser = argparse.ArgumentParser(
        description="AI 辅助烟蒂股分析 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/ai_analysis_cli.py --code 0083.HK --name "信和置业"
  python scripts/ai_analysis_cli.py --batch 0083.HK 0267.HK 1800.HK
  python scripts/ai_analysis_cli.py --pipeline --codes 0083.HK 1800.HK
  cat stocks.csv | python scripts/ai_analysis_cli.py --stdin
        """
    )
    
    parser.add_argument("--code", dest="code", help="股票代码（单只分析）")
    parser.add_argument("--name", dest="name", default="", help="股票名称")
    parser.add_argument("--batch", dest="batch", nargs="*", help="批量分析：股票代码列表")
    parser.add_argument("--pipeline", action="store_true",
                       help="Pipeline 模式（初步筛选 + AI 分析）")
    parser.add_argument("--codes", dest="codes", nargs="*",
                       help="指定分析的股票代码")
    parser.add_argument("--stdin", action="store_true",
                       help="从 stdin 读取股票列表")
    parser.add_argument("--output", dest="output", default="",
                       help="输出目录")
    parser.add_argument("--json", action="store_true",
                       help="输出 JSON 格式")
    parser.add_argument("--quiet", action="store_true",
                       help="静默模式（减少日志）")
    
    args = parser.parse_args()
    
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    
    output_file = None
    result_data = None
    
    # 确定模式
    if args.stdin:
        stocks = load_from_stdin()
        codes = [s["code"] for s in stocks]
        names = [s["name"] for s in stocks]
        result_data = analyze_batch(codes, names)
        
    elif args.code:
        result_data = analyze_single(args.code, args.name)
        
    elif args.batch:
        result_data = analyze_batch(args.batch)
        
    elif args.pipeline:
        result_data = analyze_pipeline_mode(codes=args.codes, output_dir=args.output)
        
    elif args.codes:
        result_data = analyze_batch(args.codes)
        
    else:
        parser.print_help()
        print("\n⚠️  请指定 --code, --batch, --pipeline 或 --codes")
        return 1
    
    # 输出结果
    if args.json or (result_data and not isinstance(result_data, dict)):
        print(json.dumps(result_data, ensure_ascii=False, indent=2, default=str))
    elif result_data:
        if isinstance(result_data, dict) and "error" not in result_data:
            # 单只或 pipeline 结果
            print(json.dumps(result_data, ensure_ascii=False, indent=2, default=str))
    
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
