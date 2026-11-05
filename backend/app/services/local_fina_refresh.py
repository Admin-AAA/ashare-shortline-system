"""本地逐股财务指标库 data/midterm/fina/<code>.parquet 的增量刷新（2026-11-05）。

为什么：这批文件是 10-16~17 用 scripts/midterm/backfill_longterm.py fina 阶段（逐股 fina_indicator，launchctl 一次性
补数，断点文件 backfill_state_fina.json 记「已完成」）一次性建的，之后没有任何定时任务更新它，三季报只有 47 只。
读它的现役代码：
- rolling_r12.local_first：重放日 as_of ≤ 本地覆盖日才读本地，否则走网关；
- midterm_sueg（SUE 家族）：SUE 历史全靠它，缺期就算不出 SUE、静默少票；
- datasource/fina_fallback：fina_indicator_vip 不可用时的基线。

怎么刷：按报告期全市场 fina_indicator_vip(period=…)，经数据网关（router.request，带熔断/限流/teajoin 10000 行自动翻页），
不走 _call 的 fina_fallback 分支（那个分支会拿本地基线回填，自己喂自己）。每期 1~2 次调用。
对每只可交易股票（0/3/6 开头，与 backfill_longterm.codes 同口径），用网关返回的该期全部行替换文件里该期的旧行，
其它期不动；内容没变的文件不重写；写临时文件再 os.replace 原子替换（研究进程可能正在读）。
安全阀：某期网关返回的可交易股票数比本地该期已有的少 2% 以上（疑似截断/上游异常）就整期不写；
任何一期失败，清单 _manifest.json 的 covered_through 不前移（rolling_r12 据此判断本地覆盖到哪天，不会把半新半旧的库当完整）。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

FINA_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm" / "fina"
MANIFEST = "_manifest.json"
STR_COLS = ("ts_code", "ann_date", "end_date", "update_flag")
SHRINK_TOLERANCE = 0.98
DEFAULT_PERIOD_COUNT = 4  # 当前披露期 + 前 3 期（覆盖更正版）


def quarter_ends(today: str, n: int = DEFAULT_PERIOD_COUNT) -> list[str]:
    """today（YYYYMMDD）当天或之前的最近 n 个季度末，新到旧。"""
    y, out = int(today[:4]), []
    while len(out) < n:
        for md in ("1231", "0930", "0630", "0331"):
            p = f"{y}{md}"
            if p <= today and len(out) < n:
                out.append(p)
        y -= 1
    return out


def tradable(code: str) -> bool:
    return bool(code) and code[:1] in "036"


def load_manifest(fina_dir: Path = FINA_DIR) -> dict[str, Any]:
    try:
        return json.loads((fina_dir / MANIFEST).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _atomic_write_json(path: Path, obj: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}_", suffix=".parquet")
    os.close(fd)
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def local_columns(fina_dir: Path = FINA_DIR) -> list[str]:
    import pyarrow.parquet as pq

    cols: list[str] = []
    for f in sorted(fina_dir.glob("*.parquet")):
        for c in pq.read_schema(f).names:
            if c not in cols:
                cols.append(c)
    return cols


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """字符串列保持字符串，其余一律转数值（旧文件里约 700 个数值列被存成了字符串，读者本来就会 to_numeric）。"""
    df = df.copy()
    for c in df.columns:
        if c in STR_COLS:
            df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v).strip())
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_period(period: str, fields: list[str], *, pro: Any = None) -> pd.DataFrame:
    """经网关拉一期全市场（teajoin 自动翻页）；拿不到或形状不对就抛异常。"""
    from app.services.tushare_client import get_pro

    pro = pro or get_pro()
    f = ",".join(fields)
    try:
        env = pro._router.request("fina_indicator_vip", params={"period": period}, fields=f)
        df = pro._envelope_frame("fina_indicator_vip", f, env)
    except Exception as exc:  # noqa: BLE001
        # 全字段（约 170 列）× 1 万多行的半年报，teajoin 忙时整页查询会 query timeout（11-05 午间实测连续两次）；
        # 改用小页显式 limit/offset 再试一次（teajoin 实测支持；不支持的源会在这里直接失败，不返回半截）。
        logger.warning("fina_indicator_vip %s 整页失败（%s），改 %d 行一页重试", period, exc, SMALL_PAGE)
        df = _fetch_small_pages(pro, period, f)
    if df is None or df.empty or not {"ts_code", "ann_date", "end_date"} <= set(df.columns):
        raise RuntimeError(f"fina_indicator_vip {period} 返回空或缺主键列")
    if len(df) % 10000 == 0:
        raise RuntimeError(f"fina_indicator_vip {period} 行数 {len(df)} 正好是 10000 的整数倍，疑似截断")
    bad = df[df["end_date"].astype(str) != period]
    if len(bad):
        raise RuntimeError(f"fina_indicator_vip {period} 混入 {len(bad)} 行其它报告期")
    return df


SMALL_PAGE = 3000
MAX_SMALL_PAGES = 20


def _fetch_small_pages(pro: Any, period: str, fields: str) -> pd.DataFrame:
    """显式 limit/offset 小页拉一期（调用方带了 limit/offset，网关不再自动翻页）；任一页失败就整体抛异常。"""
    frames = []
    for i in range(MAX_SMALL_PAGES):
        env = pro._router.request("fina_indicator_vip", params={"period": period, "limit": SMALL_PAGE,
                                                                "offset": i * SMALL_PAGE}, fields=fields)
        page = pro._envelope_frame("fina_indicator_vip", fields, env)
        if page is None or page.empty:
            break
        if frames and page.iloc[0].equals(frames[0].iloc[0]):
            raise RuntimeError("上游忽略 offset，小页重试取不全")
        frames.append(page)
        if len(page) < SMALL_PAGE:
            break
    else:
        raise RuntimeError(f"小页超过 {MAX_SMALL_PAGES} 页仍未取完")
    if not frames:
        raise RuntimeError("小页重试返回空")
    return pd.concat(frames, ignore_index=True)


def local_period_index(fina_dir: Path = FINA_DIR, periods: list[str] | None = None) -> dict[str, set[str]]:
    """{报告期: 本地有该期的股票}；每个文件只读一次 end_date 列。"""
    import pyarrow.parquet as pq

    want = set(periods or ())
    out: dict[str, set[str]] = {p: set() for p in want}
    for f in fina_dir.glob("*.parquet"):
        try:
            ends = {str(v) for v in pq.read_table(f, columns=["end_date"]).column(0).to_pylist()}
        except Exception:  # noqa: BLE001
            continue
        for p in (ends & want) if want else ends:
            out.setdefault(p, set()).add(f.stem)
    return out


def refresh(*, periods: list[str] | None = None, fina_dir: Path = FINA_DIR, pro: Any = None,
            today: str | None = None, dry_run: bool = False, fetch=None) -> dict[str, Any]:
    today = today or _dt.date.today().strftime("%Y%m%d")
    periods = periods or quarter_ends(today)
    fetch = fetch or (lambda p, cols: fetch_period(p, cols, pro=pro))
    fina_dir.mkdir(parents=True, exist_ok=True)
    cols = local_columns(fina_dir) or ["ts_code", "ann_date", "end_date"]
    out: dict[str, Any] = {"today": today, "periods": {}, "calls_estimate": len(periods) + 1, "dry_run": dry_run}
    new_by_code: dict[str, dict[str, pd.DataFrame]] = {}
    failed: list[str] = []
    index = local_period_index(fina_dir, periods)
    for p in periods:
        try:
            df = fetch(p, cols)
        except Exception as exc:  # noqa: BLE001
            failed.append(p)
            out["periods"][p] = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            logger.warning("local fina refresh: period %s fetch failed: %s", p, exc)
            continue
        df = df.copy()
        df["_code"] = df["ts_code"].astype(str).str[:6]
        df = df[df["_code"].map(tradable)]
        have = index.get(p, set())
        got = set(df["_code"])
        info = {"rows": int(len(df)), "codes": len(got), "local_codes_before": len(have),
                "new_codes": len(got - have)}
        if have and len(got) < len(have) * SHRINK_TOLERANCE:
            failed.append(p)
            out["periods"][p] = {**info, "status": "refused_shrink"}
            logger.warning("local fina refresh: period %s 网关只有 %d 只，本地已有 %d 只，疑似上游异常，整期不写", p, len(got), len(have))
            continue
        out["periods"][p] = {**info, "status": "fetched"}
        for code, g in df.groupby("_code"):
            new_by_code.setdefault(code, {})[p] = g.drop(columns="_code")

    # 只追加、不改旧行：本地是「按公告时点」的历史（研究和 SUE 取每期最早公告），按期接口和当初逐股接口对同一版本
    # 偶有细微数值差、也会少给一些更正版（实测 18 行），覆盖会改写历史。所以每期只追加本地还没有的 (公告日) 版本；
    # 公告日为空/非 8 位/晚于今天的行丢掉（它们会让按 ann_date 判覆盖的读者误以为本地覆盖到无穷远）。
    written = unchanged = created = rows_added = dropped_bad_ann = 0
    for code, by_p in sorted(new_by_code.items()):
        path = fina_dir / f"{code}.parquet"
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=cols)
        old_keys = set(zip(old["end_date"].astype(str), old["ann_date"].astype(str))) if len(old) else set()
        add = []
        for p, g in by_p.items():
            ann = g["ann_date"].map(lambda v: "" if v is None or (isinstance(v, float) and v != v) else str(v).strip())
            valid = ann.str.fullmatch(r"\d{8}") & (ann <= today)
            dropped_bad_ann += int((~valid).sum())
            g = g[valid & ~pd.Series([(p, a) in old_keys for a in ann], index=g.index)]
            if len(g):
                add.append(g)
        if not add:
            unchanged += 1
            continue
        new_rows = pd.concat(add, ignore_index=True)
        merged = pd.concat([x for x in (old, new_rows) if len(x)], ignore_index=True)
        merged = merged.reindex(columns=list(dict.fromkeys([*old.columns, *new_rows.columns])))
        merged = _normalize(merged).sort_values(["end_date", "ann_date"], ascending=False, kind="mergesort")
        created += 0 if path.exists() else 1
        rows_added += len(new_rows)
        if not dry_run:
            _atomic_write_parquet(merged.reset_index(drop=True), path)
        written += 1
    out.update(files_written=written, files_unchanged=unchanged, files_created=created, rows_added=rows_added,
               rows_dropped_bad_ann_date=dropped_bad_ann, failed_periods=failed)
    if not failed and not dry_run:
        man = load_manifest(fina_dir)
        man.update({"covered_through": today, "periods": periods,
                    "updated_at": _dt.datetime.now().isoformat(timespec="seconds"), "source": "fina_indicator_vip"})
        _atomic_write_json(fina_dir / MANIFEST, man)
        out["covered_through"] = today
    else:
        out["covered_through"] = load_manifest(fina_dir).get("covered_through")
    out["status"] = "completed" if not failed else "partial_failed"
    return out
