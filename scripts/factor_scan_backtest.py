#!/usr/bin/env python3
"""因子初筛：拿已完成回测的 trades，按 (signal_date, code) 配 daily_kbars 特征，
按 outcome_label (success/failed) 算每个特征的 point-biserial 相关系数 + 二元胜率差。

约束（L0/统计报告）：只读 DB，无 LLM 调用，无策略改动。统计口径:
- n<30 的特征柱不报相关（避免稀疏噪声）
- 正负 r_pb 同时给，不只看绝对值
- MA 字段在回填日为 0 已知；其他正常字段优先
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.db.session import engine  # noqa: E402  复用 .env 配置的 engine，不硬编码凭据

# 候选特征列(从 daily_kbars 拿，排除元数据字段)
FEATURES = [
    "pct_chg", "turnover_rate", "volume", "amount",
    "ma5", "ma10", "ma20", "ma60", "ma250",          # 已知回填日=0
    "macd_dif", "macd_dea", "macd_bar",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower", "cci",
    "bias_5", "bias_10", "bias_20",
    "vol_ratio_5", "vol_ratio_20",
    "breakout20", "breakout60", "max_dd20", "max_dd60",
    "chip_cost_15", "chip_cost_50", "chip_cost_85",
    "chip_weight_avg", "chip_winner_rate",
    "upper_shadow_count20", "holder_pct",
    "asi", "asit", "atr", "bbi", "brar_ar", "brar_br",
    "dpo", "ema_12", "ema_50", "ma_volume_5", "ma_volume_10",
    "mtm", "obv", "psy", "trix", "vr", "wr_6", "wr_10",
]


def fetch_trades_with_signals(bt_ids: list[str]):
    """从 backtest_trades 按 backtest_id 拉 trade_json，按 (signal_date,code) 配信号日 daily_kbar 特征。"""
    rows = []
    with engine.connect() as c:
        # 限定在 bt_ids 内的 trades
        placeholders = ",".join(f":b{i}" for i in range(len(bt_ids)))
        params = {f"b{i}": b for i, b in enumerate(bt_ids)}
        trades = c.execute(text(
            f"SELECT trade_json FROM backtest_trades WHERE backtest_id IN ({placeholders})"
        ), params).fetchall()
    # 解析 + 取 (signal_date, code)
    pairs = []
    for (raw,) in trades:
        try:
            j = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        sd = str(j.get("signal_date") or "")
        code = str(j.get("code") or "")
        label = j.get("outcome_label")
        ret = j.get("net_return_pct")
        exit_reason = j.get("exit_reason")
        if sd and code and label in ("success", "failed"):
            pairs.append((sd, code, label, ret, exit_reason))
    if not pairs:
        return rows
    # 批量取 daily_kbars 信号日特征
    codes = list({p[1] for p in pairs})
    min_sd = min(p[0] for p in pairs)
    max_sd = max(p[0] for p in pairs)
    with engine.connect() as c:
        feat_cols = [f for f in FEATURES if f != "id"]
        feat_sql = ", ".join(feat_cols)
        feat_rows = c.execute(text(
            f"SELECT code, trade_date, {feat_sql} FROM daily_kbars "
            "WHERE trade_date >= :sd_min AND trade_date <= :sd_max AND code IN :codes"
        ).bindparams(),
            {"sd_min": min_sd, "sd_max": max_sd, "codes": tuple(codes)},
        ).fetchall()
    feat_idx = {col: i + 2 for i, col in enumerate(FEATURES)}
    feat_map = {(r[0], str(r[1])): r for r in feat_rows}
    for sd, code, label, ret, exit_reason in pairs:
        feat = feat_map.get((code, sd))
        if feat is None:
            continue
        rows.append({
            "signal_date": sd, "code": code, "label": label,
            "net_return_pct": ret, "exit_reason": exit_reason,
            "features": {c: feat[feat_idx[c]] for c in FEATURES if c in feat_idx},
        })
    return rows


def point_biserial(xs: list[float], ys: list[int]) -> float | None:
    """point-biserial 相关系数：x 连续，y 二元 (0/1)。"""
    n = len(xs)
    if n < 30:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main() -> int:
    # 已完成的回测块
    week_files = sorted(Path("/tmp/bt_weeks").glob("*.json"))
    week_files = [p for p in week_files if "_summary" not in p.name]
    bt_ids = []
    for p in week_files:
        try:
            j = json.loads(p.read_text())
            bid = j.get("backtest_id")
            if bid:
                bt_ids.append(bid)
        except Exception:
            continue
    print(f"已完成 {len(bt_ids)} 个回测块, 拉 trade 并配特征")
    rows = fetch_trades_with_signals(bt_ids)
    print(f"配到特征的样本数: {len(rows)}")
    if len(rows) < 30:
        print("样本不足 30, 不算因子; 退出")
        return 0

    n_win = sum(1 for r in rows if r["label"] == "success")
    n_lose = len(rows) - n_win
    print(f"  win={n_win} ({n_win/len(rows)*100:.1f}%)  lose={n_lose}")

    # 按月打 reg tag(简单按 signal_date 月份)
    by_month = defaultdict(lambda: {"win": 0, "n": 0, "win_sum": 0.0, "lose_sum": 0.0})
    for r in rows:
        m = r["signal_date"][:7]
        by_month[m]["n"] += 1
        if r["label"] == "success":
            by_month[m]["win"] += 1
            by_month[m]["win_sum"] += r["net_return_pct"]
        else:
            by_month[m]["lose_sum"] += r["net_return_pct"]

    print("\n=== 逐月胜率/净收益分布 ===")
    for m in sorted(by_month):
        d = by_month[m]
        wr = d["win"] / d["n"] * 100 if d["n"] else 0
        avg = (d["win_sum"] + d["lose_sum"]) / d["n"] if d["n"] else 0
        print(f"  {m}: n={d['n']} WR={wr:.0f}% avg_net={avg:+.2f}%")

    # 因子相关
    print("\n=== 因子 point-biserial (r_pb, 与success=1) ===")
    print("(排序:|r_pb| 降序, 标MA已知0)")
    print(f"{'feature':<22} {'n_used':>7} {'n_zero':>7} {'r_pb':>8} {'wr_q4-q1':>10}  note")
    ranked = []
    for feat in FEATURES:
        xs = []; ys = []
        n_zero = 0
        for r in rows:
            v = r["features"].get(feat)
            try:
                fv = float(v) if v is not None else None
            except Exception:
                fv = None
            if fv is None:
                continue
            if fv == 0:
                n_zero += 1
            xs.append(fv)
            ys.append(1 if r["label"] == "success" else 0)
        if len(xs) < 30:
            continue
        r_pb = point_biserial(xs, ys)
        # q4-q1 胜率差(特征4分位档 vs 1分位档)
        sorted_pairs = sorted(zip(xs, ys))
        qn = len(sorted_pairs) // 4
        q1 = sorted_pairs[:qn]
        q4 = sorted_pairs[-qn:]
        wr1 = sum(y for _, y in q1) / len(q1) if q1 else 0
        wr4 = sum(y for _, y in q4) / len(q4) if q4 else 0
        ranked.append((feat, len(xs), n_zero, r_pb, wr4 - wr1))
    ranked.sort(key=lambda x: abs(x[3] or 0), reverse=True)
    for feat, n_used, n_zero, r_pb, wr_gap in ranked[:30]:
        note = " [MA=0 已知回填缺失]" if feat.startswith("ma") and n_zero > 0.9 * n_used else (" [全为0]" if n_zero == n_used else "")
        r_str = f"{r_pb:>+8.4f}" if r_pb is not None else f"{'+nan':>8}"
        print(f"{feat:<22} {n_used:>7} {n_zero:>7} {r_str} {wr_gap*100:>+9.1f}pp{note}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
