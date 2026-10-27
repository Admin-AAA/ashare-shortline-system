"""正式链路入口（出票/中长线/大宗/结算/日更）必须在 formal_chain() 内运行：网关拒绝旧缓存数据。"""
import inspect

import pytest

import app.services.datasource.cache as c
import app.services.datasource.gateway as g
from app.services import midterm_service, outcome_service
from app.services.datasource.gateway import DatasetGateway

ROW = {k: 1 for k in g.SCHEMAS["quote.realtime"]}


@pytest.fixture(autouse=True)
def _disk(tmp_path, monkeypatch):
    import diskcache

    dc = diskcache.Cache(str(tmp_path / "stale"))
    monkeypatch.setattr(c, "_disk_cache", lambda: dc)
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    c.clear_fresh()
    yield
    c.clear_fresh()
    dc.close()


class _P:
    config_keys = ()

    def __init__(self):
        self.name, self.up = "p", True

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        if not self.up:
            raise ConnectionError("down")
        return {k: {**ROW, "code": k, "price": 10.0} for k in q["keys"]}


@pytest.mark.parametrize("fn", [
    midterm_service.generate_picks,
    midterm_service.generate_block_trade_picks,
    outcome_service.label_pending_outcomes,
    outcome_service.reproject_revised_outcomes,
    outcome_service.recompute_stale_outcomes,
])
def test_entrypoints_wrapped(fn):
    assert hasattr(fn, "__wrapped__"), fn.__name__


def test_stale_result_returns_no_data_inside_formal_entry():
    p = _P()
    gw = DatasetGateway([p], enabled=lambda _: True, failure_threshold=99)
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    c.clear_fresh()
    p.up = False
    # 非正式链路：允许旧数据（带 stale 标记）
    assert gw.fetch_fill("quote.realtime", {}, ["600000"]).stale is True

    @midterm_service._formal_entry
    def entry():
        assert c.in_formal_chain()
        return gw.fetch_fill("quote.realtime", {}, ["600000"], allow_stale=True)

    r = entry()
    assert r.rows == {} and r.stale is False
    assert not c.in_formal_chain()  # 退出后恢复


def test_scheduler_and_flow_dispatch_use_formal_chain():
    from app.orchestration import flows
    from app.services import scheduler_service

    src = inspect.getsource(scheduler_service._execute_task)
    assert "with formal_chain():" in src
    assert src.index("with formal_chain():") < src.index("_run_block_trade_picks_hook(db, task_id)")
    assert "with formal_chain():" in inspect.getsource(flows.execute_recommendation_run.fn)
