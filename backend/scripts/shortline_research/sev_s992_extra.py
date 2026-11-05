"""§994 附加判定（预注册写死）：SLV10 与现役小市值周度近似重叠率（入选票当日在 Us 内市值前 10 的占比，N3 段）≥50% 视为近亲，
需 N3 每持有日收益 > 同进程 SCW_ref（持 20）的 N3 每持有日收益才算过。用法：python sev_s992_extra.py <res.json>"""
import json, sys
r = json.load(open(sys.argv[1]))
N3 = "N3 2024-01~2026-10"
ov = r["_info"]["SLV10_h10"].get(N3, {}).get("scw_overlap")
s, b = r["SLV10_h10"][N3], r["SCW_ref"][N3]
pd_s = s["avg"] / s["days"] if s.get("days") else None
pd_b = b["avg"] / b["days"] if b.get("days") else None
near = ov is not None and ov >= 0.5
ok = (not near) or (pd_s is not None and pd_b is not None and pd_s > pd_b)
print(f"§994 附加：N3 重叠 {ov} 近亲={near} | SLV10 每持有日 {pd_s and round(pd_s, 3)} vs SCW_ref {pd_b and round(pd_b, 3)} → {'✓' if ok else '✗'}")
for p in ("R12 2025-10~2026-10", N3, "EXT 2024-01~2025-09"):
    x = r["SCW_ref"].get(p, {})
    print(f"  SCW_ref {p.split()[0]}: n={x.get('n')} avg={x.get('avg')} pf={x.get('pf')} mdd={x.get('mdd')} days={x.get('days')}")
