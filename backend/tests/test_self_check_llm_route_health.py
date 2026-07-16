"""Regression guard: self_check B 项 LLM delta 缺失诊断（第161轮 self-improve 误报修复）。

背景: self_check B 项原取最近一条推荐（含 experiment 曝光补跑），其 agent_summary
是空骨架（skip_aux_refresh=True 不走 LLM deep review）→ 误判「LLM delta 缺失」
→ 触发 _check_llm_route_health → 用 llm_review_cache.MAX(created_at) 距今 >180 分钟
误报「LLM 审查可能全局失效」。但实际只是盘前未跑 formal run / 辅助数据死锁期，
cache 不刷新属正常，非 LLM 失效。

修复:
1. check_b_item 只看 formal run 的推荐（experiment 空骨架不触发 LLM delta 缺失）
2. _llm_route_diagnosis 纯函数：cache 从未命中 → never；>24h 无新写入 → strong_stale；
   否则 normal（命中复用时不写新 cache，cache MAX 不增长属正常）

锁定: 三分支判定 + cache MAX 局限（命中复用不写新行）的语义。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_never_when_cache_empty():
    """cache 完全为空 → never（LLM 从未成功工作过）。"""
    severity, msg = self_check._llm_route_diagnosis(None, None, 0)
    assert severity == 'never'
    assert '从未命中' in msg


def test_never_when_cache_latest_none_but_total_nonzero():
    """total>0 但 latest 解析为 None（异常状态）→ never 兜底。"""
    severity, _ = self_check._llm_route_diagnosis(None, None, 5)
    assert severity == 'never'


def test_strong_stale_when_over_24h():
    """cache 有命中但 >24h 无新写入（跨多个交易日）→ strong_stale。"""
    severity, msg = self_check._llm_route_diagnosis(
        '2026-07-10 10:00:00', 25 * 60, 100
    )
    assert severity == 'strong_stale'
    assert '>24h' in msg


def test_strong_stale_at_exact_24h_boundary():
    """恰好 24h（1440 分钟）不触发 strong_stale（> 严格大于，边界值算 normal）。"""
    severity, _ = self_check._llm_route_diagnosis(
        '2026-07-16 02:00:00', 24 * 60, 100
    )
    assert severity == 'normal'


def test_normal_when_recent_cache_write():
    """cache 最近有写入（<24h）→ normal（命中复用不写新行，cache MAX 不增长属正常）。"""
    severity, msg = self_check._llm_route_diagnosis(
        '2026-07-16 10:37:09', 870, 739
    )
    assert severity == 'normal'
    assert '非全局路由失效' in msg
