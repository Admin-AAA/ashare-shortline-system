"""分析最新跑批结果：负面标签、评分分布、链路评估"""
import json
import sys
from sqlalchemy import create_engine, text

def analyze(run_id: str):
    engine = create_engine(
        'mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4'
    )
    with engine.connect() as conn:
        # 跑批元信息
        meta = conn.execute(
            text("SELECT status, started_at, finished_at FROM recommendation_runs WHERE run_id = :rid"),
            {"rid": run_id},
        ).fetchone()
        if not meta:
            print(f"未找到跑批: {run_id}")
            return
        print(f"=== 跑批 {run_id} ===")
        print(f"状态: {meta[0]}, 开始: {meta[1]}, 结束: {meta[2]}")

        # 评分总览
        r = conn.execute(
            text("""
                SELECT bucket, COUNT(*) as cnt,
                       ROUND(AVG(final_score),1) as avg_score,
                       ROUND(MIN(final_score),1) as min_score,
                       ROUND(MAX(final_score),1) as max_score
                FROM recommendations WHERE run_id = :rid
                GROUP BY bucket ORDER BY FIELD(bucket, 'strong_buy', 'speculative', 'watch')
            """),
            {"rid": run_id},
        ).fetchall()
        print("\n=== 评分总览 ===")
        for row in r:
            print(f"  bucket={row[0]:20s} count={row[1]} avg={row[2]} min={row[3]} max={row[4]}")

        # 全部个股
        r2 = conn.execute(
            text("""
                SELECT code, name, industry, bucket, final_score, consensus_score,
                       risk_tags_json, skill_breakdown_json, agent_summary_json
                FROM recommendations WHERE run_id = :rid
                ORDER BY final_score DESC
            """),
            {"rid": run_id},
        ).fetchall()

        # AgentDecisionLog 统计
        adl_rows = conn.execute(
            text("""
                SELECT recommendation_id, agent_name, output_json
                FROM agent_decision_logs WHERE run_id = :rid
            """),
            {"rid": run_id},
        ).fetchall()
        adl_by_rec = {}
        for adl in adl_rows:
            adl_by_rec.setdefault(adl[0], []).append((adl[1], adl[2]))

        print(f"\n=== 共 {len(r2)} 只推荐详情 ===\n")
        all_risk_tags = {}
        all_skill_stances = {}
        total_tool_faults = 0

        for row in r2:
            code, name, ind, bucket, fs, cs, risk_json, skill_json, agent_json = row
            risks = json.loads(risk_json) if risk_json else []
            skills = json.loads(skill_json) if skill_json else []
            agent = json.loads(agent_json) if agent_json else {}

            for rtag in risks:
                key = str(rtag)[:80]
                all_risk_tags[key] = all_risk_tags.get(key, 0) + 1

            print(f"{code} {name:8s} {ind or '?':16s} {bucket:16s} final={fs:5.1f} cons={cs:5.1f}")

            # 每个 Skill 的审查结果
            for s in skills:
                sid = s.get("skill_id", "")[:24]
                review = s.get("agent_skill_review", {})
                stance = review.get("stance", "?")
                score_d = review.get("score_delta", "?")
                if isinstance(score_d, (int, float)):
                    score_d = round(score_d, 1)
                risk_list = review.get("risks", [])
                risk_count = len(risk_list)
                s_risk_flags = s.get("risk_flags", [])
                tool_faults = [t for t in s_risk_flags if "TOOL_FAULT" in str(t)]
                if tool_faults:
                    total_tool_faults += len(tool_faults)
                tf_str = ",TF" if tool_faults else ""
                summary = review.get("summary", "")[:80]
                all_skill_stances[stance] = all_skill_stances.get(stance, 0) + 1
                print(f"  [{sid}] stance={stance} delta={score_d} risks={risk_count}{tf_str}")
                if risk_list:
                    print(f"    risks: {risk_list[:3]}")
                if summary:
                    print(f"    summary: {summary}")

            # 风险标签
            if risks:
                print(f"  risk_tags: {risks[:5]}")
            print()

        # 汇总
        print("=" * 60)
        print("=== 汇总统计 ===")
        print(f"\n风险标签分布:")
        for tag, cnt in sorted(all_risk_tags.items(), key=lambda x: -x[1]):
            print(f"  [{cnt:3d}] {tag}")

        print(f"\nSkill stance 分布:")
        for s, cnt in sorted(all_skill_stances.items(), key=lambda x: -x[1]):
            print(f"  {s}: {cnt}")

        print(f"\nTOOL_FAULT 总数: {total_tool_faults}")

        # AgentDecisionLog 汇总
        print(f"\nAgentDecisionLog 记录数: {len(adl_rows)}")
        if adl_rows:
            stances_in_log = {}
            for rid, entries in adl_by_rec.items():
                for agent_name, output_json in entries:
                    try:
                        oj = json.loads(output_json)
                        stance = oj.get("stance", "?")
                        stances_in_log[stance] = stances_in_log.get(stance, 0) + 1
                    except Exception:
                        pass
            print(f"  Agent log stance 分布: {stances_in_log}")


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else "run_20260517_072604_fa552b"
    analyze(run_id)
