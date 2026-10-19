import threading
from concurrent.futures import ThreadPoolExecutor

from app.services.datasource.cache import ctx_bound, formal_chain, in_formal_chain, submit_ctx


def test_plain_submit_loses_marker_but_submit_ctx_keeps_it():
    with formal_chain(), ThreadPoolExecutor(2) as pool:
        assert pool.submit(in_formal_chain).result() is False  # documents the bug
        assert all(submit_ctx(pool, in_formal_chain).result() for _ in range(4))
    with ThreadPoolExecutor(1) as pool:
        assert submit_ctx(pool, in_formal_chain).result() is False


def test_thread_target_keeps_marker():
    seen = []
    with formal_chain():
        t = threading.Thread(target=ctx_bound(lambda: seen.append(in_formal_chain())))
        t.start(); t.join()
    assert seen == [True]


def test_stale_rejected_in_pool_under_formal_chain():
    from app.services.datasource.cache import stale_allowed

    with formal_chain(), ThreadPoolExecutor(2) as pool:
        assert pool.submit(stale_allowed, "quote.realtime", True).result() is True  # old leak
        assert submit_ctx(pool, stale_allowed, "quote.realtime", True).result() is False
