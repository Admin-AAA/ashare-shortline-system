"""按日重建**候选生成层**（生产规则重放），产出可复用的 canonical 候选池。

为什么需要它：此前 10+ 轮的所有测量都建立在**已存在的候选池**（冻结快照 / 一次
固定前瞻回放）之上，只能测「重排/剔除现有候选」。要测「改某个 skill 的 universe
或规则 → 候选池本身不同」这类改动，必须先能自己重建候选生成。

本轮查证结论（此前第 12 轮误判为"不可行"，实为可行）：
- 仓内已有**已提交**的权威实现：`ProductionRuleReplayCandidateBuilder(
     db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True)`
  —— `--historical-replay` 分支就是为"比实时池更早的 holdout 窗口"设计的；
- 5 个交易日冒烟（2025-06-16~20, historical_replay=True）成功产出 364 张卡，
  卡片字段与冻结快照同构（final_score / strategy_signals / recommend_price /
  pos60 / chg20 / raw_component_scores …），且自带 outcome_bars；
- 新闻类依赖**不是**阻塞项：13 个 skill 引用的 `announcement_negative_count` /
  `news_risk_count` 全部是**负向过滤**（`>0` 才触发），没有任何 skill 要求
  `positive_catalyst_count`；而这些负向过滤在 20cm 池上的命中率实测仅
  W2 0.05%（122/255,735 股票日）、W3 0.00%（2/60,276）。缺新闻数据只会让候选池
  成为**略大的超集**，偏差量级远小于要测的 0.1~1.5pp 效应。

设计：逐日构建、逐日落盘（可断点续跑），避免长窗口一次失败全废。
用法：
    cd backend && .venv/bin/python scripts/research_candidate_pool_rebuild.py \
        --start 2025-01-15 --end 2026-01-16 --out-dir output/research/pool_W1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.services.backtest.candidate_snapshot_builder import (  # noqa: E402
    ProductionRuleReplayCandidateBuilder,
)
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider  # noqa: E402


def _trading_days(start: str, end: str) -> list:
    with SessionLocal() as s:
        rows = s.execute(
            text(
                "select distinct trade_date from daily_kbars "
                "where trade_date between :a and :b order by trade_date"
            ),
            {"a": start, "b": end},
        ).all()
    return [str(r[0]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--score-line", type=float, default=72.0)
    ap.add_argument("--output-count", type=int, default=6)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = B / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    days = _trading_days(args.start, args.end)
    print(f"[rebuild] {args.start}~{args.end} 交易日 {len(days)} 天 → {out_dir}", flush=True)

    ep = ExecutionPolicy()
    done = skipped = 0
    t_start = time.time()
    for i, day in enumerate(days, 1):
        part = out_dir / f"{day}.json"
        if part.exists():
            skipped += 1
            continue
        t0 = time.time()
        try:
            with SessionLocal() as s:
                snap = ProductionRuleReplayCandidateBuilder(
                    s,
                    stock_metadata_provider=StockMetadataHistoryProvider(s),
                    historical_replay=True,
                ).build(
                    start_date=day, end_date=day, execution_policy=ep,
                    score_line=float(args.score_line), output_count=int(args.output_count),
                    skill_ids=None,
                )
            payload = snap.canonical_payload()
            tmp = part.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False),
                encoding="utf-8",
            )
            tmp.replace(part)
            cards = sum(len(d.cards) for d in snap.days)
            done += 1
            print(f"[{i}/{len(days)}] {day} cards={cards} {time.time()-t0:.1f}s", flush=True)
        except Exception as exc:  # 单日失败不放弃整窗，落 error 文件后续重试
            (out_dir / f"{day}.error.json").write_text(
                json.dumps({"trade_date": day, "error": f"{type(exc).__name__}: {exc}"},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[{i}/{len(days)}] {day} ERROR {type(exc).__name__}: {str(exc)[:120]}", flush=True)
    elapsed = time.time() - t_start
    print(f"[rebuild] done={done} skipped={skipped} elapsed={elapsed/60:.1f}min "
          f"files={len(list(out_dir.glob('*.json')))}", flush=True)


if __name__ == "__main__":
    main()
