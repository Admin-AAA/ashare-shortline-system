"""
数据库模型 — 重构版 v2。

变更：
- 去掉所有 raw_* 原始数据表（不再双写）
- daily_bars + daily_indicators → daily_kbars（合并）
- unified_quotes JSON → stock_quotes 类型化列
- 新增 margin_data / hk_holdings / holder_numbers / stk_limits
- 辅助表字段对齐 Tushare 数据结构
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


# ═════════════════════════════════════════════════════════════════════════════
# 核心数据表
# ═════════════════════════════════════════════════════════════════════════════


class Stock(Base):
    """A股股票参考表。数据来源：Tushare stock_basic。"""
    __tablename__ = "stocks"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(80))
    area: Mapped[str | None] = mapped_column(String(32))
    market: Mapped[str | None] = mapped_column(String(16))
    list_date: Mapped[str | None] = mapped_column(String(10))
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyKbar(Base):
    """日K线 + 技术指标 + 筹码分布（合并表）。

    OHLCV 来源：Tushare daily
    换手率/PE/市值：Tushare daily_basic
    技术指标：Tushare stk_factor（MACD/KDJ/RSI/BOLL/CCI）
    筹码分布：Tushare cyq_perf
    自定义指标：本地计算（bias/vol_ratio/breakout/drawdown/holder_pct）
    """
    __tablename__ = "daily_kbars"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_daily_kbar_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)

    # OHLCV
    open: Mapped[float] = mapped_column(Float, default=0)
    high: Mapped[float] = mapped_column(Float, default=0)
    low: Mapped[float] = mapped_column(Float, default=0)
    close: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    pct_chg: Mapped[float] = mapped_column(Float, default=0)
    turnover_rate: Mapped[float] = mapped_column(Float, default=0)

    # 均线（本地计算或 stk_factor_pro）
    ma5: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma10: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma20: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma60: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma250: Mapped[float | None] = mapped_column(Float, nullable=True)

    # MACD
    macd_dif: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_dea: Mapped[float | None] = mapped_column(Float, nullable=True)
    macd_bar: Mapped[float | None] = mapped_column(Float, nullable=True)

    # KDJ
    kdj_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    kdj_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    kdj_j: Mapped[float | None] = mapped_column(Float, nullable=True)

    # RSI
    rsi_6: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_12: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_24: Mapped[float | None] = mapped_column(Float, nullable=True)

    # BOLL
    boll_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    boll_lower: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CCI
    cci: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 扩展因子（from stk_factor_pro）
    adj_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    asi: Mapped[float | None] = mapped_column(Float, nullable=True)
    asit: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbi: Mapped[float | None] = mapped_column(Float, nullable=True)
    brar_ar: Mapped[float | None] = mapped_column(Float, nullable=True)
    brar_br: Mapped[float | None] = mapped_column(Float, nullable=True)
    dpo: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_12: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_50: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma_volume_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma_volume_10: Mapped[float | None] = mapped_column(Float, nullable=True)
    mtm: Mapped[float | None] = mapped_column(Float, nullable=True)
    obv: Mapped[float | None] = mapped_column(Float, nullable=True)
    psy: Mapped[float | None] = mapped_column(Float, nullable=True)
    trix: Mapped[float | None] = mapped_column(Float, nullable=True)
    vr: Mapped[float | None] = mapped_column(Float, nullable=True)
    wr_6: Mapped[float | None] = mapped_column(Float, nullable=True)
    wr_10: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 乖离率（本地计算）
    bias_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_10: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_20: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 量比（本地计算）
    vol_ratio_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol_ratio_20: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 突破信号
    breakout20: Mapped[int] = mapped_column(Integer, default=0)
    breakout60: Mapped[int] = mapped_column(Integer, default=0)

    # 最大回撤
    max_dd20: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dd60: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 筹码分布（from cyq_perf）
    chip_cost_15: Mapped[float | None] = mapped_column(Float, nullable=True)
    chip_cost_50: Mapped[float | None] = mapped_column(Float, nullable=True)
    chip_cost_85: Mapped[float | None] = mapped_column(Float, nullable=True)
    chip_weight_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    chip_winner_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 自定义特征
    upper_shadow_count20: Mapped[int] = mapped_column(Integer, default=0)
    holder_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 来源标记
    source: Mapped[str] = mapped_column(String(16), default="tushare")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockQuote(Base):
    """盘中行情快照（替代 unified_quotes JSON blob）。

    盘中数据来源：eastmoney/tencent（Tushare 不提供实时行情）。
    每个 code 每天一条记录，最新数据覆盖旧数据。
    """
    __tablename__ = "stock_quotes"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_stock_quote_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(80))
    price: Mapped[float] = mapped_column(Float, default=0)
    pct: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount_yi: Mapped[float] = mapped_column(Float, default=0)
    turnover_rate: Mapped[float] = mapped_column(Float, default=0)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_mv_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_mv_yi: Mapped[float | None] = mapped_column(Float, nullable=True)
    chg60: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    preclose: Mapped[float | None] = mapped_column(Float, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(16))
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    freshness_status: Mapped[str] = mapped_column(String(16), default="fresh")
    snapped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# ═════════════════════════════════════════════════════════════════════════════
# 辅助数据表
# ═════════════════════════════════════════════════════════════════════════════


class LimitUpBoard(Base):
    """涨停板榜单。数据来源：Tushare limit_list_d。"""
    __tablename__ = "limit_up_board"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_limit_up_board_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    pct_chg: Mapped[float] = mapped_column(Float, default=0)
    limit_type: Mapped[str] = mapped_column(String(2), default="U")  # U=涨停 D=跌停
    limit_times: Mapped[int] = mapped_column(Integer, default=1)
    first_time: Mapped[str | None] = mapped_column(String(8))
    last_time: Mapped[str | None] = mapped_column(String(8))
    open_times: Mapped[int] = mapped_column(Integer, default=0)
    up_stat: Mapped[str | None] = mapped_column(String(8))
    limit_amount_yi: Mapped[float | None] = mapped_column(Float)
    turnover_ratio: Mapped[float | None] = mapped_column(Float)
    fd_amount_yi: Mapped[float | None] = mapped_column(Float)
    industry: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConceptSector(Base):
    """概念板块列表。数据来源：Tushare ths_index。"""
    __tablename__ = "concept_sector"
    __table_args__ = (UniqueConstraint("sector_code", "trade_date", name="uq_concept_sector_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    sector_name: Mapped[str] = mapped_column(String(80))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    exchange: Mapped[str | None] = mapped_column(String(8))
    list_date: Mapped[str | None] = mapped_column(String(10))
    type: Mapped[str | None] = mapped_column(String(16))
    heat_rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConceptSectorStock(Base):
    """概念板块成分股。数据来源：Tushare ths_member。"""
    __tablename__ = "concept_sector_stock"
    __table_args__ = (UniqueConstraint("sector_code", "code", "trade_date", name="uq_concept_sector_stock_sc_c_d"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CapitalFlow(Base):
    """个股资金流向。数据来源：Tushare moneyflow。"""
    __tablename__ = "capital_flow"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_capital_flow_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    main_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    super_large_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    large_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    medium_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    small_net_inflow_yi: Mapped[float | None] = mapped_column(Float)
    main_net_inflow_ratio: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_3d_yi: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_5d_yi: Mapped[float | None] = mapped_column(Float)
    cumulative_inflow_10d_yi: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DragonTiger(Base):
    """龙虎榜。数据来源：Tushare top_list。"""
    __tablename__ = "dragon_tiger"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_dragon_tiger_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    buy_amount_yi: Mapped[float | None] = mapped_column(Float)
    sell_amount_yi: Mapped[float | None] = mapped_column(Float)
    net_amount_yi: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsAnnouncement(Base):
    """新闻公告。数据来源：eastmoney np-anotice API。"""
    __tablename__ = "news_announcement"
    __table_args__ = (UniqueConstraint("source", "article_id", name="uq_news_announcement_src_aid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(40))
    article_id: Mapped[str] = mapped_column(String(80), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    announce_date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str | None] = mapped_column(String(20))
    is_risk_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MinuteKbar(Base):
    """分钟K线（1min/5min/15min/30min/60min）。数据来源：eastmoney push2his API。"""
    __tablename__ = "minute_kbars"
    __table_args__ = (UniqueConstraint("code", "freq", "trade_time", name="uq_minute_kbar_code_freq_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    freq: Mapped[str] = mapped_column(String(5), default="1min")
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    trade_time: Mapped[str] = mapped_column(String(19))  # YYYY-MM-DD HH:MM:SS
    open: Mapped[float] = mapped_column(Float, default=0)
    high: Mapped[float] = mapped_column(Float, default=0)
    low: Mapped[float] = mapped_column(Float, default=0)
    close: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═════════════════════════════════════════════════════════════════════════════
# 新增数据表（Tushare 特有数据）
# ═════════════════════════════════════════════════════════════════════════════


class IndexDaily(Base):
    """指数日线行情。数据来源：Tushare index_daily。

    用于回测基准对比（沪深300 / 中证500 等）。
    """
    __tablename__ = "index_daily"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date", name="uq_index_daily_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(20), index=True)  # e.g. "000300.SH"
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    close: Mapped[float] = mapped_column(Float, default=0)
    pct_chg: Mapped[float] = mapped_column(Float, default=0)      # 涨跌幅(%)
    open: Mapped[float] = mapped_column(Float, default=0)
    high: Mapped[float] = mapped_column(Float, default=0)
    low: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarginData(Base):
    """融资融券数据。数据来源：Tushare margin。"""
    __tablename__ = "margin_data"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_margin_data_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    rzye: Mapped[float | None] = mapped_column(Float)   # 融资余额(亿)
    rqye: Mapped[float | None] = mapped_column(Float)   # 融券余额(亿)
    rzmre: Mapped[float | None] = mapped_column(Float)  # 融资买入额(亿)
    rzche: Mapped[float | None] = mapped_column(Float)  # 融资偿还额(亿)
    rzrqye: Mapped[float | None] = mapped_column(Float) # 融资融券余额(亿)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarginDetail(Base):
    """融资融券明细（全市场批量）。数据来源：Tushare margin_detail。"""
    __tablename__ = "margin_detail"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_margin_detail_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    rzye: Mapped[float | None] = mapped_column(Float)        # 融资余额(亿)
    rqye: Mapped[float | None] = mapped_column(Float)        # 融券余额(亿)
    rzmre: Mapped[float | None] = mapped_column(Float)       # 融资买入额(亿)
    rzche: Mapped[float | None] = mapped_column(Float)       # 融资偿还额(亿)
    rzrqye: Mapped[float | None] = mapped_column(Float)      # 融资融券余额(亿)
    rqyl: Mapped[float | None] = mapped_column(Float)        # 融券余量(股)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BlockTrade(Base):
    """大宗交易。数据来源：Tushare block_trade。"""
    __tablename__ = "block_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    price: Mapped[float | None] = mapped_column(Float)       # 成交价
    vol: Mapped[float | None] = mapped_column(Float)          # 成交量(万股)
    amount_yi: Mapped[float | None] = mapped_column(Float)    # 成交额(亿)
    buyer: Mapped[str | None] = mapped_column(String(200))    # 买方营业部
    seller: Mapped[str | None] = mapped_column(String(200))   # 卖方营业部
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HsgtFlow(Base):
    """沪深港通资金流向（全市场汇总）。数据来源：Tushare moneyflow_hsgt。"""
    __tablename__ = "hsgt_flows"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_hsgt_flows_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    north_money: Mapped[float | None] = mapped_column(Float)  # 北向资金(亿)
    south_money: Mapped[float | None] = mapped_column(Float)  # 南向资金(亿)
    hgt: Mapped[float | None] = mapped_column(Float)          # 沪股通(万元)
    sgt: Mapped[float | None] = mapped_column(Float)          # 深股通(万元)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HsgtTop10(Base):
    """北向持股TOP10。数据来源：Tushare hsgt_top10。"""
    __tablename__ = "hsgt_top10"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_hsgt_top10_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str | None] = mapped_column(String(20))
    close: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float)       # 持股数量(股)
    net_amount: Mapped[float | None] = mapped_column(Float)   # 持股变化(股)
    buy: Mapped[float | None] = mapped_column(Float)          # 买入(股)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HkHolding(Base):
    """港股通持股。数据来源：Tushare hk_hold。"""
    __tablename__ = "hk_holdings"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_hk_holdings_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    vol: Mapped[float | None] = mapped_column(Float)   # 持股数量(股)
    ratio: Mapped[float | None] = mapped_column(Float)  # 持股比例(%)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HolderNumber(Base):
    """股东人数（筹码集中度）。数据来源：Tushare stk_holdernumber。"""
    __tablename__ = "holder_numbers"
    __table_args__ = (UniqueConstraint("code", "end_date", name="uq_holder_numbers_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    end_date: Mapped[str] = mapped_column(String(10), index=True)
    holder_num: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FinaIndicator(Base):
    """财务指标（季报）。数据来源：Tushare fina_indicator。

    字段名对齐 fina_indicator 原始字段（grossprofit_margin 无下划线）。
    用于替代 feature_builder 中的常数默认值，提供真实 ROE/毛利率/同比等。
    """
    __tablename__ = "stock_fina_indicator"
    __table_args__ = (UniqueConstraint("code", "end_date", name="uq_fina_code_end_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    end_date: Mapped[str] = mapped_column(String(8), index=True)       # 财报期末 YYYYMMDD
    ann_date: Mapped[str | None] = mapped_column(String(8), index=True)  # 公告日
    roe: Mapped[float | None] = mapped_column(Float)                    # 净资产收益率 %
    grossprofit_margin: Mapped[float | None] = mapped_column(Float)     # 销售毛利率 %
    netprofit_margin: Mapped[float | None] = mapped_column(Float)       # 销售净利率 %
    netprofit_yoy: Mapped[float | None] = mapped_column(Float)          # 归母净利润同比 %
    or_yoy: Mapped[float | None] = mapped_column(Float)                # 营业收入同比 %
    eps: Mapped[float | None] = mapped_column(Float)
    bps: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StkLimit(Base):
    """每日涨跌停价格。数据来源：Tushare stk_limit。"""
    __tablename__ = "stk_limits"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_stk_limits_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    up_limit: Mapped[float | None] = mapped_column(Float)
    down_limit: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuctionData(Base):
    """集合竞价数据。数据来源：Tushare stk_auction。"""
    __tablename__ = "auction_data"
    __table_args__ = (UniqueConstraint("code", "trade_date", name="uq_auction_data_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    vol: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    pre_close: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    float_share: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BrokerRecommend(Base):
    """券商金股。数据来源：Tushare broker_recommend。"""
    __tablename__ = "broker_recommends"
    __table_args__ = (UniqueConstraint("code", "month", "broker", name="uq_broker_rec_code_month_broker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    month: Mapped[str] = mapped_column(String(6), index=True)
    broker: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HolderTrade(Base):
    """股东增减持。数据来源：Tushare stk_holdertrade。"""
    __tablename__ = "holder_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    ann_date: Mapped[str] = mapped_column(String(10), index=True)
    holder_name: Mapped[str | None] = mapped_column(String(120))
    holder_type: Mapped[str | None] = mapped_column(String(40))
    in_de: Mapped[str | None] = mapped_column(String(10))  # IN=增持, DE=减持
    change_vol: Mapped[float | None] = mapped_column(Float)
    change_ratio: Mapped[float | None] = mapped_column(Float)
    after_ratio: Mapped[float | None] = mapped_column(Float)
    avg_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InstitutionalSurvey(Base):
    """机构调研。数据来源：Tushare stk_surv。"""
    __tablename__ = "institutional_surveys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    surv_date: Mapped[str] = mapped_column(String(10), index=True)
    fund_visitors: Mapped[int | None] = mapped_column(Integer)
    rece_org: Mapped[str | None] = mapped_column(String(120))
    org_type: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ShareFloat(Base):
    """限售解禁。数据来源：Tushare share_float。"""
    __tablename__ = "share_floats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    ann_date: Mapped[str | None] = mapped_column(String(10))
    float_date: Mapped[str] = mapped_column(String(10), index=True)
    float_share: Mapped[float | None] = mapped_column(Float)
    float_ratio: Mapped[float | None] = mapped_column(Float)
    holder_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PledgeStat(Base):
    """股权质押统计。数据来源：Tushare pledge_stat。"""
    __tablename__ = "pledge_stats"
    __table_args__ = (UniqueConstraint("code", "end_date", name="uq_pledge_stats_code_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    end_date: Mapped[str] = mapped_column(String(10), index=True)
    pledge_count: Mapped[int | None] = mapped_column(Integer)
    unrest_pledge: Mapped[float | None] = mapped_column(Float)
    rest_pledge: Mapped[float | None] = mapped_column(Float)
    pledge_ratio: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Repurchase(Base):
    """股票回购。数据来源：Tushare repurchase。"""
    __tablename__ = "repurchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    ann_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    vol: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    high_limit: Mapped[float | None] = mapped_column(Float)
    low_limit: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═════════════════════════════════════════════════════════════════════════════
# 数据源管理（简化）
# ═════════════════════════════════════════════════════════════════════════════


class DataSourceHealth(Base):
    __tablename__ = "data_source_health"

    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    empty_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DataSourceConfig(Base):
    __tablename__ = "data_source_config"

    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═════════════════════════════════════════════════════════════════════════════
# 运营数据表（保持不变）
# ═════════════════════════════════════════════════════════════════════════════


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    up_count: Mapped[int] = mapped_column(Integer, default=0)
    down_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_up_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_down_count: Mapped[int] = mapped_column(Integer, default=0)
    main_industries_json: Mapped[str] = mapped_column(Text, default="[]")
    breadth_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillRegistry(Base):
    __tablename__ = "skill_registry"

    skill_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(30), default="local")
    source_url: Mapped[str | None] = mapped_column(String(500))
    installed_path: Mapped[str] = mapped_column(String(500))
    current_version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    mode: Mapped[str] = mapped_column(String(20), default="formal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    has_executable_contract: Mapped[bool] = mapped_column(Boolean, default=False)
    compatibility_json: Mapped[str] = mapped_column(Text, default="{}")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    installed_path: Mapped[str] = mapped_column(String(500))
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    contract_hash: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    version_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    skill_repo: Mapped[str | None] = mapped_column(String(500))
    skill_ref: Mapped[str | None] = mapped_column(String(200))
    skill_path: Mapped[str] = mapped_column(String(500))
    yaml_path: Mapped[str] = mapped_column(String(500))
    yaml_hash: Mapped[str] = mapped_column(String(80))
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Preset(Base):
    __tablename__ = "presets"

    preset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    score_line: Mapped[float] = mapped_column(Float, default=72.0)
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    bucket_limits_json: Mapped[str] = mapped_column(Text, default="{}")
    data_source: Mapped[str] = mapped_column(String(20), default="local_pool_preferred")
    hide_recent_repeats: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PresetSkill(Base):
    __tablename__ = "preset_skills"
    __table_args__ = (UniqueConstraint("preset_id", "skill_id", name="uq_preset_skill"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    preset_id: Mapped[str] = mapped_column(String(80), index=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    run_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    preset_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_name: Mapped[str] = mapped_column(String(120), default="")
    run_mode: Mapped[str] = mapped_column(String(20), default="formal")
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    universe_count: Mapped[int] = mapped_column(Integer, default=0)
    recommended_count: Mapped[int] = mapped_column(Integer, default=0)
    market_scope: Mapped[str] = mapped_column(String(30), default="all")
    market_scope_value: Mapped[str | None] = mapped_column(String(120))
    risk_template: Mapped[str] = mapped_column(String(20), default="balanced")
    score_line: Mapped[float] = mapped_column(Float, default=72.0)
    output_count: Mapped[int] = mapped_column(Integer, default=6)
    selected_skills_json: Mapped[str] = mapped_column(Text, default="[]")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    market_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    result_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    filter_audit_json: Mapped[str] = mapped_column(LONGTEXT, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="running")
    error_message: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress_phase: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkpoint_phase: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(60), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    recommended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(80))
    recommend_price: Mapped[float] = mapped_column(Float, default=0)
    bucket: Mapped[str] = mapped_column(String(64), default="")
    final_score: Mapped[float] = mapped_column(Float)
    consensus_score: Mapped[float] = mapped_column(Float, default=0)
    setup_type: Mapped[str | None] = mapped_column(String(80))
    entry_rule: Mapped[str | None] = mapped_column(Text)
    invalid_rule: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    risk_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    info_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    recent_repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    feature_snapshot_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"), default="{}")
    market_context_json: Mapped[str] = mapped_column(Text, default="{}")
    skill_breakdown_json: Mapped[str] = mapped_column(Text, default="[]")
    agent_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="pending")


class RecommendationOutcome(Base):
    __tablename__ = "recommendation_outcomes"

    recommendation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    entry_triggered: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_date: Mapped[str | None] = mapped_column(String(10))
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_date: Mapped[str | None] = mapped_column(String(10))
    exit_price: Mapped[float | None] = mapped_column(Float)
    executed_return: Mapped[float] = mapped_column(Float, default=0)
    weighted_return: Mapped[float] = mapped_column(Float, default=0)
    position_size: Mapped[float] = mapped_column(Float, default=1.0)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    entry_slippage_bps: Mapped[float] = mapped_column(Float, default=0)
    exit_slippage_bps: Mapped[float] = mapped_column(Float, default=0)
    stop_loss_price: Mapped[float | None] = mapped_column(Float)
    take_profit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    max_return: Mapped[float] = mapped_column(Float, default=0)
    min_return: Mapped[float] = mapped_column(Float, default=0)
    close_return: Mapped[float] = mapped_column(Float, default=0)
    achievable_return: Mapped[float] = mapped_column(Float, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0)
    hit_target: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_label: Mapped[str] = mapped_column(String(30), default="pending")
    outcome_label_legacy: Mapped[str] = mapped_column(String(30), default="pending")
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    label_source: Mapped[str] = mapped_column(String(20), default="auto")
    labeled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReviewNote(Base):
    __tablename__ = "review_notes"
    __table_args__ = (UniqueConstraint("recommendation_id", name="uq_review_note_recommendation"),)

    note_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    user_label: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    review_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("source_recommendation_id", name="uq_watch_recommendation"),)

    watch_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    source_run_id: Mapped[str] = mapped_column(String(60), index=True)
    source_recommendation_id: Mapped[str] = mapped_column(String(80), index=True)
    watch_status: Mapped[str] = mapped_column(String(20), default="watching")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentDecisionLog(Base):
    __tablename__ = "agent_decision_logs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    agent_name: Mapped[str] = mapped_column(String(80))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    backtest_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    date_from: Mapped[str] = mapped_column(String(10))
    date_to: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    result_json: Mapped[str] = mapped_column(MEDIUMTEXT, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CalibrationConfig(Base):
    __tablename__ = "calibration_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelRouteConfig(Base):
    __tablename__ = "model_route_config"

    route_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(120))
    api_key: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=1, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiCallLog(Base):
    """LLM API 调用日志。"""
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(60), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(200))
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, default=0)
    # 真实 token 用量（来自 provider usage 字段，None 表示本次调用未取得真实值）
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="success")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMReviewCache(Base):
    """LLM Skill 审查结果缓存。

    key = sha256(skill_hash + stock_feature_json + snapshot_json + rule_signal_json)
    相同输入直接复用历史 LLM 输出，不再调用 LLM。skill 文件改动 / 市场状态变化 /
    规则信号变化 → cache_key 自动失效。跨 run 持久化，日内重跑零 LLM 调用。
    """
    __tablename__ = "llm_review_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(80), index=True)
    skill_hash: Mapped[str | None] = mapped_column(String(80))
    trade_date: Mapped[str | None] = mapped_column(String(10), index=True)
    code: Mapped[str | None] = mapped_column(String(6), index=True)
    review_json: Mapped[str] = mapped_column(LONGTEXT().with_variant(Text(), "sqlite"))
    model: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(40))
    cron_expression: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduledTaskRun(Base):
    __tablename__ = "scheduled_task_runs"

    task_run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    task_name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(40))
    trigger_source: Mapped[str] = mapped_column(String(20), default="schedule")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="running")
    summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(MEDIUMTEXT, default="{}")
    result_json: Mapped[str] = mapped_column(MEDIUMTEXT, default="{}")


class StockMatrixHistory(Base):
    """个股矩阵分析历史记录。每次分析/回测结果落表，支持分页查询历史。"""
    __tablename__ = "stock_matrix_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    trade_date: Mapped[str] = mapped_column(String(10))
    analysis_type: Mapped[str] = mapped_column(String(20), default="analysis")  # analysis | backtest
    final_score: Mapped[float | None] = mapped_column(Float)
    win_rate_est: Mapped[float | None] = mapped_column(Float)
    avg_return_est: Mapped[float | None] = mapped_column(Float)
    skill_count: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str] = mapped_column(MEDIUMTEXT, default="{}")
    elapsed_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
