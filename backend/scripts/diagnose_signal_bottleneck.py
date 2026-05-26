#!/usr/bin/env python3
"""两阶段诊断：1)从日K筛选候选 → 2)按需加载候选的分钟数据。"""
from __future__ import annotations

import json, logging, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar, Stock
from sqlalchemy import and_, or_, tuple_

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MIN_PCT, MAX_PCT = 2.0, 7.0
MIN_ABOVE_VWAP = 0.86
MIN_VOL_RATIO = 1.0
RETEST_WINDOW = 8
VWAP_MAX_GAP = 170.0
VWAP_TOLERANCE = 12.0

FC = defaultdict(int)
TOTAL = 0

def _n(v, d=0.0):
    return float(v) if v is not None else d

def _is_lu(bar):
    return _n(bar.pct_chg) >= 9.5

def _pc(before, after):
    return ((after-before)/before)*100 if before>0 else 0

def _pg(a, b):
    return ((a/b)-1)*10000 if b>0 else 0

# ======== PASS 1: 候选筛选 ========
def find_candidates(db, start, end):
    """扫描日K，返回候选列表 + 元数据。"""
    logger.info("PASS1: 日K扫描 %s~%s", start, end)
    rows = db.query(DailyKbar).filter(
        DailyKbar.trade_date >= start, DailyKbar.trade_date <= end
    ).order_by(DailyKbar.trade_date.asc()).all()

    by_date = defaultdict(dict)
    hist = defaultdict(list)
    for r in rows:
        by_date[str(r.trade_date)][r.code] = r
        hist[r.code].append(r)

    stocks = {s.code: s for s in db.query(Stock).all()}
    candidates = []

    for td in sorted(by_date):
        for code, bar in by_date[td].items():
            s = stocks.get(code)
            if not s:
                continue
            if not (code.startswith("60") or code.startswith("00")):
                continue
            if s.is_st or (s.name and "ST" in s.name.upper()):
                continue
            pct = _n(bar.pct_chg)
            if pct < MIN_PCT or pct > MAX_PCT:
                continue

            h = hist.get(code, [])
            hi = next((i for i, r in enumerate(h) if str(r.trade_date)==td), -1)
            if hi <= 0:
                continue
            lu = sum(1 for r in h[max(0,hi-20):hi] if _is_lu(r))
            if lu <= 0:
                continue

            vols = [_n(r.volume) for r in h[max(0,hi-5):hi] if _n(r.volume)>0]
            avg5 = sum(vols)/len(vols) if vols else 0
            if avg5 <= 0:
                continue

            prev = _n(h[hi-1].close)
            if prev <= 0:
                continue

            candidates.append({
                "code": code, "date": td,
                "name": s.name or code,
                "prev_close": prev,
                "avg_prev5_volume": avg5,
                "daily_pct": pct,
            })

    logger.info("候选: %d", len(candidates))
    return candidates


