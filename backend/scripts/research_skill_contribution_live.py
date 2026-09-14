"""逐策略贡献归因（生产 LIVE 口径）：recommendations.bucket × 已结算 outcome。

为什么需要：候选卡（冻结快照 / 回放 / 重建池）的 strategy_signals 只有
{runtime, score}，**没有 skill 身份**，所以回测侧无法做逐策略归因。
但生产 recommendations 表带 bucket 与 skill_breakdown_json（各 540 条），
配合已结算 outcome 就能算出**每个策略在真实生产里的收益贡献**——
这正是"删掉低收益策略"这个决定所需要的证据。

口径：用 app.services.official_sample_scope.official_sample_conditions()
（canonical LIVE_FORWARD 资格）+ 已结算 outcome（net_return_pct 非空）过滤。

只读。
用法：cd backend && .venv/bin/python scripts/research_skill_contribution_live.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_service import _ADDITIONAL_COST_PCT  # noqa: E402


def _stats(sub: pd.DataFrame) -> dict:
    gp, gl = sub.net[sub.net > 0].sum(), abs(sub.net[sub.net < 0].sum())
    return {"n": len(sub), "win": float((sub.net > 0).mean()), "avg": float(sub.net.mean()),
            "sum": float(sub.net.sum()),
            "pf": float(gp / gl) if gl > 1e-9 else float("inf"),
            "avg_win": float(sub.net[sub.net > 0].mean()) if (sub.net > 0).any() else float("nan"),
            "avg_loss": float(sub.net[sub.net < 0].mean()) if (sub.net < 0).any() else float("nan")}


def main() -> None:
    with SessionLocal() as s:
        rows = s.execute(
            select(
                Recommendation.trade_date,
                Recommendation.code,
                Recommendation.bucket,
                Recommendation.final_score,
                RecommendationOutcome.executed_return,
                RecommendationOutcome.entry_triggered,
                RecommendationOutcome.hit_invalid,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.exit_reason,
            )
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .join(RecommendationOutcome,
                  RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .where(*official_sample_conditions())
            .where(RecommendationOutcome.entry_triggered.is_(True))
            .where(RecommendationOutcome.executed_return.is_not(None))
        ).all()
    df = pd.DataFrame(rows, columns=["trade_date", "code", "bucket", "final_score",
                                     "executed_return", "entry_triggered", "hit_invalid",
                                     "label", "exit_reason"])
    if df.empty:
        print("无 canonical LIVE 已结算样本")
        return
    # 口径与 outcome_service 一致：executed_return 已含滑点，再扣佣金+印花税
    df["net"] = pd.to_numeric(df["executed_return"], errors="coerce") - _ADDITIONAL_COST_PCT
    df = df.dropna(subset=["net"])
    print(f"canonical LIVE 已结算样本：{len(df)} 条，覆盖 {df.trade_date.nunique()} 个交易日 "
          f"（{df.trade_date.min()} ~ {df.trade_date.max()}）\n")
    overall = _stats(df)
    out = []
    for bucket, sub in df.groupby(df.bucket.fillna("(空)")):
        st = _stats(sub)
        rest = _stats(df[df.bucket != bucket])
        out.append((bucket, st, rest))
    out.sort(key=lambda x: -(x[2]["avg"] - overall["avg"]))
    print(f"{'bucket':32s} {'n':>4s} {'WR':>6s} {'avg':>8s} {'sum':>9s} {'PF':>5s} "
          f"{'剔除后avg':>9s} {'剔除后sum':>10s}")
    for bucket, st, rest in out:
        print(f"{str(bucket):32s} {st['n']:>4d} {st['win']*100:>5.1f}% {st['avg']:>+8.3f} "
              f"{st['sum']:>+9.2f} {st['pf']:>5.2f} {rest['avg']:>+9.3f} {rest['sum']:>+10.2f}")
    print(f"\n全样本：n={overall['n']} WR={overall['win']*100:.1f}% avg={overall['avg']:+.3f} "
          f"sum={overall['sum']:+.2f} PF={overall['pf']:.2f}")
    print("\n说明：剔除后列 = 把该 bucket 全部拿掉后的组合口径（不递补，只看方向）。"
          "剔除后 avg/sum 明显更好的才是删除候选；n<20 只作参考。")


if __name__ == "__main__":
    main()
