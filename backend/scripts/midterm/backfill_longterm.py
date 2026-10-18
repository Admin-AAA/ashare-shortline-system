"""[中长线] Resumable backfill for long-term research (runs under launchctl ashare.ltbackfill).

Uses the project's Tushare facade (app.services.tushare_client.get_pro -> shared rate limiter / transport).
Stages (each resumable via data/midterm/backfill_state.json):
  daily_hist   : pro.daily by trade_date 2018-01-02..2021-05-25 -> data/midterm/daily_hist_<year>.parquet
  daily_hist2  : pro.daily 2021-05-26..2021-12-31 (the daily_kbar parquet cache only has ~5 codes/day until 2022-01-04)
  basic_full   : pro.daily_basic by trade_date 2018-01-02..today (pe/pb/dv_ttm/total_mv...) -> data/midterm/basic_<year>.parquet
  fina         : pro.fina_indicator per stock since 2017 -> MySQL stock_fina_indicator (upsert) + data/midterm/fina/<code>.parquet
  forecast     : pro.forecast per stock -> data/midterm/forecast/<code>.parquet
  dividend     : pro.dividend per stock -> data/midterm/dividend/<code>.parquet
  hk_hold      : pro.hk_hold weekly (Fridays) SH+SZ since 2018 -> MySQL hk_holdings (upsert)
  holdernum    : pro.stk_holdernumber per stock (full history, ann_date PIT) -> data/midterm/holdernum/<code>.parquet
  events       : pro.stk_holdertrade trade_type=IN (monthly ann_date windows, auto-split near the row cap) + pro.repurchase (monthly)
                 2017-07..today -> data/midterm/events/{holdertrade_in,repurchase}_<year>.parquet
"""
from __future__ import annotations
import json, logging, sys, time, datetime as dt
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from app.db.session import SessionLocal
from app.models.tables import FinaIndicator, HkHolding
from app.services.tushare_client import get_pro

