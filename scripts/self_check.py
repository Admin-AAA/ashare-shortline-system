#!/usr/bin/env python3
"""self-improve 每轮自检脚本：输出 A/B/C/D/E 五项北极星指标摘要。

用法：
    cd backend && source .venv/bin/activate && python3 ../scripts/self_check.py
"""

import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 让 backend/ 下的 app 包可被导入
BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text

SKILLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend', 'skills')

# A 股短线真实交易成本（与 app/services/outcome_service.py:_ADDITIONAL_COST_PCT 保持一致）：
# 佣金万1双边(0.02%) + 印花税卖出(0.05%) = 0.07%/笔。executed_return 已含滑点但未扣此成本。
# 北极星 D 项目标定义为"扣费后须达标"(T2 step4)，故 D 项必须呈净成本口径，不能只看毛值。
ADDITIONAL_COST_PCT = 0.07
PL_RATIO_BASELINE = 1.94   # 北极星 D 项盈亏比历史基线（LIVE 1.94）
PL_RATIO_TARGET = 2.0      # 北极星 D 项目标（朝 2.0 攻击）

# 策略规则引擎（用于 D 项"规则覆盖前瞻校验"——区分历史污染 vs 真退化）
# 惰性加载：仅在 check_d_rule_coverage 执行时载入一次，避免 A/B/C/E 项承受导入开销
_SKILLS_CACHE: dict | None = None


def _load_skills_once() -> dict:
    global _SKILLS_CACHE
    if _SKILLS_CACHE is None:
        try:
            from app.engine.skill_loader import load_skills
            _SKILLS_CACHE = {s.skill_id: s for s in load_skills(Path(SKILLS_DIR))}
        except Exception as e:
            # 规则引擎不可用时静默降级（D 项规则覆盖校验会跳过），不影响 A/B/C/D/E 主指标
            print(f'  (规则引擎加载失败，跳过 D 项覆盖校验: {type(e).__name__}: {e})')
            _SKILLS_CACHE = {}
    return _SKILLS_CACHE


def _zero_rec_day_is_protective(stage_summaries_list: list[list[dict]]) -> bool:
    """判断一个 0 推荐日是否"保护性 0 推荐"（D 项 score_line 拦截，非 pipeline 故障）。

    保护性：至少一个 completed run 的 filter_audit 显示 score_line 阶段 input>0 且 pass=0
    （候选进入分数线但最高分低于有效线，regime 有效线过高 / 恐慌日 veto / 强势 penalty）。
    故障性：所有 completed run 都未到 score_line（prefilter/skill_gate 卡住）或无 completed run。
    """
    for stages in stage_summaries_list:
        sl = next((s for s in stages if s.get("stage") == "score_line"), None)
        if sl and (sl.get("input_count") or 0) > 0 and (sl.get("pass_count") or 0) == 0:
            return True
    return False


def check_a_item(db) -> None:
    """A 项：最近 7 天 formal 推荐产出（按交易日聚合，标出断档日）。

    区分两类 0 推荐日：
    - 保护性 0 推荐：completed run 跑到 score_line 阶段但最高分低于有效线（regime
      有效线过高 / 恐慌日 veto / 强势 regime penalty），是"准确 > 可见"的设计意图，
      不算 pipeline 故障，不触发 T12。
    - 故障性 0 推荐：completed run 在 prefilter/skill_gate 阶段就 0 通过（候选没进
      score_line），或无 completed run（全 interrupted/failed），是真 T12 断档。
    """
    rows = db.execute(text("""
        SELECT trade_date, run_mode, status, recommended_count
        FROM recommendation_runs
        WHERE run_mode='formal' AND started_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY started_at DESC
    """)).fetchall()

    print('=== 最近 7 天 formal run ===')
    for row in rows:
        print(f'  {row[0]} {row[1]} {row[2]} rec_n={row[3]}')

    # 按 trade_date 聚合：某日只要有 completed+rec_n>0 即算该日有产出
    by_date: dict[str, bool] = {}
    for r in rows:
        td = str(r[0])
        has_ok = r[2] == 'completed' and (r[3] or 0) > 0
        by_date[td] = by_date.get(td, False) or has_ok

    # 真实交易日历（daily_kbars 有 K 线的日子）：recommendation_runs 可能出现
    # 非交易日（周末/节假日被误建 run，如 2026-07-19 周日全天 interrupted）。非交易日
    # 本就无 K 线、无真实推荐义务，0 推荐是预期而非 pipeline 断档——不应计入故障日触发
    # T12 假警报（否则未来轮次会浪费精力去"修"一个本就不该有推荐的非交易日）。
    # 窗口用 >= CURDATE()-8 天（日历日，略宽于 A 项 7 天窗口的下界 07-14），确保落在窗口
    # 边界的真实交易日（如 07-14）不被 INTERVAL 严格 > 漏判；多拉的日历日无 K 线自然被过滤。
    trade_cal_rows = db.execute(text("""
        SELECT DISTINCT trade_date FROM daily_kbars
        WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 8 DAY)
    """)).fetchall()
    real_trade_dates = {str(r[0]) for r in trade_cal_rows}

    ok_dates = sorted(d for d, ok in by_date.items() if ok)
    gap_dates = sorted(d for d, ok in by_date.items() if not ok)
    print(f'A 项：最近 7 天有 {len(ok_dates)} 个交易日产出 formal 推荐（目标 ≥5）')
    if gap_dates:
        protective: list[str] = []
        faulty: list[str] = []
        non_trade: list[str] = []
        for td in gap_dates:
            # 非交易日（周末/节假日，daily_kbars 无当日 K 线）本就无推荐义务：
            # 归 non_trade 提示，不进 faulty（不触发 T12），也无需查 filter_audit。
            if td not in real_trade_dates:
                non_trade.append(td)
                continue
            stage_rows = db.execute(text("""
                SELECT filter_audit_json
                FROM recommendation_runs
                WHERE trade_date = :td AND run_mode='formal' AND status='completed'
            """).bindparams(td=td)).fetchall()
            stage_summaries_list: list[list[dict]] = []
            for sr in stage_rows:
                try:
                    fa = json.loads(sr[0]) if sr[0] else {}
                except (TypeError, ValueError):
                    fa = {}
                stage_summaries_list.append(fa.get("stage_summary") or [])
            if _zero_rec_day_is_protective(stage_summaries_list):
                protective.append(td)
            else:
                faulty.append(td)
        if protective:
            print(f'  ℹ️ {len(protective)} 个交易日保护性 0 推荐（score_line 拦截，regime 有效线过高，非 pipeline 故障，不触发 T12）: {protective}')
        if non_trade:
            print(f'  ℹ️ {len(non_trade)} 个非交易日 0 推荐（周末/节假日无 K 线，本就该 0 推荐，不触发 T12）: {non_trade}')
        if faulty:
            print(f'  ⚠️ {len(faulty)} 个交易日故障性 0 推荐（prefilter/skill_gate 卡住或全 interrupted，T12 排查）: {faulty}')

    interrupted = [r for r in rows if r[2] == 'interrupted']
    if interrupted:
        print(f'⚠️ {len(interrupted)} 个 interrupted run')


def check_b_item(db) -> None:
    """B 项：最近推荐字段完整性（五项决策链：策略名+核心因子+入场/失效+LLM delta）。

    只看 formal run 的推荐：experiment 曝光补跑（skip_aux_refresh=True）的
    agent_summary 是空骨架，本就不走 LLM deep review，不应触发 LLM delta 缺失告警。
    """
    row = db.execute(text("""
        SELECT r.final_score, r.bucket, r.setup_type, r.entry_rule, r.invalid_rule,
               JSON_EXTRACT(r.feature_snapshot_json, '$.avg_cost')   AS avg_cost,
               JSON_EXTRACT(r.agent_summary_json,  '$.score_delta')  AS llm_delta,
               JSON_EXTRACT(r.agent_summary_json,  '$.stance')       AS llm_stance
        FROM recommendations r
        JOIN recommendation_runs u ON r.run_id = u.run_id
        WHERE u.run_mode = 'formal'
        ORDER BY r.recommended_at DESC
        LIMIT 1
    """)).fetchone()

    if not row:
        print('B 项：无推荐记录')
        return

    fields = {
        '策略名(bucket)':     row[1],
        '核心因子(avg_cost)': row[5],
        '入场规则':           row[3],
        '失效规则':           row[4],
        'LLM delta':          row[6],
    }
    missing = [k for k, v in fields.items() if not v]
    missing_str = str(missing) if missing else '无'
    print(
        f'B 项：score={row[0]} bucket={row[1]} setup={row[2]} '
        f'stance={row[7]} llm_delta={row[6]} 缺失={missing_str}'
    )

    # LLM delta 缺失时追加路由健康诊断：区分「LLM 本身不可用」vs「路由配置错配」两类根因，
    # 避免像第176轮那样——model_route_config.base_url 指向 api.deepseek.com 但用的是
    # jucode.cn 的 key，401 被误判为"key 失效"实际是 base_url/key 提供商不匹配。
    if 'LLM delta' in missing:
        _check_llm_route_health(db)

    # LLM 层有效性护栏（T8/T9）：去混杂判 delta 单调性 + oppose 率 + 顶层求和超 clamp。
    check_llm_delta_effectiveness(db)


def _llm_route_diagnosis(cache_latest, mins_since, total):
    """纯函数：基于 llm_review_cache 状态判定 LLM 路由失效证据（便于单测）。

    cache MAX 的局限：LLM 命中已有 cache 时不写新行（直接复用），故 cache
    「最近写入时间」不等于「LLM 最近工作过」。真正的失效信号是 B 项主查询——
    最近 formal run 的推荐缺 score_delta——已在调用方判定。本函数只给出
    cache 层的强失效证据（从未命中 / 跨多个交易日无新写入）或非失效提示。

    返回 (severity, message)：severity ∈ {'never','strong_stale','normal'}。
    """
    STRONG_STALE_MIN = 24 * 60
    if total == 0 or cache_latest is None:
        return ('never', 'llm_review_cache 从未命中过——LLM 审查层从未成功工作过（部署/配置缺失）')
    if mins_since is not None and mins_since > STRONG_STALE_MIN:
        return ('strong_stale', f'llm_review_cache {mins_since}分钟无新写入（>24h，跨多个交易日仍无新 cache）'
                f'——配合本次「最近 formal 推荐缺 LLM delta」，LLM 路由很可能失效')
    return ('normal', f'llm_review_cache 最近写入 {mins_since}分钟前（{cache_latest}）——'
            f'cache 仍在更新，本次 LLM delta 缺失可能是「该股票命中已有 cache 但未回填 score_delta」'
            f'或单只 LLM agent 异常，非全局路由失效')


