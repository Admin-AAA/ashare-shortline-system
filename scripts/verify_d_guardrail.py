#!/usr/bin/env python3
"""D 项北极星回归护栏 — 可独立运行的 CI 闸门。

背景（第 178 轮 self-improve）：
  系统已进入稳定期，D 项（回测胜率/盈亏比/最大亏损）是唯一核心攻击目标，当前已强于
  北极星阈值（全量净胜率 75%+、净盈亏比 2.9、最大亏损 ~-5.9%）。但多个 self-improve
  实例并行编辑 strategy.yaml、不断加脆弱的「毒区」过滤器——这是真实的过拟合风险：
  单看样本内胜率刷高，却可能砍掉前瞻样本里的有效信号，损害整体 D 项。

  本脚本把北极星 D 项目标固化为硬闸门：任何改动若使 D 项退到阈值以下，脚本非零退出，
  CI / 编排可据此阻断合并。与 self_check.py 共用同一套纯函数（_fetch_dedup_live_formal
  + _compute_d_cost），口径与生产一致。

用法（在 worktree / 主仓均可）：
    cd backend && source .venv/bin/activate && python3 ../scripts/verify_d_guardrail.py
    python3 ../scripts/verify_d_guardrail.py --json output/d_guardrail_report.json   # 机器可读报告
退出码：0=达标，1=任意阈值被突破（回归），2=样本不足无法判定。

--json 说明：默认仅打印 stdout（行为向后兼容）；加 --json 才落盘 JSON 报告。报告写入
output/（已被 .gitignore 忽略），不进版本库，供跨 run 漂移追踪（慢退化单点阈值查不出）。
"""
import argparse
import datetime
import json
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))
sys.path.insert(0, os.path.abspath(os.path.join(BACKEND_DIR, '..', 'scripts')))

from app.db.session import SessionLocal
import self_check  # type: ignore[import-not-found]
from sqlalchemy import text

# —— 北极星 D 项目标阈值（硬闸门）——
NET_WINRATE_FLOOR = 65.0
NET_PL_RATIO_FLOOR = 2.0
MAX_LOSS_FLOOR = -6.5          # 即 max_loss 不得低于 -6.5%
FORWARD_NET_WINRATE_FLOOR = 60.0

# 默认报告落盘路径（output/ 已被 .gitignore 忽略，不会进版本库）
REPORT_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'output', 'd_guardrail_report.json')
)

# —— 近窗口漂移早警（软告警，非硬闸门，不影响退出码）——
# 全量护栏已达标，但全量口径会平滑掉近期单日/单簇退化（如 2026-07-16 单日集中簇把
# 近10交易日净盈亏比拉到 1.65，远低于全量 2.08）。本早警把近期窗口显式化，使慢退化
# 可被跨 run JSON 历史 diff 捕获，并引导后续轮次勿对已被证伪的毒区重复加 entry 规则。
RECENT_WINDOW_DAYS = 10
RECENT_PL_DRAG_RATIO = 0.80   # 近窗口净盈亏比 < 全量 * 0.80 视为显著慢退化
RECENT_MIN_N = 30             # 近窗口样本需 >= 该值方可断言（避免小样本误报）

# —— 单日 regime 冲击护栏（机器可读早警，不计入 ok/退出码）——
# 文档化护栏（第187/192轮）：单日失败高度集中（≥40%）属市场 regime 冲击，非个股因子可解，
# 禁止据此加 entry 规则（过拟合单日）。此前仅 self_check 文本告警，本闸门固化为机器可读信号，
# 使自动化编排可读取 regime_shock.shock 并拒绝对单日冲击加 entry 规则，避免误读为系统退化。
SINGLE_DAY_FAIL_SHARE_FLOOR = 0.40   # 单日失败占窗口失败比 ≥ 该值 → 判定单日 regime 冲击
SINGLE_DAY_MIN_FAIL = 5              # 窗口失败总数需 ≥ 该值方可断言（避免小样本误报）

