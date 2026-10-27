import pandas as pd

from app.services.survey_margin_ingest import fetch_surv_day


class _Pro:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def stk_surv(self, **kw):
        self.calls.append(kw)
        return self.pages.pop(0) if self.pages else pd.DataFrame()


def _page(n, start=0):
    return pd.DataFrame([{"ts_code": f"{600000 + start + i}.SH", "name": "x", "surv_date": "20261016",
                          "fund_visitors": "3", "rece_org": f"org{start + i}", "org_type": "基金"} for i in range(n)])


def test_fetch_surv_day_pages_until_short_page():
    pro = _Pro([_page(400), _page(5, 400)])
    rows, status = fetch_surv_day(pro, "20261016")
    assert status == "ok" and len(rows) == 405
    assert pro.calls[1]["offset"] == 400 and pro.calls[1]["limit"] == 400
    assert rows[0]["surv_date"] == "2026-10-16" and rows[0]["fund_visitors"] == 3


def test_fetch_surv_day_akshare_backup_not_written():
    df = _page(3)
    df["source"] = "akshare_jgdy_em"
    rows, status = fetch_surv_day(_Pro([df]), "20261016")
    assert rows == [] and status == "backup_only"


def test_fetch_surv_day_empty():
    assert fetch_surv_day(_Pro([]), "20251001") == ([], "empty")
