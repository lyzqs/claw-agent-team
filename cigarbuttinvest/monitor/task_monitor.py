"""
任务监控与告警模块
监控每日筛选任务的执行状态，在出现异常时发送告警
"""

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from enum import Enum

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class TaskMonitor:
    """任务监控器"""
    
    def __init__(
        self,
        alert_enabled: bool = True,
        alert_channels: List[str] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化监控器
        
        Args:
            alert_enabled: 是否启用告警
            alert_channels: 告警渠道列表（email, feishu, etc.）
            logger: 日志记录器
        """
        self.alert_enabled = alert_enabled
        self.alert_channels = alert_channels or ["log"]
        self.logger = logger or logging.getLogger("cigarbuttinvest.monitor")
        
        # 加载配置
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """加载监控配置"""
        config_path = PROJECT_ROOT / "docs" / "config" / "monitor_config.json"
        
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        # 默认配置
        return {
            "alert_thresholds": {
                "max_execution_time_seconds": 600,  # 10 分钟
                "max_failed_retries": 3,
                "max_consecutive_failures": 3,
                "min_stocks_processed": 10,
            },
            "alert_channels": {
                "log": {"enabled": True},
                "email": {"enabled": False, "recipients": []},
                "feishu": {"enabled": False, "webhook_url": ""}
            },
            "notification": {
                "on_success": False,
                "on_failure": True,
                "on_partial": True
            }
        }
    
    def check_task_health(self, run_id: str) -> Dict[str, Any]:
        """
        检查任务健康状态
        
        Args:
            run_id: 运行唯一标识
        
        Returns:
            健康检查结果
        """
        health = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "is_healthy": True,
            "issues": [],
            "warnings": []
        }
        
        # 检查运行记录
        run_record_path = PROJECT_ROOT / "docs" / "daily_runs" / f"run_{run_id}.json"
        
        if not run_record_path.exists():
            health["is_healthy"] = False
            health["issues"].append({
                "type": "missing_record",
                "message": f"运行记录文件不存在: {run_id}"
            })
            return health
        
        # 读取运行记录
        with open(run_record_path, "r", encoding="utf-8") as f:
            run_record = json.load(f)
        
        # 检查执行时间
        if "duration_seconds" in run_record:
            max_time = self.config["alert_thresholds"]["max_execution_time_seconds"]
            if run_record["duration_seconds"] > max_time:
                health["warnings"].append({
                    "type": "slow_execution",
                    "message": f"执行时间过长: {run_record['duration_seconds']:.0f}秒 (阈值: {max_time}秒)"
                })
        
        # 检查失败步骤
        failed_steps = [s for s in run_record.get("steps", []) if s.get("status") == "failed"]
        if failed_steps:
            health["is_healthy"] = False
            for step in failed_steps:
                health["issues"].append({
                    "type": "failed_step",
                    "message": f"步骤失败: {step.get('name', 'unknown')}"
                })
        
        # 检查错误列表
        if run_record.get("errors"):
            health["is_healthy"] = False
            for error in run_record["errors"]:
                health["issues"].append({
                    "type": error.get("type", "unknown"),
                    "message": error.get("error", "Unknown error")
                })
        
        return health
    
    def check_consecutive_failures(self, lookback_days: int = 7) -> Dict[str, Any]:
        """
        检查连续失败情况
        
        Args:
            lookback_days: 回溯天数
        
        Returns:
            连续失败检查结果
        """
        results = {
            "lookback_days": lookback_days,
            "consecutive_failures": 0,
            "total_failures": 0,
            "last_failure_date": None,
            "last_success_date": None,
            "should_alert": False
        }
        
        # 获取所有运行记录
        run_dir = PROJECT_ROOT / "docs" / "daily_runs"
        if not run_dir.exists():
            return results
        
        # 读取最近几天内的记录
        cutoff_date = datetime.now() - timedelta(days=lookback_days)
        run_records = []
        
        for record_file in run_dir.glob("run_*.json"):
            try:
                mtime = datetime.fromtimestamp(record_file.stat().st_mtime)
                if mtime >= cutoff_date:
                    with open(record_file, "r", encoding="utf-8") as f:
                        run_records.append((mtime, json.load(f)))
            except Exception:
                continue
        
        # 按时间排序
        run_records.sort(key=lambda x: x[0], reverse=True)
        
        # 统计失败
        consecutive = 0
        for _, record in run_records:
            status = record.get("status", "unknown")
            
            if status == "failed":
                results["total_failures"] += 1
                if results["last_failure_date"] is None:
                    results["last_failure_date"] = record.get("start_time", "").split("T")[0]
                consecutive += 1
            else:
                if results["last_success_date"] is None and status == "success":
                    results["last_success_date"] = record.get("start_time", "").split("T")[0]
                consecutive = 0
            
            results["consecutive_failures"] = max(results["consecutive_failures"], consecutive)
        
        # 判断是否需要告警
        threshold = self.config["alert_thresholds"]["max_consecutive_failures"]
        results["should_alert"] = results["consecutive_failures"] >= threshold
        
        return results
    
    def send_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        发送告警
        
        Args:
            level: 告警级别
            title: 告警标题
            message: 告警消息
            context: 上下文数据
        """
        if not self.alert_enabled:
            return
        
        alert_data = {
            "level": level.value,
            "title": title,
            "message": message,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        
        self.logger.log(
            logging.WARNING if level in [AlertLevel.WARNING, AlertLevel.ERROR] else logging.INFO,
            f"[{level.value.upper()}] {title}: {message}"
        )
        
        # 根据告警渠道发送
        for channel in self.alert_channels:
            try:
                if channel == "log":
                    self._alert_to_log(alert_data)
                elif channel == "email":
                    self._alert_to_email(alert_data)
                elif channel == "feishu":
                    self._alert_to_feishu(alert_data)
            except Exception as e:
                self.logger.error(f"Failed to send alert via {channel}: {e}")
    
    def _alert_to_log(self, alert_data: Dict[str, Any]):
        """记录到日志"""
        log_file = LOG_DIR / "alerts.log"
        log_file.parent.mkdir(exist_ok=True)
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert_data, ensure_ascii=False) + "\n")
    
    def _alert_to_email(self, alert_data: Dict[str, Any]):
        """发送邮件告警"""
        email_config = self.config.get("alert_channels", {}).get("email", {})
        
        if not email_config.get("enabled"):
            return
        
        recipients = email_config.get("recipients", [])
        if not recipients:
            return
        
        # 构建邮件
        msg = MIMEMultipart()
        msg["Subject"] = f"[{alert_data['level'].upper()}] {alert_data['title']}"
        msg["From"] = email_config.get("from", "noreply@cigarbuttinvest.local")
        
        body = f"""
{alert_data['message']}

时间: {alert_data['timestamp']}
级别: {alert_data['level']}

上下文:
{json.dumps(alert_data['context'], indent=2, ensure_ascii=False)}
"""
        msg.attach(MIMEText(body, "plain", "utf-8"))
        
        # 发送邮件
        try:
            smtp_config = email_config.get("smtp", {})
            with smtplib.SMTP(
                smtp_config.get("host", "localhost"),
                smtp_config.get("port", 25)
            ) as server:
                if smtp_config.get("use_tls"):
                    server.starttls()
                if smtp_config.get("username"):
                    server.login(smtp_config["username"], smtp_config["password"])
                
                for recipient in recipients:
                    msg["To"] = recipient
                    server.send_message(msg)
        except Exception as e:
            self.logger.error(f"Failed to send email: {e}")
    
    def _alert_to_feishu(self, alert_data: Dict[str, Any]):
        """发送飞书告警"""
        feishu_config = self.config.get("alert_channels", {}).get("feishu", {})
        
        if not feishu_config.get("enabled"):
            return
        
        webhook_url = feishu_config.get("webhook_url")
        if not webhook_url:
            return
        
        import requests
        
        # 构建飞书消息卡片
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"🚨 [{alert_data['level'].upper()}] {alert_data['title']}"},
                    "template": "red" if alert_data["level"] in ["error", "critical"] else "orange"
                },
                "elements": [
                    {"tag": "markdown", "content": alert_data["message"]},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"⏰ 时间: {alert_data['timestamp']}"}}
                ]
            }
        }
        
        try:
            response = requests.post(webhook_url, json=card, timeout=10)
            response.raise_for_status()
        except Exception as e:
            self.logger.error(f"Failed to send Feishu alert: {e}")
    
    def generate_status_report(self) -> str:
        """
        生成当前状态报告
        
        Returns:
            Markdown 格式的状态报告
        """
        # 获取今日运行记录
        today = datetime.now().strftime("%Y%m%d")
        run_dir = PROJECT_ROOT / "docs" / "daily_runs"
        
        today_records = []
        if run_dir.exists():
            for record_file in run_dir.glob(f"run_{today}_*.json"):
                try:
                    with open(record_file, "r", encoding="utf-8") as f:
                        today_records.append(json.load(f))
                except Exception:
                    continue
        
        # 构建报告
        report = f"""# CigarButtInvest 监控状态报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 今日运行状态

"""
        
        if not today_records:
            report += "> 今日暂无运行记录\n\n"
        else:
            for record in today_records:
                status_emoji = {
                    "success": "✅",
                    "failed": "❌",
                    "partial": "⚠️",
                    "skipped": "⏭️"
                }
                status = record.get("status", "unknown")
                emoji = status_emoji.get(status, "❓")
                
                report += f"""### 运行 {record.get('run_id', 'N/A')} {emoji}

- **状态**: {status}
- **开始时间**: {record.get('start_time', 'N/A')}
- **耗时**: {record.get('duration_seconds', 0):.1f} 秒

"""
        
        # 添加连续失败检查
        failure_check = self.check_consecutive_failures()
        report += f"""## 连续失败检查（近 {failure_check['lookback_days']} 天）

- **连续失败次数**: {failure_check['consecutive_failures']}
- **总失败次数**: {failure_check['total_failures']}
- **上次失败**: {failure_check['last_failure_date'] or 'N/A'}
- **上次成功**: {failure_check['last_success_date'] or 'N/A'}

"""
        
        # 添加告警阈值
        thresholds = self.config.get("alert_thresholds", {})
        report += f"""## 告警阈值

| 指标 | 阈值 |
|------|------|
| 最大执行时间 | {thresholds.get('max_execution_time_seconds', 'N/A')} 秒 |
| 最大重试次数 | {thresholds.get('max_failed_retries', 'N/A')} |
| 最大连续失败 | {thresholds.get('max_consecutive_failures', 'N/A')} |
| 最小处理股票数 | {thresholds.get('min_stocks_processed', 'N/A')} |

"""
        
        return report


