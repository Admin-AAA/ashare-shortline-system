#!/usr/bin/env python3
"""T8 — LLM 层验证（模型内化，消耗 API 额度）。

两套验证，互补：
  A. 实时对比（T8.1，显式 --live 才启用）：对给定股票调用
     /api/runs/analyze-stock(include_llm=true)。
     缓存命中即秒回（llm_review_cache 已落库）；未命中才触发 grok-4.5 实时调用。
     提取 rule_score vs LLM score、决策是否翻转、token 消耗、prompt cache 命中。
  B. 缓存聚合（T8.2/T8.3）：遍历 llm_review_cache 全量，逐 skill 统计
     评分分布 / stance 分布 / evidence·risks 完整度 / stance→action 一致性违反率
     / 幻觉 heuristic（pos20>95 未进 risks、summary 孤立大数）。

纯函数（check_stance_action / review_quality_signals / aggregate_reviews）可单测，
不依赖 IO；DB 聚合与实时对比走 IO，仅在 __main__ 触发。

用法:
    python scripts/verify_t8_llm.py             # 仅跑缓存聚合（默认零额度）
    python scripts/verify_t8_llm.py --live --codes 603757 --max-live-requests 1 \
      --max-reported-tokens 20000 --date 2026-07-16

输出: output/t8_llm_verification_<date>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
BACKEND = "http://127.0.0.1:8765"
MAX_LIVE_CODES = 3

# SKILL.md 强约束：stance → action 强制映射（违反即无效）
STANCE_ACTION_OK = {
    "strong_support": {"确认后加仓", "轻仓试错", "观察"},
    "support": {"轻仓试错", "观察"},
    "neutral_support": {"轻仓试错", "观察"},
    "neutral": {"观察", "不做"},
    "oppose": {"观察", "不做"},
    "veto": {"观察", "不做"},
}
# SKILL.md 数据修正：score>=55 给 neutral 是亏损信号（WR 14.6%），禁止
NEUTRAL_SCORE_FLOOR = 55.0


# ───────────────────────── 纯函数（可单测） ─────────────────────────

def check_stance_action(stance: str | None, action: str | None) -> bool:
    """stance→action 是否一致。未知 stance/action 视为一致（不误报）。"""
    if not stance or not action:
        return True
    ok = STANCE_ACTION_OK.get(stance)
    if ok is None:
        return True
    return action in ok


def _numbers(text: str) -> list[float]:
    if not text:
        return []
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)]


def review_quality_signals(review: dict) -> dict:
    """对单条 LLM review 做质量体检，返回信号字典（不触发 IO）。"""
    stance = review.get("stance")
    action = review.get("action")
    evidence = review.get("evidence") or []
    risks = review.get("risks") or []
    summary = review.get("summary") or ""
    score = review.get("score")

    # 把 evidence/risks/summary 全部文本拼起来，做 pos20 过热检查
    blob = " ".join([summary] + list(evidence) + list(risks))

    # 规则11：pos20 > 95 必须在 risks 明确引用"过热"
    pos20_hot = None
    m = re.search(r"pos20[=：]?\s*(\d+(?:\.\d+)?)", blob)
    if m:
        v = float(m.group(1))
        if v > 95:
            pos20_hot = v
    pos20_flagged = bool(pos20_hot) and ("过热" in " ".join(risks) or "pos20" in " ".join(risks))

    # 幻觉 heuristic：summary 中出现 >30 的孤立大数（不在证据文本里）
    summary_nums = _numbers(summary)
    evidence_blob = " ".join(list(evidence) + list(risks))
    evidence_nums = set(_numbers(evidence_blob))
    halluc_suspects = [
        n for n in summary_nums
        if abs(n) > 30 and not any(abs(n - e) < max(1.0, abs(e) * 0.05) for e in evidence_nums)
    ]

    return {
        "stance": stance,
        "action": action,
        "score": score,
        "stance_action_ok": check_stance_action(stance, action),
        "neutral_with_high_score": (stance == "neutral" and isinstance(score, (int, float)) and score >= NEUTRAL_SCORE_FLOOR),
        "has_evidence": len(evidence) > 0,
        "has_risks": len(risks) > 0,
        "summary_nonempty": bool(summary.strip()),
        "pos20_hot": pos20_hot,
        "pos20_flagged_in_risks": pos20_flagged,
        "hallucination_suspects": halluc_suspects[:8],
    }


def aggregate_reviews(reviews: list[dict]) -> dict:
    """逐 skill 聚合质量信号。reviews: 已解析的 review_json 列表（含 skill_id）。"""
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for r in reviews:
        sid = r.get("_skill_id", "unknown")
        by_skill[sid].append(r)

    out = {}
    for sid, rs in by_skill.items():
        n = len(rs)
        scores = [r.get("score") for r in rs if isinstance(r.get("score"), (int, float))]
        stances = [r.get("stance") for r in rs if r.get("stance")]
        stance_ct = defaultdict(int)
        for s in stances:
            stance_ct[s] += 1
        sa_viol = sum(1 for r in rs if not review_quality_signals(r)["stance_action_ok"])
        neutral_high = sum(1 for r in rs if review_quality_signals(r)["neutral_with_high_score"])
        no_ev = sum(1 for r in rs if not review_quality_signals(r)["has_evidence"])
        no_risk = sum(1 for r in rs if not review_quality_signals(r)["has_risks"])
        empty_sum = sum(1 for r in rs if not review_quality_signals(r)["summary_nonempty"])
        pos20_unflagged = sum(
            1 for r in rs
            if (q := review_quality_signals(r))["pos20_hot"] is not None and not q["pos20_flagged_in_risks"]
        )
        # token 消耗（仅对含 _token_cost 的新条目有效；历史条目无此字段）
        tok_real = [r["_token_cost"]["total_tokens_real"] for r in rs if isinstance(r.get("_token_cost"), dict) and r["_token_cost"].get("total_tokens_real")]
        cache_hits = [r["_token_cost"]["total_cache_hit_tokens"] for r in rs if isinstance(r.get("_token_cost"), dict) and r["_token_cost"].get("total_cache_hit_tokens")]
        out[sid] = {
            "n": n,
            "score_mean": round(sum(scores) / len(scores), 2) if scores else None,
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "stance_dist": dict(stance_ct),
            "stance_action_violations": sa_viol,
            "stance_action_violation_rate": round(sa_viol / n, 3) if n else 0,
            "neutral_with_high_score": neutral_high,
            "missing_evidence": no_ev,
            "missing_risks": no_risk,
            "empty_summary": empty_sum,
            "pos20_hot_unflagged": pos20_unflagged,
            "n_with_token_cost": len(tok_real),
            "tokens_real_total": sum(tok_real) if tok_real else 0,
            "cache_hit_tokens_total": sum(cache_hits) if cache_hits else 0,
        }
    return out


# ───────────────────────── IO 层（仅 __main__ 触发） ─────────────────────────

def _post(path: str, payload: dict, timeout: int = 200) -> dict:
    req = Request(
        f"{BACKEND}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _reported_usage(api_costs: list[dict]) -> dict[str, int | None]:
    """Aggregate provider-reported usage without turning missing fields into zero."""
    out: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "cache_hit_tokens"):
        values = [item.get(field) for item in api_costs if item.get(field) is not None]
        out[field] = sum(int(value) for value in values) if values else None
    prompt = out["prompt_tokens"]
    completion = out["completion_tokens"]
    out["total_tokens"] = (
        int(prompt or 0) + int(completion or 0)
        if prompt is not None or completion is not None
        else None
    )
    return out


def verify_live(code: str, trade_date: str | None) -> dict:
    """实时对比：缓存命中秒回，未命中触发 grok-4.5。"""
    payload = {"code": code, "trade_date": trade_date, "include_llm": True}
    try:
        resp = _post("/api/runs/analyze-stock", payload)
    except Exception as exc:  # 超时/网络 → 降级记录，不中断整体
        return {"code": code, "live_error": str(exc)[:160], "from_cache": None}
    la = resp.get("llm_analysis", {}) or {}
    usage = _reported_usage(la.get("api_costs") or [])
    return {
        "code": code,
        "name": resp.get("name"),
        "from_cache": resp.get("market_snapshot_source") is None,  # 简易判断
        "rule_score": la.get("rule_score"),
        "llm_score": la.get("score"),
        "llm_adjustment": la.get("llm_adjustment"),
        "stance": la.get("stance"),
        "confidence": la.get("confidence"),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "tokens_real": usage["total_tokens"],
        "cache_hit_tokens": usage["cache_hit_tokens"],
        "prompt_cache_active": la.get("prompt_cache_active", False),
        "status": la.get("status"),
    }


def fetch_cached_reviews() -> list[dict]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.db.session import SessionLocal
    from app.models.tables import LLMReviewCache

    db = SessionLocal()
    try:
        rows = db.query(LLMReviewCache).all()
        out = []
        for r in rows:
            try:
                rj = json.loads(r.review_json)
            except Exception:
                continue
            rj["_skill_id"] = r.skill_id
            rj["_code"] = r.code
            rj["_trade_date"] = r.trade_date
            rj["_model"] = r.model
            out.append(rj)
        return out
    finally:
        db.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="603757,002241,002292")
    ap.add_argument("--date", default=None)
    ap.add_argument(
        "--live",
        action="store_true",
        help="显式启用真实 LLM Mini Canary；默认只分析已有缓存，零真实额度",
    )
    ap.add_argument(
        "--max-live-requests",
        type=int,
        default=1,
        choices=range(1, MAX_LIVE_CODES + 1),
        metavar="1..3",
        help="真实单股请求硬上限（默认 1，最大 3）",
    )
    ap.add_argument(
        "--max-reported-tokens",
        type=int,
        default=20000,
        help="provider 已报告 token 达到该值后停止后续请求（默认 20000）",
    )
    args = ap.parse_args(argv)
    if args.max_reported_tokens <= 0:
        ap.error("--max-reported-tokens must be > 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    trade_date = args.date
    report: dict = {"task": "T8", "generated_at": _date.today().isoformat()}

    # B. 缓存聚合（T8.2/T8.3，全量，零额度）
    reviews = fetch_cached_reviews()
    report["cache_total"] = len(reviews)
    report["models"] = sorted({r.get("_model") for r in reviews if r.get("_model")})
    report["per_skill"] = aggregate_reviews(reviews)
    report["live_budget"] = {
        "enabled": args.live,
        "request_limit": args.max_live_requests if args.live else 0,
        "reported_token_stop_limit": args.max_reported_tokens if args.live else 0,
    }
    report["live_usage"] = {
        "requests_started": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tokens_reported": 0,
        "cache_hit_tokens": 0,
        "token_usage_missing_requests": 0,
        "cost": 0,
    }

    # A. 实时对比（T8.1，显式 --live 才打开；默认零真实额度）
    if args.live:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        codes = list(dict.fromkeys(codes))[: min(args.max_live_requests, MAX_LIVE_CODES)]
        live = []
        reported_tokens = 0
        missing_token_usage = 0
        for code in codes:
            try:
                item = verify_live(code, trade_date)
            except Exception as exc:
                item = {"code": code, "live_error": str(exc)[:160]}
            live.append(item)
            tokens = item.get("tokens_real")
            if isinstance(tokens, (int, float)):
                reported_tokens += int(tokens)
            else:
                missing_token_usage += 1
            if item.get("live_error") or reported_tokens >= args.max_reported_tokens:
                break
        report["live_comparison"] = live
        report["live_budget"] = {
            "enabled": True,
            "request_limit": args.max_live_requests,
            "reported_token_stop_limit": args.max_reported_tokens,
        }
        report["live_usage"] = {
            "requests_started": len(live),
            "prompt_tokens": (
                sum(int(item["prompt_tokens"]) for item in live)
                if live and all(item.get("prompt_tokens") is not None for item in live)
                else None
            ),
            "completion_tokens": (
                sum(int(item["completion_tokens"]) for item in live)
                if live and all(item.get("completion_tokens") is not None for item in live)
                else None
            ),
            "tokens_reported": reported_tokens if not missing_token_usage else None,
            "cache_hit_tokens": (
                sum(int(item["cache_hit_tokens"]) for item in live)
                if live and all(item.get("cache_hit_tokens") is not None for item in live)
                else None
            ),
            "token_usage_missing_requests": missing_token_usage,
            "cost": None,
        }

    out = ROOT / "output" / f"t8_llm_verification_{_date.today().isoformat()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[written] {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