def _check_llm_route_health(db) -> None:
    """LLM delta 缺失时的路由健康诊断（仅在最近 formal 推荐确实缺 LLM delta 时被调用）。

    用 _llm_route_diagnosis 纯函数判定 cache 失效证据；路由配置仅作排查信息打印。
    """
    try:
        cache_row = db.execute(text(
            "SELECT MAX(created_at) AS latest, "
            "TIMESTAMPDIFF(MINUTE, MAX(created_at), NOW()) AS mins_since_latest, "
            "COUNT(*) AS total "
            "FROM llm_review_cache"
        )).fetchone()
        route_rows = db.execute(text(
            "SELECT route_id, name, base_url, model, enabled, "
            "(api_key IS NOT NULL AND api_key != '') AS has_custom_key "
            "FROM model_route_config WHERE enabled = 1"
        )).fetchall()
    except Exception as exc:
        print(f'  ⚠️ LLM 路由诊断查询失败: {exc}')
        return

    cache_latest = cache_row[0] if cache_row else None
    mins_since = cache_row[1] if cache_row and cache_row[1] is not None else None
    total = int(cache_row[2] or 0) if cache_row else 0

    severity, msg = _llm_route_diagnosis(cache_latest, mins_since, total)
    prefix = '⚠️ ' if severity in ('never', 'strong_stale') else 'ℹ️ '
    print(f'  {prefix}{msg}')

    if not route_rows:
        print('    根因候选: model_route_config 无已启用路由')
    else:
        for r in route_rows:
            key_note = '自定义 key' if r[5] else '系统默认 key(.env ASHARE_AGENT_SKILL_API_KEY)'
            print(f'    路由 {r[1]} base_url={r[2]} model={r[3]} 使用{key_note}')
        print('    排查建议: 前端「设置→模型路由」点「测试」验证该 base_url 下系统默认 key 是否真实可用')
        print('    （常见根因：base_url 指向的 provider 与 .env key 发放方不一致，如 base_url=deepseek.com 但 key 是 jucode.cn 的）')


def _llm_delta_effectiveness(records: list[dict]) -> dict:
    """纯函数：LLM delta 有效性诊断（不碰 DB，便于单测）。

    背景（第 8 轮 T8 实证）：直接按「有无 delta」比分组胜率会得出「LLM 负价值」的
    误判——delta>0 的票 87% 集中 07-07/08/09（regime 不利日），无 delta 票分散在
    07-06/16/06-08（regime 较好日），且 0 个 run 同时含两类票（LLM 按 run 整体开关）。
    这是「开启 LLM 的 run 恰逢差日」的时间混杂，非 LLM 损害 D（feature_signal_guard
    预判的单日集中伪影）。去混杂后须按 delta 分档看单调性（大 delta=强信心应胜率更高），
    并盯两个真实问题：①oppose stance 占比（LLM 几乎从不反对则风险识别形同虚设）；
    ②顶层聚合 score_delta 超单 agent clamp[-18,16]（多 agent 求和未 clamp 的数据完整性）。

    参数 records: [{delta: float|None, stance: str|None, label: 'success'|'failed'}]
    返回 {monotonic_ok, bucket_wr, oppose_ratio, over_clamp_count, stance_delta_conflict, notes:[...]}
    """
    deltas = [(r["delta"], r["label"]) for r in records if r.get("delta") is not None]
    n_with = len(deltas)
    oppose = sum(1 for r in records if str(r.get("stance") or "").lower() == "oppose")
    oppose_ratio = oppose / len(records) if records else 0.0
    over_clamp = sum(1 for r in records if r.get("delta") is not None and (r["delta"] > 16 or r["delta"] < -18))
    # stance↔delta 矛盾：LLM 判 oppose/veto（反对）却仍给正 delta（推分过线）。
    # 根因（第 9 轮实证 rec_20260707_005_dc16）：score_delta=clamp(raw_score-50) 只看 score
    # 不看 stance，LLM 内部不一致时（stance=oppose/action=不做 但 score=60）→ delta=+10，
    # 反对票被推过 score_line。这是逻辑缺陷但低频（2/292）且去混杂后 delta 单调有效——
    # 不贸然改评分语义（可能错杀 LLM 识别出的隐藏强票），先显式化供前瞻样本积累后决策。
    stance_delta_conflict = sum(
        1 for r in records
        if str(r.get("stance") or "").lower() in ("oppose", "veto")
        and r.get("delta") is not None and r["delta"] > 0
    )

    def _wr(group):
        return round(sum(1 for _, l in group if l == "success") / len(group) * 100, 1) if group else None

    buckets = {
        "0-15": _wr([(d, l) for d, l in deltas if 0 <= d < 15]),
        "15-30": _wr([(d, l) for d, l in deltas if 15 <= d < 30]),
        "30+": _wr([(d, l) for d, l in deltas if d >= 30]),
    }
    # 单调性：大 delta 档胜率应 >= 小 delta 档（去混杂后 LLM 审查强度与质量正相关）
    vals = [v for v in (buckets["0-15"], buckets["15-30"], buckets["30+"]) if v is not None]
    monotonic_ok = len(vals) < 2 or vals[-1] >= vals[0]

    notes = []
    if n_with < 20:
        notes.append(f"有 delta 样本过少(n={n_with})，单调性判断为噪声")
    if oppose_ratio < 0.05 and len(records) >= 30:
        notes.append(
            f"oppose stance 占比 {oppose_ratio:.0%}<5%（n={len(records)}）。"
            f"主因是 selection bias：LLM 只审查已过 score_line 的高分候选（min54/中位76），"
            f"本就应多 support——SKILL.md 已有明确 oppose 触发标准（技术面偏空≥2维度差），"
            f"非 prompt 缺陷，勿误判「风险识别虚设」去改 prompt"
        )
    if stance_delta_conflict > 0:
        notes.append(
            f"{stance_delta_conflict} 条 stance=oppose/veto 却仍给正 delta：score_delta=clamp(score-50) "
            f"只看 score 不看 stance，LLM 内部不一致（反对但 score 及格）→ 反对票被推过线。"
            f"低频但属逻辑缺陷，前瞻样本确认无害前不改评分语义（防错杀 LLM 识别的隐藏强票）"
        )
    if over_clamp > 0:
        notes.append(
            f"{over_clamp} 条顶层聚合 score_delta 超单 agent clamp[-18,16]：多 agent 求和未 clamp，"
            f"读侧（projection/cost_service）按单 agent 语义消费会低估实际增量——数据完整性问题"
        )
    if not monotonic_ok:
        notes.append("delta 分档胜率非单调（大 delta 反而低胜率），LLM 校准失效，T9 需复核")
    return {
        "monotonic_ok": monotonic_ok,
        "bucket_wr": buckets,
        "oppose_ratio": round(oppose_ratio, 4),
        "over_clamp_count": over_clamp,
        "stance_delta_conflict": stance_delta_conflict,
        "notes": notes,
    }


def _llm_stance_winrate(records: list[dict]) -> dict:
    """纯函数：按 LLM stance 强度分档胜率——监测 LLM 信心信号是否持续提供区分度。

    背景（第193轮 T8 实证）：真 LLM 样本 strong_support n=18 WR=78% vs support n=102
    WR=62%，16pp 溢价；neutral_support 介于其间。stance 强度与胜率正相关 = LLM 审查
    提供了真实的信心区分度（非噪声）。但 strong_support 胜率溢价跨日不稳（07-08/09 强、
    07-07 无差）且样本小，**不构成可加 entry/加权规则**——仅作 LLM 层健康监测。
    若 strong_support 胜率溢价消失（→ support 水平），说明 LLM 信心校准退化，T9 需复核。

    stance 强度排序：strong_support > support > neutral_support > neutral > oppose。
    返回 {stance_wr: {stance: (n, wr)}, premium: strong_support_wr - support_wr,
    premium_ok, note}。样本不足（strong_support<10 或 support<10）时 premium=None。
    """
    groups: dict[str, list[str]] = {}
    for r in records:
        st = str(r.get("stance") or "").strip() or "None"
        groups.setdefault(st, []).append(r.get("label"))

    def _wr(labels):
        n = len(labels)
        if n == 0:
            return None
        return round(sum(1 for l in labels if l == "success") / n * 100, 1)

    stance_wr = {st: (len(labels), _wr(labels)) for st, labels in groups.items()}
    ss = stance_wr.get("strong_support")
    sp = stance_wr.get("support")
    premium = None
    premium_ok = None
    note = None
    if ss and sp and ss[0] >= 10 and sp[0] >= 10 and ss[1] is not None and sp[1] is not None:
        premium = round(ss[1] - sp[1], 1)
        # strong_support 应 >= support（信心越强胜率越高）；溢价消失/反转为负 = 校准退化
        premium_ok = premium >= 0
        if not premium_ok:
            note = (f'strong_support 胜率 {ss[1]}% 低于 support {sp[1]}%（溢价 {premium}pp 反转），'
                    f'LLM 信心校准退化——T9 prompt 应复核 strong_support 触发标准')
    return {"stance_wr": stance_wr, "premium": premium, "premium_ok": premium_ok, "note": note}


