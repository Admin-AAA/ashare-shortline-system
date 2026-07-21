#!/usr/bin/env python3
"""self-improve 每轮自检脚本：输出 A/B/C/D/E 五项北极星指标摘要。

用法：
    cd backend && source .venv/bin/activate && python3 ../scripts/self_check.py
"""

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 让 backend/ 下的 app 包可被导入
BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from app.services.feature_builder import market_regime_score
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
        # 归因分类：裸报 N 个 interrupted 会误导未来轮以为 pipeline 不稳，实际近 7 天
        # 绝大多数是「服务重启/进程退出」（self-improve 改后端重启打断的历史 run）+
        # self-improve 验证性中断 + 超时/僵尸清理——全是已知残留非进行中故障。
        # 只把「无已知归因」的当可疑（真 pipeline 异常需 T12 排查）。
        msg_rows = db.execute(text("""
            SELECT COALESCE(error_message, progress_message, '') AS msg
            FROM recommendation_runs
            WHERE run_mode='formal' AND status='interrupted'
              AND started_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
        """)).fetchall()
        benign = 0
        suspicious: list[str] = []
        for (msg,) in msg_rows:
            m = str(msg or "")
            if ("服务重启" in m or "进程退出" in m or "self-improve" in m
                    or "T3.6验证" in m or "僵尸 run" in m or "心跳过期" in m
                    or "超时" in m or "skip_aux_refresh" in m or "dispatcher" in m):
                benign += 1
            else:
                suspicious.append(m[:50])
        print(f'  ℹ️ {len(interrupted)} 个 interrupted run（已知残留 {benign}：服务重启/验证/超时，非进行中故障）')
        if suspicious:
            print(f'  ⚠️ {len(suspicious)} 个 interrupted run 无已知归因（疑真 pipeline 异常，T12 排查）: {suspicious[:5]}')


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
    ②score_delta 超单 agent clamp[-18,16]——区分前瞻新增（真回归）vs 历史存量（已修复污染）。

    参数 records: [{delta: float|None, stance: str|None, label: 'success'|'failed',
                    trade_date: str|None(可选,用于超clamp历史/前瞻分口径)}]
    返回 {monotonic_ok, bucket_wr, oppose_ratio, over_clamp_count, stance_delta_conflict, notes:[...]}
    """
    deltas = [(r["delta"], r["label"]) for r in records if r.get("delta") is not None]
    n_with = len(deltas)
    oppose = sum(1 for r in records if str(r.get("stance") or "").lower() == "oppose")
    oppose_ratio = oppose / len(records) if records else 0.0
    over_clamp = sum(1 for r in records if r.get("delta") is not None and (r["delta"] > 16 or r["delta"] < -18))
    # 超 clamp 分口径（第 11 轮实证）：root cause 是历史污染非「多 agent 求和」。
    # agent_skill_runner.py:300 `score_delta=clamp(raw_score-50, -18, 16)` 已生效，
    # 2026-07-13 起新产生数据 0 超界（前瞻验证过）；超界全是 07-10 及更早旧代码写入的
    # 存量。故按是否含日期拆分：前瞻（近期）仍新增超界 = 真回归需修；仅历史存量 = 已修复
    # 勿动历史数据（无前瞻验证手段，直接 clamp 历史会污染 outcome 对齐）。
    _RECENT = "2026-07-13"  # clamp 修复后首个 0 超界交易日（按日验证过）
    over_clamp_recent = sum(
        1 for r in records
        if r.get("delta") is not None and (r["delta"] > 16 or r["delta"] < -18)
        and r.get("trade_date") and str(r["trade_date"]) >= _RECENT
    )
    # stance↔delta 矛盾：LLM 判 oppose/veto（反对）却仍给正 delta（推分过线）。
    # 根因（第 9 轮实证 rec_20260707_005_dc16）：score_delta=clamp(raw_score-50) 只看 score
    # 不看 stance，LLM 内部不一致时（stance=oppose 但 score=60）→ delta=+10。
    # 修复（2026-07-21 a87924f T5）：agent_skill_runner 对 stance=oppose 且 delta>0 钳到 0；
    # veto 仍走 hard_veto 拦截。历史 DB 中的 agent_summary 不重写（无前瞻验证手段）。
    # 分口径同超 clamp：近期仍新增 = 🚨回归；仅历史存量 = 已修复勿再改评分语义。
    _OPPOSE_FIX = "2026-07-21"  # a87924f oppose 不再加分 上线日
    stance_delta_conflict = sum(
        1 for r in records
        if str(r.get("stance") or "").lower() in ("oppose", "veto")
        and r.get("delta") is not None and r["delta"] > 0
    )
    stance_delta_conflict_recent = sum(
        1 for r in records
        if str(r.get("stance") or "").lower() in ("oppose", "veto")
        and r.get("delta") is not None and r["delta"] > 0
        and r.get("trade_date") and str(r["trade_date"]) >= _OPPOSE_FIX
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
    if stance_delta_conflict_recent > 0:
        notes.append(
            f"🚨 {stance_delta_conflict_recent} 条近期(≥{_OPPOSE_FIX}) stance=oppose/veto 仍给正 delta："
            f"a87924f oppose 不再加分 后出现回归，查 agent_skill_runner enrich/agent_only 路径是否绕过"
        )
    elif stance_delta_conflict > 0:
        notes.append(
            f"{stance_delta_conflict} 条 stance=oppose/veto 却仍给正 delta 全为 {_OPPOSE_FIX} 前历史存量"
            f"（旧 score_delta=clamp(score-50) 只看 score 不看 stance；a87924f 已修 oppose 加分钳 0，"
            f"前瞻 0 新增）。属已修复的历史污染——勿动历史 agent_summary，勿再改评分语义"
        )
    if over_clamp_recent > 0:
        notes.append(
            f"🚨 {over_clamp_recent} 条近期(≥{_RECENT}) score_delta 超 clamp[-18,16]："
            f"clamp 修复后出现回归，查 agent_skill_runner.py:300 / 聚合路径是否绕过 clamp"
        )
    elif over_clamp > 0:
        notes.append(
            f"{over_clamp} 条 score_delta 超 clamp[-18,16] 全为 07-13 前历史存量（旧代码写入，"
            f"line:300 clamp 已修复，前瞻 0 新增）。属已修复的历史污染——勿动历史数据"
            f"（无前瞻验证手段），勿误判「多 agent 求和未 clamp」去改聚合"
        )
    if not monotonic_ok:
        notes.append("delta 分档胜率非单调（大 delta 反而低胜率），LLM 校准失效，T9 需复核")
    return {
        "monotonic_ok": monotonic_ok,
        "bucket_wr": buckets,
        "oppose_ratio": round(oppose_ratio, 4),
        "over_clamp_count": over_clamp,
        "over_clamp_recent": over_clamp_recent,
        "stance_delta_conflict": stance_delta_conflict,
        "stance_delta_conflict_recent": stance_delta_conflict_recent,
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
                   o.outcome_label,
                   u.trade_date
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
        {"delta": _f(r[0]), "stance": _stance(r[1]), "label": r[2], "trade_date": str(r[3]) if r[3] else None}
        for r in rows
    ]
    if not records:
        return
    result = _llm_delta_effectiveness(records)
    b = result["bucket_wr"]
    print(
        f'  LLM delta 有效性（去混杂·按档胜率）: 0-15={b["0-15"]}% 15-30={b["15-30"]}% '
        f'30+={b["30+"]}% | oppose率={result["oppose_ratio"]:.0%} '
        f'超clamp={result["over_clamp_count"]}条(近期{result["over_clamp_recent"]}) '
        f'stance↔delta矛盾={result["stance_delta_conflict"]}条'
        f'(近期{result.get("stance_delta_conflict_recent", 0)})'
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


def _classify_coverage_blind(
    blind: list[str],
    formal_preset_skills: set[str],
) -> dict[str, list[str]]:
    """覆盖盲区二分类：静默死亡（未进 formal preset）vs 预期稀疏（已注册但仍 0 命中）。

    背景（2026-07-21 T3.6）：E 项原把所有 formal 0 命中一律当盲区，会把
    weak-market-strong-stock-reversal 这类「已在 preset_formal_opening、补跑也跑过、
    只是当前 regime/universe 无票」的策略和「enabled 但漏注册 preset 静默死亡」
    混在一起，误导下一轮去放宽 universe（违反准确>可见）。

    - silent_death：不在 formal preset → formal 永远不评估，真故障（🚨）
    - expected_sparse：已在 formal preset → 管线已覆盖，0 命中是条件稀疏（ℹ️）
    formal_preset_skills 为空时无法判断，两端都空（调用方应跳过细分类）。
    """
    if not formal_preset_skills:
        return {"silent_death": [], "expected_sparse": []}
    silent_death = sorted(s for s in blind if s not in formal_preset_skills)
    expected_sparse = sorted(s for s in blind if s in formal_preset_skills)
    return {"silent_death": silent_death, "expected_sparse": expected_sparse}


def _classify_zero_exposure(
    zero_exp: list[str],
    experiment_completed_sids: set[str],
) -> dict[str, list[str]]:
    """0 曝光二分类：补跑已覆盖·0 产出 vs 真未入队。

    背景（2026-07-21 T3.6）：weak-market 近 30 天 0 曝光，但 07-21 00:03 曝光补跑
    已 completed（skill_gate 24 票全「不在策略股票池」/ hard_veto → recommended_count=0）。
    原话术「补跑未覆盖」是误报——补跑覆盖的是「是否入队评估」，不是「是否产出推荐」。
    0 产出是 universe/regime 正确拦截，不能为喂样本放宽门槛。

    - covered_zero：近 7 天有该 skill 的 completed experiment run（ℹ️）
    - uncovered：连 experiment run 都没有（⚠️ 真覆盖缺口）
    """
    covered_zero = sorted(s for s in zero_exp if s in experiment_completed_sids)
    uncovered = sorted(s for s in zero_exp if s not in experiment_completed_sids)
    return {"covered_zero": covered_zero, "uncovered": uncovered}


def _classify_exposure_feed_health(
    under_floor: list[str],
    *,
    last_rec: dict[str, tuple],
    last_exp: dict[str, tuple],
) -> dict[str, list[str]]:
    """曝光补跑健康三分法（纯函数，便于单测）。

    last_rec[sid] = (recommended_at, n_recs) 近窗口 skill_breakdown 有推荐
    last_exp[sid] = (started_at, recommended_count) 近窗口最新 completed experiment
      （selected_skills 命中该 sid；recommended_count 为该 run 产出）

    分类（T25）：
    - feeding：最新 completed experiment 产出 >0
    - gate_blocked：最新 completed experiment 产出 0（含：有更旧推荐但仍被更新 0 产出覆盖）
    - stalled：近窗口无 completed experiment

    旧逻辑只看「7 天内是否有推荐」会把 07-16 1 笔旧推荐 + 07-21 三次 0 产出
    误标「补跑在喂」，掩盖门线/regime 拦截。
    """
    feeding: list[str] = []
    gate_blocked: list[str] = []
    stalled: list[str] = []
    for sid in under_floor:
        rec = last_rec.get(sid)
        exp = last_exp.get(sid)
        if exp is None:
            stalled.append(sid)
            continue
        exp_ts, exp_rec_n = exp
        if int(exp_rec_n or 0) <= 0:
            gate_blocked.append(sid)
            continue
        if rec and int(rec[1] or 0) > 0:
            feeding.append(f"{sid}({int(rec[1])}笔@{str(rec[0])[:10]})")
        else:
            feeding.append(f"{sid}(exp@{str(exp_ts)[:10]},rec={int(exp_rec_n)})")
    return {
        "feeding": feeding,
        "gate_blocked": gate_blocked,
        "stalled": stalled,
    }


def _format_e_coverage_tag(
    n: int,
    win_rate: float,
    *,
    sample_floor: int = 20,
    low_wr_threshold: float = 45.0,
) -> str:
    """E 项列表标签：低胜率 vs 低覆盖（纯函数，便于单测）。

    技能文档 / T3.6：低胜率标记须 n≥20 且 winrate<45%；样本不足时只能标低覆盖
    （先补曝光再判）。旧逻辑 `win_rate < 45 → 低胜率` 把 small-cap n=1 WR=0%
    误标「低胜率需标记」，诱导对噪声样本做失败归因/降权。
    """
    if n < sample_floor:
        return "低覆盖"
    if win_rate < low_wr_threshold:
        return "低胜率"
    return "低覆盖"


def _format_e_forward_note(
    fwd_n: int,
    fwd_wins: int,
    *,
    full_n: int = 0,
    min_oos: int = 10,  # 与 MIN_OOS_SAMPLE 对齐；字面量避免前向引用
) -> str:
    """E 项前瞻胜率附注（纯函数，便于单测）。

    背景（2026-07-21 T18）：原逻辑 fwd_n>0 且 WR<45 一律标「真低胜率」，
    把 high-range 前瞻 3 笔（全在 07-16 污染日、含 soft/低线）误判为真低胜率。
    与 D 项 MIN_OOS_SAMPLE 对齐：fwd_n < min_oos → 样本过少勿判；
    fwd_n 够且 WR≥45 → 旧污染（规则修复后回升）；够且 WR<45 → 真低胜率。
    """
    if fwd_n <= 0:
        return " | 前瞻0笔(等结算)"
    fwd_wr = round(fwd_wins * 100 / fwd_n, 1)
    still = "仍" if full_n >= 20 else ""
    if fwd_n < min_oos:
        return (
            f" | 前瞻{still}{fwd_n}笔 WR={fwd_wr}%"
            f"(样本过少<{min_oos}，勿判真低胜率)"
        )
    tag = "旧污染" if fwd_wr >= 45 else "真低胜率"
    return f" | 前瞻{still}{fwd_n}笔 WR={fwd_wr}%({tag})"


def _parse_score_line_gate_stats(fa_str: str | None) -> dict:
    """从 filter_audit_json 解析 score_line 阶段被拦候选的最高最终分与最高有效线。

    实测格式（曝光补跑）：
      reason_counts.score_line = {
        "最终分 73.16 低于有效线 89.55": 1,  # key=文案, value=计数
        ...
      }
    旧实现误对 .values()（计数 int）做正则 → 恒 None，self_check 打出「无候选分」。
    另：recommendation_runs.score_line 是 base（72），真实有效线在文案/meta 里可达 89+。

    返回 {"max_candidate_score": float|None, "max_effective_line": float|None}。
    """
    out: dict = {"max_candidate_score": None, "max_effective_line": None}
    if not fa_str:
        return out
    try:
        d = json.loads(fa_str) if isinstance(fa_str, str) else fa_str
    except (TypeError, ValueError):
        return out
    if not isinstance(d, dict):
        return out

    msgs: list[str] = []
    reason_counts = d.get("reason_counts") or {}
    score_line = reason_counts.get("score_line") or {}
    if isinstance(score_line, dict):
        # 生产：key=文案 value=计数；旧单测夹具曾 key=rule_i value=文案 —— 两边都扫
        for k, v in score_line.items():
            msgs.append(str(k))
            if not isinstance(v, (int, float)):
                msgs.append(str(v))
    # 兼容 stage_summary 内嵌 reason_counts
    for stage in (d.get("stage_summary") or []):
        if not isinstance(stage, dict) or stage.get("stage") != "score_line":
            continue
        rc = stage.get("reason_counts") or {}
        if isinstance(rc, dict):
            for k, v in rc.items():
                msgs.append(str(k))
                if not isinstance(v, (int, float)):
                    msgs.append(str(v))
    pat = re.compile(
        r"最终分\s*([\d.]+)\s*低于有效线\s*([\d.]+)"
    )
    best_cand: float | None = None
    best_line: float | None = None
    for msg in msgs:
        for m in pat.finditer(msg):
            try:
                cand = float(m.group(1))
                line = float(m.group(2))
            except ValueError:
                continue
            if best_cand is None or cand > best_cand:
                best_cand = cand
            if best_line is None or line > best_line:
                best_line = line
    # 全文兜底（结构漂移时）
    if best_cand is None and isinstance(fa_str, str):
        for m in pat.finditer(fa_str):
            try:
                cand = float(m.group(1))
                line = float(m.group(2))
            except ValueError:
                continue
            if best_cand is None or cand > best_cand:
                best_cand = cand
            if best_line is None or line > best_line:
                best_line = line
    out["max_candidate_score"] = best_cand
    out["max_effective_line"] = best_line
    return out


def _parse_max_candidate_score(fa_str: str | None) -> float | None:
    """兼容旧单测/调用：只返回最高候选最终分。"""
    return _parse_score_line_gate_stats(fa_str).get("max_candidate_score")


def _build_gate_detail(base_line, fa_raw, rs_raw, snap_raw, trade_date) -> dict | None:
    """从一行 experiment run 的字段构建门线拦截明细。

    原 _fetch_gate_blocked_detail 的解析逻辑抽出为纯函数，使单条与批量
    两条查询路径输出**逐字段一致**（self_check 自检 / 策略清单页同源）。
    """
    # 兼容旧假 DB 返回 2 列 (score_line, fa) 与新 3+ 列
    base_line = float(base_line) if base_line is not None else None
    stats = _parse_score_line_gate_stats(fa_raw if fa_raw else None)
    max_cand = stats.get("max_candidate_score")
    eff_line = stats.get("max_effective_line")
    if rs_raw:
        try:
            rs = json.loads(rs_raw) if isinstance(rs_raw, str) else rs_raw
            meta = (rs or {}).get("score_line_meta") or {}
            if eff_line is None and meta.get("effective_score_line") is not None:
                eff_line = float(meta["effective_score_line"])
            if max_cand is None and rs.get("top_candidate_score") is not None:
                max_cand = float(rs["top_candidate_score"])
        except (TypeError, ValueError):
            pass
    if eff_line is None:
        eff_line = base_line
    # 恐慌日 / 弱势标注（解释有效线 89–101 的主因，勿当补跑故障）
    limit_down = None
    limit_up = None
    regime = None
    if snap_raw:
        try:
            snap = json.loads(snap_raw) if isinstance(snap_raw, str) else snap_raw
            if isinstance(snap, dict):
                limit_down = snap.get("limit_down_count")
                limit_up = snap.get("limit_up_count")
                if limit_down is not None:
                    limit_down = int(limit_down)
                if limit_up is not None:
                    limit_up = int(limit_up)
                regime = (
                    (snap.get("breadth") or {}).get("regime")
                    or snap.get("market_regime")
                    or snap.get("regime")
                )
        except (TypeError, ValueError):
            pass
    panic = bool(limit_down is not None and limit_down > 100)
    return {
        "effective_line": eff_line,
        "max_candidate_score": max_cand,
        "base_score_line": base_line,
        "trade_date": trade_date,
        "limit_down_count": limit_down,
        "limit_up_count": limit_up,
        "regime": regime,
        "panic_day": panic,
    }


def fetch_gate_blocked_details(db, sids: list[str]) -> dict[str, dict | None]:
    """批量门线拦截明细（T4 性能优化）。

    **根因**：原 _fetch_gate_blocked_detail 对每个策略各跑一次
    `LIKE '%"sid"%'` 查询——该谓词前导通配符**非 sargable**（无法走索引），
    且每次都拉 4 个大 JSON 列（filter_audit / result_summary / market_snapshot / trade_date）。
    5 个低曝光策略串行即 ~529ms（N+1 性能杀手），直接拖慢
    `/api/strategies`（1.87s）与 self_check 的 E 项自检。

    **修复**：单次 `started_at` 索引范围扫描（run_mode=experiment & status=completed
    & 近 14 天，约 39 行）→ 内存按 started_at DESC 顺序为每个 sid 取**首条命中**
    （= 原 LIMIT 1 语义）→ 返回 {sid: detail|None}。
    查询次数 5→1，且不再做 N 次全列物化。输出与单条版本逐字段一致。
    """
    if not sids:
        return {}
    targets = set(str(s) for s in sids)
    # started_at 索引范围扫描：候选集从全表 → 近 14 天 experiment/completed（~39 行）
    try:
        rows = db.execute(text("""
            SELECT r.run_id, r.selected_skills_json, r.score_line,
                   r.filter_audit_json, r.result_summary_json,
                   r.market_snapshot_json, r.trade_date, r.started_at
            FROM recommendation_runs r
            WHERE r.run_mode = 'experiment' AND r.status = 'completed'
              AND r.started_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
            ORDER BY r.started_at DESC
        """)).fetchall()
    except Exception:
        return {s: None for s in targets}
    out: dict[str, dict | None] = {s: None for s in targets}
    for row in rows:
        raw = row[1]
        if not raw:
            continue
        try:
            skills = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            continue
        if not isinstance(skills, list):
            continue
        for item in skills:
            if not isinstance(item, dict):
                continue
            sid = item.get("skill_id") or item.get("strategy_id")
            if not sid or str(sid) not in targets:
                continue
            if out.get(str(sid)) is not None:
                continue  # 已取最新命中（started_at DESC 首条）
            out[str(sid)] = _build_gate_detail(
                base_line=row[2], fa_raw=row[3], rs_raw=row[4],
                snap_raw=row[5], trade_date=row[6],
            )
            if all(v is not None for v in out.values()):
                return out  # 全部命中，提前结束
    return out


def _fetch_gate_blocked_detail(db, sid: str) -> dict | None:
    """门线拦截明细：最近 completed experiment 补跑的有效线 vs 最高候选最终分。

    单策略版（保留向后兼容：self_check 内部、策略详情端点仍逐条调用）。
    走自身单条查询（fetchone）+ 共享 _build_gate_detail，输出与批量版逐字段一致。
    不与 fetch_gate_blocked_details 共享查询，避免改变既有单条调用语义/测试。
    """
    try:
        params = {"pat": '%"' + sid + '"%'}
        row = db.execute(text("""
            SELECT r.score_line, r.filter_audit_json, r.result_summary_json,
                   r.market_snapshot_json, r.trade_date
            FROM recommendation_runs r
            WHERE r.run_mode = 'experiment' AND r.status = 'completed'
              AND r.selected_skills_json LIKE :pat
              AND r.started_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
            ORDER BY r.started_at DESC LIMIT 1
        """), params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    # 兼容旧假 DB 返回 2 列 (score_line, fa) 与新 3+ 列
    base_line = float(row[0]) if row[0] is not None else None
    fa_raw = row[1] if len(row) > 1 else None
    rs_raw = row[2] if len(row) > 2 else None
    snap_raw = row[3] if len(row) > 3 else None
    trade_date = str(row[4]) if len(row) > 4 and row[4] is not None else None
    return _build_gate_detail(base_line, fa_raw, rs_raw, snap_raw, trade_date)

def check_e_item(db, hit_skills: set) -> None:
    """E 项：策略覆盖盲区 + 低胜率标记。"""
    # 覆盖盲区
    registered = sorted(
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
    )
    # 排除 disabled 策略（manifest.status != 'active'，如 overnight-holding-method 已下线）：
    # 已下线策略本就不该命中/补跑，否则被误报为「覆盖盲区」「低覆盖需标记」，稀释对
    # 真正活跃策略的注意力。盲区和曝光公平度都只评估 active 策略；低覆盖统计仍含全量
    # （历史胜率追溯需要），但对 disabled 策略单独标注「已下线」而非「需标记」。
    def _status(sid: str) -> str:
        try:
            with open(os.path.join(SKILLS_DIR, sid, 'manifest.json')) as f:
                return str(json.load(f).get('status') or 'active')
        except Exception:
            return 'active'
    active_set = {s for s in registered if _status(s) == 'active'}
    disabled_set = set(registered) - active_set

    blind = [s for s in registered if s not in hit_skills and s in active_set]
    if disabled_set:
        print(f'E 项已下线策略（disabled，不参与盲区/曝光评估）: {sorted(disabled_set)}')
    print(f'E 项覆盖盲区：{len(blind)} 个 active 策略近 60 天 formal 0 命中: {blind}')
    # 细分类：preset 静默死亡 vs 预期稀疏（勿把后者当故障去放宽 universe）
    formal_preset_skills = {
        str(r[0])
        for r in db.execute(text(
            "SELECT skill_id FROM preset_skills WHERE preset_id = 'preset_formal_opening'"
        )).fetchall()
        if r[0]
    }
    blind_cls = _classify_coverage_blind(blind, formal_preset_skills)
    if blind_cls["silent_death"]:
        print(
            f'  🚨 覆盖盲区·静默死亡（未进 preset_formal_opening，formal 永不评估，'
            f'需 INSERT preset_skills）: {blind_cls["silent_death"]}'
        )
    if blind_cls["expected_sparse"]:
        print(
            f'  ℹ️ 覆盖盲区·预期稀疏（已在 formal preset，当前 regime/universe 0 命中，'
            f'勿放宽门槛污染 D 项）: {blind_cls["expected_sparse"]}'
        )

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
        # disabled 策略（已下线）不再补跑/标记——样本就此冻结，标注「已下线」供历史追溯，
        # 不打扰对活跃策略的判断（已下线的 overnight 等不应再出现在「需标记」清单）。
        if row[0] in disabled_set:
            print(f'  {row[0]}: n={n} wins={wins} avg_ret={avg_ret}% winrate={win_rate}% ←已下线(disabled)，样本冻结仅供追溯')
            continue
        tag = _format_e_coverage_tag(int(n or 0), float(win_rate))
        # 前瞻窗口口径（FORWARD_EPOCH 之后）：区分旧代码污染 vs 真低胜率 vs 样本过少
        # （T18：fwd_n < MIN_OOS_SAMPLE 勿判真低胜率——high-range 3 笔@07-16 污染日误判先例）
        fwd_note = _format_e_forward_note(int(fwd_n or 0), int(fwd_wins or 0), full_n=int(n or 0))
        action = "需标记" if tag == "低胜率" else "需补曝光/观察"
        print(f'  {row[0]}: n={n} wins={wins} avg_ret={avg_ret}% winrate={win_rate}%{fwd_note} ←{tag}·{action}')

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
    # 排除 disabled：已下线策略补跑不再喂，0 曝光/样本<20 是预期而非警告。
    # 细分类：补跑「覆盖」= 该 skill 近 7 天有 completed experiment run，
    # 不等于「产出推荐」。0 产出是 universe/regime 拦截，不能写「补跑未覆盖」。
    zero_exp = [s for s in active_set if s not in exposure]
    # 近 7 天 completed experiment 且 selected_skills 含该 skill_id 的策略集合。
    # selected_skills_json 是曝光补跑写入的权威字段（single_skill 入队必带）；
    # 不能用 skill_breakdown（0 推荐时无 rows，会把「跑了但 0 票」误判未覆盖）。
    exp_completed_rows = db.execute(text("""
        SELECT selected_skills_json
        FROM recommendation_runs
        WHERE run_mode = 'experiment' AND status = 'completed'
          AND started_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
          AND selected_skills_json IS NOT NULL
    """)).fetchall()
    experiment_completed_sids: set[str] = set()
    for (raw,) in exp_completed_rows:
        try:
            skills = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            skills = []
        if not isinstance(skills, list):
            continue
        for item in skills:
            if isinstance(item, dict):
                sid = item.get("skill_id") or item.get("strategy_id")
                if sid:
                    experiment_completed_sids.add(str(sid))
    zero_cls = _classify_zero_exposure(zero_exp, experiment_completed_sids)
    if zero_cls["covered_zero"]:
        print(
            f'  ℹ️ {len(zero_cls["covered_zero"])} 个 active 策略近 30 天 0 曝光但补跑已覆盖'
            f'（completed experiment 0 产出，当前 regime/universe 无票，非补跑故障——'
            f'勿为喂样本放宽门槛）: {zero_cls["covered_zero"]}'
        )
    if zero_cls["uncovered"]:
        print(
            f'⚠️ {len(zero_cls["uncovered"])} 个 active 策略近 30 天 0 曝光且补跑未入队'
            f'（真覆盖缺口，查 strategy_exposure_run）: {zero_cls["uncovered"]}'
        )
    under_floor = sorted(s for s, n in exposure.items() if n < SAMPLE_FLOOR and s in active_set)
    if under_floor:
        print(f'⚠️ {len(under_floor)} 个 active 策略样本 <{SAMPLE_FLOOR}（胜率统计不可信，补跑应持续喂）: {under_floor}')

    # ── D 项陷阱护栏：曝光-胜率错配 ≠ 应调权重 ─────────────────────────────
    # 未来 D 攻击轮几乎必看到「高胜率策略低曝光 vs daily-stock 低胜率高曝光」，
    # 会想「把高胜率曝光提上去、daily-stock 降权」来刷 D。这是过拟合陷阱：
    #   ① 曝光高低由 universe 宽窄决定（策略自主定义，架构原则），daily-stock 是
    #      通用技术分析、候选池天然大，龙虎榜/板块龙头本就 niche——是选择效应非错配。
    #   ② 高胜率样本普遍 <50（sector-relative n=38/dragon-tiger n=44），按 E 项规则
    #      样本 <50 不该按胜率大调，随样本增加会回归均值。
    #   ③ E 项补跑（experiment）已在喂低样本策略，再调权重会重复、且可能破 H_norm。
    # 按胜率强制重排曝光违背「策略优先架构」（各策略 universe 自主，不全局一刀切）。
    # 禁据下表直接调 aggregator 权重或曝光补跑阈值；要动 D 走前瞻验证，不刷历史曝光。
    wr_by_sid = {r[0]: round(r[2] * 100 / r[1], 1) for r in rows if r[1] >= SAMPLE_FLOOR}
    if wr_by_sid and exposure:
        print('  ℹ️ 曝光-胜率对照（仅参考，错配=universe 宽窄非 bug，勿按此调权重/曝光）:')
        for sid, n in exposure.most_common():
            if sid in wr_by_sid:
                print(f'    {sid}: 曝光{n}({n/total_exp*100:.0f}%) 前瞻WR={wr_by_sid[sid]}%')

    # 曝光补跑健康诊断：样本 <20 的策略里，区分「补跑在喂」vs「门线拦截」vs「停滞」。
    # T25：以「最新 completed experiment 的 recommended_count」为准——若最新补跑 0 产出，
    # 即使 7 天内更早有推荐，也标门线拦截（07-16 1 笔 + 07-21 三次 0 产出误标先例）。
    # gate_blocked 明细沿用 _fetch_gate_blocked_detail（有效线 vs 最高候选分）。
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
        last_rec: dict[str, tuple] = {
            row[0]: (row[1], int(row[2] or 0)) for row in recent_rows
        }
        exp_run_rows = db.execute(text("""
            SELECT started_at, recommended_count, selected_skills_json
            FROM recommendation_runs
            WHERE run_mode = 'experiment' AND status = 'completed'
              AND started_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
              AND selected_skills_json IS NOT NULL
            ORDER BY started_at DESC
        """)).fetchall()
        last_exp: dict[str, tuple] = {}
        for started_at, rec_n, raw in exp_run_rows:
            try:
                skills = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except (TypeError, ValueError):
                skills = []
            if not isinstance(skills, list):
                continue
            for item in skills:
                if not isinstance(item, dict):
                    continue
                sid = item.get("skill_id") or item.get("strategy_id")
                if not sid or sid in last_exp:
                    continue
                last_exp[str(sid)] = (started_at, int(rec_n or 0))
        health = _classify_exposure_feed_health(
            under_floor, last_rec=last_rec, last_exp=last_exp,
        )
        if health["feeding"]:
            print(f'  ℹ️ 补跑在喂（窄口径样本天然慢，非 bug，不加 universe）: {health["feeding"]}')
        if health["gate_blocked"]:
            details = []
            panic_n = 0
            for sid in health["gate_blocked"]:
                det = _fetch_gate_blocked_detail(db, sid)
                if det and det.get("effective_line") is not None:
                    mc = det.get("max_candidate_score")
                    mc_s = f"最高候选{mc:.1f}" if mc is not None else "无候选分"
                    tag = f"有效线{det['effective_line']:.1f} vs {mc_s}"
                    # 恐慌日（ld>100）有效线 89–101 属设计内：panic+12 + 执行/证据抬线
                    # （07-20 exposure ld=479 实证）。勿当补跑 bug 去放宽门线/universe。
                    if det.get("panic_day"):
                        panic_n += 1
                        ld = det.get("limit_down_count")
                        tag += f",恐慌日ld={ld}"
                    elif det.get("regime"):
                        tag += f",{det['regime']}"
                    details.append(f"{sid}({tag})")
                else:
                    details.append(sid)
            note = (
                f'  ℹ️ 补跑在跑但门线拦截（最新 completed experiment 0 产出；'
                f'{"恐慌日抬线+执行惩罚" if panic_n else "regime 有效线偏高/无高分票"}，'
                f'非补跑故障——勿为喂样本放宽门线，会污染胜率统计）: {details}'
            )
            print(note)
        if health["stalled"]:
            print(f'  🚨 补跑停滞（近7天无该策略 completed experiment run，可能失败/未触发，需手动触发验证）: {health["stalled"]}')


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


# 稳定前瞻纪元：当前 veto / 弱市否决 / soft-floor-ban / 去重 等重大规则 overhaul
# 已于 2026-07-13 前全部落地（rounds 160-166 / 181 / 185），07-14 起的 formal run
# 已反映稳定规则 regime。07-17~21 的微调（soft floor 可见性 / 震荡 veto 等）不改变
# selection 主体，且契约层已拦截新污染。以 07-14 为稳定基线，使前瞻窗口能累积样本、
# 真正可做样本外(OOS)验证——这是北极星 D「必须前瞻样本验证、不为刷数字」护栏的前提。
FORWARD_EPOCH_PINNED = "2026-07-14"
# 动态 epoch 之后需至少这么多已结算样本才切换为纯窗口；否则回退稳定基线。
MIN_OOS_SAMPLE = 10


def _last_rule_change_epoch(db) -> str | None:
    """原动态逻辑：最近一次改动 strategy.yaml/aggregator.py 的 commit 之后首个
    formal 交易日。commit 当天盘前的 run 是旧规则产物，故严格取 commit 时间之后。"""
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


def _pick_forward_epoch(dyn: str | None, n_settled_after_dyn: int | None) -> str:
    """纯函数：稳定化前瞻纪元决策（便于单测锁定，不碰 DB）。

    - 动态 epoch 之后已有 ≥MIN_OOS_SAMPLE 笔已结算样本 → 用动态 epoch（纯窗口，最干净）；
    - 否则回退稳定基线 FORWARD_EPOCH_PINNED（让窗口累积样本，可做 OOS 验证）。
    返回永不为 None。
    """
    if dyn is not None and (n_settled_after_dyn or 0) >= MIN_OOS_SAMPLE:
        return dyn
    return FORWARD_EPOCH_PINNED


def _resolve_forward_epoch(db) -> str:
    """稳定化前瞻纪元解析（接 DB）。

    原纯动态逻辑（_last_rule_change_epoch）的缺陷：self-improve 每轮几乎都提交规则变更，
    且近期多个交易日为保护性 0 推荐（无交易结算），导致动态 epoch 永远落在"今天之后"→
    前瞻窗口恒为空→D 项永远无法做样本外验证（违反北极星 D 护栏），且 E 项前瞻胜率列因
    `trade_date >= NULL` 恒为 0 而永远显示"前瞻0笔(等结算)"（误报）。

    修复：仅在动态 epoch 之后已有 ≥MIN_OOS_SAMPLE 笔已结算样本时才采用纯窗口；否则回退
    到稳定基线 FORWARD_EPOCH_PINNED，使前瞻窗口能累积样本、真正可做 OOS 验证。
    返回类型由 Optional[str] 改为 str（永不为 None），调用方无需再判 None。
    """
    dyn = _last_rule_change_epoch(db)
    n = 0
    if dyn is not None:
        n = db.execute(text("""
            SELECT COUNT(*) c FROM recommendations r
            JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
            WHERE r.recommendation_id LIKE 'rec_%' AND r.trade_date >= :d
              AND r.recommendation_id IN (SELECT recommendation_id FROM recommendation_outcomes o
                  WHERE o.outcome_label IN ('success','failed'))
        """), {"d": dyn}).scalar() or 0
    return _pick_forward_epoch(dyn, n)


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


# formal 硬地板与 RunRequest.FORMAL_SCORE_LINE_FLOOR 对齐；self_check 不强制导入
# contracts（避免 A/B/C/E 路径负担），常量镜像此处。
FORMAL_SCORE_LINE_FLOOR = 72.0

# 契约层地板生产态不变量（T5/Contract 一致性护栏）：
# 经验证（DB 查）：FORMAL_SCORE_LINE_FLOOR 在生产 DB 自该交易日起**已落地生效**——
# 该日（含）之后 0 笔 formal 推荐 score_line < 72。此前（≤2026-07-16）的低线
# run 是修复前遗留污染（matrix 把 score_line=55 写入 formal），非当前系统行为。
# 本护栏断言「first_clean_td 之后 0 笔 sub-72」这一**生产态不变量**，
# 与 _low_score_line_pollution 的污染诊断（后者只暴露幅度、不 fail-loud）正交：
# 诊断暴露历史污染幅度，本护栏断言当前生产态地板仍 intact。
# 价值：多 self-improve 实例并行时，若某实例/未来改动悄悄撤掉地板强制，
# 该不变量会被破坏、D 项重新被低质量票污染——本护栏 loud-fail 拦截，
# 保护 20+ 轮 D 工作成果不被静默回归吞掉。
FORMAL_FLOOR_FIRST_CLEAN_TD = "2026-07-17"


def _floor_violation_count(rows_with_sl: list, first_clean_td: str,
                          floor: float = FORMAL_SCORE_LINE_FLOOR) -> int:
    """纯函数：统计「first_clean_td 之后仍出现 sub-72 formal 推荐」的污染笔数。

    输入 rows_with_sl: [(code, trade_date, label, ret, score_line), ...]
    返回 score_line < floor 且 trade_date >= first_clean_td 的行数。
    便于单测锁定「生产态地板不变量」，不碰 DB。
    """
    n = 0
    for r in rows_with_sl:
        td = str(r[1])
        if td >= first_clean_td and (float(r[4] or 0)) < floor:
            n += 1
    return n


def _print_floor_enforcement(db) -> None:
    """D 项：契约层地板生产态不变量断言（接 DB）。

    只查 DB 一次，复用与 _low_score_line_pollution 同口径的 formal 成功/失败样本。
    断言：first_clean_td 起 0 笔 formal 推荐 score_line < FORMAL_SCORE_LINE_FLOOR。
    不变量 intact → ✅；被破坏 → 🚨 loud-fail（D 污染回归）。
    """
    rows = db.execute(text("""
        SELECT r.code, r.trade_date, o.outcome_label, o.executed_return, u.score_line
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    viol = _floor_violation_count(list(rows), FORMAL_FLOOR_FIRST_CLEAN_TD)
    if viol == 0:
        print(f'  ✅ 契约层地板生产态有效：自 {FORMAL_FLOOR_FIRST_CLEAN_TD} 起 '
              f'0 笔 formal 推荐 score_line < {FORMAL_SCORE_LINE_FLOOR}'
              f'（D 项无低质量票污染，20+ 轮 D 工作成果 intact）')
    else:
        print(f'  🚨 契约层地板生产态被破坏！{viol} 笔 formal 自 {FORMAL_FLOOR_FIRST_CLEAN_TD} 起 '
              f'score_line < {FORMAL_SCORE_LINE_FLOOR}——D 项污染回归风险，'
              f'立即排查 Contract 强制是否仍生效')


