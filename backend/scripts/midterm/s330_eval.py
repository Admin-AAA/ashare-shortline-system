"""§330/§331 附加判定（预注册写定）：
  §330 SCW5_h5：R12 与 N3 的每持有日收益（每笔 ÷ 平均持有天数）都高于同进程 SCW_ref（现役小市值周度，持 20 日）。
  §331 HOL5_h5：R12 与 N3 每笔都高于同进程 SCW5_h5（节前择时必须比普通每周换仓更赚），且每持有日收益都高于 SCW_ref。
  基本 5 条由 s286_287_eval.py（短线回撤上限 35）判定；本脚本只打印附加条件与每持有日收益对照。
用法：python s330_eval.py <res.json>"""
import json, sys
res = json.load(open(sys.argv[1]))
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", R12]


def g(n, ph, k):
    return res.get(n, {}).get(ph, {}).get(k)


print("每持有日收益（%/交易日）：")
for n in ("SCW5_h5", "HOL5_h5", "SCW_ref"):
    print(f"  {n}: " + " ".join(f"{p.split()[0]} {g(n, p, 'per_day')}" for p in P8 + [N3])
          + f" | 平均持有 R12 {g(n, R12, 'days')} N3 {g(n, N3, 'days')}")
a = all((g("SCW5_h5", p, "per_day") or -99) > (g("SCW_ref", p, "per_day") or 99) for p in (R12, N3))
b1 = all((g("HOL5_h5", p, "avg") or -99) > (g("SCW5_h5", p, "avg") or 99) for p in (R12, N3))
b2 = all((g("HOL5_h5", p, "per_day") or -99) > (g("SCW_ref", p, "per_day") or 99) for p in (R12, N3))
print(f"§330 附加（每持有日 > SCW_ref，R12 与 N3）：{'✓' if a else '✗'}")
print(f"§331 附加（每笔 > SCW5_h5：{'✓' if b1 else '✗'}；每持有日 > SCW_ref：{'✓' if b2 else '✗'}）")
for p in P8 + [N3]:
    i = res.get("_info", {}).get(p, {})
    print(f"  INFO {p.split()[0]}: 长假 {len(i.get('holiday_L', []))} 个，闸门开 {i.get('hol_gate_open')}；SCW 信号周 {i.get('scw_sigdays')}，涉及股票 {i.get('scw_n_codes')}，市值中位 {i.get('scw_mvd_med_yi')} 亿")