def check_llm_delta_effectiveness(db) -> None:
    """LLM 层（T8/T9 护栏）：delta 有效性 + oppose 率 + 超 clamp 诊断。

    防「LLM 负价值」误判（单日集中混杂），并把两个真实问题（从不 oppose / 顶层求和超 clamp）
    显式化，供 T9 prompt 优化与聚合 clamp 修复作依据。
    """
    try:
        rows = db.execute(text("""
            SELECT JSON_EXTRACT(r.agent_summary_json, '$.score_delta') AS delta,
                   JSON_EXTRACT(r.agent_summary_json, '$.stance')      AS stance,
                   o.outcome_label
            FROM recommendations r
            JOIN recommendation_runs u ON r.run_id = u.run_id
            JOIN recommendation_outcomes o ON r.recommendation_id = o.recommendation_id
            WHERE u.run_mode = 'formal' AND r.recommendation_id LIKE 'rec_%'
              AND o.outcome_label IN ('success','failed')
              AND r.agent_summary_json IS NOT NULL
        """)).fetchall()
    except Exception as exc:
        print(f'  ⚠️ LLM delta 有效性诊断查询失败: {exc}')
        return

    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            try:
                return float(json.loads(v))
            except (TypeError, ValueError):
                return None

    def _stance(v):
        # JSON_EXTRACT('$.stance') 返回带引号的 JSON 字符串（如 '"oppose"'），需 json.loads 去引号
        if v is None:
            return None
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return str(v)

    records = [
        {"delta": _f(r[0]), "stance": _stance(r[1]), "label": r[2]}
        for r in rows
    ]
    if not records:
        return
    result = _llm_delta_effectiveness(records)
    b = result["bucket_wr"]
    print(
        f'  LLM delta 有效性（去混杂·按档胜率）: 0-15={b["0-15"]}% 15-30={b["15-30"]}% '
        f'30+={b["30+"]}% | oppose率={result["oppose_ratio"]:.0%} 超clamp={result["over_clamp_count"]}条 '
        f'stance↔delta矛盾={result["stance_delta_conflict"]}条'
    )
    for note in result["notes"]:
        print(f'    ⚠️ {note}')

    # stance 强度分档胜率：监测 LLM 信心信号区分度（strong_support 应 >= support）。
    # 仅监测不加规则（strong_support 溢价跨日不稳、样本小，第193轮 T8 实证）。
    sw = _llm_stance_winrate(records)
    parts = []
    for st in ("strong_support", "support", "neutral_support", "neutral", "oppose"):
        if st in sw["stance_wr"]:
            n, wr = sw["stance_wr"][st]
            parts.append(f'{st}={wr}%(n={n})')
    if parts:
        prem = f' 溢价{sw["premium"]:+}pp' if sw["premium"] is not None else ''
        print(f'  LLM stance 分档胜率: {" ".join(parts)}{prem}')
        if sw["note"]:
            print(f'    ⚠️ {sw["note"]}')


def check_c_item(db) -> set:
    """C 项：策略命中覆盖。返回已命中策略集合供 E 项使用。"""
    rows = db.execute(text("""
        SELECT JSON_EXTRACT(skill_breakdown_json, '$[*].strategy_id') AS skills
        FROM recommendations r
        JOIN recommendation_runs u ON r.run_id = u.run_id
        WHERE u.run_mode = 'formal' AND r.skill_breakdown_json IS NOT NULL
    """)).fetchall()

    skills: set = set()
    for row in rows:
        try:
            arr = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if isinstance(arr, list):
                skills.update(arr)
        except Exception:
            pass

    print(f'C 项：formal 命中策略 {len(skills)} 个: {sorted(skills)}')
    return skills


def check_e_item(db, hit_skills: set) -> None:
    """E 项：策略覆盖盲区 + 低胜率标记。"""
    # 覆盖盲区
    registered = sorted(
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
    )
    blind = [s for s in registered if s not in hit_skills]
    print(f'E 项覆盖盲区：{len(blind)} 个策略近 60 天 formal 0 命中: {blind}')

    # 低覆盖 / 低胜率策略（按 recommendation_id 去重，避免 JSON_TABLE 多策略行重复计数）
    rows = db.execute(text("""
        WITH strategy_hits AS (
            SELECT DISTINCT rec.recommendation_id, jt.strategy_id, o.outcome_label, o.executed_return,
               u.trade_date
            FROM recommendations rec
            JOIN recommendation_runs u ON rec.run_id = u.run_id
            LEFT JOIN recommendation_outcomes o ON o.recommendation_id = rec.recommendation_id
            JOIN JSON_TABLE(
                rec.skill_breakdown_json, '$[*]'
                COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
            ) jt
            WHERE u.run_mode = 'formal'
              AND rec.skill_breakdown_json IS NOT NULL
              AND o.outcome_label IN ('success', 'failed')
        )
        SELECT strategy_id,
               COUNT(*)                                                          AS n,
               SUM(CASE WHEN outcome_label = 'success' THEN 1 ELSE 0 END)       AS wins,
               ROUND(AVG(executed_return), 2)                                    AS avg_ret,
               SUM(CASE WHEN trade_date >= :epoch THEN 1 ELSE 0 END)            AS fwd_n,
               SUM(CASE WHEN trade_date >= :epoch AND outcome_label = 'success' THEN 1 ELSE 0 END) AS fwd_wins
        FROM strategy_hits
        GROUP BY strategy_id
        HAVING n < 50 OR wins * 100 / COUNT(*) < 45
        ORDER BY wins * 100 / COUNT(*) ASC
    """), {"epoch": FORWARD_EPOCH}).fetchall()

    print('E 项低覆盖(n<50)或低胜率(<45%)策略:')
    for row in rows:
        n, wins, avg_ret, fwd_n, fwd_wins = row[1], row[2], row[3], row[4], row[5]
        win_rate = round(wins * 100 / n, 1)
        tag = '低胜率' if win_rate < 45 else '低覆盖'
        # 前瞻窗口口径（FORWARD_EPOCH 之后）：区分旧代码污染 vs 真低胜率
        # 前瞻无样本 → 等结算；前瞻胜率回升 → 旧污染；前瞻仍低 → 真低胜率需标记
        fwd_note = ""
        if fwd_n > 0:
            fwd_wr = round(fwd_wins * 100 / fwd_n, 1)
            fwd_note = f" | 前瞻{n>=20 and '仍' or ''}{fwd_n}笔 WR={fwd_wr}%{'(旧污染)' if fwd_wr >= 45 else '(真低胜率)'}"
        else:
            fwd_note = " | 前瞻0笔(等结算)"
        print(f'  {row[0]}: n={n} wins={wins} avg_ret={avg_ret}% winrate={win_rate}%{fwd_note} ←{tag}需标记')

    # 曝光公平度 + 样本置信度（近 30 天 formal+experiment，不含 backtest）
    # 目的：让每个策略胜率统计可信，避免头部策略吃掉 80%+ 曝光、尾部策略样本稀疏不可信。
    SAMPLE_FLOOR = 20          # 每策略近 30 天样本下限（统计可信）
    FAIRNESS_TARGET = 0.50     # 归一化熵目标（=1 表示完全均匀，=0 表示全集中一个策略）
    exp_rows = db.execute(text("""
        SELECT rec.recommendation_id, jt.strategy_id
        FROM recommendations rec
        JOIN recommendation_runs u ON rec.run_id = u.run_id
        JOIN JSON_TABLE(
            rec.skill_breakdown_json, '$[*]'
            COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
        ) jt
        WHERE u.run_mode IN ('formal', 'experiment')
          AND rec.skill_breakdown_json IS NOT NULL
          AND rec.recommended_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """)).fetchall()
    # 按 recommendation_id 去重（同条推荐被多策略命中，每个策略各计一次曝光）
    per_rec: dict = {}
    for rid, sid in exp_rows:
        per_rec.setdefault(rid, set()).add(sid)
    exposure: Counter = Counter()
    for sids in per_rec.values():
        for sid in sids:
            exposure[sid] += 1

    print(f'E 项曝光分布（近 30 天 formal+experiment，按推荐去重）:')
    total_exp = sum(exposure.values())
    for sid, n in exposure.most_common():
        share = (n / total_exp * 100) if total_exp else 0
        flag = '⚠️<样本下限' if n < SAMPLE_FLOOR else ''
        print(f'  {sid}: {n} ({share:.1f}%){(" " + flag) if flag else ""}')

    # 归一化熵 H/H_max：衡量曝光集中度。被命中的策略数 = exposure 的 key 数
    k = len(exposure)
    if k > 1 and total_exp > 0:
        entropy = -sum((n / total_exp) * math.log(n / total_exp) for n in exposure.values())
        h_norm = entropy / math.log(k)
    else:
        h_norm = 0.0
    fair_tag = '✅ 达标' if h_norm >= FAIRNESS_TARGET else '⚠️ 过度集中'
    print(f'E 项曝光公平度: H_norm={h_norm:.3f} （目标 ≥{FAIRNESS_TARGET}，{fair_tag}）')

    # 注册策略中近 30 天 0 曝光的（曝光补跑应持续喂到 SAMPLE_FLOOR）
    zero_exp = [s for s in registered if s not in exposure]
    if zero_exp:
        print(f'⚠️ {len(zero_exp)} 个注册策略近 30 天 0 曝光（补跑未覆盖）: {sorted(zero_exp)}')
    under_floor = sorted(s for s, n in exposure.items() if n < SAMPLE_FLOOR)
    if under_floor:
        print(f'⚠️ {len(under_floor)} 个策略样本 <{SAMPLE_FLOOR}（胜率统计不可信，补跑应持续喂）: {under_floor}')

    # 曝光补跑健康诊断：样本 <20 的策略里，区分「补跑在喂但窄口径天然慢」vs
    # 「补跑 run 失败停滞」。只查近 7 天该策略出现在 experiment run skill_breakdown
    # 的推荐数：>0 = 在喂（窄策略样本增长慢是 universe 窄非 bug）；0 = 停滞（补跑
    # 失败/run 未触发，需人工触发修复——曾出现 high-position 连续 2 天曝光补跑
    # 失败导致样本永远卡在 3 笔但 E 项只报「n<20」不自知的静默事故）。
    if under_floor:
        recent_rows = db.execute(text("""
            SELECT jt.strategy_id AS sid, MAX(u.started_at) AS latest, COUNT(*) AS n
            FROM recommendations rec
            JOIN recommendation_runs u ON rec.run_id = u.run_id AND u.run_mode = 'experiment'
            JOIN JSON_TABLE(
                rec.skill_breakdown_json, '$[*]'
                COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
            ) jt
            WHERE rec.skill_breakdown_json IS NOT NULL
              AND rec.recommended_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY jt.strategy_id
        """)).fetchall()
        recent_in_experiment: dict[str, tuple] = {row[0]: (row[1], int(row[2] or 0)) for row in recent_rows}
        stalled: list[str] = []
        feeding: list[str] = []
        for sid in under_floor:
            hit = recent_in_experiment.get(sid)
            if hit and hit[1] > 0:
                feeding.append(f'{sid}({hit[1]}笔@{str(hit[0])[:10]})')
            else:
                stalled.append(sid)
        if feeding:
            print(f'  ℹ️ 补跑在喂（窄口径样本天然慢，非 bug，不加 universe）: {feeding}')
        if stalled:
            print(f'  🚨 补跑停滞（近7天 0 experiment 命中，run 可能失败/未触发，需手动触发验证）: {stalled}')