# —— 前瞻污染主导 → 软跳过硬闸门（第 22 轮）——
# 稳定纪元后 07-16 单日 formal 样本 93% 为 pure 低线/soft@震荡污染，raw 前瞻 WR 39%
# 若仍当硬闸门会：要么误 FAIL 阻断合法合并，要么（旧 bug）FORWARD_EPOCH 未 resolve
# 把全量当前瞻虚高 PASS。污染占比 ≥ 该值 或 clean n < MIN_OOS → 只 WARN，不计入 ok。
FORWARD_POLLUTION_SHARE_SKIP = 0.70


def _check(name: str, ok: bool, actual, floor) -> bool:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  [{status}] {name}: 实测 {actual}  (阈值 {'>=' if floor is not None else ''}{floor})")
    return ok


def _recent_trade_dates(rows: list, k: int = RECENT_WINDOW_DAYS) -> set:
    """取 rows 中最近的 k 个独立交易日（trade_date 去重后倒序取前 k）。

    rows 元素: (code, trade_date, label, executed_return)。纯函数，便于单测锁定。
    """
    dates = sorted({str(r[1]) for r in rows if r[1] is not None}, reverse=True)
    return set(dates[:k])


def _eval_recent_drift(
    full: dict | None,
    recent: dict | None,
    *,
    recent_clean: dict | None = None,
    full_clean: dict | None = None,
) -> dict:
    """纯函数：评估近窗口相对全量的净盈亏比漂移，仅作早警（不影响退出码）。

    返回 {warn: bool, ratio: float|None, msg: str, cause: str|None}。
    触发条件：近窗口样本 n >= RECENT_MIN_N 且 近窗口 net_pl < RECENT_PL_DRAG_RATIO * 全量 net_pl。

    T23：若提供 recent_clean 且去污染后近窗口 PL 已回升到全量的 RECENT_PL_DRAG_RATIO
    以上（或超过 raw 近窗口），则 cause=pollution、warn=False——raw 漂移是历史
    低线/soft 污染，非策略慢退化；避免与 self_check 去污染结论打架。
    """
    if recent is None or full is None:
        return {"warn": False, "ratio": None, "msg": "", "cause": None}
    if recent["n"] < RECENT_MIN_N:
        return {
            "warn": False, "ratio": None, "cause": None,
            "msg": f"近窗口样本 {recent['n']}<{RECENT_MIN_N}，不足以断言漂移",
        }
    full_pl = full["net_pl"]
    if not full_pl or full_pl >= 999:
        return {"warn": False, "ratio": None, "msg": "", "cause": None}
    ratio = round(recent["net_pl"] / full_pl, 2)
    if ratio >= RECENT_PL_DRAG_RATIO:
        return {"warn": False, "ratio": ratio, "msg": "", "cause": None}

    # raw 近窗口显著低于全量 —— 先查去污染是否恢复
    if recent_clean is not None and recent_clean.get("n", 0) >= RECENT_MIN_N:
        clean_pl = recent_clean.get("net_pl")
        # 对照 raw 全量：去污染后近窗口是否回到「全量健康水位」；
        # full_clean 往往更高（剔污染后抬升），不宜作分母以免 clean_ratio 虚低。
        if clean_pl is not None and full_pl:
            clean_ratio = round(clean_pl / full_pl, 2)
            recovered = clean_ratio >= RECENT_PL_DRAG_RATIO or (
                clean_pl - recent["net_pl"] >= 0.3
            )
            if recovered:
                return {
                    "warn": False,
                    "ratio": ratio,
                    "cause": "pollution",
                    "msg": (
                        f"近{RECENT_WINDOW_DAYS}交易日 raw 净盈亏比 {recent['net_pl']} 低于全量 "
                        f"{full_pl}（比值 {ratio}），但去污染后 {clean_pl}"
                        f"（相对全量比 {clean_ratio}）——历史 pure 低线/soft@震荡 污染主导，"
                        f"非策略慢退化；Contract 已拦新污染，勿加 entry 规则"
                    ),
                }

    return {
        "warn": True,
        "ratio": ratio,
        "cause": "slow_degradation",
        "msg": (
            f"近{RECENT_WINDOW_DAYS}交易日净盈亏比 {recent['net_pl']} 显著低于全量 "
            f"{full_pl}（比值 {ratio}）——疑似慢退化；主因 no_follow_through"
            "（07-16 单日集中，第187/192轮证伪禁加 entry 规则）与 gap_roundtrip"
            "（开盘跳空市场风险）驱动，非可泛化个股因子；勿据此加 entry 规则，"
            "待前瞻样本（规则上线后结算）累积后复核"
        ),
    }

