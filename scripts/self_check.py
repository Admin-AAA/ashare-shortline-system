#!/usr/bin/env python3
"""self-improve 每轮自检脚本：输出 A/B/C/D/E 五项北极星指标摘要。

用法：
    cd backend && source .venv/bin/activate && python3 ../scripts/self_check.py
"""

import json
import math
import os
import sys
from collections import Counter
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


def check_a_item(db) -> None:
    """A 项：最近 7 天 formal 推荐产出（按交易日聚合，标出断档日）。"""
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

    ok_dates = sorted(d for d, ok in by_date.items() if ok)
    # 断档日 = 系统跑过 formal run 但当日无任何成功推荐（排除周末/假日——那几天没有 run）
    gap_dates = sorted(d for d, ok in by_date.items() if not ok)
    print(f'A 项：最近 7 天有 {len(ok_dates)} 个交易日产出 formal 推荐（目标 ≥5）')
    if gap_dates:
        print(f'⚠️ {len(gap_dates)} 个交易日系统跑了但 0 推荐（T12 排查: prefilter 过滤/数据缺失）: {gap_dates}')

    interrupted = [r for r in rows if r[2] == 'interrupted']
    if interrupted:
        print(f'⚠️ {len(interrupted)} 个 interrupted run')


def check_b_item(db) -> None:
    """B 项：最近推荐字段完整性（五项决策链：策略名+核心因子+入场/失效+LLM delta）。"""
    row = db.execute(text("""
        SELECT final_score, bucket, setup_type, entry_rule, invalid_rule,
               JSON_EXTRACT(feature_snapshot_json, '$.avg_cost')   AS avg_cost,
               JSON_EXTRACT(agent_summary_json,  '$.score_delta')  AS llm_delta,
               JSON_EXTRACT(agent_summary_json,  '$.stance')       AS llm_stance
        FROM recommendations
        ORDER BY recommended_at DESC
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


def _check_llm_route_health(db) -> None:
    """LLM delta 缺失时的路由健康诊断：查最近 llm_review_cache 命中情况 + 路由启用状态。"""
    try:
        cache_row = db.execute(text(
            "SELECT MAX(created_at) AS latest, "
            "TIMESTAMPDIFF(MINUTE, MAX(created_at), NOW()) AS mins_since_latest "
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

    # 用「距最近一次缓存命中的分钟数」而非「窗口内计数」——后者会被窗口边界外的历史记录
    # 掩盖真实空窗（如本轮修复前 10:34→15:30 长达 5h 空窗，24h 计数仍 >0 不会报警）。
    mins_since = cache_row[1] if cache_row and cache_row[1] is not None else None
    STALE_THRESHOLD_MIN = 180  # formal run 按小时触发，>3h 无命中即视为异常
    if mins_since is None or mins_since > STALE_THRESHOLD_MIN:
        hint = f'{mins_since}分钟前' if mins_since is not None else '从未命中过'
        print(f'  ⚠️ 最近一次 llm_review_cache 命中是 {hint}（>{STALE_THRESHOLD_MIN}分钟无命中，LLM 审查可能全局失效）')
        if not route_rows:
            print('    根因候选: model_route_config 无已启用路由')
        else:
            for r in route_rows:
                key_note = '自定义 key' if r[5] else '系统默认 key(.env ASHARE_AGENT_SKILL_API_KEY)'
                print(f'    路由 {r[1]} base_url={r[2]} model={r[3]} 使用{key_note}')
            print('    排查建议: 前端「设置→模型路由」点「测试」验证该 base_url 下系统默认 key 是否真实可用')
            print('    （常见根因：base_url 指向的 provider 与 .env key 发放方不一致，如 base_url=deepseek.com 但 key 是 jucode.cn 的）')


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


# 前瞻样本分界：自该交易日起，同日去重 + 弱市否决等修复已部署进生产，
# 其后的 formal run 才反映修复后的真实胜率。此前样本含修复前历史污染
# （如 2026-07-07/07-08 在去重与弱市否决 commit 之前生成的 run），
# 不应据此循环加规则（见 T3 失败归因 / 第160-163轮）。
FORWARD_EPOCH = "2026-07-09"


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
        return (f'⚠️ 护栏: 前瞻窗口(≥{FORWARD_EPOCH})样本不足，全量胜率 {full_wr}% 高于基线 '
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

    # 前瞻窗口（修复后）：FORWARD_EPOCH 之后的 run 才反映去重/弱市否决的真实胜率。
    # 若前瞻窗口胜率显著高于全量，说明全量下滑是历史污染而非真退化——
    # 切勿据此循环加规则（禁止过拟合，第164轮 T3 失败归因结论）。
    fwd_res = _compute_d_window(rows, since_date=FORWARD_EPOCH)
    _fmt_d(f'D 项(前瞻≥{FORWARD_EPOCH})', fwd_res, WIN_RATE_BASELINE, MAX_LOSS_BASELINE)
    _fmt_d_cost(f'D 项(前瞻≥{FORWARD_EPOCH})', _compute_d_cost(rows, since_date=FORWARD_EPOCH),
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
        _print_residual_poison_zone(uncovered)


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
    if (chg20 is not None and chg20 > 50) or (pos60 is not None and pos60 > 90):
        return "extension_chase"
    return "other"


def _print_residual_poison_zone(uncovered: list[dict]) -> None:
    """打印真残留毒区：未覆盖失败笔数 + 模式分布 + 各模式平均净收益 + 集中度提示。

    目的：把"下一轮 D 攻击的精确目标"显式化，避免每轮靠猜（历史污染已用 blocked 段处理，
    本段专盯真退化毒区）。模式映射复用 failure_attribution.classify_failure_mode 口径。
    """
    # 尝试用权威实现；失败（跨包导入脆弱）则回退内联镜像。
    classify = None
    try:
        import importlib.util
        fa_path = os.path.join(BACKEND_DIR, 'scripts', 'failure_attribution.py')
        if os.path.exists(fa_path):
            spec = importlib.util.spec_from_file_location('failure_attribution_lazy', fa_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            classify = mod.classify_failure_mode
    except Exception:
        classify = None

    modes: Counter = Counter()
    mode_rets: dict[str, list[float]] = {}
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
            by_date[rid.split('_')[1]] += 1

    print(f'D 项真残留毒区（未被任何规则覆盖）：{len(uncovered)} 笔（下一轮 D 攻击精确目标）')
    for m, c in modes.most_common():
        rets = mode_rets.get(m, [])
        avg = round(sum(rets) / len(rets), 2) if rets else 0.0
        print(f'  - {m}: {c} 笔（均净 {avg}%）')
    if by_date:
        top_date, top_n = by_date.most_common(1)[0]
        if top_n >= 3:
            print(f'  ⚠️ 单日集中：{top_date} {top_n} 笔（疑似单日 regime 风险，非个股因子可解）')

    # 数据质量标记：RSI=0 是上游数据污染（_f(None,0.0) 写库 + rsi_6 缺守卫），
    # 不是策略可解毒区。统计残留毒区里 RSI 污染笔占比，提示勿当策略毒区循环加规则。
    rsi_polluted = [u for u in uncovered
                    if u['fs'].get('rsi_6') == 0.0 and u['fs'].get('rsi_12') == 50.0]
    if rsi_polluted:
        print(f'  ℹ️ 其中 {len(rsi_polluted)} 笔为 RSI 数据污染（rsi_6=0 & rsi_12=50，'
              f'上游 stk_factor 缺失被 _f 转 0）— 数据质量问题，非策略可解，'
              f'feature_builder 已加 rsi_6/rsi_24 守卫清洗，勿当毒区加规则')


def main() -> None:
    db = SessionLocal()
    try:
        check_a_item(db)
        check_b_item(db)
        hit_skills = check_c_item(db)
        check_e_item(db, hit_skills)
        check_d_item(db)
    finally:
        db.close()


if __name__ == '__main__':
    main()