# ======== PASS 2: 信号检查 ========
def load_candidate_minutes(db, candidates):
    """只加载候选的 (code, date) 的 15min 数据。"""
    pairs = list({(c["code"], c["date"]) for c in candidates})
    logger.info("PASS2: 加载 %d 对候选的 15min 数据", len(pairs))

    # 批量查询
    conditions = []
    for code, td in pairs:
        conditions.append(
            and_(
                MinuteKbar.code == code,
                MinuteKbar.freq == "15min",
                MinuteKbar.trade_time >= f"{td} 09:00",
                MinuteKbar.trade_time <= f"{td} 15:30",
            )
        )

    idx = defaultdict(list)
    batch_size = 500
    for i in range(0, len(conditions), batch_size):
        batch = conditions[i:i+batch_size]
        rows = db.query(MinuteKbar).filter(or_(*batch)).order_by(MinuteKbar.trade_time).all()
        for r in rows:
            td = str(r.trade_time)[:10]
            idx[(r.code, td)].append({
                "time": str(r.trade_time)[11:16],
                "close": _n(r.close),
                "high": _n(r.high),
                "low": _n(r.low),
                "open": _n(r.open),
                "volume": _n(r.volume),
                "amount": _n(r.amount),
            })
        logger.info("  ... batch %d/%d: %d bars loaded", i//batch_size+1, (len(conditions)+batch_size-1)//batch_size, len(rows))

    return idx


def load_benchmark(db, start, end):
    """000300 15min 数据。"""
    rows = db.query(MinuteKbar).filter(
        MinuteKbar.code == "000300",
        MinuteKbar.freq == "15min",
        MinuteKbar.trade_time >= f"{start} 09:00",
        MinuteKbar.trade_time <= f"{end} 15:30",
    ).order_by(MinuteKbar.trade_time).all()

    bm = defaultdict(list)
    for r in rows:
        td = str(r.trade_time)[:10]
        bm[td].append({
            "time": str(r.trade_time)[11:16],
            "close": _n(r.close),
            "high": _n(r.high),
            "low": _n(r.low),
            "open": _n(r.open),
            "volume": _n(r.volume),
            "amount": _n(r.amount),
        })
    for bars in bm.values():
        prev = None
        for b in bars:
            b["pre_close"] = prev if prev is not None else b["close"]
            prev = b["close"]
    logger.info("benchmark: %d dates", len(bm))
    return bm


def check(minutes, bm_minutes, cand):
    global FC
    if not minutes:
        FC["no_minutes"] += 1
        return False, ["no_minutes"], None
    if not bm_minutes:
        FC["no_benchmark"] += 1
        return False, ["no_benchmark"], None

    times = [m["time"] for m in minutes]
    try:
        ts = next(i for i, v in enumerate(times) if v >= "14:30")
    except StopIteration:
        FC["no_tail"] += 1
        return False, ["no_tail"], None

    bmm = {m["time"]: m for m in bm_minutes}
    cv, ca = 0.0, 0.0
    ac, dh = 0, 0.0
    lh = None
    all_pct, all_vr, all_rs, all_ar = [], [], [], []
    all_ho, all_ro, all_ca = [], [], []

    for i, m in enumerate(minutes):
        c, h, l, v, a = m["close"], m["high"], m["low"], m["volume"], m["amount"]
        if h >= dh:
            dh = h
            lh = i
        cv += v
        ca += a
        vwap = ca/(cv*100) if cv>0 and ca>0 else c
        if i >= ts and c >= vwap*(1-VWAP_TOLERANCE/10000):
            ac += 1
        if i < ts or lh is None:
            continue
        if i - lh > RETEST_WINDOW:
            continue

        prog = (i+1)/max(1,len(minutes))
        fde = cv/max(prog,0.05)
        vr = fde/max(cand["avg_prev5_volume"],1)
        pct = ((c-cand["prev_close"])/cand["prev_close"])*100 if cand["prev_close"]>0 else 0
        bm = bmm.get(times[i])
        br = _pc(bm["pre_close"],bm["close"]) if bm else 0
        rs = pct - br
        ar = ac/max(1,i-ts+1)
        ho = l >= vwap*(1-VWAP_TOLERANCE/10000)
        vg = _pg(l, vwap)
        ro = vg <= VWAP_MAX_GAP
        ca_ok = c >= vwap

        all_pct.append(pct)
        all_vr.append(vr)
        all_rs.append(rs)
        all_ar.append(ar)
        all_ho.append(ho)
        all_ro.append(ro)
        all_ca.append(ca_ok)

        if pct < MIN_PCT or pct > MAX_PCT:
            continue
        if vr < MIN_VOL_RATIO:
            continue
        if rs < 0:
            continue
        if ar < MIN_ABOVE_VWAP:
            continue
        if not ho or not ro or not ca_ok:
            continue

        FC["PASSED"] += 1
        return True, [], None

    # 归因
    bp = max(all_pct) if all_pct else 0
    bv = max(all_vr) if all_vr else 0
    br_val = max(all_rs) if all_rs else 0
    ba = max(all_ar) if all_ar else 0
    bho = any(all_ho)
    bro = any(all_ro)
    bca = any(all_ca)

    reasons = []
    if bp < MIN_PCT:
        FC["pct<3%"] += 1
        reasons.append("pct<3%")
    elif bp > MAX_PCT:
        FC["pct>5%"] += 1
        reasons.append("pct>5%")
    if bv < MIN_VOL_RATIO:
        FC["vol<1x"] += 1
        reasons.append("量比<1")
    if br_val < 0:
        FC["弱于大盘"] += 1
        reasons.append("弱于大盘")
    if ba < MIN_ABOVE_VWAP:
        FC["above_vwap"] += 1
        reasons.append(f"vwap上方<{int(MIN_ABOVE_VWAP*100)}%")
    if not bho:
        FC["hold_fail"] += 1
        reasons.append("hold_fail")
    if not bro:
        FC["retest_fail"] += 1
        reasons.append("retest_fail")
    if not bca:
        FC["close<vwap"] += 1
        reasons.append("收盘<vwap")
    if not reasons:
        FC["other"] += 1
        reasons.append("other")

    return False, reasons, {
        "pct": round(bp,2), "vol_ratio": round(bv,2), "rs": round(br_val,2),
        "above_vwap": round(ba,2), "hold": bho, "retest": bro, "close>vwap": bca,
    }


def main():
    global TOTAL, FC
    db = SessionLocal()
    start, end = "2026-02-01", "2026-05-25"

    candidates = find_candidates(db, start, end)
    bm = load_benchmark(db, start, end)
    minute_idx = load_candidate_minutes(db, candidates)
    db.close()

    TOTAL = len(candidates)
    detail = []
    for cand in candidates:
        minutes = minute_idx.get((cand["code"], cand["date"]), [])
        bm_mins = bm.get(cand["date"], [])
        passed, reasons, best = check(minutes, bm_mins, cand)
        detail.append({
            "code": cand["code"], "name": cand["name"], "date": cand["date"],
            "passed": passed, "reasons": reasons, "best": best,
        })
        if TOTAL % 200 == 0:
            logger.info("  进度: %d/%d (通过 %d)", len(detail), TOTAL, FC.get("PASSED",0))

    # === 输出 ===
    passed_n = FC.get("PASSED", 0)
    print(f"\n{'='*60}")
    print(f"  信号瓶颈诊断  {start} ~ {end}")
    print(f"{'='*60}")
    print(f"  候选: {TOTAL}  |  通过: {passed_n} ({passed_n/max(TOTAL,1)*100:.1f}%)")
    print()

    sf = sorted([(k,v) for k,v in FC.items() if k!="PASSED"], key=lambda x: -x[1])
    print(f"  {'瓶颈':<20} {'数量':>8} {'占比':>8}")
    print(f"  {'-'*36}")
    for k, v in sf:
        r = v/max(TOTAL,1)*100
        b = "█"*min(int(r/2), 50)
        print(f"  {k:<20} {v:>8} {r:>7.1f}% {b}")

    # 每人失败数
    pc = defaultdict(int)
    for d in detail:
        pc[0 if d["passed"] else len(d["reasons"])] += 1
    print(f"\n  每人失败条件数:")
    for n in sorted(pc):
        l = "✅ 通过" if n==0 else f"{n}个失败"
        print(f"    {l:<10} {pc[n]:>5} {'█'*min(pc[n],50)}")

    # 失败案例
    print(f"\n  典型失败案例:")
    n=0
    for d in detail:
        if not d["passed"] and n<8:
            print(f"    {d['date']} {d['code']} {d['name']}: {', '.join(d['reasons'][:3])}")
            if d.get("best"):
                b=d["best"]
                print(f"      pct={b.get('pct')} vr={b.get('vol_ratio')} rs={b.get('rs')} "
                      f"above={b.get('above_vwap')} hold={b.get('hold')} ret={b.get('retest')} ca={b.get('close>vwap')}")
            n+=1

    # 保存
    out = Path(__file__).resolve().parent.parent / "data" / "signal_diagnosis.json"
    out.parent.mkdir(exist_ok=True)
    with open(out,"w") as f:
        json.dump({"total":TOTAL,"passed":passed_n,"fails":dict(sf),"dist":dict(pc),"details":detail[:50]}, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
