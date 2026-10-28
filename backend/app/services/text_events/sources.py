"""teajoin 文本/事件接口如何变成一条 text_event。

纯函数：不访问网络。网关调用在 ingest.py，沿用 get_pro() 的限速、重试、熔断和降级。
提示词字段白名单在这里收口，股价、涨跌幅、目标价不会进入正文。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping

from app.engine.trading_calendar import normalize_trade_date
from app.services.text_events.timealign import align_publication, parse_publish_time

# 这些列是行情或目标价，正文和 payload 都不要。
_MARKET_FIELDS = frozenset({
    "price", "close", "open", "high", "low", "pre_close", "current_price",
    "max_price", "min_price", "avg_price", "pct_chg", "pct_change", "change",
    "pe", "target_price", "rt_pct_chg", "bid_pct_chg", "hk_close", "hk_pct_chg",
    "ah_premium", "ah_comparison",
})


@dataclass(frozen=True)
class TextSource:
    api: str
    mode: str  # day | snapshot | code
    date_param: str | None
    publish_date_fields: tuple[str, ...]
    title_fields: tuple[str, ...]
    body_fields: tuple[str, ...]
    id_fields: tuple[str, ...]
    required_any: tuple[str, ...]
    time_field: str | None = None
    code_field: str | None = "ts_code"
    title_prefix: str = ""
    extra_params: Mapping[str, str] = field(default_factory=dict)
    page_limit: int = 2000
    max_pages: int = 20
    note: str = ""


def _src(**kwargs: Any) -> TextSource:
    return TextSource(**kwargs)


DAILY_SOURCES: tuple[TextSource, ...] = (
    _src(api="report_rc", mode="day", date_param="report_date",
         publish_date_fields=("report_date",),
         title_fields=("report_title",), title_prefix="研报",
         body_fields=("report_type", "classify", "org_name", "author_name", "rating", "quarter"),
         id_fields=("ts_code", "report_date", "org_name", "report_title", "author_name"),
         required_any=("report_title", "rating"),
         note="目标价和盈利预测数字不进正文"),
    _src(api="stk_surv", mode="day", date_param="trade_date",
         publish_date_fields=("surv_date", "trade_date"),
         title_fields=("name", "rece_mode"), title_prefix="机构调研",
         body_fields=("content", "rece_org", "org_type", "rece_place", "rece_mode", "comp_rece"),
         id_fields=("ts_code", "surv_date", "rece_org", "org_type", "content"),
         required_any=("content", "rece_org"),
         note="全文 content 落在 text_event；旧 institutional_surveys 仍只留机构名"),
    _src(api="forecast", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date", "first_ann_date"),
         title_fields=("type",), title_prefix="业绩预告",
         body_fields=("type", "summary", "change_reason", "p_change_min", "p_change_max", "end_date"),
         id_fields=("ts_code", "ann_date", "end_date", "type", "update_flag"),
         required_any=("summary", "change_reason", "type")),
    _src(api="ths_hot", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",), time_field="rank_time",
         title_fields=("ts_name", "concept"), title_prefix="同花顺热榜",
         body_fields=("rank_reason", "concept", "rank"),
         id_fields=("ts_code", "trade_date", "rank_time", "rank_reason"),
         required_any=("rank_reason",),
         note="无 rank_reason 的热榜行不入库；current_price 丢弃"),
    _src(api="kpl_list", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",), time_field="lu_time",
         title_fields=("name", "theme"), title_prefix="涨停原因",
         body_fields=("lu_desc", "theme", "tag", "status"),
         id_fields=("ts_code", "trade_date", "lu_desc", "tag", "theme"),
         required_any=("lu_desc", "theme")),
    _src(api="limit_list_ths", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",), time_field="first_lu_time",
         title_fields=("name", "tag"), title_prefix="同花顺涨停原因",
         body_fields=("lu_desc", "tag", "status", "limit_type"),
         id_fields=("ts_code", "trade_date", "lu_desc", "tag", "limit_type"),
         required_any=("lu_desc", "tag")),
    _src(api="st", mode="day", date_param="pub_date",
         publish_date_fields=("pub_date", "imp_date"),
         title_fields=("name", "st_type"), title_prefix="ST",
         body_fields=("st_type", "st_reason", "st_explain"),
         id_fields=("ts_code", "pub_date", "st_type", "st_reason"),
         required_any=("st_reason", "st_explain")),
    _src(api="idx_anns", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",), code_field=None,
         title_fields=("title",), title_prefix="指数公告",
         body_fields=("title", "type", "source"),
         id_fields=("ann_date", "title", "url"),
         required_any=("title",)),
    _src(api="express", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("end_date",), title_prefix="业绩快报",
         body_fields=("perf_summary", "remark", "end_date"),
         id_fields=("ts_code", "ann_date", "end_date"),
         required_any=("perf_summary", "remark")),
    _src(api="fina_audit", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("audit_result",), title_prefix="审计意见",
         body_fields=("audit_result", "audit_agency", "audit_sign", "end_date"),
         id_fields=("ts_code", "ann_date", "end_date", "audit_result"),
         required_any=("audit_result",)),
    _src(api="eco_cal", mode="day", date_param="date",
         publish_date_fields=("date",), time_field="time", code_field=None,
         title_fields=("country", "event"), title_prefix="宏观日历",
         body_fields=("country", "currency", "event", "value", "pre_value"),
         id_fields=("date", "time", "country", "event"),
         required_any=("event",),
         note="fore_value 不进正文，避免把预期路径写进提示词"),
    _src(api="dividend", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("div_proc",), title_prefix="分红",
         body_fields=("div_proc", "cash_div", "cash_div_tax", "ex_date", "record_date"),
         id_fields=("ts_code", "ann_date", "end_date", "div_proc"),
         required_any=("div_proc",)),
    _src(api="repurchase", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("proc",), title_prefix="回购",
         body_fields=("proc", "vol", "amount", "exp_date"),
         id_fields=("ts_code", "ann_date", "end_date", "proc"),
         required_any=("proc",)),
    _src(api="share_float", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("holder_name",), title_prefix="限售解禁",
         body_fields=("holder_name", "float_date", "float_share", "float_ratio"),
         id_fields=("ts_code", "ann_date", "float_date", "holder_name"),
         required_any=("holder_name", "float_ratio"),
         note="发布日用 ann_date，不用未来的 float_date"),
    _src(api="stk_holdertrade", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("holder_name", "in_de"), title_prefix="股东增减持",
         body_fields=("holder_name", "holder_type", "in_de", "change_vol", "change_ratio", "after_ratio"),
         id_fields=("ts_code", "ann_date", "holder_name", "in_de", "change_vol"),
         required_any=("holder_name", "in_de"),
         note="avg_price 不进正文"),
    _src(api="stk_alert", mode="day", date_param="start_end",
         publish_date_fields=("start_date",),
         title_fields=("name", "type"), title_prefix="交易异常提示",
         body_fields=("type", "start_date", "end_date"),
         id_fields=("ts_code", "start_date", "end_date", "type"),
         required_any=("type",),
         note="type 往往是短码，文本有限，仍然入库供对照"),
    _src(api="stk_managers", mode="day", date_param="ann_date",
         publish_date_fields=("ann_date",),
         title_fields=("name", "title"), title_prefix="人事",
         body_fields=("name", "title", "edu", "lev", "begin_date", "end_date"),
         id_fields=("ts_code", "ann_date", "name", "title", "begin_date"),
         required_any=("name", "title")),
    _src(api="top_list", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",),
         title_fields=("name", "reason"), title_prefix="龙虎榜",
         body_fields=("reason",),
         id_fields=("ts_code", "trade_date", "reason"),
         required_any=("reason",),
         note="收盘价和涨跌幅不进正文"),
    _src(api="top_inst", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",),
         title_fields=("exalter", "side"), title_prefix="龙虎榜席位",
         body_fields=("reason", "exalter", "side"),
         id_fields=("ts_code", "trade_date", "exalter", "side", "reason"),
         required_any=("reason", "exalter")),
    _src(api="stk_shock", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",),
         title_fields=("name", "reason"), title_prefix="异常波动",
         body_fields=("reason", "trade_market", "period"),
         id_fields=("ts_code", "trade_date", "reason", "period"),
         required_any=("reason",)),
    _src(api="stk_high_shock", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",),
         title_fields=("name", "reason"), title_prefix="严重异常波动",
         body_fields=("reason", "trade_market", "period"),
         id_fields=("ts_code", "trade_date", "reason", "period"),
         required_any=("reason",)),
    _src(api="kpl_concept_cons", mode="day", date_param="trade_date",
         publish_date_fields=("trade_date",), code_field="con_code",
         title_fields=("name", "con_name"), title_prefix="题材成分",
         body_fields=("desc", "name"),
         id_fields=("con_code", "trade_date", "ts_code", "desc"),
         required_any=("desc",),
         note="ts_code 是题材，con_code 才是个股"),
)

SNAPSHOT_SOURCES: tuple[TextSource, ...] = (
    _src(api="cn_schedule", mode="snapshot", date_param=None, code_field=None,
         publish_date_fields=("publish_date",),
         title_fields=("title",), title_prefix="宏观日程",
         body_fields=("title", "issuing_org", "month", "data_api"),
         id_fields=("month", "title", "publish_date"),
         required_any=("title",),
         note="目录没有按日参数，每次拉当前窗口"),
)

CODE_SOURCES: tuple[TextSource, ...] = (
    _src(api="forecast_vip", mode="code", date_param=None,
         publish_date_fields=("ann_date", "first_ann_date"),
         title_fields=("type",), title_prefix="业绩预告",
         body_fields=("type", "summary", "change_reason", "p_change_min", "p_change_max", "end_date"),
         id_fields=("ts_code", "ann_date", "end_date", "type", "update_flag"),
         required_any=("summary", "change_reason", "type"),
         note="与 forecast 同文本。全市场 VIP 过重，只在指定 ts_code 时回补"),
    _src(api="namechange", mode="code", date_param=None,
         publish_date_fields=("ann_date", "start_date"),
         title_fields=("name",), title_prefix="更名",
         body_fields=("name", "change_reason", "start_date", "end_date"),
         id_fields=("ts_code", "ann_date", "start_date", "name"),
         required_any=("change_reason", "name"),
         note="没有全市场按日参数，只按股票回补"),
    _src(api="express_vip", mode="code", date_param=None,
         publish_date_fields=("ann_date",),
         title_fields=("end_date",), title_prefix="业绩快报",
         body_fields=("perf_summary", "remark", "end_date"),
         id_fields=("ts_code", "ann_date", "end_date"),
         required_any=("perf_summary", "remark"),
         note="与 express 同文本。探测只能按股票拉，不进默认日更"),
)

# 考虑过、但不做文本入库的接口及原因。键是接口名或本地源。
NOT_INGESTED: dict[str, str] = {
    "anns_d": "teajoin 目录没有公告接口。个股公告继续走东财/巨潮/小石，再用 news_announcement 镜像进文本层",
    "news": "teajoin 目录没有新闻接口",
    "broker_recommend": "只有月份、券商和代码，没有叙述；结构化表 broker_recommends 已经在用",
    "disclosure_date": "只有财报预约披露日，没有正文",
    "dc_hot": "探测字段只有排名、涨跌幅和现价，没有原因文本",
    "rt_k": "实时行情，不是文本，且 teajoin 不提供",
    "rt_min": "实时分钟，不是文本，且 teajoin 不提供",
    "stock_company": "公司简介是静态档案，不是按日事件",
    "pledge_stat": "质押数量和比例，探测字段里没有原因叙述",
    "pledge_detail": "质押明细没有原因字段",
    "fina_mainbz": "主营构成是数值拆分，不是事件叙述",
    "fund_div": "基金分红进度，不是个股事件",
    "stk_rewards": "职务和报酬数字；探测只能按股票拉，title 是职务不是事件叙述",
    "dc_concept_cons": "按 trade_date 探测为空，且需要 theme_code，没有稳定的全市场按日文本",
}

_FIELD_LABELS = {
    "report_type": "报告类型", "classify": "分类", "org_name": "机构", "author_name": "作者",
    "rating": "评级", "quarter": "季度", "content": "调研内容", "rece_org": "接待机构",
    "org_type": "机构类型", "rece_place": "地点", "rece_mode": "方式", "comp_rece": "公司接待人",
    "type": "类型", "summary": "摘要", "change_reason": "变动原因", "p_change_min": "净利润变动下限%",
    "p_change_max": "净利润变动上限%", "end_date": "报告期", "rank_reason": "上榜原因",
    "concept": "概念", "rank": "排名", "lu_desc": "涨停原因", "theme": "题材", "tag": "标签",
    "status": "状态", "limit_type": "涨跌停类型", "st_type": "ST类型", "st_reason": "原因",
    "st_explain": "说明", "title": "标题", "source": "来源", "perf_summary": "业绩摘要",
    "remark": "备注", "audit_result": "审计结论", "audit_agency": "会计师事务所",
    "audit_sign": "签字会计师", "country": "国家", "currency": "币种", "event": "事件",
    "value": "公布值", "pre_value": "前值", "div_proc": "分红进度", "cash_div": "每股派息(税前)",
    "cash_div_tax": "每股派息(税后)", "ex_date": "除权除息日", "record_date": "股权登记日",
    "proc": "回购进度", "vol": "数量", "amount": "金额", "exp_date": "过期日",
    "holder_name": "股东", "float_date": "解禁日", "float_share": "解禁股数", "float_ratio": "解禁比例%",
    "holder_type": "股东类型", "in_de": "方向", "change_vol": "变动股数", "change_ratio": "变动比例%",
    "after_ratio": "变动后比例%", "start_date": "开始日期", "name": "姓名", "edu": "学历",
    "lev": "岗位类别", "begin_date": "任职开始", "issuing_org": "发布机构", "month": "月份",
    "data_api": "对应接口", "reason": "原因", "exalter": "营业部", "side": "买卖方向",
    "trade_market": "市场", "period": "区间", "desc": "说明", "con_name": "成分股",
}


def source_by_name(name: str) -> TextSource | None:
    for spec in (*DAILY_SOURCES, *SNAPSHOT_SOURCES, *CODE_SOURCES):
        if spec.api == name:
            return spec
    return None


def params_for_day(spec: TextSource, day_iso: str) -> dict[str, Any]:
    compact = day_iso.replace("-", "")
    if spec.date_param == "start_end":
        return {"start_date": compact, "end_date": compact, **dict(spec.extra_params)}
    if not spec.date_param:
        return dict(spec.extra_params)
    return {spec.date_param: compact, **dict(spec.extra_params)}


def materialize(spec: TextSource, record: Mapping[str, Any], sessions: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    row = _prepare(spec, record)
    publish_date = ""
    for key in spec.publish_date_fields:
        publish_date = normalize_trade_date(row.get(key))
        if len(publish_date) == 10:
            break
    if len(publish_date) != 10:
        return None
    if not any(_filled(row.get(key)) for key in spec.required_any):
        return None
    title = " ".join(part for part in (spec.title_prefix, *(_text(row.get(key)) for key in spec.title_fields)) if part).strip()
    body = _render_body(spec, row)
    if not title and not body:
        return None
    if not body:
        body = title
    code = _stock_code(row.get(spec.code_field)) if spec.code_field else ""
    clock_raw = row.get(spec.time_field) if spec.time_field else None
    publish_time = parse_publish_time(publish_date, clock_raw)
    aligned = align_publication(publish_date=publish_date, publish_time=publish_time, sessions=list(sessions))
    id_parts = [code, publish_date, *(_text(row.get(key)) for key in spec.id_fields)]
    event_id = hashlib.sha1((spec.api + "|" + "|".join(id_parts)).encode("utf-8")).hexdigest()[:40]
    payload = {
        key: _text(row.get(key))
        for key in (*spec.body_fields, *spec.id_fields)
        if key not in _MARKET_FIELDS and _filled(row.get(key))
    }
    return {
        "source": spec.api,
        "event_id": event_id,
        "code": code,
        "title": title[:500],
        "body": body,
        "publish_date": aligned.publish_date,
        "publish_time": aligned.publish_time,
        "available_at": aligned.available_at,
        "time_quality": aligned.time_quality,
        "signal_trade_date": aligned.signal_trade_date,
        "entry_trade_date": aligned.entry_trade_date,
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def announcement_to_event(row: Mapping[str, Any], sessions: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    """把现有 news_announcement 行镜像进文本层。不改原表的 sentiment。"""
    title = _text(row.get("title"))
    if not title:
        return None
    publish_date = normalize_trade_date(row.get("announce_date"))
    if len(publish_date) != 10:
        return None
    summary = _text(row.get("summary"))
    code = _stock_code(row.get("code")) or str(row.get("code") or "")[:6]
    aligned = align_publication(publish_date=publish_date, publish_time=None, sessions=list(sessions))
    upstream = _text(row.get("source")) or "unknown"
    article_id = _text(row.get("article_id")) or hashlib.sha1(title.encode("utf-8")).hexdigest()[:40]
    return {
        "source": f"announcement:{upstream}"[:40],
        "event_id": article_id[:80],
        "code": code if len(code) == 6 and code.isdigit() else "",
        "title": title[:500],
        "body": summary or title,
        "publish_date": aligned.publish_date,
        "publish_time": None,
        "available_at": aligned.available_at,
        "time_quality": aligned.time_quality,
        "signal_trade_date": aligned.signal_trade_date,
        "entry_trade_date": aligned.entry_trade_date,
        "payload_json": json.dumps(
            {"upstream_source": upstream, "category": _text(row.get("category")), "legacy_sentiment": _text(row.get("sentiment"))},
            ensure_ascii=False, sort_keys=True,
        ),
    }


def _prepare(spec: TextSource, record: Mapping[str, Any]) -> dict[str, Any]:
    row = {str(key): _clean(value) for key, value in record.items() if str(key) not in _MARKET_FIELDS}
    if spec.api == "stk_holdertrade":
        direction = str(row.get("in_de") or "").strip().upper()
        row["in_de"] = {"IN": "增持", "DE": "减持"}.get(direction, row.get("in_de"))
    return row


def _render_body(spec: TextSource, row: Mapping[str, Any]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for key in spec.body_fields:
        if key in _MARKET_FIELDS or key in seen:
            continue
        seen.add(key)
        value = _text(row.get(key))
        if not value:
            continue
        lines.append(f"{_FIELD_LABELS.get(key, key)}：{value}")
    return "\n".join(lines)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "null"}:
        return None
    return value


def _filled(value: Any) -> bool:
    return bool(_text(value))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return text


def _stock_code(value: Any) -> str:
    head = str(value or "").split(".")[0].strip()
    if len(head) == 6 and head.isdigit():
        return head
    return ""