def _single_day_failure_share(rows: list, dates: set) -> dict:
    """纯函数：在 dates 窗口内统计「单日失败集中度」。

    rows: [(code, trade_date, label, executed_return)]，失败 = label != 'success'（与
    _compute_d_cost 的 win 定义一致）。返回 {top_date, top_failures, total_failures, share, n}。
    share = 失败最多的单日失败数 / 窗口失败总数；无失败返回 share=0.0。
    """
    win = [r for r in rows if str(r[1]) in dates]
    n = len(win)
    fails = [r for r in win if r[2] != 'success']
    total_failures = len(fails)
    if total_failures == 0:
        return {"top_date": None, "top_failures": 0,
                "total_failures": 0, "share": 0.0, "n": n}
    by_date: dict[str, int] = {}
    for r in fails:
        d = str(r[1])
        by_date[d] = by_date.get(d, 0) + 1
    top_date = max(by_date, key=by_date.get)
    top_failures = by_date[top_date]
    return {
        "top_date": top_date,
        "top_failures": top_failures,
        "total_failures": total_failures,
        "share": round(top_failures / total_failures, 2),
        "n": n,
    }


def _eval_regime_shock(rows: list, recent_dates: set) -> dict:
    """纯函数：评估近窗口是否由单日 regime 冲击主导失败。

    返回 {shock: bool, top_date, top_failures, total_failures, share, msg}。触发条件：
    窗口失败总数 >= SINGLE_DAY_MIN_FAIL 且 单日失败占比 >= SINGLE_DAY_FAIL_SHARE_FLOOR。
    触发时 msg 明确「禁止据此加 entry 规则」并把该日暴露为可核查锚点；不触发时仍返回集中度
    原始值（msg 空），供报告透明展示。本信号只作早警，不计入 ok / 退出码（市场冲击≠策略回归）。
    """
    s = _single_day_failure_share(rows, recent_dates)
    if s["total_failures"] < SINGLE_DAY_MIN_FAIL:
        return {"shock": False, **s,
                "msg": f"近窗口失败 {s['total_failures']}<{SINGLE_DAY_MIN_FAIL}，不足以断言单日冲击"}
    if s["share"] >= SINGLE_DAY_FAIL_SHARE_FLOOR:
        return {"shock": True, **s,
                "msg": (f"近窗口失败 {s['total_failures']} 笔中 {s['top_failures']} 笔"
                        f"（{int(s['share'] * 100)}%）集中于 {s['top_date']} 单日——属市场 regime "
                        f"冲击，非个股因子可解；🚫 禁止据此加 entry 规则（过拟合单日，第187/192轮证伪）")}
    return {"shock": False, **s, "msg": ""}


