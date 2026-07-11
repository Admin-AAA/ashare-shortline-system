#!/usr/bin/env python3
"""曝光补跑目标诊断（只读，绝不改写任何数据）。

E 项闭环有两套「样本数」口径，历史上常被混用导致误判，本工具把两者并列呈现：

  [A] 胜率可信样本（补跑真正认的口径，与 scheduler_service._run_strategy_exposure 完全一致）
      = metrics_by_strategy(近30天 formal+experiment)
      = 按 (trade_date, code) 取 final_score 最高的一条去重（每票每天只算一次实际推荐）
      + 结算滞后 SETTLE_LAG_DAYS 截断（未结算样本不计入胜率）
      → 用于决定「哪些策略样本<阈值需要补跑」，也用于策略胜率计算。

  [B] 曝光活跃度（self_check E 项「曝光分布 / H_norm」口径）
      = JSON_TABLE 展开 skill_breakdown_json，近30天 formal+experiment，按推荐去重、不截断
      → 仅度量「各策略被推荐/曝光的频次与公平度」，不代表胜率可信度。

两套皆有效、度量不同；补跑只认 [A]。年轻系统近期活跃数据多落在 SETTLE_LAG 截断区内，
故 [A] 普遍远小于 [B]（如 daily-stock [A]=7 vs [B]=115）——这是「胜率样本尚未结算」的真实反映，
补跑会持续喂，待数据越过结算滞后窗口后 [A] 自然抬升、策略退出补跑。

用法: python3 backend/scripts/diagnose_exposure_backfill.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.outcome_service import metrics_by_strategy, SETTLE_LAG_DAYS
from app.services.scheduler_service import DEFAULT_EXPOSURE_MIN_SAMPLE
from app.services.workbench_service import sync_skills

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(BACKEND_DIR, "skills")


def has_self_improve_flag(skill_id: str) -> bool:
    path = os.path.join(SKILLS_DIR, skill_id, "strategy.yaml")
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        return "SELF-IMPROVE FLAG" in fh.read()


def _exposure_view(db, window_days: int = 30) -> dict[str, int]:
    """[B] self_check 曝光分布口径：JSON_TABLE 展开，按推荐去重，不截断。"""
    rows = db.execute(text("""
        SELECT jt.strategy_id, COUNT(DISTINCT rec.recommendation_id) n
        FROM recommendations rec
        JOIN recommendation_runs u ON rec.run_id = u.run_id
        JOIN JSON_TABLE(rec.skill_breakdown_json, '$[*]'
            COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')) jt
        WHERE u.run_mode IN ('formal', 'experiment')
          AND rec.skill_breakdown_json IS NOT NULL
          AND rec.recommended_at >= DATE_SUB(NOW(), INTERVAL :w DAY)
        GROUP BY jt.strategy_id
    """), {"w": window_days}).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def main() -> None:
    db = SessionLocal()
    try:
        window = 30
        min_sample = DEFAULT_EXPOSURE_MIN_SAMPLE

        skills = sync_skills(db)
        enabled = [s for s in skills if s.enabled]

        # [A] 胜率可信样本（补跑口径，与 _run_strategy_exposure 一致）
        metrics = metrics_by_strategy(db, window_days=window, run_mode=("formal", "experiment"))
        backfill_sample = {m.get("strategy_id"): int(m.get("sample_count") or 0) for m in metrics}
        backfill_hit = {m.get("strategy_id"): float(m.get("hit_rate") or 0.0) for m in metrics}

        # [B] 曝光活跃度（self_check 口径，仅参考）
        exposure = _exposure_view(db, window)

        print(f"窗口: 近 {window} 天 formal+experiment | 补跑阈值(胜率可信样本)<{min_sample} | SETTLE_LAG={SETTLE_LAG_DAYS}")
        print(f"enabled 策略总数: {len(enabled)}\n")

        print(f"=== [A] 胜率可信样本（补跑真正认的口径，与 _run_strategy_exposure 一致）===")
        print(f"    下一轮盘前补跑将命中（样本<{min_sample}）：")
        targets = []
        for s in sorted(enabled, key=lambda x: x.skill_id):
            cnt = backfill_sample.get(s.skill_id, 0)
            flag = "⚠️ regime盲区(已标记)" if has_self_improve_flag(s.skill_id) else "❗ 未标记(需排查)"
            if cnt < min_sample:
                targets.append(s.skill_id)
                print(f"    {s.skill_id:38s} n={cnt:3d}  命中率={backfill_hit.get(s.skill_id, 0.0):5.1f}%  {flag}")
        covered = [s.skill_id for s in enabled if backfill_sample.get(s.skill_id, 0) >= min_sample]
        print(f"    已达标（样本≥{min_sample}）：{len(covered)} 个 -> {covered}")

        print(f"\n=== [B] 曝光活跃度（self_check 口径，仅公平度参考，不驱动补跑）===")
        for sid, n in sorted(exposure.items(), key=lambda kv: -kv[1]):
            print(f"    {sid:38s} n={n}")

        flagged = sum(1 for sid in targets if has_self_improve_flag(sid))
        print(f"\n结论: 补跑将命中 {len(targets)} 个策略（按 [A] 口径）；"
              f"其中 {flagged} 个带 SELF-IMPROVE FLAG（预期内 regime 盲区，补跑持续喂、等市场转活跃）；"
              f"{len(targets) - flagged} 个未标记（若为 0 则无真·新覆盖问题）。")
        print(f"注: [A] 普遍远小于 [B]（如 daily-stock [A]={backfill_sample.get('daily-stock-analysis-skill', 0)} "
              f"vs [B]={exposure.get('daily-stock-analysis-skill', 0)}）系 SETTLE_LAG 截断导致近期活跃样本未结算，"
              f"属年轻系统正常现象，待数据越过结算窗口后 [A] 自然抬升。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
