#!/usr/bin/env python3
"""
CigarButtInvest 主入口脚本

用法:
    python -m cigarbuttinvest.main              # 运行每日筛选
    python -m cigarbuttinvest.main --dry-run   # 试运行模式
    python -m cigarbuttinvest.main --status    # 查看运行状态
    python -m cigarbuttinvest.main --report    # 生成状态报告
"""

import sys
import argparse
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="CigarButtInvest 港股烟蒂股筛选系统",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # 每日筛选
    run_parser = subparsers.add_parser("run", help="运行每日筛选")
    run_parser.add_argument("--dry-run", action="store_true", help="试运行模式")
    run_parser.add_argument("--force", action="store_true", help="强制运行（忽略交易日检查）")
    run_parser.add_argument("--run-id", type=str, help="指定运行ID")
    
    # 查看状态
    status_parser = subparsers.add_parser("status", help="查看任务状态")
    
    # 生成报告
    report_parser = subparsers.add_parser("report", help="生成状态报告")
    
    # 健康检查
    health_parser = subparsers.add_parser("health", help="运行健康检查")
    
    # 部署命令
    deploy_parser = subparsers.add_parser("deploy", help="部署定时任务")
    deploy_parser.add_argument("--systemd", action="store_true", help="使用 systemd 部署")
    
    args = parser.parse_args()
    
    if args.command == "run":
        from scheduler.daily_job import run_daily_screening
        import logging
        
        logging.basicConfig(level=logging.INFO)
        
        run_id = args.run_id or __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        
        result = run_daily_screening(
            run_id=run_id,
            dry_run=args.dry_run
        )
        
        print(f"\n任务完成 - 状态: {result['status']}")
        print(f"运行ID: {result['run_id']}")
        
        if result.get("errors"):
            print(f"错误数: {len(result['errors'])}")
            for err in result["errors"]:
                print(f"  - {err}")
        
        return 0 if result["status"] in ["success", "skipped"] else 1
    
    elif args.command == "status":
        from monitor.task_monitor import TaskMonitor
        
        monitor = TaskMonitor()
        failure_check = monitor.check_consecutive_failures()
        
        print("=== CigarButtInvest 运行状态 ===")
        print(f"连续失败次数: {failure_check['consecutive_failures']}")
        print(f"总失败次数: {failure_check['total_failures']}")
        print(f"上次失败: {failure_check['last_failure_date'] or 'N/A'}")
        print(f"上次成功: {failure_check['last_success_date'] or 'N/A'}")
        
        return 0
    
    elif args.command == "report":
        from monitor.task_monitor import TaskMonitor
        
        monitor = TaskMonitor()
        report = monitor.generate_status_report()
        print(report)
        
        # 保存报告
        report_path = PROJECT_ROOT / "docs" / "daily_runs" / "status_report.md"
        report_path.parent.mkdir(exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n报告已保存: {report_path}")
        
        return 0
    
    elif args.command == "health":
        from monitor.task_monitor import run_health_check
        
        result = run_health_check()
        
        if result:
            print("✅ 健康检查通过")
            return 0
        else:
            print("❌ 健康检查失败")
            return 1
    
    elif args.command == "deploy":
        if args.systemd:
            print("部署 systemd 服务...")
            print(f"\n请执行以下命令部署定时任务:")
            print(f"\n1. 复制 service 文件:")
            print(f"   cp {PROJECT_ROOT}/docs/config/cigarbuttinvest-screening.service /etc/systemd/system/")
            print(f"   cp {PROJECT_ROOT}/docs/config/cigarbuttinvest-screening.timer /etc/systemd/system/")
            print(f"   cp {PROJECT_ROOT}/docs/config/cigarbuttinvest-healthcheck.service /etc/systemd/system/")
            print(f"   cp {PROJECT_ROOT}/docs/config/cigarbuttinvest-healthcheck.timer /etc/systemd/system/")
            print(f"\n2. 重载 systemd:")
            print(f"   sudo systemctl daemon-reload")
            print(f"\n3. 启用并启动定时器:")
            print(f"   sudo systemctl enable cigarbuttinvest-screening.timer")
            print(f"   sudo systemctl enable cigarbuttinvest-healthcheck.timer")
            print(f"   sudo systemctl start cigarbuttinvest-screening.timer")
            print(f"   sudo systemctl start cigarbuttinvest-healthcheck.timer")
            print(f"\n4. 查看状态:")
            print(f"   systemctl list-timers --all | grep cigarbuttinvest")
            return 0
        else:
            print("使用 cron 部署...")
            cron_cmd = f"25 9 * * 1-5 cd {PROJECT_ROOT} && python3 -m cigarbuttinvest.scheduler.daily_job >> logs/daily_job.log 2>&1"
            print(f"\n请在 crontab 中添加以下行:")
            print(f"\n{cron_cmd}")
            return 0
    
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
