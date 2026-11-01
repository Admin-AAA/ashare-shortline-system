"""§109 股权激励草案公告缓存：巨潮资讯公开检索（只读），category_gqjl_szsh + searchkey「草案」，全市场按月翻页。
输出 output/research/sprint4/incentive/YYYYMM.parquet（列 code, name, ann_ms, ann_date(YYYYMMDD CST), title, ann_id）。
单线程、每请求 >= 1.0 秒，已完成月份跳过（当月/最后一个月总是重拉）。不写库。
用法：python scripts/midterm/fetch_incentive_cache.py 2018-01 2026-10 [--end-day 2026-10-16]"""
import sys, time, re, calendar, datetime as dt
from pathlib import Path
import httpx
import pandas as pd

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"  # same endpoint as app/services/cninfo_news.py
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
           "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"}
OUT = Path(__file__).resolve().parents[2] / "output/research/sprint4/incentive"
TZ = dt.timezone(dt.timedelta(hours=8))
_last = [0.0]


def post(cl, data, tries=5):
    for k in range(tries):
        w = 1.0 - (time.monotonic() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.monotonic()
        try:
            r = cl.post(QUERY_URL, data=data); r.raise_for_status(); return r.json() or {}
        except Exception as e:
            print("  retry", k, type(e).__name__, str(e)[:120], flush=True); time.sleep(5 * (k + 1))
    raise RuntimeError("cninfo failed")


def fetch(cl, se):
    rows, page, total = [], 1, None
    while True:
        d = post(cl, {"pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext", "plate": "", "stock": "",
                      "searchkey": "草案", "secid": "", "category": "category_gqjl_szsh", "trade": "", "seDate": se,
                      "sortName": "", "sortType": "", "isHLtitle": "true"})
        total = d.get("totalAnnouncement") if total is None else total
        a = d.get("announcements") or []
        for x in a:
            ms = int(x.get("announcementTime") or 0)
            rows.append({"code": str(x.get("secCode") or ""), "name": str(x.get("secName") or ""), "ann_ms": ms,
                         "ann_date": dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y%m%d"),
                         "title": re.sub(r"</?em>", "", str(x.get("announcementTitle") or "")),
                         "ann_id": str(x.get("announcementId") or "")})
        if not a or not d.get("hasMore"): break
        page += 1
    return rows, total


if __name__ == "__main__":
    s, e = sys.argv[1], sys.argv[2]
    end_day = sys.argv[sys.argv.index("--end-day") + 1] if "--end-day" in sys.argv else None
    OUT.mkdir(parents=True, exist_ok=True)
    y, m = map(int, s.split("-")); ey, em = map(int, e.split("-"))
    cl = httpx.Client(headers=HEADERS, timeout=20)
    while (y, m) <= (ey, em):
        f = OUT / f"{y}{m:02d}.parquet"
        last = (y, m) == (ey, em)
        if f.exists() and not last:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1); continue
        hi = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
        if last and end_day: hi = end_day
        rows, total = fetch(cl, f"{y}-{m:02d}-01~{hi}")
        df = pd.DataFrame(rows, columns=["code", "name", "ann_ms", "ann_date", "title", "ann_id"]).drop_duplicates("ann_id")
        df.to_parquet(f, index=False)
        print(f"{y}-{m:02d} total={total} got={len(df)}", flush=True)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    print("done", flush=True)