def _low_score_line_pollution(
    rows_with_sl: list,
    *,
    floor: float = FORMAL_SCORE_LINE_FLOOR,
) -> dict | None:
    """纯函数：诊断 formal 推荐是否被低 score_line run 污染。

    输入 rows_with_sl: [(code, trade_date, label, ret, score_line), ...]
    （同一 (code, trade_date) 可有多条不同 score_line 的 run 产物）。

    返回 None 表示无污染；否则返回污染笔数 / 污染胜率 / 剔除后反事实指标。
    背景（2026-07-21 T3）：matrix 把 score_line=55 写入 formal（07-16 14 票），
    近 10 日净盈亏比被拖到 1.65。契约层已加 formal 地板，本护栏暴露历史污染幅度，
    防未来轮次把"被低线污染的 D 项下滑"误判为策略退化去加 entry 规则。
    """
    if not rows_with_sl:
        return None
    # 全量：按 (code, date) 去重取最差 ret（与 _fetch_dedup_live_formal 一致）
    all_best: dict[tuple, tuple] = {}
    clean_best: dict[tuple, tuple] = {}
    polluted_best: dict[tuple, tuple] = {}
    for r in rows_with_sl:
        code, td, label, ret, sl = r[0], str(r[1]), r[2], r[3], float(r[4] or 0)
        key = (code, td)
        row4 = (code, td, label, ret)
        if key not in all_best or float(ret or 0) < float(all_best[key][3] or 0):
            all_best[key] = row4
        if sl >= floor:
            if key not in clean_best or float(ret or 0) < float(clean_best[key][3] or 0):
                clean_best[key] = row4
        else:
            if key not in polluted_best or float(ret or 0) < float(polluted_best[key][3] or 0):
                polluted_best[key] = row4
    if not polluted_best:
        return None
    # 仅在"干净总体"里不存在的污染独立票才算真污染（同日同股高线 run 也覆盖则不算）
    pure_polluted = {
        k: v for k, v in polluted_best.items() if k not in clean_best
    }
    if not pure_polluted:
        return None

    def _wr(items: list) -> tuple[float, int]:
        if not items:
            return 0.0, 0
        wins = sum(1 for x in items if x[2] == 'success')
        return round(wins * 100 / len(items), 1), len(items)

    pol_items = list(pure_polluted.values())
    clean_items = list(clean_best.values())
    all_items = list(all_best.values())
    pol_wr, pol_n = _wr(pol_items)
    clean_wr, clean_n = _wr(clean_items)
    full_wr, full_n = _wr(all_items)
    # 按 score_line 分桶计数（只看 pure polluted 来源 run 的 sl）
    sl_buckets: Counter = Counter()
    for r in rows_with_sl:
        key = (r[0], str(r[1]))
        if key in pure_polluted and float(r[4] or 0) < floor:
            sl_buckets[float(r[4] or 0)] += 1
    return {
        "floor": floor,
        "polluted_n": pol_n,
        "polluted_wr": pol_wr,
        "clean_n": clean_n,
        "clean_wr": clean_wr,
        "full_n": full_n,
        "full_wr": full_wr,
        "delta_pp": round(clean_wr - full_wr, 1) if clean_n and full_n else 0.0,
        "sl_buckets": dict(sorted(sl_buckets.items())),
    }


