#!/usr/bin/env python3
"""
Issue #12 扩展筛选 - 最终验证脚本
使用确认可用的港股代码进行筛选验证
"""
import sys, json, logging, time
sys.path.insert(0, '/root/.openclaw/workspace-agent-team/cigarbuttinvest')
from data.extended_screening import _fetch_stock_metrics, _filter_stock, ScreeningCriteria, ScreeningResult, save_screening_result
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

# 已确认可用的港股代码（来自 engine/fetcher.py 的 _get_major_hk_stocks）
CONFIRMED_STOCKS = [
    "0001.HK", "0002.HK", "0003.HK", "0005.HK", "0006.HK", "0011.HK", "0016.HK",
    "0017.HK", "0019.HK", "0027.HK", "0066.HK", "0083.HK", "0175.HK", "0188.HK",
    "0267.HK", "0291.HK", "0293.HK", "0386.HK", "0390.HK", "0604.HK", "0688.HK",
    "0753.HK", "0823.HK", "0880.HK", "0941.HK", "0986.HK", "1038.HK", "1044.HK",
    "1088.HK", "1093.HK", "1109.HK", "1113.HK", "1171.HK", "1209.HK", "1211.HK",
    "1288.HK", "1336.HK", "1339.HK", "1398.HK", "1700.HK", "1755.HK", "1772.HK",
    "1789.HK", "1800.HK", "1810.HK", "1872.HK", "1908.HK", "1988.HK", "2007.HK",
    "2018.HK", "2202.HK", "2238.HK", "2282.HK", "2318.HK", "2328.HK", "2333.HK",
    "2600.HK", "2628.HK", "2888.HK", "2899.HK", "3319.HK", "3328.HK", "3333.HK",
    "3690.HK", "3888.HK", "3900.HK", "3988.HK", "6030.HK", "6837.HK", "6886.HK",
    "7600.HK", "9633.HK", "9818.HK", "9888.HK", "9900.HK", "9922.HK", "9955.HK",
    "9961.HK", "9966.HK", "9987.HK", "9988.HK", "9989.HK", "9991.HK", "9992.HK",
    "9996.HK", "9999.HK", "0700.HK", "1282.HK", "2098.HK", "2362.HK", "3311.HK",
    "3600.HK", "3800.HK", "7283.HK", "9021.HK", "9098.HK", "9668.HK", "9698.HK",
    "9800.HK", "9898.HK", "9918.HK", "9939.HK", "9973.HK", "9979.HK", "9986.HK",
    "9999.HK",
]
CONFIRMED_STOCKS = list(set(CONFIRMED_STOCKS))  # deduplicate

criteria = ScreeningCriteria()  # PB≤0.5, 股息率≥6%
print(f"筛选条件: PB≤{criteria.pb_max}, 股息率≥{criteria.dividend_yield_min*100:.0f}%")
print(f"测试 {len(CONFIRMED_STOCKS)} 只确认可用的港股...")

start = time.time()
all_data = []
errors = 0

for code in CONFIRMED_STOCKS:
    data = _fetch_stock_metrics(code)
    if data:
        all_data.append(data)
    else:
        errors += 1

print(f"成功获取 {len(all_data)} 只, 失败 {errors} 只, 耗时 {time.time()-start:.1f}秒")

passed = []
failed = []
for data in all_data:
    p, reasons = _filter_stock(data, criteria)
    data['_filter_reasons'] = reasons
    if p:
        passed.append(data)
    else:
        failed.append(data)

passed.sort(key=lambda x: x.get('pb') or 9999)
failed.sort(key=lambda x: x.get('pb') or 9999)

print(f"\n筛选结果:")
print(f"  通过: {len(passed)} 只")
print(f"  未通过: {len(failed)} 只")

if passed:
    print(f"\n✅ 通过筛选的烟蒂股候选:")
    for s in passed:
        div = s.get('dividend_yield')
        div_str = f'{div*100:.1f}%' if div else 'N/A'
        pb = s.get('pb')
        pb_str = f'{pb:.3f}' if pb else 'N/A'
        print(f"  {s.get('code')} - {s.get('name')} - PB={pb_str} - 股息率={div_str}")
else:
    print(f"\n⚠️ 无股票同时满足 PB≤0.5 且 股息率≥6%")
    print(f"\n接近条件的股票 (最低PB):")
    for s in failed[:10]:
        div = s.get('dividend_yield')
        div_str = f'{div*100:.1f}%' if div else 'N/A'
        pb = s.get('pb')
        pb_str = f'{pb:.3f}' if pb else 'N/A'
        reasons = s.get('_filter_reasons', [])
        print(f"  {s.get('code')} - {s.get('name')} - PB={pb_str} - 股息率={div_str} - {reasons[:1]}")

# 保存结果
result = ScreeningResult(
    total_stocks=len(CONFIRMED_STOCKS),
    passed_stocks=passed,
    failed_stocks=failed,
    data_errors=errors,
    criteria=criteria.to_dict()
)

output_dir = Path('docs/results/expanded')
output_dir.mkdir(parents=True, exist_ok=True)
files = save_screening_result(result)
print(f"\n结果已保存: {[f.name for f in files]}")
