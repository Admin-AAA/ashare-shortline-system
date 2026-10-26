"""teajoin（DaoShare，Tushare 协议代理）数据范围探测。严格限速、每个接口少量请求；密钥只从 backend/.env（Settings）读取，绝不打印/落盘。

  PYTHONPATH=. .venv/bin/python scripts/probe_teajoin.py [--out docs/teajoin_probe_results.json] [--sleep 0.6]

每项记录：是否可用、返回字段、行数、延迟、最新/最早日期（按指定日期列）、错误信息。不碰数据库、不碰现有 provider 网关与额度。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import requests

from app.core.config import get_settings

LAST = "20261023"      # 最近一个已收盘且已落库的交易日
TODAY = "20261026"
OLD = "20200102"       # 2020 年第一个交易日


def probes() -> list[dict]:
    p = []

    def add(api, params, note, datecol=None, fields=""):
        p.append({"api": api, "params": params, "note": note, "datecol": datecol, "fields": fields})

    # —— 基础/日线
    add("trade_cal", {"exchange": "SSE", "start_date": "20261020", "end_date": "20261031"}, "交易日历", "cal_date")
    add("stock_basic", {"list_status": "L", "limit": 5}, "股票列表(仅取5条)")
    add("daily", {"trade_date": LAST}, "日线·最近交易日全市场", "trade_date")
    add("daily", {"trade_date": TODAY}, "日线·今天(盘后是否已出)", "trade_date")
    add("daily", {"trade_date": OLD}, "日线·2020-01-02全市场", "trade_date")
    add("daily_basic", {"trade_date": LAST}, "每日指标", "trade_date")
    add("adj_factor", {"trade_date": LAST}, "复权因子", "trade_date")
    add("stk_limit", {"trade_date": LAST}, "涨跌停价", "trade_date")
    add("suspend_d", {"trade_date": LAST}, "停复牌", "trade_date")
    add("bak_daily", {"trade_date": LAST}, "备用日线(bak_daily)", "trade_date")
    add("bak_basic", {"trade_date": LAST}, "备用基础(bak_basic)", "trade_date")
    add("index_daily", {"ts_code": "000905.SH", "start_date": "20261016", "end_date": "20261026"}, "指数日线", "trade_date")
    add("stock_st", {"trade_date": LAST}, "ST名单(stock_st)", "trade_date")
    add("namechange", {"ts_code": "000001.SZ"}, "曾用名")
    # —— 涨停/打板
    add("limit_list_d", {"trade_date": LAST}, "涨跌停榜 最近交易日", "trade_date")
    add("limit_list_d", {"trade_date": OLD}, "涨跌停榜 2020-01-02", "trade_date")
    add("limit_list_d", {"trade_date": "20200821"}, "涨跌停榜 2020-08-21(本地文件停在这天)", "trade_date")
    add("limit_list_d", {"trade_date": TODAY}, "涨跌停榜 今天", "trade_date")
    add("limit_list_ths", {"trade_date": LAST, "limit_type": "涨停池"}, "同花顺涨停池", "trade_date")
    add("limit_step", {"trade_date": LAST}, "连板天梯", "trade_date")
    add("limit_cpt_list", {"trade_date": LAST}, "涨停最强板块", "trade_date")
    add("kpl_list", {"trade_date": LAST, "tag": "涨停"}, "开盘啦涨停榜", "trade_date")
    add("kpl_list", {"trade_date": OLD, "tag": "涨停"}, "开盘啦 2020-01-02", "trade_date")
    add("top_list", {"trade_date": LAST}, "龙虎榜", "trade_date")
    add("top_inst", {"trade_date": LAST}, "龙虎榜机构", "trade_date")
    add("hm_list", {}, "游资名录")
    add("hm_detail", {"trade_date": LAST}, "游资每日明细", "trade_date")
    # —— 竞价
    add("stk_auction", {"trade_date": LAST}, "集合竞价(早盘用) 最近交易日", "trade_date")
    add("stk_auction_o", {"trade_date": LAST}, "开盘竞价(盘后) 最近交易日", "trade_date")
    add("stk_auction_c", {"trade_date": LAST}, "收盘竞价(盘后) 最近交易日", "trade_date")
    add("stk_auction_o", {"trade_date": OLD}, "开盘竞价 2020-01-02", "trade_date")
    add("stk_auction_o", {"trade_date": "20230103"}, "开盘竞价 2023-01-03", "trade_date")
    add("stk_auction_o", {"trade_date": "20250102"}, "开盘竞价 2025-01-02", "trade_date")
    add("stk_auction_o", {"trade_date": TODAY}, "开盘竞价 今天", "trade_date")
    # —— 分钟/实时/tick
    add("stk_mins", {"ts_code": "000001.SZ", "freq": "1min", "start_date": "2026-10-23 09:30:00", "end_date": "2026-10-23 09:35:00"}, "1分钟K 最近交易日", "trade_time")
    add("stk_mins", {"ts_code": "000001.SZ", "freq": "1min", "start_date": "2024-01-03 09:30:00", "end_date": "2024-01-03 09:35:00"}, "1分钟K 2024-01", "trade_time")
    add("stk_mins", {"ts_code": "000001.SZ", "freq": "1min", "start_date": "2020-01-03 09:30:00", "end_date": "2020-01-03 09:35:00"}, "1分钟K 2020-01", "trade_time")
    add("etf_mins", {"ts_code": "510300.SH", "freq": "1min", "start_date": "2026-10-23 09:30:00", "end_date": "2026-10-23 09:35:00"}, "ETF分钟K", "trade_time")
    add("rt_k", {"ts_code": "000001.SZ"}, "实时日线rt_k")
    add("rt_min", {"ts_code": "000001.SZ", "freq": "1MIN"}, "实时分钟rt_min")
    add("rt_tick", {"ts_code": "000001.SZ"}, "实时逐笔rt_tick(猜测接口名)")
    add("realtime_tick", {"ts_code": "000001.SZ", "src": "sina"}, "realtime_tick(猜测)")
    add("realtime_quote", {"ts_code": "000001.SZ"}, "realtime_quote(猜测，盘口)")
    add("stk_ticks", {"ts_code": "000001.SZ", "trade_date": LAST}, "stk_ticks(猜测，逐笔)")
    # —— 资金/筹码/因子/融资
    add("moneyflow", {"trade_date": LAST}, "个股资金流", "trade_date")
    add("moneyflow_dc", {"trade_date": LAST}, "东财资金流", "trade_date")
    add("moneyflow_ths", {"trade_date": LAST}, "同花顺资金流", "trade_date")
    add("cyq_perf", {"trade_date": LAST}, "筹码及胜率", "trade_date")
    add("cyq_perf", {"trade_date": "20260730"}, "筹码 2026-07-30(我们的空洞日)", "trade_date")
    add("cyq_perf", {"trade_date": "20260825"}, "筹码 2026-08-25(空洞日)", "trade_date")
    add("cyq_perf", {"trade_date": "20260831"}, "筹码 2026-08-31(空洞日)", "trade_date")
    add("cyq_perf", {"trade_date": "20260909"}, "筹码 2026-09-09(空洞日)", "trade_date")
    add("stk_factor_pro", {"trade_date": LAST}, "专业因子", "trade_date")
    add("stk_factor", {"trade_date": LAST}, "技术因子", "trade_date")
    add("margin_detail", {"trade_date": LAST}, "融资融券明细", "trade_date")
    add("margin", {"trade_date": LAST}, "融资融券汇总", "trade_date")
    add("block_trade", {"trade_date": LAST}, "大宗交易", "trade_date")
    add("ths_hot", {"trade_date": LAST}, "同花顺热点", "trade_date")
    # —— 概念板块
    add("ths_index", {}, "同花顺板块列表(看是否全量~2500)")
    add("ths_member", {"ts_code": "885312.TI"}, "同花顺板块成分")
    add("dc_index", {"trade_date": LAST}, "东财概念板块", "trade_date")
    add("kpl_concept_cons", {"trade_date": LAST}, "开盘啦题材成分", "trade_date")
    # —— 财务/事件
    add("fina_indicator", {"ts_code": "000001.SZ", "limit": 3}, "财务指标(单股)")
    add("fina_indicator_vip", {"period": "20260630"}, "财务指标VIP(全市场按期)", "end_date")
    add("income_vip", {"period": "20260630"}, "利润表VIP", "end_date")
    add("forecast", {"ann_date": LAST}, "业绩预告", "ann_date")
    add("express", {"ann_date": LAST}, "业绩快报", "ann_date")
    add("repurchase", {"ann_date": LAST}, "回购", "ann_date")
    add("share_float", {"ann_date": LAST}, "限售解禁", "ann_date")
    add("stk_holdertrade", {"ann_date": LAST}, "股东增减持", "ann_date")
    add("stk_holdernumber", {"ann_date": LAST}, "股东人数", "ann_date")
    add("dividend", {"ann_date": LAST}, "分红", "ann_date")
    add("anns_d", {"ann_date": LAST}, "公告 anns_d", "ann_date")
    add("news", {"src": "sina", "start_date": "2026-10-23 09:00:00", "end_date": "2026-10-23 09:30:00"}, "新闻(文档说不含)")
    return p


def call(url: str, token: str, api: str, params: dict, fields: str, timeout: float = 60.0) -> dict:
    t0 = time.perf_counter()
    try:
        r = requests.post(url, json={"api_name": api, "token": token, "params": params, "fields": fields}, timeout=timeout,
                          proxies={"http": "", "https": ""})
        ms = round((time.perf_counter() - t0) * 1000)
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return {"http": r.status_code, "ms": ms, "error": "non-json: " + r.text[:120].replace(token, "***")}
        return {"http": r.status_code, "ms": ms, "json": j}
    except Exception as exc:  # noqa: BLE001
        return {"http": None, "ms": round((time.perf_counter() - t0) * 1000), "error": f"{type(exc).__name__}: {str(exc)[:120]}".replace(token, "***")}


def summarize(res: dict, datecol: str | None, token: str) -> dict:
    out = {"http": res.get("http"), "ms": res.get("ms")}
    if "error" in res:
        out["status"] = "error"
        out["error"] = res["error"]
        return out
    j = res["json"]
    code, msg = j.get("code"), str(j.get("msg") or "")
    data = j.get("data") or {}
    items, flds = data.get("items") or [], data.get("fields") or []
    out.update({"code": code, "msg": msg.replace(token, "***")[:160], "rows": len(items), "fields": flds,
                "has_more": data.get("has_more")})
    if code not in (0, "0", None):
        out["status"] = "error"
    else:
        out["status"] = "ok" if items else "empty"
    if items and datecol and datecol in flds:
        i = flds.index(datecol)
        vals = sorted(str(r[i]) for r in items if r[i] is not None)
        if vals:
            out["date_min"], out["date_max"] = vals[0], vals[-1]
    if items:
        out["sample_row"] = dict(zip(flds, items[0]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/teajoin_probe_results.json")
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--only", default="", help="只跑指定接口名（逗号分隔）")
    args = ap.parse_args()
    st = get_settings()
    token, url = st.tushare_teajoin_token.strip(), st.tushare_teajoin_url.strip()
    if not token:
        raise SystemExit("未配置 ASHARE_TUSHARE_TEAJOIN_TOKEN")
    only = {x for x in args.only.split(",") if x}
    results = []
    for pr in probes():
        if only and pr["api"] not in only:
            continue
        res = call(url, token, pr["api"], pr["params"], pr["fields"])
        s = summarize(res, pr["datecol"], token)
        results.append({"api": pr["api"], "params": pr["params"], "note": pr["note"], **s})
        print(f"{s.get('status','?'):6} {pr['api']:18} {pr['note'][:28]:30} rows={s.get('rows')} {s.get('ms')}ms "
              f"{s.get('date_min','')}~{s.get('date_max','')} {s.get('msg','') or s.get('error','')}"[:200], flush=True)
        time.sleep(args.sleep)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "results": results},
                              ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