def _print_low_score_line_pollution(db) -> None:
    """D 项：低 score_line formal run 污染诊断（接 DB）。"""
    rows = db.execute(text("""
        SELECT r.code, r.trade_date, o.outcome_label, o.executed_return, u.score_line
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    result = _low_score_line_pollution(list(rows))
    if result is None:
        return
    print(
        f'  ⚠️ 护栏: formal 低 score_line 污染（<{result["floor"]}）独立票 '
        f'{result["polluted_n"]} 笔 WR={result["polluted_wr"]}% '
        f'| 剔除后 WR {result["full_wr"]}%→{result["clean_wr"]}% '
        f'(Δ+{result["delta_pp"]}pp, n={result["clean_n"]}) '
        f'| sl 分布={result["sl_buckets"]}'
    )
    print(
        f'     ——契约层 FORMAL_SCORE_LINE_FLOOR={result["floor"]} 已拦截新污染；'
        f'历史低线票不重标（避免刷数字），D 项下滑先查是否低线污染而非策略退化'
    )


def _soft_regime_pollution(
    rows_with_soft: list,
    *,
    banned_bases: frozenset[str] | set[str] = frozenset({"震荡"}),
) -> dict | None:
    """纯函数：诊断已被 ban 的 regime soft floor 历史污染幅度。

    输入 rows_with_soft: [(code, trade_date, label, ret, soft_applied, market_base), ...]
    soft_applied 为 truthy（1/True/'true'）表示该笔靠 soft floor 放行。

    pure 污染 = 仅有 soft@banned_base 产出、同日同股无 hard-pass 孪生票。
    返回 None 表示无污染；否则返回污染 WR / 剔除后反事实 + 分日/分 sentiment 桶。
    背景（2026-07-21 T13/T14）：aggregator 已禁 震荡 soft，但历史 23 笔 pure soft@震荡
    WR=30.4% 仍落库拖累近 10 日 PL。本护栏暴露幅度，防把污染当策略退化加 entry。
    """
    if not rows_with_soft:
        return None

    def _truthy(v) -> bool:
        return v is not None and str(v).lower() in ("1", "true", "yes")

    all_best: dict[tuple, tuple] = {}
    hard_keys: set[tuple] = set()
    soft_banned_best: dict[tuple, tuple] = {}
    soft_banned_meta: dict[tuple, tuple] = {}  # key -> (td, sent)

    for r in rows_with_soft:
        code, td, label, ret = r[0], str(r[1]), r[2], r[3]
        soft, base = r[4], r[5]
        # optional sentiment at index 6
        sent = r[6] if len(r) > 6 else None
        key = (code, td)
        row4 = (code, td, label, ret)
        if key not in all_best or float(ret or 0) < float(all_best[key][3] or 0):
            all_best[key] = row4
        if not _truthy(soft):
            hard_keys.add(key)
        elif str(base or "") in banned_bases:
            if key not in soft_banned_best or float(ret or 0) < float(soft_banned_best[key][3] or 0):
                soft_banned_best[key] = row4
                soft_banned_meta[key] = (td, str(sent) if sent is not None else None)

    pure = {k: v for k, v in soft_banned_best.items() if k not in hard_keys}
    if not pure:
        return None

    day_buckets: Counter = Counter()
    sent_buckets: Counter = Counter()
    for k in pure:
        td, sent = soft_banned_meta.get(k, (k[1], None))
        day_buckets[td] += 1
        if sent is not None and sent != "None":
            sent_buckets[sent] += 1

    def _wr(items: list) -> tuple[float, int]:
        if not items:
            return 0.0, 0
        wins = sum(1 for x in items if x[2] == "success")
        return round(wins * 100 / len(items), 1), len(items)

    pol_items = list(pure.values())
    clean_items = [v for k, v in all_best.items() if k not in pure]
    all_items = list(all_best.values())
    pol_wr, pol_n = _wr(pol_items)
    clean_wr, clean_n = _wr(clean_items)
    full_wr, full_n = _wr(all_items)
    rets = [float(x[3] or 0) for x in pol_items]
    avg_ret = round(sum(rets) / len(rets), 2) if rets else 0.0
    return {
        "banned_bases": sorted(banned_bases),
        "polluted_n": pol_n,
        "polluted_wr": pol_wr,
        "polluted_avg": avg_ret,
        "clean_n": clean_n,
        "clean_wr": clean_wr,
        "full_n": full_n,
        "full_wr": full_wr,
        "delta_pp": round(clean_wr - full_wr, 1) if clean_n and full_n else 0.0,
        "day_buckets": dict(sorted(day_buckets.items())),
        "sent_buckets": dict(sorted(sent_buckets.items())),
        "independent_days": len({k[1] for k in pure}),
    }


def _print_soft_regime_pollution(db) -> None:
    """D 项：已 ban regime 的 soft floor 历史污染诊断（接 DB）。"""
    rows = db.execute(text("""
        SELECT r.code, r.trade_date, o.outcome_label, o.executed_return,
               JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.dynamic_soft_floor_applied') AS soft,
               JSON_UNQUOTE(JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.market_base')) AS mbase,
               JSON_UNQUOTE(JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.market_sentiment')) AS sent
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    result = _soft_regime_pollution(list(rows))
    if result is None:
        return
    print(
        f'  ⚠️ 护栏: soft@{result["banned_bases"]} 历史污染独立票 '
        f'{result["polluted_n"]} 笔 WR={result["polluted_wr"]}% avg={result["polluted_avg"]}% '
        f'(独立日={result["independent_days"]}) '
        f'| 剔除后 WR {result["full_wr"]}%→{result["clean_wr"]}% '
        f'(Δ+{result["delta_pp"]}pp, n={result["clean_n"]}) '
        f'| 日分布={result["day_buckets"]} sentiment={result["sent_buckets"]}'
    )
    print(
        f'     ——aggregator 已禁 {"/".join(result["banned_bases"])} soft（新 run 不再产出）；'
        f'历史票不重标；近10日 PL 偏低先查 soft 污染+单日 regime，勿加 entry 规则'
    )


def _pure_pollution_keys(
    rows_enriched: list,
    *,
    score_floor: float = FORMAL_SCORE_LINE_FLOOR,
    banned_soft_bases: frozenset[str] | set[str] = frozenset({"震荡"}),
) -> dict:
    """从 enriched formal 行提取 pure 污染 (code, trade_date) 集合。

    输入 rows_enriched: [(code, trade_date, label, ret, score_line, soft_applied, market_base), ...]
    pure_low = 仅 sl<floor 产出且无 hard 孪生
    pure_soft = 仅 soft@banned_base 产出且无 hard 孪生
    union = 并集（两污染源正交时 = 和）
    纯函数，便于单测。
    """
    def _truthy(v) -> bool:
        return v is not None and str(v).lower() in ("1", "true", "yes")

    hard_sl: set[tuple] = set()
    low_sl: set[tuple] = set()
    hard_soft: set[tuple] = set()
    soft_ban: set[tuple] = set()
    for r in rows_enriched:
        code, td = r[0], str(r[1])
        key = (code, td)
        sl = float(r[4] or 0)
        soft, base = r[5], r[6]
        if sl >= score_floor:
            hard_sl.add(key)
        else:
            low_sl.add(key)
        if not _truthy(soft):
            hard_soft.add(key)
        elif str(base or "") in banned_soft_bases:
            soft_ban.add(key)
    pure_low = low_sl - hard_sl
    pure_soft = soft_ban - hard_soft
    return {
        "pure_low": pure_low,
        "pure_soft": pure_soft,
        "union": pure_low | pure_soft,
    }


def _clean_d_counterfactual(
    dedup_rows: list,
    pollution_keys: set[tuple],
    *,
    last10_dates: set | None = None,
    since_date: str | None = None,
) -> dict | None:
    """去 pure 污染后的 D 项反事实（不改主口径，只报告）。

    dedup_rows: _fetch_dedup_live_formal 口径 [(code, trade_date, label, ret), ...]
    pollution_keys: pure 污染 (code, trade_date)
    since_date: 可选前瞻纪元，额外报前瞻窗口 raw vs clean。
    返回 full/last10[/fwd] 的 raw vs clean 净成本指标 + 关键结论。
    """
    if not dedup_rows:
        return None
    clean_rows = [
        r for r in dedup_rows
        if (r[0], str(r[1])) not in pollution_keys
    ]
    if last10_dates is None:
        dates = sorted({str(r[1]) for r in dedup_rows})
        last10_dates = set(dates[-10:]) if dates else set()
    full_raw = _compute_d_cost(dedup_rows)
    full_clean = _compute_d_cost(clean_rows)
    l10_raw = _compute_d_cost(dedup_rows, trade_dates=last10_dates)
    l10_clean = _compute_d_cost(clean_rows, trade_dates=last10_dates)
    if full_raw is None:
        return None
    out = {
        "pollution_n": len(pollution_keys),
        "full_raw": full_raw,
        "full_clean": full_clean,
        "last10_raw": l10_raw,
        "last10_clean": l10_clean,
        "wr_delta_full": (
            round(full_clean["net_wr"] - full_raw["net_wr"], 1)
            if full_clean else None
        ),
        "pl_delta_full": (
            round(full_clean["net_pl"] - full_raw["net_pl"], 2)
            if full_clean else None
        ),
        "wr_delta_last10": (
            round(l10_clean["net_wr"] - l10_raw["net_wr"], 1)
            if l10_raw and l10_clean else None
        ),
        "pl_delta_last10": (
            round(l10_clean["net_pl"] - l10_raw["net_pl"], 2)
            if l10_raw and l10_clean else None
        ),
        "fwd_raw": None,
        "fwd_clean": None,
        "wr_delta_fwd": None,
        "pl_delta_fwd": None,
        "fwd_pollution_n": 0,
        "fwd_clean_n": 0,
    }
    if since_date:
        fwd_raw = _compute_d_cost(dedup_rows, since_date=since_date)
        fwd_clean = _compute_d_cost(clean_rows, since_date=since_date)
        out["fwd_raw"] = fwd_raw
        out["fwd_clean"] = fwd_clean
        # 前瞻窗口内 pure 污染票数（独立 code+date）
        fwd_keys = {
            (r[0], str(r[1])) for r in dedup_rows if str(r[1]) >= since_date
        }
        out["fwd_pollution_n"] = len(fwd_keys & pollution_keys)
        out["fwd_clean_n"] = len(fwd_keys - pollution_keys)
        if fwd_raw and fwd_clean:
            out["wr_delta_fwd"] = round(fwd_clean["net_wr"] - fwd_raw["net_wr"], 1)
            out["pl_delta_fwd"] = round(fwd_clean["net_pl"] - fwd_raw["net_pl"], 2)
    return out


def _print_clean_d_counterfactual(db) -> None:
    """D 项：低线+soft@震荡 合并去污染反事实（接 DB）。

    背景（2026-07-21 T16）：两道污染护栏各自报 WR 提升，但近10日净 PL 1.65
    是否被污染解释不清楚 → 误把 PL 压缩当 entry 可解。本函数合并 pure 污染源，
    对全量/近10日/前瞻同时报去污染后 WR 与净 PL。

    背景（2026-07-21 T17）：稳定前瞻纪元后 07-16 单日成为窗口全部样本且 26/28
    为 pure 污染——raw 前瞻 WR 39% 会被误读为 OOS 退化。前瞻去污染 + 污染占比
    显式化：污染主导 → 非 OOS 退化；clean 样本过少 → 仍不足 OOS。
    """
    raw = db.execute(text("""
        SELECT r.code, r.trade_date, o.outcome_label, o.executed_return, u.score_line,
               JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.dynamic_soft_floor_applied') AS soft,
               JSON_UNQUOTE(JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.market_base')) AS mbase
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    keys = _pure_pollution_keys(list(raw))
    if not keys["union"]:
        return
    dedup = _fetch_dedup_live_formal(db)
    dates = sorted({str(r[1]) for r in dedup})
    last10 = set(dates[-10:]) if dates else set()
    ep = FORWARD_EPOCH  # 模块级，main 入口已 resolve
    cf = _clean_d_counterfactual(
        dedup, keys["union"], last10_dates=last10, since_date=ep,
    )
    if cf is None or cf["full_clean"] is None:
        return
    fr, fc = cf["full_raw"], cf["full_clean"]
    print(
        f'  ℹ️ 去污染反事实（pure 低线 {len(keys["pure_low"])} + soft@震荡 {len(keys["pure_soft"])}'
        f' = {cf["pollution_n"]} 独立票，不改主口径）:'
    )
    print(
        f'     全量 净WR {fr["net_wr"]}%→{fc["net_wr"]}% (Δ{cf["wr_delta_full"]:+}pp)'
        f' | 净PL {fr["net_pl"]}→{fc["net_pl"]} (Δ{cf["pl_delta_full"]:+})'
        f' | 净均 {fr["net_avg"]}%→{fc["net_avg"]}% | n {fr["n"]}→{fc["n"]}'
    )
    lr, lc = cf["last10_raw"], cf["last10_clean"]
    if lr and lc and cf["wr_delta_last10"] is not None:
        print(
            f'     近10日 净WR {lr["net_wr"]}%→{lc["net_wr"]}% (Δ{cf["wr_delta_last10"]:+}pp)'
            f' | 净PL {lr["net_pl"]}→{lc["net_pl"]} (Δ{cf["pl_delta_last10"]:+})'
            f' | 净均 {lr["net_avg"]}%→{lc["net_avg"]}% | n {lr["n"]}→{lc["n"]}'
        )
        if abs(cf["pl_delta_last10"] or 0) < 0.05 and (cf["wr_delta_last10"] or 0) >= 3:
            print(
                f'     ——近10日去污染后 WR 回升但净 PL 几乎不变（{lr["net_pl"]}→{lc["net_pl"]}）：'
                f'PL 压缩主因不在低线/soft 污染，勿加 entry 规则；'
                f'查出场结构/单日 regime（trailing 过早锁利、震荡日）'
            )
        elif (cf["pl_delta_last10"] or 0) >= 0.1:
            print(
                f'     ——近10日去污染后净 PL 回升：历史污染是主拖累之一，'
                f'契约层+soft ban 已拦截新污染，等前瞻 formal 结算确认'
            )
    fwr, fwc = cf["fwd_raw"], cf["fwd_clean"]
    if ep and fwr is not None:
        poll_n, clean_n = cf["fwd_pollution_n"], cf["fwd_clean_n"]
        total_fwd = poll_n + clean_n
        share = round(poll_n * 100 / total_fwd, 0) if total_fwd else 0
        # 前瞻窗内 pure_low / pure_soft 拆分（污染源可追责，勿只报总数）
        fwd_keys = {(r[0], str(r[1])) for r in dedup if str(r[1]) >= ep}
        n_low, n_soft = _forward_pollution_split(
            fwd_keys, keys["pure_low"], keys["pure_soft"],
        )
        split_note = f"低线{n_low}+soft@震荡{n_soft}"
        if fwc is not None and clean_n > 0:
            print(
                f'     前瞻≥{ep} 净WR {fwr["net_wr"]}%→{fwc["net_wr"]}%'
                f' | 净PL {fwr["net_pl"]}→{fwc["net_pl"]}'
                f' | n {fwr["n"]}→{fwc["n"]}'
                f' （污染 {poll_n}/{total_fwd}={share:.0f}%={split_note}）'
            )
        else:
            print(
                f'     前瞻≥{ep} raw n={fwr["n"]} 净WR={fwr["net_wr"]}% 净PL={fwr["net_pl"]}'
                f' | pure 污染 {poll_n}/{total_fwd}={share:.0f}%（{split_note}），clean 仅 {clean_n} 笔'
            )
        # 污染主导（≥70%）或 clean 过少（<MIN_OOS_SAMPLE）→ 非 OOS 退化
        if total_fwd > 0 and (share >= 70 or clean_n < MIN_OOS_SAMPLE):
            print(
                f'     ——前瞻窗口被 pure 污染主导（{poll_n}/{total_fwd}={split_note}）或 clean 样本'
                f' <{MIN_OOS_SAMPLE}：raw 低胜率是单日/受污染 regime（如 07-16 soft+低线），'
                f'非 D 项 OOS 退化；Contract 已拦截新污染，待多交易日 clean formal 结算后再做 OOS'
            )


def _forward_pollution_split(
    fwd_keys: set[tuple],
    pure_low: set[tuple],
    pure_soft: set[tuple],
) -> tuple[int, int]:
    """纯函数：前瞻窗内 pure_low / pure_soft 票数（可重叠，各自计数）。

    背景：仅报污染总数会掩盖「主要是低线还是 soft」——07-16 实证低线15+soft11。
    返回 (n_low, n_soft)。
    """
    return len(pure_low & fwd_keys), len(pure_soft & fwd_keys)


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

    返回 {board: {n, win_rate, max_loss, avg, net_pl, net_avg}}（无样本为 None）。
    net_pl/net_avg 复用 _compute_d_cost 的精确口径（total P/L=Σ赢/Σ|亏|，扣 0.07%/笔），
    使 D 项盈亏比退化可按板块分解——定位「赢家变小」是全局还是局部(regime 漂移)，
    避免把健康的 20cm 分段误判为退化源而过拟合 penalize。

    防过拟合护栏（数据驱动结论，第169轮 T3 实证）：
    若 20cm 是胜率/盈亏比最高分段，后续 self-improve 轮次**不得**对 20cm 加风险闸门/
    降权/缩小 universe——会砍掉系统最强分段、损害整体 D 项胜率。最大亏损在两类板块均
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
        net = [x - ADDITIONAL_COST_PCT for x in rets]
        gp = sum(x for x in rets if x > 0)
        gl = abs(sum(x for x in rets if x < 0))
        np_ = sum(x for x in net if x > 0)
        nl = abs(sum(x for x in net if x < 0))
        net_pl = round(np_ / nl, 2) if nl > 0 else 999.0
        out[board] = {
            "n": n,
            "win_rate": round(wins * 100 / n, 1),
            "max_loss": round(min(rets), 2) if rets else 0.0,
            "avg": round(sum(rets) / n, 2),
            "net_pl": net_pl,
            "net_avg": round(sum(net) / n, 2),
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
              f'| 最大亏损 {m["max_loss"]}% | 平均收益 {m["avg"]}% '
              f'| 净盈亏比 {m["net_pl"]} | 净均收益 {m["net_avg"]}%')
    # 防过拟合护栏：D 项盈亏比退化若集中在 10cm、20cm 仍健康，
    # 后续轮次禁止对 20cm 加闸门/降权（会误伤强分段，把 regime 漂移当全局退化刷数字）。
    c20, c10 = bd.get("20cm"), bd.get("10cm")
    if (c20 and c10 and c20["net_pl"] >= 2.0 and c10["net_pl"] < 1.5
            and c20["net_pl"] > c10["net_pl"] * 1.2):
        print(f'  ⚠️ 护栏: 净盈亏比退化集中在 10cm({c10["net_pl"]})，20cm 健康({c20["net_pl"]})'
              f'→ 禁止对 20cm 加风险闸门/降权/缩小 universe（会误伤强分段，过拟合）')


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


def _check_market_regime_calibration() -> str | None:
    """T5 一致性护栏（第185轮闭环 round-185）：market_regime_score 必须保持「逆势立场」。

    函数返回值越高 → 自适应 score_line 门槛越宽松（放行更多票）。强牛(up_ratio>=0.65)
    市场观测胜率虽高(~96.4%)但含选择偏差（严格门槛只放行最优选手），故函数刻意返低分(严格)。

    ❌ 若有人为"修正校准"而翻转（强牛返高分、弱市返低分），会放行更差票、D 项反而恶化
    （过拟合）。本护栏纯函数比对函数输出梯度，不碰 DB，便于单测锁定。

    返回 None = 校准正常（强牛返分 < 弱市返分）；返回 str = 疑似被翻转、需人工核查。
    """
    weak = market_regime_score({"up_count": 10, "sample_count": 100})    # up_ratio=0.10 <0.35
    strong = market_regime_score({"up_count": 90, "sample_count": 100})  # up_ratio=0.90 >=0.65
    if not (strong < weak):
        return (f'⚠️ [T5] market_regime_score 校准疑似被翻转：强牛返分({strong:.0f})≥'
                f'弱市返分({weak:.0f})。强牛高胜率含选择偏差，翻转会放行更差票损害 D 项'
                f'——禁止翻转，保持逆势立场（见 feature_builder.market_regime_score docstring）')
    return None


def _forward_window_day_spread(rows: list, epoch: str) -> tuple[int, float, str | None]:
    """纯函数：前瞻窗口的交易日分布（便于单测锁定）。

    返回 (distinct_days, dominant_day_share, dominant_day)。
    窗口过窄（≤2 交易日 / 单日占比过高）→ 单日 regime 噪声主导，不足以做 OOS 验证，
    低胜率是单日受污染样本而非 D 项退化（禁止据此循环加规则，第164轮过拟合护栏）。
    rows 元素: (code, trade_date, label, executed_return)。
    """
    from collections import Counter
    fwd = [str(r[1]) for r in rows if str(r[1]) >= epoch]
    if not fwd:
        return 0, 0.0, None
    c = Counter(fwd)
    distinct = len(c)
    dom_day, dom_n = c.most_common(1)[0]
    return distinct, round(dom_n / len(fwd), 2), dom_day


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

    # 前瞻窗口（稳定纪元后）：FORWARD_EPOCH 之后的 run 才反映去重/弱市否决的真实胜率。
    # 若前瞻窗口胜率显著高于全量，说明全量下滑是历史污染而非真退化——
    # 切勿据此循环加规则（禁止过拟合，第164轮 T3 失败归因结论）。
    #
    # 稳定纪元护栏：FORWARD_EPOCH 现取稳定基线（07-14，重大 overhaul 后；见 _resolve_forward_epoch），
    # 仅当某次规则变更后已累积 ≥MIN_OOS_SAMPLE 笔结算样本才切纯动态窗口。旧逻辑用纯动态 git
    # epoch 会随每轮规则提交重置→窗口永远空→无法 OOS 验证；且 epoch=None 时 E 项前瞻列因
    # `trade_date >= NULL` 恒 0 而误报"前瞻0笔"。现 epoch 永非 None，窗口可累积、OOS 可验证。
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
        # 窗口过窄护栏：近期多个交易日为保护性 0 推荐，已结算 formal 样本常集中于单一
        # 受污染 regime 日（如 07-16：soft@震荡11笔+低线14笔+单日regime风险6笔，均已
        # Contract 拦截）。此低胜率为单日噪声，非 D 项退化，禁止据此循环加 entry 规则。
        _fwd_days, _fwd_dom_share, _fwd_dom = _forward_window_day_spread(rows, FORWARD_EPOCH)
        if _fwd_days <= 2:
            _dom_note = f"（主导日 {_fwd_dom} 占 {_fwd_dom_share*100:.0f}%）" if _fwd_days == 1 else ""
            print(f'  ⚠️ 护栏: 前瞻窗口仅 {_fwd_days} 个交易日{_dom_note}——过窄，'
                  f'不足以做样本外(OOS)验证。该低胜率为单日/受污染 regime 样本'
                  f'（07-16 含 soft@震荡+低线+单日regime风险，均已 Contract 拦截），'
                  f'非 D 项退化；待更多交易日结算后复核，勿据此循环加规则。')

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

    # T5 一致性护栏：market_regime_score 必须保持逆势立场（强牛返分<弱市返分）。
    # 若被"修正校准"翻转会放行更差票、D 项恶化（过拟合，lessons #185 已锁）。
    _calib = _check_market_regime_calibration()
    if _calib:
        print(f'  {_calib}')

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

    # 低 score_line formal 污染护栏（matrix/手动降线 → 低质量票进 LIVE formal 拖累 D）
    _print_low_score_line_pollution(db)
    # soft@震荡 历史污染护栏（aggregator 已禁，历史票仍拖近10日 PL）
    _print_soft_regime_pollution(db)
    # 契约层地板生产态不变量断言（T5/Contract 一致性：地板强制仍 intact）
    _print_floor_enforcement(db)
    # 两道污染合并去污染反事实：近10日 WR vs PL 是否同向回升（防误判 PL=污染）
    _print_clean_d_counterfactual(db)

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
               o.exit_reason, o.max_return, r.trade_date, r.final_score
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


def _covered_by_soft_floor_ban(fs: dict) -> bool:
    """路 3：历史 soft floor 票若落在当前已禁用 soft 的 regime，算已被覆盖。

    背景（2026-07-21 T13）：aggregator 对 market_base=='震荡' 禁用 soft floor
    （高潮+活跃均净负，合 23 笔 WR30.4%）。旧覆盖口径只查 prefilter/veto，
    把 soft@震荡 失败仍报「真残留」→ 误导下轮 D 攻击去挖 entry 规则。
    本函数读 feature_snapshot.score_line_meta（run 时写入），不碰 DB。
    """
    meta = (fs or {}).get('score_line_meta') or {}
    if not meta.get('dynamic_soft_floor_applied'):
        return False
    return meta.get('market_base') == '震荡'


def _failure_is_covered(fs: dict, sb: list, skills: dict) -> bool:
    """单笔失败是否被当前规则覆盖（三路并查）。纯函数，便于单测。

    路 1: evaluate_skill_prefilter(market)
    路 2: run_strategy hard_veto / 排除 phase
    路 3: soft floor ban（震荡 regime 历史 soft 票）
    """
    if _covered_by_soft_floor_ban(fs):
        return True
    from app.engine.strategy_runner import run_strategy, evaluate_skill_prefilter

    for s in sb:
        sid = s.get('strategy_id')
        skill = skills.get(sid)
        if not skill:
            continue
        try:
            prefilter_pass, _reason = evaluate_skill_prefilter(skill, dict(fs), stage='market')
            sig = run_strategy(skill, dict(fs), [], skip_universe_check=False)
            if (not prefilter_pass) or sig.get('hard_veto') or (sig.get('phase') == '排除'):
                return True
        except Exception:
            continue
    return False


def _classify_failed_coverage(rows, skills) -> tuple[int, int, int, int]:
    """统计失败样本中被当前规则覆盖（hard_veto / universe / prefilter / soft ban）的笔数。

    纯函数（不碰 DB），便于单元测试。返回 (blocked_count, new_total, wins, total)：
      - blocked_count: 失败样本中会被当前规则拦截的去重 recommendation_id 数
      - new_total: total - blocked_count（剔除被覆盖失败笔后的样本数）
      - wins: 成功笔数（不变）
      - total: 样本总数

    覆盖口径复刻生产管线，三路并查任一命中即算覆盖：
      1. evaluate_skill_prefilter(stage='market')：universe + prefilter.market.include
         /include_any/exclude（生产 prefilter 全量，含 run_strategy._passes_universe 漏掉
         的 prefilter.market.include/include_any）。
      2. run_strategy：phases hard_veto（如弱市隔夜毒区）+ 排除 phase + setup hard_veto
         + risk rules。这些在 prefilter 之后、scoring 阶段才判定，evaluate_skill_prefilter 不查。
      3. soft floor ban：feature_snapshot.score_line_meta 显示历史 soft 且 market_base==震荡
         （aggregator 已禁 震荡 soft，见 _covered_by_soft_floor_ban）。
    三路并查确保与生产一致——只查 universe+prefilter.market.exclude（旧实现）会漏掉
    prefilter.market.include 覆盖（如 chip_loose），低估覆盖率、高估残留毒区。
    """
    total = len(rows)
    wins = sum(1 for r in rows if r[1] == 'success')

    blocked_rids: set = set()
    for row in rows:
        rid, label, _ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        if _failure_is_covered(fs, sb, skills):
            blocked_rids.add(rid)

    blocked = len(blocked_rids)
    return blocked, total - blocked, wins, total


# --------------------------------------------------------------------------- #
# 真残留毒区采集 + 归因（D 项规则覆盖校验第二段，旧实现遗漏）
# --------------------------------------------------------------------------- #
def _collect_uncovered_failures(rows, skills) -> list[dict]:
    """采集失败样本中被当前规则「未」覆盖的笔。

    与 _classify_failed_coverage 同源、同覆盖口径（三路并查），只是收集未覆盖项。
    返回 list[dict]，每项含 rid / feature_snapshot(dict) / exit_reason / max_return /
    executed_return，供 _print_residual_poison_zone 做模式归因。纯函数，便于单测。
    """
    out: list[dict] = []
    for row in rows:
        rid, label, ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        exit_reason = row[5] if len(row) > 5 else None
        max_return = row[6] if len(row) > 6 else None
        if label != 'failed':
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        if not _failure_is_covered(fs, sb, skills):
            # trade_date / final_score：SQL 扩展列（index 7/8）；rid 回退
            td = str(row[7]) if len(row) > 7 and row[7] is not None else ''
            if not td:
                parts = str(rid).split('_')
                if len(parts) > 1 and len(parts[1]) == 8:
                    d = parts[1]
                    td = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
            final_score = row[8] if len(row) > 8 else None
            if final_score is None:
                final_score = fs.get('final_score')
            out.append({
                'rid': rid,
                'fs': fs,
                'exit_reason': exit_reason,
                'max_return': max_return,
                'executed_return': ret,
                'trade_date': td,
                'final_score': final_score,
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
    market_regime + market_sentiment_phase（历史可能为单维度伪活跃）。

    T28 起 _market_sentiment 已双维度（涨停率 + lu/ld≥1.2）；本函数仍报历史标签分布，
    配合 _pseudo_active_sentiment_counterfactual 区分「历史伪活跃」vs 真活跃毒区。

    纯函数（仅读 uncovered），返回按 count 降序的 [(base, sent), {count, avg_return}]。
    """
    counts: Counter = Counter()
    rets_by_key: dict[tuple[str, str], list[float]] = {}
    for u in uncovered:
        fs = u.get('fs') or {}
        meta = fs.get('score_line_meta') or {}
        base = str(fs.get('market_regime') or meta.get('market_base') or '?')
        sent = str(
            fs.get('market_sentiment_phase')
            or meta.get('market_sentiment')
            or '?'
        )
        # market_regime 有时是「弱势·活跃」合体；拆开
        if '·' in base and sent in ('?', ''):
            parts = base.split('·', 1)
            base, sent = parts[0], parts[1]
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


def _pseudo_active_sentiment_counterfactual(
    uncovered: list[dict],
    market_snaps: dict[str, dict],
) -> dict | None:
    """用当前双维度 _market_sentiment 重判历史毒区票的情绪（纯函数）。

    market_snaps: trade_date -> {sample_count, up_count, limit_up_count, limit_down_count}
    返回 {
      weak_active_n, pseudo_n, still_active_n,
      would_raise_line_n,  # 伪活跃→平淡 且 final_score < new_eff@72 会被抬线挡住
      day_breakdown: {date: {n, pseudo, lu, ld, new_sent}},
    }
    无弱势·活跃票返回 None。
    """
    from app.engine.aggregator import (
        _market_sentiment,
        _market_base,
        resolve_score_line_context,
    )

    weak_active: list[dict] = []
    for u in uncovered:
        fs = u.get('fs') or {}
        meta = fs.get('score_line_meta') or {}
        base = str(fs.get('market_regime') or meta.get('market_base') or '?')
        sent = str(
            fs.get('market_sentiment_phase')
            or meta.get('market_sentiment')
            or '?'
        )
        if '·' in base and sent in ('?', ''):
            parts = base.split('·', 1)
            base, sent = parts[0], parts[1]
        # market_regime 有时已是合体标签
        if base == '弱势' and sent == '活跃':
            weak_active.append(u)
        elif base.startswith('弱势') and '活跃' in base:
            weak_active.append(u)

    if not weak_active:
        return None

    pseudo_n = 0
    still_n = 0
    would_block = 0
    days: dict[str, dict] = {}
    for u in weak_active:
        td = str(u.get('trade_date') or '')
        # rid 回退
        if not td:
            rid = str(u.get('rid') or '')
            parts = rid.split('_')
            if len(parts) > 1 and len(parts[1]) == 8:
                d = parts[1]
                td = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        snap = market_snaps.get(td) or {}
        if not snap:
            still_n += 1
            continue
        new_sent = _market_sentiment(snap)
        new_base = _market_base(snap)
        is_pseudo = new_sent != '活跃'
        if is_pseudo:
            pseudo_n += 1
        else:
            still_n += 1
        day = days.setdefault(td, {
            "n": 0, "pseudo": 0, "lu": snap.get("limit_up_count"),
            "ld": snap.get("limit_down_count"), "up": snap.get("up_count"),
            "sample": snap.get("sample_count"), "new_sent": new_sent,
            "new_base": new_base,
        })
        day["n"] += 1
        if is_pseudo:
            day["pseudo"] += 1
        # 反事实：伪活跃→平淡时，base72 新有效线 vs 历史 final_score
        # 注：平淡 madj 更低（+2 vs +5），线更低；会挡的是 score < 新线 的低分票
        ctx = resolve_score_line_context(72.0, snap)
        score = u.get('final_score')
        if score is None:
            score = (u.get('fs') or {}).get('final_score')
        if is_pseudo and score is not None and float(score) < float(ctx['effective_score_line']):
            would_block += 1

    return {
        "weak_active_n": len(weak_active),
        "pseudo_n": pseudo_n,
        "still_active_n": still_n,
        "would_block_n": would_block,
        "day_breakdown": days,
    }


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
    # feature_snapshot 存 run 时的历史标签（T28 前可能为伪活跃）。
    # T28 起 _market_sentiment 双维度；下方伪活跃反事实用当前规则重判。
    regime_sent_breakdown = _residual_regime_sentiment_breakdown(uncovered)
    if regime_sent_breakdown:
        print('  毒区 × (regime, sentiment) 分布（定位 regime 误判根因）:')
        for (base, sent), entry in regime_sent_breakdown:
            print(f'    - {base}·{sent}: {entry["count"]} 笔（均净 {entry["avg_return"]}%）')

    # T30：双维度情绪反事实——历史「弱势·活跃」有多少是伪活跃
    try:
        from app.db.session import SessionLocal as _SL
        _db = _SL()
        try:
            snap_rows = _db.execute(text("""
                SELECT trade_date, sample_count, up_count, limit_up_count, limit_down_count
                FROM market_snapshots
            """)).fetchall()
            market_snaps = {
                str(r[0]): {
                    "sample_count": r[1],
                    "up_count": r[2],
                    "limit_up_count": r[3],
                    "limit_down_count": r[4],
                }
                for r in snap_rows
            }
        finally:
            _db.close()
        cf = _pseudo_active_sentiment_counterfactual(uncovered, market_snaps)
        if cf and cf["weak_active_n"] > 0:
            print(
                f'  ℹ️ 伪活跃反事实（T28 双维度重判历史弱势·活跃 {cf["weak_active_n"]} 笔）: '
                f'伪活跃 {cf["pseudo_n"]} / 仍活跃 {cf["still_active_n"]}'
                f'（不重标历史；新 formal 用双维度）'
            )
            # 按日摘要
            for td, d in sorted(cf["day_breakdown"].items()):
                flag = '伪活跃→' + str(d.get("new_sent")) if d.get("pseudo") else '仍活跃'
                print(
                    f'     · {td}: {d["n"]} 笔（{flag}，lu={d.get("lu")} ld={d.get("ld")} '
                    f'up={d.get("up")}/{d.get("sample")}）'
                )
            if cf["pseudo_n"] >= max(3, cf["weak_active_n"] // 2):
                print(
                    f'     ——弱势·活跃毒区主因是历史伪活跃（涨停孤岛+普跌），'
                    f'T28 已修情绪入口；勿对 residual 弱势·活跃加 entry 规则'
                )
    except Exception as _e:
        # 诊断失败不阻断主报告
        pass

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


def _base_score_line_from_fs(fs: dict) -> float | None:
    """从 feature_snapshot.score_line_meta.base_score_line 取 run 基线。

    生产写入与 recommendation_runs.score_line 一致（实证 mismatch=0）。
    用于在 7-tuple 覆盖口径下识别低线污染，无需扩展 SQL 行形状。
    """
    meta = (fs or {}).get("score_line_meta") or {}
    v = meta.get("base_score_line")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _forward_veto_kept_stats(
    fwd_rows: list,
    blocked_rids: set,
    *,
    score_floor: float = FORMAL_SCORE_LINE_FLOOR,
) -> dict | None:
    """纯函数：前瞻窗口剔除 blocked 失败后算净 WR/PL，并拆 pure 低线残留。

    residual_low = kept 中 base_score_line < score_floor 的票（run 参数污染，
    不在 strategy 三路覆盖路径）。clean_kept = 剔除 residual_low 后的指标。
    n < 3 返回 None（噪声大）。
    """
    kept = [r for r in fwd_rows if not (r[1] == "failed" and r[0] in blocked_rids)]
    n = len(kept)
    if n < 3:
        return None

    def _net_metrics(items: list) -> dict:
        wins = [float(r[2]) - ADDITIONAL_COST_PCT for r in items if r[1] == "success"]
        losses = [float(r[2]) - ADDITIONAL_COST_PCT for r in items if r[1] == "failed"]
        nn = len(items)
        wr = round(len(wins) * 100 / nn, 1) if nn else 0.0
        win_avg = sum(wins) / len(wins) if wins else 0.0
        loss_avg = abs(sum(losses) / len(losses)) if losses else 0.0
        pl = round(win_avg / loss_avg, 2) if loss_avg else 0.0
        avg = round((sum(wins) + sum(losses)) / nn, 2) if nn else 0.0
        return {"n": nn, "wr": wr, "pl": pl, "avg": avg}

    # residual pure 低线：base_score_line < floor
    residual_low_rids: set = set()
    clean_kept: list = []
    for r in kept:
        fs = json.loads(r[3]) if r[3] else {}
        base = _base_score_line_from_fs(fs)
        if base is not None and base < score_floor:
            residual_low_rids.add(r[0])
        else:
            clean_kept.append(r)

    full = _net_metrics(kept)
    clean = _net_metrics(clean_kept) if clean_kept else None
    return {
        "n": full["n"],
        "wr": full["wr"],
        "pl": full["pl"],
        "avg": full["avg"],
        "blocked_n": len(blocked_rids),
        "residual_low_n": len(residual_low_rids),
        "clean_n": clean["n"] if clean else 0,
        "clean_wr": clean["wr"] if clean else None,
        "clean_pl": clean["pl"] if clean else None,
        "clean_avg": clean["avg"] if clean else None,
    }


def _collect_forward_blocked_rids(fwd_rows: list, skills: dict) -> set:
    """前瞻失败笔中被当前规则覆盖（三路）的 rid 集合。纯函数便于单测。"""
    blocked: set = set()
    for row in fwd_rows:
        rid, label, _ret, fsj, sbj = row[0], row[1], row[2], row[3], row[4]
        if label != "failed":
            continue
        fs = json.loads(fsj) if fsj else {}
        sb = json.loads(sbj) if sbj else []
        if _failure_is_covered(fs, sb, skills):
            blocked.add(rid)
    return blocked


def _print_forward_veto_counterfactual(rows, skills, win_rate_baseline: float) -> None:
    """前瞻窗口内，剔除被当前规则覆盖的失败笔后重算胜率 + 净盈亏比。

    解决"D 项前瞻盈亏比低→每轮误判需加规则"的循环：前瞻窗口(FORWARD_EPOCH 之后)的
    formal run 含 veto/soft-ban commit 前生成的样本。直接看前瞻盈亏比会误判。

    覆盖口径与 _classify_failed_coverage / _failure_is_covered 一致（三路：
    prefilter + hard_veto/排除 + soft@震荡 ban）。T19：旧实现漏 soft ban。

    T20：pure 低线污染不在 strategy 覆盖路径（是 run 参数污染）。剔除三路后
    若 kept 仍含 base_score_line < 72 票，显式 residual_low_n + clean 子集指标，
    指向去污染反事实而非再加 entry。实证 07-16 前瞻：剔除 11 后 kept 17 中
    12 为 pure 低线，clean 仅 5 笔仍单日、不足 OOS。
    """
    if not FORWARD_EPOCH:
        return

    fwd = {row[0]: row for row in rows if row[0].startswith("rec_")}
    fwd_rows = [r for r in fwd.values() if _rid_trade_date(r[0]) >= FORWARD_EPOCH]
    if not fwd_rows:
        return

    blocked_rids = _collect_forward_blocked_rids(fwd_rows, skills)
    stats = _forward_veto_kept_stats(fwd_rows, blocked_rids)
    if stats is None:
        return
    pl_tag = (
        "✅" if stats["pl"] >= PL_RATIO_TARGET
        else ("⚠️" if stats["pl"] < PL_RATIO_BASELINE else "")
    )
    print(
        f'D 项前瞻规则覆盖反事实：前瞻窗口剔除 {stats["blocked_n"]} 笔被覆盖失败'
        f'（veto/prefilter/soft@震荡）后 '
        f'WR={stats["wr"]}% (n={stats["n"]}) 净盈亏比={stats["pl"]} {pl_tag} '
        f'平均收益={stats["avg"]}% '
        f'（对照前瞻未剔除：见上方"D 项(前瞻)"行）'
    )
    if stats["residual_low_n"] > 0:
        share = round(stats["residual_low_n"] * 100 / stats["n"], 0)
        clean_note = ""
        if stats["clean_n"] > 0 and stats["clean_wr"] is not None:
            clean_note = (
                f'；再剔 pure 低线后 n={stats["clean_n"]} '
                f'WR={stats["clean_wr"]}% 净PL={stats["clean_pl"]}'
            )
        print(
            f'     ——kept 中 pure 低线残留 {stats["residual_low_n"]}/{stats["n"]}'
            f'={share:.0f}%（run score_line < {FORMAL_SCORE_LINE_FLOOR}，'
            f'不在 strategy 覆盖路径）{clean_note}'
        )
        if stats["clean_n"] < MIN_OOS_SAMPLE:
            print(
                f'     ——clean 仅 {stats["clean_n"]} 笔 <{MIN_OOS_SAMPLE}：'
                f'覆盖反事实后的低 PL 仍被低线污染/单日主导，'
                f'非「规则覆盖无效」；与上方去污染反事实交叉看，勿加 entry'
            )
    elif stats["wr"] < win_rate_baseline - 10 and stats["n"] < 30:
        print(
            f'     ——覆盖反事实后 WR 仍低且 n 小：可能单日 regime；'
            f'与上方「去污染反事实·前瞻」交叉看，勿仅据此加 entry'
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
