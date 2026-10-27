"""提示词版本、JSON 合同和程序性公告预筛。

分数是事件方向和力度，不是预测涨跌幅：
-100 强烈利空，0 中性，+100 强烈利好。confidence 是模型对这次判断的把握，0 到 1。
提示词只放文本本身，不放股价、涨跌幅或之后的行情。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from app.services.model_router_service import _extract_json_object
from app.services.text_events.keywords import legacy_keyword_label

PROMPT_VERSION = "text-event-v1"

LABELS = ("利好", "利空", "中性")
CATEGORIES = (
    "业绩", "回购", "减持", "解禁", "处罚", "重组", "定增",
    "人事", "股东会", "研报评级", "调研", "其他",
)

_LABEL_ALIASES = {
    "利好": "利好", "正面": "利好", "positive": "利好", "bullish": "利好",
    "利空": "利空", "负面": "利空", "negative": "利空", "bearish": "利空",
    "中性": "中性", "neutral": "中性",
}
_CATEGORY_ALIASES = {
    "业绩预告": "业绩", "业绩快报": "业绩", "年报": "业绩", "季报": "业绩",
    "盈利": "业绩", "调研纪要": "调研", "机构调研": "调研",
    "评级": "研报评级", "研报": "研报评级",
    "股东大会": "股东会", "股东会": "股东会",
    "增发": "定增", "定向增发": "定增",
    "减持计划": "减持", "增持": "其他",
    "解禁": "解禁", "限售解禁": "解禁",
    "回购": "回购", "处罚": "处罚", "立案": "处罚",
    "重组": "重组", "并购": "重组", "人事": "人事", "高管": "人事",
}

# 只看标题。正文里顺带提到「股东会」不应把一篇调研纪要直接打成程序性中性。
_PROCEDURAL: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"股东(?:大)?会"), "股东会", "标题是股东会召集、通知或决议"),
    (re.compile(r"(?:法律意见|律师意见|律师事务所)"), "其他", "标题是律师意见书或法律意见书"),
    (re.compile(r"(?:制度的?修订|修订.{0,12}制度|公司章程|章程修正|治理制度|议事规则)"), "其他", "标题是制度或章程修订"),
)

SYSTEM_PROMPT = """你是 A 股公司文本的归档员，只给研究和展示用的标签。
只根据用户给出的标题和正文，判断这件事对这家公司的股东利益、治理或基本面是利好、利空还是中性。
不要预测股价，不要写涨跌幅，不要提后续走势，不要使用正文以外的行情或日期之后的信息。
分数表示事件方向和力度，不是预测收益：-100 很强的利空，0 中性，+100 很强的利好。
confidence 是你对本次判断的把握，0 到 1。
只输出一个 JSON 对象，不要 Markdown：
{"label":"利好|利空|中性","category":"业绩|回购|减持|解禁|处罚|重组|定增|人事|股东会|研报评级|调研|其他","score":0,"confidence":0.5,"rationale":"不超过80字，不提价格"}"""


@dataclass(frozen=True)
class RuleHit:
    category: str
    rationale: str


def procedural_rule(title: str) -> RuleHit | None:
    text = title or ""
    for pattern, category, reason in _PROCEDURAL:
        if pattern.search(text):
            return RuleHit(category=category, rationale=f"规则预筛：{reason}，标为中性")
    return None


def input_hash(*, source: str, event_id: str, code: str, publish_date: str, title: str, body: str) -> str:
    raw = "\n".join([
        PROMPT_VERSION,
        source or "",
        event_id or "",
        code or "",
        publish_date or "",
        title or "",
        body or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def user_prompt(*, source: str, code: str, publish_date: str, title: str, body: str) -> str:
    """模型看到的全部内容。调用方必须先去掉股价字段。"""
    text = (body or "").strip()
    if len(text) > 6000:
        text = text[:6000] + "\n…（正文已截断，截断只影响本次提示，不影响 input_hash）"
    return (
        f"来源: {source}\n"
        f"代码: {code or '（无个股代码）'}\n"
        f"发布日期: {publish_date}\n"
        f"标题: {title or ''}\n"
        f"正文:\n{text}"
    )


def parse_label_json(text: str) -> dict | None:
    """严格合同：label 和 score 必须在范围内。类别写歪了归到「其他」，不因此失败。"""
    parsed = _extract_json_object(text or "")
    if not isinstance(parsed, dict):
        return None
    label = _LABEL_ALIASES.get(str(parsed.get("label") or "").strip().lower(), None)
    if label is None:
        label = _LABEL_ALIASES.get(str(parsed.get("label") or "").strip())
    if label not in LABELS:
        return None
    score = _number(parsed.get("score"))
    confidence = _number(parsed.get("confidence"))
    if score is None or confidence is None:
        return None
    score_i = max(-100, min(100, int(round(score))))
    confidence_f = max(0.0, min(1.0, float(confidence)))
    category_raw = str(parsed.get("category") or "").strip()
    category = category_raw if category_raw in CATEGORIES else _CATEGORY_ALIASES.get(category_raw, "其他")
    rationale = str(parsed.get("rationale") or "").strip().replace("\n", " ")
    if not rationale:
        return None
    return {
        "label": label,
        "category": category,
        "score": score_i,
        "confidence": round(confidence_f, 4),
        "rationale": rationale[:500],
    }


def keyword_columns(title: str, body: str) -> dict[str, str]:
    verdict = legacy_keyword_label(f"{title or ''}\n{body or ''}")
    return {
        "keyword_label": verdict.label,
        "keyword_sentiment": verdict.sentiment,
        "keyword_hits": ",".join(verdict.hits)[:200],
    }


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
