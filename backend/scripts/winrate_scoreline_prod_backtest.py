"""生产口径分数线对比回测（frozen snapshot + 因果出场 + 费用）。

回答：当前 0 出票是分数线 85-91 拦掉 65-78 分段（正期望）所致。降到多少合适？
与 PATH-A 不同：本脚本用 frozen snapshot 的生产 final_score（多策略聚合+quality_cap），
分数尺度与生产一致，可真实评估分数线门槛。只读，不写库。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.board_rules import limit_pct_for_code
from app.services.outcome_simulation import _resolve_exit_causal

SNAP = Path(__file__).resolve().parents[1] / "output" / "frozen_snapshots" / "holdout_244d.json"

# 生产执行参数（与 CHAMPION_CONFIG / 生产 policy 对齐）
STOP_LOSS = -4.5
TAKE_PROFIT = 6.0
HORIZON = 5
FEE_PCT = (0.0001 * 2 + 0.0005) * 100  # 佣金来回+印花税
SLIP = 10.0  # bps
MAX_ENTRY_GAP = 10.0
TOP_N = 6  # 生产 output_count


def _to_bar_obj(b, pct_chg=0.0):
    """frozen snapshot 的 bar 是 dict；因果出场引擎要属性访问的对象。

    修复：outcome_bars 无 amount，而出场可卖性闸门(no_liquidity_reason_from_bar)要求
    volume>0 且 amount>0；缺 amount 会把每根 bar 判为卖不出、因果出场退化为持有到期。
    用 volume*close 作成交额正值代理（仅过 >0 闸门，成交价只用 OHLC）；pct_chg 供一字板判定。
    """
    vol = float(b.get("volume") or 0)
    cls = float(b.get("close") or 0)
    return type("Bar", (), {
        "trade_date": b.get("trade_date"), "open": b.get("open"), "high": b.get("high"),
        "low": b.get("low"), "close": b.get("close"), "volume": b.get("volume"),
        "amount": vol * cls, "pct_chg": pct_chg,
        "tradable": b.get("tradable", True),
    })()


def simulate(day, score_line, top_n=TOP_N):
    """对一天：选 final_score>=score_line 的 top_n，T+1入场模拟，返回交易列表。"""
    cards = [c for c in day["cards"] if float(c.get("final_score") or 0) >= score_line]
    cards.sort(key=lambda c: float(c.get("final_score") or 0), reverse=True)
    cards = cards[:top_n]
    ob = day["outcome_bars"]
    trades = []
    for c in cards:
        code = c["code"]
        raw_bars = ob.get(code) or []
        if len(raw_bars) < 2:
            continue
        entry_bar = raw_bars[0]
        entry_open = float(entry_bar.get("open") or 0)
        if entry_open <= 0 or not entry_bar.get("tradable", True):
            continue
        sig_close = float(c.get("close") or 0)
        gap = (entry_open / sig_close - 1) * 100 if sig_close > 0 else 0
        if gap > MAX_ENTRY_GAP:
            continue
        entry_price = entry_open * (1 + SLIP / 1e4)
        holding_raw = raw_bars[: HORIZON + 1]
        holding = []
        prev_close = sig_close if sig_close > 0 else 0.0
        for b in holding_raw:
            bc = float(b.get("close") or 0)
            pct = (bc / prev_close - 1) * 100 if prev_close > 0 and bc > 0 else 0.0
            holding.append(_to_bar_obj(b, pct))
            if bc > 0:
                prev_close = bc
        try:
            exit_px, exit_date, reason = _resolve_exit_causal(
                holding,
                stop_loss_price=entry_price * (1 + STOP_LOSS / 100),
                take_profit_price=entry_price * (1 + TAKE_PROFIT / 100),
                code=code,
                entry_price=entry_price,
                dynamic_trailing=True,
                trailing_policy="profit_floor",
                profit_lock_ratio=0.5,
                activate_pct=None,
                min_lock_pct=None,
            )
        except Exception:
            continue
        if not exit_px or exit_px <= 0:
            continue
        exit_price = exit_px * (1 - SLIP / 1e4)
        net = (exit_price / entry_price - 1) * 100 - FEE_PCT
        trades.append({"net": net, "reason": reason, "score": float(c.get("final_score") or 0)})
    return trades


def evaluate(days, score_line, label):
    all_t = []
    days_with = 0
    for day in days:
        t = simulate(day, score_line)
        if t:
            days_with += 1
            all_t.extend(t)
    if not all_t:
        print(f"  线={score_line}: 0 笔")
        return None
    n = len(all_t)
    wr = sum(1 for x in all_t if x["net"] > 0) / n * 100
    avg = sum(x["net"] for x in all_t) / n
    aw = [x["net"] for x in all_t if x["net"] > 0]
    al = [x["net"] for x in all_t if x["net"] <= 0]
    pl = (sum(aw) / len(aw)) / abs(sum(al) / len(al)) if aw and al else 0
    pf = sum(aw) / abs(sum(al)) if al else 0
    return dict(line=score_line, n=n, days=days_with, wr=wr, avg=avg, pl=pl, pf=pf)


def main():
    d = json.load(open(SNAP))
    days = d["days"]
    print(f"frozen snapshot: {len(days)} 天 (生产口径 final_score)\n")
    print(f"{'分数线':>6} {'天数':>5} {'笔数':>6} {'胜率%':>7} {'均净%':>9} {'盈亏比':>7} {'PF':>7}")
    for sl in [60, 65, 68, 70, 72]:
        r = evaluate(days, sl, "")
        if r:
            print(f"{sl:>6} {r['days']:>5} {r['n']:>6} {r['wr']:>7.1f} {r['avg']:>+9.3f} {r['pl']:>7.2f} {r['pf']:>7.3f}")
    print("\n判读: frozen snapshot 分数被cap到72(历史回放数据不全),故只测<=72的线.")
    print("       生产LIVE数据全,有78+候选;本回测回答的是'降线放行的票是否正期望'.")


if __name__ == "__main__":
    main()