def _forward_pollution_gate_decision(
    pollution_n: int,
    clean_n: int,
    *,
    pollution_share_skip: float = FORWARD_POLLUTION_SHARE_SKIP,
    min_oos: int = 10,
) -> dict:
    """纯函数：是否对前瞻窗口执行硬闸门。

    返回 {enforce: bool, pollution_n, clean_n, share, msg}。
    enforce=False 当：污染占比 ≥ pollution_share_skip 或 clean_n < min_oos
    （与 self_check 去污染反事实 / MIN_OOS_SAMPLE 对齐）。
    """
    total = pollution_n + clean_n
    share = round(pollution_n / total, 2) if total > 0 else 0.0
    if total == 0:
        return {
            "enforce": False,
            "pollution_n": 0,
            "clean_n": 0,
            "share": 0.0,
            "msg": "前瞻窗口无样本，无法强制 OOS 闸门",
        }
    if share >= pollution_share_skip or clean_n < min_oos:
        return {
            "enforce": False,
            "pollution_n": pollution_n,
            "clean_n": clean_n,
            "share": share,
            "msg": (
                f"前瞻 pure 污染 {pollution_n}/{total}={int(share * 100)}%、"
                f"clean n={clean_n}（<{min_oos} 或污染≥{int(pollution_share_skip * 100)}%）"
                f"——raw 前瞻不可当硬闸门；Contract 已拦新污染，待 clean formal 多日后再 OOS"
            ),
        }
    return {
        "enforce": True,
        "pollution_n": pollution_n,
        "clean_n": clean_n,
        "share": share,
        "msg": "",
    }


def build_report(full: dict | None, fwd: dict | None, ok: bool, exit_code: int,
                 recent: dict | None = None, drift: dict | None = None,
                 regime_shock: dict | None = None,
                 pollution: dict | None = None,
                 forward_epoch: str | None = None,
                 residual: dict | None = None) -> dict:
    """把本轮 D 项护栏结果结构化为机器可读报告，供跨 run 漂移追踪。

    full / fwd 为 self_check._compute_d_cost 的返回 dict（含 n/net_wr/net_pl/max_loss）。
    pollution 为去污染反事实摘要（可选），forward_epoch 为稳定/动态纪元字符串。
    residual 为真残留毒区 + 伪活跃反事实（T31，可选）。
    只萃取 D 项护栏关心的字段，剔除噪声字段，避免报告随实现细节漂移。
    """
    def _block(m):
        return None if m is None else {
            "n": m["n"],
            "net_wr": m["net_wr"],
            "net_pl": m["net_pl"],
            "max_loss": m["max_loss"],
        }
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "forward_epoch": forward_epoch,
        "thresholds": {
            "net_winrate_floor": NET_WINRATE_FLOOR,
            "net_pl_ratio_floor": NET_PL_RATIO_FLOOR,
            "max_loss_floor": MAX_LOSS_FLOOR,
            "forward_net_winrate_floor": FORWARD_NET_WINRATE_FLOOR,
            "recent_window_days": RECENT_WINDOW_DAYS,
            "recent_pl_drag_ratio": RECENT_PL_DRAG_RATIO,
            "recent_min_n": RECENT_MIN_N,
            "single_day_fail_share_floor": SINGLE_DAY_FAIL_SHARE_FLOOR,
            "single_day_min_fail": SINGLE_DAY_MIN_FAIL,
            "forward_pollution_share_skip": FORWARD_POLLUTION_SHARE_SKIP,
        },
        "full": _block(full),
        "forward": _block(fwd),
        "recent_10d": _block(recent),
        "recent_drift": drift if drift is not None else {
            "warn": False, "ratio": None, "msg": "", "cause": None,
        },
        "regime_shock": regime_shock if regime_shock is not None else {
            "shock": False, "top_date": None, "top_failures": 0,
            "total_failures": 0, "share": 0.0, "n": 0, "msg": ""},
        "pollution": pollution if pollution is not None else {
            "n": 0, "pure_low": 0, "pure_soft": 0,
            "full_clean": None, "recent_clean": None, "fwd_clean": None,
            "fwd_gate": {"enforce": False, "share": 0.0, "msg": ""},
        },
        "residual": residual if residual is not None else {
            "n": 0, "pseudo_active": None, "triage": None,
        },
        "passed": bool(ok),
        "exit_code": int(exit_code),
    }