def run_health_check() -> bool:
    """
    运行健康检查
    
    Returns:
        True 如果健康检查通过
    """
    monitor = TaskMonitor()
    logger = logging.getLogger("cigarbuttinvest.health_check")
    
    # 检查今日运行记录
    today = datetime.now().strftime("%Y%m%d")
    run_dir = PROJECT_ROOT / "docs" / "daily_runs"
    
    latest_run_id = None
    if run_dir.exists():
        runs = list(run_dir.glob(f"run_{today}_*.json"))
        if runs:
            runs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            latest_run_id = runs[0].stem[4:]  # 去掉 "run_" 前缀
    
    if not latest_run_id:
        logger.warning("今日无运行记录")
        monitor.send_alert(
            AlertLevel.WARNING,
            "无运行记录",
            "今日尚未执行任何筛选任务",
            {"run_date": today}
        )
        return False
    
    # 检查健康状态
    health = monitor.check_task_health(latest_run_id)
    
    if not health["is_healthy"]:
        logger.error(f"健康检查失败: {health['issues']}")
        monitor.send_alert(
            AlertLevel.ERROR,
            "任务执行异常",
            f"任务 {latest_run_id} 执行失败",
            health
        )
        return False
    
    # 检查连续失败
    failure_check = monitor.check_consecutive_failures()
    if failure_check["should_alert"]:
        logger.error(f"连续失败告警: {failure_check['consecutive_failures']} 次")
        monitor.send_alert(
            AlertLevel.ERROR,
            "连续任务失败",
            f"连续 {failure_check['consecutive_failures']} 次任务失败",
            failure_check
        )
        return False
    
    logger.info("健康检查通过")
    return True


if __name__ == "__main__":
    # 测试
    monitor = TaskMonitor()
    health = monitor.check_consecutive_failures()
    print(f"连续失败: {health}")
    
    status_report = monitor.generate_status_report()
    print(status_report)