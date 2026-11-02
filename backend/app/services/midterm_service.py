"""[中长线] 中线策略：选股、持仓跟踪与回测画像。

与短线 replay 完全独立：信号在周五收盘后生成，次日开盘买入，固定持有 20 个交易日后开盘卖出。
收益用 pct_chg 复权（daily_kbars 是不复权价）。回测脚本：scripts/midterm/research4.py。
"""
from __future__ import annotations

import bisect
import datetime as _dt
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[2] / "output" / "midterm" / "picks"
METRICS_FILE = Path(__file__).resolve().parent / "midterm_backtest_metrics.json"
EVENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm" / "events"
HOLD_DAYS = 20

STRATEGIES: dict[str, dict[str, Any]] = {
    "mid-small-cap-weekly": {
        "name": "小市值周度分批（小盘趋势过滤）",
        "horizon": "mid",
        "summary": "每个周五收盘后，选全A（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）总市值最小的10只，次日开盘买入、各持有20个交易日。仅当小盘指数（最小400只等权）站上20日均线时开新仓，避开 2024 年初那类微盘踩踏。",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §277 低价股×小市值（scripts/midterm/lt_research104.py LPS_W，邢不行文章，预注册、未调参；2026-10-25 起直接上线）
    "mid-lowprice-smallcap-weekly": {
        "name": "低价股×小市值周度（小盘趋势过滤，持20日）",
        "horizon": "mid",
        "summary": "每个周五收盘后，在全A（剔除ST/退市整理、北交所、上市不足一年、20日均成交额<3000万、收盘价≤2元；不要求盈利）里，同时满足「收盘价最低的200只之一」和「总市值最小的200只之一」的股票，按总市值从小到大取前10只，次日开盘买入、各持有20个交易日。与「小市值周度」相同：仅当小盘指数（最小400只等权）站上20日均线时开新仓。思路来自邢不行「低价股效应」：散户的「便宜错觉」+ 超跌反转。",
        "kind": "lowprice_small",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §312 小盘高股息（scripts/midterm/lt_research118.py SDY_W，邢不行小市值框架+红利，预注册、未调参；2026-10-25 起直接上线）
    "mid-smallcap-dividend-weekly": {
        "name": "小盘高股息周度（小盘趋势过滤，持20日）",
        "horizon": "mid",
        "summary": "每个周五收盘后，先取与「小市值周度」相同的合格股（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）里总市值最小的400只（即小盘指数成分），再在其中挑股息率TTM（过去12个月现金分红/市值）最高的10只，次日开盘买入、各持有20个交易日。与「小市值周度」相同：仅当小盘指数站上20日均线时开新仓。思路：同样吃小盘溢价，但挑小票里分红最多、最像现金奶牛的，避开不分红小票的ST/退市风险；与「小市值周度」选股几乎不重叠。",
        "kind": "smallcap_div",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §314 小盘低市销率（scripts/midterm/lt_research119.py SPS_W，邢不行小市值框架+市销率，预注册、未调参；2026-10-25 起直接上线）
    "mid-smallcap-lowps-weekly": {
        "name": "小盘低市销率周度（小盘趋势过滤，持20日）",
        "horizon": "mid",
        "summary": "每个周五收盘后，先取与「小市值周度」相同的合格股（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）里总市值最小的400只（即小盘指数成分），再在其中挑市销率PS_TTM（总市值/过去12个月营收）最低的10只，次日开盘买入、各持有20个交易日。与「小市值周度」相同：仅当小盘指数站上20日均线时开新仓。思路：同样吃小盘溢价，但用比利润更稳定、更难修饰的营收来挑“市值相对生意规模最便宜”的小票；与「小市值周度」「小盘高股息周度」「低价小市值周度」选股几乎不重叠。",
        "kind": "smallcap_lowps",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §315-D 小盘反转（scripts/midterm/lt_research120.py SMR_W，小市值周度框架+近20日跌幅最大，预注册、未调参；2026-10-25 起直接上线）
    "mid-smallcap-reversal-weekly": {
        "name": "小盘超跌反转周度（小盘趋势过滤，持20日）",
        "horizon": "mid",
        "summary": "每个周五收盘后，先取与「小市值周度」相同的合格股（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）里总市值最小的400只（即小盘指数成分），再在其中挑近20个交易日跌幅最大的10只（小盘里被错杀、最可能反弹的一批，与「最小10只」「低价」「高股息」「低市销率」几乎不重合），下周一开盘买入、持有20个交易日；小盘指数在20日均线下方时不开新仓。",
        "kind": "smallcap_rev",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §91 事件型（scripts/midterm/lt_research12.py 预注册 6 版中达标的 2 版；同市值五分位对照算超额；mid 版 §100 加金额≥0.5%）
    "mid-repurchase-plan": {
        # §100 预注册 RP_r05（scripts/midterm/lt_research18.py，未调参）：在 §91 R_plan_h20 基础上只保留金额上限≥总市值0.5%的预案
        "name": "回购预案事件（金额≥市值0.5%，持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的股票回购「预案」公告（同一股票365天内只取第一次），只保留回购金额上限≥总市值0.5%的（剔除象征性小额回购），剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按回购金额上限占总市值比例从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。",
        "kind": "repurchase",
        "min_ratio": 0.005,
        "hold_days": 20,
        "schedule": "daily",
    },
    # §364 回购预案弱反应（scripts/shortline_research/rpu_s364.py RPU_h5；§282 短线 8 段全过；2026-11-01 上线）
    "mid-repurchase-underreact": {
        "name": "回购预案弱反应（金额≥市值0.5%，持5日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「回购预案金额≥市值0.5%」事件池上，只保留信号日（公告后首个交易日）涨跌落在−1%~+2%、且收盘未封涨停的票；按信号日涨跌从低到高（越弱反应优先，§398），次日开盘买入，各持有5个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：公司大额回购是硬信号，首日几乎没定价时短线反应不足；§364 上线后 §398 改排序键。",
        "kind": "repurchase_underreact",
        "min_ratio": 0.005,
        "hold_days": 5,
        "schedule": "daily",
    },

    # §461 回购预案弱反应持20（scripts/midterm/rpu20_s461.py RPU20_h20；§282 达标；2026-11-01 上线）
    "mid-repurchase-plan-underreact": {
        "name": "回购预案弱反应（金额≥市值0.5%，持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「回购预案金额≥市值0.5%」事件池上，只保留信号日涨跌落在−1%~+2%、且收盘未封涨停的票；按信号日涨跌从低到高（越弱反应优先），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：短线 RPU 持5已上线；§461 证中线持20 亦达标。与 mid-repurchase-underreact（持5）仅持有期不同。",
        "kind": "repurchase_underreact",
        "min_ratio": 0.005,
        "hold_days": 20,
        "schedule": "daily",
    },
    # §95 新权重复评（scripts/midterm/lt_research15.py EA_h20，即 §93 G_h20 原条件，未调参）
    "mid-earnings-accel": {
        "name": "财报业绩加速（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（年报/季报，同一报告期只取第一次公告）：扣非净利同比≥50%、营收同比≥20%、扣非净利为正、且扣非同比高于上一期报告（加速）。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万。排序分（§RV-C EA_combo）= 当日这批候选里「信号日涨跌幅」平均秩百分位 + 「总市值从小到大」平均秩百分位，两者等权、高者优先，平手按代码；次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。",
        "kind": "earnings_accel",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §489 标准化意外∩业绩加速（scripts/midterm/sueg_s489.py SUEG_h20；§282 达标；2026-11-01 上线）
    "mid-sue-accel": {
        "name": "标准化意外∩业绩加速（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「财报业绩加速」事件池上再要求标准化意外盈利 SUE≥2（单季扣非相对去年同季的变化 ÷ 该公司过去8期该变化的标准差；单季扣非须为正）。按 min(SUE,20) 从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：Bernard–Thomas SUE 与业绩加速合取（§489），两路盈余信息同向。",
        "kind": "sue_accel",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §513 业绩加速∩放量（scripts/midterm/eavol_s513.py EAVOL_h20；§282 达标；2026-11-01 上线）
    "mid-earnings-accel-vol": {
        "name": "业绩加速∩放量确认（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「财报业绩加速」事件池上再要求信号日成交额≥1.5×前20日均额。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按扣非净利同比（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：PEAD 量能确认（§513），盈余信息放量交易时漂移更稳。",
        "kind": "earnings_accel_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §515 SUEG∩放量（scripts/midterm/suegvol_s515.py SUEGVOL_h20；§282 达标；2026-11-01 上线）
    "mid-sue-accel-vol": {
        "name": "标准化意外∩加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「SUE≥2.2 ∩ 业绩加速」事件池上再要求信号日成交额≥1.5×前20日均额。按 min(SUE,20) 从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：Bernard–Thomas SUE × PEAD 加速 × 量能确认（§515→§604 硬抬 SUE≥2.2）。",
        "kind": "sue_accel_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §103 业绩预告正向漂移（scripts/midterm/lt_research20.py F_any_h20，预注册 3 版中采纳的一版，未调参；实盘候选 app/services/midterm_forecast.py）
    "mid-forecast-beat": {
        "name": "业绩预告首次预喜（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的业绩预告，同一报告期只认公司第一次预告（修正公告不算）：类型为预增/扭亏/略增、预告净利同比增幅下限>0、预告净利润下限>0。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。信号集中在1/4/7/10月预告季。",
        "kind": "forecast_beat",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §455 预告合集弱反应（scripts/midterm/fau_s455.py FAU_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-beat-underreact": {
        "name": "业绩预告首次预喜弱反应（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告预增/扭亏/略增」事件池上，只保留信号日涨跌落在−1%~+2%、且收盘未封涨停的票；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：预告合集 PEAD + 首日弱反应过滤（§455）；与 mid-forecast-beat 全量及单拆弱反应子集不同。",
        "kind": "forecast_beat_underreact",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §429/§436 业绩预告扭亏（ftz_s429 / lt_research181；§436 改按增幅排序；2026-11-01 上线）
    "mid-forecast-turnaround": {
        "name": "业绩预告扭亏（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的业绩预告，同一报告期只认公司第一次预告：类型为扭亏、预告净利润下限>0。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按预告增幅下限从高到低（§436：缺增幅时回退净利下限；事件门槛不变），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：扭亏 PEAD；与现役 mid-forecast-beat / preincrease / slight 事件子集或排序不同。",
        "kind": "forecast_turnaround",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §433 业绩预告预增单拆（scripts/midterm/fzg_s433.py FZG_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-preincrease": {
        "name": "业绩预告预增（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的业绩预告，同一报告期只认公司第一次预告：类型为预增、预告净利同比增幅下限>0、预告净利润下限>0。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：预增 PEAD；与现役 mid-forecast-beat（预增/扭亏/略增）及 mid-forecast-turnaround（扭亏）事件子集不同。",
        "kind": "forecast_preincrease",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §451 预告预增弱反应（scripts/midterm/fzgu_s451.py FZGU_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-preincrease-underreact": {
        "name": "业绩预告预增弱反应（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告预增」事件池上，只保留信号日涨跌落在−1%~+2%、且收盘未封涨停的票；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：预增 PEAD + 首日弱反应过滤（§451）；与 mid-forecast-preincrease 全量事件不同。",
        "kind": "forecast_preincrease_underreact",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §517 预增∩放量（scripts/midterm/fzgvol_s517.py FZGVOL_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-preincrease-vol": {
        "name": "业绩预告预增∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告预增」事件池上再要求信号日成交额≥1.5×前20日均额。按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：预增 PEAD × 量能确认（§517）。",
        "kind": "forecast_preincrease_vol",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §549 预增∩弱反应∩放量（scripts/midterm/fzguvol_s549.py FZGUVOL_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-preincrease-underreact-vol": {
        "name": "业绩预告预增弱反应∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告预增」事件池上，只保留信号日涨跌落在−1%~+2%、未封涨停，且成交额≥1.5×前20日均额的票；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：预增 PEAD × 弱反应 × 量能确认（§549）。",
        "kind": "forecast_preincrease_underreact_vol",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §435/§438 业绩预告略增（flz_s435 / lt_research182；§438 改按净利下限排序；2026-11-01 上线）
    "mid-forecast-slight": {
        "name": "业绩预告略增（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的业绩预告，同一报告期只认公司第一次预告：类型为略增、预告净利同比增幅下限>0、预告净利润下限>0。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按预告净利下限从高到低（§438：事件门槛不变，排序由增幅改为净利下限），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：略增 PEAD；与 mid-forecast-beat / preincrease / turnaround 事件子集或排序不同。",
        "kind": "forecast_slight",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §453 预告略增弱反应（scripts/midterm/flzu_s453.py FLZU_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-slight-underreact": {
        "name": "业绩预告略增弱反应（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告略增」事件池上，只保留信号日涨跌落在−1%~+2%、且收盘未封涨停的票；按预告净利下限从高到低（与 §438 略增全量排序一致），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：略增 PEAD + 首日弱反应过滤（§453）；与 mid-forecast-slight 全量事件不同。",
        "kind": "forecast_slight_underreact",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §519 略增∩放量（scripts/midterm/flzvol_s519.py FLZVOL_h20；§282 达标；2026-11-01 上线）
    "mid-forecast-slight-vol": {
        "name": "业绩预告略增∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次业绩预告略增」事件池上再要求信号日成交额≥1.5×前20日均额。按预告净利下限从高到低（与 §438 一致），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：略增 PEAD × 量能确认（§519）。",
        "kind": "forecast_slight_vol",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §109 股权激励草案（含股票期权）公告后漂移（原按 −r20；§402 改为按信号日 −pct 排序，事件门槛不变；实盘候选 app/services/midterm_incentive.py）
    "mid-incentive-option": {
        "name": "股票期权激励草案首次公告（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今巨潮资讯新披露的股权激励计划「草案」（修订/更正/调整/终止类不算），只认公司180天内第一次披露、且计划含股票期权（期权或期权+限制性股票）的。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按信号日涨跌幅从低到高（§402：越弱反应优先；事件门槛不变），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。",
        "kind": "incentive_option",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §459 激励期权弱反应硬过滤（scripts/midterm/iou_s459.py IOU_h20；§282 达标；2026-11-01 上线）
    "mid-incentive-underreact": {
        "name": "股权激励期权弱反应（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次含股票期权的激励草案」事件池上，只保留信号日涨跌落在−1%~+2%、且收盘未封涨停的票；按信号日涨跌从低到高（越弱反应优先，与 §402 一致），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：激励公告 + 首日弱反应硬过滤（§459）；与 mid-incentive-option 全量事件不同。",
        "kind": "incentive_underreact",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §523/§722 激励期权∩放量（§722 放量≥1.8×；scripts/midterm/iovol_v18_s722.py；§282 达标）
    "mid-incentive-vol": {
        "name": "股权激励期权∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「首次含股票期权的激励草案」事件池上再要求信号日成交额≥1.8×前20日均额（§722：放量 1.5×→1.8×）。按信号日涨跌从低到高（§402：越弱反应优先），次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：激励治理信号 × 量能确认（§523）+ 放量硬抬（§722）。",
        "kind": "incentive_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §114 大宗交易溢价成交后漂移（原 BT_prem_h20 按 ratio 排序；§388 改为按 premium 排序；§474 ratio 0.5%→0.45%；§490 prem≥0.5%；§498 prem≥1%；§512 prem≥1.2%；§514 prem≥1.35%；实盘候选 app/services/midterm_block_trade.py）
    # Tushare 大宗交易约 21:00 发布：17:35 盘后流水线通常 no_data，需 21:00 后 / 次日开盘前调用 generate_block_trade_picks(db, T) 出票
    "mid-block-trade-premium": {
        "name": "大宗交易溢价成交（持20日）",
        "horizon": "mid",
        "summary": "每个交易日晚间（交易所/Tushare 约21:00 发布当日大宗交易后），汇总每只股票当天全部大宗成交：成交量加权的大宗成交价相对收盘溢价≥1.35%，且大宗成交总额≥流通市值0.45%。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按大宗成交溢价率（量加权价相对收盘）从高到低，次日开盘买入，各持有20个交易日（§388 排序=溢价；§474：ratio≥0.45%；§498：prem≥1%；§512：prem≥1.2%；§514：prem≥1.35%）。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。",
        "kind": "block_trade_premium",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §357 销售费用率同比下降（scripts/midterm/lt_research139.py SEXP_h20 = §198 规则；§282 绝对收益标准复判达标；2026-10-31 上线）
    "mid-sales-exp-drop": {
        "name": "销售费用率同比下降（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告：销售费用率（saleexp_to_gr，销售费用/营业总收入）本期∈[0.2,40]、去年同报告期∈[0.2,60]，且同比下降≥0.5个百分点（§426：原≥1.0）。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按降幅（封顶10个百分点）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：费用纪律改善事件；§357上线后§426放宽降幅门槛（事件其余不变）。",
        "kind": "sexp_yoy",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §525/§536/§550/§694/§718 销售费用率下降∩放量（降幅≥0.35；§718 放量≥1.8×；§694 排序去封顶；scripts/midterm/sexpvol_v18_s718.py）
    "mid-sales-exp-vol": {
        "name": "销售费用率下降∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「销售费用率同比下降≥0.35个百分点」事件池上再要求信号日成交额≥1.8×前20日均额（§550：降幅 0.4→0.35；§718：放量 1.5×→1.8×）。按降幅（原始百分点，§694 起不再封顶10）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：费用纪律事件 × 量能确认（§525）+ 降幅再软扩（§550）+ 排序去封顶（§694）+ 放量硬抬（§718）。",
        "kind": "sexp_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §359 财务费用率同比下降（scripts/midterm/lt_research140.py FINA_h20 = §203 规则；§282 绝对收益标准复判达标；2026-10-31 晚间上线）
    "mid-fina-exp-drop": {
        "name": "财务费用率同比下降（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告：财务费用率（finaexp_of_gr，财务费用/营业总收入）本期∈[-2,15]、去年同报告期∈[-2,25]，且同比下降≥0.5个百分点。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按降幅（原始百分点，§396 起不再封顶8）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：融资成本负担减轻事件；§359 上线后 §396 去掉排序封顶（事件门槛不变）。",
        "kind": "fina_yoy",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §527 财务费用率下降∩放量（scripts/midterm/finavol_s527.py FINAVOL_h20；§282 达标；2026-11-01 上线）
    "mid-fina-exp-vol": {
        "name": "财务费用率下降∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「财务费用率同比下降≥0.5个百分点」事件池上再要求信号日成交额≥1.5×前20日均额。按降幅（原始百分点，§396 去封顶）从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：融资成本减轻事件 × 量能确认（§527）。",
        "kind": "fina_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §421 分红上调持20（scripts/midterm/lt_research48.py DINC_h20 = §158 规则；§282 绝对收益复判达标；2026-11-01 上线）
    "mid-dividend-increase": {
        "name": "现金分红同比增加（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次公布的分红「预案」（同一报告期只取第一次预案）：每股税前派现>0，且较去年同一报告期（优先取实施记录，否则取最早预案）提高 ≥30%（去年同期>0）；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按增幅从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：分红上调信号（Michaely–Thaler–Womack）；§158 旧超额标准否决后，§421 按§282绝对收益复判达标上线。与 /long 同事件持60日版并存。",
        "kind": "dinc_yoy",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §535 分红增加∩放量（scripts/midterm/dincvol_s535.py DINCVOL_h20；§282 达标；2026-11-01 上线）
    "mid-dividend-increase-vol": {
        "name": "现金分红同比增加∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，在「现金分红同比增加≥30%」事件池上再要求信号日成交额≥1.5×前20日均额。按增幅从高到低，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：分红上调 × 量能确认（§535）。",
        "kind": "dinc_vol",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §599 营业周期缩短∩放量（scripts/midterm/tdvol_s599.py TDVOL_h20；§282 达标；2026-11-01 上线）
    "mid-turn-days-vol": {
        "name": "营业周期缩短∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：营业周期（turn_days）同比缩短≥20日（本期∈[30,400]、去年∈[30,800]），且信号日成交额≥1.5×前20日均额；按缩短天数（封顶200）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：营运效率改善 × 量能确认（§599 / §184）。",
        "kind": "td_vol",
        "hold_days": 20,
        "schedule": "daily",
    },

    # §615 固定资产周转扩张∩放量（scripts/midterm/fatvol_s615.py FATVOL_h20；§282 达标；2026-11-01 上线）
    "mid-fa-turn-vol": {
        "name": "固定资产周转扩张∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：固定资产周转率（fa_turn）同比升幅≥0.10 且本期≥0.5，且信号日成交额≥1.5×前20日均额；按升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：固定资产周转扩张 × 量能确认（§615 / §205）。",
        "kind": "fat_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §619/§620 有形资产/负债改善∩放量（升幅≥0.25；§620 硬抬 ADOPT；2026-11-01 上线）
    "mid-tad-vol": {
        "name": "有形资产/负债改善∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：有形资产/负债（tangibleasset_to_debt）本期∈[0.3,5]、去年同报告期∈[0.2,8]且同比升幅≥0.25，且信号日成交额≥1.5×前20日均额；按升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：有形资产覆盖负债改善 × 量能确认（§619/§620 / §234）。",
        "kind": "tad_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §621 股东权益/负债改善∩放量（scripts/midterm/etdvol_s621.py ETDVOL_h20；§282 达标；2026-11-01 上线）
    "mid-etd-vol": {
        "name": "股东权益/负债改善∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：股东权益/负债（eqt_to_debt）本期∈[0.3,5]、去年同报告期∈[0.2,8]且同比升幅≥0.20，且信号日成交额≥1.5×前20日均额；按升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：股东权益覆盖负债改善 × 量能确认（§621 / §236）。",
        "kind": "etd_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §623 流动资产占比上升∩放量（scripts/midterm/casvol_s623.py CASVOL_h20；§282 达标；2026-11-01 上线）
    "mid-cas-vol": {
        "name": "流动资产占比上升∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：流动资产/总资产（ca_to_assets）本期∈[20,95]、去年同报告期∈[10,100]且同比升幅≥3.0个百分点，且信号日成交额≥1.5×前20日均额；按升幅（封顶25）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：流动资产占比上升 × 量能确认（§623 / §242）。",
        "kind": "cas_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §661 FAT∩TD∩放量（scripts/midterm/fattdvol_s661.py FATTDVOL_h20；§282 达标；2026-11-02 上线）
    "mid-fa-td-vol": {
        "name": "固定资产周转∩营业周期∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足固定资产周转率（fa_turn）升幅≥0.12且本期≥0.5，与营业周期（turn_days）同比缩短≥20日，且信号日成交额≥1.5×前20日均额；按固定资产周转升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：效率双改善 × 量能确认（§661）。",
        "kind": "fat_td_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §663/§750 ETD∩FAT∩放量（scripts/midterm/etdfatvol_v18_s750.py ETDFATVOL_v18_h20；§750 放量≥1.8×；2026-11-02 上线）
    "mid-etd-fa-vol": {
        "name": "股东权益/负债∩固定资产周转∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足股东权益/负债（eqt_to_debt）升幅≥0.20，与固定资产周转率（fa_turn）升幅≥0.10且本期≥0.5，且信号日成交额≥1.8×前20日均额（§750：放量 1.5×→1.8×）；按股东权益/负债升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：BS∩效率双改善 × 量能确认（§663）+ 放量硬抬（§750）。",
        "kind": "etd_fat_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §656 TAD∩ETD∩放量（scripts/midterm/tadetdvol_s656.py TADETDVOL_h20；§282 达标；2026-11-02 上线）
    "mid-tad-etd-vol": {
        "name": "有形资产/负债∩股东权益/负债∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足有形资产/负债（tangibleasset_to_debt）升幅≥0.25 与股东权益/负债（eqt_to_debt）升幅≥0.20，且信号日成交额≥1.5×前20日均额；按有形资产/负债升幅（封顶2.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：资产负债表双改善 × 量能确认（§656）。",
        "kind": "tad_etd_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §669 SUEG∩TD∩放量（scripts/midterm/suegtdvol_s669.py SUEGTDVOL_h20；§282 达标；2026-11-02 上线）
    "mid-sue-td-vol": {
        "name": "盈余加速∩营业周期∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足 SUE≥2.2 且业绩加速（G，扣非同比≥55%且加速、营收同比≥25%；§782：原 orv≥20），与营业周期（turn_days）同比缩短≥20日，且信号日成交额≥1.5×前20日均额；按 SUE（封顶20）从高到低排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：盈余惊喜 × 周期效率 × 量能确认（§669/§762 dtp硬抬/§782 orv硬抬）。",
        "kind": "sue_td_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §671 SUEG∩FAT∩放量（scripts/midterm/suegfatvol_s671.py SUEGFATVOL_h20；§282 达标；2026-11-02 上线）
    "mid-sue-fa-vol": {
        "name": "盈余加速∩固定资产周转∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足 SUE≥2.2 且业绩加速（G），与固定资产周转率（fa_turn）升幅≥0.10且本期≥0.5，且信号日成交额≥1.5×前20日均额；按 SUE（封顶20）从高到低排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：盈余惊喜 × 资产周转效率 × 量能确认（§671）。",
        "kind": "sue_fa_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §699–§712 业绩加速∩营业周期∩放量（§712 dtp≥60；§702 TD≥25；§700 TD排序；scripts/midterm/eatdvol_d60_s712.py）
    "mid-ea-td-vol": {
        "name": "业绩加速∩营业周期∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足业绩加速（G：扣非净利同比≥60%且加速、营收同比≥20%、扣非净利>0；§712：原 dtp≥55），与营业周期（turn_days）同比缩短≥25日（§702），且信号日成交额≥1.5×前20日均额；按营业周期缩短天数（封顶200，§700）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：业绩加速 × 周期效率 × 量能确认（§699）+ TD排序（§700）。",
        "kind": "ea_td_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §763 业绩加速∩流动资产占比∩放量（scripts/midterm/eacasvol_s763.py EACASVOL_h20；§282 达标；2026-11-02 上线）
    "mid-ea-cas-vol": {
        "name": "业绩加速∩流动资产占比∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足业绩加速（G：扣非净利同比≥50%且加速、营收同比≥20%、扣非净利>0），与流动资产占总资产比重升幅≥3.0个百分点，且信号日成交额≥1.5×前20日均额；按扣非净利同比（封顶300）从高到低排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：业绩加速 × 资产流动性结构 × 量能确认（§763）。",
        "kind": "ea_cas_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §793/§798/§800 销售费用∩盈余加速∩放量（scripts/midterm/sexpsuegvol_o45_s810.py SEXPSUEGVOL_o45_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-sexp-sue-vol": {
        "name": "销售费用∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足销售费用/营业总收入同比下降≥0.35 个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥60、营收同比≥45、扣非>0、同比加速；§802 dtp≥60 / §810 orv≥45 硬抬），且信号日成交额≥1.5×前20日均额；按销售费用率降幅（去封顶）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：费用率改善 × 盈余惊喜加速 × 量能确认（§793/§798/§800/§802/§804/§806/§808/§810）。",
        "kind": "sexp_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §801 管理费用∩盈余加速∩放量（scripts/midterm/adminsuegvol_o30_s809.py ADMINSUEGVOL_o30_h20；§282 达标；2026-11-02 上线）
    "mid-admin-sue-vol": {
        "name": "管理费用∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足管理费用/营业总收入同比下降≥1.0 个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥30；§803 dtp / §809 orv 硬抬），且信号日成交额≥1.5×前20日均额；按管理费用率降幅（封顶10）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：管理费用率改善 × 盈余惊喜加速 × 量能确认（§801/§803/§805/§809）。",
        "kind": "admin_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §816/§818/§822 营业利润加速∩盈余加速∩放量（scripts/midterm/opsuegvol_d60_s822.py OPSUEGVOL_d60_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-op-sue-vol": {
        "name": "营业利润加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足营业利润同比≥20%且较去年同报告期加速≥20个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥60、营收同比≥20；§822 dtp硬抬），且信号日成交额≥1.5×前20日均额；按营业利润同比加速（封顶100）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：营业利润加速 × 盈余惊喜加速 × 量能确认（§816/§818/§822）。",
        "kind": "op_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §817/§819 营收加速∩盈余加速∩放量（scripts/midterm/revsuegvol_d55_s819.py REVSUEGVOL_d55_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-rev-sue-vol": {
        "name": "营收加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足营收同比≥20%且较去年同报告期加速≥20个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥20；§819 dtp硬抬），且信号日成交额≥1.5×前20日均额；按营收同比加速（封顶100）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：营收加速 × 盈余惊喜加速 × 量能确认（§817/§819）。",
        "kind": "rev_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §827 单季营业利润环比加速∩盈余加速∩放量（scripts/midterm/qopsuegvol_s827.py QOPSUEGVOL_h20；§282 达标；2026-11-02 上线）
    "mid-qop-sue-vol": {
        "name": "单季营业利润环比加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足单季营业利润环比≥20%且较去年同报告期加速≥20个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按单季营业利润环比加速（封顶100）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：单季营业利润环比加速 × 盈余惊喜加速 × 量能确认（§827）。",
        "kind": "qop_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §829/§832 经营现金流/营收改善∩盈余加速∩放量（scripts/midterm/ocfssuegvol_o25_s832.py OCFSSUEGVOL_o25_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-ocfs-sue-vol": {
        "name": "经营现金流/营收改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足经营现金流/营收同比升幅≥3.0 个百分点（当期∈[-20,80]、去年∈[-50,120]），与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥25；§832 orv硬抬），且信号日成交额≥1.5×前20日均额；按升幅（封顶30）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：现金流质量改善 × 盈余惊喜加速 × 量能确认（§829/§832）。",
        "kind": "ocfs_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §833 经营现金流/短期债务改善∩盈余加速∩放量（scripts/midterm/ocsdsuegvol_s833.py OCSDSUEGVOL_h20；§282 达标；2026-11-02 上线）
    "mid-ocsd-sue-vol": {
        "name": "经营现金流/短期债务改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足经营现金流/短期债务同比升幅≥0.05（当期∈[-0.20,0.60]、去年∈[-0.50,1.20]），与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按升幅（封顶0.40）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：偿债现金流改善 × 盈余惊喜加速 × 量能确认（§833）。",
        "kind": "ocsd_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §837 净利率改善∩盈余加速∩放量（scripts/midterm/npmsuegvol_s837.py NPMSUEGVOL_h20；§282 达标；2026-11-02 上线）
    "mid-npm-sue-vol": {
        "name": "净利率改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足净利率同比升幅≥2.0个百分点（当期净利率≥3），与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按升幅（封顶20）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：净利率改善 × 盈余惊喜加速 × 量能确认（§837）。",
        "kind": "npm_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §844/§846 单季营收加速∩盈余加速∩放量（scripts/midterm/qssuegvol_d55_s846.py QSSUEGVOL_d55_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-qs-sue-vol": {
        "name": "单季营收加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足单季营收同比≥20%且较去年同季加速≥20个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥20；§846 dtp硬抬），且信号日成交额≥1.5×前20日均额；按加速（封顶100）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：单季营收加速 × 盈余惊喜加速 × 量能确认（§844/§846）。",
        "kind": "qs_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §849/§850 自由现金流每股改善∩盈余加速∩放量（scripts/midterm/fcffsuegvol_d55_s850.py FCFFSUEGVOL_d55_h20；§282+B 达标；2026-11-02 上线/硬抬）
    "mid-fcff-sue-vol": {
        "name": "自由现金流每股改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足自由现金流每股同比升幅≥0.5元（当期>0），与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥20；§850 dtp硬抬），且信号日成交额≥1.5×前20日均额；按升幅（封顶5）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：自由现金流改善 × 盈余惊喜加速 × 量能确认（§849/§850）。",
        "kind": "fcff_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §851 经营现金流加速∩盈余加速∩放量（scripts/midterm/ocfsuegvol_s851.py OCFSUEGVOL_h20；§282 达标；2026-11-02 上线）
    "mid-ocf-sue-vol": {
        "name": "经营现金流加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足经营现金流同比≥50%且较去年同报告期加速（ocfps>0），与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按经营现金流同比（封顶300）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：经营现金流加速 × 盈余惊喜加速 × 量能确认（§851；≠经营现金流/营收口径的 mid-ocfs-sue-vol）。",
        "kind": "ocf_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §855/§856 每股现金流同比加速∩盈余加速∩放量（scripts/midterm/cfpssuegvol_d55_s856.py CFPSSUEGVOL_d55_h20；§282 达标；dtp≥55；2026-11-02 上线）
    "mid-cfps-sue-vol": {
        "name": "每股现金流同比加速∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足每股现金流同比≥20%且较去年同报告期加速≥20个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥20），且信号日成交额≥1.5×前20日均额；按加速（封顶100）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：每股现金流加速 × 盈余惊喜加速 × 量能确认（§855/§856 dtp≥55；≠经营现金流同比口径的 mid-ocf-sue-vol）。",
        "kind": "cfps_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §861/§862 每股未分配利润高增∩盈余加速∩放量（scripts/midterm/udpsuegvol_d55_s862.py UDPSUEGVOL_d55_h20；§282 达标；dtp≥55；2026-11-02 上线）
    "mid-udp-sue-vol": {
        "name": "每股未分配利润高增∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足每股未分配利润同比增速≥30%（本期≥0.5元、去年同期>0），与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥20），且信号日成交额≥1.5×前20日均额；按增速（封顶2）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：留存利润积累 × 盈余惊喜加速 × 量能确认（§861/§862 dtp≥55）。",
        "kind": "udp_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §869 每股股权自由现金流改善∩盈余加速∩放量（scripts/midterm/fcfesuegvol_s869.py FCFESUEGVOL_h20；§282 达标；2026-11-03 上线）
    "mid-fcfe-sue-vol": {
        "name": "每股股权自由现金流改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足股权自由现金流每股同比升幅≥0.5元（当期>0），与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按升幅（封顶5）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：股权自由现金流改善 × 盈余惊喜加速 × 量能确认（§869；≠企业自由现金流口径的 mid-fcff-sue-vol）。",
        "kind": "fcfe_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §871/§872/§874 固定资产周转改善∩盈余加速∩放量（scripts/midterm/fatsuegvol_o25_s874.py FATSUEGVOL_o25_h20；§282+B 达标；dtp≥55,orv≥25；2026-11-03 上线；≠ mid-sue-fa-vol）
    "mid-fat-sue-vol": {
        "name": "固定资产周转改善∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足固定资产周转率同比升幅≥0.10（本期≥0.5），与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥25；§872 dtp / §874 orv 硬抬），且信号日成交额≥1.5×前20日均额；按升幅（封顶2）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：固定资产周转改善 × 盈余惊喜加速 × 量能确认（§871/§872/§874；≠按SUE排序的 mid-sue-fa-vol）。",
        "kind": "fat_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §875/§876/§878 单季总资产净利率扩张∩盈余加速∩放量（scripts/midterm/qnsuegvol_o25_s878.py QNSUEGVOL_o25_h20；§282+B 达标；dtp≥55,orv≥25；2026-11-03 上线）
    "mid-qn-sue-vol": {
        "name": "单季总资产净利率扩张∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足单季总资产净利率（q_npta）≥1.0且较去年同报告期扩张≥0.5个百分点，与 SUE≥2.2 且业绩加速 G（扣非同比≥55、营收同比≥25；§876 dtp / §878 orv 硬抬），且信号日成交额≥1.5×前20日均额；按扩张（封顶5）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：单季 ROA 扩张 × 盈余惊喜加速 × 量能确认（§875/§876/§878；≠ROE/ROIC∩SUEG）。",
        "kind": "qn_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §877 ROE同比高增∩盈余加速∩放量（scripts/midterm/roysuegvol_s877.py ROYSUEGVOL_h20；§282 达标；2026-11-03 上线；≠ΔROE 的 ROESUEG）
    "mid-roy-sue-vol": {
        "name": "ROE同比高增∩盈余加速∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足 ROE 同比（roe_yoy）≥20%，与 SUE≥2.2 且业绩加速 G（扣非同比≥50、营收同比≥20），且信号日成交额≥1.5×前20日均额；按 roe_yoy（封顶100）从高到低排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：ROE 同比高增 × 盈余惊喜加速 × 量能确认（§877；≠ΔROE 口径的 ROESUEG）。",
        "kind": "roy_sue_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §735 净负债转净现金∩营业周期∩放量（scripts/midterm/ncstdvol_s735.py NCSTDVOL_h20；§282 达标；2026-11-02 上线）
    "mid-ncs-td-vol": {
        "name": "净负债转净现金∩营业周期∩放量（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描首次披露的定期报告：同日满足净负债转为净现金（去年净负债>0且有息负债>0、本期净负债≤0、扣非净利>0），与营业周期（turn_days）同比缩短≥20日，且信号日成交额≥1.5×前20日均额；按「净负债减少额÷去年有息负债」（封顶3.0）从大到小排序，次日开盘买入，各持有20个交易日。不限同时持有数量；每天新推荐数量有上限（默认5只）。思路：资产负债表修复 × 周期效率 × 量能确认（§735）。",
        "kind": "ncs_td_vol",
        "hold_days": 20,
        "schedule": "daily",
    },
    # §102 红利低波（scripts/midterm/lt_research19.py DVLV_h60，预注册 3 版中唯一达标，未调参）
    "long-dividend-lowvol": {
        "name": "红利低波月度组合（股息率+低波动，持60日）",
        "horizon": "long",
        "summary": "每月最后一个交易日收盘后，在全A（剔除北交所）总市值前1500名里，剔除ST、上市不足250个交易日、股价<3元，只看近12个月股息率（dv_ttm，取当周或上周最近一期周度快照）>0 的股票；按股息率排名（越高越好）+ 近60日日收益波动率排名（越低越好）相加，取得分最低的10只，次日开盘等权买入、各持有60个交易日。仍在持有期内的不重复买、也不顺延补位（与回测一致）。",
        "kind": "dividend_lowvol",
        "top_n": 10,
        "hold_days": 60,
        "schedule": "month_end",
    },
    # §269 营收与扣非净利连续多期双增长（scripts/midterm/lt_research102.py CG4_h60，预注册、未调参；2026-10-25 起直接上线）
    "long-growth-streak": {
        "name": "营收与扣非净利连续4期双增长（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（同一报告期只取第一次公告）：本期及之前连续至少4个报告期（逐期相连、不跳期），营业收入同比和扣非净利润同比都≥5%（稳健增长、不看一次性大涨）；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万的，按连续双增期数（封顶12期）从多到少排序、同期数再按扣非净利同比（封顶100%）从高到低，次日开盘买，持有60个交易日（已持有不重复买）。",
        "kind": "cg_streak",
        "hold_days": 60,
        "schedule": "daily",
    },
    # §261 净负债转为净现金（scripts/midterm/lt_research98.py NCS_h60，预注册、未调参；2026-10-25 起直接上线，§278 曾因 ST 口径下线、§282 新标准重评达标后恢复）
    "long-net-cash-switch": {
        "name": "净负债转为净现金（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（同一报告期只取第一次公告）：去年同期有息负债多于账上现金（净负债>0、有息负债>0），本期变成现金多于有息负债（净负债≤0，即净现金），且本期扣非净利润为正；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万的，按「净负债减少额÷去年有息负债」从大到小排序（封顶3倍），次日开盘买，持有60个交易日（已持有不重复买）。",
        "kind": "ncs_switch",
        "hold_days": 60,
        "schedule": "daily",
    },
}


# §278（2026-10-25）st_hist.parquet 重建后复核：以下 /long 策略在新 ST 口径下不再满足各自上线标准，按「不设观察档」下线——
# §282（新达标标准）重评后，其中 long-net-cash-switch 达标已移回 STRATEGIES；以下 4 个仍不达标，维持下线。
# 不再出票、不再在 /long 展示；仅保留定义（候选函数/历史 picks 文件/测试）以便复现与追溯，原因见 app/api/midterm.py LONG_REJECTED 与 docs/strategy-research.md §278。
# §338（2026-10-27）long-lowturn-lowvol：现行生产代码近12个月满期卖出每笔为负（40 笔 −1.13%、中位数 −2.54%），owner 决定下线，同样方式移入此处。
RETIRED_STRATEGIES: dict[str, dict[str, Any]] = {
    # §98 防御型异象（scripts/midterm/lt_research16.py LTV_h60）；§338 下线
    "long-lowturn-lowvol": {
        "name": "低换手+低波动月度组合（持60日）",
        "horizon": "long",
        "summary": "每月最后一个交易日收盘后，在全A（剔除北交所）总市值排名第200–1500的股票里，剔除ST、上市不足250个交易日、股价<3元；按近60日平均换手率排名 + 近60日日收益波动率排名（都越低越好）相加，取得分最低的10只，次日开盘等权买入、各持有60个交易日。仍在持有期内的不重复买、也不顺延补位（与回测一致）。",
        "kind": "lowturn_lowvol",
        "top_n": 10,
        "hold_days": 60,
        "schedule": "month_end",
    },
    "long-repurchase-big": {
        "name": "大额回购预案（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描新发布的股票回购「预案」公告（同一股票365天内只取第一次），只保留回购金额上限≥总市值1%的，剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按金额占市值比例从高到低，次日开盘买入，各持有60个交易日。不限同时持有数量；每天新推荐数量有上限（每个策略每日最多 5 只，系统配置可调），当天信号超出时按上述排序取前几只买入。",
        "kind": "repurchase",
        "min_ratio": 0.01,
        "hold_days": 60,
        "schedule": "daily",
    },
    # §240 流动负债占比同比下降（scripts/midterm/lt_research88.py CLD_h60，预注册、未调参；2026-10-25 起直接上线）
    "long-current-debt-down": {
        "name": "流动负债占比同比下降（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（同一报告期只取第一次公告）：流动负债/负债合计（currentdebt_to_debt）本期在 30%~95%、去年同期在 20%~100%，且较去年同期下降 ≥5 个百分点；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万的，按降幅从大到小排序（降幅封顶40），次日开盘买，持有60个交易日（已持有不重复买）。",
        "kind": "cld_yoy",
        "hold_days": 60,
        "schedule": "daily",
    },
    # §158 分红增加（scripts/midterm/lt_research48.py DINC_h60，预注册、未调参；2026-10-25 起直接上线）
    "long-dividend-increase": {
        "name": "现金分红同比增加（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次公布的分红「预案」（同一报告期只取第一次预案）：每股税前派现>0，且较去年同一报告期（优先取实施记录，否则取最早预案）提高 ≥30%（去年同期>0）；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万的，按增幅从大到小排序，次日开盘买，持有60个交易日（已持有不重复买）。",
        "kind": "dinc_yoy",
        "hold_days": 60,
        "schedule": "daily",
    },
    # §252 每股未分配利润同比高增（scripts/midterm/lt_research94.py UDP_h60，预注册、未调参；2026-10-25 起直接上线）
    "long-undist-profit-growth": {
        "name": "每股未分配利润同比高增（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（同一报告期只取第一次公告）：去年同期每股未分配利润（undist_profit_ps）>0、本期 ≥0.5 元/股，且同比增速 ≥30%；剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万的，按增速从大到小排序（增速封顶200%），次日开盘买，持有60个交易日（已持有不重复买）。",
        "kind": "udp_yoy",
        "hold_days": 60,
        "schedule": "daily",
    },
}


# 定量选股（top_n）类策略的说明补一句每日新推荐上限（事件型策略的说明里已写明）。
_DAILY_CAP_NOTE = "实际每次新推荐数量另受「每策略每日新推荐上限」限制（默认 5 只，系统配置可调），超出按同一排序取前几只。"
for _m in STRATEGIES.values():
    if _m.get("top_n") and _DAILY_CAP_NOTE not in str(_m.get("summary") or ""):
        _m["summary"] = f"{_m['summary']} {_DAILY_CAP_NOTE}"


def _formal_entry(fn):
    """正式链路入口：函数内经数据网关的请求一律拒绝旧缓存（stale）数据。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from app.services.datasource.cache import formal_chain

        with formal_chain():
            return fn(*args, **kwargs)

    return wrapper


def _strategy_meta(strategy_id: str) -> dict[str, Any]:
    """现役优先，其次已下线（RETIRED_STRATEGIES，只为历史 picks 文件保留名称/持有期），都没有返回空 dict。"""
    return STRATEGIES.get(strategy_id) or RETIRED_STRATEGIES.get(strategy_id) or {}


def _hold(strategy_id: str) -> int:
    return int(_strategy_meta(strategy_id).get("hold_days", HOLD_DAYS))


def is_signal_day(strategy_id: str, d: _dt.date) -> bool:
    schedule = STRATEGIES[strategy_id]["schedule"]
    if schedule == "daily":
        return True
    if schedule == "month_end":  # 无交易日历时的近似：下一个工作日已是下个月（generate_picks 用 trade_calendar 精确判断）
        nxt = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
        return d.weekday() < 5 and nxt.month != d.month
    if d.weekday() != 4:
        return False
    return schedule == "weekly" or d.day <= 7


def _universe(db, as_of: str, limit: int) -> list[dict[str, Any]]:
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return []
    d20, d250 = dates[19], dates[250]
    basic_date = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    rows = db.execute(text(f"""
        SELECT b.code, b.total_mv, b.pe_ttm, a.amt20
        FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20, COUNT(*) n FROM daily_kbars
              WHERE trade_date> :d20x AND trade_date<=:d GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250) old ON old.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d AND close>0) today ON today.code=b.code
        WHERE b.trade_date=:bd AND b.pe_ttm>0 AND a.amt20>3e7
          AND LEFT(b.code,1) NOT IN ('4','8','9')
        ORDER BY b.total_mv ASC LIMIT {int(limit) * 3}
    """), {"d20x": dates[20], "d": as_of, "d250": d250, "bd": basic_date}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    names = dict(db.execute(text("SELECT code,name FROM stocks")).all())
    out = []
    for r in rows:
        if r["code"] in st:
            continue
        out.append({"code": r["code"], "name": names.get(r["code"], ""), "total_mv": float(r["total_mv"]),
                    "pe_ttm": float(r["pe_ttm"]), "amt20": float(r["amt20"])})
        if len(out) >= limit:
            break
    return out


def _lowprice_universe(db, as_of: str, limit: int, rank_n: int = 200, min_close: float = 2.0) -> list[dict[str, Any]]:
    """§277 LPS_W：合格池（非ST/退市、非北交所、上市≥250个交易日、20日均额>3000万、收盘价>2元，不要求盈利）里
    收盘价最低 rank_n 只 ∩ 总市值最小 rank_n 只，按总市值升序取 limit 只。与 scripts/midterm/lt_research104.py 口径一致。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return []
    basic_date = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    rows = db.execute(text("""
        SELECT b.code, b.total_mv, b.pe_ttm, a.amt20, today.close
        FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date> :d20x AND trade_date<=:d GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250) old ON old.code=b.code
        JOIN (SELECT code, close FROM daily_kbars WHERE trade_date=:d AND close>0) today ON today.code=b.code
        WHERE b.trade_date=:bd AND b.total_mv>0 AND a.amt20>3e7 AND today.close> :mc
          AND LEFT(b.code,1) NOT IN ('4','8','9')
    """), {"d20x": dates[20], "d": as_of, "d250": dates[250], "bd": basic_date, "mc": min_close}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE (is_st=1 OR name LIKE '%ST%' OR name LIKE '%退%') "
        "AND effective_from<=:d AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    pool = [r for r in rows if r["code"] not in st]
    by_px = sorted(pool, key=lambda r: (float(r["close"]), r["code"]))[:rank_n]
    by_mv = sorted(pool, key=lambda r: (float(r["total_mv"]), r["code"]))[:rank_n]
    px_rank = {r["code"]: i + 1 for i, r in enumerate(by_px)}
    mv_rank = {r["code"]: i + 1 for i, r in enumerate(by_mv)}
    names = dict(db.execute(text("SELECT code,name FROM stocks")).all())
    out = []
    for r in by_mv:
        if r["code"] not in px_rank:
            continue
        out.append({"code": r["code"], "name": names.get(r["code"], ""), "total_mv": float(r["total_mv"]),
                    "close": float(r["close"]), "pe_ttm": (float(r["pe_ttm"]) if r["pe_ttm"] is not None else None),
                    "amt20": float(r["amt20"]), "price_rank": px_rank[r["code"]], "mv_rank": mv_rank[r["code"]]})
        if len(out) >= limit:
            break
    return out


def _r20_map(db, codes: list[str], dates: list[str], as_of: str) -> dict[str, float]:
    """§315-D：每只股票最近 20 个自身交易日的复权涨跌幅（%，逐日 pct_chg 限 ±35% 连乘，与 lt_research.build 的 ac 口径一致）。
    停牌导致近 20 个自身交易日不足、或 as_of 当日无行情的不返回。dates 为最近 251 个交易日（降序）。"""
    if not codes:
        return {}
    rows = db.execute(text("SELECT code, trade_date, pct_chg FROM daily_kbars WHERE trade_date>=:d0 AND trade_date<=:d AND code IN :cs")
                      .bindparams(bindparam("cs", expanding=True)), {"d0": dates[min(59, len(dates) - 1)], "d": as_of, "cs": list(codes)}).all()
    by: dict[str, list[tuple[str, float]]] = {}
    for code, td, pc in rows:
        by.setdefault(code, []).append((str(td)[:10], float(pc or 0)))
    out: dict[str, float] = {}
    for code, lst in by.items():
        lst.sort()
        if len(lst) < 20 or lst[-1][0] != as_of:
            continue
        v = 1.0
        for _, pc in lst[-20:]:
            v *= 1 + max(-35.0, min(35.0, pc)) / 100
        out[code] = round((v - 1) * 100, 3)
    return out


def _smallcap_div_universe(db, as_of: str, limit: int, pool_n: int = 400, factor: str = "dv_ttm") -> list[dict[str, Any]] | None:
    """§312 SDY_W：合格池（非ST/退市、非北交所、上市≥250个交易日、20日均额>3000万、PE_ttm>0）按总市值升序前 pool_n 只
    （小盘指数成分口径）里，dv_ttm（周度快照，同 dividend_lowvol）>0 的按 dv_ttm 降序取 limit 只。与 scripts/midterm/lt_research118.py 口径一致。
    dv_ttm 快照取不到返回 None（data_error）。
    factor="ps_ttm"（§314 SPS_W，scripts/midterm/lt_research119.py）：同一池子里 ps_ttm>0 的按 ps_ttm 升序取 limit 只。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return []
    basic_date = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    rows = db.execute(text("""
        SELECT b.code, b.total_mv, b.pe_ttm, a.amt20
        FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date> :d20x AND trade_date<=:d GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250) old ON old.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d AND close>0) today ON today.code=b.code
        WHERE b.trade_date=:bd AND b.total_mv>0 AND b.pe_ttm>0 AND a.amt20>3e7
          AND LEFT(b.code,1) NOT IN ('4','8','9')
    """), {"d20x": dates[20], "d": as_of, "d250": dates[250], "bd": basic_date}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE (is_st=1 OR name LIKE '%ST%' OR name LIKE '%退%') "
        "AND effective_from<=:d AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    pool = sorted([r for r in rows if r["code"] not in st], key=lambda r: (float(r["total_mv"]), r["code"]))[:pool_n]
    if factor == "r20":  # §315-D SMR_W：池内近20个交易日涨跌幅（复权，逐日 pct_chg 限 ±35% 连乘）升序取 limit 只
        r20 = _r20_map(db, [r["code"] for r in pool], dates, as_of)
        mv_rank = {r["code"]: i + 1 for i, r in enumerate(pool)}
        cand = sorted([r for r in pool if r["code"] in r20], key=lambda r: (r20[r["code"]], float(r["total_mv"]), r["code"]))[:limit]
        names = dict(db.execute(text("SELECT code,name FROM stocks")).all())
        return [{"code": r["code"], "name": names.get(r["code"], ""), "total_mv": float(r["total_mv"]), "pe_ttm": float(r["pe_ttm"]),
                 "amt20": float(r["amt20"]), "r20": r20[r["code"]], "mv_rank": mv_rank[r["code"]], "dv_snap": as_of} for r in cand]
    snap = _weekly_snapshot_date(db, as_of, dates)
    wb = _weekly_basic(snap, extra=("ps_ttm",) if factor == "ps_ttm" else ()) if snap else None
    if wb is None or len(wb) == 0 or factor not in wb.columns:
        return None
    dv = {}
    for code, v in zip(wb["ts_code"].astype(str).str[:6], wb[factor]):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == fv and fv > 0:
            dv[code] = fv
    mv_rank = {r["code"]: i + 1 for i, r in enumerate(pool)}
    sgn = 1.0 if factor == "ps_ttm" else -1.0
    cand = sorted([r for r in pool if r["code"] in dv], key=lambda r: (sgn * dv[r["code"]], float(r["total_mv"]), r["code"]))[:limit]
    names = dict(db.execute(text("SELECT code,name FROM stocks")).all())
    return [{"code": r["code"], "name": names.get(r["code"], ""), "total_mv": float(r["total_mv"]), "pe_ttm": float(r["pe_ttm"]),
             "amt20": float(r["amt20"]), factor: dv[r["code"]], "mv_rank": mv_rank[r["code"]], "dv_snap": snap} for r in cand]


def small_cap_guard(db, as_of: str, window: int = 20) -> dict[str, Any]:
    """小盘趋势过滤：总市值最小400只（pe>0、非北交所）等权日收益累乘成指数，指数 > MA20 才开新仓。
    成分按当日（或之前最近一日）daily_basic 排名，与 research7.py 口径一致。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT :n"), {"d": as_of, "n": window * 2})][::-1]
    if len(dates) < window * 2 or dates[-1] != as_of:
        return {"on": False, "reason": "insufficient_dates"}
    level, levels = 1.0, []
    for d in dates:
        bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": d}).scalar()
        r = db.execute(text("""
            SELECT AVG(LEAST(GREATEST(k.pct_chg,-20),20)) FROM daily_kbars k JOIN (
              SELECT code FROM daily_basic WHERE trade_date=:bd AND pe_ttm>0 AND LEFT(code,1) NOT IN ('4','8','9')
              ORDER BY total_mv ASC LIMIT 400) m ON m.code=k.code WHERE k.trade_date=:d"""), {"bd": bd, "d": d}).scalar()
        level *= 1 + float(r or 0) / 100
        levels.append(level)
    ma = sum(levels[-window:]) / window
    return {"on": levels[-1] > ma, "index": round(levels[-1], 4), "ma20": round(ma, 4)}


def _held_codes(db, sid: str, as_of: str) -> set[str]:
    """之前批次中截至 as_of 仍在持有期（买入后不足该策略 hold_days 个交易日）的代码。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<:d "
        "ORDER BY trade_date DESC LIMIT :n"), {"d": as_of, "n": _hold(sid) + 1})]
    if not dates or not OUT_DIR.exists():
        return set()
    cutoff = dates[-1]
    held: set[str] = set()
    for f in OUT_DIR.glob(f"*_{sid}.json"):
        sd = f.name.split("_", 1)[0]
        if cutoff <= sd < as_of:
            held |= {p["code"] for p in json.loads(f.read_text())["picks"]}
    return held


# 网关全部上游失败、且失败都属于「等一会儿就好」的类型时，正式链路按这个节奏等待再试（秒）。
# promax 的 upstream_pool_exhausted 实测几秒到一分钟内恢复；quantdata 日配额耗尽当天不会恢复（传输层直接跳过）。
PRO_RETRY_WAITS: tuple[float, ...] = (20.0, 40.0, 60.0)
_WAITABLE_CATEGORIES = frozenset({
    "http_rate_limit", "business_rate_limit", "quota_exhausted", "circuit_open", "http_server",
    "timeout", "network",
})


def _pro_fetch_with_wait(api: str, label: str, *, alt_params: dict | None = None, **params):
    """get_pro().<api>(**params)；上游全部瞬时失败时按 PRO_RETRY_WAITS 等待重试，最终失败返回 None。

    alt_params：本次尝试里所有“活着的”上游都回 invalid_request（参数写法不被接受）时，换成这组参数再试一次
    （只换一次，不消耗等待次数）。例：forecast 主用 ann_date（promax 必须有 ann_date 或 ts_code），
    备用 start_date/end_date。"""
    import time as _time

    from app.services.tushare_client import get_pro
    from app.services.tushare_transport import TushareProviderError

    waits = list(PRO_RETRY_WAITS)
    while True:
        try:
            return getattr(get_pro(), api)(**params)
        except TushareProviderError as exc:
            cats = {getattr(getattr(f, "category", None), "value", "") for f in getattr(exc, "failures", ())}
            if alt_params is not None and "invalid_request" in cats \
                    and cats <= {"invalid_request", "quota_exhausted", "circuit_open"}:
                logger.warning("%s %s invalid params %s (%s); retry with %s",
                               api, label, sorted(k for k in params if k != "fields"), exc, sorted(alt_params))
                keep = {k: v for k, v in params.items() if k == "fields"}
                params, alt_params = {**keep, **alt_params}, None
                continue
            live = [f for f in getattr(exc, "failures", ())
                    if getattr(getattr(f, "category", None), "value", "") != "quota_exhausted"]
            if waits and cats and cats <= _WAITABLE_CATEGORIES and live:
                wait = waits.pop(0)
                logger.warning("%s %s transient failure (%s); retry in %.0fs", api, label, sorted(cats), wait)
                _time.sleep(wait)
                continue
            logger.warning("%s %s failed: %s", api, label, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s %s failed: %s", api, label, exc)
            return None


def _fetch_repurchase_backup(start: str, end: str):
    """东财回购报表（降级：召回约 55%，见 datasource/akshare_backup.py）。失败/关闭返回 None。"""
    import pandas as pd
    try:
        from app.services.datasource.akshare_backup import backup_enabled, backup_gateway

        if not backup_enabled():
            return None
        res = backup_gateway().fetch_first(
            "tushare.repurchase", {"params": {"start_date": start, "end_date": end}, "fields": ""},
            allow_stale=False)
        if res.provider is None:
            return None
        df = pd.DataFrame.from_records(list(res.rows or []))
        if df.empty:
            df = pd.DataFrame(columns=["ts_code", "ann_date", "proc", "amount", "source"])
        df.attrs["degraded_source"] = "em_hglist"
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("repurchase backup %s-%s failed: %s", start, end, exc)
        return None


def _fetch_repurchase_window(start: str, end: str):
    df = _pro_fetch_with_wait("repurchase", f"{start}-{end}", start_date=start, end_date=end)
    if df is not None:
        return df
    df = _fetch_repurchase_backup(start, end)
    if df is not None:
        logger.warning("repurchase %s-%s served by degraded backup em_hglist (%d rows)", start, end, len(df))
    return df


def _store_repurchase_events(new) -> None:
    """把新拉到的公告并入 data/midterm/events/repurchase_<year>.parquet，供以后"365天内首次"判断。非致命。"""
    import pandas as pd
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        new = new.copy()
        new["ann_date"] = new["ann_date"].astype(str).str.strip()
        for year, part in new.groupby(new["ann_date"].str.slice(0, 4)):
            f = EVENTS_DIR / f"repurchase_{year}.parquet"
            merged = pd.concat([pd.read_parquet(f), part]) if f.exists() else part
            merged = merged.drop_duplicates(["ts_code", "ann_date", "proc", "amount"])
            for col in ("vol", "amount", "high_limit", "low_limit"):
                if col in merged:
                    merged[col] = pd.to_numeric(merged[col], errors="coerce")
            merged.to_parquet(f, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("store repurchase events failed: %s", exc)



def _scaled_total_mv(
    total_mv_snapshot: float,
    close_bd,
    close_t,
    adj_bd=None,
    adj_t=None,
    total_share=None,
) -> float:
    """把 daily_basic 快照总市值折算到信号日：total_mv_snapshot * close(signal) / close(snapshot)。

    daily_kbars.close 为不复权价。若快照日与信号日之间 adj_factor 变化（疑似除权除息/送转等），
    不复权收盘比不再对应同一股本，优先用 total_share * close_t（单位与 total_mv 一致：万股×元=万元）；
    本地无 total_share 时退回快照值，避免错误缩放。
    """
    snap = float(total_mv_snapshot)
    if snap <= 0:
        return snap
    try:
        c_bd = float(close_bd) if close_bd is not None else None
        c_t = float(close_t) if close_t is not None else None
    except (TypeError, ValueError):
        return snap
    if not c_bd or not c_t or c_bd <= 0 or c_t <= 0:
        return snap
    corp_action = False
    try:
        if adj_bd is not None and adj_t is not None:
            a_bd, a_t = float(adj_bd), float(adj_t)
            if a_bd > 0 and a_t > 0 and abs(a_bd - a_t) > 1e-9:
                corp_action = True
    except (TypeError, ValueError):
        corp_action = False
    if corp_action:
        try:
            shares = float(total_share) if total_share is not None else None
        except (TypeError, ValueError):
            shares = None
        if shares is not None and shares > 0:
            return shares * c_t
        return snap
    return snap * c_t / c_bd



def _filter_repurchase_underreact(db, as_of: str, pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§364：信号日 pct∈[-1,+2] 且未封涨停；§398 起按 signal_pct 升序（越弱反应优先），不再按 ratio。
    daily_kbars 无 up_limit 列，涨停价用前收 ×(1+9.8%/19.8%) 近似（与研究脚本一致）。"""
    if not pool:
        return []
    codes = [p["code"] for p in pool]
    rows = db.execute(text(
        "SELECT code, pct_chg, close FROM daily_kbars "
        "WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    by = {r["code"]: r for r in rows}
    out = []
    for p in pool:
        r = by.get(p["code"])
        if not r or r["pct_chg"] is None or r["close"] is None:
            continue
        pct = float(r["pct_chg"])
        if pct < -1.0 or pct > 2.0:
            continue
        close = float(r["close"])
        code = p["code"]
        band = 0.198 if code.startswith(("3", "688")) else 0.098
        pre = close / (1 + pct / 100.0) if pct > -99.999 else close
        ul = pre * (1 + band)
        if close >= ul - 0.005:
            continue
        q = dict(p)
        q["signal_pct"] = round(pct, 3)
        q["score"] = round(-pct, 6)  # §398：与研究 sc_pct=−pct 一致
        out.append(q)
    return sorted(out, key=lambda x: (x["signal_pct"], x["code"]))


def repurchase_plan_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的新回购预案（公告日在 (上一交易日, as_of] 内，周末/盘后公告顺延到下一交易日），
    同一股票 365 天内只取首次；流动性口径与 lt_research.py liq 一致。按 回购金额上限/总市值 降序。
    数据拉取失败返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    new = _fetch_repurchase_window(start, end)
    if new is None:
        return None
    degraded = new.attrs.get("degraded_source")
    if len(new) and not degraded:
        # 降级备源（东财）的公告日/金额口径与 Tushare 不完全一致，不写进"365天内首次"历史库，避免污染以后的判断
        _store_repurchase_events(new)
    files = sorted(EVENTS_DIR.glob("repurchase_*.parquet"))
    hist = pd.concat([pd.read_parquet(f) for f in files] + ([new] if len(new) else []))
    hist["ann_date"] = hist["ann_date"].astype(str).str.strip()
    plans = hist[hist["proc"].astype(str).str.contains("预案")].drop_duplicates(["ts_code", "ann_date"])
    today = plans[(plans["ann_date"] >= start) & (plans["ann_date"] <= end)]
    if today.empty:
        return []
    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=365)).strftime("%Y%m%d")
    earlier = set(plans[(plans["ann_date"] < start) & (plans["ann_date"] >= cutoff)]["ts_code"].str.slice(0, 6))
    amt: dict[str, float] = {}
    ann: dict[str, str] = {}
    for _, r in today.iterrows():
        code = str(r["ts_code"])[:6]
        if code in earlier or code[0] in "489":
            continue
        a = pd.to_numeric(r.get("amount"), errors="coerce")
        amt[code] = max(amt.get(code, 0.0), 0.0 if pd.isna(a) else float(a))
        ann[code] = str(r["ann_date"])
    if not amt:
        return []
    codes = sorted(amt)
    bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    if not bd:
        return []
    q = text("""
        SELECT b.code, b.total_mv, a.amt20,
               kb.close AS close_bd, kt.close AS close_t,
               kb.adj_factor AS adj_bd, kt.adj_factor AS adj_t
        FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date>:d20x AND trade_date<=:d AND code IN :codes GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250 AND code IN :codes) old ON old.code=b.code
        JOIN daily_kbars kt ON kt.code=b.code AND kt.trade_date=:d AND kt.close>0
        LEFT JOIN daily_kbars kb ON kb.code=b.code AND kb.trade_date=:bd
        WHERE b.trade_date=:bd AND b.code IN :codes
    """).bindparams(bindparam("codes", expanding=True))
    rows = db.execute(q, {"d20x": dates[20], "d": as_of, "d250": dates[250], "bd": bd, "codes": codes}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": codes}).all())
    out = []
    for r in rows:
        if r["code"] in st or not r["amt20"] or float(r["amt20"]) <= 5e7 or not r["total_mv"]:
            continue
        mv = _scaled_total_mv(
            float(r["total_mv"]), r["close_bd"], r["close_t"], r["adj_bd"], r["adj_t"],
        )
        out.append({"code": r["code"], "name": names.get(r["code"], ""), "ann_date": ann[r["code"]],
                    "repurchase_amount": amt[r["code"]], "total_mv": mv,
                    "ratio": round(amt[r["code"]] / (mv * 1e4), 5), "amt20": float(r["amt20"]),
                    **({"data_source": f"degraded:{degraded}"} if degraded else {})})
    return sorted(out, key=lambda x: (-x["ratio"], x["code"]))


def _quarter_ends(as_of: str, n: int = 3) -> list[str]:
    d = _dt.date.fromisoformat(as_of)
    out, y, q = [], d.year, (d.month - 1) // 3  # q = number of quarters fully ended this year
    while len(out) < n:
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}{['0331', '0630', '0930', '1231'][q - 1]}")
        q -= 1
    return out


def _fetch_fina_period(period: str):
    from app.services.tushare_client import get_pro
    try:
        return get_pro().fina_indicator_vip(period=period, fields="ts_code,ann_date,end_date,dt_netprofit_yoy,or_yoy,profit_dedt")
    except Exception as exc:  # noqa: BLE001
        logger.warning("fina_indicator_vip %s failed: %s", period, exc)
        return None


def _liq_ok(db, as_of: str, dates: list[str], codes: list[str]) -> dict[str, dict[str, Any]]:
    """lt_research liq 口径：非ST、上市>250交易日、20日均成交额>5000万、当日有成交。"""
    q = text("""
        SELECT b.code, b.total_mv, a.amt20, t.pct_chg FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date>:d20x AND trade_date<=:d AND code IN :codes GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250 AND code IN :codes) old ON old.code=b.code
        JOIN (SELECT code, pct_chg FROM daily_kbars WHERE trade_date=:d AND close>0 AND code IN :codes) t ON t.code=b.code
        WHERE b.trade_date=(SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d) AND b.code IN :codes
    """).bindparams(bindparam("codes", expanding=True))
    rows = db.execute(q, {"d20x": dates[20], "d": as_of, "d250": dates[250], "codes": codes}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    return {r["code"]: dict(r) for r in rows if r["code"] not in st and r["amt20"] and float(r["amt20"]) > 5e7}


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def ea_combo_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§RV-C EA_combo：在传入的当日候选池内写排序分，并按分数降序、代码升序返回。

    与 backend/scripts/midterm/rv1030_cand.py 的 sc_cbG 同口径（polars 默认平均秩）：
    百分位 = rank(method=average) / n，n 为池内只数（含该项为空的票）；秩从 1 起，最小值秩为 1。
    score = 信号日涨跌幅（ann_pct_chg，越大越好）百分位 + 总市值（total_mv，越小越好）百分位。
    某项为空时该项百分位记 0（研究脚本 fill_null(0)）。平手不把代码微扰写进 score，
    由二级键 code 升序打破，与研究里 score − code×1e-10 的先后一致。
    """
    import pandas as pd

    n = len(rows)
    if n == 0:
        return []
    pct = pd.Series([_finite(r.get("ann_pct_chg")) for r in rows], dtype="float64")
    mv = pd.Series([_finite(r.get("total_mv")) for r in rows], dtype="float64")
    ann_rank = pct.rank(method="average") / n
    mv_rank = (-mv).rank(method="average") / n
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        p1 = 0.0 if pd.isna(ann_rank.iloc[i]) else round(float(ann_rank.iloc[i]), 6)
        p2 = 0.0 if pd.isna(mv_rank.iloc[i]) else round(float(mv_rank.iloc[i]), 6)
        item = dict(r)
        item["ann_pct"] = p1
        item["mv_pct"] = p2
        item["score"] = round(p1 + p2, 6)
        out.append(item)
    out.sort(key=lambda x: (-x["score"], str(x.get("code") or "")))
    return out


def earnings_accel_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的首次定期报告公告（公告日在 (上一交易日, as_of]），入选条件同 lt_research14 G。

    排序为 §RV-C EA_combo（见 ea_combo_rank）：百分位在通过事件条件且通过流动性过滤的当日候选池内计算。
    信号日涨跌幅 = as_of 当日 daily_kbars.pct_chg（研究里 sd 的 pct_chg；周末公告落在下一个交易日）。
    数据失败返回 None。
    """
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    periods = _quarter_ends(as_of, 3)  # 最近两个报告期 + 上一期（算加速）
    frames = {}
    for per in periods:
        f = _fetch_fina_period(per)
        if f is None:
            return None
        f = f.copy()
        f["ann_date"] = f["ann_date"].astype(str).str.strip()
        frames[per] = f.sort_values("ann_date").drop_duplicates("ts_code", keep="first")
    out: dict[str, dict[str, Any]] = {}
    for cur, prev in ((periods[0], periods[1]), (periods[1], periods[2])):
        f = frames[cur]
        today = f[(f["ann_date"] >= start) & (f["ann_date"] <= end)]
        if today.empty:
            continue
        pv = dict(zip(frames[prev]["ts_code"], pd.to_numeric(frames[prev]["dt_netprofit_yoy"], errors="coerce")))
        for _, r in today.iterrows():
            code = str(r["ts_code"])[:6]
            if code[0] in "489":
                continue
            dtp = pd.to_numeric(r["dt_netprofit_yoy"], errors="coerce")
            orv = pd.to_numeric(r["or_yoy"], errors="coerce")
            pdd = pd.to_numeric(r["profit_dedt"], errors="coerce")
            prev_dtp = pv.get(r["ts_code"])
            if any(pd.isna(x) for x in (dtp, orv, pdd)) or prev_dtp is None or pd.isna(prev_dtp):
                continue
            if dtp >= 50 and orv >= 20 and pdd > 0 and dtp > prev_dtp:
                out[code] = {"code": code, "ann_date": str(r["ann_date"]), "end_date": cur, "dt_netprofit_yoy": float(dtp),
                             "or_yoy": float(orv), "prev_dt_netprofit_yoy": float(prev_dtp),
                             "profit_dedt": float(pdd)}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": _finite(ok[c].get("total_mv")),
            "ann_pct_chg": _finite(ok[c].get("pct_chg")), "amt20": float(ok[c]["amt20"])} for c in ok]
    return ea_combo_rank(res)


def _is_month_end(db, as_of: str) -> bool:
    """as_of 是否为当月最后一个交易日（trade_calendar SSE；日历缺失时退回工作日近似）。"""
    nxt = db.execute(text(
        "SELECT MIN(cal_date) FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>:d"),
        {"d": as_of.replace("-", "")}).scalar()
    if not nxt:
        d = _dt.date.fromisoformat(as_of)
        nd = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
        return nd.month != d.month
    return str(nxt)[4:6] != as_of[5:7]


def lowturn_lowvol_candidates(db, as_of: str, top_n: int = 10) -> list[dict[str, Any]] | None:
    """§98 lt_research16 LTV 口径：非北交所按折算总市值从大到小排名第 200–1500、非ST、上市≥250交易日、收盘≥3；
    近60日均换手率秩 + 近60日日收益标准差秩（都低好，平均秩；同分按波动率低者优先）之和最低的 top_n 只。数据不足返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 300"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    if not bd:
        return None
    mv = pd.DataFrame(db.execute(text("""
        SELECT b.code, b.total_mv, kb.close AS close_bd, kt.close AS close_t
        FROM daily_basic b
        JOIN daily_kbars kt ON kt.code=b.code AND kt.trade_date=:d
        LEFT JOIN daily_kbars kb ON kb.code=b.code AND kb.trade_date=:bd
        WHERE b.trade_date=:bd AND b.total_mv>0 AND kt.close>0 AND LEFT(b.code,1) NOT IN ('4','8','9')
    """), {"bd": bd, "d": as_of}).mappings().all())
    if mv.empty:
        return None
    mv["close_bd"] = pd.to_numeric(mv["close_bd"], errors="coerce")
    ratio = (mv["close_t"].astype(float) / mv["close_bd"]).where(mv["close_bd"] > 0, 1.0)
    mv["mvd"] = mv["total_mv"].astype(float) * ratio  # = 总股本 × 当日收盘（研究口径）
    mv["rk_mv"] = mv["mvd"].rank(ascending=False, method="first")
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    mv = mv[(mv["rk_mv"] >= 200) & (mv["rk_mv"] <= 1500) & (mv["close_t"].astype(float) >= 3) & ~mv["code"].isin(st)]
    if mv.empty:
        return []
    k = pd.DataFrame(db.execute(text(
        "SELECT code, trade_date, turnover_rate, pct_chg FROM daily_kbars "
        "WHERE trade_date>=:d0 AND trade_date<=:d AND close>0 AND code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"d0": dates[-1], "d": as_of, "codes": mv["code"].tolist()}).mappings().all())
    if k.empty:
        return None
    k = k.sort_values(["code", "trade_date"])
    age = k.groupby("code").size()
    k = k[k["code"].isin(age[age >= 251].index)].groupby("code").tail(60)
    g = k.groupby("code").agg(n=("pct_chg", "size"), turn60=("turnover_rate", "mean"),
                              vol60=("pct_chg", lambda x: (x.astype(float) / 100).clip(-0.35, 0.35).std(ddof=1)))
    g = g[(g["n"] >= 60) & g["turn60"].notna() & g["vol60"].notna()].reset_index()
    if g.empty:
        return []
    g["score"] = (g["turn60"].rank(method="average") + g["vol60"].rank(method="average")
                  + g["vol60"].rank(method="first") * 1e-6)
    g = g.sort_values(["score", "code"]).head(int(top_n)).merge(mv[["code", "mvd", "rk_mv"]], on="code")
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": g["code"].tolist()}).all())
    return [{"code": r.code, "name": names.get(r.code, ""), "total_mv": round(float(r.mvd), 1), "mv_rank": int(r.rk_mv),
             "turn60": round(float(r.turn60), 4), "vol60": round(float(r.vol60) * 100, 3), "score": round(float(r.score), 1)}
            for r in g.sort_values("score").itertuples()]


BASIC_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm"


def _weekly_snapshot_date(db, as_of: str, dates: list[str]) -> str | None:
    """§102 研究口径的周度 daily_basic 快照日：as_of 若是当周（ISO 周）最后一个交易日则取 as_of，否则取上一周最后一个交易日。
    dates = daily_kbars 交易日（降序，dates[0]==as_of）。"""
    d = _dt.date.fromisoformat(as_of)
    nxt = db.execute(text(
        "SELECT MIN(cal_date) FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>:d"),
        {"d": as_of.replace("-", "")}).scalar()
    if nxt:
        nd = _dt.datetime.strptime(str(nxt).replace("-", "")[:8], "%Y%m%d").date()
    else:
        nd = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
    wk = d.isocalendar()[:2]
    if nd.isocalendar()[:2] != wk:
        return as_of
    for x in dates:
        if _dt.date.fromisoformat(x).isocalendar()[:2] != wk:
            return x
    return None


def _weekly_basic(snap: str, extra: tuple[str, ...] = ()):
    """周度 daily_basic 快照（ts_code,total_share,dv_ttm）：先读研究用 data/midterm/basic_<year>.parquet，
    没有该日再经统一数据网关（tushare_client.get_pro 的 provider router，只读）拉 daily_basic。失败返回 None。"""
    import pandas as pd
    key = snap.replace("-", "")
    f = BASIC_DIR / f"basic_{key[:4]}.parquet"
    if f.exists():
        try:
            b = pd.read_parquet(f, columns=["ts_code", "trade_date", "total_share", "dv_ttm", *extra])
            b = b[b["trade_date"].astype(str) == key]
            if not b.empty:
                return b
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly basic parquet %s read failed: %s", f.name, exc)
    try:
        from app.services.tushare_client import get_pro
        b = get_pro().daily_basic(trade_date=key, fields=",".join(["ts_code", "trade_date", "total_share", "dv_ttm", *extra]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_basic %s via gateway failed: %s", key, exc)
        return None
    return b if b is not None and not b.empty else None


def dividend_lowvol_candidates(db, as_of: str, top_n: int = 10) -> list[dict[str, Any]] | None:
    """§102 lt_research19 DVLV_h60 口径：非北交所按 周度 total_share × 当日收盘 从大到小排名第 1–1500、非ST、上市≥250交易日、收盘≥3；
    dv_ttm（周度快照：当周最后交易日=as_of 时取 as_of，否则上周最后交易日）>0 的子集内，dv_ttm 秩（高好，平均秩）+ 近60日日收益标准差秩
    （低好，平均秩；同分按波动率低者、再按代码）之和最低的 top_n 只。数据不足返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 300"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    snap = _weekly_snapshot_date(db, as_of, dates)
    b = _weekly_basic(snap) if snap else None
    if b is None:
        return None
    b = pd.DataFrame({"code": b["ts_code"].astype(str).str[:6],
                      "total_share": pd.to_numeric(b["total_share"], errors="coerce"),
                      "dv_ttm": pd.to_numeric(b["dv_ttm"], errors="coerce")})
    b = b[~b["code"].str[0].isin(["4", "8", "9"]) & b["total_share"].notna()].drop_duplicates("code")
    px = pd.DataFrame(db.execute(text(
        "SELECT code, close FROM daily_kbars WHERE trade_date=:d AND close>0"), {"d": as_of}).mappings().all())
    if px.empty:
        return None
    mv = b.merge(px, on="code")
    mv["close"] = mv["close"].astype(float)
    mv["mvd"] = mv["total_share"] * mv["close"]  # 万股 × 元 = 万元（研究口径）
    mv["rk_mv"] = mv["mvd"].rank(ascending=False, method="first")
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    mv = mv[(mv["rk_mv"] <= 1500) & (mv["close"] >= 3) & ~mv["code"].isin(st)]
    if mv.empty:
        return []
    k = pd.DataFrame(db.execute(text(
        "SELECT code, trade_date, pct_chg FROM daily_kbars "
        "WHERE trade_date>=:d0 AND trade_date<=:d AND close>0 AND code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"d0": dates[-1], "d": as_of, "codes": mv["code"].tolist()}).mappings().all())
    if k.empty:
        return None
    k = k.sort_values(["code", "trade_date"])
    age = k.groupby("code").size()
    k = k[k["code"].isin(age[age >= 251].index)].groupby("code").tail(60)
    g = k.groupby("code").agg(n=("pct_chg", "size"),
                              vol60=("pct_chg", lambda x: (x.astype(float) / 100).clip(-0.35, 0.35).std(ddof=1)))
    g = g[(g["n"] >= 60) & g["vol60"].notna()].reset_index().merge(mv[["code", "mvd", "rk_mv", "dv_ttm"]], on="code")
    g = g[g["dv_ttm"] > 0].sort_values("code").reset_index(drop=True)
    if g.empty:
        return []
    g["score"] = ((-g["dv_ttm"]).rank(method="average") + g["vol60"].rank(method="average")
                  + g["vol60"].rank(method="first") * 1e-6)
    g = g.sort_values(["score", "code"]).head(int(top_n))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": g["code"].tolist()}).all())
    return [{"code": r.code, "name": names.get(r.code, ""), "total_mv": round(float(r.mvd), 1), "mv_rank": int(r.rk_mv),
             "dv_ttm": round(float(r.dv_ttm), 2), "vol60": round(float(r.vol60) * 100, 3), "score": round(float(r.score), 1),
             "basic_date": snap}
            for r in g.itertuples()]


def check_daily_basic_staleness(db, as_of: str, max_lag: int = 1) -> dict[str, Any]:
    """只读告警：daily_basic 最近快照（<=as_of）落后 as_of 多少个交易日；超过 max_lag 记 warning + emit_alert。
    不改变任何选股逻辑（各策略仍按原口径取最近快照/按收盘折算）。"""
    bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    if not bd:
        lag = None
    else:
        lag = int(db.execute(text(
            "SELECT COUNT(DISTINCT trade_date) FROM daily_kbars WHERE code='000001' AND trade_date>:b AND trade_date<=:d"),
            {"b": bd, "d": as_of}).scalar() or 0)
    info = {"table": "daily_basic", "as_of": as_of, "snapshot": bd, "lag_trading_days": lag}
    if lag is None or lag > max_lag:
        msg = f"daily_basic 快照过期：as_of={as_of} 最近快照={bd} 落后 {lag} 个交易日（中长线市值/PE/流通市值口径受影响）"
        logger.warning(msg)
        try:
            from app.core.alerts import emit_alert
            emit_alert("data_stale", "daily_basic", msg)
        except Exception as exc:  # noqa: BLE001 — 告警失败不影响出票，但要留痕
            logger.warning("emit_alert daily_basic stale failed: %s", exc)
        info["stale"] = True
    else:
        info["stale"] = False
    return info


def _save_picks(sid: str, as_of: str, picks: list[dict[str, Any]], apply_daily_cap: bool = False) -> int:
    """落盘一个策略当日新选股文件，返回实际入选只数。

    apply_daily_cap=True（仅实盘出票路径）：picks 已是该策略自身排序（各 *_candidates 的排序键）并剔除已持有，
    这里只切前 N 只，与第 N 名同分的顺延（settings.max_daily_picks_per_strategy，默认 5，0=不限），被截断的数量与代码写入文件 daily_cap 审计字段。
    不是持仓上限：20 仓位（回测口径）、持有 20/60 日与此无关。

    封签（midterm_live_seal）：每个批次写入 generated_at / entry_deadline（D+1 09:15）。实盘路径（apply_daily_cap=True）
    过了 D+1 09:15 后不再覆盖已落盘的批次——开盘后重跑不能改写已生效的实盘记录；没有旧文件时照常落盘，但
    generated_at 晚于 entry_deadline，统计时记为「补跑」（late），不算实盘。"""
    from app.services import midterm_live_seal as seal

    path = OUT_DIR / f"{as_of}_{sid}.json"
    if apply_daily_cap and path.exists() and seal.past_deadline(as_of):
        try:
            kept = len(json.loads(path.read_text()).get("picks") or [])
        except Exception:  # noqa: BLE001 - 读不出旧文件也不覆盖，宁可少一批也不改写实盘记录
            kept = 0
        logger.warning("%s %s 已过 %s，实盘批次已生效，不覆盖（保留 %d 只）",
                       as_of, sid, seal.entry_deadline(as_of).isoformat(timespec="minutes"), kept)
        return kept
    doc: dict[str, Any] = {"strategy_id": sid, "signal_date": as_of, **seal.stamp(as_of)}
    if apply_daily_cap:
        from app.services.daily_pick_cap import cap_ordered_picks

        picks, audit = cap_ordered_picks(picks)
        if audit:
            doc["daily_cap"] = audit
            logger.info("%s %s 每日新推荐上限截断：保留 %d / 候选 %d，丢弃 %s",
                        as_of, sid, audit["kept_count"], audit["candidates_before_cap"], audit["truncated_codes"])
    doc["picks"] = picks
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    return len(picks)


@_formal_entry
def generate_picks(db, as_of: str, force: bool = False, horizon: str | None = None,
                   strategy_ids: list[str] | None = None, apply_daily_cap: bool = False) -> dict[str, Any]:
    """盘后调用：若是信号日，生成并落盘各中线策略的选股。非致命。

    horizon / strategy_ids 只用于手动补跑（POST /api/horizons/{mid,long}/run），默认 None = 17:35 全部策略。
    幂等：同一 as_of 重跑覆盖同一文件；_held_codes 只看 as_of 之前的批次。

    apply_daily_cap：每策略每日新推荐上限（settings.max_daily_picks_per_strategy，默认 5）。仅实盘调用方
    （17:35 定时任务 / 手动补跑 API）传 True；默认 False，历史重建/回放（scripts/rebuild_replay_picks.py）与既有测试不受影响。"""
    d = _dt.date.fromisoformat(as_of)
    try:
        check_daily_basic_staleness(db, as_of)
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_basic staleness check failed: %s", exc)
    result: dict[str, Any] = {}
    try:
        _generate_picks_loop(db, as_of, d, force, horizon, strategy_ids, result, apply_daily_cap)
    except Exception as exc:
        cur = result.pop("__current__", None)
        if cur:
            result[cur] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        _record_status(as_of, result)
        raise
    result.pop("__current__", None)
    _record_status(as_of, result)
    return result


def _generate_picks_loop(db, as_of: str, d, force: bool, horizon, strategy_ids, result: dict[str, Any],
                         apply_daily_cap: bool = False) -> None:
    rp_cands: list[dict[str, Any]] | None | str = "unset"
    for sid, meta in STRATEGIES.items():
        if horizon and meta.get("horizon") != horizon:
            continue
        if strategy_ids and sid not in strategy_ids:
            continue
        if meta.get("schedule") == "month_end":
            if not force and not _is_month_end(db, as_of):
                result[sid] = {"skipped": "not_signal_day"}
                continue
        elif not force and not is_signal_day(sid, d):
            result[sid] = {"skipped": "not_signal_day"}
            continue
        result["__current__"] = sid
        if meta.get("kind") in ("lowturn_lowvol", "dividend_lowvol"):
            cand_fn = lowturn_lowvol_candidates if meta["kind"] == "lowturn_lowvol" else dividend_lowvol_candidates
            cands = cand_fn(db, as_of, int(meta["top_n"]))
            if cands is None:
                result[sid] = "no_data"
                continue
            held = _held_codes(db, sid, as_of)
            picks = [p for p in cands if p["code"] not in held]  # 已持有不重复买、也不顺延补位（与回测一致）
            if not picks:
                result[sid] = {"skipped": "all_held"} if cands else 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "earnings_accel":
            held = _held_codes(db, sid, as_of)
            ea = earnings_accel_candidates(db, as_of)
            if ea is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ea if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "sue_accel":
            from app.services.midterm_sueg import sueg_candidates
            held = _held_codes(db, sid, as_of)
            sg = sueg_candidates(db, as_of)
            if sg is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in sg if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "earnings_accel_vol":
            from app.services.midterm_eavol import eavol_candidates
            held = _held_codes(db, sid, as_of)
            ev = eavol_candidates(db, as_of)
            if ev is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ev if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "sue_accel_vol":
            from app.services.midterm_suegvol import suegvol_candidates
            held = _held_codes(db, sid, as_of)
            sg = suegvol_candidates(db, as_of)
            if sg is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in sg if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_beat":
            from app.services.midterm_forecast import forecast_beat_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_beat_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "forecast_beat_underreact":
            from app.services.midterm_forecast import forecast_beat_underreact_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_beat_underreact_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_turnaround":
            from app.services.midterm_forecast import forecast_turnaround_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_turnaround_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_preincrease":
            from app.services.midterm_forecast import forecast_preincrease_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_preincrease_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "forecast_preincrease_underreact":
            from app.services.midterm_forecast import forecast_preincrease_underreact_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_preincrease_underreact_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_preincrease_vol":
            from app.services.midterm_forecast import forecast_preincrease_vol_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_preincrease_vol_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_preincrease_underreact_vol":
            from app.services.midterm_forecast import forecast_preincrease_underreact_vol_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_preincrease_underreact_vol_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "forecast_slight":
            from app.services.midterm_forecast import forecast_slight_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_slight_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "forecast_slight_underreact":
            from app.services.midterm_forecast import forecast_slight_underreact_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_slight_underreact_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
        if meta.get("kind") == "forecast_slight_vol":
            from app.services.midterm_forecast import forecast_slight_vol_candidates
            held = _held_codes(db, sid, as_of)
            fb = forecast_slight_vol_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
            picks = [p for p in fb if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "incentive_option":
            from app.services.midterm_incentive import incentive_candidates
            held = _held_codes(db, sid, as_of)
            ic = incentive_candidates(db, as_of)
            if ic is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ic if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "incentive_underreact":
            from app.services.midterm_incentive import incentive_underreact_candidates
            held = _held_codes(db, sid, as_of)
            ic = incentive_underreact_candidates(db, as_of)
            if ic is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ic if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "incentive_vol":
            from app.services.midterm_incentive import incentive_vol_candidates
            held = _held_codes(db, sid, as_of)
            ic = incentive_vol_candidates(db, as_of)
            if ic is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ic if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "sexp_vol":
            from app.services.midterm_fina_events import sexpvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = sexpvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fina_vol":
            from app.services.midterm_fina_events import finavol_candidates
            held = _held_codes(db, sid, as_of)
            cands = finavol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "dinc_vol":
            from app.services.midterm_fina_events import dincvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = dincvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "td_vol":
            from app.services.midterm_fina_events import tdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = tdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fat_vol":
            from app.services.midterm_fina_events import fatvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = fatvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "tad_vol":
            from app.services.midterm_fina_events import tadvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = tadvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "etd_vol":
            from app.services.midterm_fina_events import etdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = etdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue




        if meta.get("kind") == "etd_fat_vol":
            from app.services.midterm_fina_events import etdfatvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = etdfatvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue


        if meta.get("kind") == "ncs_td_vol":
            from app.services.midterm_fina_events import ncstdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = ncstdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fat_td_vol":
            from app.services.midterm_fina_events import fattdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = fattdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "tad_etd_vol":
            from app.services.midterm_fina_events import tadetdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = tadetdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "sue_td_vol":
            from app.services.midterm_suegvol import suegtdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = suegtdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "sue_fa_vol":
            from app.services.midterm_suegvol import suegfatvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = suegfatvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "sexp_sue_vol":
            from app.services.midterm_suegvol import sexpsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = sexpsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "admin_sue_vol":
            from app.services.midterm_suegvol import adminsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = adminsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "op_sue_vol":
            from app.services.midterm_suegvol import opsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = opsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "rev_sue_vol":
            from app.services.midterm_suegvol import revsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = revsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "qop_sue_vol":
            from app.services.midterm_suegvol import qopsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = qopsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "ocfs_sue_vol":
            from app.services.midterm_suegvol import ocfssuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = ocfssuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "ocsd_sue_vol":
            from app.services.midterm_suegvol import ocsdsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = ocsdsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "npm_sue_vol":
            from app.services.midterm_suegvol import npmsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = npmsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "qs_sue_vol":
            from app.services.midterm_suegvol import qssuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = qssuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fcff_sue_vol":
            from app.services.midterm_suegvol import fcffsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = fcffsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "ocf_sue_vol":
            from app.services.midterm_suegvol import ocfsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = ocfsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue


        if meta.get("kind") == "cfps_sue_vol":
            from app.services.midterm_suegvol import cfpssuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = cfpssuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue


        if meta.get("kind") == "udp_sue_vol":
            from app.services.midterm_suegvol import udpsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = udpsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fcfe_sue_vol":
            from app.services.midterm_suegvol import fcfesuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = fcfesuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "fat_sue_vol":
            from app.services.midterm_suegvol import fatsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = fatsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "qn_sue_vol":
            from app.services.midterm_suegvol import qnsuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = qnsuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "roy_sue_vol":
            from app.services.midterm_suegvol import roysuegvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = roysuegvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue



        if meta.get("kind") == "ea_td_vol":
            from app.services.midterm_eavol import eatdvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = eatdvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "ea_cas_vol":
            from app.services.midterm_eavol import eacasvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = eacasvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") == "cas_vol":
            from app.services.midterm_fina_events import casvol_candidates
            held = _held_codes(db, sid, as_of)
            cands = casvol_candidates(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue

        if meta.get("kind") in ("cld_yoy", "dinc_yoy", "udp_yoy", "ncs_switch", "cg_streak", "sexp_yoy", "fina_yoy"):
            from app.services import midterm_fina_events as fe
            cand_fn = {"cld_yoy": fe.cld_candidates, "dinc_yoy": fe.dinc_candidates, "udp_yoy": fe.udp_candidates,
                       "ncs_switch": fe.ncs_candidates, "cg_streak": fe.cg_candidates, "sexp_yoy": fe.sexp_candidates, "fina_yoy": fe.fina_candidates}[meta["kind"]]
            # 实盘推荐不设持仓只数上限；每日新推荐数量由 _save_picks(apply_daily_cap) 统一限制（默认 5，按排序取前 N），已持有的不重复买；回测指标按 20 仓位口径
            held = _held_codes(db, sid, as_of)
            cands = cand_fn(db, as_of)
            if cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in cands if p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "block_trade_premium":
            result[sid] = _block_trade_picks(db, sid, meta, as_of, apply_daily_cap)
            continue
        if meta.get("kind") == "repurchase":
            held = _held_codes(db, sid, as_of)
            if rp_cands == "unset":
                rp_cands = repurchase_plan_candidates(db, as_of)
            if rp_cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in rp_cands if p["ratio"] >= meta["min_ratio"] and p["code"] not in held]
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        if meta.get("kind") == "repurchase_underreact":
            held = _held_codes(db, sid, as_of)
            if rp_cands == "unset":
                rp_cands = repurchase_plan_candidates(db, as_of)
            if rp_cands is None:
                result[sid] = "no_data"
                continue
            pool = [p for p in rp_cands if p["ratio"] >= meta["min_ratio"] and p["code"] not in held]
            picks = _filter_repurchase_underreact(db, as_of, pool)
            if not picks:
                result[sid] = 0
                continue
            result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)
            continue
        # daily_basic 市值未更新到 as_of 时不拿旧市值排名/算小盘指数（10-16 批次曾用 10-09 市值）：标 data_error，补数后重跑。
        basic_max = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
        if basic_max is None or str(basic_max)[:10] < as_of:
            result[sid] = {"error": "daily_basic_stale", "daily_basic_max": str(basic_max)[:10] if basic_max else None}
            continue
        guard = small_cap_guard(db, as_of) if meta.get("guard") else {"on": True}
        if not guard["on"]:
            result[sid] = {"skipped": "small_cap_below_ma20", **guard}
            continue
        if meta.get("kind") == "lowprice_small":
            picks = _lowprice_universe(db, as_of, meta["top_n"])
        elif meta.get("kind") in ("smallcap_div", "smallcap_lowps", "smallcap_rev"):
            picks = _smallcap_div_universe(db, as_of, meta["top_n"], factor={"smallcap_lowps": "ps_ttm", "smallcap_rev": "r20"}.get(meta["kind"], "dv_ttm"))
            if picks is None:
                result[sid] = "no_data"
                continue
        else:
            picks = _universe(db, as_of, meta["top_n"])
        held = _held_codes(db, sid, as_of)
        picks = [p for p in picks if p["code"] not in held]  # 已持有不重复买、也不顺延补位（与回测一致）
        if not picks and held:
            result[sid] = {"skipped": "all_held"}
            continue
        if not picks:
            result[sid] = "no_data"
            continue
        result[sid] = _save_picks(sid, as_of, picks, apply_daily_cap)


DAILY_STATUS_VALUES = ("ok", "no_signal", "data_error", "slots_full", "not_signal_day")  # slots_full 仅用于 10-22 前的旧状态文件（持仓名额上限已去掉）


def classify_daily_status(raw: Any) -> str:
    """generate_picks 单策略原始结果 -> ok / no_signal / data_error / slots_full / not_signal_day。"""
    if isinstance(raw, bool):
        return "data_error"
    if isinstance(raw, int):
        return "ok" if raw > 0 else "no_signal"
    if raw == "no_data":
        return "data_error"
    if isinstance(raw, dict):
        if "error" in raw:
            return "data_error"
        sk = raw.get("skipped")
        if sk == "slots_full":
            return "slots_full"
        if sk == "not_signal_day":
            return "not_signal_day"
        if sk in ("all_held", "small_cap_below_ma20"):
            return "no_signal"
    return "data_error"


def _status_dir() -> Path:
    """每日各策略运行状态 {as_of}.json 目录（generate_picks 落盘，/strategies 读）；跟随 OUT_DIR，测试 monkeypatch 时一并隔离。"""
    return OUT_DIR.parent / "status"


def _record_status(as_of: str, result: dict[str, Any]) -> None:
    """把本次结果并入 status/{as_of}.json（同日多次运行按策略覆盖）。非致命。"""
    try:
        sd = _status_dir()
        sd.mkdir(parents=True, exist_ok=True)
        f = sd / f"{as_of}.json"
        stored = json.loads(f.read_text()) if f.exists() else {}
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        for sid, raw in result.items():
            if sid.startswith("__"):
                continue
            stored[sid] = {"status": classify_daily_status(raw), "raw": raw, "recorded_at": now}
        f.write_text(json.dumps(stored, ensure_ascii=False, indent=1, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("record midterm status failed: %s", exc)


def latest_daily_status(sid: str, *, lookback: int = 15) -> dict[str, Any] | None:
    """最近一个有记录的业务日里该策略的状态；忽略 not_signal_day 之前先返回最近的那条（含 not_signal_day）。"""
    sd = _status_dir()
    if not sd.exists():
        return None
    for f in sorted(sd.glob("*.json"), reverse=True)[:lookback]:
        try:
            row = json.loads(f.read_text()).get(sid)
        except Exception:  # noqa: BLE001
            continue
        if row:
            out = {"date": f.stem, "status": row.get("status"), "recorded_at": row.get("recorded_at")}
            raw = row.get("raw")
            if isinstance(raw, dict):
                out["detail"] = raw.get("skipped") or raw.get("error")
            return out
    return None


def _block_trade_picks(db, sid: str, meta: dict[str, Any], as_of: str, apply_daily_cap: bool = False) -> Any:
    """mid-block-trade-premium 出票（幂等：同一 as_of 重跑覆盖同一文件）。大宗数据未发布 -> "no_data"、不落盘。"""
    from app.services.midterm_block_trade import block_trade_candidates
    held = _held_codes(db, sid, as_of)
    bt = block_trade_candidates(db, as_of)
    if bt is None:
        return "no_data"
    picks = [p for p in bt if p["code"] not in held]
    if not picks:
        return 0
    return _save_picks(sid, as_of, picks, apply_daily_cap)


@_formal_entry
def generate_block_trade_picks(db, as_of: str, apply_daily_cap: bool = False) -> dict[str, Any]:
    """只跑 mid-block-trade-premium（as_of = 大宗交易日 T，YYYY-MM-DD）。供 21:00 后 / T+1 开盘前的晚间任务调用。"""
    sid = "mid-block-trade-premium"
    try:
        res = {sid: _block_trade_picks(db, sid, STRATEGIES[sid], as_of, apply_daily_cap)}
    except Exception as exc:
        _record_status(as_of, {sid: {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}})
        raise
    _record_status(as_of, res)
    return res


_REALIZED_FINAL: dict[tuple[str, str, int], dict[str, Any]] = {}
# 待买入 / 持有中的结算依赖最新日K：按「日K + 涨跌停表」的库内数据版本缓存（Prefect 写入新日K后版本立刻变），
# 版本变了或超过 _LIVE_MAX_AGE 秒就整体作废。条目数 = 未平仓股票数（约千级），另设上限防意外膨胀。
_REALIZED_LIVE: dict[tuple[str, str, int], dict[str, Any]] = {}
_LIVE_STATE: dict[str, Any] = {"version": None, "ts": 0.0}
_LIVE_LOCK = threading.Lock()
_PREFETCH_LOCK = threading.Lock()
_LIVE_MAX_AGE = 600.0
_LIVE_MAX_ENTRIES = 20000
_BAR_SQL = (
    "SELECT k.trade_date, k.open, k.high, k.close, k.pct_chg, l.up_limit, l.down_limit FROM daily_kbars k "
    "LEFT JOIN stk_limits l ON l.code=k.code AND l.trade_date=k.trade_date "
)


def _realize(db, code: str, signal_date: str, hold: int = HOLD_DAYS) -> dict[str, Any]:
    """结算单只入选股。已平仓/开盘涨停取消的结果之后不会再变，进程内缓存（复盘、交易明细、诊断每次
    都要全量结算 700+ 只，逐只查日K约 2.5ms/只）；待买入/持有中先查按数据版本缓存的结果
    （由 prefetch_realized 批量填充并校验版本），没有再单查。"""
    key = (code, signal_date, hold)
    hit = _REALIZED_FINAL.get(key)
    if hit is not None:
        return dict(hit)
    live = _REALIZED_LIVE.get(key)
    if live is not None:
        return dict(live)
    out = _realize_query(db, code, signal_date, hold)
    _remember_realized(key, out, None)  # 单查不知道对应哪个数据版本：未平仓结果不进缓存
    return out


def _remember_realized(key: tuple[str, str, int], out: dict[str, Any], version: Any) -> None:
    if out["status"] in ("closed", "skipped"):
        _REALIZED_FINAL[key] = dict(out)
        return
    with _LIVE_LOCK:
        # 只在版本没被别的请求切换时写入，避免旧数据算出的结果挂到新版本下
        if version is not None and version == _LIVE_STATE["version"] and len(_REALIZED_LIVE) < _LIVE_MAX_ENTRIES:
            _REALIZED_LIVE[key] = dict(out)


def _refresh_live_version(db) -> Any:
    """取一次库内数据版本（两个 MAX 查询，<1ms）；变了或过期就清空未平仓结算缓存。返回当前版本。"""
    from app.services.data_version import data_version

    version = data_version(db, ("daily_kbars", "stk_limits"))
    with _LIVE_LOCK:
        if (version is None or version != _LIVE_STATE["version"]
                or time.time() - _LIVE_STATE["ts"] >= _LIVE_MAX_AGE):
            _REALIZED_LIVE.clear()
            _LIVE_STATE["version"] = version
            _LIVE_STATE["ts"] = time.time()
    return version


def prefetch_realized(db, batches: list[tuple[str, str, list[str]]]) -> None:
    """批量结算：先校验数据版本，再把还没缓存的 (code, signal_date, hold) 用少量 IN 查询一次取齐日K，
    逐只按与 _realize_query 完全相同的规则计算（同一 SQL 条件：trade_date>signal_date、close>0、按日期取前 hold+11 根）。
    batches: [(strategy_id, signal_date, [code, ...]), ...]"""
    if db is None:  # 无库会话（单测里 _realize 被替换）时不预取，逐只走 _realize
        return
    version = _refresh_live_version(db)
    if not _missing_realized(batches):
        return
    # 同一时刻只让一个请求做批量结算（复盘页会并发发 3~4 个请求，都要同一批股票）；
    # 后进来的等它算完，再看还缺什么，通常已经不缺了。
    with _PREFETCH_LOCK:
        need = _missing_realized(batches)
        codes = sorted(need)
        for i in range(0, len(codes), 200):
            chunk = codes[i:i + 200]
            # 每只股票各自的下界（该股最早的信号日），避免一只老信号把整批都拉长
            conds = " OR ".join(f"(k.code=:c{j} AND k.trade_date>:d{j})" for j in range(len(chunk)))
            params: dict[str, Any] = {}
            for j, code in enumerate(chunk):
                params[f"c{j}"] = code
                params[f"d{j}"] = min(d for d, _ in need[code])
            rows = db.execute(
                text(_BAR_SQL.replace("SELECT k.trade_date", "SELECT k.code AS _code, k.trade_date")
                     + f"WHERE ({conds}) AND k.close>0 ORDER BY k.code, k.trade_date"),
                params,
            ).all()
            by_code: dict[str, list[Any]] = {}
            for r in rows:
                by_code.setdefault(r._code, []).append(r)
            for code in chunk:
                bars = by_code.get(code, [])
                dates = [r.trade_date for r in bars]
                for signal_date, hold in need[code]:
                    start = bisect.bisect_right(dates, signal_date)  # 第一根 trade_date > signal_date
                    sub = bars[start:start + hold + 11]
                    _remember_realized((code, signal_date, hold), _realize_rows(sub, hold), version)


def _missing_realized(batches: list[tuple[str, str, list[str]]]) -> dict[str, list[tuple[str, int]]]:
    need: dict[str, list[tuple[str, int]]] = {}
    for sid, signal_date, codes in batches:
        hold = _hold(sid)
        for code in codes:
            key = (code, signal_date, hold)
            if key in _REALIZED_FINAL or key in _REALIZED_LIVE:
                continue
            need.setdefault(code, []).append((signal_date, hold))
    return need


def warm_realized_cache() -> None:
    """启动后台预热：把全部批次结算一遍，首次打开中长线复盘/诊断页不用等。"""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        _load_batches(db, _pick_files(None, None))
    finally:
        db.close()


def _realize_query(db, code: str, signal_date: str, hold: int) -> dict[str, Any]:
    """次日开盘买入（一字/开盘涨停买不进 -> skipped）；第 hold 个交易日开盘卖出，跌停封死顺延；0.3% 成本。"""
    rows = db.execute(text(
        _BAR_SQL + "WHERE k.code=:c AND k.trade_date>:d AND k.close>0 ORDER BY k.trade_date LIMIT :n"),
        {"c": code, "d": signal_date, "n": hold + 11}).all()
    return _realize_rows(rows, hold)


def _realize_rows(rows: list[Any], hold: int) -> dict[str, Any]:
    """结算规则本体（单查 / 批量共用）：rows 为信号日之后、close>0、按日期升序的前 hold+11 根日K。"""
    if not rows or not rows[0].open:
        return {"status": "pending"}
    e = rows[0]
    if e.up_limit and e.open >= e.up_limit - 0.005:
        return {"status": "skipped", "entry_date": e.trade_date, "reason": "开盘涨停买不进",
                "cancel_code": "limit_up_open", "entry_open": e.open}
    tri = 1.0
    for i, r in enumerate(rows):
        if i > 0:
            tri *= 1 + (r.pct_chg or 0) / 100
        if i >= hold and not (r.down_limit and r.high <= r.down_limit + 0.005):
            ret = (r.open / r.close * tri) / (e.open / e.close) - 1 - 0.003
            return {"status": "closed", "entry_date": e.trade_date, "exit_date": r.trade_date,
                    "ret": round(ret * 100, 2), "days": i, "entry_price": e.open, "exit_price": r.open,
                    "exit_delayed_days": i - hold}
    last = rows[-1]
    ret = tri / (e.open / e.close) - 1
    return {"status": "holding", "entry_date": e.trade_date, "mark_date": last.trade_date,
            "ret": round(ret * 100, 2), "days": len(rows) - 1, "entry_price": e.open, "mark_price": last.close}


# 统一 rule_score：各策略自己的排序指标（原单位，见 label / better），selection_reason 为中文入选依据。
# 业绩预告：列表展示真实增幅下限；排序分仍是落盘的 score=min(原值, 300)，批内名次按该分再按代码，这里不重排。
# 业绩加速：新批次 score 是 §RV-C EA_combo（ann_pct + mv_pct）。没有这两项的旧批次仍按封顶同比展示，避免把 300 分说成百分位。
_RANK_CAP_NOTE = "排序时≥300%同等优先，平手按代码"
_EA_COMBO_NOTE = "当日候选池内等权相加，平手按代码"


def _f(x: Any, nd: int = 2) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _shown_pct(raw: Any, fallback: Any) -> float:
    """列表上的同比：有原始值就按 1 位小数展示（与入选理由同一格式）；缺字段时退回已落盘的排序分。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(fallback)
    return float(f"{v:.1f}")


def annotate_pick(sid: str, p: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    kind = STRATEGIES.get(sid, {}).get("kind", "small_cap" if sid == "mid-small-cap-weekly" else None)
    score = label = better = reason = note = None
    try:
        if kind == "small_cap":
            score, label, better = round(float(p["total_mv"]) / 1e4, 2), "总市值(亿元)", "lower"
            reason = f"总市值 {_f(score)} 亿，合格股（非ST/北交所、PE>0、上市≥1年、20日均额>3000万）里最小的10只之一；PE_ttm {_f(p.get('pe_ttm'), 1)}"
        elif kind == "lowprice_small":
            score, label, better = round(float(p["total_mv"]) / 1e4, 2), "总市值(亿元)", "lower"
            pe = p.get("pe_ttm")
            reason = (f"收盘价 {_f(p.get('close'))} 元（合格股里股价第 {p.get('price_rank', '')} 低）、总市值 {_f(score)} 亿（第 {p.get('mv_rank', '')} 小），"
                      f"同时在「股价最低200只」和「市值最小200只」里；PE_ttm {_f(pe, 1) if pe is not None else '亏损/无'}")
        elif kind == "smallcap_div":
            score, label, better = round(float(p["dv_ttm"]), 2), "股息率TTM(%)", "higher"
            reason = (f"股息率TTM {_f(score)}%（{p.get('dv_snap', '')} 周度快照），总市值 {_f(float(p['total_mv']) / 1e4)} 亿"
                      f"（合格股里第 {p.get('mv_rank', '')} 小，在最小400只内）；PE_ttm {_f(p.get('pe_ttm'), 1)}")
        elif kind == "smallcap_lowps":
            score, label, better = round(float(p["ps_ttm"]), 2), "市销率PS_TTM", "lower"
            reason = (f"市销率PS_TTM {_f(score)}（{p.get('dv_snap', '')} 周度快照，最小400只小票里最低的一批），总市值 {_f(float(p['total_mv']) / 1e4)} 亿"
                      f"（合格股里第 {p.get('mv_rank', '')} 小）；PE_ttm {_f(p.get('pe_ttm'), 1)}")
        elif kind == "smallcap_rev":
            score, label, better = round(float(p["r20"]), 2), "近20日涨跌幅(%)", "lower"
            reason = (f"近20个交易日涨跌幅 {_f(score)}%（最小400只小票里跌得最多的一批），总市值 {_f(float(p['total_mv']) / 1e4)} 亿"
                      f"（合格股里第 {p.get('mv_rank', '')} 小）；PE_ttm {_f(p.get('pe_ttm'), 1)}")
        elif kind == "repurchase":
            score, label, better = round(float(p["ratio"]) * 100, 3), "回购金额上限/总市值(%)", "higher"
            reason = f"{p.get('ann_date', '')} 回购预案，金额上限 {_f(float(p.get('repurchase_amount') or 0) / 1e8)} 亿元，占总市值 {_f(score)}%"
        elif kind == "repurchase_underreact":
            score, label, better = round(float(p.get("signal_pct") if p.get("signal_pct") is not None else -float(p.get("score") or 0)), 3), "信号日涨跌(%)", "lower"
            reason = (f"{p.get('ann_date', '')} 回购预案弱反应，金额上限 {_f(float(p.get('repurchase_amount') or 0) / 1e8)} 亿元，"
                      f"占总市值 {_f(float(p.get('ratio') or 0) * 100, 3)}%，信号日涨跌 {_f(score)}%")
        elif kind == "earnings_accel":
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：扣非净利同比 {_f(p.get('dt_netprofit_yoy'), 1)}%"
                      f"（上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，加速），营收同比 {_f(p.get('or_yoy'), 1)}%")
            if "ann_pct" in p and "mv_pct" in p:
                score, label, better = round(float(p["score"]), 4), "公告日涨幅百分位+小市值百分位", "higher"
                note = _EA_COMBO_NOTE
                mv_yi = _finite(p.get("total_mv"))
                mv_yi = None if mv_yi is None else mv_yi / 1e4
                reason += (f"；信号日涨跌幅 {_f(p.get('ann_pct_chg'), 2)}%，总市值 {_f(mv_yi)} 亿"
                           f"（涨幅百分位 {_f(float(p['ann_pct']) * 100, 1)}%，小市值百分位 {_f(float(p['mv_pct']) * 100, 1)}%）")
            else:
                score, label, better = _shown_pct(p.get("dt_netprofit_yoy"), p["score"]), "扣非净利同比(%)", "higher"
                note = _RANK_CAP_NOTE
        elif kind == "sue_accel":
            score, label, better = round(float(p["score"]), 4), "SUE(封顶20)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：SUE {_f(p.get('sue'), 2)}，"
                      f"单季扣非 {_f(float(p.get('q_sq') or 0) / 1e8, 2)} 亿；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）")
        elif kind == "earnings_accel_vol":
            score, label, better = _shown_pct(p.get("dt_netprofit_yoy"), p["score"]), "扣非净利同比(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告放量确认：扣非净利同比 {_f(p.get('dt_netprofit_yoy'), 1)}%"
                      f"（上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，加速），营收同比 {_f(p.get('or_yoy'), 1)}%；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "sue_accel_vol":
            score, label, better = round(float(p["score"]), 4), "SUE(封顶20)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：SUE {_f(p.get('sue'), 2)}，"
                      f"单季扣非 {_f(float(p.get('q_sq') or 0) / 1e8, 2)} 亿；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "forecast_beat":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告（{p.get('forecast_type', '')}，{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元")
        elif kind == "forecast_beat_underreact":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告弱反应（{p.get('forecast_type', '')}，{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%")
        elif kind == "forecast_turnaround":
            if p.get("p_change_min") is not None:
                score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
                note = _RANK_CAP_NOTE
            else:
                score, label, better = round(float(p.get("net_profit_min") or p["score"]) / 1e8, 3), "预告净利下限(亿元)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次业绩预告扭亏（{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e8, 3)} 亿元")
        elif kind == "forecast_preincrease":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告预增（{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元")
        elif kind == "forecast_preincrease_underreact":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告预增弱反应（{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%")
        elif kind == "forecast_preincrease_vol":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告预增放量（{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "forecast_preincrease_underreact_vol":
            score, label, better = _shown_pct(p.get("p_change_min"), p["score"]), "预告净利增幅下限(%)", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 首次业绩预告预增弱反应放量（{p.get('end_date', '')}）："
                      f"增幅下限 {_f(p.get('p_change_min'), 1)}%，净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e4)} 亿元；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "forecast_slight":
            score, label, better = round(float(p.get("net_profit_min") or p["score"]) / 1e8, 3), "预告净利下限(亿元)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次业绩预告略增（{p.get('end_date', '')}）："
                      f"净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e8, 3)} 亿元，增幅下限 {_f(p.get('p_change_min'), 1)}%")
        elif kind == "forecast_slight_underreact":
            score, label, better = round(float(p.get("net_profit_min") or p["score"]) / 1e8, 3), "预告净利下限(亿元)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次业绩预告略增弱反应（{p.get('end_date', '')}）："
                      f"净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e8, 3)} 亿元，增幅下限 {_f(p.get('p_change_min'), 1)}%；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%")
        elif kind == "forecast_slight_vol":
            score, label, better = round(float(p.get("net_profit_min") or p["score"]) / 1e8, 3), "预告净利下限(亿元)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次业绩预告略增放量（{p.get('end_date', '')}）："
                      f"净利下限 {_f(float(p.get('net_profit_min') or 0) / 1e8, 3)} 亿元，增幅下限 {_f(p.get('p_change_min'), 1)}%；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "incentive_option":
            score, label, better = round(float(p["r20"]) * 100, 2), "公告前20日涨幅(%)", "lower"
            reason = f"{p.get('ann_date', '')} 首次披露含股票期权的激励草案：{p.get('title', '')}；公告前20日涨幅 {_f(score)}%"
        elif kind == "incentive_underreact":
            score, label, better = round(float(p.get("signal_pct") if p.get("signal_pct") is not None else -float(p["score"])), 2), "信号日涨跌(%)", "lower"
            reason = (f"{p.get('ann_date', '')} 首次披露含股票期权的激励草案弱反应：{p.get('title', '')}；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%")
        elif kind == "incentive_vol":
            score, label, better = round(float(p.get("signal_pct") if p.get("signal_pct") is not None else -float(p["score"])), 2), "信号日涨跌(%)", "lower"
            reason = (f"{p.get('ann_date', '')} 首次披露含股票期权的激励草案放量：{p.get('title', '')}；"
                      f"信号日涨跌 {_f(p.get('signal_pct'), 2)}%；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "block_trade_premium":
            score, label, better = round(float(p.get("premium") or p["score"]) * 100, 3), "大宗溢价率(%)", "higher"
            reason = (f"大宗成交 {p.get('n_trades', '')} 笔，量加权价相对收盘溢价 {_f(score)}%，"
                      f"成交额占流通市值 {_f(float(p.get('ratio') or 0) * 100, 3)}%")
        elif kind == "lowturn_lowvol":
            score, label, better = float(p["score"]), "60日换手排名+60日波动排名(越低越好)", "lower"
            reason = f"市值第 {p.get('mv_rank', '')} 名；60日均换手 {_f(p.get('turn60'))}%、60日日波动 {_f(p.get('vol60'))}%，合计排名最低的10只之一"
        elif kind == "cld_yoy":
            score, label, better = round(float(p["score"]), 2), "流动负债占比降幅(百分点，封顶40)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：流动负债/负债合计 {_f(p.get('prev'), 1)}% → "
                      f"{_f(p.get('cur'), 1)}%，同比下降 {_f(p.get('delta'), 1)} 个百分点")
        elif kind == "sexp_yoy":
            score, label, better = round(float(p["score"]), 2), "销售费用率降幅(百分点，封顶10)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：销售费用率 {_f(p.get('prev'), 1)}% → "
                      f"{_f(p.get('cur'), 1)}%，同比下降 {_f(p.get('delta'), 1)} 个百分点")
        elif kind == "sexp_vol":
            score, label, better = round(float(p["score"]), 2), "销售费用率降幅(百分点，原始/§694去封顶)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告销售费用率下降放量："
                      f"{_f(p.get('prev'), 1)}% → {_f(p.get('cur'), 1)}%，同比下降 {_f(p.get('delta'), 1)} 个百分点；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fina_yoy":
            score, label, better = round(float(p["score"]), 2), "财务费用率降幅(百分点)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：财务费用率 {_f(p.get('prev'), 1)}% → "
                      f"{_f(p.get('cur'), 1)}%，同比下降 {_f(p.get('delta'), 1)} 个百分点")
        elif kind == "fina_vol":
            score, label, better = round(float(p["score"]), 2), "财务费用率降幅(百分点)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告财务费用率下降放量："
                      f"{_f(p.get('prev'), 1)}% → {_f(p.get('cur'), 1)}%，同比下降 {_f(p.get('delta'), 1)} 个百分点；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "dinc_yoy":
            score, label, better = round(float(p["inc"]) * 100, 1), "每股派现同比增幅(%)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次公布 {p.get('end_date', '')} 分红预案：每股税前派现 {_f(p.get('cash_div_tax'), 3)} 元，"
                      f"去年同期 {_f(p.get('prev_dps'), 3)} 元，同比 +{_f(score, 1)}%")
        elif kind == "dinc_vol":
            score, label, better = round(float(p["inc"]) * 100, 1), "每股派现同比增幅(%)", "higher"
            reason = (f"{p.get('ann_date', '')} 首次公布 {p.get('end_date', '')} 分红预案放量：每股税前派现 {_f(p.get('cash_div_tax'), 3)} 元，"
                      f"去年同期 {_f(p.get('prev_dps'), 3)} 元，同比 +{_f(score, 1)}%；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "td_vol":
            score, label, better = round(float(p["score"]), 1), "营业周期缩短天数(封顶200)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告营业周期缩短放量："
                      f"{_f(p.get('prev'), 1)} 日 → {_f(p.get('cur'), 1)} 日，缩短 {_f(p.get('improve'), 1)} 日；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fat_vol":
            score, label, better = round(float(p["score"]), 3), "固定资产周转升幅(封顶2.0)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告固定资产周转扩张放量："
                      f"{_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)}，升幅 {_f(p.get('dq'), 3)}；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "tad_vol":
            score, label, better = round(float(p["score"]), 3), "有形资产/负债升幅(封顶2.0)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告有形资产/负债改善放量："
                      f"{_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)}，升幅 {_f(p.get('delta'), 3)}；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "etd_vol":
            score, label, better = round(float(p["score"]), 3), "股东权益/负债升幅(封顶2.0)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告股东权益/负债改善放量："
                      f"{_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)}，升幅 {_f(p.get('delta'), 3)}；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "etd_fat_vol":
            score, label, better = round(float(p["score"]), 3), "股东权益/负债升幅(封顶2.0)×FAT合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告 ETD∩FAT 放量："
                      f"eqt_to_debt {_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)} 升幅 {_f(p.get('delta'), 3)}；"
                      f"fa_turn 升幅 {_f(p.get('fat_dq'), 3)}；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "ncs_td_vol":
            score, label, better = round(float(p["score"]), 3), "净现金转换强度(封顶3.0)×TD合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告 NCS∩TD 放量："
                      f"净负债 {_f(p.get('netdebt_prev'), 2)} → {_f(p.get('netdebt'), 2)}；"
                      f"营业周期缩短 {_f(p.get('td_improve'), 1)} 日；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fat_td_vol":
            score, label, better = round(float(p["score"]), 3), "固定资产周转升幅(封顶2.0)×TD合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告 FAT∩TD 放量："
                      f"fa_turn {_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)} 升幅 {_f(p.get('dq'), 3)}；"
                      f"营业周期缩短 {_f(p.get('td_improve'), 1)} 日；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "tad_etd_vol":
            score, label, better = round(float(p["score"]), 3), "有形资产/负债升幅(封顶2.0)×ETD合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告 TAD∩ETD 放量："
                      f"有形资产/负债 {_f(p.get('prev'), 3)} → {_f(p.get('cur'), 3)} 升幅 {_f(p.get('delta'), 3)}；"
                      f"股东权益/负债升幅 {_f(p.get('etd_delta'), 3)}；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "sue_td_vol":
            score, label, better = round(float(p["score"]), 4), "SUE(封顶20)×TD合取", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：SUE {_f(p.get('sue'), 2)} ∩ 营业周期缩短 {_f(p.get('td_improve'), 1)} 日；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "sue_fa_vol":
            score, label, better = round(float(p["score"]), 4), "SUE(封顶20)×FAT合取", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：SUE {_f(p.get('sue'), 2)} ∩ fa_turn 升幅 {_f(p.get('fat_dq'), 3)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "sexp_sue_vol":
            score, label, better = round(float(p["score"]), 3), "销售费用率降幅(pp)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：销售费用率 {_f(p.get('sexp_prev'), 3)} → {_f(p.get('sexp_cur'), 3)}"
                      f"（降幅 {_f(p.get('sexp_delta'), 3)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "admin_sue_vol":
            score, label, better = round(float(p["score"]), 3), "管理费用率降幅(pp,封顶10)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：管理费用率 {_f(p.get('admin_prev'), 3)} → {_f(p.get('admin_cur'), 3)}"
                      f"（降幅 {_f(p.get('admin_delta'), 3)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "op_sue_vol":
            score, label, better = round(float(p["score"]), 1), "营业利润同比加速(pp,封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：营业利润同比 {_f(p.get('prev_op_yoy'), 1)}% → {_f(p.get('op_yoy'), 1)}%"
                      f"（加速 {_f(p.get('op_accel'), 1)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "rev_sue_vol":
            score, label, better = round(float(p["score"]), 1), "营收同比加速(pp,封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：营收同比 {_f(p.get('prev_or_yoy'), 1)}% → {_f(p.get('rev_or_yoy'), 1)}%"
                      f"（加速 {_f(p.get('rev_accel'), 1)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "qop_sue_vol":
            score, label, better = round(float(p["score"]), 1), "单季营业利润环比加速(pp,封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：单季营业利润环比 {_f(p.get('prev_q_op_qoq'), 1)}% → {_f(p.get('q_op_qoq'), 1)}%"
                      f"（加速 {_f(p.get('qop_accel'), 1)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "ocfs_sue_vol":
            score, label, better = round(float(p["score"]), 2), "经营现金流/营收升幅(pp,封顶30)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：经营现金流/营收 {_f(p.get('ocfs_prev'), 2)} → {_f(p.get('ocfs_cur'), 2)}"
                      f"（升幅 {_f(p.get('ocfs_dq'), 2)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "ocsd_sue_vol":
            score, label, better = round(float(p["score"]), 3), "经营现金流/短期债务升幅(封顶0.40)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：经营现金流/短期债务 {_f(p.get('ocsd_prev'), 3)} → {_f(p.get('ocsd_cur'), 3)}"
                      f"（升幅 {_f(p.get('ocsd_dq'), 3)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "npm_sue_vol":
            score, label, better = round(float(p["score"]), 2), "净利率升幅(pp,封顶20)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：净利率 {_f(p.get('npm_prev'), 2)} → {_f(p.get('npm_cur'), 2)}"
                      f"（升幅 {_f(p.get('npm_dq'), 2)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "qs_sue_vol":
            score, label, better = round(float(p["score"]), 1), "单季营收同比加速(pp,封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：单季营收同比 {_f(p.get('prev_q_sales_yoy'), 1)}% → {_f(p.get('q_sales_yoy'), 1)}%"
                      f"（加速 {_f(p.get('qs_accel'), 1)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fcff_sue_vol":
            score, label, better = round(float(p["score"]), 2), "自由现金流每股升幅(元,封顶5)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：FCFF/股 {_f(p.get('fcff_prev'), 2)} → {_f(p.get('fcff_cur'), 2)}"
                      f"（升幅 {_f(p.get('fcff_dq'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "ocf_sue_vol":
            score, label, better = round(float(p["score"]), 1), "经营现金流同比(%,封顶300)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：经营现金流同比 {_f(p.get('prev_ocf_yoy'), 1)}% → {_f(p.get('ocf_yoy'), 1)}%"
                      f"（加速 {_f(p.get('ocf_accel'), 1)}pp，ocfps {_f(p.get('ocfps'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "cfps_sue_vol":
            score, label, better = round(float(p["score"]), 1), "每股现金流同比加速(pp,封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：cfps_yoy {_f(p.get('prev_cfps_yoy'), 1)}% → {_f(p.get('cfps_yoy'), 1)}%"
                      f"（加速 {_f(p.get('cfps_accel'), 1)}pp）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "udp_sue_vol":
            score, label, better = round(float(p["score"]), 2), "未分配利润同比增速(封顶2)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：未分配利润/股 {_f(p.get('udp_prev'), 2)} → {_f(p.get('udp_cur'), 2)}"
                      f"（增速 {_f(p.get('udp_g'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fcfe_sue_vol":
            score, label, better = round(float(p["score"]), 2), "股权自由现金流每股升幅(元,封顶5)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：FCFE/股 {_f(p.get('fcfe_prev'), 2)} → {_f(p.get('fcfe_cur'), 2)}"
                      f"（升幅 {_f(p.get('fcfe_dq'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "fat_sue_vol":
            score, label, better = round(float(p["score"]), 2), "固定资产周转升幅(封顶2)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：fa_turn {_f(p.get('fat_prev'), 2)} → {_f(p.get('fat_cur'), 2)}"
                      f"（升幅 {_f(p.get('fat_dq'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"业绩加速（扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%，上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%，"
                      f"营收同比 {_f(p.get('or_yoy'), 1)}%）；成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "qn_sue_vol":
            score, label, better = round(float(p["score"]), 2), "单季ROA扩张(封顶5)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：q_npta {_f(p.get('qn_prev'), 2)} → {_f(p.get('qn_cur'), 2)}"
                      f"（扩张 {_f(p.get('qn_dq'), 2)}）∩ SUE {_f(p.get('sue'), 2)}；"
                      f"放量 {_f(p.get('vol_ratio'), 2)}×")

        elif kind == "roy_sue_vol":
            score, label, better = round(float(p["score"]), 2), "ROE同比(封顶100)×SUEG合取", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：roe_yoy {_f(p.get('roe_yoy'), 1)}%"
                      f"∩ SUE {_f(p.get('sue'), 2)}；放量 {_f(p.get('vol_ratio'), 2)}×")

        elif kind == "ea_td_vol":
            score, label, better = round(float(p["score"]), 1), "营业周期缩短天数(封顶200)×EA合取", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：营业周期缩短 {_f(p.get('td_improve'), 1)} 日"
                      f"（§700 排序）∩ 扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%"
                      f"（上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%）、营收同比 {_f(p.get('or_yoy'), 1)}%；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "ea_cas_vol":
            score, label, better = round(float(p["score"]), 1), "扣非净利同比(%)×CAS合取", "higher"
            note = _RANK_CAP_NOTE
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')}：流动资产占比 {_f(p.get('cas_prev'), 2)}% → {_f(p.get('cas_cur'), 2)}%"
                      f"（升幅 {_f(p.get('cas_delta'), 2)}pp）∩ 扣非同比 {_f(p.get('dt_netprofit_yoy'), 1)}%"
                      f"（上期 {_f(p.get('prev_dt_netprofit_yoy'), 1)}%）、营收同比 {_f(p.get('or_yoy'), 1)}%；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "cas_vol":
            score, label, better = round(float(p["score"]), 2), "流动资产占比升幅(pp,封顶25)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告流动资产占比上升放量："
                      f"{_f(p.get('prev'), 2)}% → {_f(p.get('cur'), 2)}%，升幅 {_f(p.get('delta'), 2)}pp；"
                      f"成交额/20日均额 {_f(p.get('vol_ratio'), 2)}×")
        elif kind == "udp_yoy":
            score, label, better = round(float(p["score"]) * 100, 1), "每股未分配利润同比增速(%)，封顶200", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：每股未分配利润 {_f(p.get('prev'), 2)} → "
                      f"{_f(p.get('cur'), 2)} 元，同比 +{_f(float(p.get('growth') or 0) * 100, 1)}%")
        elif kind == "ncs_switch":
            score, label, better = round(float(p["score"]), 2), "净负债减少额÷去年有息负债(倍，封顶3)", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：净负债 {_f(float(p.get('netdebt_prev') or 0) / 1e8, 2)} 亿 → "
                      f"{_f(float(p.get('netdebt') or 0) / 1e8, 2)} 亿（转为净现金），去年同期有息负债 {_f(float(p.get('interestdebt_prev') or 0) / 1e8, 2)} 亿；"
                      f"扣非净利 {_f(float(p.get('profit_dedt') or 0) / 1e8, 2)} 亿")
        elif kind == "cg_streak":
            score, label, better = round(float(p["score"]), 3), "连续双增期数(封顶12)+扣非同比/1000", "higher"
            reason = (f"{p.get('ann_date', '')} 披露 {p.get('end_date', '')} 定期报告：营收同比 +{_f(p.get('or_yoy'), 1)}%、"
                      f"扣非净利同比 +{_f(p.get('dt_netprofit_yoy'), 1)}%，已连续 {p.get('streak', '')} 个报告期营收和扣非净利双增（各≥5%）")
        elif kind == "dividend_lowvol":
            score, label, better = float(p["score"]), "股息率排名+60日波动排名(越低越好)", "lower"
            reason = f"市值第 {p.get('mv_rank', '')} 名；股息率(TTM) {_f(p.get('dv_ttm'))}%、60日日波动 {_f(p.get('vol60'))}%，合计排名最低的10只之一"
    except (KeyError, TypeError, ValueError):
        score = label = better = reason = note = None
    p["rule_score"], p["rule_score_label"], p["rule_score_better"] = score, label, better
    p["rule_score_note"] = note
    p["selection_reason"] = reason
    if rank is not None:
        p["selection_rank"] = rank
    st = p.get("status")
    p.setdefault("cancel_code", None)
    p["cancel_reason"] = p.get("reason") if st == "skipped" else None
    p["ret_includes_cost"] = True if st == "closed" else (False if st == "holding" else None)
    return p


def _pick_files(strategy_id: str | None, horizon: str | None) -> list[Path]:
    """列出 picks 文件。指定 horizon 时只返回「现役且 horizon 匹配」的策略：已下线策略（RETIRED_STRATEGIES）
    和查不到的 id 都不进 /mid、/long 列表（历史文件保留不删，按 strategy_id 显式查询仍可读到）。"""
    if not OUT_DIR.exists():
        return []
    out = []
    for f in sorted(OUT_DIR.glob("*.json"), reverse=True):
        sid = f.stem.split("_", 1)[1] if "_" in f.stem else ""
        if strategy_id and sid != strategy_id:
            continue
        if horizon and STRATEGIES.get(sid, {}).get("horizon") != horizon:
            continue
        out.append(f)
    return out


def _horizon_of(strategy_id: str) -> str:
    """策略所属周期：现役/已下线表优先；都查不到按 id 前缀（long-* → long，其余 mid），不再一律当 mid。"""
    h = _strategy_meta(strategy_id).get("horizon")
    if h:
        return h
    return "long" if strategy_id.startswith("long-") else "mid"


# picks/*.json 原文缓存：按 (路径, mtime_ns, 大小) 校验，文件被重写会自动重读。只缓存原文字符串
# （每次 json.loads 出新对象，调用方随便改不会污染缓存）。条目上限 = 文件数量级。
_FILE_CACHE: OrderedDict[str, tuple[tuple[int, int], str, int]] = OrderedDict()
_FILE_CACHE_LOCK = threading.Lock()
_FILE_CACHE_MAX = 5000


def _read_pick_file(f: Path) -> tuple[str, int]:
    """返回 (原文, picks 只数)。"""
    st = f.stat()
    sig = (st.st_mtime_ns, st.st_size)
    k = str(f)
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(k)
        if hit is not None and hit[0] == sig:
            _FILE_CACHE.move_to_end(k)
            return hit[1], hit[2]
    raw = f.read_text()
    n = len(json.loads(raw).get("picks", []))
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[k] = (sig, raw, n)
        _FILE_CACHE.move_to_end(k)
        while len(_FILE_CACHE) > _FILE_CACHE_MAX:
            _FILE_CACHE.popitem(last=False)
    return raw, n


def _load_batch(db, f: Path, _prefetched: bool = False) -> dict[str, Any]:
    if not _prefetched:
        return _load_batches(db, [f])[0]
    data = json.loads(_read_pick_file(f)[0])
    sid = data["strategy_id"]
    for i, p in enumerate(data["picks"], 1):
        p.update(_realize(db, p["code"], data["signal_date"], _hold(sid)))
        annotate_pick(sid, p, i)
    closed = [p["ret"] for p in data["picks"] if p.get("status") in ("closed", "holding")]
    data["strategy_name"] = _strategy_meta(sid).get("name") or "已下线策略"
    data["horizon"] = _horizon_of(sid)
    data["hold_days"] = _hold(sid)
    data["avg_ret"] = round(sum(closed) / len(closed), 2) if closed else None
    data["status"] = ("closed" if all(p.get("status") in ("closed", "skipped") for p in data["picks"])
                      else "pending" if all(p.get("status") == "pending" for p in data["picks"]) else "holding")
    return data


def _load_batches(db, files: list[Path]) -> list[dict[str, Any]]:
    """批量版 _load_batch：先一次性校验数据版本并批量结算所有未缓存的入选股，再逐个组装，结果与逐个 _load_batch 相同。"""
    metas = []
    for f in files:
        d = json.loads(_read_pick_file(f)[0])
        metas.append((d["strategy_id"], d["signal_date"], [p["code"] for p in d["picks"]]))
    prefetch_realized(db, metas)
    return [_load_batch(db, f, _prefetched=True) for f in files]


def list_picks(db, strategy_id: str | None = None, limit_batches: int = 30,
               horizon: str | None = None) -> list[dict[str, Any]]:
    return _load_batches(db, _pick_files(strategy_id, horizon)[:limit_batches])


def list_picks_page(db, strategy_id: str | None = None, horizon: str | None = None,
                    page: int = 1, page_size: int = 30) -> dict[str, Any]:
    """批次分页：total=批次总数、total_picks=全部只数（不算收益，便宜）、has_more。"""
    files = _pick_files(strategy_id, horizon)
    start = (max(page, 1) - 1) * page_size
    total_picks = sum(_read_pick_file(f)[1] for f in files)
    batches = _load_batches(db, files[start:start + page_size])
    return {"batches": batches, "total": len(files), "total_picks": total_picks, "page": page,
            "page_size": page_size, "has_more": start + len(batches) < len(files)}


def pick_detail(db, horizon: str, strategy_id: str, signal_date: str, code: str,
                before: int = 20, after: int = 80) -> dict[str, Any] | None:
    """单股详情：该批次里这只票的全部字段 + 信号日前 before / 后 after 根日K + 同股其它中长线信号。"""
    meta = STRATEGIES.get(strategy_id)
    if meta is None or meta.get("horizon") != horizon:
        return None
    f = OUT_DIR / f"{signal_date}_{strategy_id}.json"
    if not f.exists():
        return None
    batch = _load_batch(db, f)
    pick = next((p for p in batch["picks"] if p["code"] == code), None)
    if pick is None:
        return None
    cols = "trade_date, open, high, low, close, pct_chg, volume, amount"
    pre = db.execute(text(f"SELECT {cols} FROM daily_kbars WHERE code=:c AND trade_date<:d AND close>0 "
                          "ORDER BY trade_date DESC LIMIT :n"), {"c": code, "d": signal_date, "n": before}).mappings().all()
    post = db.execute(text(f"SELECT {cols} FROM daily_kbars WHERE code=:c AND trade_date>=:d AND close>0 "
                           "ORDER BY trade_date LIMIT :n"), {"c": code, "d": signal_date, "n": after}).mappings().all()
    bars = [{k: (str(v)[:10] if k == "trade_date" else (float(v) if v is not None else None)) for k, v in r.items()}
            for r in [*reversed(pre), *post]]
    others = []
    for g in sorted(OUT_DIR.glob("*.json"), reverse=True):
        sd, _, sid = g.stem.partition("_")
        if (sd, sid) == (signal_date, strategy_id):
            continue
        if sid not in STRATEGIES:  # 已下线策略的历史批次不进「同股其它信号」（详情接口只认现役策略，链接会 404）
            continue
        if any(q.get("code") == code for q in json.loads(g.read_text()).get("picks", [])):
            others.append({"signal_date": sd, "strategy_id": sid,
                           "strategy_name": _strategy_meta(sid).get("name") or "已下线策略",
                           "horizon": _horizon_of(sid)})
    return {"horizon": horizon, "strategy_id": strategy_id, "strategy_name": meta.get("name"),
            "strategy_summary": meta.get("summary"), "hold_days": _hold(strategy_id),
            "signal_date": signal_date, "code": code, "name": pick.get("name"),
            "batch_size": len(batch["picks"]), "batch_status": batch["status"], "pick": pick,
            "bars": bars, "other_signals": others}


def _next_month_end_trade_date(db, today: _dt.date | None = None) -> str | None:
    """下一（含今日）月末交易日 YYYY-MM-DD；优先 trade_calendar SSE，日历不足时用工作日近似。"""
    today = today or _dt.date.today()
    key = today.strftime("%Y%m%d")
    rows: list[str] = []
    if db is not None:
        try:
            rows = [str(r).replace("-", "")[:8] for r in db.execute(text(
                "SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>=:d "
                "ORDER BY cal_date LIMIT 80"), {"d": key}).scalars().all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("next month-end trade_calendar lookup failed: %s", exc)
            rows = []
    for i, d in enumerate(rows):
        dd = _dt.datetime.strptime(d, "%Y%m%d").date()
        if i + 1 < len(rows):
            nxt_m = rows[i + 1][4:6]
        else:
            nd = dd + _dt.timedelta(days=3 if dd.weekday() == 4 else 1)
            nxt_m = f"{nd.month:02d}"
        if nxt_m != d[4:6]:
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    # 日历缺失：从 today 起找「下一工作日跨月」的工作日
    d = today
    for _ in range(62):
        if d.weekday() < 5:
            nd = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
            while nd.weekday() >= 5:
                nd += _dt.timedelta(days=1)
            if nd.month != d.month:
                return d.isoformat()
        d += _dt.timedelta(days=1)
    return None


def _strategy_has_batch(strategy_id: str) -> bool:
    if not OUT_DIR.exists():
        return False
    return any(OUT_DIR.glob(f"*_{strategy_id}.json"))


def next_batch_note(strategy_id: str, db=None, *, today: _dt.date | None = None) -> str:
    """策略下次出票说明（前端策略卡用）。无历史批次时前缀「尚未出票；」。"""
    meta = STRATEGIES.get(strategy_id) or {}
    kind = meta.get("kind")
    schedule = meta.get("schedule")
    if kind == "block_trade_premium" or strategy_id == "mid-block-trade-premium":
        base = "每晚约 21:00 大宗数据发布后选股，次日开盘买"
    elif schedule == "weekly" or strategy_id == "mid-small-cap-weekly":
        # is_signal_day：仅周五（weekday==4）收盘后出票
        base = "每个周五收盘后选股，次日开盘买"
    elif schedule == "month_end":
        nxt = _next_month_end_trade_date(db, today=today)
        if nxt:
            base = f"每月最后一个交易日收盘后选股，下一批：{nxt}，次日开盘买"
        else:
            base = "每月最后一个交易日收盘后选股，次日开盘买"
    else:
        # daily 及默认
        base = "每个交易日盘后（17:35）选股，次日开盘买"
    if not _strategy_has_batch(strategy_id):
        return f"尚未出票；{base}"
    return base


def strategies_overview(horizon: str | None = None, db=None) -> list[dict[str, Any]]:
    metrics = json.loads(METRICS_FILE.read_text()) if METRICS_FILE.exists() else {}
    own_db = False
    if db is None and any(m.get("schedule") == "month_end" for m in STRATEGIES.values()
                          if horizon is None or m.get("horizon") == horizon):
        try:
            from app.db.session import SessionLocal
            db = SessionLocal()
            own_db = True
        except Exception:  # noqa: BLE001
            db = None
    try:
        out = []
        for sid, meta in STRATEGIES.items():
            if horizon is not None and meta.get("horizon") != horizon:
                continue
            out.append({
                "id": sid,
                **{k: v for k, v in meta.items() if k not in ("top_n", "guard", "hold_days")},
                "hold_days": _hold(sid),
                "top_n": meta.get("top_n"),
                "metrics": metrics.get(sid, {}),
                "next_batch_note": next_batch_note(sid, db),
                "daily_status": latest_daily_status(sid),
            })
        return out
    finally:
        if own_db and db is not None:
            db.close()
