"""小石全市场 1 分钟线（cn-minute）归档 + 每日增量。

背景：小石现行契约只保留滚动 3 个月（earliest_retained_date 随日期后移），逐日不下载就会丢；本脚本把全市场分钟线
落到本地并每天增量保存。**不触碰** backend/data/xiaoshi_min1（旧的 230 只，另一套结构）。

目录（backend/data/xiaoshi_min_all/，已被 .gitignore 的 backend/data/ 覆盖）：
  raw/symbol/<6位代码>.parquet            单股底座（目录里 partition=market=CN/symbol=XXXXXX，约 2026-07-13~08-19，几十~几百 KB）
  raw/bucket/<YYYY-MM>/bucket=<0-f>.parquet 月度代码桶增量（一个桶含约 1/16 的股票，当月文件每天变大）
  raw/bucket/<YYYY-MM>/bucket=<x>.v<sha8>.parquet  旧版本：仅当新版本的 first_date 比旧版晚（滚动保留把最老几天裁掉）时才留存，避免丢天
  state.json                              {key: {sha256,size,first_date,last_date,path}}、bucket_rep（桶→代表股票）、最近一次运行摘要
  sync.log                                每次运行一行 JSON 摘要

列名/单位（原样保存小石 canonical，不改写原文件）：
  market, symbol(6位), exchange(SH/SZ/BJ), trade_date, trade_time(上海时间，分钟线标签=该分钟结束), open/high/low/close(不复权元),
  volume(**股**), amount(**元**), trading_status, is_suspended, adj_factor(新契约为空), available_at, revision_time ...
读取时用 load_minutes() 统一成旧 xiaoshi_min1 的列：ts_code(如 000001.SZ)、trade_date、trade_time、open/high/low/close、vol(股)、amount(元)；
旧 xiaoshi_min1 底库的 vol 也是股、amount 也是元，两边单位一致；差别：旧底库每天固定 241 根(含 09:30、含零成交补位分钟)，
旧 update 层与新契约每天约 238~240 根(无零成交分钟，2026-07-20 起没有 09:30 根，集合竞价量并入 09:31 根)。同一(symbol,trade_time)取 revision_time 较新者。

限速/保护：对小石 API ≤ 6 次/秒（实盘行情也用小石，不挤占）；R2 下载 4 线程；启动前和每 200 次请求看一次 live 小石熔断(live:gw:xiaoshi*)，
开着就等（日K退役端点的熔断除外）；单次失败只记日志；整个脚本任何异常都 exit 0（不阻塞选股），用 nice 低优先级跑。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/sync_xiaoshi_min_all.py [--max-sessions N] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

B = Path(__file__).resolve().parents[1]
ROOT = B / "data" / "xiaoshi_min_all"
RAW = ROOT / "raw"
STATE = ROOT / "state.json"
LOG = ROOT / "sync.log"
DATASET = "cn-minute"
MAX_QPS = 6.0
DL_THREADS = 4
MAX_CONSEC_429 = 4
UNIFIED_COLS = ["ts_code", "trade_date", "trade_time", "open", "high", "low", "close", "vol", "amount"]


# ───────────────────────── 纯函数（有单测） ─────────────────────────
def key_to_relpath(partition: str) -> str:
    """catalog partition → raw 下的相对路径。"""
    parts = dict(p.split("=", 1) for p in partition.split("/") if "=" in p)
    if "symbol" in parts:
        return f"symbol/{parts['symbol']}.parquet"
    return f"bucket/{parts['month']}/bucket={parts['bucket']}.parquet"


def plan_downloads(catalog: list[dict], state_files: dict) -> list[dict]:
    """catalog 条目里 sha256 与本地记录不同（或没有）的需要下载。"""
    todo = []
    for it in catalog:
        rec = state_files.get(it["key"])
        if rec is None or rec.get("sha256") != it.get("sha256"):
            todo.append(it)
    return todo


def should_archive_old(old: dict | None, new_first_date: str | None) -> bool:
    """新版本 first_date 比旧版晚 = 滚动保留裁掉了老的几天，旧版要留存；否则可直接覆盖。"""
    if not old or not old.get("first_date") or not new_first_date:
        return False
    return str(new_first_date) > str(old["first_date"])


def unify(df):
    """小石 canonical → 旧 xiaoshi_min1 风格列（polars DataFrame）。"""
    import polars as pl

    c = pl.col
    suffix = pl.when(c("exchange").is_not_null()).then(c("exchange")).otherwise(
        pl.when(c("symbol").str.starts_with("6") | c("symbol").str.starts_with("9")).then(pl.lit("SH")).otherwise(pl.lit("SZ")))
    out = df.with_columns(
        (c("symbol").cast(pl.Utf8) + "." + suffix.cast(pl.Utf8)).alias("ts_code"),
        c("trade_time").cast(pl.Datetime("ns")).alias("trade_time"),
        c("volume").cast(pl.Float64).alias("vol"),
    ).with_columns(c("trade_time").dt.truncate("1d").alias("trade_date"))
    return out.select(UNIFIED_COLS)


def load_minutes(codes: list[str], start: str | None = None, end: str | None = None):
    """读取归档分钟线（polars，统一列）。codes 为 6 位代码；start/end 为 YYYY-MM-DD（含）。"""
    import polars as pl

    c = pl.col
    codes = [str(x)[:6] for x in codes]
    files: list[Path] = []
    for code in codes:
        p = RAW / "symbol" / f"{code}.parquet"
        if p.exists():
            files.append(p)
    files += sorted((RAW / "bucket").glob("*/bucket=*.parquet"))
    frames = []
    for f in files:
        lf = pl.scan_parquet(f).filter(c("symbol").cast(pl.Utf8).is_in(codes))
        frames.append(lf.collect())
    if not frames:
        return pl.DataFrame(schema={k: pl.Float64 for k in UNIFIED_COLS})
    df = pl.concat([x for x in frames if x.height], how="diagonal_relaxed") if any(x.height for x in frames) else frames[0]
    if "revision_time" not in df.columns:
        df = df.with_columns(pl.lit(None).alias("revision_time"))
    df = df.sort("revision_time", nulls_last=False).unique(["symbol", "trade_time"], keep="last", maintain_order=False)
    u = unify(df).sort("ts_code", "trade_time")
    if start:
        u = u.filter(c("trade_date") >= pl.lit(start).str.to_date().cast(pl.Datetime("ns")))
    if end:
        u = u.filter(c("trade_date") <= pl.lit(end).str.to_date().cast(pl.Datetime("ns")))
    return u


# ───────────────────────── 运行 ─────────────────────────
class _Pace:
    def __init__(self, qps: float) -> None:
        self.gap, self.last, self.lock = 1.0 / qps, 0.0, threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            sleep = self.last + self.gap - now
            if sleep > 0:
                time.sleep(sleep)
            self.last = time.monotonic()


def _xiaoshi_live_breaker_wait() -> float:
    """live 进程里小石**行情类**熔断（key 含 quote/realtime/rt_）开着的剩余秒数（最大者）。
    注意：live:gw:xiaoshi_kline:bar.daily 是已退役的小石日K端点（契约 stability.legacy_aliases=false），其 probe_failed 熔断每 5 分钟重开一次，与本脚本无关，不看。"""
    try:
        from app.core import shared_breaker as sb

        conn = sb._conn()
        if conn is None:
            return 0.0
        rows = conn.execute("SELECT key, open_until FROM breaker_state WHERE key LIKE 'live:%xiaoshi%' AND (key LIKE '%quote%' OR key LIKE '%realtime%' OR key LIKE '%rt_%')").fetchall()
        conn.close()
        now = time.time()
        return max([u - now for _, u in rows] + [0.0])
    except Exception:  # noqa: BLE001
        return 0.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"files": {}, "bucket_rep": {}, "last_run": None}


def _save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    os.replace(tmp, STATE)


def _adopt_existing(catalog: list[dict], files: dict) -> int:
    """上次被中断时已落盘但没来得及记进 state 的文件：大小与 sha256 都对得上就认领，不重复消耗下载会话。"""
    n = 0
    for it in catalog:
        key = it["key"]
        if key in files:
            continue
        dest = RAW / key_to_relpath(it["partition"])
        try:
            if dest.exists() and dest.stat().st_size == int(it.get("size") or -1) and _sha256(dest) == it.get("sha256"):
                files[key] = {"sha256": it.get("sha256"), "size": it.get("size"), "first_date": it.get("first_date"),
                              "last_date": it.get("last_date"), "path": key_to_relpath(it["partition"])}
                n += 1
        except OSError:
            continue
    return n


def _fetch_catalog(client, pace: _Pace) -> list[dict]:
    items, off = [], 0
    while True:
        pace.wait()
        r = client.get_history_catalog(dataset=DATASET, limit=200, offset=off)
        items += [i for i in r.get("items", []) if i.get("dataset") == DATASET]
        off += 200
        if off >= int(r.get("total") or 0):
            break
    return items


def _download(url: str, dest: Path, size: int, sha: str, http) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    os.close(fd)
    try:
        with http.stream("GET", url, timeout=120, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
        p = Path(tmp)
        if size and p.stat().st_size != size:
            raise RuntimeError(f"size mismatch {p.stat().st_size}!={size}")
        if sha and _sha256(p) != sha:
            raise RuntimeError("sha256 mismatch")
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)


def run(max_sessions: int | None = None, dry_run: bool = False) -> dict:
    import httpx
    from app.services.xiaoshi_client import get_xiaoshi_client

    t0 = time.time()
    ROOT.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    files = state["files"]
    pace = _Pace(MAX_QPS)
    client = get_xiaoshi_client()
    wait = _xiaoshi_live_breaker_wait()
    if wait > 0:
        time.sleep(min(wait + 5, 600))
    catalog = _fetch_catalog(client, pace)
    adopted = _adopt_existing(catalog, files)
    if adopted and not dry_run:
        _save_state(state)
    todo = plan_downloads(catalog, files)
    summary: dict[str, Any] = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "catalog_files": len(catalog), "to_download": len(todo),
                               "downloaded": 0, "bytes": 0, "errors": 0, "sessions": 0, "adopted": adopted}
    if dry_run:
        summary["dry_run"] = True
        return summary
    by_key = {i["key"]: i for i in todo}
    sym_todo = [i for i in todo if "symbol=" in i["partition"]]
    bucket_todo = {i["key"]: i for i in todo if "bucket=" in i["partition"]}
    # 需要会话的代码：缺单股底座的；以及负责取 URL 的桶代表股票
    codes: list[str] = [str(i.get("symbol") or i.get("code")) for i in sym_todo]
    reps = state["bucket_rep"]  # "YYYY-MM/bucket" → code
    need_rep = {k: i for k, i in bucket_todo.items()}
    for k, i in need_rep.items():
        rel = key_to_relpath(i["partition"])
        rep = reps.get(rel)
        if rep and rep not in codes:
            codes.append(rep)
    if max_sessions:
        codes = codes[:max_sessions]
    http = httpx.Client(follow_redirects=True)
    got_urls: dict[str, tuple[str, dict]] = {}
    pool = ThreadPoolExecutor(max_workers=DL_THREADS)
    futs = {}
    lock = threading.Lock()

    def submit_download(f: dict) -> None:
        key = f["key"]
        if key not in by_key or key in futs or not f.get("url"):
            return
        it = by_key[key]
        rel = key_to_relpath(it["partition"])
        dest = RAW / rel
        old = files.get(key)
        # 同路径旧版本（桶文件每天换 key/sha）：满足条件则另存为旧版本
        for ok, orec in list(files.items()):
            if orec.get("path") == rel and ok != key and should_archive_old(orec, it.get("first_date")):
                src = RAW / rel
                if src.exists():
                    arch = src.with_name(src.stem + f".v{orec['sha256'][:8]}.parquet")
                    if not arch.exists():
                        os.replace(src, arch)
                    orec["path"] = str(arch.relative_to(RAW))
        futs[key] = (pool.submit(_download, f["url"], dest, int(it.get("size") or 0), it.get("sha256") or "", http), it, rel)

    consec_429 = 0
    for n, code in enumerate(codes):
        if consec_429 >= MAX_CONSEC_429:
            summary["rate_limited"] = True  # 小石对 download-session 限流：停手，下次运行（或补跑循环）接着来
            break
        if n % 200 == 0 and n:
            w = _xiaoshi_live_breaker_wait()
            if w > 0:
                time.sleep(min(w + 5, 600))
        try:
            pace.wait()
            r = client.get_history_download_session(dataset=DATASET, code=code)
            summary["sessions"] += 1
            consec_429 = 0
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            consec_429 = consec_429 + 1 if "429" in str(exc) else 0
            _log_err(f"session {code}: {str(exc)[:120]}")
            continue
        for f in r.get("files") or []:
            part = f.get("partition") or ""
            if "bucket=" in part:
                reps.setdefault(key_to_relpath(part), code)
            submit_download(f)
        # 回收已完成的
        _collect(futs, files, summary, lock)
        if n % 10 == 0:
            _save_state(state)
    _collect(futs, files, summary, lock, wait_all=True)
    pool.shutdown(wait=True)
    http.close()
    state["last_run"] = summary
    _save_state(state)
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


def _collect(futs: dict, files: dict, summary: dict, lock, wait_all: bool = False) -> None:
    for key, (fut, it, rel) in list(futs.items()):
        if not (fut.done() or wait_all):
            continue
        try:
            fut.result()
            files[key] = {"sha256": it.get("sha256"), "size": it.get("size"), "first_date": it.get("first_date"),
                          "last_date": it.get("last_date"), "path": rel}
            summary["downloaded"] += 1
            summary["bytes"] += int(it.get("size") or 0)
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            _log_err(f"download {rel}: {str(exc)[:120]}")
        del futs[key]


def _log_err(msg: str) -> None:
    with open(ROOT / "errors.log", "a") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sessions", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        sys.path.insert(0, str(B))
        s = run(a.max_sessions, a.dry_run)
    except Exception as exc:  # noqa: BLE001 - 不阻塞选股：任何异常都只记日志
        s = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "fatal": str(exc)[:300]}
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
