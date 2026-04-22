#!/usr/bin/env python3
"""
筛选 Pipeline - AI 辅助烟蒂股筛选工作流

集成 ai_analyzer.py 到筛选流程，支持：
1. 初步筛选 → AI 深度分析 两阶段工作流
2. 通过 OpenClaw sessions_send/sessions_spawn 触发 AI agent
3. 生成含 AI 分析的完整报告

Usage:
    from engine.pipeline import CigarButtPipeline, run_pipeline
    pipeline = CigarButtPipeline()
    report = pipeline.run(stocks_list, use_ai=True)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger("cigarbuttinvest.engine.pipeline")


# ==============================================================================
# 数据结构
# ==============================================================================

@dataclass
class PipelineConfig:
    """Pipeline 配置"""
    # 初步筛选条件
    pb_max: float = 0.5
    dividend_yield_min: float = 0.06  # 6%
    # AI 分析配置
    use_ai: bool = True
    ai_timeout_seconds: int = 300
    ai_max_retries: int = 3
    ai_batch_size: int = 5
    ai_concurrent: bool = False
    # 报告配置
    output_dir: str = "docs/results"
    include_raw_data: bool = False


@dataclass
class PipelineStage:
    """Pipeline 阶段"""
    name: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    duration_ms: int = 0
    status: str = "pending"  # pending / running / done / failed / skipped
    records_processed: int = 0
    records_passed: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineReport:
    """Pipeline 运行报告"""
    run_id: str
    started_at: str
    completed_at: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    stages: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    ai_results: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==============================================================================
# Pipeline 主类
# ==============================================================================

class CigarButtPipeline:
    """
    烟蒂股筛选 Pipeline
    
    工作流：
    1. 初步筛选（PB≤0.5, 股息率≥6%）
    2. AI 深度分析（可选，通过 OpenClaw sessions_send）
    3. 生成报告
    
    集成 ai_analyzer.py，通过 sessions_send 向 AI agent 发送分析任务。
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._stages: List[PipelineStage] = []
        self._report: Optional[PipelineReport] = None
    
    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    
    def run(self, stocks: List[Dict[str, Any]],
            use_ai: Optional[bool] = None,
            ai_callback: Optional[Callable] = None) -> PipelineReport:
        """
        执行完整筛选 Pipeline
        
        Args:
            stocks: 股票数据列表
            use_ai: 是否启用 AI 分析（None 则使用 config 默认值）
            ai_callback: AI 分析进度回调 (current, total, result) -> None
        
        Returns:
            PipelineReport 运行报告
        """
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._report = PipelineReport(
            run_id=run_id,
            started_at=datetime.now().isoformat(),
            config=self._config_to_dict(),
        )
        
        logger.info(f"[Pipeline {run_id}] Starting with {len(stocks)} stocks")
        
        # Stage 1: 初步筛选
        stage1 = self._stage_initial_screen(stocks)
        self._report.stages.append(stage1.to_dict())
        
        if stage1.status == "failed":
            self._report.errors.append("Stage 1 (initial screen) failed")
            return self._finish_report()
        
        # Stage 2: AI 分析（可选）
        if use_ai if use_ai is not None else self.config.use_ai:
            ai_stocks = self._get_ai_candidates(stage1)
            if ai_stocks:
                stage2 = self._stage_ai_analysis(ai_stocks, ai_callback)
                self._report.stages.append(stage2.to_dict())
                self._report.ai_results = [r.to_dict() for r in stage2.details.get("results", [])]
            else:
                logger.info("No stocks passed initial screen for AI analysis")
        else:
            s = PipelineStage(name="ai_analysis", status="skipped",
                             details={"reason": "use_ai=False"})
            self._report.stages.append(s.to_dict())
        
        # Summary
        self._build_summary()
        
        return self._finish_report()
    
    def save_report(self, report: PipelineReport,
                    output_dir: Optional[str] = None) -> List[Path]:
        """保存 Pipeline 报告"""
        output_dir = Path(output_dir or self.config.output_dir)
        today = datetime.now().strftime("%Y-%m-%d")
        output_dir = output_dir / today
        output_dir.mkdir(parents=True, exist_ok=True)
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        files = []
        
        # JSON 报告
        json_path = output_dir / f"pipeline_report_{ts}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        files.append(json_path)
        
        # Markdown 摘要报告
        md_path = output_dir / f"pipeline_summary_{ts}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 烟蒂股筛选 Pipeline 运行报告\n\n")
            f.write(f"**运行ID**: {report.run_id}\n")
            f.write(f"**开始时间**: {report.started_at}\n")
            f.write(f"**结束时间**: {report.completed_at}\n\n")
            f.write(f"## 阶段概览\n\n")
            f.write(f"| 阶段 | 状态 | 处理 | 通过 | 耗时 |\n")
            f.write(f"|------|------|------|------|------|\n")
            for s in report.stages:
                dur = s.get("duration_ms", 0) / 1000
                f.write(f"| {s.get('name')} | {s.get('status')} | "
                        f"{s.get('records_processed', 0)} | "
                        f"{s.get('records_passed', 0)} | {dur:.1f}s |\n")
            f.write(f"\n## 总体摘要\n\n")
            for k, v in report.summary.items():
                f.write(f"- **{k}**: {v}\n")
            
            if report.ai_results:
                f.write(f"\n## AI 分析结果\n\n")
                f.write(f"| 股票 | 评级 | NAV层级 | 子类型 | 建议 |\n")
                f.write(f"|------|------|---------|-------|------|\n")
                for r in report.ai_results:
                    rec = r.get("investment_recommendation", "N/A")
                    subtypes = ", ".join(r.get("matched_subtypes", [])) or "N/A"
                    f.write(f"| {r.get('code')} {r.get('name')} | {r.get('rating', 'N/A')} | "
                            f"{r.get('nav_tier', 'N/A')} | {subtypes} | {rec} |\n")
            f.write(f"\n---\n*由 CigarButtInvest Pipeline 自动生成*\n")
        files.append(md_path)
        
        logger.info(f"Report saved to {output_dir}: {[f.name for f in files]}")
        return files
    
    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    
    def _stage_initial_screen(self, stocks: List[Dict[str, Any]]) -> PipelineStage:
        """阶段1：初步筛选"""
        stage = PipelineStage(name="initial_screen", status="running")
        start = time.time()
        
        try:
            passed = []
            for s in stocks:
                # 简单 PB + 股息率 筛选
                pb = s.get("pb")
                dy = s.get("dividend_yield")
                passed_test = (
                    (pb is not None and pb <= self.config.pb_max and pb > 0) or
                    (pb is not None and pb < 0)  # 负PB也通过（特殊情况）
                )
                
                if passed_test:
                    if dy is None or dy >= self.config.dividend_yield_min or pb < 0:
                        passed.append(s)
                
                stage.records_processed += 1
            
            stage.records_passed = len(passed)
            stage.status = "done"
            stage.details = {
                "criteria": f"PB≤{self.config.pb_max}, 股息率≥{self.config.dividend_yield_min*100:.0f}%",
                "passed_stocks": [s.get("code") for s in passed],
                "pass_rate": f"{len(passed)/max(len(stocks),1)*100:.1f}%"
            }
            logger.info(f"Initial screen: {len(passed)}/{len(stocks)} passed")
            
        except Exception as e:
            stage.status = "failed"
            stage.details = {"error": str(e)}
            logger.error(f"Initial screen failed: {e}")
        
        stage.duration_ms = int((time.time() - start) * 1000)
        stage.completed_at = datetime.now().isoformat()
        return stage
    
    def _stage_ai_analysis(self, stocks: List[Dict[str, Any]],
                          callback: Optional[Callable]) -> PipelineStage:
        """阶段2：AI 深度分析"""
        stage = PipelineStage(name="ai_analysis", status="running")
        start = time.time()
        
        try:
            from engine.ai_analyzer import AIStockAnalyzer, AnalysisConfig
            
            ai_config = AnalysisConfig(
                timeout_seconds=self.config.ai_timeout_seconds,
                max_retries=self.config.ai_max_retries,
                enable_fact_check=True,
                enable_nav_calc=True,
                enable_subtype=True,
            )
            
            analyzer = AIStockAnalyzer(config=ai_config)
            results = analyzer.analyze_stocks_batch(stocks, progress_callback=callback)
            
            stage.records_processed = len(stocks)
            stage.records_passed = len(results)
            stage.status = "done"
            stage.details = {"results": results}
            logger.info(f"AI analysis: {len(results)} results")
            
        except Exception as e:
            stage.status = "failed"
            stage.details = {"error": str(e)}
            logger.error(f"AI analysis failed: {e}")
            # 返回占位结果（runtime 不可用时）
            stage.records_processed = len(stocks)
            stage.records_passed = 0
            stage.details = {
                "error": str(e),
                "note": "AI analysis unavailable outside OpenClaw runtime"
            }
        
        stage.duration_ms = int((time.time() - start) * 1000)
        stage.completed_at = datetime.now().isoformat()
        return stage
    
    def _get_ai_candidates(self, stage: PipelineStage) -> List[Dict[str, Any]]:
        """从阶段1结果中获取 AI 分析候选"""
        passed_codes = stage.details.get("passed_stocks", [])
        if not passed_codes:
            return []
        # 返回 code 列表（AI analyzer 会构建 task）
        return [{"code": c} for c in passed_codes]
    
    def _build_summary(self):
        """构建摘要"""
        if not self._report:
            return
        s = self._report.summary
        total = sum(st.get("records_processed", 0) for st in self._report.stages)
        passed = sum(st.get("records_passed", 0) for st in self._report.stages)
        s["total_stocks"] = total
        s["passed_initial_screen"] = self._report.stages[0].get("records_passed", 0) if self._report.stages else 0
        s["ai_analyzed"] = len(self._report.ai_results)
        s["pass_rate"] = f"{passed/max(total,1)*100:.1f}%"
    
    def _config_to_dict(self) -> Dict[str, Any]:
        return {
            "pb_max": self.config.pb_max,
            "dividend_yield_min": self.config.dividend_yield_min,
            "use_ai": self.config.use_ai,
            "ai_timeout": self.config.ai_timeout_seconds,
            "ai_max_retries": self.config.ai_max_retries,
        }
    
    def _finish_report(self) -> PipelineReport:
        self._report.completed_at = datetime.now().isoformat()
        return self._report


# ==============================================================================
# 便捷函数
# ==============================================================================

def run_pipeline(stocks: List[Dict[str, Any]],
                use_ai: bool = True,
                save: bool = True,
                output_dir: Optional[str] = None) -> PipelineReport:
    """一行命令执行 Pipeline"""
    pipeline = CigarButtPipeline(PipelineConfig(use_ai=use_ai))
    report = pipeline.run(stocks, use_ai=use_ai)
    if save:
        pipeline.save_report(report, output_dir)
    return report
