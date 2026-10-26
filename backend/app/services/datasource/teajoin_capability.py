"""teajoin（DaoShare）能力登记：官方 guide 目录（205 个接口名）逐个实测的结果（docs/teajoin_catalog_probe*.json，2026-10-26 18:50–19:30）。

* TEAJOIN_APIS            实测可用、纳入路由的接口（含 etf_mins 等已验证接口）。做主源的规则见 LeaderSpec（tushare_transport）。
* NOT_ROUTED              目录里有但不路由的接口及原因（stk_mins 已于 2026-10-26 21:00 开通权限并实测通过，转入 TEAJOIN_APIS）。
* INTRADAY_SENSITIVE      盘中（工作日 09:15–15:05）live 进程里延迟敏感的接口：此时 teajoin 不做主源（排末位兜底），
                          盘后与研究回补进程（已限速）仍做主源，把 promax/quantdata 额度留给实盘 rt_k。
* HEAVY / FAST            读超时分档：大表/慢接口 90s、小表 15s，其余 40s（做主源且有备源时；超时立即换源，不重试）。
rt_k / rt_min / anns_d / news / tick / 盘口 不在名单内：teajoin 不提供，保持 promax/quantdata 原链路。
stk_mins（历史分钟）：teajoin 主源；单次最多 8000 行（超出静默截断、保留最新的），client 按 ≤30 天窗口取；指数/ETF 返回空（ETF 用 etf_mins）。
"""
from __future__ import annotations

TEAJOIN_APIS: frozenset[str] = frozenset({
    "adj_factor", "bak_basic", "bak_daily", "balancesheet", "balancesheet_vip", "bc_bestotcqt", "bc_otcqt",
    "block_trade", "bond_blk", "bond_blk_detail", "broker_recommend", "bse_mapping", "cashflow",
    "cashflow_vip", "cb_basic", "cb_call", "cb_daily", "cb_factor_pro", "cb_issue", "cb_rate", "cb_rating",
    "cb_share", "ccass_hold", "ci_daily", "ci_index_member", "cn_cpi", "cn_gdp", "cn_m", "cn_pmi", "cn_ppi",
    "cn_schedule", "cyq_chips", "cyq_perf", "daily", "daily_basic", "daily_info", "dc_concept",
    "dc_concept_cons", "dc_daily", "dc_hot", "dc_index", "dc_member", "disclosure_date", "dividend",
    "eco_cal", "etf_auction", "etf_basic", "etf_index", "etf_limit", "etf_mins", "etf_sh_cons",
    "etf_share_size", "etf_sz_cons", "express", "express_vip", "fina_audit", "fina_indicator",
    "fina_indicator_vip", "fina_mainbz", "fina_mainbz_vip", "forecast", "forecast_vip", "ft_limit",
    "fund_adj", "fund_basic", "fund_company", "fund_daily", "fund_div", "fund_factor_pro", "fund_manager",
    "fund_nav", "fund_portfolio", "fund_share", "fut_basic", "fut_daily", "fut_daily_adj", "fut_holding",
    "fut_index_daily", "fut_inv_weekly", "fut_mapping", "fut_rcpt_mat", "fut_settle", "fut_trade_cal",
    "fut_trade_param", "fut_weekly_detail", "fut_weekly_monthly", "fut_wsr", "fx_daily", "fx_obasic",
    "ggt_daily", "ggt_top10", "gz_index", "hibor", "hk_basic", "hk_hold", "hk_tradecal", "hm_detail",
    "hm_list", "hsgt_top10", "idx_anns", "idx_factor_pro", "income", "income_vip", "index_basic",
    "index_classify", "index_daily", "index_dailybasic", "index_global", "index_member_all",
    "index_monthly", "index_weekly", "index_weight", "kpl_concept", "kpl_concept_cons", "kpl_list", "libor",
    "limit_cpt_list", "limit_list_d", "limit_list_ths", "limit_step", "margin", "margin_detail",
    "margin_secs", "mkt_idx_bmk", "moneyflow", "moneyflow_cnt_ths", "moneyflow_dc", "moneyflow_hsgt",
    "moneyflow_ind_dc", "moneyflow_ind_ths", "moneyflow_mkt_dc", "moneyflow_ths", "monthly", "namechange",
    "new_share", "opt_basic", "opt_daily", "pledge_detail", "pledge_stat", "repo_daily", "report_rc",
    "repurchase", "sf_month", "sge_basic", "sge_daily", "share_float", "shibor", "shibor_lpr",
    "shibor_quote", "slb_len", "slb_len_mm", "slb_sec", "slb_sec_detail", "st", "stk_account",
    "stk_ah_comparison", "stk_alert", "stk_auction", "stk_auction_c", "stk_auction_o", "stk_factor",
    "stk_factor_pro", "stk_high_shock", "stk_holdernumber", "stk_holdertrade", "stk_limit", "stk_managers", "stk_mins",
    "stk_nineturn", "stk_rewards", "stk_seasoned", "stk_shock", "stk_surv", "stk_week_month_adj",
    "stk_weekly_monthly", "stock_basic", "stock_company", "stock_hsgt", "stock_st", "suspend_d", "sw_daily",
    "sz_daily_info", "tdx_daily", "tdx_index", "tdx_member", "ths_daily", "ths_hot", "ths_index",
    "ths_member", "top10_cb_holders", "top10_floatholders", "top10_holders", "top_inst", "top_list",
    "trade_cal", "us_basic", "us_tbr", "us_tltr", "us_tradecal", "us_trltr", "us_trycr", "us_tycr",
    "vix_index", "weekly", "wz_index"
})

NOT_ROUTED: dict[str, str] = {
    "rt_k": "teajoin 不支持实时行情",
    "rt_min": "teajoin 不支持实时分钟",
    "anns_d": "teajoin 不支持公告",
    "news": "teajoin 不支持新闻",
}

INTRADAY_SENSITIVE: frozenset[str] = frozenset({
    "adj_factor", "bak_basic", "bak_daily", "cyq_perf", "daily", "daily_basic", "dc_index", "etf_mins",
    "hm_detail", "hm_list", "index_daily", "kpl_concept_cons", "kpl_list", "limit_cpt_list", "limit_list_d",
    "limit_list_ths", "limit_step", "moneyflow", "moneyflow_dc", "moneyflow_ths", "namechange",
    "stk_auction", "stk_auction_c", "stk_auction_o", "stk_factor", "stk_factor_pro", "stk_limit",
    "stock_basic", "stock_st", "suspend_d", "ths_hot", "ths_index", "ths_member", "top_inst", "top_list",
    "trade_cal"
})

HEAVY_APIS: frozenset[str] = frozenset({
    "bak_daily", "balancesheet", "balancesheet_vip", "block_trade", "cashflow", "cashflow_vip", "cyq_perf",
    "daily_basic", "dc_daily", "dc_index", "dc_member", "express_vip", "fina_indicator",
    "fina_indicator_vip", "fina_mainbz", "fina_mainbz_vip", "forecast_vip", "fund_portfolio", "fut_holding",
    "income", "income_vip", "kpl_concept_cons", "limit_list_d", "margin_detail", "moneyflow",
    "moneyflow_dc", "moneyflow_ths", "report_rc", "stk_factor_pro", "stk_holdernumber", "stk_holdertrade",
    "stk_limit", "sw_daily", "tdx_daily", "ths_daily", "top10_floatholders", "top10_holders"
})

FAST_APIS: frozenset[str] = frozenset({
    "hm_list", "limit_cpt_list", "limit_step", "namechange", "stock_basic", "stock_st", "suspend_d",
    "ths_hot", "top_list", "trade_cal"
})