D = ROOT / "data" / "midterm"; STATE = D / "backfill_state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ltbackfill")
for noisy in ("app", "httpx", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

class _State(dict):
    """per-stage checkpoint files so parallel launchctl jobs (different stages) never clobber each other."""
    def get(self, k, default=None):
        f = D / f"backfill_state_{k}.json"
        if f.exists(): return json.loads(f.read_text())
        if STATE.exists(): return json.loads(STATE.read_text()).get(k, default)  # legacy combined file
        return default
    def __setitem__(self, k, v):
        (D / f"backfill_state_{k}.json").write_text(json.dumps(v))
def state(): return _State()
def save(s): pass

def call(pro, name, tries=4, **kw):
    for i in range(tries):
        try:
            t0 = time.time(); out = getattr(pro, name)(**kw)
            log.info("call %s %s -> %s rows %.1fs", name, kw.get("trade_date") or kw.get("ts_code", ""), 0 if out is None else len(out), time.time() - t0)
            return out
        except Exception as e:
            log.warning("%s %s failed (%s) try %d", name, kw, str(e)[:80], i + 1); time.sleep(15 * (i + 1))
    return None

def trade_days(pro, start, end):
    cal = call(pro, "trade_cal", exchange="SSE", start_date=start, end_date=end, is_open="1")
    return sorted(cal["cal_date"].astype(str).tolist())

def week_ends(days):
    last = {}
    for d in days:
        y, w, _ = dt.date(int(d[:4]), int(d[4:6]), int(d[6:])).isocalendar(); last[(y, w)] = d
    return sorted(last.values())

def _to_parquet(df, f):
    """write parquet; on mixed-type object columns coerce to numeric when possible, else str/None."""
    try:
        df.to_parquet(f, index=False); return
    except Exception:
        pass
    df = df.copy()
    for col in df.columns[df.dtypes == object]:
        num = pd.to_numeric(df[col], errors="coerce")
        if num.notna().sum() == df[col].notna().sum():
            df[col] = num
        else:
            df[col] = df[col].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    df.to_parquet(f, index=False)

def by_date_stage(pro, s, key, api, start, end, prefix, weekly=False):
    done = set(s.get(key, []))
    days = trade_days(pro, start, end)
    if weekly: days = week_ends(days)
    days = [d for d in days if d not in done]
    buf: dict[str, list] = {}
    for n, d in enumerate(days):
        df = call(pro, api, trade_date=d)
        if df is None: continue
        buf.setdefault(d[:4], []).append(df); done.add(d)
        if n % 20 == 19 or n == len(days) - 1:
            for y, parts in buf.items():
                f = D / f"{prefix}_{y}.parquet"
                new = pd.concat(parts)
                if f.exists(): new = pd.concat([pd.read_parquet(f), new]).drop_duplicates(["ts_code", "trade_date"])
                _to_parquet(new, f)
            buf = {}; s[key] = sorted(done); save(s); log.info("%s %d/%d", key, n + 1, len(days))

def codes(db):
    rows = db.execute(text("SELECT DISTINCT code FROM daily_kbars WHERE trade_date >= '2021-06-01'")).scalars().all()
    hist = set()
    for f in D.glob("daily_hist_*.parquet"):
        hist |= set(pd.read_parquet(f, columns=["ts_code"])["ts_code"].str[:6])
    return sorted({c for c in set(rows) | hist if c and len(c) == 6 and c[0] in "036"})

def ts(c): return f"{c}.SH" if c.startswith("6") else f"{c}.SZ"

def f_(v):
    try:
        v = float(v); return None if v != v else v
    except Exception: return None

def per_code_stage(pro, db, s, key, api, outdir, to_mysql=False):
    for _ in range(3):
        if not _per_code_pass(pro, db, s, key, api, outdir, to_mysql): break

def _per_code_pass(pro, db, s, key, api, outdir, to_mysql):
    done = set(s.get(key, [])); (D / outdir).mkdir(exist_ok=True)
    todo = [c for c in codes(db) if c not in done]
    for n, c in enumerate(todo):
        kw = {"ts_code": ts(c)}
        if api == "fina_indicator": kw["start_date"] = "20170101"
        if api == "stk_holdernumber": time.sleep(1.0)  # promax per-minute quota for this api is low; stay under it
        df = call(pro, api, tries=6, **kw)
        if df is None:
            time.sleep(30); continue  # transient failure: leave undone for the next pass
        if not df.empty:
            _to_parquet(df, D / outdir / f"{c}.parquet")
            if to_mysql:
                rows = {}
                for _, r in df.iterrows():
                    ed = str(r.get("end_date") or "")
                    if len(ed) != 8: continue
                    rows[ed] = {"code": c, "end_date": ed, "ann_date": str(r.get("ann_date") or "") or None,
                                **{k: f_(r.get(k)) for k in ("roe", "grossprofit_margin", "netprofit_margin", "netprofit_yoy", "or_yoy", "eps", "bps")}}
                if rows:
                    stmt = mysql_insert(FinaIndicator).values(list(rows.values()))
                    stmt = stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in ("ann_date", "roe", "grossprofit_margin", "netprofit_margin", "netprofit_yoy", "or_yoy", "eps", "bps")})
                    db.execute(stmt); db.commit()
        done.add(c)
        if n % 25 == 24 or n == len(todo) - 1:
            s[key] = sorted(done); save(s); log.info("%s %d/%d", key, n + 1, len(todo))
    s[key] = sorted(done)
    return len([c for c in todo if c not in done])

def _range_fetch(pro, api, a, b, cap, **extra):
    """fetch [a,b] by ann_date range; bisect the window when the result is at/near the provider row cap."""
    df = call(pro, api, start_date=a, end_date=b, **extra)
    if df is None: return None
    if len(df) >= cap and a != b:
        da, db_ = dt.datetime.strptime(a, "%Y%m%d"), dt.datetime.strptime(b, "%Y%m%d")
        mid = da + (db_ - da) / 2
        m1, m2 = mid.strftime("%Y%m%d"), (mid + dt.timedelta(days=1)).strftime("%Y%m%d")
        l, r = _range_fetch(pro, api, a, m1, cap, **extra), _range_fetch(pro, api, m2, b, cap, **extra)
        if l is None or r is None: return None
        df = pd.concat([l, r])
    return df

