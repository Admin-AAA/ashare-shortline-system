from app.services.evaluation_metrics import compute_full_metrics
from app.services.outcome_service import _ADDITIONAL_COST_PCT


def mk(net, executed=True, td="2024-01-01"):
    return {
        "net_return_pct": net,
        "executed": executed,
        "trade_date": td,
        "code": "X",
        "gross_return_pct": net,
        "achievable_return_pct": net,
    }


# 5 笔微盈(0.08%) + 3 笔盈利(2.0%) + 2 笔亏损(-1.0%) + 1 笔未成交(0)
trades = (
    [mk(0.08, td=f"2024-01-0{i}") for i in range(1, 6)]
    + [mk(2.0, td=f"2024-02-0{i}") for i in range(1, 4)]
    + [mk(-1.0, td=f"2024-03-0{i}") for i in range(1, 3)]
    + [mk(0.0, executed=False, td="2024-04-01")]
)

m = compute_full_metrics(trades)
print("=" * 60)
print("策略页指标（来自真实 compute_full_metrics）")
print("=" * 60)
print(f"胜率 hit_rate          = {m['hit_rate']}")
print(f"盈亏比 profit_loss_ratio = {m['profit_loss_ratio']}")
print(f"平均净收益 avg_return   = {m['average_net_return']}")
print(f"已成交笔数(胜率分母)    = {m['executed_trade_count']}")
print(f"总样本 trade_count      = {m['trade_count']}")
print(f"DB 成功阈值 _ADDITIONAL_COST_PCT = {_ADDITIONAL_COST_PCT}")
print()
print("逐笔归类对比：")
print(f"{'net%':>8} {'成交':>4} | {'盈亏比归类':>8} | {'胜率归类':>8}")
print("-" * 40)
for t in trades:
    net = t["net_return_pct"]
    ex = t["executed"]
    plr = "盈" if net > 0 else ("亏" if net < 0 else "平")
    hit = "赢" if (ex and net > 0.10) else ("输" if ex else "未成交")
    print(f"{net:>8} {str(ex):>4} | {plr:>8} | {hit:>8}")

print()
print("结论验证：")
micro = [t for t in trades if 0 < t["net_return_pct"] <= 0.10]
print(f"  net∈(0,0.10] 的微盈交易数 = {len(micro)} 笔")
print(f"    这 {len(micro)} 笔在『盈亏比』里被算作：盈利（计入 wins）")
print(f"    这 {len(micro)} 笔在『胜率』里被算作：未盈利（不算 accurate）")
print(f"  → 同一笔交易在两个指标里被归到不同阵营，盈/亏边界不统一")
