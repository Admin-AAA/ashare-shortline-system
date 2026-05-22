"""
日K线回填脚本 v2 — 使用 Tushare 自建服务器为主要数据源。

fetch_daily_kbars 合并 daily + daily_basic + stk_factor + cyq_perf，
一次调用获取 OHLCV + 技术指标 + 筹码分布，直接写入 daily_kbars。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, StockQuote
from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators
from app.services.tushare_client import fetch_daily_kbars, fetch_latest_trade_date

PROGRESS_FILE = Path(__file__).resolve().parent / ".backfill_daily_bars_progress.jsonl"


@dataclass
class FetchResult:
    status: str
    code: str
    name: str
    bar_count: int = 0
    latest_trade_date: str | None = None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill A-share daily K-line data via Tushare.")
    parser.add_argument("--days", type=int, default=90, help="K-line count to fetch per stock.")
    parser.add_argument("--min-bars", type=int, default=30, help="Minimum bars required.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N stocks.")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N stocks.")
    parser.add_argument("--commit-interval", type=int, default=50, help="Commit every N stocks.")
    parser.add_argument("--force", action="store_true", help="Refetch even when local bars are ready.")
    parser.add_argument("--reset-progress", action="store_true", help="Ignore previous progress file.")
    parser.add_argument("--codes", type=str, default="", help="Comma-separated codes to backfill (empty = all).")
    parser.add_argument("--rate-limit", type=int, default=0, help="Max requests per minute (0 = unlimited).")
    return parser.parse_args()


def load_progress(reset: bool, target_trade_date: str) -> set[str]:
    if reset:
        PROGRESS_FILE.unlink(missing_ok=True)
        return set()
    if not PROGRESS_FILE.exists():
        return set()
    done: set[str] = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(item.get("target_trade_date") or "") != target_trade_date:
            continue
        if item.get("status") == "ok":
            code = str(item.get("code") or "")
            if code:
                done.add(code)
    return done


def append_progress(result: FetchResult, target_trade_date: str) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_trade_date": target_trade_date,
        "status": result.status,
        "code": result.code,
        "name": result.name,
        "bar_count": result.bar_count,
        "latest_trade_date": result.latest_trade_date,
        "error": result.error,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with PROGRESS_FILE.open("a", encoding="utf-8") as h:
        h.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_universe(db, limit_codes: list[str] | None) -> list[dict[str, Any]]:
    """从 stock_quotes 加载候选股票列表。"""
    if limit_codes:
        return [{"code": c, "name": c} for c in limit_codes]

    quotes = db.scalars(
        select(StockQuote).where(StockQuote.freshness_status == "fresh")
        .order_by(StockQuote.total_mv_yi.is_(None), StockQuote.total_mv_yi.desc())
        .limit(5200)
    ).all()
    return [{"code": q.code, "name": q.name} for q in quotes]


def latest_allowed_bar_date() -> str:
    """最新允许的 bar 日期（最新交易日）。"""
    try:
        return fetch_latest_trade_date()
    except Exception:
        from datetime import date
        return date.today().strftime("%Y%m%d")


def main() -> int:
    args = parse_args()
    settings = get_settings()
    latest_allowed = latest_allowed_bar_date()
    target_display = f"{latest_allowed[:4]}-{latest_allowed[4:6]}-{latest_allowed[6:]}"

    limit_codes = None
    if args.codes:
        limit_codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    db = SessionLocal()
    try:
        universe = load_universe(db, limit_codes)

        done_codes = load_progress(args.reset_progress, latest_allowed)

        # 过滤已完成的
        pending = []
        skipped = 0
        for q in universe:
            code = str(q.get("code") or "")
            if not code or len(code) != 6:
                continue
            if code in done_codes and not args.force:
                skipped += 1
                continue
            pending.append(q)

        if args.offset:
            pending = pending[args.offset:]
        if args.limit > 0:
            pending = pending[:args.limit]

        total = len(pending)
        print(f"日K回填: 待处理 {total} 只 (跳过 {skipped} 只已完成)")
        print(f"最新交易日: {target_display}, 目标: ≥{args.min_bars} bars/只")
        print()

        ok = 0
        short = 0
        failed = 0
        t0 = time.monotonic()

        for i, q in enumerate(pending):
            code = str(q.get("code") or "")
            name = str(q.get("name") or code)

            try:
                bars = fetch_daily_kbars(code, days=args.days)
                if args.rate_limit > 0:
                    time.sleep(60.0 / args.rate_limit)

                if not bars:
                    failed += 1
                    result = FetchResult("fail", code, name, error="Tushare 返回空")
                    append_progress(result, latest_allowed)
                    print(f"  [{i + 1}/{total}] {code} {name} — 无数据")
                    continue

                # 过滤掉未来日期
                bars = [b for b in bars if str(b.get("trade_date") or "") <= target_display]

                # 补充本地计算的自定义指标
                _enrich_bars_with_local_indicators(bars)

                latest = max(str(b.get("trade_date") or "") for b in bars) if bars else None
                bar_count = len(bars)

                # 写入 daily_kbars
                _persist_kbars(db, code, bars)

                if bar_count >= args.min_bars and latest and latest >= target_display:
                    ok += 1
                    result = FetchResult("ok", code, name, bar_count, latest)
                else:
                    short += 1
                    result = FetchResult("short", code, name, bar_count, latest,
                                         f"仅 {bar_count} bars, latest={latest}")
                append_progress(result, latest_allowed)

                if (i + 1) % args.commit_interval == 0:
                    db.commit()

                elapsed = time.monotonic() - t0
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                print(f"  [{i + 1}/{total}] {code} {name} — {bar_count} bars, "
                      f"latest={latest} ({ok} ok / {short} short / {failed} fail) "
                      f"| {rate:.0f}只/min")

            except Exception as exc:
                failed += 1
                result = FetchResult("fail", code, name, error=str(exc)[:200])
                append_progress(result, latest_allowed)
                print(f"  [{i + 1}/{total}] {code} {name} — ERROR: {exc}")

        db.commit()
        elapsed = time.monotonic() - t0
        print()
        print(f"完成: ok={ok} short={short} fail={failed}, 总耗时 {elapsed:.0f}s")
        return 0 if failed == 0 else 1

    finally:
        db.close()


def _enrich_bars_with_local_indicators(bars: list[dict[str, Any]]) -> None:
    """补充本地计算的自定义指标：bias, vol_ratio, breakout, drawdown, holder_pct 等。"""
    if len(bars) < 5:
        return

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    for i, bar in enumerate(bars):
        # 均线（简单移动平均）
        n5 = min(i + 1, 5)
        n10 = min(i + 1, 10)
        n20 = min(i + 1, 20)
        n60 = min(i + 1, 60)
        n250 = min(i + 1, 250)

        ma5 = sum(closes[i - n5 + 1 : i + 1]) / n5
        ma10 = sum(closes[i - n10 + 1 : i + 1]) / n10
        ma20 = sum(closes[i - n20 + 1 : i + 1]) / n20
        ma60 = sum(closes[i - n60 + 1 : i + 1]) / n60
        ma250 = sum(closes[i - n250 + 1 : i + 1]) / n250

        bar.setdefault("ma5", round(ma5, 4))
        bar.setdefault("ma10", round(ma10, 4))
        bar.setdefault("ma20", round(ma20, 4))
        bar.setdefault("ma60", round(ma60, 4))
        bar.setdefault("ma250", round(ma250, 4))

        # 乖离率
        bar.setdefault("bias_5", round((closes[i] - ma5) / ma5 * 100, 4) if ma5 else None)
        bar.setdefault("bias_10", round((closes[i] - ma10) / ma10 * 100, 4) if ma10 else None)
        bar.setdefault("bias_20", round((closes[i] - ma20) / ma20 * 100, 4) if ma20 else None)

        # 量比
        vol5 = sum(volumes[max(0, i - 4):i + 1]) / min(5, i + 1)
        vol20 = sum(volumes[max(0, i - 19):i + 1]) / min(20, i + 1)
        bar.setdefault("vol_ratio_5", round(volumes[i] / vol5, 4) if vol5 else None)
        bar.setdefault("vol_ratio_20", round(volumes[i] / vol20, 4) if vol20 else None)

        # 突破信号
        bar.setdefault("breakout20", 1 if closes[i] > ma20 else 0)
        bar.setdefault("breakout60", 1 if closes[i] > ma60 else 0)

    # 最大回撤（需要完整序列计算）
    for i in range(len(bars)):
        if i >= 20:
            max20 = max(closes[i - 19:i + 1])
            dd20 = min((closes[j] - max20) / max20 * 100 for j in range(i - 19, i + 1))
            bars[i].setdefault("max_dd20", round(dd20, 4))
        if i >= 60:
            max60 = max(closes[max(0, i - 59):i + 1])
            dd60 = min((closes[j] - max60) / max60 * 100 for j in range(max(0, i - 59), i + 1))
            bars[i].setdefault("max_dd60", round(dd60, 4))

    # 持仓比例、利润效率
    for i, bar in enumerate(bars):
        if bar.get("ma60") and bar["close"] > 0:
            pct = (bar["close"] - bar["ma60"]) / bar["ma60"] * 100
            bar.setdefault("holder_pct", round(pct, 2))
        if i >= 60 and bars[i].get("ma60"):
            gains = [bars[j]["close"] for j in range(max(0, i - 59), i + 1)
                     if bars[j]["close"] > bars[j].get("ma60", bars[j]["close"])]
            bar.setdefault("profit_efficiency", round(len(gains) / 60, 4) if gains else 0.0)


def _persist_kbars(db, code: str, bars: list[dict[str, Any]]) -> None:
    """批量 upsert daily_kbars。"""
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    rows = []
    for bar in bars:
        rows.append({
            "code": code,
            "trade_date": bar["trade_date"],
            "open": bar.get("open", 0),
            "high": bar.get("high", 0),
            "low": bar.get("low", 0),
            "close": bar.get("close", 0),
            "volume": bar.get("volume", 0),
            "amount": bar.get("amount", 0),
            "pct_chg": bar.get("pct_chg", 0),
            "turnover_rate": bar.get("turnover_rate", 0),
            "ma5": bar.get("ma5"),
            "ma10": bar.get("ma10"),
            "ma20": bar.get("ma20"),
            "ma60": bar.get("ma60"),
            "ma250": bar.get("ma250"),
            "macd_dif": bar.get("macd_dif"),
            "macd_dea": bar.get("macd_dea"),
            "macd_bar": bar.get("macd"),
            "kdj_k": bar.get("kdj_k"),
            "kdj_d": bar.get("kdj_d"),
            "kdj_j": bar.get("kdj_j"),
            "rsi_6": bar.get("rsi_6"),
            "rsi_12": bar.get("rsi_12"),
            "rsi_24": bar.get("rsi_24"),
            "boll_upper": bar.get("boll_upper"),
            "boll_mid": bar.get("boll_mid"),
            "boll_lower": bar.get("boll_lower"),
            "cci": bar.get("cci"),
            "bias_5": bar.get("bias_5"),
            "bias_10": bar.get("bias_10"),
            "bias_20": bar.get("bias_20"),
            "vol_ratio_5": bar.get("vol_ratio_5"),
            "vol_ratio_20": bar.get("vol_ratio_20"),
            "breakout20": bar.get("breakout20", 0),
            "breakout60": bar.get("breakout60", 0),
            "max_dd20": bar.get("max_dd20"),
            "max_dd60": bar.get("max_dd60"),
            "chip_cost_15": bar.get("chip_cost_15"),
            "chip_cost_50": bar.get("chip_cost_50"),
            "chip_cost_85": bar.get("chip_cost_85"),
            "chip_weight_avg": bar.get("chip_weight_avg"),
            "chip_winner_rate": bar.get("chip_winner_rate"),
            "holder_pct": bar.get("holder_pct"),
            "profit_efficiency": bar.get("profit_efficiency"),
            "source": bar.get("source", "tushare"),
        })

    if rows:
        stmt = mysql_insert(DailyKbar).values(rows)
        stmt = stmt.on_duplicate_key_update(
            open=stmt.inserted.open,
            high=stmt.inserted.high,
            low=stmt.inserted.low,
            close=stmt.inserted.close,
            volume=stmt.inserted.volume,
            amount=stmt.inserted.amount,
            pct_chg=stmt.inserted.pct_chg,
            turnover_rate=stmt.inserted.turnover_rate,
            ma5=stmt.inserted.ma5,
            ma10=stmt.inserted.ma10,
            ma20=stmt.inserted.ma20,
            ma60=stmt.inserted.ma60,
            ma250=stmt.inserted.ma250,
            macd_dif=stmt.inserted.macd_dif,
            macd_dea=stmt.inserted.macd_dea,
            macd_bar=stmt.inserted.macd_bar,
            kdj_k=stmt.inserted.kdj_k,
            kdj_d=stmt.inserted.kdj_d,
            kdj_j=stmt.inserted.kdj_j,
            rsi_6=stmt.inserted.rsi_6,
            rsi_12=stmt.inserted.rsi_12,
            rsi_24=stmt.inserted.rsi_24,
            boll_upper=stmt.inserted.boll_upper,
            boll_mid=stmt.inserted.boll_mid,
            boll_lower=stmt.inserted.boll_lower,
            cci=stmt.inserted.cci,
            bias_5=stmt.inserted.bias_5,
            bias_10=stmt.inserted.bias_10,
            bias_20=stmt.inserted.bias_20,
            vol_ratio_5=stmt.inserted.vol_ratio_5,
            vol_ratio_20=stmt.inserted.vol_ratio_20,
            breakout20=stmt.inserted.breakout20,
            breakout60=stmt.inserted.breakout60,
            max_dd20=stmt.inserted.max_dd20,
            max_dd60=stmt.inserted.max_dd60,
            chip_cost_15=stmt.inserted.chip_cost_15,
            chip_cost_50=stmt.inserted.chip_cost_50,
            chip_cost_85=stmt.inserted.chip_cost_85,
            chip_weight_avg=stmt.inserted.chip_weight_avg,
            chip_winner_rate=stmt.inserted.chip_winner_rate,
            holder_pct=stmt.inserted.holder_pct,
            profit_efficiency=stmt.inserted.profit_efficiency,
            source=stmt.inserted.source,
            updated_at=stmt.inserted.updated_at,
        )
        db.execute(stmt)


if __name__ == "__main__":
    raise SystemExit(main())
