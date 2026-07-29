#!/usr/bin/env python3
"""因子稳健性扫描（探索工具，非晋级依据）：在多个回测块（跨月）上分别算因子 r_pb，看方向是否一致。

数据源：/tmp/bt_weeks/*.json（已完成的单周日历回测）
输出：每个因子的"全样本 r_pb" + "逐月 r_pb" + 是否方向一致

⚠️ 重要：本脚本的「方向一致」仅是单 regime 跨月方向同号，不等同于真因子。
近零月份（|r_pb|<0.05）方向偶然同号 + 单强势月主导整体|r|，会产生伪稳健
（见 [[point-biserial-single-feature-illusion]]）。任何据此加生产规则的动作，
必须先过 feature_signal_guard + 独立 OOS（n≥30、≥10 交易日）再评估。
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.db.session import engine  # noqa: E402  复用 .env 配置的 engine，不硬编码凭据

# 仅保留"全样本有信号"且回填不为 0 的特征
FEATURES = [
    "pct_chg", "turnover_rate", "volume", "amount",
    "macd_dif", "macd_dea", "macd_bar",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower", "cci",
    "bias_5", "bias_10", "bias_20",
    "vol_ratio_5", "vol_ratio_20",
    "max_dd20", "max_dd60",
    "chip_cost_15", "chip_cost_50", "chip_cost_85",
    "chip_weight_avg", "chip_winner_rate",
    "holder_pct", "profit_efficiency",
    "asi", "asit", "atr", "bbi", "brar_ar", "brar_br",
    "dpo", "ema_12", "ema_50", "ma_volume_5", "ma_volume_10",
    "mtm", "obv", "psy", "trix", "vr", "wr_6", "wr_10",
]


def point_biserial(xs: list[float], ys: list[int]) -> float | None:
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


def fetch(bt_ids: list[str]) -> list[dict]:
    out = []
    with engine.connect() as c:
        placeholders = ",".join(f":b{i}" for i in range(len(bt_ids)))
        params = {f"b{i}": b for i, b in enumerate(bt_ids)}
        trades = c.execute(text(
            f"SELECT trade_json, backtest_id FROM backtest_trades WHERE backtest_id IN ({placeholders})"
        ), params).fetchall()
    # 收集 (signal_date, code, backtest_id)
    pairs = []
    for raw, bid in trades:
        try:
            j = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        sd = str(j.get("signal_date") or "")
        code = str(j.get("code") or "")
        label = j.get("outcome_label")
        ret = j.get("net_return_pct")
        if sd and code and label in ("success", "failed"):
            pairs.append((sd, code, bid, label, ret))
    if not pairs:
        return out
    # 批量取每日特征
    codes = list({p[1] for p in pairs})
    min_sd = min(p[0] for p in pairs)
    max_sd = max(p[0] for p in pairs)
    feat_sql = ", ".join(FEATURES)
    with engine.connect() as c:
        feat_rows = c.execute(text(
            f"SELECT code, trade_date, {feat_sql} FROM daily_kbars "
            "WHERE trade_date >= :sd_min AND trade_date <= :sd_max AND code IN :codes"
        ), {"sd_min": min_sd, "sd_max": max_sd, "codes": tuple(codes)}).fetchall()
    feat_idx = {col: i + 2 for i, col in enumerate(FEATURES)}
    feat_map = {(r[0], str(r[1])): r for r in feat_rows}
    for sd, code, bid, label, ret in pairs:
        feat = feat_map.get((code, sd))
        if feat is None:
            continue
        out.append({
            "signal_date": sd, "code": code, "bid": bid,
            "label": 1 if label == "success" else 0,
            "net_return_pct": float(ret) if ret is not None else 0.0,
            "month": sd[:7],
            "features": {c: feat[feat_idx[c]] for c in FEATURES if c in feat_idx},
        })
    return out


def main() -> int:
    week_files = sorted(Path("/tmp/bt_weeks").glob("*.json"))
    week_files = [p for p in week_files if "_summary" not in p.name]
    bt_ids = [json.loads(p.read_text()).get("backtest_id") for p in week_files]
    bt_ids = [b for b in bt_ids if b]
    # 加 4月单周
    bt_ids.append("btr_93d766836dea46ce94bc97dff2e633f7")
    bt_ids = list(set(bt_ids))
    print(f"使用 {len(bt_ids)} 个回测块")
    rows = fetch(bt_ids)
    print(f"配到特征的样本: {len(rows)}, win={sum(r['label'] for r in rows)} ({sum(r['label'] for r in rows)/len(rows)*100:.1f}%)")

    by_month = defaultdict(list)
    for r in rows:
        by_month[r["month"]].append(r)
    print(f"覆盖月份: {sorted(by_month)}")
    print()
    print("=== 逐月胜率/净收益 ===")
    for m in sorted(by_month):
        d = by_month[m]
        wr = sum(r["label"] for r in d) / len(d) * 100
        avg = sum(r["net_return_pct"] for r in d) / len(d)
        print(f"  {m}: n={len(d)} WR={wr:.0f}% avg_net={avg:+.2f}%")

    print()
    print("=== 因子稳健性（跨月 r_pb 是否一致） ===")
    print("⚠️ 仅探索：方向一致≠真因子；近零月同号+单强势月主导=伪稳健。晋级须 feature_signal_guard + 独立 OOS")
    print(f"{'feature':<22} {'all_r':>9} {'n':>5}  {'per-month r_pb':<46} sign_consistent")
    rows_sorted = sorted(rows, key=lambda r: r["signal_date"])
    # 全样本
    for feat in FEATURES:
        xs_all = []; ys_all = []
        for r in rows:
            v = r["features"].get(feat)
            try:
                fv = float(v) if v is not None else None
            except Exception:
                fv = None
            if fv is None:
                continue
            xs_all.append(fv); ys_all.append(r["label"])
        if len(xs_all) < 30:
            continue
        r_all = point_biserial(xs_all, ys_all)
        # 逐月
        per_month = []
        for m in sorted(by_month):
            xs_m = []; ys_m = []
            for r in by_month[m]:
                v = r["features"].get(feat)
                try:
                    fv = float(v) if v is not None else None
                except Exception:
                    fv = None
                if fv is None: continue
                xs_m.append(fv); ys_m.append(r["label"])
            r_m = point_biserial(xs_m, ys_m)
            per_month.append((m, r_m))
        # 方向一致性
        non_na = [(m, v) for m, v in per_month if v is not None]
        if r_all is None or not non_na:
            consistent = "?"
        else:
            same_sign = sum(1 for _, v in non_na if (r_all > 0) == (v > 0))
            ratio = same_sign / len(non_na)
            consistent = f"{same_sign}/{len(non_na)}"
        # wr_q4-q1 差
        sorted_pairs = sorted(zip(xs_all, ys_all))
        n = len(sorted_pairs); qn = n // 4
        q1 = sorted_pairs[:qn]; q4 = sorted_pairs[-qn:]
        wr1 = sum(y for _, y in q1) / len(q1) if q1 else 0
        wr4 = sum(y for _, y in q4) / len(q4) if q4 else 0
        # 仅打印 |r_all|≥0.05 的
        if r_all is None or abs(r_all) < 0.04:
            continue
        per_str = "  ".join(f"{m[-2:]}:{v:+.3f}" if v is not None else f"{m[-2:]}:n/a" for m, v in per_month)
        print(f"{feat:<22} {r_all:>+9.4f} {len(xs_all):>5}  {per_str:<46} {consistent}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
