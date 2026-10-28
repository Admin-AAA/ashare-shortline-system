"""文本事件标签：规则、JSON、缓存和时间对齐。不连 MySQL，不导入会拉起 plotly 的入口。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.market import TextEvent, TextEventLabel
from app.services.datasource.teajoin_capability import TEAJOIN_APIS
from app.services.text_events.ingest import fetch_records, ingest_range, upsert_events
from app.services.text_events.keywords import RISK_KEYWORDS, legacy_keyword_label
from app.services.text_events.labeler import EventView, label_event, pending_events
from app.services.text_events.sample_report import build_sample_report
from app.services.text_events.schema import (
    SYSTEM_PROMPT,
    input_hash,
    parse_label_json,
    procedural_rule,
    user_prompt,
)
from app.services.text_events.sources import (
    CODE_SOURCES,
    DAILY_SOURCES,
    NOT_INGESTED,
    SNAPSHOT_SOURCES,
    materialize,
    source_by_name,
)
from app.services.text_events.study import excess_study, holding_return
from app.services.text_events.timealign import align_publication

SESSIONS = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
GOOD = '{"label":"利好","category":"业绩","score":80,"confidence":0.8,"rationale":"预告净利润同比上升"}'


class _Settings:
    def __init__(self, enabled: bool, retries: int = 0) -> None:
        self.text_event_llm_enabled = enabled
        self.text_event_llm_max_retries = retries
        self.text_event_llm_concurrency = 1
        self.text_event_llm_qpm = 1000


class _Completer:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls = 0
        self.users: list[str] = []

    def complete(self, *, system: str, user: str):
        self.users.append(user)
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        assert "不要预测股价" in system
        return SimpleNamespace(content=content, model="fake-model", prompt_tokens=12, completion_tokens=6, latency_ms=9.0)


def _engine():
    engine = create_engine("sqlite://")
    TextEvent.__table__.create(engine)
    TextEventLabel.__table__.create(engine)
    return engine


def _add_event(db, **kwargs) -> EventView:
    payload = {
        "source": "forecast",
        "event_id": "e1",
        "code": "000001",
        "title": "业绩预告 预增",
        "body": "摘要：净利润同比上升",
        "publish_date": "2026-10-16",
        "publish_time": None,
        "available_at": "2026-10-19 15:00:00",
        "time_quality": "date_only",
        "signal_trade_date": "2026-10-19",
        "entry_trade_date": "2026-10-20",
        "provider": "test",
        "payload_json": "{}",
    }
    payload.update(kwargs)
    db.add(TextEvent(**payload))
    db.commit()
    row = db.scalar(select(TextEvent).where(TextEvent.event_id == payload["event_id"]))
    assert row is not None
    return EventView.from_row(row)


def test_keyword_table_is_the_historical_one_and_still_misfires():
    assert RISK_KEYWORDS == ("风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报")
    verdict = legacy_keyword_label("关于解除公司股票交易其他风险警示暨撤销ST的公告")
    assert verdict.label == "利空"
    assert "风险" in verdict.hits and "ST" in verdict.hits
    done = legacy_keyword_label("股东减持计划实施完成")
    assert done.label == "利空" and "减持" in done.hits
    assert procedural_rule("关于解除公司股票交易其他风险警示暨撤销ST的公告") is None


def test_procedural_titles_are_neutral_without_calling_the_model():
    engine = _engine()
    db = sessionmaker(bind=engine)()
    view = _add_event(db, title="关于召开2026年年度股东大会的通知", body="会议时间另行通知", event_id="meet")
    completer = _Completer([GOOD])
    result = label_event(db, view, settings=_Settings(True), completer=completer)
    assert completer.calls == 0
    assert result.status == "ok" and result.label == "中性" and result.label_origin == "rule"
    assert result.category == "股东会" and result.score == 0
    stored = db.scalar(select(TextEventLabel))
    assert stored is not None and stored.model == "rule" and stored.keyword_label == "中性"
    again = label_event(db, view, settings=_Settings(True), completer=completer)
    assert again.cached and completer.calls == 0


def test_json_contract_strips_fences_and_clamps():
    fenced = parse_label_json("说明如下\n```json\n" + GOOD + "\n```")
    assert fenced is not None and fenced["label"] == "利好" and fenced["score"] == 80
    loose = parse_label_json('模型回答 {"label":"利空","category":"随便写","score":150,"confidence":2,"rationale":"被立案调查"}')
    assert loose is not None
    assert loose["label"] == "利空" and loose["category"] == "其他"
    assert loose["score"] == 100 and loose["confidence"] == 1.0
    assert parse_label_json('{"label":"观望","category":"业绩","score":1,"confidence":0.2,"rationale":"不清"}') is None
    assert parse_label_json('{"label":"利好","category":"业绩","score":"x","confidence":0.2,"rationale":"不清"}') is None


def test_llm_cache_is_idempotent_and_bad_json_retries():
    engine = _engine()
    db = sessionmaker(bind=engine)()
    view = _add_event(db)
    completer = _Completer(["不是 json", GOOD])
    result = label_event(db, view, settings=_Settings(True, retries=1), completer=completer)
    assert result.status == "ok" and result.label == "利好" and result.model == "fake-model"
    assert completer.calls == 2
    assert "88" not in completer.users[0]
    second = label_event(db, view, settings=_Settings(True, retries=1), completer=completer)
    assert second.cached and completer.calls == 2
    assert pending_events(db, 10) == []
    row = db.scalar(select(TextEvent).where(TextEvent.event_id == "e1"))
    assert row is not None
    row.body = "摘要：由预增改为预减"
    row.title = "业绩预告 预减"
    db.commit()
    updated = EventView.from_row(row)
    third = label_event(db, updated, settings=_Settings(True, retries=0), completer=completer)
    assert third.status == "ok" and completer.calls == 3
    assert input_hash(
        source=updated.source, event_id=updated.event_id, code=updated.code,
        publish_date=updated.publish_date, title=updated.title, body=updated.body,
    ) != input_hash(
        source=view.source, event_id=view.event_id, code=view.code,
        publish_date=view.publish_date, title=view.title, body=view.body,
    )


def test_llm_flag_off_skips_without_a_failed_row():
    engine = _engine()
    db = sessionmaker(bind=engine)()
    view = _add_event(db, title="减持计划实施完成", body="股东减持已经实施完毕")
    completer = _Completer([GOOD])
    result = label_event(db, view, settings=_Settings(False), completer=completer)
    assert result.status == "skipped" and result.keyword_label == "利空"
    assert completer.calls == 0
    assert db.scalar(select(TextEventLabel)) is None


def test_time_alignment_matches_close_signal_next_open():
    only = align_publication(publish_date="2026-10-16", publish_time=None, sessions=SESSIONS)
    assert only.time_quality == "date_only"
    assert only.signal_trade_date == "2026-10-19"
    assert only.entry_trade_date == "2026-10-20"
    assert only.available_at == "2026-10-19 15:00:00"
    morning = align_publication(publish_date="2026-10-16", publish_time="2026-10-16 10:00:00", sessions=SESSIONS)
    assert morning.time_quality == "precise"
    assert morning.signal_trade_date == "2026-10-16" and morning.entry_trade_date == "2026-10-19"
    preopen = align_publication(publish_date="2026-10-16", publish_time="2026-10-16 08:00:00", sessions=SESSIONS)
    assert preopen.signal_trade_date == "2026-10-16"
    after = align_publication(publish_date="2026-10-16", publish_time="2026-10-16 16:30:00", sessions=SESSIONS)
    assert after.time_quality == "precise"
    assert after.signal_trade_date == "2026-10-19" and after.entry_trade_date == "2026-10-20"


def test_report_row_drops_target_price_before_the_prompt():
    spec = source_by_name("report_rc")
    assert spec is not None
    event = materialize(spec, {
        "ts_code": "000001.SZ", "name": "平安银行", "report_date": "20261023",
        "report_title": "维持买入", "report_type": "点评", "classify": "首次",
        "org_name": "某券商", "author_name": "甲", "rating": "买入",
        "max_price": 88.5, "min_price": 10, "eps": 1.2, "pe": 8,
    }, SESSIONS)
    assert event is not None
    assert "88.5" not in event["body"] and "88.5" not in event["payload_json"]
    assert "买入" in event["body"]
    prompt = user_prompt(
        source=event["source"], code=event["code"], publish_date=event["publish_date"],
        title=event["title"], body=event["body"],
    )
    assert "88.5" not in prompt and "股价" not in prompt
    assert "不要预测股价" in SYSTEM_PROMPT
    assert event["time_quality"] == "date_only"
    assert event["publish_date"] == "2026-10-23"


def test_kpl_clock_is_precise_and_st_reason_is_kept():
    kpl = source_by_name("kpl_list")
    assert kpl is not None
    event = materialize(kpl, {
        "ts_code": "000001.SZ", "name": "平安银行", "trade_date": "20261016",
        "lu_time": "093500", "lu_desc": "银行板块", "theme": "金融", "tag": "涨停",
        "pct_chg": 10.0, "price": 12.3,
    }, SESSIONS)
    assert event is not None
    assert event["time_quality"] == "precise" and event["publish_time"] == "2026-10-16 09:35:00"
    assert event["signal_trade_date"] == "2026-10-16"
    assert "12.3" not in event["body"]
    st = source_by_name("st")
    assert st is not None
    row = materialize(st, {
        "ts_code": "000002.SZ", "name": "乙", "pub_date": "20261016",
        "st_type": "撤销ST", "st_reason": "撤销其他风险警示", "st_explain": "已消除",
    }, SESSIONS)
    assert row is not None and "撤销其他风险警示" in row["body"]
    top = source_by_name("top_list")
    assert top is not None
    listed = materialize(top, {
        "ts_code": "000001.SZ", "name": "平安银行", "trade_date": "20261016",
        "close": 12.3, "pct_change": 10.0, "reason": "日涨幅偏离值达7%",
    }, SESSIONS)
    assert listed is not None and "12.3" not in listed["body"] and "偏离" in listed["body"]
    concept = source_by_name("kpl_concept_cons")
    assert concept is not None
    member = materialize(concept, {
        "ts_code": "000001.KP", "name": "银行", "con_code": "000001.SZ", "con_name": "平安银行",
        "trade_date": "20261016", "desc": "零售银行主线",
    }, SESSIONS)
    assert member is not None and member["code"] == "000001" and "零售银行" in member["body"]


def test_gateway_fetch_is_idempotent_and_isolates_upstream_errors():
    class Frame:
        def __init__(self, rows: list[dict]) -> None:
            self._rows = rows
            self.empty = not rows

        def to_dict(self, orient: str) -> list[dict]:
            assert orient == "records"
            return self._rows

    class Pro:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def st(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("pub_date") == "20261016":
                raise RuntimeError("query capacity busy")
            return Frame([{
                "ts_code": "000001.SZ", "name": "甲", "pub_date": kwargs["pub_date"],
                "st_type": "*ST", "st_reason": "连续亏损", "st_explain": "",
            }])

        def report_rc(self, **kwargs):
            self.calls.append(kwargs)
            return Frame([{
                "ts_code": "000001.SZ", "report_date": "20261023", "report_title": "点评",
                "rating": "增持", "org_name": "甲", "author_name": "乙", "max_price": 50,
            }])

    engine = _engine()
    db = sessionmaker(bind=engine)()
    summary = ingest_range(db, start="2026-10-16", end="2026-10-19", sources=["st"], pro=Pro())
    statuses = [item["status"] for item in summary["results"]]
    assert statuses[0] == "error" and "capacity" in summary["results"][0]["error"]
    assert "ok" in statuses
    spec = replace(source_by_name("report_rc"), page_limit=1, max_pages=3)
    pro = Pro()
    rows, page_status = fetch_records(pro, spec, {"report_date": "20261023"})
    assert page_status == "offset_ignored" and len(rows) == 1
    event = materialize(source_by_name("report_rc"), rows[0], SESSIONS)
    assert event is not None
    assert upsert_events(db, [event], provider="gateway") == 1
    assert upsert_events(db, [event], provider="gateway") == 0


def test_sample_report_and_excess_return_are_research_only():
    labeled = SimpleNamespace(
        status="ok", cached=False, label="利好", category="业绩", label_origin="llm",
        keyword_label="中性", source="forecast", event_id="1", title="预增", rationale="同比上升",
        latency_ms=10, prompt_tokens=20, completion_tokens=5,
    )
    failed = SimpleNamespace(
        status="failed", cached=False, label=None, category=None, label_origin="llm",
        keyword_label=None, source="forecast", event_id="2", title="", rationale=None,
        latency_ms=None, prompt_tokens=None, completion_tokens=None,
    )
    report = build_sample_report([labeled, failed], price_prompt=1.0, price_completion=2.0)
    assert report["failure_rate"] == 0.5
    assert report["keyword_agreement"]["agreed"] == 0
    assert report["disagreements"][0]["keyword_label"] == "中性"
    assert report["cost"] is not None and report["disclaimer"] == "仅供参考，不参与选股"
    assert report["participates_in_selection"] is False
    sessions = ["2026-10-20", "2026-10-21", "2026-10-22"]
    bars = {
        "000001": {"2026-10-20": {"open": 10, "close": 12}, "2026-10-21": {"open": 12, "close": 12}, "2026-10-22": {"open": 12, "close": 11}},
    }
    for index in range(5):
        code = f"60000{index}"
        bars[code] = {
            "2026-10-20": {"open": 10, "close": 11},
            "2026-10-21": {"open": 11, "close": 11},
            "2026-10-22": {"open": 11, "close": 11},
        }
    assert abs(holding_return(bars, sessions, "000001", "2026-10-20", 1) - 0.2) < 1e-9
    study = excess_study(
        [{"label": "利好", "code": "000001", "entry_trade_date": "2026-10-20"}],
        bars, sessions, None, horizons=(1,),
    )
    assert study["benchmark"] == "sample_equal_weight"
    assert study["participates_in_selection"] is False
    assert study["by_label"]["利好"]["T+1"]["mean_excess"] == 0.1
    caps = {"2026-10-20": {code: float(i + 1) for i, code in enumerate(bars)}}
    # 6 只股票拆成五档后同档不足 5 个对照，不能假装算出市值分位超额。
    thin = excess_study(
        [{"label": "利好", "code": "000001", "entry_trade_date": "2026-10-20"}],
        bars, sessions, caps, horizons=(1,),
    )
    assert thin["benchmark"] == "size_quintile" and thin["events_used"] == 0


def test_catalog_lists_text_apis_and_states_why_others_are_absent():
    ingested = {spec.api for spec in (*DAILY_SOURCES, *SNAPSHOT_SOURCES, *CODE_SOURCES)}
    assert {"report_rc", "stk_surv", "forecast", "ths_hot", "kpl_list", "st", "idx_anns"} <= ingested
    assert ingested <= TEAJOIN_APIS
    assert NOT_INGESTED["anns_d"].startswith("teajoin")
    assert NOT_INGESTED["news"].startswith("teajoin")
    assert "broker_recommend" in NOT_INGESTED and "dc_hot" in NOT_INGESTED
    backend = Path(__file__).resolve().parents[1]
    for rel in (
        "app/services/feature_builder.py",
        "app/services/backtest/research_runner.py",
        "app/services/market_data.py",
    ):
        text = (backend / rel).read_text(encoding="utf-8")
        assert "text_event" not in text
    drawer = Path(__file__).resolve().parents[2] / "frontend-v2/src/pages/HorizonExtras.tsx"
    assert "仅供参考，不参与选股" in drawer.read_text(encoding="utf-8")
    assert "不进入选股" in drawer.read_text(encoding="utf-8")
