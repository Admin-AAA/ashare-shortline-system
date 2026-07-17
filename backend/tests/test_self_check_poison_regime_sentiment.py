"""Regression guard: 毒区 regime×sentiment 交叉分布（第166轮 T3.4 失败归因）。

背景: _market_sentiment 仅用 limit_up_rate 单维度判活跃，会把"涨停集中+大面积下跌"
的冰点日（2026-07-07 up_ratio=12.4% lu_rate=0.9%）误判为活跃，使弱势·活跃 score_line
+3 放宽本该过滤的低质量票。38 笔真残留毒区里"活跃" sentiment 占 33 笔（87%）。

_residual_regime_sentiment_breakdown 是纯函数：按 feature_snapshot 的 market_regime
+ market_sentiment_phase 交叉分桶，返回按 count 降序的 [(base, sent), {count, avg_return}]。
供 _print_residual_poison_zone 打印 + 下一轮修复 _market_sentiment 作精确依据。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _u(rid: str, base: str, sent: str, ret: float | None) -> dict:
    """构造一个 uncovered 笔：rid + feature_snapshot(market_regime/sentiment_phase) + executed_return。"""
    return {
        "rid": rid,
        "fs": {"market_regime": base, "market_sentiment_phase": sent},
        "exit_reason": "stop_loss_hit",
        "max_return": 0.0,
        "executed_return": ret,
    }


def test_empty_returns_empty_list():
    """无样本 → 空列表。"""
    assert self_check._residual_regime_sentiment_breakdown([]) == []


def test_groups_by_regime_sentiment_and_counts():
    """3 笔弱势·活跃 + 1 笔震荡·冰点 → 按桶聚合 count。"""
    uncovered = [
        _u("rec_1", "弱势", "活跃", -5.7),
        _u("rec_2", "弱势", "活跃", -1.2),
        _u("rec_3", "弱势", "活跃", None),  # executed_return=None 不进 avg 分母
        _u("rec_4", "震荡", "冰点", -3.0),
    ]
    out = self_check._residual_regime_sentiment_breakdown(uncovered)
    # 按 count 降序：弱势·活跃(3) 排前
    assert out[0][0] == ("弱势", "活跃")
    assert out[0][1]["count"] == 3
    # 震荡·冰点(1) 排后
    assert out[1][0] == ("震荡", "冰点")
    assert out[1][1]["count"] == 1


def test_avg_return_skips_none_and_rounds():
    """avg_return 只用非 None 的 executed_return，保留 2 位小数。"""
    uncovered = [
        _u("rec_1", "弱势", "活跃", -5.7),
        _u("rec_2", "弱势", "活跃", -1.2),
        _u("rec_3", "弱势", "活跃", None),  # 不计入 avg
    ]
    out = self_check._residual_regime_sentiment_breakdown(uncovered)
    # (-5.7 + -1.2) / 2 = -3.45
    assert out[0][1]["avg_return"] == -3.45


def test_missing_regime_fields_default_to_question_mark():
    """feature_snapshot 无 market_regime/sentiment_phase → 用 '?' 占位，不崩。"""
    uncovered = [{"rid": "rec_1", "fs": {}, "executed_return": -2.0}]
    out = self_check._residual_regime_sentiment_breakdown(uncovered)
    assert out[0][0] == ("?", "?")
    assert out[0][1]["count"] == 1
    assert out[0][1]["avg_return"] == -2.0


def test_all_none_returns_zero_avg():
    """全部 executed_return=None → avg_return=0.0（无样本可平均）。"""
    uncovered = [
        _u("rec_1", "弱势", "活跃", None),
        _u("rec_2", "弱势", "活跃", None),
    ]
    out = self_check._residual_regime_sentiment_breakdown(uncovered)
    assert out[0][1]["avg_return"] == 0.0


def test_weak_active_dominates_real_poison_zone_snapshot():
    """锁定 2026-07-16 实测快照：38 笔毒区里"活跃"sentiment 占绝大多数，印证
    _market_sentiment 单维度判定把冰点日误判活跃是真毒区根因。此快照防止
    后续轮次回归丢失该发现（例如误删 _market_sentiment_phase 字段读取）。
    """
    uncovered = [
        _u(f"rec_{i}", base, sent, -4.0)
        for i, (base, sent) in enumerate(
            [("弱势", "活跃")] * 16
            + [("震荡", "活跃")] * 10
            + [("结构性强势", "活跃")] * 7
            + [("震荡", "高潮")] * 4
            + [("结构性强势", "高潮")] * 1,
            start=1,
        )
    ]
    out = self_check._residual_regime_sentiment_breakdown(uncovered)
    assert out[0][0] == ("弱势", "活跃")
    assert out[0][1]["count"] == 16
    active_total = sum(e["count"] for (b, s), e in out if s == "活跃")
    assert active_total == 33  # 33/38 = 87% 毒区落在"活跃"sentiment