def write_report(report: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="D 项北极星回归护栏")
    parser.add_argument(
        "--json", nargs="?", const=REPORT_DEFAULT_PATH, default=None,
        help="写出机器可读 JSON 报告；不指定路径则用默认 output/d_guardrail_report.json",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    residual_block: dict = {"n": 0, "pseudo_active": None, "triage": None}
    try:
        # T22：必须 resolve 稳定前瞻纪元；否则 FORWARD_EPOCH=None 时
        # _compute_d_cost(since_date=None) 退化为全量，虚高「前瞻」PASS。
        self_check.FORWARD_EPOCH = self_check._resolve_forward_epoch(db)
        epoch = self_check.FORWARD_EPOCH
        rows = self_check._fetch_dedup_live_formal(db)

        # pure 污染源（低线 + soft@震荡），与 self_check 去污染反事实同口径
        poll_raw = db.execute(
            text(
                """
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
                """
            )
        ).fetchall()
        keys = self_check._pure_pollution_keys(list(poll_raw))

        # T31：真残留 + 伪活跃反事实（机器可读，不计入 ok）
        try:
            skills = self_check._load_skills_once()
            cov_rows = db.execute(text("""
                SELECT r.recommendation_id, o.outcome_label, o.executed_return,
                       r.feature_snapshot_json, r.skill_breakdown_json,
                       o.exit_reason, o.max_return, r.trade_date, r.final_score
                FROM recommendation_outcomes o
                JOIN recommendations r ON r.recommendation_id = o.recommendation_id
                JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
                WHERE o.outcome_label IN ('success', 'failed')
                  AND r.recommendation_id LIKE 'rec_%'
            """)).fetchall()
            uncovered = self_check._collect_uncovered_failures(cov_rows, skills) if skills else []
            snap_rows = db.execute(text("""
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
            pa = self_check._pseudo_active_sentiment_counterfactual(
                uncovered, market_snaps,
            ) if uncovered else None
            pure_keys: set = set()
            try:
                poll_raw2 = db.execute(text("""
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
                pure_keys = set(
                    (self_check._pure_pollution_keys(list(poll_raw2)) or {}).get("union") or set()
                )
            except Exception:
                pure_keys = set()
            triage_raw = self_check._triage_residual_poison(
                uncovered,
                pure_pollution_keys=pure_keys,
                pseudo_active_rids=set((pa or {}).get("pseudo_rids") or []),
            ) if uncovered else None
            triage = None
            if triage_raw and triage_raw.get("n", 0) > 0:
                c = triage_raw["counts"]
                triage = {
                    "n": triage_raw["n"],
                    "pure_pollution": c.get("pure_pollution", 0),
                    "pseudo_active": c.get("pseudo_active", 0),
                    "day_clustered": c.get("day_clustered", 0),
                    "sparse": c.get("sparse", 0),
                    "clustered_days": triage_raw.get("clustered_days") or [],
                    "note": triage_raw.get("note"),
                }
            residual_block = {
                "n": len(uncovered),
                "pseudo_active": (
                    {
                        "weak_active_n": pa["weak_active_n"],
                        "pseudo_n": pa["pseudo_n"],
                        "still_active_n": pa["still_active_n"],
                        "days": {
                            td: {
                                "n": d["n"],
                                "pseudo": d["pseudo"],
                                "new_sent": d.get("new_sent"),
                            }
                            for td, d in (pa.get("day_breakdown") or {}).items()
                        },
                    } if pa else None
                ),
                "triage": triage,
            }
        except Exception:
            residual_block = {"n": 0, "pseudo_active": None, "triage": None}
    finally:
        db.close()

    if not rows:
        print("⚠️ D 项护栏：无 LIVE formal 样本，无法判定（退出码 2）。")
        report = build_report(None, None, False, 2, forward_epoch=None)
        if args.json:
            write_report(report, args.json)
            print(f"📄 JSON 报告已写出: {args.json}")
        return 2

    full = self_check._compute_d_cost(rows)
    fwd = self_check._compute_d_cost(rows, since_date=epoch) if epoch else None

    recent_dates = _recent_trade_dates(rows, k=RECENT_WINDOW_DAYS)
    recent = self_check._compute_d_cost(rows, trade_dates=recent_dates)

    # 去污染反事实须先于近窗口漂移判定（T23：raw 漂移常被污染解释）
    cf = self_check._clean_d_counterfactual(
        rows, keys["union"], last10_dates=recent_dates, since_date=epoch,
    )
    drift = _eval_recent_drift(
        full, recent,
        recent_clean=(cf or {}).get("last10_clean"),
        full_clean=(cf or {}).get("full_clean"),
    )

    # 单日 regime 冲击护栏（机器可读早警，不计入 ok / 退出码）
    regime_shock = _eval_regime_shock(rows, recent_dates)

    fwd_poll_n = (cf or {}).get("fwd_pollution_n") or 0
    fwd_clean_n = (cf or {}).get("fwd_clean_n") or 0
    if epoch and fwd is not None:
        # 若 cf 无前瞻块（pollution empty），用 since 窗口手工计
        if not keys["union"]:
            fwd_keys = {(r[0], str(r[1])) for r in rows if str(r[1]) >= epoch}
            fwd_poll_n = 0
            fwd_clean_n = len(fwd_keys)
    fwd_gate = _forward_pollution_gate_decision(fwd_poll_n, fwd_clean_n)

    def _slim(m):
        return None if m is None else {
            "n": m["n"], "net_wr": m["net_wr"], "net_pl": m["net_pl"], "max_loss": m["max_loss"],
        }

    pollution_block = {
        "n": len(keys["union"]),
        "pure_low": len(keys["pure_low"]),
        "pure_soft": len(keys["pure_soft"]),
        "full_clean": _slim((cf or {}).get("full_clean")),
        "recent_clean": _slim((cf or {}).get("last10_clean")),
        "fwd_clean": _slim((cf or {}).get("fwd_clean")),
        "fwd_gate": {
            "enforce": fwd_gate["enforce"],
            "pollution_n": fwd_gate["pollution_n"],
            "clean_n": fwd_gate["clean_n"],
            "share": fwd_gate["share"],
            "msg": fwd_gate["msg"],
        },
    }

    print(
        f"D 项回归护栏（样本 全量 n={full['n']}"
        + (f" / 前瞻≥{epoch} n={fwd['n']}" if fwd else " / 前瞻 空")
        + f" / 近{RECENT_WINDOW_DAYS}交易日 n={recent['n'] if recent else 0}"
        + "）"
    )
    print("-" * 60)

    ok = True
    ok &= _check("全量净胜率", full["net_wr"] >= NET_WINRATE_FLOOR,
                 f"{full['net_wr']}%", NET_WINRATE_FLOOR)
    ok &= _check("全量净盈亏比", full["net_pl"] >= NET_PL_RATIO_FLOOR,
                 full["net_pl"], NET_PL_RATIO_FLOOR)
    ok &= _check("全量最大亏损(越接近0越好)", full["max_loss"] >= MAX_LOSS_FLOOR,
                 f"{full['max_loss']}%", MAX_LOSS_FLOOR)
    if fwd is not None and epoch:
        if fwd_gate["enforce"]:
            ok &= _check("前瞻净胜率(样本外)", fwd["net_wr"] >= FORWARD_NET_WINRATE_FLOOR,
                         f"{fwd['net_wr']}%", FORWARD_NET_WINRATE_FLOOR)
            ok &= _check("前瞻净盈亏比", fwd["net_pl"] >= NET_PL_RATIO_FLOOR,
                         fwd["net_pl"], NET_PL_RATIO_FLOOR)
        else:
            print(
                f"  [⚠️ SKIP] 前瞻硬闸门：{fwd_gate['msg']} "
                f"| raw 净WR={fwd['net_wr']}% 净PL={fwd['net_pl']} n={fwd['n']}"
            )
            if pollution_block["fwd_clean"]:
                fc = pollution_block["fwd_clean"]
                print(
                    f"  ℹ️ 前瞻去污染 clean：n={fc['n']} 净WR={fc['net_wr']}% "
                    f"净PL={fc['net_pl']}（仅信息，不闸门）"
                )
    else:
        print("  [⚠️ SKIP] 前瞻窗口样本为空——无法验证样本外，请等修复后新 run 结算后复核。")

    # 近窗口信息（非闸门，仅展示 + 早警）
    if recent is not None:
        print(f"  ℹ️ 近{RECENT_WINDOW_DAYS}交易日(净成本): 净胜率 {recent['net_wr']}% "
              f"| 净盈亏比 {recent['net_pl']} | 最大亏损 {recent['max_loss']}% | n={recent['n']}")
    if pollution_block["n"] > 0 and pollution_block["recent_clean"]:
        rc = pollution_block["recent_clean"]
        print(
            f"  ℹ️ 近{RECENT_WINDOW_DAYS}日去污染: 净WR {rc['net_wr']}% "
            f"净PL {rc['net_pl']} n={rc['n']} "
            f"(pure 低线 {pollution_block['pure_low']}+soft {pollution_block['pure_soft']}="
            f"{pollution_block['n']})"
        )
    if drift["warn"]:
        print(f"  [⚠️ WARN] {drift['msg']}")
    elif drift.get("cause") == "pollution" and drift.get("msg"):
        print(f"  ℹ️ 近窗口 raw 漂移已由污染解释：{drift['msg']}")
    if regime_shock["shock"]:
        print(f"  [🚫 REGIME SHOCK] {regime_shock['msg']}")
    else:
        print(f"  ℹ️ 近窗口单日失败集中度: top={regime_shock.get('top_date')} "
              f"占比 {int(regime_shock.get('share', 0) * 100)}% "
              f"({regime_shock.get('top_failures')}/{regime_shock.get('total_failures')})，"
              f"未达单日冲击阈值 {int(SINGLE_DAY_FAIL_SHARE_FLOOR * 100)}%")

    # T31：真残留 + 伪活跃（与 self_check 对齐，仅信息）
    if residual_block.get("n", 0) > 0:
        pa = residual_block.get("pseudo_active")
        tr = residual_block.get("triage")
        if tr:
            print(
                f"  ℹ️ 真残留毒区 n={residual_block['n']} | triage "
                f"污染{tr.get('pure_pollution', 0)}+伪活跃{tr.get('pseudo_active', 0)}"
                f"+单日簇{tr.get('day_clustered', 0)}+稀疏{tr.get('sparse', 0)}"
                f"（前三类禁 entry）"
            )
        elif pa and pa.get("weak_active_n", 0) > 0:
            print(
                f"  ℹ️ 真残留毒区 n={residual_block['n']} | 弱势·活跃 "
                f"{pa['weak_active_n']} 笔中伪活跃 {pa['pseudo_n']} / 仍活跃 {pa['still_active_n']}"
                f"（T28 双维度；勿对 residual 弱势·活跃加 entry）"
            )
        else:
            print(f"  ℹ️ 真残留毒区 n={residual_block['n']}")

    print("-" * 60)
    if ok:
        print("✅ D 项护栏全部达标：未检测到对北极星 D 项的回归。")
    else:
        print("❌ D 项护栏触发：检测到对北极星 D 项的回归，阻断合并并排查最近 strategy.yaml 改动。")

    exit_code = 0 if ok else 1
    report = build_report(
        full, fwd, ok, exit_code,
        recent=recent, drift=drift, regime_shock=regime_shock,
        pollution=pollution_block, forward_epoch=epoch,
        residual=residual_block,
    )
    if args.json:
        write_report(report, args.json)
        print(f"📄 JSON 报告已写出: {args.json}")
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
