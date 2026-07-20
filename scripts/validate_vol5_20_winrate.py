"""验证 vol5_20 对全量 LIVE formal 样本胜率的预测力（point-biserial + 分段胜率）。

针对 no_follow_through 毒区 07-16 簇的 vol5_20∈[3.75,3.95] 极致放量现象，
检验「vol5_20 越高 → 胜率越低」是否在全量样本（非仅 18 笔失败）成立，
避免对 18 笔小样本过拟合。
"""
from __future__ import annotations

import json
import math
from statistics import mean

import pymysql

CONN = dict(host="127.0.0.1", port=3306, user="aowang", password="1279990918",
            db="ashare_workbench", cursorclass=pymysql.cursors.DictCursor)


def point_biserial(xs, ys):
    """xs=连续特征, ys=0/1 标签。返回 (r, n)。None 剔除。"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 10:
        return None, n
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    n1 = sum(ys); n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None, n
    m1 = mean([x for x, y in pairs if y == 1])
    m0 = mean([x for x, y in pairs if y == 0])
    sd = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    if sd == 0:
        return None, n
    r = (m1 - m0) / sd * math.sqrt(n1 * n0 / (n * n))
    return r, n


def main():
    conn = pymysql.connect(**CONN)
    cur = conn.cursor()
    cur.execute("""
        SELECT r.feature_snapshot_json, o.outcome_label, o.executed_return, o.max_return
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode='formal'
        WHERE o.outcome_label IN ('success','failed') AND r.recommendation_id LIKE 'rec_%'
    """)
    rows = cur.fetchall()
    conn.close()

    vol = []; label = []; ret = []
    for r in rows:
        fs = json.loads(r["feature_snapshot_json"]) if r["feature_snapshot_json"] else {}
        v = fs.get("vol5_20")
        y = 1 if r["outcome_label"] == "success" else 0
        vol.append(v); label.append(y)
        ret.append(r["executed_return"])

    r_pb, n = point_biserial(vol, label)
    print(f"vol5_20 point-biserial(对胜负): r={r_pb and round(r_pb,4)} n={n}")

    # 分段胜率（净收益已含 executed_return，未扣费；扣费口径另算）
    buckets = [("<1.0", lambda v: v < 1.0), ("1.0-2.0", lambda v: 1.0 <= v < 2.0),
               ("2.0-3.0", lambda v: 2.0 <= v < 3.0), ("3.0-3.5", lambda v: 3.0 <= v < 3.5),
               ("3.5-4.0", lambda v: 3.5 <= v < 4.0), (">=4.0", lambda v: v >= 4.0)]
    print("\n=== vol5_20 分段胜率（全量 LIVE formal）===")
    for name, fn in buckets:
        sub = [(v, y, rt) for v, y, rt in zip(vol, label, ret) if v is not None and fn(v)]
        if not sub:
            print(f"  {name}: n=0")
            continue
        wins = sum(1 for _, y, _ in sub if y == 1)
        wr = wins / len(sub) * 100
        avg_ret = mean([rt for _, _, rt in sub if rt is not None])
        print(f"  {name}: n={len(sub):3d} 胜率={wr:.1f}% 均净收益={avg_ret:.2f}%")

    # 联合：vol5_20 高 且 pos60 高（追高放量）
    print("\n=== vol5_20>=3.5 且 pos60>=70 的胜率 ===")
    cur2 = []
    for r in rows:
        fs = json.loads(r["feature_snapshot_json"]) if r["feature_snapshot_json"] else {}
        v = fs.get("vol5_20"); p = fs.get("pos60")
        if v is not None and p is not None:
            cur2.append((v, p, 1 if r["outcome_label"] == "success" else 0, r["executed_return"]))
    hi = [c for c in cur2 if c[0] >= 3.5 and c[1] >= 70]
    lo = [c for c in cur2 if not (c[0] >= 3.5 and c[1] >= 70)]
    for tag, grp in [("vol>=3.5 & pos60>=70", hi), ("其余", lo)]:
        if grp:
            wr = sum(1 for c in grp if c[2] == 1) / len(grp) * 100
            ar = mean([c[3] for c in grp if c[3] is not None])
            print(f"  {tag}: n={len(grp)} 胜率={wr:.1f}% 均净={ar:.2f}%")


if __name__ == "__main__":
    main()
