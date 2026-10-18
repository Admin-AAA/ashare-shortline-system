"""
辅助数据获取（Tushare-only）。
涨停板、概念板块、资金流向、龙虎榜均通过自建 Tushare 服务器获取。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from hashlib import sha1
from typing import Any

from app.core.config import get_settings
from app.engine.trading_calendar import normalize_trade_date as _normalize_trade_date
from app.services.market_data import _split_sources

logger = logging.getLogger(__name__)


def _limit_up_sources(settings: Any) -> list[str]:
    return _split_sources(settings.limit_up_sources)


def _concept_sector_sources(settings: Any) -> list[str]:
    return _split_sources(settings.concept_sector_sources)


def _capital_flow_sources(settings: Any) -> list[str]:
    return _split_sources(settings.capital_flow_sources)


def _dragon_tiger_sources(settings: Any) -> list[str]:
    return _split_sources(settings.dragon_tiger_sources)


def _news_sources(settings: Any) -> list[str]:
    return _split_sources(settings.news_sources)


def _em():
    from app.services.datasource import eastmoney

    return eastmoney


def _em_td(trade_date: str | None) -> str:
    return _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")


# ── Dispatch ────────────────────────────────────────────────────────────

def _compact_trade_date(value: Any) -> str:
    return _normalize_trade_date(value).replace("-", "")


def _fetch_limit_up_board(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        rows = _fetch_tushare_limit_up_board(settings, trade_date=trade_date)
        if rows:
            return rows
        # Tushare 限速/失败时回退本地 limit_up_board 表（已同步的涨停事实数据）
        local_rows = _fetch_local_limit_up_board(trade_date)
        if local_rows:
            logger.info("涨停板使用本地表 fallback: %d 行 (trade_date=%s)", len(local_rows), trade_date)
        return local_rows
    if source == "eastmoney" and _em().aux_enabled():
        return _em().limit_up(_em_td(trade_date))
    return []


def _fetch_local_limit_up_board(trade_date: str | None = None) -> list[dict[str, Any]]:
    """从本地 limit_up_board 表读涨停板（Tushare 限速时的 fallback）。

    涨停板是已发生的事实，本地表有持久化数据；运行时不应因 Tushare 429 让
    limit-up-reseal-relay 等策略饿死。字段对齐 fetch_limit_up_board 的返回结构。
    """
    try:
        from app.db.session import SessionLocal
        from app.models.tables import LimitUpBoard
        from app.services.tushare_client import _fmt_date

        td = _compact_trade_date(trade_date)
        if not td:
            return []
        db = SessionLocal()
        try:
            rows = (
                db.query(LimitUpBoard)
                .filter(LimitUpBoard.trade_date == _fmt_date(td))
                .all()
            )
            result: list[dict[str, Any]] = []
            for row in rows:
                result.append({
                    "code": row.code,
                    "name": row.name or "",
                    "trade_date": row.trade_date,
                    "pct_chg": float(row.pct_chg) if row.pct_chg is not None else 0.0,
                    "limit_times": int(row.limit_times or 0),
                    "limit_type": row.limit_type or "",
                    "first_time": row.first_time or "",
                    "last_time": row.last_time or "",
                    "open_times": int(row.open_times or 0),
                    "up_stat": row.up_stat or "",
                    "limit_amount_yi": float(row.limit_amount_yi) if row.limit_amount_yi is not None else 0.0,
                    "turnover_ratio": float(row.turnover_ratio) if row.turnover_ratio is not None else 0.0,
                    "fd_amount_yi": float(row.fd_amount_yi) if row.fd_amount_yi is not None else 0.0,
                    "industry": row.industry or "",
                })
            return result
        finally:
            db.close()
    except Exception as e:
        logger.warning("本地涨停板表不可用: %s", e)
        return []


def _fetch_concept_sectors(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_concept_sectors(settings, trade_date=trade_date)
    if source == "eastmoney" and _em().aux_enabled():
        return _em().concept_sectors()
    return []


def _fetch_capital_flow(source: str, code: str, settings: Any, trade_date: str | None = None) -> dict[str, Any] | None:
    if source == "tushare":
        return _fetch_tushare_moneyflow(code, settings, trade_date=trade_date)
    if source == "eastmoney" and _em().aux_enabled():
        return _em().capital_flow(code, _em_td(trade_date) if trade_date else None)
    return None


def _fetch_dragon_tiger(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_dragon_tiger(settings, trade_date=trade_date)
    if source == "eastmoney" and _em().aux_enabled():
        return _em().dragon_tiger(_em_td(trade_date))
    return []


def _fetch_news(source: str, code: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_news(code, settings, trade_date=trade_date)
    if source == "eastmoney":
        return _fetch_eastmoney_news(code, settings)
    return []


# ── Tushare 实现 ────────────────────────────────────────────────────────

def _fetch_tushare_moneyflow(code: str, settings: Any, trade_date: str | None = None) -> dict[str, Any] | None:
    try:
        from app.services.tushare_client import get_pro

        pro = get_pro()
        ts_code = f"{code}.{'SH' if code.startswith(('6', '9')) else 'SZ'}"
        target = _compact_trade_date(trade_date)
        if target:
            end_day = date.fromisoformat(_normalize_trade_date(target))
            start = (end_day - timedelta(days=30)).strftime("%Y%m%d")
            try:
                df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=target)
            except Exception:
                df = pro.moneyflow(ts_code=ts_code, trade_date=target)
        else:
            df = pro.moneyflow(ts_code=ts_code, limit=10)
        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date", ascending=False)
        if target:
            df = df[df["trade_date"].astype(str) <= target]
            if df.empty or str(df.iloc[0].get("trade_date", "")) != target:
                return None
        latest = df.iloc[0]
        row_trade_date = _normalize_trade_date(latest.get("trade_date", target))

        cum_3d = 0.0
        cum_5d = 0.0
        cum_10d = 0.0
        for i in range(min(len(df), 10)):
            row = df.iloc[i]
            net_mf = float(row.get("net_mf_amount") or 0)
            if i < 3:
                cum_3d += net_mf
            if i < 5:
                cum_5d += net_mf
            cum_10d += net_mf

        return {
            "code": code,
            "trade_date": row_trade_date,
            "main_net_inflow_yi": round(float(latest.get("net_mf_amount") or 0) / 1e8, 4),
            "super_large_net_inflow_yi": round(
                float(latest.get("buy_elg_amount") or 0) / 1e8
                - float(latest.get("sell_elg_amount") or 0) / 1e8,
                4,
            ),
            "large_net_inflow_yi": round(
                float(latest.get("buy_lg_amount") or 0) / 1e8
                - float(latest.get("sell_lg_amount") or 0) / 1e8,
                4,
            ),
            "medium_net_inflow_yi": round(
                float(latest.get("buy_md_amount") or 0) / 1e8
                - float(latest.get("sell_md_amount") or 0) / 1e8,
                4,
            ),
            "small_net_inflow_yi": round(
                float(latest.get("buy_sm_amount") or 0) / 1e8
                - float(latest.get("sell_sm_amount") or 0) / 1e8,
                4,
            ),
            "main_net_inflow_ratio": round(float(latest.get("net_mf_vol") or 0), 2),
            "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
            "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
            "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
        }
    except Exception as e:
        logger.debug("Tushare资金流向不可用 (%s): %s", code, e)
        return None


def _fetch_tushare_moneyflow_batch(
    codes: list[str], settings: Any, trade_date: str | None = None
) -> dict[str, dict[str, Any]]:
    """批量获取资金流向：按日期拉取全市场，过滤目标股票。

    比逐股票调用快 ~12x（120 只股票：120 次 API → ~10 次批量 API）。
    每个日期一次 API 调用获取全市场数据，然后过滤目标 codes。
    累积流入需要 10 天窗口数据。
    """
    try:
        import pandas as pd
        from app.services.tushare_client import get_pro

        pro = get_pro()
        target = _compact_trade_date(trade_date)
        if not target:
            return {}
        end_day = date.fromisoformat(_normalize_trade_date(target))
        code_set = set(codes)
        ts_code_map = {
            code: f"{code}.{'SH' if code.startswith(('6', '9')) else 'SZ'}"
            for code in codes
        }
        ts_code_set = set(ts_code_map.values())

        # 拉取 10 天窗口的数据（用于计算累积流入）
        day_frames: list[pd.DataFrame] = []
        for offset in range(10):
            d = (end_day - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = pro.moneyflow(trade_date=d)
                if df is not None and not df.empty:
                    df = df[df["ts_code"].isin(ts_code_set)]
                    if not df.empty:
                        day_frames.append(df)
            except Exception:
                continue

        if not day_frames:
            return {}

        combined = pd.concat(day_frames, ignore_index=True)
        combined["trade_date"] = combined["trade_date"].astype(str)

        result: dict[str, dict[str, Any]] = {}
        for code in codes:
            ts_code = ts_code_map[code]
            stock_df = combined[combined["ts_code"] == ts_code].sort_values(
                "trade_date", ascending=False
            )
            if stock_df.empty:
                continue
            latest = stock_df.iloc[0]
            row_trade_date = _normalize_trade_date(latest.get("trade_date", target))
            if row_trade_date != _normalize_trade_date(target):
                continue

            cum_3d = 0.0
            cum_5d = 0.0
            cum_10d = 0.0
            for i in range(min(len(stock_df), 10)):
                row = stock_df.iloc[i]
                net_mf = float(row.get("net_mf_amount") or 0)
                if i < 3:
                    cum_3d += net_mf
                if i < 5:
                    cum_5d += net_mf
                cum_10d += net_mf

            result[code] = {
                "code": code,
                "trade_date": row_trade_date,
                "main_net_inflow_yi": round(float(latest.get("net_mf_amount") or 0) / 1e8, 4),
                "super_large_net_inflow_yi": round(
                    float(latest.get("buy_elg_amount") or 0) / 1e8
                    - float(latest.get("sell_elg_amount") or 0) / 1e8,
                    4,
                ),
                "large_net_inflow_yi": round(
                    float(latest.get("buy_lg_amount") or 0) / 1e8
                    - float(latest.get("sell_lg_amount") or 0) / 1e8,
                    4,
                ),
                "medium_net_inflow_yi": round(
                    float(latest.get("buy_md_amount") or 0) / 1e8
                    - float(latest.get("sell_md_amount") or 0) / 1e8,
                    4,
                ),
                "small_net_inflow_yi": round(
                    float(latest.get("buy_sm_amount") or 0) / 1e8
                    - float(latest.get("sell_sm_amount") or 0) / 1e8,
                    4,
                ),
                "main_net_inflow_ratio": round(float(latest.get("net_mf_vol") or 0), 2),
                "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
                "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
                "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
            }
        return result
    except Exception as e:
        logger.debug("Tushare批量资金流向不可用: %s", e)
        return {}


def _fetch_tushare_dragon_tiger(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_dragon_tiger

        return fetch_dragon_tiger(_compact_trade_date(trade_date) or None)
    except Exception as e:
        logger.warning("Tushare龙虎榜不可用: %s", e)
        return []


def _fetch_tushare_limit_up_board(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_limit_up_board

        rows = fetch_limit_up_board(_compact_trade_date(trade_date) or None)
        for row in rows:
            row.setdefault("fd_amount_yi", float(row.pop("fd_amount", 0) or 0))
        return rows
    except Exception as e:
        logger.warning("Tushare涨停板不可用: %s", e)
        return []


def _fetch_tushare_concept_sectors(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_concept_sectors, fetch_latest_trade_date

        rows = fetch_concept_sectors()
        target_trade_date = _normalize_trade_date(trade_date) or _normalize_trade_date(fetch_latest_trade_date())
        for rank, row in enumerate(rows, 1):
            row["trade_date"] = target_trade_date
            row["heat_rank"] = rank
        return rows
    except Exception as e:
        logger.warning("Tushare概念板块不可用: %s", e)
        return []


def _fetch_tushare_news(code: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    """Tushare 个股公告。news 公共新闻不按股票代码稳定映射，正式链路先接 anns_d。"""
    try:
        from app.services.tushare_client import get_pro, ts_code

        pro = get_pro()
        compact_target = _compact_trade_date(trade_date)
        fields = "ts_code,ann_date,title,url"
        kwargs: dict[str, Any] = {"ts_code": ts_code(code), "fields": fields}
        if compact_target:
            kwargs["start_date"] = (
                date.fromisoformat(_normalize_trade_date(compact_target)) - timedelta(days=7)
            ).strftime("%Y%m%d")
            kwargs["end_date"] = compact_target
        else:
            kwargs["limit"] = 30
        df = pro.anns_d(**kwargs)
    except Exception as e:
        logger.debug("Tushare公告不可用 (%s): %s", code, e)
        return []
    if df is None or df.empty:
        return []

    items: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        row_code = str(row.get("ts_code") or "").split(".")[0]
        if row_code and row_code != code:
            continue
        announce_date = _normalize_trade_date(row.get("ann_date"))
        title = str(row.get("title") or "").strip()
        if not announce_date or not title:
            continue
        url = str(row.get("url") or "").strip()
        is_risk = _contains_any(title, ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报", "冻结"])
        is_positive = _contains_any(title, ["增长", "预增", "扭亏", "中标", "签约", "回购", "增持", "分红", "重组", "获批"])
        if is_risk:
            sentiment = "negative"
        elif is_positive:
            sentiment = "positive"
        else:
            sentiment = "neutral"
        items.append({
            "source": "tushare",
            "article_id": _stable_article_id("tushare", code, announce_date, title, url),
            "code": code,
            "announce_date": announce_date,
            "title": title,
            "summary": url,
            "category": "announcement",
            "sentiment": sentiment,
            "is_risk_warning": is_risk,
        })
    return items


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _stable_article_id(source: str, code: str, announce_date: str, title: str, url: str) -> str:
    raw = "|".join([source, code, announce_date, title, url])
    return sha1(raw.encode("utf-8")).hexdigest()[:40]


# ── 东方财富新闻 ──────────────────────────────────────────────────────


def _eastmoney_article_id(row: dict[str, Any]) -> str:
    """东方财富公告 id：2026-09 起字段 notice_id 改名为 art_code，两者都兼容。"""
    for key in ("art_code", "notice_id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _fetch_eastmoney_news(code: str, settings: Any) -> list[dict[str, Any]]:
    """东方财富个股公告+新闻。"""
    import httpx

    def _http_get_json(url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/",
        }
        for _attempt in range(3):
            try:
                with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error) if last_error else "HTTP 请求失败")

    items: list[dict[str, Any]] = []

    # 公告
    try:
        ann_url = (
            "https://np-anotice-stock.eastmoney.com/api/security/ann"
            f"?page_size=15&page_index=1&ann_type=A"
            f"&stock_list={code}"
            "&column=NOTICE_COLUMN"
        )
        ann_data = _http_get_json(ann_url)
        ann_rows = ann_data.get("data", {}).get("list") or []
        for row in ann_rows:
            title = str(row.get("title_ch", ""))
            is_risk = any(
                kw in title
                for kw in ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报"]
            )
            items.append({
                "source": "eastmoney",
                "article_id": _eastmoney_article_id(row),
                "code": code,
                "announce_date": str(row.get("notice_date", ""))[:10],
                "title": title,
                "summary": str(row.get("summary", "")),
                "category": "announcement",
                "sentiment": "negative" if is_risk else "neutral",
                "is_risk_warning": is_risk,
            })
    except Exception:
        pass

    # 新闻
    try:
        news_url = (
            "https://np-anotice-stock.eastmoney.com/api/security/notify"
            f"?page_size=15&page_index=1"
            f"&stock_list={code}"
            "&column=NOTICE_COLUMN"
        )
        news_data = _http_get_json(news_url)
        news_rows = news_data.get("data", {}).get("list") or []
        for row in news_rows:
            title = str(row.get("title_ch", ""))
            is_negative = any(
                kw in title
                for kw in ["风险", "亏损", "下滑", "减持", "处罚", "跌停", "爆雷", "问询"]
            )
            is_positive = any(
                kw in title
                for kw in ["增长", "预增", "利好", "突破", "签约", "中标", "回购", "增持", "分红"]
            )
            if is_negative:
                sentiment = "negative"
            elif is_positive:
                sentiment = "positive"
            else:
                sentiment = "neutral"
            items.append({
                "source": "eastmoney",
                "article_id": _eastmoney_article_id(row),
                "code": code,
                "announce_date": str(row.get("notice_date", ""))[:10],
                "title": title,
                "summary": str(row.get("summary", "")),
                "category": "news",
                "sentiment": sentiment,
                "is_risk_warning": is_negative,
            })
    except Exception:
        pass

    return items
