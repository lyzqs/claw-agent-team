"""
香港联交所交易日历
用于判断是否为交易日（跳过周末和香港节假日）
"""

from datetime import datetime, timedelta
from typing import List, Tuple
import calendar


# 香港联交所2026年节假日（已知的固定节假日）
# 实际使用时建议从 akshare 或其他数据源获取最新节假日列表
HKEX_HOLIDAYS_2026 = [
    # 元旦
    "2026-01-01",
    # 农历新年
    "2026-02-17",  # 正月初一
    "2026-02-18",  # 正月初二
    "2026-02-19",  # 正月初三
    # 清明节
    "2026-04-05",
    "2026-04-06",
    # 劳动节
    "2026-05-01",
    # 佛诞
    "2026-05-07",
    # 端午节
    "2026-05-31",
    # 香港回归纪念日
    "2026-07-01",
    # 中秋节
    "2026-09-28",
    "2026-09-29",
    # 国庆节
    "2026-10-01",
    "2026-10-02",
    # 重阳节
    "2026-10-17",
    # 圣诞节
    "2026-12-25",
    "2026-12-26",
]


def is_trading_day(date: datetime = None) -> bool:
    """
    判断指定日期是否为交易日
    
    Args:
        date: 要判断的日期，默认为今天
    
    Returns:
        True 如果是交易日，否则 False
    """
    if date is None:
        date = datetime.now()
    
    # 检查是否为周末
    if date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    
    # 检查是否为节假日
    date_str = date.strftime("%Y-%m-%d")
    if date_str in HKEX_HOLIDAYS_2026:
        return False
    
    return True


def get_next_trading_day(date: datetime = None) -> datetime:
    """
    获取下一个交易日
    
    Args:
        date: 参考日期，默认为今天
    
    Returns:
        下一个交易日的日期
    """
    if date is None:
        date = datetime.now()
    
    # 从明天开始查找
    check_date = date + timedelta(days=1)
    max_days = 14  # 最多查找14天（覆盖两个周末）
    
    for _ in range(max_days):
        if is_trading_day(check_date):
            return check_date
        check_date += timedelta(days=1)
    
    return check_date  # 返回找到的日期


def get_trading_days_range(
    start_date: datetime,
    end_date: datetime
) -> List[datetime]:
    """
    获取指定日期范围内的所有交易日
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
    
    Returns:
        交易日列表
    """
    trading_days = []
    current = start_date
    
    while current <= end_date:
        if is_trading_day(current):
            trading_days.append(current)
        current += timedelta(days=1)
    
    return trading_days


def is_market_open_time() -> bool:
    """
    判断当前是否在交易时间内
    香港联交所交易时间：
    - 上午: 09:30 - 12:00
    - 下午: 13:00 - 16:00
    
    Returns:
        True 如果在交易时间内
    """
    now = datetime.now()
    
    # 检查是否为交易日
    if not is_trading_day(now):
        return False
    
    hour = now.hour
    minute = now.minute
    
    # 上午交易时段: 09:30 - 12:00
    morning_start = (hour == 9 and minute >= 30) or (hour > 9 and hour < 12)
    morning_end = hour == 11 or (hour == 12 and minute == 0)
    
    # 下午交易时段: 13:00 - 16:00
    afternoon_start = hour >= 13
    afternoon_end = hour < 16
    
    return (morning_start and not morning_end) or (afternoon_start and afternoon_end)


def get_next_screening_time() -> datetime:
    """
    获取下一个筛选任务的执行时间
    筛选任务在每个交易日 9:30 执行
    
    Returns:
        下一次执行时间
    """
    now = datetime.now()
    today = now.date()
    
    # 今天 9:30
    target_hour, target_minute = 9, 30
    
    # 检查今天是否是交易日且还没到执行时间
    today_dt = datetime.combine(today, datetime.min.time().replace(
        hour=target_hour, minute=target_minute
    ))
    
    if is_trading_day(now) and now < today_dt:
        return today_dt
    
    # 否则找下一个交易日 9:30
    next_day = get_next_trading_day(now)
    return datetime.combine(
        next_day.date(),
        datetime.min.time().replace(hour=target_hour, minute=target_minute)
    )


if __name__ == "__main__":
    # 测试
    today = datetime.now()
    print(f"今天是: {today.strftime('%Y-%m-%d %A')}")
    print(f"是否为交易日: {is_trading_day(today)}")
    print(f"下一个交易日: {get_next_trading_day(today).strftime('%Y-%m-%d')}")
    print(f"下一个执行时间: {get_next_screening_time().strftime('%Y-%m-%d %H:%M')}")
    print(f"是否在交易时间内: {is_market_open_time()}")
