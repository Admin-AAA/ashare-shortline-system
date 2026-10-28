"""公告/新闻关键词规则的唯一副本。

历史实现把同一张风险词表抄在东财、巨潮、小石和辅助行情里。
这里保留原来的判定（风险词优先、多数路径不会判正），
新的 LLM 标签用 ``legacy_keyword_label`` 作对照，不替换旧字段。

已知误伤（故意不在本模块里“修好”，否则对照基线会漂移）：
「解除ST风险警示」「减持计划完成」仍会因「ST / 风险 / 减持」被判利空。
"""
from __future__ import annotations

from dataclasses import dataclass

# 四处公告路径共用的风险词。顺序保持历史写法。
RISK_KEYWORDS: tuple[str, ...] = (
    "风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报",
)

# anns_d 那一支多出来的一个词，其它三支没有。分开，避免把「冻结」扩散到它们。
ANNS_D_EXTRA_RISK: tuple[str, ...] = ("冻结",)

ANNS_D_POSITIVE: tuple[str, ...] = (
    "增长", "预增", "扭亏", "中标", "签约", "回购", "增持", "分红", "重组", "获批",
)

# 东财「新闻」接口（不是公告）用的是另一张更短的表，同样收敛到这里。
NEWS_NEGATIVE: tuple[str, ...] = (
    "风险", "亏损", "下滑", "减持", "处罚", "跌停", "爆雷", "问询",
)
NEWS_POSITIVE: tuple[str, ...] = (
    "增长", "预增", "利好", "突破", "签约", "中标", "回购", "增持", "分红",
)


def title_hits(text: str, keywords: tuple[str, ...] | list[str]) -> list[str]:
    raw = text or ""
    return [word for word in keywords if word and word in raw]


def legacy_is_risk(title: str, extra: tuple[str, ...] = ()) -> bool:
    """标题里是否命中历史风险词。``extra`` 只给 anns_d 的「冻结」。"""
    return any(word in (title or "") for word in (*RISK_KEYWORDS, *extra))


def legacy_sentiment(
    title: str,
    *,
    extra_risk: tuple[str, ...] = (),
    positive: tuple[str, ...] = (),
) -> str:
    """历史情绪：风险词优先于正面词；没给正面词时，非风险一律中性（不会判正）。"""
    if legacy_is_risk(title, extra_risk):
        return "negative"
    if positive and any(word in (title or "") for word in positive):
        return "positive"
    return "neutral"


def listed_sentiment(title: str, negative: tuple[str, ...], positive: tuple[str, ...] = ()) -> str:
    """按调用方自己的词表判断。东财新闻的词表和公告词表不一样，不能混用。"""
    text = title or ""
    if any(word in text for word in negative):
        return "negative"
    if positive and any(word in text for word in positive):
        return "positive"
    return "neutral"


@dataclass(frozen=True)
class KeywordVerdict:
    """对照标签。``label`` 只有「利空 / 中性」，与旧公告逻辑一致，几乎不判利好。"""

    label: str
    sentiment: str
    hits: tuple[str, ...]

    @property
    def is_risk(self) -> bool:
        return self.sentiment == "negative"


def legacy_keyword_label(text: str) -> KeywordVerdict:
    """给新标签表的 keyword_* 对照列。扫描标题+正文，词表仍是 ``RISK_KEYWORDS``。"""
    hits = tuple(title_hits(text or "", RISK_KEYWORDS))
    if hits:
        return KeywordVerdict(label="利空", sentiment="negative", hits=hits)
    return KeywordVerdict(label="中性", sentiment="neutral", hits=())
