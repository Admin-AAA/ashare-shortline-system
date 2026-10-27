"""teajoin 目录级探测：按官方 guide 的 205 个接口名逐个做 1~4 次小调用（limit 50），确认可用性/字段/最新日期/延迟。
密钥只从 Settings(.env) 读取，绝不打印/落盘。严格限速（2 线程、每线程间隔≥1.5s、容量忙退避 8s）。

  PYTHONPATH=. .venv/bin/python scripts/probe_teajoin_catalog.py --catalog /tmp/catalog.txt --out ../docs/teajoin_catalog_probe.json
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from scripts.probe_teajoin import call

LAST = "20261023"
SKIP = {"stk_mins"}  # 需单独购买历史分钟权限，已确认无权限
DATECOLS = ("trade_date", "end_date", "ann_date", "cal_date", "date", "report_date", "pub_date", "month", "f_ann_date", "trade_time", "ex_date", "list_date")

IDX = {"ts_code": "000300.SH"}
OVERRIDES: dict[str, list[dict]] = {
    # 财务类：按代码+报告期
    **{a: [{"ts_code": "000001.SZ", "period": "20260630"}, {"ts_code": "000001.SZ"}] for a in
       ("income", "income_vip", "balancesheet", "balancesheet_vip", "cashflow", "cashflow_vip", "express", "express_vip", "forecast_vip", "fina_mainbz", "fina_mainbz_vip", "fina_audit")},
    "fina_indicator": [{"ts_code": "000001.SZ"}],
    "disclosure_date": [{"end_date": "20260930"}, {"ts_code": "000001.SZ"}],
    "top10_holders": [{"ts_code": "000001.SZ"}], "top10_floatholders": [{"ts_code": "000001.SZ"}],
    "pledge_detail": [{"ts_code": "000001.SZ"}], "pledge_stat": [{"end_date": "20261016"}, {"ts_code": "000001.SZ"}],
    "stock_company": [{"ts_code": "000001.SZ"}], "stk_managers": [{"ts_code": "000001.SZ"}], "stk_rewards": [{"ts_code": "000001.SZ"}],
    "stk_ah_comparison": [{"trade_date": LAST}, {}], "stk_seasoned": [{"ann_date": "20261023"}, {"start_date": "20260101", "end_date": "20261023"}],
    "stk_alert": [{"start_date": "20260901", "end_date": "20261023"}, {}],
    "stk_shock": [{"trade_date": LAST}], "stk_high_shock": [{"trade_date": LAST}],
    "bse_mapping": [{}], "stock_hsgt": [{"type": "HK_SZ"}, {"trade_date": LAST}],
    "st": [{"pub_date": "20260714"}, {"pub_date": "20260701"}],
    "stk_weekly_monthly": [{"trade_date": "20261023", "freq": "week"}, {"ts_code": "000001.SZ", "freq": "week"}],
    "stk_week_month_adj": [{"trade_date": "20261023", "freq": "week"}, {"ts_code": "000001.SZ", "freq": "week"}],
    "weekly": [{"trade_date": "20261023"}], "monthly": [{"trade_date": "20260831"}],
    "moneyflow_hsgt": [{"start_date": "20261015", "end_date": "20261023"}],
    "hk_hold": [{"trade_date": LAST}, {"ts_code": "000001.SZ"}],
    "ggt_daily": [{"start_date": "20261015", "end_date": "20261023"}],
    "ggt_top10": [{"trade_date": LAST}], "hsgt_top10": [{"trade_date": LAST}],
    "moneyflow_ind_ths": [{"trade_date": LAST}], "moneyflow_ind_dc": [{"trade_date": LAST}], "moneyflow_cnt_ths": [{"trade_date": LAST}],
    "moneyflow_mkt_dc": [{"start_date": "20261015", "end_date": "20261023"}],
    "ccass_hold": [{"trade_date": LAST}, {"ts_code": "00700.HK"}],
    "kpl_concept": [{"trade_date": LAST}, {}], "dc_hot": [{"trade_date": LAST, "market": "A股市场", "hot_type": "人气榜"}, {"trade_date": LAST}],
    "dc_member": [{"trade_date": LAST}], "dc_daily": [{"trade_date": LAST}], "dc_concept": [{"trade_date": LAST}], "dc_concept_cons": [{"trade_date": LAST}],
    "tdx_index": [{"trade_date": LAST}], "tdx_member": [{"trade_date": LAST}], "tdx_daily": [{"trade_date": LAST}],
    "ths_daily": [{"trade_date": LAST}], "stk_surv": [{"trade_date": LAST}], "stk_nineturn": [{"trade_date": LAST, "freq": "daily"}],
    "report_rc": [{"report_date": LAST}, {"ts_code": "000001.SZ"}], "broker_recommend": [{"month": "202610"}, {"month": "202609"}],
    "stk_factor_pro": [{"trade_date": LAST}, {"ts_code": "000001.SZ", "start_date": "20261016", "end_date": "20261023"}],
    "limit_step": [{"trade_date": LAST}],
    "index_basic": [{"market": "SSE"}], "index_weekly": [{"ts_code": "000300.SH", "start_date": "20260901", "end_date": "20261023"}],
    "index_monthly": [{"ts_code": "000300.SH", "start_date": "20260101", "end_date": "20261023"}],
    "index_weight": [{"index_code": "000300.SH", "start_date": "20261001", "end_date": "20261023"}],
    "index_dailybasic": [{"trade_date": LAST}], "index_classify": [{"level": "L1", "src": "SW2021"}],
    "index_member_all": [{"l1_code": "801010.SI"}, {"ts_code": "000001.SZ"}], "sw_daily": [{"trade_date": LAST}],
    "ci_index_member": [{"index_code": "CI005001.CI"}, {}], "ci_daily": [{"trade_date": LAST}],
    "index_global": [{"ts_code": "SPX", "start_date": "20261015", "end_date": "20261023"}],
    "idx_anns": [{"ann_date": "20261023"}, {}], "idx_factor_pro": [{"ts_code": "000300.SH", "start_date": "20261016", "end_date": "20261023"}],
    "mkt_idx_bmk": [{"trade_date": LAST}, {}], "daily_info": [{"trade_date": LAST}], "sz_daily_info": [{"trade_date": LAST}],
    "fund_basic": [{"market": "E"}], "fund_company": [{}], "fund_manager": [{"ts_code": "510300.SH"}, {"name": "刘"}],
    "fund_nav": [{"ts_code": "510300.SH"}, {"nav_date": LAST}], "fund_daily": [{"trade_date": LAST}], "fund_adj": [{"trade_date": LAST}],
    "fund_div": [{"ann_date": "20261023"}, {"ts_code": "510300.SH"}], "fund_portfolio": [{"ts_code": "510300.SH"}, {"ann_date": "20261023"}],
    "fund_share": [{"trade_date": LAST}, {"ts_code": "510300.SH"}], "fund_factor_pro": [{"trade_date": LAST}],
    "etf_auction": [{"trade_date": LAST}], "etf_basic": [{}], "etf_index": [{}], "etf_limit": [{"trade_date": LAST}], "etf_share_size": [{"trade_date": LAST}],
    "etf_sh_cons": [{"trade_date": LAST}, {}], "etf_sz_cons": [{"trade_date": LAST}, {}],
    "etf_mins": [{"ts_code": "510300.SH", "freq": "1min", "start_date": "2026-10-23 09:30:00", "end_date": "2026-10-23 09:35:00"}],
    "fut_basic": [{"exchange": "DCE"}], "fut_daily": [{"trade_date": LAST}], "fut_holding": [{"trade_date": LAST}], "fut_wsr": [{"trade_date": LAST}],
    "fut_settle": [{"trade_date": LAST}], "fut_mapping": [{"ts_code": "RB.SHF"}, {}], "fut_index_daily": [{"trade_date": LAST}, {}],
    "fut_trade_cal": [{"exchange": "DCE", "start_date": "20261020", "end_date": "20261031"}], "fut_weekly_monthly": [{"trade_date": "20261023", "freq": "week"}],
    "fut_weekly_detail": [{"prd": "RB", "week": "202642"}, {"week": "202642"}],
    "fut_daily_adj": [{"trade_date": LAST}], "fut_inv_weekly": [{"start_date": "20261001", "end_date": "20261023"}, {}],
    "fut_trade_param": [{}], "fut_rcpt_mat": [{"ts_code": "RB.SHF"}, {}],
    "opt_basic": [{"exchange": "SSE"}], "opt_daily": [{"trade_date": LAST}], "ft_limit": [{"trade_date": LAST}],
    "cb_basic": [{}], "cb_daily": [{"trade_date": LAST}], "cb_call": [{"ann_date": "20261023"}, {}], "cb_issue": [{"ann_date": "20261023"}, {}],
    "cb_rate": [{"ts_code": "113008.SH"}], "cb_rating": [{"ts_code": "113008.SH"}, {}], "cb_share": [{"ts_code": "113008.SH"}, {}],
    "cb_factor_pro": [{"trade_date": LAST}], "top10_cb_holders": [{"ts_code": "113008.SH"}, {}],
    "bond_blk": [{"start_date": "20261015", "end_date": "20261023"}], "bond_blk_detail": [{"start_date": "20261015", "end_date": "20261023"}],
    "bc_otcqt": [{"trade_date": LAST}, {}], "bc_bestotcqt": [{"trade_date": LAST}, {}],
    "hk_basic": [{"list_status": "L"}], "hk_tradecal": [{"start_date": "20261020", "end_date": "20261031"}],
    "us_basic": [{}], "us_tradecal": [{"start_date": "20261020", "end_date": "20261031"}],
    "us_tbr": [{"date": "20261023"}, {"start_date": "20261015", "end_date": "20261023"}], "us_tltr": [{"date": "20261023"}, {}], "us_trltr": [{"date": "20261023"}, {}],
    "us_tycr": [{"date": "20261023"}, {}], "us_trycr": [{"date": "20261023"}, {}], "vix_index": [{"start_date": "20261015", "end_date": "20261023"}, {}],
    "cn_gdp": [{}], "cn_cpi": [{}], "cn_ppi": [{}], "cn_pmi": [{}], "cn_m": [{}], "cn_schedule": [{}],
    "shibor": [{"start_date": "20261015", "end_date": "20261023"}], "shibor_quote": [{"start_date": "20261015", "end_date": "20261023"}],
    "shibor_lpr": [{"start_date": "20260801", "end_date": "20261023"}], "hibor": [{"start_date": "20261015", "end_date": "20261023"}],
    "libor": [{"start_date": "20200501", "end_date": "20200624"}], "eco_cal": [{"date": "20261023"}, {}],
    "repo_daily": [{"trade_date": LAST}], "sge_basic": [{}], "sge_daily": [{"trade_date": LAST}], "fx_daily": [{"trade_date": LAST}, {}],
    "fx_obasic": [{}], "new_share": [{"start_date": "20261001", "end_date": "20261023"}],
    "stk_account": [{"start_date": "20190101", "end_date": "20190222"}], "slb_len": [{"trade_date": "20250725"}], "slb_len_mm": [{"trade_date": "20250725"}],
    "slb_sec": [{"trade_date": "20240930"}], "slb_sec_detail": [{"trade_date": "20240710"}], "gz_index": [{"start_date": "20190301", "end_date": "20190304"}],
    "wz_index": [{"start_date": "20190301", "end_date": "20190304"}, {}], "sf_month": [{}], "margin_secs": [{"trade_date": LAST}],
    "stock_basic": [{"list_status": "L"}],
    # —— 第二轮：第一轮参数不对/容量忙/超时的接口
    "cyq_chips": [{"ts_code": "000001.SZ", "trade_date": "20260625"}],
    "dc_index": [{"trade_date": LAST, "idx_type": "概念板块"}],
    "index_daily": [{"ts_code": "000300.SH", "start_date": "20261016", "end_date": "20261023"}],
    "hm_list": [{}], "namechange": [{"ts_code": "000001.SZ"}], "ths_index": [{}], "ths_member": [{"ts_code": "885312.TI"}],
    "dc_concept_cons": [{"trade_date": LAST, "theme_code": "BK1184"}, {"theme_code": "BK1184"}],
    "index_weight": [{"index_code": "000300.SH", "start_date": "20260901", "end_date": "20261023"}, {"index_code": "000300.SH"}],
    "pledge_detail": [{"ts_code": "000014.SZ"}, {"ts_code": "600519.SH"}],
    "fut_weekly_detail": [{"prd": "RB", "week": "202641"}, {"prd": "RB", "start_week": "202640", "end_week": "202643"}],
    "ggt_daily": [{"trade_date": LAST}, {"start_date": "20260901", "end_date": "20261023"}],
}
GENERIC = [
    {"trade_date": LAST},
    {"ts_code": "000001.SZ", "start_date": "20260901", "end_date": "20261023"},
    {"start_date": "20261015", "end_date": "20261023"},
    {"ann_date": "20261023"},
]


def summarize(res: dict, token: str) -> dict:
    j = res.get("json") or {}
    d = (j.get("data") or {}) if isinstance(j, dict) else {}
    fields, items = d.get("fields") or [], d.get("items") or []
    code = j.get("code") if isinstance(j, dict) else None
    out = {"ms": res.get("ms"), "http": res.get("http"), "code": code, "rows": len(items), "fields": fields,
           "msg": str((j.get("msg") if isinstance(j, dict) else "") or res.get("error") or "")[:140].replace(token, "***"),
           "has_more": d.get("has_more")}
    for c in DATECOLS:
        if c in fields and items:
            i = fields.index(c)
            vals = sorted(str(r[i]) for r in items if r[i])
            if vals:
                out["datecol"], out["date_min"], out["date_max"] = c, vals[0], vals[-1]
                break
    return out


def probe_one(api: str, url: str, token: str, sleep: float) -> dict:
    cands = OVERRIDES.get(api) or GENERIC
    attempts = []
    best = None
    for params in cands[:4]:
        p = dict(params)
        p.setdefault("limit", 50)
        s = None
        for k in range(2):
            r = call(url, token, api, p, "", timeout=90.0)
            s = summarize(r, token)
            if "busy" in s["msg"] or r.get("error"):
                time.sleep(8)
                continue
            break
        time.sleep(sleep)
        s["params"] = {k: v for k, v in p.items() if k != "limit"}
        attempts.append({"params": s["params"], "rows": s["rows"], "ms": s["ms"], "msg": s["msg"][:80]})
        ok = s["code"] == 0
        if ok and s["rows"] > 0:
            s["status"] = "ok"
            best = s
            break
        if ok and (best is None or best.get("status") != "ok"):
            s["status"] = "empty"
            best = best or s
        elif best is None:
            s["status"] = "error"
            best = s
    best = dict(best)
    best["api"] = api
    best["attempts"] = attempts
    if best["status"] == "error" and any(x in best["msg"] for x in ("权限", "未收录", "未开通", "不支持", "not")):
        best["status"] = "no_permission"
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="/tmp/catalog.txt")
    ap.add_argument("--out", default="../docs/teajoin_catalog_probe.json")
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    st = get_settings()
    token, url = st.tushare_teajoin_token.strip(), st.tushare_teajoin_url.strip()
    if not token:
        raise SystemExit("teajoin token 未配置")
    apis = [x.strip() for x in Path(a.catalog).read_text().split() if x.strip() and x.strip() not in SKIP]
    if a.only:
        apis = [x for x in apis if x in set(a.only.split(","))]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(lambda x: probe_one(x, url, token, a.sleep), apis))
    out = {"probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "elapsed_s": round(time.time() - t0),
           "catalog_size": len(apis), "results": results}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    from collections import Counter
    print(Counter(r["status"] for r in results))


if __name__ == "__main__":
    main()