# 前瞻样本分界：自该交易日起，已上线的策略规则（strategy.yaml / aggregator.py）
# 之后生成的 formal run 才反映修复后的真实胜率。此前样本含修复前历史污染
# （如 2026-07-07/07-08 在去重与弱市否决 commit 之前生成的 run），
# 不应据此循环加规则（见 T3 失败归因 / 第160-163轮）。
#
# 时间错位陷阱（d-item-time-lag-illusion）：硬编码日期会随每轮新规则上线而失准——
# 例如 2026-07-09 设为 epoch 时，07-09 当天生成的 overnight 失败样本其实是 07-16
# 震荡 veto 上线前的产物，却被当"前瞻修复后"统计，造成 D 项前瞻盈亏比假性下滑 1.36，
# 误导后续轮次循环加规则。故改为动态解析：取最近一次改动 strategy.yaml/aggregator.py
# 的 commit 时间之后的首个 formal 交易日。commit 后若无交易日，返回 None
# （前瞻窗口空，self_check 报"样本不足"，不再拿修复前样本算假指标）。
REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _last_rule_change_time() -> datetime | None:
    """最近一次改动策略规则（strategy.yaml / aggregator.py）的 commit 时间。

    本地仓库时间；self_check 仅本地运行（CLAUDE.md「本地 MySQL 为主」），
    仓库历史必在。git 不可用时返回 None（降级为无前瞻窗口）。

    返回 naive datetime（去掉 tzinfo，保留 commit 壁钟时间）：recommendation_runs.started_at
    在 DB 里是 naive 北京时间，壁钟对齐才能正确比较「commit 之后生成的 run」。
    """
    try:
        out = subprocess.check_output(
            [
                "git", "-C", REPO_DIR, "log", "-1", "--format=%cI",
                "--", "backend/skills/", "backend/app/engine/aggregator.py",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        if not out:
            return None
        return datetime.fromisoformat(out).replace(tzinfo=None)
    except Exception:
        return None


def _resolve_forward_epoch(db) -> str | None:
    """策略规则最近一次上线 commit 之后的首个 formal 交易日。

    规则 commit 时间后的首个交易日，该日及以后的 formal run 才是在新规则下生成。
    commit 当天盘前的 run 仍是旧规则产物，故严格用 commit 时间之后首个 trade_date。
    若 commit 后还没有 formal 推荐交易日，返回 None（前瞻窗口空）。
    """
    t = _last_rule_change_time()
    if t is None:
        return None
    # 该 commit 时间之后首个有 LIVE formal 推荐的交易日
    row = db.execute(text("""
        SELECT MIN(r.trade_date) AS d
        FROM recommendations r
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE r.recommendation_id LIKE 'rec_%'
          AND u.started_at >= :t
    """), {"t": t}).first()
    d = row[0] if row else None
    return str(d) if d else None


# 模块加载时暂置为占位 None；main() 里建立 db 后解析真实值并写回模块级常量，
# 供 check_e_item / check_d_item 等所有引用点统一使用。
FORWARD_EPOCH: str | None = None


def _fetch_dedup_live_formal(db) -> list:
    """LIVE formal 成功/失败样本，按 (code, trade_date) 去重。
    返回 [(code, trade_date_str, label, executed_return), ...]。
    """
    return db.execute(text("""
        SELECT r.code, r.trade_date, MAX(o.outcome_label) AS label,
               ROUND(MIN(o.executed_return), 2) AS min_ret
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
        GROUP BY r.code, r.trade_date
    """)).fetchall()


def _compute_d_window(rows: list, *, since_date: str | None = None,
                      trade_dates: set | None = None) -> tuple | None:
    """纯函数：在 rows（[(code, trade_date, label, executed_return)]）上按窗口
    计算 (win_rate, n, max_loss)。since_date / trade_dates 二选一过滤。
    不碰 DB，便于单元测试锁定统计逻辑，避免每轮误判退化循环加规则过拟合。
    """
    if trade_dates is not None:
        win = [r for r in rows if str(r[1]) in trade_dates]
    elif since_date is not None:
        win = [r for r in rows if str(r[1]) >= since_date]
    else:
        win = list(rows)
    if not win:
        return None
    n = len(win)
    wins = sum(1 for r in win if r[2] == 'success')
    wr = round(wins * 100 / n, 1)
    ml = round(min(r[3] for r in win if r[3] is not None), 2)
    return wr, n, ml


def _classify_maxloss(ml: float, baseline_loss: float) -> str:
    """分类 maxloss 相对基线的状态。

    maxloss 是单只股票的最差收益（极端值），对单一样本敏感——一只高开入场票（gap+7%）
    在 MAX_STOP_LOSS_PCT=-5.7% 下触发价略低于止损价，maxloss 就从 -5.80% 推到 -5.88%。
    这种 ≤0.5pp 的差异是日内波动噪声，不是退化信号。真退化（如 -9.18%→-7%）远超容差，
    仍报"恶化"。
    """
    MAXLOSS_TOLERANCE_PP = 0.5  # maxloss 容差：极端值对单一样本敏感，≤0.5pp 算噪声
    if ml < baseline_loss - MAXLOSS_TOLERANCE_PP:
        return '⚠️ 恶化'
    if ml <= baseline_loss + MAXLOSS_TOLERANCE_PP:
        return f'持平（基线 ±{MAXLOSS_TOLERANCE_PP}pp 噪声范围）'
    return '优于基线'


def _fmt_d(tag: str, res: tuple | None, baseline_wr: float, baseline_loss: float) -> None:
    if res is None:
        print(f'  {tag}: 样本不足（前瞻窗口需等修复后新 run 结算）')
        return
    wr, n, ml = res
    d = round(wr - baseline_wr, 1)
    tag_w = f'⚠️ 下滑 {abs(d)}pp' if d < 0 else f'↑ 高于基线 {d}pp'
    tag_l = _classify_maxloss(ml, baseline_loss)
    print(f'  {tag}: 胜率 {wr}% (n={n}, 基线 {baseline_wr}%, {tag_w}) | 最大亏损 {ml}% ({tag_l})')


def _compute_d_cost(rows: list, *, since_date: str | None = None,
                    trade_dates: set | None = None) -> dict | None:
    """纯函数：在去重 LIVE formal 样本 rows[(code, trade_date, label, ret)] 上，
    计算扣交易成本(0.07%/笔)后的净指标——暴露 D 项第三子指标 盈亏比(P/L ratio)
    并诚实呈现净胜率/净平均收益。

    executed_return 已含滑点但未扣佣金+印花税（见 outcome_service._ADDITIONAL_COST_PCT），
    而北极星 D 项目标定义为"扣费后须达标"(T2 step4)。此前 D 项只报毛胜率+最大亏损，
    盈亏比与净胜率缺失 → 1/3 核心攻击目标不可见。本函数补齐，纯函数便于单测锁定逻辑，
    不碰 DB，与 _compute_d_window 正交（后者保毛胜率/最大亏损基线对比连续性）。
    """
    if trade_dates is not None:
        win = [r for r in rows if str(r[1]) in trade_dates]
    elif since_date is not None:
        win = [r for r in rows if str(r[1]) >= since_date]
    else:
        win = list(rows)
    rets = [float(r[3]) for r in win if r[3] is not None]
    n = len(rets)
    if n == 0:
        return None
    net = [x - ADDITIONAL_COST_PCT for x in rets]
    gross_wins = sum(1 for x in rets if x > 0)
    net_wins = sum(1 for x in net if x > 0)
    gross_profit = sum(x for x in rets if x > 0)
    gross_loss = abs(sum(x for x in rets if x < 0))
    net_profit = sum(x for x in net if x > 0)
    net_loss = abs(sum(x for x in net if x < 0))
    gross_pl = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0
    net_pl = round(net_profit / net_loss, 2) if net_loss > 0 else 999.0
    return {
        "n": n,
        "gross_wr": round(gross_wins * 100 / n, 1),
        "net_wr": round(net_wins * 100 / n, 1),
        "gross_pl": gross_pl,
        "net_pl": net_pl,
        "gross_avg": round(sum(rets) / n, 2),
        "net_avg": round(sum(net) / n, 2),
        "max_loss": round(min(rets), 2),
    }


def _fmt_d_cost(tag: str, m: dict | None, baseline_wr: float, baseline_pl: float) -> None:
    """打印净成本 D 项块（盈亏比 + 净胜率 + 净平均收益）。"""
    if m is None:
        print(f'  {tag}(净成本): 样本不足')
        return
    d_w = round(m["net_wr"] - baseline_wr, 1)
    tag_w = (f'⚠️ 净胜率下滑 {abs(d_w)}pp' if d_w < 0
             else f'净胜率高于基线 {d_w}pp')
    d_p = round(m["net_pl"] - baseline_pl, 2)
    if m["net_pl"] >= PL_RATIO_TARGET:
        tag_p = f'✅ 净盈亏比达标(≥{PL_RATIO_TARGET})'
    elif d_p < 0:
        tag_p = f'⚠️ 净盈亏比低于基线 {abs(d_p)}'
    else:
        tag_p = f'净盈亏比高于基线 {d_p}(未达{PL_RATIO_TARGET}目标)'
    print(f'  {tag}(净成本,扣{ADDITIONAL_COST_PCT}%): 净胜率 {m["net_wr"]}% (毛 {m["gross_wr"]}%, {tag_w}) '
          f'| 盈亏比 净 {m["net_pl"]}/毛 {m["gross_pl"]} ({tag_p}) '
          f'| 平均收益 净 {m["net_avg"]}%/毛 {m["gross_avg"]}%')


def _single_day_pl_drag(rows: list, trade_dates: set) -> dict | None:
    """纯函数：诊断近 N 日窗口内 PF 是否被单一交易日拖累（单日 regime 噪声 vs 真退化）。

    背景（第191轮 T3 实证）：D 项近10日净盈亏比低于基线时，需先区分「单日情绪异常日
    主导」vs「多日普遍低 PF」。前者（如单一冰点被误判活跃日，见 lessons #350 /
    no_follow_through 子簇护栏）个股 entry 侧不可解、禁止加规则；后者才需正常排查。
    若不显式诊断，运营者看到 PF<基线 会误判真退化、循环加 entry 规则过拟合噪声。

    判定：剔除「窗口内总亏损最大的单日」后 PF 回升 >= PF_RECOVER_MIN，且该日亏损
    占窗口总亏损 >= LOSS_SHARE_MIN → 返回单日拖累诊断；否则 None（PF 下滑是普遍的，
    可能是真退化或多日连续低 PF，需正常排查，不豁免）。

    不碰 DB，便于单测锁定。返回 {worst_day, day_loss, day_loss_share, pf_with, pf_without, recover}。
    """
    PF_RECOVER_MIN = 0.15   # 剔除单日后 PF 回升 >=0.15 才算「单日显著拖累」
    LOSS_SHARE_MIN = 0.40   # 该日亏损占窗口总亏损 >=40% 才认定是单日主导
    win_rows = [r for r in rows if str(r[1]) in trade_dates]
    if not win_rows:
        return None
    # 按日聚合净收益（扣成本口径，与 _compute_d_cost 一致）
    day_net: dict[str, list[float]] = {}
    for r in win_rows:
        if r[3] is None:
            continue
        day_net.setdefault(str(r[1]), []).append(float(r[3]) - ADDITIONAL_COST_PCT)
    if not day_net:
        return None

    def _pf(nets: list[float]) -> float:
        profit = sum(x for x in nets if x > 0)
        loss = abs(sum(x for x in nets if x < 0))
        return round(profit / loss, 2) if loss > 0 else 999.0

    day_loss = {d: abs(sum(x for x in nets if x < 0)) for d, nets in day_net.items()}
    total_loss = sum(day_loss.values())
    if total_loss <= 0:
        return None
    worst_day = max(day_loss, key=lambda d: day_loss[d])
    worst_loss = day_loss[worst_day]
    loss_share = worst_loss / total_loss

    all_nets = [x for nets in day_net.values() for x in nets]
    pf_with = _pf(all_nets)
    pf_without = _pf([x for d, nets in day_net.items() if d != worst_day for x in nets])
    recover = round(pf_without - pf_with, 2)

    if loss_share >= LOSS_SHARE_MIN and recover >= PF_RECOVER_MIN:
        return {
            "worst_day": worst_day,
            "day_loss": round(worst_loss, 2),
            "day_loss_share": round(loss_share * 100, 1),
            "pf_with": pf_with,
            "pf_without": pf_without,
            "recover": recover,
        }
    return None


def _is_20cm_code(code: str) -> bool:
    """20cm 板块判定（创业板 300/301、科创板 688/689）。"""
    return str(code).startswith(("300", "301", "688", "689"))


def _compute_d_board_breakdown(rows: list) -> dict:
    """纯函数：按板块(20cm 创业板/科创板 vs 10cm 主板/中小板)拆分 D 项。

    返回 {board: {n, win_rate, max_loss, avg}}（无样本为 None）。
    防过拟合护栏（数据驱动结论，第169轮 T3 实证）：
    若 20cm 是胜率最高分段，后续 self-improve 轮次**不得**对 20cm 加风险闸门/降权/
    缩小 universe——会砍掉系统最强分段、损害整体 D 项胜率。最大亏损在两类板块均
    为止损地板(~-5.7%)，并非「20cm 跳空驱动」，故板块不是 maxloss 的归因因子。
    """
    out: dict = {}
    for board in ("20cm", "10cm"):
        grp = [r for r in rows if _is_20cm_code(r[0]) == (board == "20cm")]
        n = len(grp)
        if n == 0:
            out[board] = None
            continue
        wins = sum(1 for r in grp if r[2] == "success")
        rets = [float(r[3]) for r in grp if r[3] is not None]
        out[board] = {
            "n": n,
            "win_rate": round(wins * 100 / n, 1),
            "max_loss": round(min(rets), 2) if rets else 0.0,
            "avg": round(sum(rets) / n, 2),
        }
    return out


_BOARD_LABEL = {"20cm": "20cm(创业板/科创板)", "10cm": "10cm(主板/中小板)"}


def _fmt_d_board(tag: str, bd: dict) -> None:
    if not bd:
        return
    for board in ("20cm", "10cm"):
        m = bd.get(board)
        if m is None:
            print(f'  {tag}[{_BOARD_LABEL[board]}]: 样本不足')
            continue
        print(f'  {tag}[{_BOARD_LABEL[board]}]: 胜率 {m["win_rate"]}% (n={m["n"]}) '
              f'| 最大亏损 {m["max_loss"]}% | 平均收益 {m["avg"]}%')


def _rsi_pollution_split(rows: list) -> tuple[float, float, int, int] | None:
    """纯函数：按 RSI 污染（rsi_6=0 & rsi_12=50）拆分去重样本，返回
    (clean_wr, polluted_wr, clean_n, polluted_n)。无 clean 或无 polluted 返回 None。
    rows 元素: (code, trade_date, label, rsi6, rsi12)。便于单测锁定分类+胜率计算。
    """
    if not rows:
        return None
    clean = [r for r in rows if not (float(r[3] or 0) == 0.0 and float(r[4] or 0) == 50.0)]
    polluted = [r for r in rows if float(r[3] or 0) == 0.0 and float(r[4] or 0) == 50.0]
    if not clean or not polluted:
        return None
    clean_wr = round(sum(1 for r in clean if r[2] == 'success') * 100 / len(clean), 1)
    polluted_wr = round(sum(1 for r in polluted if r[2] == 'success') * 100 / len(polluted), 1)
    return (clean_wr, polluted_wr, len(clean), len(polluted))


def _d_rsi_pollution_impact(db) -> tuple[float, float, int, int] | None:
    """D 项 RSI 数据污染影响（按 code+trade_date 去重，与 _fetch_dedup_live_formal 同口径）。

    RSI 污染（rsi_6=0 & rsi_12=50）是历史 stk_factor 缺失被 _f(None,0.0) 写入的产物，
    feature_builder 已加守卫（新数据不污染）。这些样本选股时用了错的 RSI，不反映策略
    真实能力，但不剔除主口径（避免系统性刷数字）。返回 (clean_wr, polluted_wr,
    clean_n, polluted_n) 供 check_d_item 打印影响差——胜率下滑时区分「策略退化」
    vs「数据污染扭曲」，防止误判循环加规则（第164轮过拟合护栏）。
    """
    rows = db.execute(text("""
        SELECT r.code, r.trade_date,
               MAX(o.outcome_label) AS label,
               MAX(JSON_EXTRACT(r.feature_snapshot_json, '$.rsi_6')) AS rsi6,
               MAX(JSON_EXTRACT(r.feature_snapshot_json, '$.rsi_12')) AS rsi12
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
        GROUP BY r.code, r.trade_date
    """)).fetchall()
    return _rsi_pollution_split(rows)


def _forward_verification_flag(full_wr: float | None, fwd_result: tuple | None,
                                baseline_wr: float) -> str | None:
    """D 项前瞻验证护栏（纯函数，便于单测锁定）。

    返回非空警示字符串当且仅当：全量胜率高于基线，**但**前瞻窗口（FORWARD_EPOCH 之后、
    修复后新 run 结算的样本）为空(None)——此时全量胜率可能含修复前历史污染，
    未经样本外验证，运营者不可据此 claim D 项改进（违反「D 项必须前瞻样本验证、不为刷数字而刷」护栏）。

    返回 None 的两种情况：
      - 前瞻窗口已有样本（fwd_result 非 None）→ 已可样本外验证，无需警示；
      - 全量胜率未高于基线 → 没有「虚高改进」可误读。
    """
    if full_wr is None:
        return None
    if fwd_result is not None:
        return None
    if full_wr > baseline_wr:
        ep_label = FORWARD_EPOCH or "规则上线后"
        return (f'⚠️ 护栏: 前瞻窗口(≥{ep_label})样本不足，全量胜率 {full_wr}% 高于基线 '
                f'{baseline_wr}% 但未经样本外验证——勿据此 claim D 项改进；'
                f'修复后新 run 结算后下轮复核')
    return None


def check_d_item(db) -> None:
    """D 项：核心指标（胜率 + 最大亏损），多窗口呈现以区分历史污染 vs 前瞻真退化。

    只统计真实 LIVE formal 推荐（recommendation_id 以 rec_ 开头）。
    回测（btrec_）与 experiment 补跑不纳入北极星 D 项——它们会虚高胜率、
    掩盖真实 formal 表现（回测污染曾使 D 项胜率从真实 59% 虚报到 68%）。
    """
    WIN_RATE_BASELINE = 61.5
    MAX_LOSS_BASELINE = -5.80

    # 胜率按 (code, trade_date) 去重：同一交易日同一只股票常被多个 formal run 重复推荐
    # （2026-07-08 单日 14 个 run，松发股份被推荐 7 次全部 -5.69% 止损）。去重后反映
    # 真实独立股票胜率，避免把失败股放大 N 倍扭曲胜率。
    rows = _fetch_dedup_live_formal(db)
    full_res = _compute_d_window(rows)
    _fmt_d('D 项(全量)', full_res, WIN_RATE_BASELINE, MAX_LOSS_BASELINE)
    _fmt_d_cost('D 项(全量)', _compute_d_cost(rows), WIN_RATE_BASELINE, PL_RATIO_BASELINE)

    # 近 10 交易日窗口：看近期趋势（自然老化修复前历史污染）。
    dates = sorted({str(r[1]) for r in rows})
    last10 = set(dates[-10:]) if dates else set()
    _fmt_d('D 项(近10交易日)', _compute_d_window(rows, trade_dates=last10),
           WIN_RATE_BASELINE, MAX_LOSS_BASELINE)
    _fmt_d_cost('D 项(近10交易日)', _compute_d_cost(rows, trade_dates=last10),
                WIN_RATE_BASELINE, PL_RATIO_BASELINE)
    # 单日 PF 拖累护栏：近10日 PF 低于基线时，诊断是否被单一交易日（情绪异常日）主导。
    # 是 → 明确「单日 regime 驱动，非策略退化」，禁止据此循环加 entry 规则（lessons #350）。
    _drag = _single_day_pl_drag(rows, last10)
    if _drag is not None:
        print(f'  ⚠️ 护栏: 近10日盈亏比被单日 {_drag["worst_day"]} 拖累——该日亏损占窗口 '
              f'{_drag["day_loss_share"]}%，剔除后 PF {_drag["pf_with"]}→{_drag["pf_without"]} '
              f'(回升{_drag["recover"]})。单日 regime 噪声非策略退化，禁止据此加 entry 规则')

    # 前瞻窗口（修复后）：FORWARD_EPOCH 之后的 run 才反映去重/弱市否决的真实胜率。
    # 若前瞻窗口胜率显著高于全量，说明全量下滑是历史污染而非真退化——
    # 切勿据此循环加规则（禁止过拟合，第164轮 T3 失败归因结论）。
    #
    # 时间错位护栏：FORWARD_EPOCH 动态跟踪最近一次策略规则 commit 后的首个交易日。
    # 若为 None（最近 commit 后尚无 formal run 结算），前瞻窗口空——报"样本不足"，
    # 绝不退回全量当前瞻（曾因此把 07-16 震荡 veto 上线前的 07-09 失败样本当前瞻，
    # 造出盈亏比 1.36 假性下滑，误导循环加规则）。
    if FORWARD_EPOCH is None:
        fwd_res = None
        fwd_cost = None
        print(f'  D 项(前瞻): 样本不足（最近规则上线后尚无 formal run 结算，待新交易日）')
    else:
        fwd_res = _compute_d_window(rows, since_date=FORWARD_EPOCH)
        fwd_cost = _compute_d_cost(rows, since_date=FORWARD_EPOCH)
        _fmt_d(f'D 项(前瞻≥{FORWARD_EPOCH})', fwd_res, WIN_RATE_BASELINE, MAX_LOSS_BASELINE)
        _fmt_d_cost(f'D 项(前瞻≥{FORWARD_EPOCH})', fwd_cost,
                    WIN_RATE_BASELINE, PL_RATIO_BASELINE)

    # 前瞻验证护栏：全量高于基线但前瞻窗口空 → 不可 claim 改进（见 _forward_verification_flag）
    flag = _forward_verification_flag(full_res[0] if full_res else None, fwd_res, WIN_RATE_BASELINE)
    if flag:
        print(f'  {flag}')

    # 板块拆分护栏（第169轮 T3 实证）：验证「最大亏损由 20cm 跳空驱动」假设。
    # 实证结论：20cm 是胜率最高分段（非风险源），maxloss 在两类板块均为止损地板(~-5.7%)。
    # 故后续轮次不得对 20cm 加风险闸门/降权——会砍掉最强分段、损害整体 D 项胜率。
    bd = _compute_d_board_breakdown(rows)
    _fmt_d_board('D 项(板块拆分)', bd)
    _20 = bd.get("20cm") or {}
    _10 = bd.get("10cm") or {}
    if _20 and _10 and _20.get("win_rate", 0) > _10.get("win_rate", 0):
        print(f'  ⚠️ 护栏: 20cm 胜率({_20.get("win_rate")}%)高于10cm({_10.get("win_rate")}%)'
              f'— 后续轮次禁止对 20cm 加风险闸门/降权（会损害 D 项）')

    # 数据质量影响诊断：RSI 污染样本是历史 stk_factor 缺失产物（feature_builder 已加守卫），
    # 不剔除主口径，但报告干净 vs 污染胜率差——胜率下滑时区分「策略退化」vs「数据污染扭曲」。
    rsi_impact = _d_rsi_pollution_impact(db)
    if rsi_impact:
        clean_wr, pol_wr, clean_n, pol_n = rsi_impact
        delta = round(clean_wr - pol_wr, 1)
        if delta > 3:
            print(f'  ℹ️ 数据质量影响：RSI 污染样本（rsi_6=0&rsi_12=50，历史 stk_factor 缺失）'
                  f'WR {pol_wr}% (n={pol_n}) vs 干净样本 WR {clean_wr}% (n={clean_n})，差 {delta}pp')
            print(f'     ——污染样本不剔除（避免刷数字），但胜率下滑时先排查是否数据污染扭曲'
                  f'而非策略退化（feature_builder 已加守卫，新数据不污染）')

    # 单日集中度诊断（独立股票口径）：防止单日异常高量扭曲 D 项判断。
    # 已改用独立股票数（同股同日多 run 只算 1 只），避免"单日 N 笔"假警报——
    # 2026-07-08 笔口径 126 笔占 71%，独立股票口径仅 36 只占 21%，不再是扭曲信号。
    total_n = db.execute(text("""
        SELECT COUNT(DISTINCT CONCAT(r.code,'_',r.trade_date))
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success','failed') AND r.recommendation_id LIKE 'rec_%'
    """)).fetchone()[0]
    top_day = db.execute(text("""
        SELECT r.trade_date AS d, COUNT(DISTINCT r.code) AS n
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success','failed') AND r.recommendation_id LIKE 'rec_%'
        GROUP BY d ORDER BY n DESC LIMIT 1
    """)).fetchone()
    if total_n and top_day:
        share = top_day[1] * 100 / total_n
        flag = (f'⚠️ 单日 {top_day[0]} 独立股票 {share:.0f}%（D 项可能被单日集中扭曲，'
                f'勿据此循环加规则）') if share >= 40 else '单日集中度正常'
        print(f'D 项单日集中度：最大单日 {top_day[0]} {top_day[1]} 只独立股票（{share:.0f}%）— {flag}')

    # 规则覆盖前瞻校验：区分"历史污染"vs"真退化"
    check_d_rule_coverage(db, WIN_RATE_BASELINE)


def check_d_rule_coverage(db, win_rate_baseline: float) -> None:
    """D 项规则覆盖前瞻校验：用当前 strategy.yaml 重跑所有 LIVE formal 失败样本，
    统计已被当前规则覆盖（会被 hard_veto / universe / prefilter 排除）的失败笔。

    目的：D 项胜率会被"规则加入前的历史失败样本"污染（规则晚于失败样本 commit），
    单看总胜率无法区分"历史污染"vs"真退化"，导致每轮误判持续恶化、循环加规则。
    本校验把失败样本拆成两段：
      - 已被当前规则覆盖：待前瞻验证（下一个交易日 formal run 会自动确认）
      - 未被覆盖：真残留毒区清单（下一轮 D 攻击的精确目标）

    注：用 feature_snapshot_json 重跑 stock，market_regime 在生产 run 时即写入快照
    （非事后补值），故 prefilter / phase 判断与生产一致。"已被覆盖"复刻生产
    evaluate_skill_prefilter（universe + prefilter.market.include/include_any/exclude）
    + run_strategy 的 hard_veto/排除 phase，覆盖口径与生产一致——能拦住的就是真拦住了。
    """
    skills = _load_skills_once()
    if not skills:
        return

    rows = db.execute(text("""
        SELECT r.recommendation_id, o.outcome_label, o.executed_return,
               r.feature_snapshot_json, r.skill_breakdown_json,
               o.exit_reason, o.max_return
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    if not rows:
        return

    blocked, new_total, wins, total = _classify_failed_coverage(rows, skills)
    new_wr = round(wins * 100 / new_total, 1) if new_total else 0.0
    delta = round(new_wr - win_rate_baseline, 1)
    tag = f'↑ 高于基线 {delta}pp' if delta >= 0 else f'⚠️ 真退化 {abs(delta)}pp'
    print(
        f'D 项规则覆盖：{blocked} 笔失败已被当前规则覆盖（待前瞻验证），'
        f'反事实胜率 {new_wr}% （n={new_total}，基线 {win_rate_baseline}%，{tag}）'
    )

    # 真残留毒区：未被当前任何规则覆盖的失败笔——下一轮 D 攻击的精确目标。
    # 旧实现只打印 blocked 而丢弃这段，导致每轮 D 攻击靠猜 targets（见函数 docstring）。
    uncovered = _collect_uncovered_failures(rows, skills)
    if uncovered:
        # 成功样本对照：供「失败 vs 成功特征可分性」护栏判定个股 entry 侧是否可解。
        success_items = [
            {'fs': json.loads(r[3]) if r[3] else {}}
            for r in rows if r[1] == 'success'
        ]
        _print_residual_poison_zone(uncovered, success_items=success_items)

    # 前瞻窗口规则覆盖反事实：剔除被当前 veto 覆盖的失败笔后重算胜率+盈亏比。
    # 解决"前瞻盈亏比低→误判需加规则"的循环：前瞻样本含 veto commit 前的 run（veto 还没生效），
    # 直接看前瞻盈亏比会误判"veto 没用"。本段剔除被覆盖失败笔，反映 veto 后的真实预期。
    _print_forward_veto_counterfactual(rows, skills, win_rate_baseline)


def _classify_failed_coverage(rows, skills) -> tuple[int, int, int, int]:
    """统计失败样本中被当前规则覆盖（hard_veto / universe / prefilter 排除）的笔数。

    纯函数（不碰 DB），便于单元测试。返回 (blocked_count, new_total, wins, total)：
      - blocked_count: 失败样本中会被当前 yaml 拦截的去重 recommendation_id 数
      - new_total: total - blocked_count（剔除被覆盖失败笔后的样本数）
      - wins: 成功笔数（不变）
      - total: 样本总数

    覆盖口径复刻生产管线，两路并查任一命中即算覆盖：
      1. evaluate_skill_prefilter(stage='market')：universe + prefilter.market.include
         /include_any/exclude（生产 prefilter 全量，含 run_strategy._passes_universe 漏掉
         的 prefilter.market.include/include_any）。
      2. run_strategy：phases hard_veto（如弱市隔夜毒区）+ 排除 phase + setup hard_veto
         + risk rules。这些在 prefilter 之后、scoring 阶段才判定，evaluate_skill_prefilter 不查。
    两路并查确保与生产一致——只查 universe+prefilter.market.exclude（旧实现）会漏掉
    prefilter.market.include 覆盖（如 chip_loose），低估覆盖率、高估残留毒区。
    """
    from app.engine.strategy_runner import run_strategy, evaluate_skill_prefilter

    total = len(rows)
    wins = sum(1 for r in rows if r[1] == 'success')

    blocked_rids: set = set()
    for row in rows:
        rid, label, _ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        for s in sb:
            sid = s.get('strategy_id')
            skill = skills.get(sid)
            if not skill:
                continue
            try:
                # 路 1：生产 prefilter（universe + prefilter.market.include/include_any/exclude）
                prefilter_pass, _reason = evaluate_skill_prefilter(skill, dict(fs), stage='market')
                # 路 2：scoring 阶段 hard_veto / 排除 phase（prefilter 之后才判定）
                sig = run_strategy(skill, dict(fs), [], skip_universe_check=False)
                if (not prefilter_pass) or sig.get('hard_veto') or (sig.get('phase') == '排除'):
                    blocked_rids.add(rid)
                    break
            except Exception:
                continue

    blocked = len(blocked_rids)
    return blocked, total - blocked, wins, total


# --------------------------------------------------------------------------- #
# 真残留毒区采集 + 归因（D 项规则覆盖校验第二段，旧实现遗漏）
# --------------------------------------------------------------------------- #
def _collect_uncovered_failures(rows, skills) -> list[dict]:
    """采集失败样本中被当前规则「未」覆盖（不被 hard_veto / universe / prefilter 排除）的笔。

    与 _classify_failed_coverage 同源、同覆盖口径（两路并查），只是收集未覆盖项。
    返回 list[dict]，每项含 rid / feature_snapshot(dict) / exit_reason / max_return /
    executed_return，供 _print_residual_poison_zone 做模式归因。纯函数，便于单测。
    """
    from app.engine.strategy_runner import run_strategy, evaluate_skill_prefilter

    out: list[dict] = []
    for row in rows:
        rid, label, ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        exit_reason = row[5] if len(row) > 5 else None
        max_return = row[6] if len(row) > 6 else None
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        covered = False
        for s in sb:
            sid = s.get('strategy_id')
            skill = skills.get(sid)
            if not skill:
                continue
            try:
                prefilter_pass, _reason = evaluate_skill_prefilter(skill, dict(fs), stage='market')
                sig = run_strategy(skill, dict(fs), [], skip_universe_check=False)
                if (not prefilter_pass) or sig.get('hard_veto') or (sig.get('phase') == '排除'):
                    covered = True
                    break
            except Exception:
                continue
        if not covered:
            out.append({
                'rid': rid,
                'fs': fs,
                'exit_reason': exit_reason,
                'max_return': max_return,
                'executed_return': ret,
            })
    return out


def _classify_failure_mode_inline(
    *, exit_reason, executed_return, max_return, rsi_12, chg20, pos60, setup_type
) -> str:
    """内联镜像 failure_attribution.classify_failure_mode（纯函数、避免跨包导入脆弱性）。

    逻辑与 backend/scripts/failure_attribution.py 保持一致；若未来该模块可稳定导入，
    此处应改回调用其权威实现，避免漂移。
    """
    WEAK_SETUPS = {"分歧回封接力", "放量突破", "阶段高点蓄势观察"}
    if exit_reason == "stop_loss_gap":
        return "tail_gap"
    if (
        exit_reason in ("trailing_stop_hit", "stop_loss_hit")
        and (max_return is not None and max_return > 3.0)
        and (executed_return is not None and executed_return < 0.0)
    ):
        return "gap_roundtrip"
    if setup_type in WEAK_SETUPS:
        return "bad_setup"
    if rsi_12 is not None and rsi_12 < 45:
        return "weak_stock"
    if (
        max_return is not None
        and executed_return is not None
        and max_return <= 0.5
        and executed_return < 0.0
    ):
        return "no_follow_through"
    if (chg20 is not None and chg20 > 50) or (pos60 is not None and pos60 > 90):
        return "extension_chase"
    return "other"


def _residual_regime_sentiment_breakdown(
    uncovered: list[dict],
) -> list[tuple[tuple[str, str], dict]]:
    """真残留毒区按 (market_regime, market_sentiment_phase) 交叉分布。

    feature_snapshot 存 run 时由 aggregator _market_base/_market_sentiment 算出的
    market_regime + market_sentiment_phase。_market_sentiment 仅用 limit_up_rate 单维度
    判活跃，会把"涨停集中+大面积下跌"的冰点日误判为活跃，使弱势·活跃 score_line +3
    放宽本该过滤的低质量票。本函数把该误判在毒区里的占比显式化，供前瞻样本积累后
    修复 _market_sentiment（加 up_ratio/limit_down 双维度）作精确依据。

    纯函数（仅读 uncovered），返回按 count 降序的 [(base, sent), {count, avg_return}]。
    """
    counts: Counter = Counter()
    rets_by_key: dict[tuple[str, str], list[float]] = {}
    for u in uncovered:
        fs = u.get('fs') or {}
        base = str(fs.get('market_regime') or '?')
        sent = str(fs.get('market_sentiment_phase') or '?')
        key = (base, sent)
        counts[key] += 1
        ret = u.get('executed_return')
        if ret is not None:
            rets_by_key.setdefault(key, []).append(float(ret))
    out: list[tuple[tuple[str, str], dict[str, Any]]] = []
    for key, c in counts.most_common():
        rets = rets_by_key.get(key, [])
        avg = round(sum(rets) / len(rets), 2) if rets else 0.0
        out.append((key, {"count": c, "avg_return": avg}))
    return out


def _entry_separability_falsified(fail_items: list[dict], success_items: list[dict],
                                  feature_keys: tuple = ("vol5_20", "pos60"),
                                  tol: float = 0.20) -> dict | None:
    """纯函数：判定某毒区模式在「干净个股特征」上失败 vs 成功是否中位数全重叠。

    背景（第192轮 T3 实证）：no_follow_through low_vol 失败笔与同窗口成功笔在
    vol5_20(1.18 vs 1.38)、pos60(77.3 vs 78.3) 中位数几乎完全重叠（rel_gap 0.14/0.01）。
    这两个是最干净的维度——量能(vol5_20)与位置(pos60)误伤率低；而 turnover/chg20 虽有
    中等 gap(0.44/0.36)但 lessons #350 已证伪其阈值误伤 40%+（误删最佳股票），不可用。
    干净维度全重叠 + 中等维度误伤过高 → 无高特异性 entry 规则可加，失败由 regime 驱动
    而非个股特征，entry 侧不可解。

    判定：对 feature_keys（默认仅 vol5_20/pos60 两干净维度）算失败/成功中位数相对差异，
    全部 < tol（默认 20%）视为「干净特征全重叠、无干净可分维度」，返回 {feature_gap}；
    任一 >= tol 则返回 None。fail/success_items 需含 fs。样本不足返回 None。
    """
    import statistics

    def _med(items, key):
        vals = [float((it.get('fs') or {}).get(key)) for it in items
                if (it.get('fs') or {}).get(key) is not None]
        return statistics.median(vals) if vals else None

    if len(fail_items) < 3 or len(success_items) < 5:
        return None
    gap: dict[str, float] = {}
    for k in feature_keys:
        fm = _med(fail_items, k)
        sm = _med(success_items, k)
        if fm is None or sm is None:
            continue
        denom = abs(sm) if abs(sm) > 1e-6 else 1.0
        gap[k] = round(abs(fm - sm) / denom, 2)
    if gap and all(g < tol for g in gap.values()):
        return {"feature_gap": gap}
    return None


def _print_residual_poison_zone(uncovered: list[dict], success_items: list[dict] | None = None) -> None:
    """打印真残留毒区：未覆盖失败笔数 + 模式分布 + 各模式平均净收益 + 集中度提示。

    目的：把"下一轮 D 攻击的精确目标"显式化，避免每轮靠猜（历史污染已用 blocked 段处理，
    本段专盯真退化毒区）。模式映射复用 failure_attribution.classify_failure_mode 口径。
    """
    # 尝试用权威实现；失败（跨包导入脆弱）则回退内联镜像。
    classify = None
    try:
        import importlib.util
        import sys as _sys
        fa_path = os.path.join(BACKEND_DIR, 'scripts', 'failure_attribution.py')
        if os.path.exists(fa_path):
            spec = importlib.util.spec_from_file_location('failure_attribution_lazy', fa_path)
            mod = importlib.util.module_from_spec(spec)
            # 必须先注册到 sys.modules 再 exec_module：failure_attribution 用 dataclass，
            # dataclass._is_type 通过 sys.modules[cls.__module__] 解析类型注解；
            # 未注册时该查找返回 None → 'NoneType' has no '__dict__' → 整个模块加载失败，
            # 静默回退内联镜像（与权威实现漂移，归因口径不可信）。
            _sys.modules['failure_attribution_lazy'] = mod
            spec.loader.exec_module(mod)
            classify = mod.classify_failure_mode
    except Exception:
        classify = None

    modes: Counter = Counter()
    mode_rets: dict[str, list[float]] = {}
    mode_dates: dict[str, list[str]] = {}
    by_date: Counter = Counter()
    for u in uncovered:
        fs = u['fs']
        if classify is not None:
            mode = classify(
                exit_reason=u.get('exit_reason') or '',
                executed_return=u.get('executed_return'),
                max_return=u.get('max_return'),
                rsi_12=fs.get('rsi_12'),
                chg20=fs.get('chg20'),
                pos60=fs.get('pos60'),
                setup_type=fs.get('setup_type'),
            )
        else:
            mode = _classify_failure_mode_inline(
                exit_reason=u.get('exit_reason') or '',
                executed_return=u.get('executed_return'),
                max_return=u.get('max_return'),
                rsi_12=fs.get('rsi_12'),
                chg20=fs.get('chg20'),
                pos60=fs.get('pos60'),
                setup_type=fs.get('setup_type'),
            )
        modes[mode] += 1
        ret = u.get('executed_return')
        if ret is not None:
            mode_rets.setdefault(mode, []).append(float(ret))
        rid = u.get('rid', '')
        # rid 形如 rec_20260707_003_xxxx → 提取交易日
        if '_' in rid:
            _d = rid.split('_')[1]
            by_date[_d] += 1
            mode_dates.setdefault(mode, []).append(_d)

    print(f'D 项真残留毒区（未被任何规则覆盖）：{len(uncovered)} 笔（下一轮 D 攻击精确目标）')
    for m, c in modes.most_common():
        rets = mode_rets.get(m, [])
        avg = round(sum(rets) / len(rets), 2) if rets else 0.0
        print(f'  - {m}: {c} 笔（均净 {avg}%）')
    if by_date:
        top_date, top_n = by_date.most_common(1)[0]
        if top_n >= 3:
            print(f'  ⚠️ 单日集中：{top_date} {top_n} 笔（疑似单日 regime 风险，非个股因子可解）')

    # 单日集中度护栏：逐模式判定是否被单一交易日主导。单日（尤其情绪异常日）集中驱动的
    # 毒区，个股 entry 侧不可解，任何据此加的 entry 规则都是对一个交易日噪声的过拟合
    # （lessons.md 第187轮：no_follow_through 18笔被多角度证伪，活跃档 up_ratio∈[0.50,0.65)
    # 28笔全部来自 07-16 单日，剔除后无其它日期样本）。把判定显式化，防每轮重复加规则。
    try:
        import importlib.util as _ilu
        _gpath = os.path.join(os.path.dirname(__file__), 'poison_zone_day_guard.py')
        if os.path.exists(_gpath):
            _gspec = _ilu.spec_from_file_location('poison_zone_day_guard_lazy', _gpath)
            _gmod = _ilu.module_from_spec(_gspec)
            _gspec.loader.exec_module(_gmod)
            _guard_lines = []
            for m, c in modes.most_common():
                if c < 3:
                    continue
                _r = _gmod.single_day_concentration(mode_dates.get(m, []))
                _verdict = '🚫禁止entry规则' if _r['single_day_driven'] else '✅可验证'
                # 失败 vs 成功特征可分性复检：模式级跨多日（✅可验证）仍可能「失败与成功
                # 在关键个股特征上中位数全重叠」——无可分阈值，失败由 regime 驱动而非个股，
                # entry 侧不可解。把 ✅ 降级为 🚫（no_follow_through 第192轮证伪先例）。
                # 排除 high_vol(vol5_20>=3)子簇：它已被下方子簇级单日集中护栏单独禁，混进来
                # 会偶然抬高 vol5_20 失败中位数、误判「可分」。只判 low_vol（模式级 ✅ 真子集）。
                if not _r['single_day_driven'] and success_items:
                    _mode_items = [u for u in uncovered
                                   if _classify_failure_mode_inline(
                                       exit_reason=u.get('exit_reason') or '',
                                       executed_return=u.get('executed_return'),
                                       max_return=u.get('max_return'),
                                       rsi_12=(u.get('fs') or {}).get('rsi_12'),
                                       chg20=(u.get('fs') or {}).get('chg20'),
                                       pos60=(u.get('fs') or {}).get('pos60'),
                                       setup_type=(u.get('fs') or {}).get('setup_type'),
                                   ) == m
                                   and ((u.get('fs') or {}).get('vol5_20') is None
                                        or float((u.get('fs') or {}).get('vol5_20')) < 3.0)]
                    _sep = _entry_separability_falsified(_mode_items, success_items)
                    if _sep is not None:
                        _verdict = '🚫个股特征与成功样本中位数全重叠(regime驱动非个股,entry侧不可解)'
                _guard_lines.append(
                    f'    - {m}: 独立日={_r["independent_days"]} top={_r["top_day"]}'
                    f'({_r["top_day_ratio"]:.0%}) → {_verdict}'
                )
            if _guard_lines:
                print('  毒区单日集中度护栏（单日主导→禁止据其加 entry 规则）:')
                for _ln in _guard_lines:
                    print(_ln)

            # 子簇级护栏：模式级绿灯（跨多日）仍可能掩盖「特征子簇集中单日」。
            # no_follow_through 18笔模式级 top=07-16(44%)→✅，但 vol5_20>=3 子簇 8/9
            # 集中 07-16（89%）。对最大模式按 vol5_20 高/低分子簇检测隐藏单日集中。
            if modes:
                _top_mode = modes.most_common(1)[0][0]
                _sub_items = []
                for u in uncovered:
                    _fs = u['fs']
                    _m = _classify_failure_mode_inline(
                        exit_reason=u.get('exit_reason') or '',
                        executed_return=u.get('executed_return'),
                        max_return=u.get('max_return'),
                        rsi_12=_fs.get('rsi_12'), chg20=_fs.get('chg20'),
                        pos60=_fs.get('pos60'), setup_type=_fs.get('setup_type'),
                    )
                    if _m != _top_mode or '_' not in u.get('rid', ''):
                        continue
                    _v = _fs.get('vol5_20')
                    _cl = 'high_vol(>=3)' if (_v is not None and _v >= 3) else 'low_vol(<3)'
                    _sub_items.append((_cl, u['rid'].split('_')[1]))
                _worst = _gmod.subcluster_day_concentration(_sub_items)
                if _worst:
                    print(f'  ⚠️ 子簇级单日集中：{_top_mode} 的 {_worst["cluster_key"]} 子簇 '
                          f'{_worst["n"]}笔中 {_worst["top_day_ratio"]:.0%} 集中 {_worst["top_day"]}'
                          f' — 模式级绿灯掩盖子簇单日风险，勿据该子簇加 entry 规则（过拟合单日）')
    except Exception:
        pass

    # base x sentiment 交叉分布：定位"弱势·活跃是真毒区"类 regime 误判根因。
    # feature_snapshot 存 market_regime（base）+ market_sentiment_phase（sentiment），
    # 由 aggregator _market_base/_market_sentiment 在 run 时算出并写入。
    # _market_sentiment 仅用 limit_up_rate 单维度判活跃，会把"涨停集中+大面积下跌"
    # 的冰点日（如 2026-07-07 up_ratio=12.4% lu_rate=0.9%）误判为活跃，使弱势·活跃
    # score_line +3 放宽本该过滤的低质量票。此段把该误判在毒区里的占比显式化，
    # 供前瞻样本积累后修复 _market_sentiment（加 up_ratio/limit_down 双维度）作精确依据。
    regime_sent_breakdown = _residual_regime_sentiment_breakdown(uncovered)
    if regime_sent_breakdown:
        print('  毒区 × (regime, sentiment) 分布（定位 regime 误判根因）:')
        for (base, sent), entry in regime_sent_breakdown:
            print(f'    - {base}·{sent}: {entry["count"]} 笔（均净 {entry["avg_return"]}%）')

    # 数据质量标记：RSI=0 是上游数据污染（_f(None,0.0) 写库 + rsi_6 缺守卫），
    # 不是策略可解毒区。统计残留毒区里 RSI 污染笔占比，提示勿当策略毒区循环加规则。
    rsi_polluted = [u for u in uncovered
                    if u['fs'].get('rsi_6') == 0.0 and u['fs'].get('rsi_12') == 50.0]
    if rsi_polluted:
        print(f'  ℹ️ 其中 {len(rsi_polluted)} 笔为 RSI 数据污染（rsi_6=0 & rsi_12=50，'
              f'上游 stk_factor 缺失被 _f 转 0）— 数据质量问题，非策略可解，'
              f'feature_builder 已加 rsi_6/rsi_24 守卫清洗，勿当毒区加规则')

    # 元护栏（第 7 轮）：point-biserial 单特征挖毒区在 n≈200 样本上全是伪影——头部候选
    # 多为「稀疏特征 × 单日 regime」乘积（holder_trade / dragon_tiger_nb / roe 三连证伪）。
    # 未来轮次挖到「高预测力特征」时，先用 feature_signal_guard.evaluate_feature_signal
    # 判其正例时间分布，trustworthy=False（小样本/单日集中/跨日过窄）即禁止据此加规则。
    print('  ℹ️ 特征信号护栏：point-biserial 单特征候选须先经 '
          'feature_signal_guard.evaluate_feature_signal 判正例时间分布，'
          '小样本(<12)/单日集中(≥40%)/跨日<3 一律禁止据此加规则（第7轮 holder_trade/'
          'dragon_tiger_nb/roe 三连伪影实证）')


def _print_forward_veto_counterfactual(rows, skills, win_rate_baseline: float) -> None:
    """前瞻窗口内，剔除被当前 veto 覆盖的失败笔后重算胜率 + 净盈亏比。

    解决"D 项前瞻盈亏比低→每轮误判需加规则"的循环：前瞻窗口(FORWARD_EPOCH 之后)的
    formal run 含 veto commit 前生成的样本（veto 当时还没生效，失败笔已结算入库）。
    直接看前瞻盈亏比会误判"veto 没用 / 还需加规则"。本段用当前 strategy.yaml 重跑前瞻
    失败笔，剔除会被 veto 拦截的，重算净胜率 + 净盈亏比（扣 0.07%/笔），反映 veto 后
    的真实前瞻预期。覆盖口径与 _classify_failed_coverage 一致（hard_veto / 排除 phase /
    prefilter）。

    纯函数（仅读 rows/skills），不碰 DB，便于单测。
    """
    from app.engine.strategy_runner import run_strategy, evaluate_skill_prefilter

    if not FORWARD_EPOCH:
        return

    # 前瞻窗口子集（去重 by rid，与 _classify_failed_coverage 同口径）
    fwd = {row[0]: row for row in rows if row[0].startswith('rec_')}
    fwd_rows = [r for r in fwd.values() if _rid_trade_date(r[0]) >= FORWARD_EPOCH]
    if not fwd_rows:
        return

    blocked_rids: set = set()
    for row in fwd_rows:
        rid, label, _ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        for s in sb:
            sid = s.get('strategy_id')
            skill = skills.get(sid)
            if not skill:
                continue
            try:
                prefilter_pass, _ = evaluate_skill_prefilter(skill, dict(fs), stage='market')
                sig = run_strategy(skill, dict(fs), [], skip_universe_check=False)
                if (not prefilter_pass) or sig.get('hard_veto') or (sig.get('phase') == '排除'):
                    blocked_rids.add(rid)
                    break
            except Exception:
                continue

    # 剔除被覆盖失败笔后的样本（扣 0.07%/笔净成本）
    kept = [r for r in fwd_rows if not (r[1] == 'failed' and r[0] in blocked_rids)]
    n = len(kept)
    if n < 3:  # 样本太少不打印（噪声大，单测用 3+ 笔）
        return
    wins = [float(r[2]) - 0.07 for r in kept if r[1] == 'success']
    losses = [float(r[2]) - 0.07 for r in kept if r[1] == 'failed']
    wr = round(len(wins) * 100 / n, 1)
    win_avg = sum(wins) / len(wins) if wins else 0.0
    loss_avg = abs(sum(losses) / len(losses)) if losses else 0.0
    pl = round(win_avg / loss_avg, 2) if loss_avg else 0.0
    avg = round((sum(wins) + sum(losses)) / n, 2)
    blocked_n = len(blocked_rids)
    pl_tag = '✅' if pl >= PL_RATIO_TARGET else ('⚠️' if pl < PL_RATIO_BASELINE else '')
    print(
        f'D 项前瞻 veto 反事实：前瞻窗口剔除 {blocked_n} 笔被覆盖失败后 '
        f'WR={wr}% (n={n}) 净盈亏比={pl} {pl_tag} 平均收益={avg}% '
        f'（对照前瞻未剔除：见上方"D 项(前瞻)"行；本行反映 veto 后真实预期）'
    )


def _rid_trade_date(rid: str) -> str:
    """rec_20260709_001_xxxx → '2026-07-09'（与 FORWARD_EPOCH 同格式，可直接字符串比较）。
    无法解析返回空串（排序时排到最前）。"""
    parts = rid.split('_')
    if len(parts) < 2 or len(parts[1]) != 8:
        return ''
    d = parts[1]
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}'


def main() -> None:
    global FORWARD_EPOCH
    db = SessionLocal()
    try:
        FORWARD_EPOCH = _resolve_forward_epoch(db)
        check_a_item(db)
        check_b_item(db)
        hit_skills = check_c_item(db)
        check_e_item(db, hit_skills)
        check_d_item(db)
    finally:
        db.close()


if __name__ == '__main__':
    main()