def events_stage(pro, s, start="20170701"):
    out = D / "events"; out.mkdir(exist_ok=True)
    today = dt.date.today()
    # holdertrade: increases only (trade_type=IN) -- far fewer rows than DE, so monthly windows fit under the row cap
    for api, name, step, cap, extra in (("repurchase", "repurchase", 31, 1900, {}), ("stk_holdertrade", "holdertrade_in", 31, 2900, {"trade_type": "IN"})):
        key = f"events_{name}"; done = set(s.get(key, []))
        a = dt.datetime.strptime(start, "%Y%m%d").date(); wins = []
        while a <= today:
            b = min(a + dt.timedelta(days=step - 1), today); wins.append((a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))); a = b + dt.timedelta(days=1)
        todo = [w for w in wins if f"{w[0]}-{w[1]}" not in done]
        buf: dict[str, list] = {}
        for n, (wa, wb) in enumerate(todo):
            df = _range_fetch(pro, api, wa, wb, cap, **extra)
            if df is not None:
                if len(df): buf.setdefault(wa[:4], []).append(df)
                done.add(f"{wa}-{wb}")
            if n % 10 == 9 or n == len(todo) - 1:
                for y, parts in buf.items():
                    f = out / f"{name}_{y}.parquet"; new = pd.concat(parts)
                    if f.exists(): new = pd.concat([pd.read_parquet(f), new])
                    _to_parquet(new.drop_duplicates(), f)
                buf = {}; s[key] = sorted(done); log.info("%s %d/%d", key, n + 1, len(todo))
            time.sleep(0.6)

def hk_stage(pro, db, s):
    done = set(s.get("hk_hold", []))
    days = trade_days(pro, "20180101", dt.date.today().strftime("%Y%m%d"))
    fridays = {}
    for d in days:  # last trading day of each ISO week
        y, w, _ = dt.date(int(d[:4]), int(d[4:6]), int(d[6:])).isocalendar(); fridays[(y, w)] = d
    todo = [d for d in sorted(fridays.values()) if d not in done]
    for n, d in enumerate(todo):
        rows = []
        for ex in ("SH", "SZ"):
            df = call(pro, "hk_hold", trade_date=d, exchange=ex)
            if df is None: continue
            for _, r in df.iterrows():
                c = str(r.get("ts_code", ""))[:6]
                if len(c) == 6:
                    rows.append({"code": c, "trade_date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "vol": f_(r.get("vol")), "ratio": f_(r.get("ratio"))})
        if rows:
            for i in range(0, len(rows), 2000):
                stmt = mysql_insert(HkHolding).values(rows[i:i + 2000])
                db.execute(stmt.on_duplicate_key_update(vol=stmt.inserted.vol, ratio=stmt.inserted.ratio)); db.commit()
        done.add(d)
        if n % 10 == 9 or n == len(todo) - 1:
            s["hk_hold"] = sorted(done); save(s); log.info("hk_hold %d/%d", n + 1, len(todo))

def main():
    D.mkdir(parents=True, exist_ok=True)
    pro = get_pro(); db = SessionLocal(); s = state()
    stages = sys.argv[1:] or ["daily_hist", "basic_full", "forecast", "dividend", "hk_hold", "fina"]
    today = dt.date.today().strftime("%Y%m%d")
    for st in stages:
        log.info("== stage %s", st)
        if st == "daily_hist": by_date_stage(pro, s, st, "daily", "20180102", "20210525", "daily_hist")
        elif st == "daily_hist2": by_date_stage(pro, s, st, "daily", "20210526", "20211231", "daily_hist")
        elif st == "basic_full": by_date_stage(pro, s, st, "daily_basic", "20180102", today, "basic", weekly=True)
        elif st == "fina": per_code_stage(pro, db, s, st, "fina_indicator", "fina", to_mysql=True)
        elif st == "forecast": per_code_stage(pro, db, s, st, "forecast", "forecast")
        elif st == "dividend": per_code_stage(pro, db, s, st, "dividend", "dividend")
        elif st == "hk_hold": hk_stage(pro, db, s)
        elif st == "holdernum": per_code_stage(pro, db, s, st, "stk_holdernumber", "holdernum")
        elif st == "events": events_stage(pro, s)
    log.info("ALL DONE")

if __name__ == "__main__":
    main()
