"""[中线研究 §1065+ 数据] 经统一网关（get_pro，research 命名空间）拉取研究用历史：
  broker  : broker_recommend 券商月度金股，按月一次调用（2020-01 起）。
  report_rc: 卖方研报盈利预测，按 span_days 个自然日一段调用（默认 5；网关单页 10000 行自动翻页，可用 30 天一段）；
             span_days≤5 时若一段返回 ≥2900 行再逐日重拉作保险。
只写研究缓存 backend/data/research_cache/{broker_recommend,report_rc}/（gitignore），不写数据库、不动现有表。
限速：盘中（工作日 09:15–15:05）30 次/分钟，盘后 60 次/分钟；可断点续拉（已存在的段跳过）。
用法：cd backend && PYTHONPATH=. ../.venv/bin/python scripts/midterm/fetch_mid1126.py broker|report_rc [start=20190101] [end=20261125]"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2] / "data/research_cache"


def _pace() -> None:
    now = datetime.now(TZ)
    intraday = now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 5)
    time.sleep(60.0 / (30 if intraday else 60))


def _call(pro, api: str, **kw) -> pd.DataFrame:
    for attempt in range(4):
        try:
            return pro.query(api, **kw)
        except Exception as e:  # noqa: BLE001
            print(f"  retry {api} {kw} attempt={attempt} err={str(e)[:120]}", flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"{api} {kw} failed 4 times")


def fetch_broker(pro, start: str, end: str) -> None:
    out = ROOT / "broker_recommend"
    out.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(start[:6], end[:6], freq="M").strftime("%Y%m")
    for m in months:
        f = out / f"{m}.parquet"
        if f.exists():
            continue
        df = _call(pro, "broker_recommend", month=m)
        df.to_parquet(f, index=False)
        print(f"broker {m} rows={len(df)}", flush=True)
        _pace()


def fetch_rc(pro, start: str, end: str, span_days: int = 5) -> None:
    """span_days 天一段。网关客户端对 report_rc 单页 10000 行自动翻页取全（2026-11-26 实测两段与逐日合计逐行数一致），
    逐日拆分只作保险。"""
    out = ROOT / "report_rc"
    out.mkdir(parents=True, exist_ok=True)
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    while d0 <= d1:
        e = min(d0 + timedelta(days=span_days - 1), d1)
        s_, e_ = d0.strftime("%Y%m%d"), e.strftime("%Y%m%d")
        f = out / f"{s_}_{e_}.parquet"
        if not f.exists():
            df = _call(pro, "report_rc", start_date=s_, end_date=e_)
            _pace()
            if span_days <= 5 and len(df) >= 2900:
                parts = []
                for k in range((e - d0).days + 1):
                    day = (d0 + timedelta(days=k)).strftime("%Y%m%d")
                    p = _call(pro, "report_rc", report_date=day)
                    _pace()
                    if len(p) >= 2900:
                        print(f"  WARN day {day} rows={len(p)} may be truncated", flush=True)
                    parts.append(p)
                df = pd.concat(parts, ignore_index=True) if parts else df
            df.to_parquet(f, index=False)
            print(f"rc {s_}-{e_} rows={len(df)}", flush=True)
        d0 = e + timedelta(days=1)


def main() -> None:
    from app.services.tushare_client import get_pro

    what = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else "20190101"
    end = sys.argv[3] if len(sys.argv) > 3 else "20261125"
    pro = get_pro()
    if what == "broker":
        fetch_broker(pro, start, end)
    elif what == "report_rc":
        fetch_rc(pro, start, end, int(sys.argv[4]) if len(sys.argv) > 4 else 5)
    else:
        raise SystemExit(f"unknown {what}")
    print("DONE", what, flush=True)


if __name__ == "__main__":
    main()
